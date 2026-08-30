"""Stage 4 requires CPU diagnostics, real PMM results, then timer IRQs."""

from exception_output import validate_exception_output
from timer_output import EXCEPTION_END, TIMER_OUTPUT
from pmm_output import PMM_END, validate_pmm_output

POST_IRQ = b"[TEST] PMM post-IRQ accounting verified\r\n"


def validate_boot_output(output: bytes, vector: int = 3) -> list[str]:
    if vector != 3:
        return validate_exception_output(output, vector)
    cpu, end, remaining = output.partition(EXCEPTION_END)
    errors = validate_exception_output(cpu + end, vector)
    pmm, end, timer = remaining.partition(PMM_END)
    errors.extend(validate_pmm_output(pmm + end))
    expected_timer = TIMER_OUTPUT + POST_IRQ
    if timer != expected_timer:
        missing = [line.decode("ascii").strip() for line in expected_timer.splitlines(keepends=True)
                   if line not in timer]
        errors.append("Timer output missing: " + ", ".join(missing) if missing else
                      "Timer output order/count/trailing data invalid")
    return errors
