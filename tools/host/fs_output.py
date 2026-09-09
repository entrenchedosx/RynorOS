"""Host reference decoder + serial validator for RYNORFS v1 (Stage 17b).

The decoder is written from the format specification, not from fs.c: it
re-derives superblock, directory, extents, and file contents from image
bytes so guest reads can be checked against an independent expectation.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"RYNORFS\x00"
VERSION = 1
BLOCK = 512
MAX_DIR_BLOCKS = 64

FS_CODES = {
    "ok", "invalid", "notfound", "notfile", "notdir", "badhandle",
    "range", "ioerr", "corrupt", "unsupported", "busy",
}


@dataclass
class FsEntry:
    name: str
    ftype: int  # 1 file, 2 dir
    first: int
    count: int
    length: int


@dataclass
class FsImage:
    total: int
    dir_start: int
    dir_blocks: int
    data_start: int
    data_blocks: int
    entries: dict = field(default_factory=dict)  # name -> FsEntry


def decode(image: bytes) -> FsImage:
    """Decode and fully validate an image. Raises ValueError(code, detail)."""
    def fail(code, detail=""):
        raise ValueError(code, detail)
    if len(image) < BLOCK or len(image) % BLOCK:
        fail("invalid", "size")
    if image[0:8] != MAGIC:
        fail("invalid", "magic")
    (version,) = struct.unpack("<I", image[8:12])
    if version != VERSION:
        fail("unsupported", "version")
    (blksz,) = struct.unpack("<I", image[12:16])
    if blksz != BLOCK:
        fail("unsupported", "blksize")
    if struct.unpack("<Q", image[56:64])[0]:
        fail("corrupt", "reserved")
    if any(image[64:BLOCK]):
        fail("corrupt", "padding")
    total, dir_start, dir_blocks = struct.unpack("<QQQ", image[16:40])
    data_start, data_blocks = struct.unpack("<QQ", image[40:56])
    nblocks = len(image) // BLOCK
    if not total or total > nblocks:
        fail("corrupt", "total-range")
    if not dir_blocks:
        fail("corrupt", "dir-empty")
    if dir_blocks > MAX_DIR_BLOCKS:
        fail("unsupported", "dir-too-big")
    for label, start, count in (("dir", dir_start, dir_blocks), ("data", data_start, data_blocks)):
        if not count or start > total or count > total - start:
            fail("corrupt", f"{label}-range")
        if not start:
            fail("corrupt", f"{label}-zero")
    if not data_blocks:
        fail("corrupt", "data-empty")
    for (a, na), (b, nb) in [((0, 1), (dir_start, dir_blocks)),
                             ((0, 1), (data_start, data_blocks)),
                             ((dir_start, dir_blocks), (data_start, data_blocks))]:
        if a < b + nb and b < a + na:
            fail("corrupt", "overlap")
    entries = {}
    for slot in range(dir_blocks * 8):
        base = (dir_start + slot // 8) * BLOCK + (slot % 8) * 64
        record = image[base:base + 64]
        if not any(record):
            continue
        nul = record.find(b"\x00")
        if nul <= 0 or nul > 31:
            fail("corrupt", "bad-name")
        name = record[:nul].decode("ascii")
        if any(not 0x20 <= c <= 0x7E for c in map(ord, name)):
            fail("corrupt", "bad-name")
        for comp in name.split("/"):
            if not comp or comp in (".", ".."):
                fail("corrupt", "bad-name")
        if any(record[nul + 1:32]):
            fail("corrupt", "name-padding")
        ftype = record[32]
        if ftype not in (1, 2):
            fail("corrupt", "bad-type")
        if any(record[33:40]):
            fail("corrupt", "entry-reserved")
        first, count, length = struct.unpack("<QQQ", record[40:64])
        if ftype == 2:
            if first or count or length:
                fail("corrupt", "dir-data")
        else:
            if not count:
                if length or first:
                    fail("corrupt", "empty-extent")
            else:
                if not length or length > count * BLOCK:
                    fail("corrupt", "length-range")
                data_end = data_start + data_blocks
                if first < data_start or first >= data_end or count > data_end - first:
                    fail("corrupt", "extent-range")
        if name in entries:
            fail("corrupt", "duplicate")
        entries[name] = FsEntry(name, ftype, first, count, length)
    for name in entries:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            holder = entries.get(parent)
            if holder is None or holder.ftype != 2:
                fail("corrupt", "dangling-parent")
    files = [(a.first, a.count, a.name) for a in entries.values()
             if a.ftype == 1 and a.count]
    for x in range(len(files)):
        for y in range(x + 1, len(files)):
            if files[x][0] < files[y][0] + files[y][1] and files[y][0] < files[x][0] + files[x][1]:
                fail("corrupt", "overlap")
    return FsImage(total, dir_start, dir_blocks, data_start, data_blocks, entries)


def file_bytes(image: bytes, fs: FsImage, name: str) -> bytes:
    entry = fs.entries[name]
    assert entry.ftype == 1
    out = bytearray()
    remaining = entry.length
    block = entry.first
    while remaining:
        chunk = image[block * BLOCK:block * BLOCK + BLOCK][:remaining]
        if not chunk:
            raise ValueError("corrupt", "short-image")
        out += chunk
        remaining -= len(chunk)
        block += 1
    return bytes(out)


def block_sum(data: bytes) -> int:
    return sum(data)


def block_wsum(data: bytes) -> int:
    total = 0
    for i, byte in enumerate(data):
        total += i * byte
    return total


_FILE_RE = re.compile(rb"^\[FS\] file path=(\S+) size=(\d+) sum=(\d+) wsum=(\d+)$")
_PART_RE = re.compile(rb"^\[FS\] part path=(\S+) off=(\d+) len=(\d+) sum=(\d+) wsum=(\d+)$")
_MOUNT_RE = re.compile(rb"^\[FS\] mounted dev=(\d+) blocks=(\d+)$")
_CORRUPT_RE = re.compile(rb"^\[FS\] corrupt slot=(\d+) code=([a-z]+)$")
_WRITE_RE = re.compile(rb"^\[FS\] write path=(\S+) off=(\d+) len=(\d+) hex=([0-9A-F]*)$")
_FAULT_RE = re.compile(rb"^\[FS\] fault case=([a-z]+) written=(\d+) code=([a-z]+)$")


@dataclass
class FsEvidence:
    mounted: tuple | None = None
    files: dict = field(default_factory=dict)
    parts: list = field(default_factory=list)
    corrupts: dict = field(default_factory=dict)
    writes: list = field(default_factory=list)
    faults: list = field(default_factory=list)
    handles: bool = False
    accounting: bool = False
    verified: bool = False
    failures: list = field(default_factory=list)
    # Ordered content events for phased validation: ("file"|"part"|"write", ...).
    # File/part lines are checked against content patched by the writes
    # printed SO FAR, mirroring guest program order.
    events: list = field(default_factory=list)


def parse_serial(observed: bytes) -> FsEvidence:
    evidence = FsEvidence()
    for raw in observed.split(b"\r\n"):
        line = raw.strip()
        if line.startswith(b"[FS] failure="):
            evidence.failures.append(line.decode("ascii", "replace"))
            continue
        match = _MOUNT_RE.match(line)
        if match:
            evidence.mounted = (int(match.group(1)), int(match.group(2)))
            continue
        match = _FILE_RE.match(line)
        if match:
            path = match.group(1).decode("ascii")
            row = (path, int(match.group(2)), int(match.group(3)), int(match.group(4)))
            evidence.files[path] = row[1:]
            evidence.events.append(("file",) + row)
            continue
        match = _PART_RE.match(line)
        if match:
            row = (match.group(1).decode("ascii"), int(match.group(2)),
                   int(match.group(3)), int(match.group(4)), int(match.group(5)))
            evidence.parts.append(row)
            evidence.events.append(("part",) + row)
            continue
        match = _CORRUPT_RE.match(line)
        if match:
            evidence.corrupts[int(match.group(1))] = match.group(2).decode("ascii")
            continue
        match = _WRITE_RE.match(line)
        if match:
            row = (match.group(1).decode("ascii"), int(match.group(2)),
                   int(match.group(3)), match.group(4).decode("ascii"))
            evidence.writes.append(row)
            evidence.events.append(("write",) + row)
            continue
        match = _FAULT_RE.match(line)
        if match:
            evidence.faults.append((match.group(1).decode("ascii"), int(match.group(2)),
                                    match.group(3).decode("ascii")))
            continue
        if line == b"[FS] handles ok":
            evidence.handles = True
        elif line == b"[FS] accounting balanced":
            evidence.accounting = True
        elif line == b"[FS] fs verified":
            evidence.verified = True
    return evidence


def split_fs_sections(tail: bytes) -> tuple:
    """Split a post-shell tail into (blk_part, fs_part).

    [FS] lines must form one contiguous run at the end; anything else is
    an error only if it is neither empty nor a [BLK] line (blk validity
    itself is checked by blk_output, not here).
    """
    lines = tail.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    first = None
    for index, line in enumerate(lines):
        if line.strip().startswith(b"[FS] "):
            first = index
            break
    if first is None:
        return tail, b""
    return b"\r\n".join(lines[:first]) + (b"\r\n" if first else b""), \
        b"\r\n".join(lines[first:]) + b"\r\n"


def validate_fs_section(part: bytes) -> list:
    """Structural check for the optional trailing filesystem section."""
    if part == b"":
        return []
    lines = part.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines or not lines[0].strip().startswith(b"[FS] mounted"):
        return ["filesystem section must start with [FS] mounted"]
    allowed = (_MOUNT_RE, _FILE_RE, _PART_RE, _CORRUPT_RE, _WRITE_RE, _FAULT_RE)
    for line in lines:
        text = line.strip()
        if text in (b"[FS] handles ok", b"[FS] accounting balanced",
                    b"[FS] fs verified"):
            continue
        if text.startswith(b"[FS] failure="):
            return [f"guest failure: {text.decode('ascii', 'replace')}"]
        if text.startswith(b"[FS] ") and any(rx.match(text) for rx in allowed):
            continue
        return [f"unexpected filesystem output: {text[:60]!r}"]
    if lines[-1].strip() != b"[FS] fs verified":
        return ["filesystem section missing fs verified"]
    return []


def validate(evidence: FsEvidence, image: bytes) -> list:
    """Compare guest evidence against the independently decoded image.

    image is raw bytes (read from the image file by the caller). [] valid.
    """
    errors = []
    if evidence.failures:
        errors.append(f"guest failures: {evidence.failures}")
    try:
        fs = decode(image)
    except ValueError as error:
        return errors + [f"host cannot decode test image: {error}"]
    if evidence.mounted is None:
        errors.append("missing [FS] mounted line")
    else:
        _dev, blocks = evidence.mounted
        if blocks != fs.total:
            errors.append(f"mounted blocks {blocks} != image {fs.total}")
    # Patched expectations, applied in printed order: every file/part
    # line is checked against content patched by the writes printed SO
    # FAR, mirroring guest program order (pre-write evidence against
    # pristine bytes, readback evidence against patched bytes).
    patched = {}
    for name, entry in fs.entries.items():
        if entry.ftype == 1:
            patched[name] = bytearray(file_bytes(image, fs, name))
    seen_files = set()
    for event in evidence.events:
        if event[0] == "write":
            _, path, off, length, hexdata = event
            key = path[1:] if path.startswith("/") else None
            if key is None or key not in patched:
                errors.append(f"write to unknown file {path}")
                continue
            if len(hexdata) != 2 * length:
                errors.append(f"write hex length mismatch for {path}")
                continue
            try:
                payload = bytes.fromhex(hexdata)
            except ValueError:
                errors.append(f"write hex malformed for {path}")
                continue
            blob = patched[key]
            if off > len(blob) or length > len(blob) - off:
                errors.append(f"write {path}@{off}+{length} outside file")
                continue
            blob[off:off + length] = payload
        elif event[0] == "file":
            _, path, size, asum, wsum = event
            key = path[1:] if path.startswith("/") else None
            entry = fs.entries.get(key) if key is not None else None
            if entry is None or entry.ftype != 1:
                errors.append(f"file evidence for unknown file {path}")
                continue
            if size != entry.length:
                errors.append(f"{path} size {size} != {entry.length}")
                continue
            seen_files.add(path)
            blob = bytes(patched[key])
            if (asum, wsum) != (block_sum(blob), block_wsum(blob)):
                errors.append(f"{path} sums differ")
        elif event[0] == "part":
            _, path, off, length, asum, wsum = event
            key = path[1:] if path.startswith("/") else None
            entry = fs.entries.get(key) if key is not None else None
            if entry is None or entry.ftype != 1:
                errors.append(f"part for unknown file {path}")
                continue
            blob = bytes(patched[key])
            if off > len(blob) or off + length > len(blob):
                errors.append(f"part {path}@{off}+{length} outside file")
                continue
            window = blob[off:off + length]
            if (asum, wsum) != (block_sum(window), block_wsum(window)):
                errors.append(f"part {path}@{off} sums differ")
    for name, entry in sorted(fs.entries.items()):
        if entry.ftype == 1 and ("/" + name) not in seen_files:
            errors.append(f"missing file evidence for /{name}")
    if not evidence.handles:
        errors.append("missing handles evidence")
    if not evidence.accounting:
        errors.append("missing accounting evidence")
    if not evidence.verified:
        errors.append("missing [FS] fs verified")
    return errors
