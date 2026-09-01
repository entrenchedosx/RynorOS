"""Strict Stage 10 records for the basic kernel runtime.

The bounded string/buffer tests emit fixed, host-recomputed formatted outputs;
the runtime services emit an FNV-1a 64 digest fold per worker thread. Every
value here is recomputed independently from the W_INPUT literals and FNV/fold
constants. Serial verification alone cannot reject an accurate canned result;
mandatory physical worker/IRQ evidence provides the additional execution gate.
Accounting baseline flows in from the Stage 9 display state."""

import re
import struct

RUNTIME_START = (b"[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage10 basic kernel runtime\r\n"
                 b"[RUNTIME] self-test started\r\n")
RUNTIME_END = b"[TEST] runtime api verified\r\n"

WORKERS = 7
ROUNDS = 40
FOLD_MULT = 131
FNV_BASIS = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
FNV_MASK = (1 << 64) - 1

# Mirrors the kernel W_INPUT table. The host re-derives every worker acc.
W_INPUT = ("w0:data0123", "w1:xyz987", "w2:kernelruntime", "w3:0123456789",
           "w4:abcdefghiz", "w5:rhinoS10SERV", "w6:q")

STR_FMT = '[STR] fmt0="rynor 42 2a" fmt1="334" fmt2="FF"'
STR_END = "[STR] strings, bounds, overlap and formatting verified (synthetic)"
BUF_WRAP = '[BUF] wrap="cdef"'
BUF_END = "[BUF] buffers, wrap, capacity and bounds verified (synthetic)"
SVC_END = "[SVC] digest, uppercase and count services verified (synthetic)"
DISPATCH_END = "[RUNTIME] dispatch rejects invalid, overlapping and undersized requests"
WORKER_DONE = "[RUNTIME] worker digests and round counts verified under preemption"


def fnv1a(data: bytes) -> int:
    h = FNV_BASIS
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & FNV_MASK
    return h


def worker_acc(inp: str) -> int:
    d = fnv1a(inp.encode("ascii"))
    acc = 0
    for _ in range(ROUNDS):
        acc = (acc * FOLD_MULT + d) & FNV_MASK
    return acc


def total_fold() -> int:
    return sum(worker_acc(i) for i in W_INPUT) & FNV_MASK


def fixture(previous: dict | None = None, ram_mib: int = 4) -> bytes:
    """Synthetic Stage 10 transcript. Reuses the Stage 9 display accounting for
    the final line so a composed repository fixture stays internally coherent."""
    accounting = previous or dict(allocated=122880, free=ram_mib * 1024 * 1024 - 122880, tables=14)
    parts = [RUNTIME_START,
             (STR_FMT + "\r\n").encode(), (STR_END + "\r\n").encode(),
             (BUF_WRAP + "\r\n").encode(), (BUF_END + "\r\n").encode(),
             (SVC_END + "\r\n").encode(), (DISPATCH_END + "\r\n").encode()]
    for i in range(WORKERS):
        parts.append(("[RUNTIME] worker=%d acc=0x%X rounds=%d\r\n" % (i, worker_acc(W_INPUT[i]), ROUNDS)).encode())
    parts.append(("[RUNTIME] total=%d\r\n" % total_fold()).encode())
    parts.append((WORKER_DONE + "\r\n").encode())
    parts.append(("[RUNTIME] final allocated_bytes=%d free_bytes=%d table_pages=%d\r\n"
                  % (accounting["allocated"], accounting["free"], accounting["tables"])).encode())
    parts.append(RUNTIME_END)
    return b"".join(parts)


# Accounting used in the synthetic repository fixture, matching the Stage 9
# DISPLAY_GOOD end state so full-boot composition stays internally coherent.
DISPLAY_ACCOUNTING = dict(allocated=122880, free=921600, tables=14)
RUNTIME_GOOD = fixture(DISPLAY_ACCOUNTING)


def _exact(lines, value: str) -> None:
    if next(lines, None) != value + "\r\n":
        raise ValueError("Runtime missing/out-of-order line: " + value)


_RE_WORKER = re.compile(r"\[RUNTIME\] worker=(\d+) acc=0x([0-9A-F]+) rounds=(\d+)\r\n")
_RE_FINAL = re.compile(r"\[RUNTIME\] final allocated_bytes=(\d+) free_bytes=(\d+) table_pages=(\d+)\r\n")
_RE_TOTAL = re.compile(r"\[RUNTIME\] total=(\d+)\r\n")


def parse_runtime_output(output: bytes, previous: dict | None = None) -> dict:
    if len(output) > 32768:
        raise ValueError("Runtime output too large")
    lines = iter(output.decode("ascii").splitlines(keepends=True))
    for line in RUNTIME_START.decode().splitlines():
        _exact(lines, line)
    for probe in (STR_FMT, STR_END, BUF_WRAP, BUF_END, SVC_END, DISPATCH_END):
        _exact(lines, probe)
    seen = set()
    for i in range(WORKERS):
        m = _RE_WORKER.fullmatch(next(lines, ""))
        if not m:
            raise ValueError("Runtime invalid worker record")
        idx, acc, rounds = int(m.group(1)), int(m.group(2), 16), int(m.group(3))
        if idx != i or idx in seen or rounds != ROUNDS or acc != worker_acc(W_INPUT[idx]):
            raise ValueError("Runtime worker digest/fold mismatch")
        seen.add(idx)
    tm = _RE_TOTAL.fullmatch(next(lines, ""))
    if not tm or int(tm.group(1)) != total_fold():
        raise ValueError("Runtime total fold mismatch")
    _exact(lines, WORKER_DONE)
    fm = _RE_FINAL.fullmatch(next(lines, ""))
    if not fm:
        raise ValueError("Runtime final accounting invalid")
    allocated, free, tables = (int(v) for v in fm.groups())
    for v in (allocated, free):
        if v % 4096:
            raise ValueError("Runtime accounting not page aligned")
    if previous:
        if (allocated, free, tables) != (previous["allocated"], previous["free"], previous["tables"]):
            raise ValueError("Runtime accounting does not match display baseline")
    _exact(lines, RUNTIME_END.decode("ascii").rstrip("\r\n"))
    if next(lines, None) is not None:
        raise ValueError("Runtime unexpected trailing records")
    return dict(allocated=allocated, free=free, tables=tables, total=total_fold())


def validate_runtime_output(output: bytes, previous: dict | None = None) -> list[str]:
    try:
        parse_runtime_output(output, previous)
    except (ValueError, UnicodeDecodeError) as error:
        return [str(error)]
    return []


def verify_runtime_memory(data: bytes, service_start: int, service_end: int) -> None:
    """Physical guest records, not serial assertions. RIPs come from Stage 7
    hardware IRQ frames; results from actual worker-owned stacks. This detects
    reviewed mutations, not an adversary forging both evidence and verifier."""
    if len(data) != WORKERS * 9 * 8:
        raise ValueError('runtime execution evidence: incorrect record size')
    ids, stacks = set(), set()
    for slot, record in enumerate(struct.iter_unpack('<9Q', data)):
        acc, rounds, tid, stack, preemptions, rip, rsp, probe, attempts = record
        if rounds != ROUNDS or acc != worker_acc(W_INPUT[slot]):
            raise ValueError('runtime execution evidence: worker result missing/incorrect')
        if tid <= 1 or tid in ids:
            raise ValueError('runtime execution evidence: invalid worker identity')
        offset = stack - 0xffffe00000000000
        if offset < 0 or offset % (5 * 4096) or offset >= 8 * 5 * 4096 or stack in stacks:
            raise ValueError('runtime execution evidence: invalid worker stack')
        if not (stack + 4096 <= rsp < stack + 5 * 4096 and service_start <= rip < service_end):
            raise ValueError('runtime execution evidence: IRQ outside service/owned stack')
        expected = fnv1a(bytes((i + slot) & 255 for i in range(4096)))
        if probe != expected or not 2 <= preemptions <= attempts <= 131072:
            raise ValueError('runtime execution evidence: preemption/probe result missing')
        ids.add(tid)
        stacks.add(stack)


def verify_runtime_trace(data: bytes, trace: str, start: int, end: int) -> None:
    """Independent QEMU CPU interrupt records corroborate guest-saved state.
    Hardware (i=0) IRQ0, ring 0, normal kernel selectors, distinct event IDs.
    A physical-memory forgery alone cannot substitute for CPU execution."""
    verify_runtime_memory(data, start, end)
    pattern = (r'^\s*(\d+): v=20 e=0000 i=0 cpl=0 IP=0008:([0-9a-f]{16}) '
               r'pc=([0-9a-f]{16}) SP=0010:([0-9a-f]{16})\b')
    events = []
    for m in re.finditer(pattern, trace, re.MULTILINE):
        seq, rip, pc, rsp = int(m[1]), int(m[2], 16), int(m[3], 16), int(m[4], 16)
        if start <= rip < end and rip == pc:
            events.append((seq, rip, rsp))
    if len({e[0] for e in events}) != len(events):
        raise ValueError('runtime execution evidence: duplicate CPU IRQ records')
    for row in struct.iter_unpack('<9Q', data):
        stack, required, rip, rsp = row[3:7]
        matching = [(ip, sp) for _, ip, sp in events if stack + 4096 <= sp < stack + 5 * 4096]
        if len(matching) < required or (rip, rsp) not in matching:
            raise ValueError('runtime execution evidence: CPU IRQ trace does not corroborate worker')
