"""Stage 18d Slice D file/pipe-section validation (test images only)."""
import re

FREAD_START = b"[FREAD] self-test started\r\n"
FREAD_VERIFIED = b"[FREAD] fread verified\r\n"
PIPE_START = b"[PIPE] self-test started\r\n"
PIPE_VERIFIED = b"[PIPE] pipe verified\r\n"

_LOAD_WRITE_RE = re.compile(
    rb"\[LOAD\] write slot=(\d+) fd=(\d+) len=(\d+) nwritten=(\d+) hex=([0-9a-f]*)")
_TRANSFER_RE = re.compile(
    rb"\[PIPE\] transfer (\w+) bytes=(\d+) turns=(\d+) full=(\d+) empty=(\d+) overlap=(\d+)")

_NUM = r"(\d+)"


def _fread_allowed():
    return (
        re.compile(rb"\[FREAD\] self-test started"),
        re.compile(rb"\[FREAD\] abi pins ok"),
        re.compile(rb"\[FREAD\] accounting balanced"),
        re.compile(rb"\[FREAD\] spawn slot=" + _NUM.encode() + rb" gen=" + _NUM.encode()),
        re.compile(rb"\[FREAD\] wait state=" + _NUM.encode() + rb" code=" + _NUM.encode()),
        re.compile(rb"\[FREAD\] basic ok"),
        re.compile(rb"\[FREAD\] bounds ok"),
        re.compile(rb"\[FREAD\] probe ok"),
        re.compile(rb"\[FREAD\] discovery probe ok"),
        re.compile(rb"\[FREAD\] discovery matrix ok"),
        re.compile(rb"\[FREAD\] no image, skipped"),
        re.compile(rb"\[FREAD\] fread verified"),
    )


def _pipe_allowed():
    return (
        re.compile(rb"\[PIPE\] self-test started"),
        re.compile(rb"\[PIPE\] abi pins ok"),
        re.compile(rb"\[PIPE\] accounting balanced"),
        re.compile(rb"\[PIPE\] spawn_pipe a=" + _NUM.encode() + rb" b=" + _NUM.encode()),
        re.compile(rb"\[PIPE\] wait state=" + _NUM.encode() + rb" code=" + _NUM.encode()),
        re.compile(rb"\[PIPE\] transfer \w+ bytes=" + _NUM.encode() + rb" turns=" +
                   _NUM.encode() + rb" full=" + _NUM.encode() + rb" empty=" +
                   _NUM.encode() + rb" overlap=" + _NUM.encode()),
        re.compile(rb"\[PIPE\] pipe invariants ok"),
        re.compile(rb"\[PIPE\] small stream ok"),
        re.compile(rb"\[PIPE\] wraparound ok"),
        re.compile(rb"\[PIPE\] oversized full ok"),
        re.compile(rb"\[PIPE\] oversized empty ok"),
        re.compile(rb"\[PIPE\] empty-live ok"),
        re.compile(rb"\[PIPE\] terminal eof ok"),
        re.compile(rb"\[PIPE\] consumer exited before producer wait"),
        re.compile(rb"\[PIPE\] eof-before-wait ok"),
        re.compile(rb"\[PIPE\] hostile read ok"),
        re.compile(rb"\[PIPE\] syscall probe ok"),
        re.compile(rb"\[PIPE\] rollback core ok"),
        re.compile(rb"\[PIPE\] rollback table ok"),
        re.compile(rb"\[PIPE\] rollback pipe-busy ok"),
        re.compile(rb"\[PIPE\] rollback matrix ok"),
        re.compile(rb"\[PIPE\] thread exhaustion ok"),
        re.compile(rb"\[PIPE\] producer abort ok"),
        re.compile(rb"\[PIPE\] consumer abort ok"),
        re.compile(rb"\[PIPE\] double abort ok"),
        re.compile(rb"\[PIPE\] producer fault ok"),
        re.compile(rb"\[PIPE\] consumer fault ok"),
        re.compile(rb"\[PIPE\] interaction matrix ok"),
        re.compile(rb"\[PIPE\] reuse iter=" + _NUM.encode()),
        re.compile(rb"\[PIPE\] reuse ok"),
        re.compile(rb"\[PIPE\] ticks iters=" + _NUM.encode() + rb" cpl3_ticks=" +
                   _NUM.encode() + rb" switches=" + _NUM.encode()),
        re.compile(rb"\[PIPE\] tick observability ok"),
        re.compile(rb"\[PIPE\] no image, skipped"),
        re.compile(rb"\[PIPE\] pipe verified"),
    )


def _section(output: bytes, start: bytes, verified: bytes):
    begin = output.find(start)
    if begin < 0:
        return None
    end = output.find(verified, begin)
    if end < 0:
        return None
    return output[begin:end + len(verified)]


def strip_section(output: bytes, start: bytes, verified: bytes) -> bytes:
    """Remove one complete marker-delimited run (first occurrence).
    Absent or unterminated runs leave the output untouched."""
    part = _section(output, start, verified)
    if part is None:
        return output
    return output.replace(part, b"", 1)


def extract_fread_section(output: bytes):
    return _section(output, FREAD_START, FREAD_VERIFIED)


def extract_pipe_section(output: bytes):
    return _section(output, PIPE_START, PIPE_VERIFIED)


def _validate(part: bytes, start: bytes, verified: bytes, allowed, label: str) -> list:
    if part is None:
        return [label + " section incomplete"]
    lines = part.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines or lines[0].strip() != start.strip():
        return [label + " section incomplete"]
    if lines[-1].strip() != verified.strip():
        return [label + " section incomplete"]
    for line in lines:
        text = line.strip()
        if text.startswith(b"[FREAD] failure=") or text.startswith(b"[PIPE] failure="):
            return ["guest failure: " + text.decode("ascii", "replace")]
        if _LOAD_WRITE_RE.fullmatch(text):
            continue
        if not any(rx.fullmatch(text) for rx in allowed):
            return [label + " mismatch: unexpected line %r" % text[:60]]
    return []


def validate_fread_section(output: bytes) -> list:
    return _validate(extract_fread_section(output), FREAD_START,
                     FREAD_VERIFIED, _fread_allowed(), "fread")


def validate_pipe_section(output: bytes) -> list:
    return _validate(extract_pipe_section(output), PIPE_START,
                     PIPE_VERIFIED, _pipe_allowed(), "pipe")


def collect_load_writes(part: bytes) -> list:
    """Child [LOAD] write payloads in transcript order as (slot, hex)."""
    out = []
    for line in part.split(b"\r\n"):
        m = _LOAD_WRITE_RE.fullmatch(line.strip())
        if m:
            out.append((int(m.group(1)), m.group(5).decode()))
    return out


def collect_transfers(part: bytes) -> list:
    """[PIPE] transfer rows as dicts in transcript order."""
    out = []
    for line in part.split(b"\r\n"):
        m = _TRANSFER_RE.fullmatch(line.strip())
        if m:
            out.append({"name": m.group(1).decode(),
                        "bytes": int(m.group(2)), "turns": int(m.group(3)),
                        "full": int(m.group(4)), "empty": int(m.group(5)),
                        "overlap": int(m.group(6))})
    return out
