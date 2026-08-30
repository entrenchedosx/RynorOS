"""Validate captured serial diagnostics; never a source of emulator output."""

import re

BOOT_PREFIX = b"Rynorkernel booted.\r\nRynorOS 0.1.0 | x86_64 | stage1\r\n"
VECTOR_NAMES = {0: "divide_error", 1: "debug", 3: "breakpoint", 6: "invalid_opcode",
                13: "general_protection", 14: "page_fault"}
GPR_ROWS = (("rax", "rbx", "rcx", "rdx"), ("rbp", "rsi", "rdi", "r8"),
            ("r9", "r10", "r11", "r12"), ("r13", "r14", "r15"))


def diagnostic_pattern(vector: int) -> str:
    prefix = BOOT_PREFIX.decode("ascii") + (
        "[CPU] GDT initialized\r\n[CPU] IDT initialized\r\n"
        "[TEST] triggering controlled exception\r\n"
        f"[EXCEPTION] vector={vector:02d} name={VECTOR_NAMES[vector]} "
        f"error_source={'cpu' if vector in (13, 14) else 'synthetic'}"
    )
    def field(name):
        return rf" {name}=0x(?P<{name}>[0-9a-f]{{16}})"
    pattern = re.escape(prefix) + field("error") + "\r\n" + re.escape("[STATE]")
    pattern += "".join(field(name) for name in ("rip", "cs", "rflags", "rsp", "ss")) + "\r\n"
    for row in GPR_ROWS:
        pattern += re.escape("[GPR]") + "".join(field(name) for name in row) + "\r\n"
    if vector == 14:
        pattern += re.escape("[PAGE]") + field("cr2") + "\r\n"
    action = "resume" if vector == 3 else "halt"
    return pattern + re.escape(f"[EXCEPTION] action={action}\r\n[TEST] exception handling verified\r\n")


def validate_exception_output(output: bytes, vector: int = 3) -> list[str]:
    if type(vector) is not int or vector not in VECTOR_NAMES:
        return ["unsupported expected exception vector"]
    try:
        text = output.decode("ascii")
    except UnicodeDecodeError:
        return ["serial output is not ASCII"]
    match = re.fullmatch(diagnostic_pattern(vector), text)
    if match is None:
        errors = []
        for marker in (BOOT_PREFIX.decode("ascii"), "[CPU] GDT initialized\r\n",
                       "[CPU] IDT initialized\r\n", "[TEST] triggering controlled exception\r\n",
                       f"[EXCEPTION] vector={vector:02d} ", "[STATE]", "[GPR]",
                       "[TEST] exception handling verified\r\n"):
            if marker not in text:
                errors.append(f"missing marker: {marker.strip()!r}")
        return errors or ["diagnostic format/order/count or completion state is invalid"]
    state = {key: int(value, 16) for key, value in match.groupdict().items()}
    expected = {name: 0x101 + i for i, name in enumerate(name for row in GPR_ROWS for name in row)}
    expected.update(cs=8, ss=16, error=0x18 if vector == 13 else 0,
                    rflags=0x402 if vector == 3 else (0x102 if vector == 1 else 0x10002))
    if vector == 0:
        expected.update(rax=1, rcx=0, rdx=0)
    if vector == 13:
        expected.update(rax=0x18)
    if vector == 14:
        expected.update(cr2=0x200000)
    errors = [f"captured {name}: expected 0x{value:x}, got 0x{state[name]:x}"
              for name, value in expected.items() if state[name] != value]
    if not 0x8000 <= state["rip"] < 0x10000:
        errors.append("captured RIP is outside the linked kernel window")
    if not 0x7c000 <= state["rsp"] < 0x80000 or state["rsp"] % 8:
        errors.append("captured RSP is outside/aligned incorrectly for the fixed kernel stack")
    return errors
