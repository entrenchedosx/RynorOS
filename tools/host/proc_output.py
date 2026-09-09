"""Stage 18d Slice C proc-section validation (test images only)."""
import re

PROC_START = b"[PROC] self-test started\r\n"
PROC_VERIFIED = b"[PROC] proc verified\r\n"
# Child write-evidence rows land inside the trailing proc run (children
# run during the proc phase); they are collected here, goldens asserted
# by the test, never structurally validated as proc rows.
_LOAD_WRITE_RE = re.compile(
    rb"\[LOAD\] write slot=(\d+) fd=(\d+) len=(\d+) nwritten=(\d+) hex=([0-9a-f]*)")

_NUM = r"(\d+)"


def split_proc_tail(tail: bytes) -> tuple:
    """Split a trailing contiguous [PROC] run (with embedded child
    [LOAD] write rows) off tail. Returns (head, proc_part). Absent proc
    lines -> (tail, b"")."""
    lines = tail.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    first = None
    for index, line in enumerate(lines):
        if line.strip().startswith(b"[PROC] "):
            first = index
            break
    if first is None:
        return tail, b""
    for line in lines[first:]:
        text = line.strip()
        if text.startswith(b"[PROC] ") or _LOAD_WRITE_RE.fullmatch(text):
            continue
        return tail, b"\r\n".join(lines[first:]) + b"\r\n"
    return ((b"\r\n".join(lines[:first]) + (b"\r\n" if first else b"")),
            b"\r\n".join(lines[first:]) + b"\r\n")


def _allowed():
    return (
        re.compile(rb"\[PROC\] self-test started"),
        re.compile(rb"\[PROC\] abi pins ok"),
        re.compile(rb"\[PROC\] spawn slot=" + _NUM.encode() + rb" gen=" + _NUM.encode()),
        re.compile(rb"\[PROC\] wait state=" + _NUM.encode() + rb" code=" + _NUM.encode()),
        re.compile(rb"\[PROC\] accounting balanced"),
        re.compile(rb"\[PROC\] selfterm ok"),
        re.compile(rb"\[PROC\] table matrix ok"),
        re.compile(rb"\[PROC\] sequential ok"),
        re.compile(rb"\[PROC\] full matrix ok"),
        re.compile(rb"\[PROC\] wait matrix ok"),
        re.compile(rb"\[PROC\] terminate matrix ok"),
        re.compile(rb"\[PROC\] fault matrix ok"),
        re.compile(rb"\[PROC\] argv matrix ok"),
        re.compile(rb"\[PROC\] rynx reject ok"),
        re.compile(rb"\[PROC\] rynx matrix ok"),
        re.compile(rb"\[PROC\] owner matrix ok"),
        re.compile(rb"\[PROC\] guest matrix ok"),
        re.compile(rb"\[PROC\] big matrix ok"),
        re.compile(rb"\[PROC\] no image, skipped"),
        re.compile(rb"\[PROC\] proc verified"),
    )


def validate_proc_section(part: bytes) -> list:
    """Structural check for the trailing proc section.

    Incomplete sections report 'proc section incomplete' (the boot loop
    keeps waiting); complete-but-wrong sections report 'proc mismatch'.
    """
    if part == b"":
        return []
    lines = part.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines or lines[0].strip() != PROC_START.strip():
        return ["proc section incomplete"]
    if lines[-1].strip() != PROC_VERIFIED.strip():
        return ["proc section incomplete"]
    allowed = _allowed()
    for line in lines:
        text = line.strip()
        if text.startswith(b"[PROC] failure="):
            return ["guest failure: " + text.decode("ascii", "replace")]
        if _LOAD_WRITE_RE.fullmatch(text):
            continue
        if not any(rx.fullmatch(text) for rx in allowed):
            return ["proc mismatch: unexpected line %r" % text[:60]]
    return []


def collect_load_writes(part: bytes) -> list:
    """Child [LOAD] write payloads in transcript order as (slot, hex)."""
    out = []
    for line in part.split(b"\r\n"):
        m = _LOAD_WRITE_RE.fullmatch(line.strip())
        if m:
            out.append((int(m.group(1)), m.group(5).decode()))
    return out
