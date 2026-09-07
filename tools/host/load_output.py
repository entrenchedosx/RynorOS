"""Serial validator for the Stage 18b loader section ([LOAD] ...).

Trailing section after [USER]: an optional [SYSTEM] stage18b banner then
[LOAD] lines, ending with [LOAD] load verified (programs ran) or the
single line [LOAD] no image, skipped (no RYNX found). Structural checks
live here; semantic checks (bytes vs image, exit codes, reasons) need
the filesystem image and live in validate().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CODE_BASE = 0x400000
DATA_BASE = 0x600000
STACK_PAGE = 0x7FF000
STACK_TOP = 0x800000
PAGE = 4096

VERIFIED_LINE = b"[LOAD] load verified\r\n"
SKIPPED_LINE = b"[LOAD] no image, skipped\r\n"

_SYSTEM_RE = re.compile(rb"^\[SYSTEM\] RynorOS \S+ \| Rynorkernel \| stage18b program loader$")
_CREATE_RE = re.compile(rb"^\[LOAD\] create slot=(\d+) code_size=(\d+) tables=(\d+)$")
_PROGRAM_RE = re.compile(rb"^\[LOAD\] program path=(\S+) entry=(0x[0-9a-f]+) "
                         rb"code=(\d+) data=(\d+)/(\d+)$")
_MAP_RE = re.compile(rb"^\[LOAD\] map slot=(\d+) kind=(code|data|stack) "
                     rb"va=(0x[0-9a-f]{16}) pa=(0x[0-9a-f]{16}) perm=(rx|rw)$")
_EXIT_RE = re.compile(rb"^\[LOAD\] exit slot=(\d+) code=(\d+)$")
_WRITE_RE = re.compile(rb"^\[LOAD\] write slot=(\d+) fd=(\d+) len=(\d+) "
                       rb"nwritten=(\d+) hex=([0-9a-f]*)$")
_REJECT_RE = re.compile(rb"^\[LOAD\] reject path=(\S+) reason=([a-z_]+)$")
_DESTROY_RE = re.compile(rb"^\[LOAD\] destroy slot=(\d+)$")
_TICKSPIN_RE = re.compile(rb"^\[LOAD\] tickspin cpl3_delta=(\d+) preemptions=(\d+)$")

_BALANCED = b"[LOAD] accounting balanced"
_VERIFIED = b"[LOAD] load verified"
_SKIPPED = b"[LOAD] no image, skipped"
_TEST_RE = re.compile(rb"^\[TEST\] load self-test passed$")

# Expected outcomes per program path (exit codes) and per bad path
# (reject reasons). The RYNX bytes themselves come from the image.
EXIT_CODES = {
    "/rnyx/exit42.rnx": 42,
    "/rnyx/writehello.rnx": 0,
    "/rnyx/fib27.rnx": 196418,
    "/rnyx/bsszero.rnx": 42,
    "/rnyx/sysprobe.rnx": 0,
}
REJECT_REASONS = {
    "/rnyx/bad00.rnx": "magic",
    "/rnyx/bad01.rnx": "version",
    "/rnyx/bad02.rnx": "arch",
    "/rnyx/bad03.rnx": "header",
    "/rnyx/bad04.rnx": "header",
    "/rnyx/bad05.rnx": "entry",
    "/rnyx/bad06.rnx": "code_size",
    "/rnyx/bad07.rnx": "code_size",
    "/rnyx/bad08.rnx": "data_size",
    "/rnyx/bad09.rnx": "data_size",
    "/rnyx/bad10.rnx": "shape",
    "/rnyx/bad11.rnx": "shape",
    "/rnyx/bad12.rnx": "data_size",
}
WRITE_HELLO = b"hello"


@dataclass
class LoadEvidence:
    creates: list = field(default_factory=list)  # (slot, size, tables)
    programs: list = field(default_factory=list)  # (path, entry, code, fsz, msz)
    maps: list = field(default_factory=list)  # (slot, kind, va, pa, perm)
    exits: list = field(default_factory=list)  # (slot, code)
    writes: list = field(default_factory=list)  # (slot, fd, len, n, bytes)
    rejects: list = field(default_factory=list)  # (path, reason)
    destroys: list = field(default_factory=list)  # slot
    tickspin: list = field(default_factory=list)  # (delta, preemptions)
    balanced: int = 0
    failures: list = field(default_factory=list)


def _parse_hex(text: bytes) -> bytes:
    return bytes(int(text[i:i + 2], 16) for i in range(0, len(text), 2))


def parse_serial(observed: bytes) -> LoadEvidence:
    evidence = LoadEvidence()
    for raw in observed.split(b"\r\n"):
        line = raw.strip()
        if line.startswith(b"[LOAD] failure="):
            evidence.failures.append(line.decode("ascii", "replace"))
            continue
        match = _PROGRAM_RE.match(line)
        if match:
            evidence.programs.append((match.group(1).decode("ascii"),
                                      int(match.group(2), 16), int(match.group(3)),
                                      int(match.group(4)), int(match.group(5))))
            continue
        match = _MAP_RE.match(line)
        if match:
            evidence.maps.append((int(match.group(1)), match.group(2).decode("ascii"),
                                  int(match.group(3), 16), int(match.group(4), 16),
                                  match.group(5).decode("ascii")))
            continue
        match = _EXIT_RE.match(line)
        if match:
            evidence.exits.append((int(match.group(1)), int(match.group(2))))
            continue
        match = _WRITE_RE.match(line)
        if match:
            evidence.writes.append((int(match.group(1)), int(match.group(2)),
                                    int(match.group(3)), int(match.group(4)),
                                    _parse_hex(match.group(5))))
            continue
        match = _REJECT_RE.match(line)
        if match:
            evidence.rejects.append((match.group(1).decode("ascii"),
                                     match.group(2).decode("ascii")))
            continue
        match = _DESTROY_RE.match(line)
        if match:
            evidence.destroys.append(int(match.group(1)))
            continue
        match = _TICKSPIN_RE.match(line)
        if match:
            evidence.tickspin.append((int(match.group(1)), int(match.group(2))))
            continue
        match = _CREATE_RE.match(line)
        if match:
            evidence.creates.append((int(match.group(1)), int(match.group(2)),
                                     int(match.group(3))))
            continue
        if line == _BALANCED:
            evidence.balanced += 1
    return evidence


def _is_load_start(line: bytes) -> bool:
    text = line.strip()
    return text.startswith(b"[LOAD] ") or bool(_SYSTEM_RE.match(text))


def split_load_sections(tail: bytes) -> tuple:
    """Split a post-userspace tail into (pre, load_part).

    The load section (optional banner + [LOAD] lines) must form one
    contiguous run at the end.
    """
    lines = tail.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    first = None
    for index, line in enumerate(lines):
        if _is_load_start(line):
            first = index
            break
    if first is None:
        return tail, b""
    return b"\r\n".join(lines[:first]) + (b"\r\n" if first else b""), \
        b"\r\n".join(lines[first:]) + b"\r\n"


_ALLOWED = (_CREATE_RE, _PROGRAM_RE, _MAP_RE, _EXIT_RE, _WRITE_RE, _REJECT_RE,
            _DESTROY_RE, _TICKSPIN_RE)


def validate_load_section(part: bytes) -> list:
    """Structural check for the optional trailing loader section."""
    if part == b"":
        return []
    lines = part.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines:
        return ["load section empty"]
    if lines == [_SKIPPED]:
        return []
    for line in lines:
        text = line.strip()
        if text in (_BALANCED, _VERIFIED):
            continue
        if _SYSTEM_RE.match(text) or _TEST_RE.match(text):
            continue
        if text.startswith(b"[LOAD] failure="):
            return [f"guest failure: {text.decode('ascii', 'replace')}"]
        if text.startswith(b"[LOAD] ") and any(rx.match(text) for rx in _ALLOWED):
            continue
        return [f"unexpected loader output: {text[:60]!r}"]
    if lines[-1].strip() != _VERIFIED:
        return ["load section missing load verified"]
    return []


def _parse_rnyx_envelope(blob: bytes):
    """Parse an RYNX file image. Returns dict or None."""
    import struct as _struct

    if len(blob) < 28 or blob[:4] != b"RYNX":
        return None
    _ver, _arch, hlen, _res, entry, code, fsz, msz = _struct.unpack("<HHHHIIII", blob[4:28])
    return {"version": _ver, "arch": _arch, "header_len": hlen,
            "entry": entry, "code": code, "filesz": fsz, "memsz": msz,
            "total": len(blob)}


def validate(evidence: LoadEvidence, files: dict) -> list:
    """Compare guest evidence against the image files. [] valid.

    files maps absolute paths ("/rnyx/exit42.rnx") to file bytes, as
    decoded from the filesystem image (never the build inputs).
    """
    errors = []
    if evidence.failures:
        errors.append(f"guest failures: {evidence.failures}")
    # Six contexts created (P1/P2/P3/A/B/bsszero/sysprobe), all with 6
    # tables; destroys must pair them exactly (slots reused across phases).
    if len(evidence.creates) != 7:
        errors.append(f"want 7 creates, got {len(evidence.creates)}")
    if len(evidence.destroys) != 7:
        errors.append(f"want 7 destroys, got {len(evidence.destroys)}")
    if len(evidence.creates) == 7:
        tables = {row[2] for row in evidence.creates}
        if tables != {6}:
            errors.append(f"create table counts differ: {sorted(tables)}")
        for _, size, _ in evidence.creates:
            if not 0 < size <= PAGE:
                errors.append(f"create code_size out of range: {size}")
        if sorted(row[0] for row in evidence.creates) != sorted(evidence.destroys):
            errors.append("create/destroy slot multisets differ")
    # Programs referenced must exist with matching envelope geometry.
    # Seven programs: exit42/writehello/fib27 ×2 isolation/bsszero/sysprobe.
    # The set is pinned (not guest-defined): a canned 7×exit42 run with
    # matching exits must not validate.
    if len(evidence.programs) != 7:
        errors.append(f"want 7 programs, got {len(evidence.programs)}")
    want_programs = sorted(["/rnyx/exit42.rnx", "/rnyx/writehello.rnx",
                            "/rnyx/fib27.rnx", "/rnyx/exit42.rnx",
                            "/rnyx/exit42.rnx", "/rnyx/bsszero.rnx",
                            "/rnyx/sysprobe.rnx"])
    if sorted(path for path, _, _, _, _ in evidence.programs) != want_programs:
        errors.append(f"program set differs: {[p for p, _, _, _, _ in evidence.programs]}")
    seen_programs = []
    for path, entry, code, fsz, msz in evidence.programs:
        if path not in files:
            errors.append(f"program without file: {path}")
            continue
        seen_programs.append(path)
        envelope = _parse_rnyx_envelope(files[path])
        if envelope is None:
            errors.append(f"program file not an envelope: {path}")
            continue
        if entry != CODE_BASE or code != envelope["code"] or \
                fsz != envelope["filesz"] or msz != envelope["memsz"]:
            errors.append(f"program geometry differs for {path}")
        if envelope["version"] != 1 or envelope["arch"] != 1 or \
                envelope["header_len"] != 28 or envelope["entry"] != 0:
            errors.append(f"program envelope not v1 for {path}")
    # Exit codes exact per program (isolation runs exit42 twice).
    # Seven exits, slot/order ignored only after count is pinned.
    if len(evidence.exits) != 7:
        errors.append(f"want 7 exits, got {len(evidence.exits)}")
    want_exits = []
    for path in seen_programs:
        if path in EXIT_CODES:
            want_exits.append(EXIT_CODES[path])
    if sorted(code for _, code in evidence.exits) != sorted(want_exits):
        errors.append(f"exit rows differ: {sorted(evidence.exits)}")
    # Writes: P2's hello plus the hostile probe's six calls (one good
    # with the first 8 code bytes as payload, five rejected with empty
    # payloads). Order is program order; rejected calls write nothing.
    if len(evidence.writes) != 7:
        errors.append(f"want 7 writes, got {len(evidence.writes)}")
    else:
        s0, f0, l0, n0, p0 = evidence.writes[0]
        if (s0, f0, l0, n0, p0) != (0, 1, len(WRITE_HELLO), len(WRITE_HELLO), WRITE_HELLO):
            errors.append(f"write row differs: {evidence.writes[0]}")
        sysblob = files.get("/rnyx/sysprobe.rnx", b"")
        want_probe = [
            (0, 1, 8, 8, sysblob[28:36] if len(sysblob) >= 36 else b""),
            (0, 2, 8, 0, b""),
            (0, 1, 8, 0, b""),
            (0, 1, 0xFFFFFFFFFFFFFFFF, 0, b""),
            (0, 1, 8, 0, b""),
            (0, 1, 8, 0, b""),
        ]
        if [(s, f, l, n, p) for s, f, l, n, p in evidence.writes[1:]] != want_probe:
            errors.append(f"probe write rows differ: {evidence.writes[1:]}")
    # Maps: fixed windows per slot plus cross-slot frame disjointness.
    # Seven contexts × 3 rows = 21; per-slot exactly 3 (no dup collapse).
    if len(evidence.maps) != 21:
        errors.append(f"want 21 maps, got {len(evidence.maps)}")
    want_maps = {(0, "code", CODE_BASE, "rx"), (0, "data", DATA_BASE, "rw"),
                 (0, "stack", STACK_PAGE, "rw")}
    for slot in {row[0] for row in evidence.maps}:
        rows = [row for row in evidence.maps if row[0] == slot]
        got = {(s, kind, va, perm) for s, kind, va, _, perm in rows}
        want = {(slot, kind, va, perm) for _, kind, va, perm in want_maps}
        if got != want:
            errors.append(f"map rows differ for slot {slot}: {sorted(got)}")
    frames = [pa for _, _, _, pa, _ in evidence.maps]
    if any(pa & (PAGE - 1) for pa in frames):
        errors.append("map physical address misaligned")
    # Frames of one live context are always distinct; PMM may reuse freed
    # frames across phases, so cross-phase uniqueness is NOT required.
    # The isolation pair (slot 1 maps print right after slot 0's) must be
    # disjoint: same VAs, simultaneously live, different frames.
    triples = [frames[i:i + 3] for i in range(0, len(frames), 3)]
    if any(len(triple) != 3 or len(set(triple)) != 3 for triple in triples):
        errors.append("map triple shares a frame")
    slots = [row[0] for row in evidence.maps]
    try:
        first_one = slots.index(1)
    except ValueError:
        errors.append("isolation slot missing")
    else:
        if first_one < 3 or set(frames[first_one:first_one + 3]) & set(frames[first_one - 3:first_one]):
            errors.append("isolation pair shares a frame")
    # Reject matrix exact per path.
    if sorted(evidence.rejects) != sorted(REJECT_REASONS.items()):
        errors.append(f"reject rows differ: {sorted(evidence.rejects)}")
    # Destroys pair with creates (checked above); tickspin shows real
    # preemption during the loaded spin window.
    if len(evidence.tickspin) != 1:
        errors.append(f"want 1 tickspin row, got {len(evidence.tickspin)}")
    else:
        delta, preemptions = evidence.tickspin[0]
        if delta < 1 or preemptions < 1:
            errors.append(f"tickspin shows no preemption: {evidence.tickspin[0]}")
    if evidence.balanced != 8:
        errors.append(f"want 8 accounting lines, got {evidence.balanced}")
    return errors
