"""Strict scheduler evidence parser. Synthetic fixtures are not hardware evidence."""
import re

SCHED_START = b"[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage7 kernel execution\r\n[SCHED] self-test started\r\n"
SCHED_END = b"[TEST] scheduler self-test passed\r\n"
STEPS = (
    "[SCHED] stacks ownership, guard mappings and reuse verified",
    "[SCHED] real OOM and mapping rollback verified",
    "[SCHED] lifecycle exhaustion, stale IDs and repeated reap verified",
)
# Intentionally synthetic parser fixture. No guest consults this byte string.
SCHED_GOOD = (SCHED_START +
    (STEPS[0] + "\r\n" + STEPS[1] + "\r\n" +
     "[SCHED] IRQ nesting and lock contracts verified\r\n" + STEPS[2] + "\r\n" +
     "[SCHED] non-yielding timer probe started\r\n").encode() +
    b"".join(f"[SCHED] worker={i+1} preemptions=6 dispatches=6 rsp={0xffffe00000004f00+i*20480} irq_rsp={0xffffe00000004f00+i*20480} irq_rip=35000\r\n".encode() for i in range(3)) +
    b"[SCHED] two-runnable ticks=24 switches=24\r\n[SCHED] single-runnable timer return verified\r\n" +
    b"[SCHED] final allocated_bytes=106496 free_bytes=937984 table_pages=10\r\n" +
    SCHED_END + b"[TEST] preemptions=48 runs=1200000\r\n")


def parse_sched_output(output: bytes, heap: dict | None = None) -> dict:
    if len(output) > 16384:
        raise ValueError("SCHED output too large")
    lines = iter(output.decode("ascii").splitlines(keepends=True))

    def exact(s):
        if next(lines, None) != s + "\r\n":
            raise ValueError("SCHED missing/out-of-order line: " + s)

    def numbers(pattern):
        match = re.fullmatch(pattern + r"\r\n", next(lines, ""))
        if not match:
            raise ValueError("SCHED invalid numeric record")
        values = tuple(int(n) for n in match.groups())
        if any(n >= 1 << 64 for n in values):
            raise ValueError("SCHED numeric overflow")
        return values

    for line in SCHED_START.decode().splitlines():
        exact(line)
    exact(STEPS[0]); exact(STEPS[1])
    exact("[SCHED] IRQ nesting and lock contracts verified")
    exact(STEPS[2]); exact("[SCHED] non-yielding timer probe started")
    workers = []
    slots = set()
    for i in range(1, 4):
        worker, preempted, dispatched, rsp, irq_rsp, rip = numbers(
            r"\[SCHED\] worker=(\d+) preemptions=(\d+) dispatches=(\d+) rsp=(\d+) irq_rsp=(\d+) irq_rip=(\d+)")
        # The workers occupy fresh slots today, but the stack check must not
        # hardcode slot geometry: any valid worker slot is acceptable.
        bases = [0xffffe00000000000 + slot * 20480 for slot in range(8)]
        slot = next((n for n, base in enumerate(bases)
                     if base + 4096 <= rsp < base + 20480), None)
        irq_slot = next((n for n, base in enumerate(bases)
                         if base + 4096 <= irq_rsp < base + 20480), None)
        if (worker != i or preempted < 2 or dispatched < 2 or preempted > 24 or dispatched > 24 or
                slot is None or irq_slot != slot or slot in slots or
                not (rsp - 8 <= irq_rsp <= rsp) or not rip):
            raise ValueError("SCHED invalid hardware worker evidence")
        slots.add(slot)
        workers.append(dict(id=worker, preemptions=preempted, dispatches=dispatched,
                            rsp=rsp, irq_rsp=irq_rsp, irq_rip=rip))
    exact("[SCHED] two-runnable ticks=24 switches=24")
    exact("[SCHED] single-runnable timer return verified")
    allocated, free, tables = numbers(r"\[SCHED\] final allocated_bytes=(\d+) free_bytes=(\d+) table_pages=(\d+)")
    if allocated != 106496 or free % 4096 or tables != 10:
        raise ValueError("SCHED final accounting invalid")
    if heap and (allocated != heap["allocated"] or free != heap["free"] or tables != heap["tables"]):
        raise ValueError("SCHED leaked resources since heap test")
    exact("[TEST] scheduler self-test passed")
    preemptions, runs = numbers(r"\[TEST\] preemptions=(\d+) runs=(\d+)")
    if preemptions != 48 or runs < 3 or sum(w["preemptions"] for w in workers) > 24:
        raise ValueError("SCHED inconsistent progress")
    if next(lines, None) is not None:
        raise ValueError("SCHED unexpected trailing records")
    return dict(preemptions=preemptions, runs=runs, workers=workers,
                allocated=allocated, free=free, tables=tables)


def validate_sched_output(output: bytes, heap: dict | None = None) -> list[str]:
    try:
        parse_sched_output(output, heap)
    except (ValueError, UnicodeDecodeError) as error:
        return [str(error)]
    return []
