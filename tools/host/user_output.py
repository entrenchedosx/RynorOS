"""Serial validator for the Stage 18a protected-userspace section.

The guest self-test prints a trailing [USER] section with deterministic
lifecycle, gate, fault-matrix, and preemption evidence. This module parses
that section and checks exact values where the design locks them (exit
codes, fault vectors/errors/CR2, tick/switch/preemption counts, mapping
VAs/perms), presence and cross-consistency where timing or allocation
order is involved (physical addresses, GPR spill counters).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CODE_BASE = 0x400000
DATA_BASE = 0x600000
STACK_PAGE = 0x7FF000
STACK_TOP = 0x800000
PAGE = 4096

_INIT_RE = re.compile(rb"^\[USER\] initialized$")
_SMP_RE = re.compile(rb"^\[USER\] smep=([01]) smap=([01])$")
_START_RE = re.compile(rb"^\[USER\] self-test started$")
_CREATE_RE = re.compile(rb"^\[USER\] create slot=(\d+) code_size=(\d+) tables=(\d+)$")
_MAP_RE = re.compile(rb"^\[USER\] map slot=(\d+) kind=(code|data|stack) "
                     rb"va=(0x[0-9a-f]{16}) pa=(0x[0-9a-f]{16}) perm=(rx|rw)$")
_ADMIT_RE = re.compile(rb"^\[USER\] admission rejected slots=2$")
_DESTROY_RE = re.compile(rb"^\[USER\] destroy slot=(\d+)$")
_EXIT_RE = re.compile(rb"^\[USER\] exit slot=(\d+) code=(\d+)$")
_YIELD_RE = re.compile(rb"^\[USER\] yield slot=(\d+) count=(\d+)$")
_FAULT_RE = re.compile(rb"^\[USER\] fault slot=(\d+) vector=(\d+) error=(0x[0-9a-f]{16}) "
                       rb"rip=(0x[0-9a-f]{16}) cr2=(0x[0-9a-f]{16})$")
_PREEMPT_RE = re.compile(rb"^\[USER\] preempt slot=(\d+) preemptions=(\d+)$")
_WPREEMPT_RE = re.compile(rb"^\[USER\] preempt worker preemptions=(\d+)$")
_CPL3_RE = re.compile(rb"^\[USER\] cpl3_ticks=(\d+) ticks=(\d+) switches=(\d+)$")
_GPRS_RE = re.compile(rb"^\[USER\] gprs stable slot=(\d+) counter=(\d+)$")
_SYSTEM_RE = re.compile(rb"^\[SYSTEM\] RynorOS \S+ \| Rynorkernel \| stage18a protected userspace$")
_TEST_RE = re.compile(rb"^\[TEST\] userspace self-test passed$")

# Exact architectural expectations: (vector, error, rip_rule, cr2_rule).
# rip_rule: None = inside the faulting code page; int = exact RIP;
#   "CR2" = RIP must equal the fault CR2 (instruction-fetch faults).
# cr2_rule: None = meaningless for this fault (stale CR2, unchecked);
#   int = exact CR2; "KERNEL" = canonical supervisor address outside the
#   user range (guest asserts the exact landmark; the host asserts the
#   violation shape so link-address shifts never desync the two).
_FAULT_ROWS = [
    (6, 0x0, None, None),
    (14, 0x05, None, 0x8000),
    (14, 0x04, None, 0xFFFFFFFF80000000),
    (14, 0x07, None, CODE_BASE),
    (14, 0x15, DATA_BASE, DATA_BASE),
    (13, 0x0, None, None),
    (14, 0x04, None, 0x0),
    (14, 0x05, None, "KERNEL"),
    (14, 0x07, None, "KERNEL"),
    (14, 0x15, "CR2", "KERNEL"),
    (14, 0x15, STACK_PAGE, STACK_PAGE),
    (13, 0x0, None, None),
    (13, 0x10, None, None),
    (13, 0x40, None, None),
    # TI-bit selector with explicitly invalid LDT: canonical
    # #GP(index 3, LDT) before any memory access.
    (13, 0x1C, None, None),
    (13, 0x08, None, None),
    (13, 0x18, None, None),
    (13, 0x20, None, None),
    (0, 0x0, None, None),
    (6, 0x0, None, None),
    # Noncanonical-RSP push: #SS on silicon, #GP(0) on QEMU TCG.
    # Pinned to the emulator-observed value (see user-test.c note).
    (13, 0x0, None, None),
    (14, 0x07, None, "KERNEL"),
    # Forged returns to kernel CS: inward return must fault.
    (13, 0x08, None, None),
    (13, 0x08, None, None),
    # MSR access is CPL0-only.
    (13, 0x0, None, None),
    (128, 0x99, None, None),
]

_BALANCED = b"[USER] accounting balanced"
_VERIFIED = b"[USER] user verified"
# Final guest line of the self-test, with its CRLF terminator. The host boot
# loop uses this as the completion signal for normal boots: the structural
# validator accepts a valid prefix with no userspace section yet, so only
# this marker proves the guest finished.
VERIFIED_LINE = _VERIFIED + b"\r\n"
_MARKERS = (
    b"[USER] lifecycle verified",
    b"[USER] oom rollback verified",
    b"[USER] gate verified",
    b"[USER] faults verified",
    b"[USER] preemption verified",
)


@dataclass
class UserEvidence:
    smep: int | None = None
    smap: int | None = None
    creates: list = field(default_factory=list)  # (slot, size, tables)
    maps: list = field(default_factory=list)  # (slot, kind, va, pa, perm)
    destroys: list = field(default_factory=list)  # slot
    exits: list = field(default_factory=list)  # (slot, code)
    yields: list = field(default_factory=list)  # (slot, count)
    faults: list = field(default_factory=list)  # (slot, vector, error, rip, cr2)
    preempts: list = field(default_factory=list)  # (slot, count)
    worker_preempts: list = field(default_factory=list)
    cpl3: list = field(default_factory=list)  # (cpl3, ticks, switches)
    gprs: list = field(default_factory=list)  # (slot, counter)
    balanced: int = 0
    markers: set = field(default_factory=set)
    failures: list = field(default_factory=list)
    codesizes: dict = field(default_factory=dict)  # slot -> latest code_size


def parse_serial(observed: bytes) -> UserEvidence:
    evidence = UserEvidence()
    for raw in observed.split(b"\r\n"):
        line = raw.strip()
        if line.startswith(b"[USER] failure="):
            evidence.failures.append(line.decode("ascii", "replace"))
            continue
        if _INIT_RE.match(line) or _START_RE.match(line):
            continue
        match = _SMP_RE.match(line)
        if match:
            evidence.smep, evidence.smap = int(match.group(1)), int(match.group(2))
            continue
        match = _CREATE_RE.match(line)
        if match:
            row = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            evidence.creates.append(row)
            evidence.codesizes[row[0]] = row[1]
            continue
        match = _MAP_RE.match(line)
        if match:
            evidence.maps.append((int(match.group(1)), match.group(2).decode("ascii"),
                                  int(match.group(3), 16), int(match.group(4), 16),
                                  match.group(5).decode("ascii")))
            continue
        if _ADMIT_RE.match(line):
            continue
        match = _DESTROY_RE.match(line)
        if match:
            evidence.destroys.append(int(match.group(1)))
            continue
        match = _EXIT_RE.match(line)
        if match:
            evidence.exits.append((int(match.group(1)), int(match.group(2))))
            continue
        match = _YIELD_RE.match(line)
        if match:
            evidence.yields.append((int(match.group(1)), int(match.group(2))))
            continue
        match = _FAULT_RE.match(line)
        if match:
            evidence.faults.append((int(match.group(1)), int(match.group(2)),
                                    int(match.group(3), 16), int(match.group(4), 16),
                                    int(match.group(5), 16)))
            continue
        match = _PREEMPT_RE.match(line)
        if match:
            evidence.preempts.append((int(match.group(1)), int(match.group(2))))
            continue
        match = _WPREEMPT_RE.match(line)
        if match:
            evidence.worker_preempts.append(int(match.group(1)))
            continue
        match = _CPL3_RE.match(line)
        if match:
            evidence.cpl3.append((int(match.group(1)), int(match.group(2)),
                                  int(match.group(3))))
            continue
        match = _GPRS_RE.match(line)
        if match:
            evidence.gprs.append((int(match.group(1)), int(match.group(2))))
            continue
        if line == _BALANCED:
            evidence.balanced += 1
        elif line in _MARKERS:
            evidence.markers.add(line)
    return evidence


def split_user_sections(tail: bytes) -> tuple:
    """Split a post-filesystem tail into (pre, user_part).

    [USER] lines must form one contiguous run at the end; anything else
    is an error only if it is neither empty nor a non-USER line (other
    sections' validity is checked by their own validators, not here).
    """
    lines = tail.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    first = None
    for index, line in enumerate(lines):
        if line.strip().startswith(b"[USER] "):
            first = index
            break
    if first is None:
        return tail, b""
    return b"\r\n".join(lines[:first]) + (b"\r\n" if first else b""), \
        b"\r\n".join(lines[first:]) + b"\r\n"


_ALLOWED = (_SMP_RE, _CREATE_RE, _MAP_RE, _ADMIT_RE, _DESTROY_RE, _EXIT_RE,
            _YIELD_RE, _FAULT_RE, _PREEMPT_RE, _WPREEMPT_RE,
            _CPL3_RE, _GPRS_RE)


def validate_user_section(part: bytes) -> list:
    """Structural check for the optional trailing userspace section."""
    if part == b"":
        return []
    lines = part.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines or not lines[0].strip().startswith(b"[USER] initialized"):
        return ["userspace section must start with [USER] initialized"]
    for line in lines:
        text = line.strip()
        if text in (b"[USER] initialized", b"[USER] self-test started",
                    _BALANCED, _VERIFIED) or text in _MARKERS:
            continue
        if _SYSTEM_RE.match(text) or _TEST_RE.match(text):
            continue
        if text.startswith(b"[USER] failure="):
            return [f"guest failure: {text.decode('ascii', 'replace')}"]
        if text.startswith(b"[USER] ") and any(rx.match(text) for rx in _ALLOWED):
            continue
        return [f"unexpected userspace output: {text[:60]!r}"]
    if lines[-1].strip() != _VERIFIED:
        return ["userspace section missing user verified"]
    return []


def validate(evidence: UserEvidence) -> list:
    """Compare guest evidence against Stage 18a expectations. [] valid."""
    errors = []
    if evidence.failures:
        errors.append(f"guest failures: {evidence.failures}")
    if evidence.smep is None or evidence.smap is None:
        errors.append("missing smep/smap probe line")
    if len(evidence.creates) != 33:
        errors.append(f"want 33 creates, got {len(evidence.creates)}")
    else:
        tables = {row[2] for row in evidence.creates}
        if tables != {6}:
            errors.append(f"create table counts differ: {sorted(tables)}")
        for _, size, _ in evidence.creates:
            if not 0 < size <= PAGE:
                errors.append(f"create code_size out of range: {size}")
        if sorted(row[0] for row in evidence.creates) != \
                sorted(row for row in evidence.destroys):
            errors.append("create/destroy slot multisets differ")
    if len(evidence.destroys) != 33:
        errors.append(f"want 33 destroys, got {len(evidence.destroys)}")
    want_maps = {(0, "code", CODE_BASE, "rx"), (0, "data", DATA_BASE, "rw"),
                 (0, "stack", STACK_PAGE, "rw")}
    got_maps = {(slot, kind, va, perm) for slot, kind, va, _, perm in evidence.maps}
    if len(evidence.maps) != 3:
        errors.append(f"want 3 map rows, got {len(evidence.maps)}")
    elif got_maps != want_maps:
        errors.append(f"map rows differ: {sorted(got_maps)}")
    else:
        pas = [pa for _, _, _, pa, _ in evidence.maps]
        if any(pa & (PAGE - 1) for pa in pas):
            errors.append("map physical address misaligned")
        if len(set(pas)) != 3:
            errors.append("map physical addresses not distinct")
    if sorted(evidence.exits) != [(0, 7), (0, 9), (0, 42), (1, 7)]:
        errors.append(f"exit rows differ: {sorted(evidence.exits)}")
    if evidence.yields != [(0, 1)]:
        errors.append(f"yield rows differ: {evidence.yields}")
    if len(evidence.faults) != len(_FAULT_ROWS):
        errors.append(f"want {len(_FAULT_ROWS)} faults, got {len(evidence.faults)}")
    else:
        for index, ((slot, vector, error, rip, cr2), (want_vector, want_error, want_rip, want_cr2)) \
                in enumerate(zip(evidence.faults, _FAULT_ROWS)):
            if slot != 0 or vector != want_vector or error != want_error:
                errors.append(f"fault {index} identity differs: "
                              f"slot={slot} vector={vector} error={error:#x}")
                continue
            if want_cr2 == "KERNEL":
                if cr2 == 0 or (cr2 >> 48) not in (0x0, 0xFFFF) or \
                        CODE_BASE <= cr2 < STACK_TOP:
                    errors.append(f"fault {index} cr2 not a supervisor address: {cr2:#x}")
            elif want_cr2 is not None and cr2 != want_cr2:
                errors.append(f"fault {index} cr2 differs: {cr2:#x}")
            if want_rip == "CR2":
                if rip != cr2:
                    errors.append(f"fetch fault {index} rip/cr2 differ: "
                                  f"{rip:#x}/{cr2:#x}")
            elif want_rip is not None:
                if rip != want_rip:
                    errors.append(f"fault {index} rip differs: {rip:#x}")
            else:
                size = evidence.codesizes.get(slot, 0)
                if not CODE_BASE <= rip < CODE_BASE + size:
                    errors.append(f"fault {index} rip outside code: {rip:#x}")
    if evidence.preempts != [(0, 13)]:
        errors.append(f"preempt rows differ: {evidence.preempts}")
    if evidence.worker_preempts != [13]:
        errors.append(f"worker preempt rows differ: {evidence.worker_preempts}")
    if evidence.cpl3 != [(26, 26, 28)]:
        errors.append(f"cpl3 rows differ: {evidence.cpl3}")
    if len(evidence.gprs) != 2:
        errors.append(f"want 2 gprs rows, got {len(evidence.gprs)}")
    else:
        if any(counter <= 0 for _, counter in evidence.gprs):
            errors.append(f"gprs counters not positive: {evidence.gprs}")
        if sorted(slot for slot, _ in evidence.gprs) != [0, 1]:
            errors.append(f"gprs slots differ: {evidence.gprs}")
    if evidence.balanced != 7:
        errors.append(f"want 7 accounting lines, got {evidence.balanced}")
    missing = [marker.decode("ascii") for marker in _MARKERS
               if marker not in evidence.markers]
    if missing:
        errors.append(f"missing markers: {missing}")
    return errors
