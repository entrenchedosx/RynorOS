"""Stage 18d Slice E CPL3 shell transcript validation (shell images only).

Shell rows ([SH]) and shell-driver rows ([SHD]) have exact shapes;
child/echo bytes travel raw AND as kernel [LOAD] write rows (the host
asserts selected goldens from the hex channel). Anything else that is
not a kernel section row is tolerated as echo/child output here and
pinned by the tests.
"""
import re

SH_READY = b"[SH] ready"
SH_PROMPT = b"[SH] prompt"
SHD_HALT = b"[SHD] halt"

_LOAD_WRITE_RE = re.compile(
    rb"\[LOAD\] write slot=(\d+) fd=(\d+) len=(\d+) nwritten=(\d+) hex=([0-9a-f]*)")


def _load_hex(m) -> bytes | None:
    """Hex payload of a [LOAD] write row, or None when the row is
    poll-truncated (a partially-flushed line still regex-matches, so
    the nwritten length cross-check is what makes this poll-safe)."""
    try:
        nwritten = int(m.group(4))
    except ValueError:
        return None
    raw = m.group(5)
    if len(raw) != 2 * nwritten:
        return None
    try:
        return bytes.fromhex(raw.decode())
    except ValueError:
        return None


def terminal_stream(output: bytes) -> bytes:
    """Reconstructed terminal stream: hex-decoded [LOAD] payloads in
    transcript order. CPL3 write() bytes surface ONLY here (the kernel
    never forwards user bytes raw); shell markers, echoes, and child
    output all multiplex through this channel with slot attribution
    available via collect_load_writes. Partial trailing rows are
    skipped until complete (poll-safe)."""
    out = bytearray()
    for line in output.split(b"\r\n"):
        m = _LOAD_WRITE_RE.fullmatch(line.strip())
        if m:
            payload = _load_hex(m)
            if payload is not None:
                out += payload
    return bytes(out)


def stream_contains(output: bytes, marker: bytes) -> bool:
    return marker in terminal_stream(output)
_SH_RE = re.compile(
    rb"\[(SH|SHD)\] (ready|prompt|wantkey \d+|done status=\d+|"
    rb"spawned a=\d+,\d+( b=\d+,\d+)?|"
    rb"reaped \d+,\d+ (exited|faulted|aborted) \d+|"
    rb"overlap 1|abort line|abort child status=\d+|"
    rb"abort pipeline status=\d+|error [a-z-]+( \d+)?|"
    rb"script \S+|script done status=\d+|"
    rb"balanced|missing \S+|malformed \S+|halt code=\d+|fault shell)")
_KERNEL_ROW = re.compile(
    rb"\[(TEST|SYSTEM|CPU|EXCEPTION|STATE|GPR|MM|PAGE|PMM|VM|HEAP|SCHED|KBD|FB|"
    rb"RUNTIME|SHELL|BLK|FS|USER|LOAD|RT|INPUT|PROC|FREAD|PIPE|SHD)\][^\r]*")


def _lines(output: bytes) -> list:
    """Split on CRLF after folding lone LF (child/plain `\n` output
    merges cleanly instead of gluing to the next marker row)."""
    return output.replace(b"\n", b"\r\n").replace(b"\r\r\n", b"\r\n").split(b"\r\n")


def strip_shell_lines(output: bytes) -> bytes:
    """Remove shell-run rows ([SH]/[SHD]) and kernel-observed child
    write rows so the legacy greedy section chain validates the
    kernel sections underneath. Shell goldens come from the raw
    transcript in the tests; stripping here is validation-only."""
    kept = []
    for line in output.split(b"\r\n"):
        text = line.strip()
        if text.startswith(b"[SH] ") or text.startswith(b"[SHD] "):
            continue
        if _LOAD_WRITE_RE.fullmatch(text):
            continue
        kept.append(line)
    return b"\r\n".join(kept)


def has_shell_rows(output: bytes) -> bool:
    # Shell rows surface raw ([SHD] kernel prints) and hex-wrapped
    # ([SH] CPL3 prints inside [LOAD] rows); either signals a shell run.
    for line in output.split(b"\r\n"):
        text = line.strip()
        if text.startswith(b"[SH] ") or text.startswith(b"[SHD] "):
            return True
    return b"[SH] " in terminal_stream(output)


_ROW_RE = re.compile(rb"\[(SH|SHD)\] [^\r\n]*")


def validate_sh_section(output: bytes) -> list:
    """Structural check over shell rows (raw kernel [SHD] rows plus
    decoded-stream [SH] rows; unanchored so rows glued to child output
    still validate).

    Unknown [SH]/[SHD] rows are mismatches (typo/degrader tripwire);
    kernel failure rows are guest failures. All other bytes are
    tolerated (goldens live in the tests).
    """
    for m in list(_ROW_RE.finditer(output)) + \
            list(_ROW_RE.finditer(terminal_stream(output))):
        text = m.group(0).strip()
        if text.startswith(b"[SH] failure=") or text.startswith(b"[SHD] failure="):
            return ["guest failure: " + text.decode("ascii", "replace")]
        if not _SH_RE.fullmatch(text):
            return ["shell mismatch: unexpected row %r" % text[:80]]
    return []


def collect_sh_rows(output: bytes) -> list:
    """All [SH]/[SHD] rows in order as decoded strings (stream rows in
    appearance order, then raw kernel rows)."""
    out = [m.group(0).decode("ascii", "replace")
           for m in _ROW_RE.finditer(terminal_stream(output))]
    out += [m.group(0).decode("ascii", "replace")
            for m in _ROW_RE.finditer(output)
            if m.group(0).startswith(b"[SHD] ")]
    return out


def collect_load_writes(output: bytes) -> list:
    """Kernel-observed serial payloads in order as (slot, hex)."""
    out = []
    for line in output.split(b"\r\n"):
        m = _LOAD_WRITE_RE.fullmatch(line.strip())
        if m and _load_hex(m) is not None:
            out.append((int(m.group(1)), m.group(5).decode()))
    return out


def collect_done_statuses(output: bytes) -> list:
    """[SH] done status=N values in order."""
    out = []
    for row in collect_sh_rows(output):
        m = re.fullmatch(r"\[SH\] done status=(\d+)", row)
        if m:
            out.append(int(m.group(1)))
    return out
