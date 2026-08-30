"""Strict validation of the kernel scheduler serial records (Stage 7)."""

import re

SCHED_END = b"[TEST] scheduler self-test passed\r\n"
# Representative passing scheduler section used by synthetic parser fixtures.
SCHED_GOOD = SCHED_END + b"[TEST] preemptions=60 runs=1200000\r\n"


def parse_sched_output(output: bytes) -> dict:
    if len(output) > 16384:
        raise ValueError("SCHED output too large")
    lines = iter(output.decode("ascii").splitlines(keepends=True))

    def exact(text):
        if next(lines, None) != text + "\r\n":
            raise ValueError("SCHED missing/out-of-order line: " + text)

    exact("[TEST] scheduler self-test passed")
    match = re.fullmatch(r"\[TEST\] preemptions=(\d+) runs=(\d+)\r\n", next(lines, ""))
    if not match:
        raise ValueError("SCHED invalid statistics record")
    preemptions, runs = (int(n) for n in match.groups())
    if preemptions < 1:
        raise ValueError("SCHED must show at least one real preemption switch")
    if runs < 4:
        raise ValueError("SCHED workers never resumed repeatedly on their own stacks")
    if next(lines, None) is not None:
        raise ValueError("SCHED unexpected trailing records")
    return {"preemptions": preemptions, "runs": runs}


def validate_sched_output(output: bytes) -> list[str]:
    try:
        parse_sched_output(output)
    except (ValueError, UnicodeDecodeError) as error:
        return [str(error)]
    return []
