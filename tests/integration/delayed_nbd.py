"""Small, deterministic NBD export for the real IDE late-write integration.

The export captures the selected NBD_CMD_WRITE payload, then withholds both
the backing-store write and its reply until the guest reports its bounded
timeout. A success release writes+fsyncs the exact payload before replying;
an error release replies without changing the image.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import struct
import threading
import time


_NBD_MAGIC = 0x4E42444D41474943
_NBD_OPTS_MAGIC = 0x49484156454F5054
_NBD_REP_MAGIC = 0x0003E889045565A9
_NBD_REQUEST_MAGIC = 0x25609513
_NBD_REPLY_MAGIC = 0x67446698

_NBD_FLAG_FIXED_NEWSTYLE = 1
_NBD_FLAG_NO_ZEROES = 2
_NBD_FLAG_HAS_FLAGS = 1
_NBD_FLAG_SEND_FLUSH = 4

_NBD_OPT_EXPORT_NAME = 1
_NBD_OPT_ABORT = 2
_NBD_OPT_LIST = 3
_NBD_OPT_INFO = 6
_NBD_OPT_GO = 7
_NBD_OPT_STRUCTURED_REPLY = 8

_NBD_REP_ACK = 1
_NBD_REP_SERVER = 2
_NBD_REP_INFO = 3
_NBD_REP_ERR_UNSUP = 0x80000001
_NBD_INFO_EXPORT = 0

_NBD_CMD_READ = 0
_NBD_CMD_WRITE = 1
_NBD_CMD_DISC = 2
_NBD_CMD_FLUSH = 3
_NBD_CMD_TRIM = 4
_NBD_CMD_CACHE = 5
_NBD_CMD_WRITE_ZEROES = 6

_NBD_CMD_FLAG_FUA = 1
_MAX_REQUEST = 32 * 1024 * 1024


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = connection.recv(size - len(chunks))
        if not part:
            raise EOFError("NBD peer closed the connection")
        chunks.extend(part)
    return bytes(chunks)


class DelayedNbdExport:
    """Serve one raw file and optionally gate one identified write reply.

    The gated write remains uncommitted and unacknowledged until the host
    explicitly releases it after guest timeout evidence.
    """

    def __init__(self, image: Path, target_offset: int | None, expected_payload: bytes | None,
                 event_path: Path, export_name: str = "rynor-late"):
        self.image = Path(image)
        self.target_offset = target_offset
        self.expected_payload = bytes(expected_payload) if expected_payload is not None else None
        self.event_path = Path(event_path)
        self.export_name = export_name
        if target_offset is None:
            if expected_payload is not None:
                raise ValueError("untargeted export cannot have an expected payload")
            self.expected_payload = None
        elif target_offset < 0 or expected_payload is None or len(self.expected_payload) != 512:
            raise ValueError("late-write target must be a nonnegative 512-byte sector")
        self.size = self.image.stat().st_size
        if target_offset is not None and target_offset + len(self.expected_payload) > self.size:
            raise ValueError("late-write target is outside the NBD export")
        self.event_path.parent.mkdir(parents=True, exist_ok=True)

        self.write_seen = threading.Event()
        self.reply_sent = threading.Event()
        self.release_reply = threading.Event()
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._events: list[dict] = []
        self._event_sequence = 0
        self._file = self.image.open("r+b", buffering=0)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(2)
        self._listener.settimeout(0.2)
        self.host, self.port = self._listener.getsockname()
        self._connection: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, name="delayed-nbd", daemon=True)
        self._write_records: list[dict] = []
        self._read_records: list[dict] = []
        self._submit_ns: int | None = None
        self._write_ns: int | None = None
        self._reply_ns: int | None = None
        self._release_ns: int | None = None
        self._release_error: int | None = None
        self._target_handle: int | None = None
        self._reply_handle: int | None = None
        self._failure: str | None = None
        self._record("listening", host=self.host, port=self.port,
                     export=self.export_name, size=self.size,
                     target_offset=self.target_offset,
                     target_length=len(self.expected_payload)
                     if self.expected_payload is not None else None)

    def start(self) -> None:
        self._thread.start()

    def _record(self, event: str, **fields) -> None:
        with self._event_lock:
            self._event_sequence += 1
            row = {"sequence": self._event_sequence, "event": event,
                   "monotonic_ns": time.monotonic_ns(), **fields}
            with self.event_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
            self._events.append(row)

    def _reply_option(self, connection: socket.socket, option: int,
                      reply_type: int, payload: bytes = b"") -> None:
        connection.sendall(struct.pack("!QIII", _NBD_REP_MAGIC, option,
                                       reply_type, len(payload)) + payload)

    def _negotiate(self, connection: socket.socket) -> None:
        connection.sendall(struct.pack("!QQH", _NBD_MAGIC, _NBD_OPTS_MAGIC,
                                       _NBD_FLAG_FIXED_NEWSTYLE | _NBD_FLAG_NO_ZEROES))
        (client_flags,) = struct.unpack("!I", _read_exact(connection, 4))
        if not (client_flags & _NBD_FLAG_FIXED_NEWSTYLE):
            raise RuntimeError("QEMU did not negotiate fixed-newstyle NBD")

        wanted_name = self.export_name.encode("ascii")
        while not self.stopped.is_set():
            magic, option, length = struct.unpack("!QII", _read_exact(connection, 16))
            if magic != _NBD_OPTS_MAGIC or length > 65536:
                raise RuntimeError("malformed NBD option request")
            payload = _read_exact(connection, length)
            if option in (_NBD_OPT_INFO, _NBD_OPT_GO):
                if len(payload) < 6:
                    raise RuntimeError("short NBD_INFO/GO option")
                (name_length,) = struct.unpack_from("!I", payload)
                name_end = 4 + name_length
                if name_end + 2 > len(payload):
                    raise RuntimeError("invalid NBD export-name length")
                name = payload[4:name_end]
                (info_count,) = struct.unpack_from("!H", payload, name_end)
                if name not in (b"", wanted_name) or \
                        name_end + 2 + 2 * info_count != len(payload):
                    self._reply_option(connection, option, _NBD_REP_ERR_UNSUP)
                    continue
                export_info = struct.pack("!HQH", _NBD_INFO_EXPORT, self.size,
                                          _NBD_FLAG_HAS_FLAGS | _NBD_FLAG_SEND_FLUSH)
                self._reply_option(connection, option, _NBD_REP_INFO, export_info)
                self._reply_option(connection, option, _NBD_REP_ACK)
                if option == _NBD_OPT_GO:
                    return
            elif option == _NBD_OPT_EXPORT_NAME:
                if payload not in (b"", wanted_name):
                    raise RuntimeError("QEMU selected an unknown NBD export")
                connection.sendall(struct.pack("!QH", self.size,
                                               _NBD_FLAG_HAS_FLAGS | _NBD_FLAG_SEND_FLUSH))
                if not (client_flags & _NBD_FLAG_NO_ZEROES):
                    connection.sendall(bytes(124))
                return
            elif option == _NBD_OPT_ABORT:
                self._reply_option(connection, option, _NBD_REP_ACK)
                raise EOFError("QEMU aborted NBD negotiation")
            elif option == _NBD_OPT_LIST:
                export = struct.pack("!I", len(wanted_name)) + wanted_name
                self._reply_option(connection, option, _NBD_REP_SERVER, export)
                self._reply_option(connection, option, _NBD_REP_ACK)
            else:
                # In particular, declining structured replies keeps the
                # transmission phase on the classic fixed 28-byte header.
                self._reply_option(connection, option, _NBD_REP_ERR_UNSUP)
        raise EOFError("NBD export stopped during negotiation")

    def _range_valid(self, offset: int, length: int) -> bool:
        return length <= _MAX_REQUEST and offset <= self.size and \
            length <= self.size - offset

    def _read_at(self, offset: int, length: int) -> bytes:
        with self._lock:
            self._file.seek(offset)
            result = self._file.read(length)
        if len(result) != length:
            raise OSError("short read from NBD backing image")
        return result

    def _write_at(self, offset: int, payload: bytes) -> None:
        with self._lock:
            self._file.seek(offset)
            count = self._file.write(payload)
            if count != len(payload):
                raise OSError("short write to NBD backing image")
            self._file.flush()
            os.fsync(self._file.fileno())

    def _send_reply(self, connection: socket.socket, handle: int,
                    error: int = 0, data: bytes = b"") -> None:
        connection.sendall(struct.pack("!IIQ", _NBD_REPLY_MAGIC, error, handle))
        if data:
            connection.sendall(data)

    def _transmission(self, connection: socket.socket) -> None:
        while not self.stopped.is_set():
            magic, flags, command, handle, offset, length = struct.unpack(
                "!IHHQQI", _read_exact(connection, 28))
            if magic != _NBD_REQUEST_MAGIC:
                raise RuntimeError("malformed NBD transmission request")
            if command == _NBD_CMD_DISC:
                self._record("disconnect", handle=handle)
                return
            if command == _NBD_CMD_FLUSH:
                os.fsync(self._file.fileno())
                self._send_reply(connection, handle)
                continue
            if command not in (_NBD_CMD_READ, _NBD_CMD_WRITE, _NBD_CMD_CACHE,
                               _NBD_CMD_WRITE_ZEROES):
                self._send_reply(connection, handle, 95)
                continue
            if not self._range_valid(offset, length):
                if command == _NBD_CMD_WRITE:
                    if length > _MAX_REQUEST:
                        raise RuntimeError("oversized NBD write payload")
                    _read_exact(connection, length)
                self._send_reply(connection, handle, 22)
                continue

            if command == _NBD_CMD_READ:
                read_record = {"handle": handle, "command": command,
                               "offset": offset, "length": length,
                               "submitted_ns": time.monotonic_ns()}
                self._read_records.append(read_record)
                self._record("read-submitted", **read_record)
                self._send_reply(connection, handle, data=self._read_at(offset, length))
                continue
            if command == _NBD_CMD_CACHE:
                self._read_at(offset, length)
                self._send_reply(connection, handle)
                continue
            if command == _NBD_CMD_WRITE:
                payload = _read_exact(connection, length)
            else:
                payload = bytes(length)

            is_target = self.target_offset is not None and offset == self.target_offset and \
                length == len(self.expected_payload)
            record = {"handle": handle, "command": command,
                      "offset": offset, "length": length,
                      "target": is_target,
                      "payload_matches": payload == self.expected_payload
                      if is_target else None,
                      "payload_hex": payload.hex() if is_target else None,
                      "durable": False,
                      "submitted_ns": time.monotonic_ns()}
            self._write_records.append(record)
            self._record("write-submitted", **record)
            if is_target:
                self._submit_ns = record["submitted_ns"]
                self._target_handle = handle
                self._record("target-write-submitted-pending-reply",
                             offset=offset, length=length,
                             payload_matches=record["payload_matches"],
                             handle=handle, monotonic_ns=self._submit_ns)
                self.write_seen.set()
                if payload != self.expected_payload:
                    self._failure = "target NBD_WRITE payload differs from guest pattern"
                    self._send_reply(connection, handle, 5)
                    continue
                if not self.release_reply.wait(55):
                    self._failure = "host did not release the delayed NBD write"
                    self._send_reply(connection, handle, 5)
                    continue
                if self._release_error is not None:
                    self._send_reply(connection, handle, self._release_error)
                    self._reply_ns = time.monotonic_ns()
                    self._reply_handle = handle
                    self._record("target-write-error-reply-sent", handle=handle,
                                 error=self._release_error, monotonic_ns=self._reply_ns)
                    self.reply_sent.set()
                    continue
            self._write_at(offset, payload)
            record["durable"] = True
            record["durable_ns"] = time.monotonic_ns()
            self._record("write-durable", **record)
            if is_target:
                self._write_ns = record["durable_ns"]
                self._record("target-write-durable-after-release",
                             handle=handle, monotonic_ns=self._write_ns)
            if flags & _NBD_CMD_FLAG_FUA:
                os.fsync(self._file.fileno())
            self._send_reply(connection, handle)
            if is_target:
                self._reply_ns = time.monotonic_ns()
                self._reply_handle = handle
                self._record("target-write-reply-sent", handle=handle,
                             monotonic_ns=self._reply_ns)
                self.reply_sent.set()

    def _serve_connection(self, connection: socket.socket) -> None:
        self._connection = connection
        with connection:
            self._negotiate(connection)
            self._record("transmission-started")
            self._transmission(connection)

    def _serve(self) -> None:
        try:
            while not self.stopped.is_set():
                try:
                    connection, address = self._listener.accept()
                except socket.timeout:
                    continue
                self._record("client-connected", address=address[0])
                try:
                    self._serve_connection(connection)
                except EOFError as error:
                    if not self.stopped.is_set() and not self.reply_sent.is_set():
                        self._failure = str(error)
                        self._record("client-ended-before-late-reply", detail=str(error))
                except (OSError, RuntimeError, ValueError, struct.error) as error:
                    self._failure = f"{type(error).__name__}: {error}"
                    self._record("server-failure", detail=self._failure)
                    return
        except OSError as error:
            if not self.stopped.is_set():
                self._failure = f"{type(error).__name__}: {error}"
                self._record("listener-failure", detail=self._failure)

    def snapshot(self) -> dict:
        return {"write_seen": self.write_seen.is_set(),
                "reply_sent": self.reply_sent.is_set(),
                "submit_ns": self._submit_ns,
                "write_ns": self._write_ns,
                "release_ns": self._release_ns,
                "release_error": self._release_error,
                "reply_ns": self._reply_ns,
                "target_handle": self._target_handle,
                "reply_handle": self._reply_handle,
                "writes": list(self._write_records),
                "reads": list(self._read_records),
                "events": list(self._events),
                "failure": self._failure}

    def note_guest_timeout(self) -> None:
        self._record("guest-timeout-observed")

    def release_success(self) -> None:
        if self._release_ns is None:
            self._release_ns = time.monotonic_ns()
            self._record("host-released-success-reply", monotonic_ns=self._release_ns)
        self.release_reply.set()

    def release_error(self, error: int = 5) -> None:
        if error <= 0:
            raise ValueError("NBD error release requires a positive errno")
        if self._release_ns is None:
            self._release_ns = time.monotonic_ns()
            self._release_error = error
            self._record("host-released-error-reply", error=error,
                         monotonic_ns=self._release_ns)
        self.release_reply.set()

    def close(self) -> None:
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self._release_ns is None and self.write_seen.is_set():
            self.release_error()
        self.release_reply.set()
        try:
            self._listener.close()
        except OSError:
            pass
        connection = self._connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=5)
        if not self._thread.is_alive():
            self._file.close()

    def __enter__(self) -> "DelayedNbdExport":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
