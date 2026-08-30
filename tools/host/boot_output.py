"""Require CPU diagnostics, real PMM, VM and heap results, then timer IRQs
and the Stage 7 preemptive scheduler, finally the post-IRQ accounting line."""

from exception_output import validate_exception_output
from timer_output import EXCEPTION_END, TIMER_OUTPUT
from pmm_output import PMM_END, validate_pmm_output, parse_pmm_output
from vm_output import VM_END, validate_vm_output, parse_vm_output
from heap_output import HEAP_END, validate_heap_output, parse_heap_output
from sched_output import SCHED_START, SCHED_END, validate_sched_output

POST_IRQ = b"[TEST] PMM post-IRQ accounting verified\r\n"


def validate_boot_output(output: bytes, vector: int = 3) -> list[str]:
    if vector != 3:
        return validate_exception_output(output, vector)
    cpu, end, remaining = output.partition(EXCEPTION_END)
    errors = validate_exception_output(cpu + end, vector)
    pmm, end, remaining = remaining.partition(PMM_END)
    pmm_errors = validate_pmm_output(pmm + end)
    errors.extend(pmm_errors)
    vm, end, remaining = remaining.partition(VM_END)
    vm_errors = validate_vm_output(vm + end, parse_pmm_output(pmm + PMM_END) if not pmm_errors else None)
    errors.extend(vm_errors)
    vm_state = parse_vm_output(vm + end) if not vm_errors else None
    heap, end, timer = remaining.partition(HEAP_END)
    heap_errors = validate_heap_output(heap + end, vm_state)
    errors.extend(heap_errors)
    heap_state = parse_heap_output(heap + end) if not heap_errors else None
    sched, end, after = timer.partition(SCHED_START)
    expected_timer = TIMER_OUTPUT
    if sched != expected_timer:
        missing = [line.decode("ascii").strip() for line in expected_timer.splitlines(keepends=True)
                   if line not in sched]
        errors.append("Timer output missing: " + ", ".join(missing) if missing else
                      "Timer output order/count/trailing data invalid")
    stats, sep, post = after.partition(POST_IRQ)
    errors.extend(validate_sched_output(end + stats, heap_state))
    if sep == b"" or post != b"":
        errors.append("Post-IRQ accounting missing or not final")
    return errors
