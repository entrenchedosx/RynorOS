"""Require CPU diagnostics, real PMM, VM and heap results, then timer IRQs
and the Stage 7 preemptive scheduler, finally the post-IRQ accounting line."""

from exception_output import validate_exception_output
from timer_output import EXCEPTION_END, TIMER_OUTPUT
from pmm_output import PMM_END, validate_pmm_output, parse_pmm_output
from vm_output import VM_END, validate_vm_output, parse_vm_output
from heap_output import HEAP_END, validate_heap_output, parse_heap_output
from sched_output import SCHED_START, SCHED_END, validate_sched_output, parse_sched_output
from kbd_output import KBD_START, KEYS, validate_kbd_output, parse_kbd_output
from display_output import DISPLAY_START, DISPLAY_END, validate_display_output, parse_display_output
from runtime_output import validate_runtime_output, parse_runtime_output
from shell_output import SHELL_START, SHELL_END, validate_shell_output

POST_IRQ = b"[TEST] PMM post-IRQ accounting verified\r\n"


def validate_boot_output(output: bytes, vector: int = 3, keys=KEYS,
                         require_shell: bool = False) -> list[str]:
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
    sched, kbd_sep, kbd = stats.partition(KBD_START)
    sched_errors = validate_sched_output(end + sched, heap_state)
    errors.extend(sched_errors)
    sched_state = parse_sched_output(end + sched, heap_state) if not sched_errors else None
    kbd_section, fb_sep, fb_section = kbd.partition(DISPLAY_START)
    kbd_state = None
    if kbd_sep != b"":
        kbd_errors = validate_kbd_output(KBD_START + kbd_section, keys, sched_state)
        errors.extend(kbd_errors)
        if not kbd_errors:
            kbd_state = parse_kbd_output(KBD_START + kbd_section, keys, sched_state)
    else:
        errors.append("Stage 8 keyboard output missing")
    display_state = None
    runtime_state = None
    if fb_sep != b"":
        display_head, run_sep, run_tail = (DISPLAY_START + fb_section).partition(DISPLAY_END)
        display_section = display_head + DISPLAY_END
        display_errors = validate_display_output(display_section, kbd_state)
        errors.extend(display_errors)
        display_state = parse_display_output(display_section, kbd_state) if not display_errors else None
        if run_sep != b"":
            run_errors = validate_runtime_output(run_tail, display_state)
            errors.extend(run_errors)
            runtime_state = parse_runtime_output(run_tail, display_state) if not run_errors else None
        else:
            errors.append("Stage 10 runtime output missing")
            runtime_state = None
    else:
        errors.append("Stage 9 display output missing")
    if sep == b"":
        errors.append("Post-IRQ accounting missing or not final")
        return errors
    # Stage 11 shell output is optional and follows POST_IRQ. When present it
    # must be a complete shell section with nothing before or after it.
    shell_head, shell_sep, shell_tail = post.partition(SHELL_START)
    if shell_sep != b"":
        shell_sec, tail_sep, tail = (SHELL_START + shell_tail).partition(SHELL_END)
        if shell_head != b"" or tail_sep == b"" or tail != b"":
            errors.append("Shell output incomplete or not final")
        else:
            if runtime_state is None:
                errors.append("Shell baseline accounting missing")
            else:
                errors.extend(validate_shell_output(shell_sec + SHELL_END, runtime_state))
    elif require_shell:
        errors.append("Required interactive shell output missing")
    elif post != b"":
        errors.append("Unexpected output after Post-IRQ accounting")
    return errors
