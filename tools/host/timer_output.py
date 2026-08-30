"""Strict Stage 3 transcript validation; Stage 2 fatal variants stop earlier."""

from exception_output import validate_exception_output

TIMER_OUTPUT = (
    b"[IRQ] controller initialized\r\n"
    b"[TIMER] initialized\r\n"
    b"[TIMER] clock_hz=1193182 divisor=11932 mode=2\r\n"
    b"[TEST] waiting for timer interrupts\r\n"
    b"[TIMER] tick=1\r\n"
    b"[TIMER] tick=2\r\n"
    b"[TIMER] tick=3\r\n"
    b"[TEST] timer interrupt handling verified\r\n"
)
EXCEPTION_END = b"[TEST] exception handling verified\r\n"


def validate_boot_output(output: bytes, vector: int = 3) -> list[str]:
    if vector != 3:
        return validate_exception_output(output, vector)
    prefix, separator, timer = output.partition(EXCEPTION_END)
    errors = validate_exception_output(prefix + separator, vector)
    if timer != TIMER_OUTPUT:
        missing = [line.decode("ascii") for line in TIMER_OUTPUT.splitlines(keepends=True)
                   if line not in timer]
        errors.append("Timer output missing: " + ", ".join(line.strip() for line in missing)
                      if missing else "Timer output has invalid order, count, or trailing data")
    return errors
