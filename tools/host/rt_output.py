"""Serial validator for the Stage 18c runtime section ([RT] ...).

Trailing section after [LOAD]: an optional [SYSTEM] stage18c banner then
[RT] lines, ending with [RT] rt verified (programs ran) or the single
line [RT] no image, skipped (no conformance image found). Structural
checks live here; semantic checks (counts, exits, class evidence,
kernel-observed write bytes) need no image bytes: every number is pinned
against golden constants derived from the frozen library contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from load_output import _WRITE_RE as _LOAD_WRITE_RE

CODE_BASE = 0x400000
PAGE = 4096

VERIFIED_LINE = b"[RT] rt verified\r\n"
SKIPPED_LINE = b"[RT] no image, skipped\r\n"

_SYSTEM_RE = re.compile(rb"^\[SYSTEM\] RynorOS \S+ \| Rynorkernel \| stage18c runtime library$")
_CREATE_RE = re.compile(rb"^\[RT\] create slot=(\d+) code_size=(\d+) tables=(\d+)$")
_PROGRAM_RE = re.compile(rb"^\[RT\] program path=(\S+) entry=(0x[0-9a-f]+) "
                         rb"code=(\d+) data=(\d+)/(\d+)$")
_EXIT_RE = re.compile(rb"^\[RT\] exit slot=(\d+) code=(\d+)$")
_DESTROY_RE = re.compile(rb"^\[RT\] destroy slot=(\d+)$")

_BALANCED = b"[RT] accounting balanced"
_VERIFIED = b"[RT] rt verified"
_SKIPPED = b"[RT] no image, skipped"
_TEST_RE = re.compile(rb"^\[TEST\] rt self-test passed$")
# Program output travels as kernel [LOAD] write rows interleaved with the
# driver rows (the serial sink only carries hex evidence). The semantic
# check in validate() pins their exact bytes; structurally they belong
# to this section, not to a loader run.

# Conformance programs in driver order with their expected exits.
PROGRAMS = [
    "/rt/fmt.rnx", "/rt/alloc.rnx", "/rt/write.rnx",
    "/rt/nap.rnx", "/rt/wait.rnx", "/rt/nosys.rnx",
    "/rt/rlprint.rnx",
]

# Exact class-evidence lines in program order. Hand-derived from the
# frozen library contract (enum values, watermark math, offsets); the
# guest prints actuals, so any behavioral deviation mismatches here.
CLASS_LINES = [
    b"[RT] fmt ok s=hello u=42 x=2a c=Z rc=21",
    b"[RT] fmt badspec rc=4294967295 untouched=1",
    b"[RT] fmt trunc rc=4294967294 untouched=1",
    b"[RT] fmt wm0=0 wm1=0 live=0",
    b"[RT] alloc a1off=0 a2off=16 wm1=48 live=2",
    b"[RT] alloc fr1=0 fr2=0 live=0 wm2=48",
    b"[RT] alloc fill live=15 wm3=1968",
    b"[RT] alloc nomem rc=5 wm4=1968 live=14",
    b"[RT] alloc drained live=0",
    b"[RT] alloc badalign rc=1 oversize rc=2 badfree rc=1 dblfree rc=1",
    b"[RT] write hello rc=0",
    b"[RT] write badfd rc=1 overlen rc=2",
    b"[RT] write wm0=0 live=0",
    b"[RT] nap ok rc=0",
    b"[RT] nap over rc=2",
    b"[RT] wait again rc=4",
    b"[RT] wait ok rc=0",
    b"[RT] nosys open rc=3 read rc=3",
]

# Kernel-observed [LOAD] write payloads in chronological order. Class
# evidence never appears as raw transcript text (the serial sink only
# carries kernel hex rows), so these payloads ARE the class evidence:
# the 18 class lines (each emitted with one rt_write, CRLF included),
# the write program's bare "hello" payload, and the .rl program's three
# prints. The kernel channel independently corroborates the guest
# channel: missing rows, extra rows (e.g. a removed fd check issuing
# the syscall), or altered bytes all fail below.
RL_PRINT_PAYLOADS = [b"42", b"true", b"hi"]
WRITE_PAYLOADS = ([line + b"\r\n" for line in CLASS_LINES[:10]]
                  + [b"hello"]
                  + [line + b"\r\n" for line in CLASS_LINES[10:]]
                  + RL_PRINT_PAYLOADS)


@dataclass
class RtEvidence:
    creates: list = field(default_factory=list)  # (slot, size, tables)
    programs: list = field(default_factory=list)  # (path, entry, code, fsz, msz)
    exits: list = field(default_factory=list)  # (slot, code)
    destroys: list = field(default_factory=list)  # slot
    balanced: int = 0
    failures: list = field(default_factory=list)


def parse_serial(observed: bytes) -> RtEvidence:
    evidence = RtEvidence()
    for raw in observed.split(b"\r\n"):
        line = raw.strip()
        if line.startswith(b"[RT] failure="):
            evidence.failures.append(line.decode("ascii", "replace"))
            continue
        match = _PROGRAM_RE.match(line)
        if match:
            evidence.programs.append((match.group(1).decode("ascii"),
                                      int(match.group(2), 16), int(match.group(3)),
                                      int(match.group(4)), int(match.group(5))))
            continue
        match = _EXIT_RE.match(line)
        if match:
            evidence.exits.append((int(match.group(1)), int(match.group(2))))
            continue
        match = _DESTROY_RE.match(line)
        if match:
            evidence.destroys.append(int(match.group(1)))
            continue
        match = _CREATE_RE.match(line)
        if match:
            evidence.creates.append((int(match.group(1)), int(match.group(2)),
                                     int(match.group(3))))
            continue
        if line == _BALANCED:
            evidence.balanced += 1
    return evidence


def _is_rt_start(line: bytes) -> bool:
    text = line.strip()
    return text.startswith(b"[RT] ") or bool(_SYSTEM_RE.match(text))


def split_rt_sections(tail: bytes) -> tuple:
    """Split a post-userspace tail into (pre, rt_part).

    The rt section (optional banner + [RT] lines) must form one
    contiguous run at the end.
    """
    lines = tail.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    first = None
    for index, line in enumerate(lines):
        if _is_rt_start(line):
            first = index
            break
    if first is None:
        return tail, b""
    return b"\r\n".join(lines[:first]) + (b"\r\n" if first else b""), \
        b"\r\n".join(lines[first:]) + b"\r\n"


# NOTE: raw class-evidence text is deliberately NOT allowed here: class
# lines must arrive as kernel-observed write payloads (checked in
# validate), so a stray raw copy fails structural validation instead of
# being double-counted.
_ALLOWED = (_CREATE_RE, _PROGRAM_RE, _EXIT_RE, _DESTROY_RE)


def validate_rt_section(part: bytes) -> list:
    """Structural check for the optional trailing runtime section."""
    if part == b"":
        return []
    lines = part.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines:
        return ["rt section empty"]
    if lines == [_SKIPPED]:
        return []
    for line in lines:
        text = line.strip()
        if text in (_BALANCED, _VERIFIED):
            continue
        if _SYSTEM_RE.match(text) or _TEST_RE.match(text):
            continue
        if text.startswith(b"[RT] failure="):
            return [f"guest failure: {text.decode('ascii', 'replace')}"]
        if text.startswith(b"[RT] ") and any(rx.match(text) for rx in _ALLOWED):
            continue
        if _LOAD_WRITE_RE.match(text):
            continue
        return [f"unexpected runtime output: {text[:60]!r}"]
    if lines[-1].strip() != _VERIFIED:
        return ["rt section missing rt verified"]
    return []


def validate(evidence: RtEvidence, writes: list) -> list:
    """Compare guest evidence against golden constants. [] valid.

    writes are (slot, fd, len, nwritten, payload) kernel-observed
    [LOAD] write rows backing the class lines plus the .rl prints.
    """
    errors = []
    if evidence.failures:
        errors.append(f"guest failures: {evidence.failures}")
    # Seven contexts created, all with 6 tables; destroys pair exactly.
    if len(evidence.creates) != 7:
        errors.append(f"want 7 creates, got {len(evidence.creates)}")
    else:
        tables = {row[2] for row in evidence.creates}
        if tables != {6}:
            errors.append(f"create table counts differ: {sorted(tables)}")
        for _, size, _ in evidence.creates:
            if not 0 < size <= PAGE:
                errors.append(f"create code_size out of range: {size}")
        if sorted(row[0] for row in evidence.creates) != sorted(evidence.destroys):
            errors.append("create/destroy slot multisets differ")
    if len(evidence.destroys) != 7:
        errors.append(f"want 7 destroys, got {len(evidence.destroys)}")
    # Program set pinned (not guest-defined): all seven paths, each with
    # the fixed entry base.
    if [path for path, _, _, _, _ in evidence.programs] != PROGRAMS:
        errors.append(f"program set differs: {[p for p, _, _, _, _ in evidence.programs]}")
    for _, entry, _, _, _ in evidence.programs:
        if entry != CODE_BASE:
            errors.append(f"program entry not code base: {entry:#x}")
    for path, _, code, fsz, msz in evidence.programs:
        if not 0 < code <= PAGE:
            errors.append(f"program {path} code_size out of range: {code}")
        if not 0 <= fsz <= PAGE:
            errors.append(f"program {path} data_filesz out of range: {fsz}")
        if not fsz <= msz <= PAGE:
            errors.append(f"program {path} data_memsz out of range: {fsz}/{msz}")
    # Every conformance program exits 0 (internal mismatches exit nonzero
    # fail-fast, so a wrong exit is itself the detection).
    if len(evidence.exits) != 7 or any(code != 0 for _, code in evidence.exits):
        errors.append(f"exit rows differ: {evidence.exits}")
    # Class evidence exact: the kernel-observed write payloads, in order,
    # must equal the golden (18 class lines with CRLF, the bare hello,
    # the .rl prints). The guest prints actuals, so any behavioral
    # deviation mismatches here; extra or missing rows (e.g. a removed
    # validation issuing the syscall anyway) mismatch too.
    got = [(s, f, l, n, p) for s, f, l, n, p in writes]
    want = [(0, 1, len(pay), len(pay), pay) for pay in WRITE_PAYLOADS]
    if got != want:
        errors.append(f"kernel write rows differ: {[(s, f, l, n) for s, f, l, n, _ in writes]}")
    if evidence.balanced != 7:
        errors.append(f"want 7 accounting lines, got {evidence.balanced}")
    return errors
