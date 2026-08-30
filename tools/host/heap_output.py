"""Strict validation of real kernel-heap serial records; never an execution substitute."""
import re

HEAP_BASE = 0xFFFFC00000000000
HEAP_ARENA_BYTES = 65536
HEAP_END = b"[TEST] HEAP self-test passed\r\n"


def parse_heap_output(output: bytes, vm: dict | None = None) -> dict:
    if len(output) > 16384:
        raise ValueError("HEAP output too large")
    lines = iter(output.decode("ascii").splitlines(keepends=True))
    values = {}

    def exact(text):
        if next(lines, None) != text + "\r\n":
            raise ValueError("HEAP missing/out-of-order line: " + text)

    def numeric(pattern):
        match = re.fullmatch(pattern + r"\r\n", next(lines, ""))
        if not match:
            raise ValueError("HEAP invalid numeric record: " + pattern)
        result = [int(n) for n in match.groups()]
        if any(n >= 1 << 64 for n in result):
            raise ValueError("HEAP value outside uint64")
        return result

    def hexaddr(pattern):
        match = re.fullmatch(pattern + r"\r\n", next(lines, ""))
        if not match:
            raise ValueError("HEAP invalid address record: " + pattern)
        addr = [int(n, 16) for n in match.groups()]
        if any(n >= 1 << 64 for n in addr):
            raise ValueError("HEAP address outside uint64")
        return addr

    arena, mapped = numeric(r"\[HEAP\] initialize arena=(\d+) mapped=(\d+)")
    if arena != HEAP_ARENA_BYTES or mapped != arena:
        raise ValueError("HEAP arena/mapped size invalid")
    free_blocks, = numeric(r"\[HEAP\] free_blocks=(\d+)")
    if free_blocks != 1:
        raise ValueError("HEAP initial partition must hold exactly one block")
    exact("[TEST] HEAP initialization rollback verified")
    exact("[TEST] HEAP adversarial boundaries and corruption verified")
    small, mid, align4096 = hexaddr(r"\[HEAP\] small=0x([0-9a-f]{16}) mid=0x([0-9a-f]{16}) align4096=0x([0-9a-f]{16})")
    lo, hi = HEAP_BASE + 16, HEAP_BASE + HEAP_ARENA_BYTES
    if not all(lo <= a < hi for a in (small, mid, align4096)) or small % 8 or mid % 16 or align4096 % 4096:
        raise ValueError("HEAP allocations must be in-arena and aligned")
    if len({small, mid, align4096}) != 3:
        raise ValueError("HEAP allocations must be distinct blocks")
    exact("[TEST] HEAP boundary and alignment verified")
    coalesced_free, coalesced_used = numeric(r"\[HEAP\] coalesced free_blocks=(\d+) used=(\d+)")
    if coalesced_free != 1 or coalesced_used != 0:
        raise ValueError("HEAP did not fully coalesce after frees")
    exact("[TEST] HEAP coalescing verified")
    exact("[TEST] HEAP invalid calls rejected")
    stress, oom = numeric(r"\[HEAP\] stress blocks=(\d+) oom=(\d+)")
    if oom != 1 or stress != HEAP_ARENA_BYTES // (256 + 32):
        raise ValueError("HEAP stress must fill then report out-of-memory")
    exact("[TEST] HEAP stress and OOM verified")
    allocated, free, tables = numeric(r"\[HEAP\] PMM allocated_bytes=(\d+) free_bytes=(\d+) table_pages=(\d+)")
    if tables != 10 or allocated != (16 + tables) * 4096 or free % 4096:
        raise ValueError("HEAP PMM/table accounting invalid")
    if vm and (allocated + free != vm["allocated"] + vm["free"] or
               allocated - vm["allocated"] != (16 + 3) * 4096):
        raise ValueError("HEAP ownership disagrees with VM/PMM")
    final_used, final_mapped = numeric(r"\[HEAP\] final used=(\d+) mapped=(\d+)")
    if final_used != 0 or final_mapped != HEAP_ARENA_BYTES:
        raise ValueError("HEAP final accounting invalid")
    exact("[TEST] HEAP self-test passed")
    if next(lines, None) is not None:
        raise ValueError("HEAP unexpected trailing records")
    values.update(arena=arena, small=small, mid=mid, align4096=align4096,
                  stress=stress, final_used=final_used, final_mapped=final_mapped,
                  allocated=allocated, free=free, tables=tables)
    return values


def validate_heap_output(output: bytes, vm: dict | None = None) -> list[str]:
    try:
        parse_heap_output(output, vm)
    except (ValueError, UnicodeDecodeError) as error:
        return [str(error)]
    return []
