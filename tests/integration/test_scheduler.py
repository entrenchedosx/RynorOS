"""Real scheduler probes and implementation mutations, never assertion inversion."""
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from boot_output import POST_IRQ
from sched_output import SCHED_START, SCHED_END, parse_sched_output
from kbd_output import KBD_START
from timer_output import TIMER_OUTPUT
from test_boot import elf_symbol


class SchedulerTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual(state["cleanup"], "monitor-quit")
        self.assertEqual(state["returncode"], 0)

    def test_real_scheduler_preempts_workers(self):
        destination = ROOT / "build/sched-tests/normal"
        build_image(ROOT, destination)
        # Repeated boots of exactly the same image, not timing-based assertions.
        for run in range(3):
            logs = destination / f"logs-{run}"
            try:
                output = boot_image(destination / "rynoros.img", logs)
            finally:
                self.cleanup(logs)
            self.assertIn(TIMER_OUTPUT, output)
            section = SCHED_START + output.partition(SCHED_START)[2].partition(KBD_START)[0]
            parsed = parse_sched_output(section)
            self.assertEqual(parsed["preemptions"], 48)
            elf = destination / "rynorkernel.elf"
            for w in parsed["workers"]:
                self.assertGreaterEqual(w["irq_rip"], elf_symbol(elf, "sched_test_loop_begin"))
                self.assertLess(w["irq_rip"], elf_symbol(elf, "sched_test_loop_end"))
                self.assertEqual(w["preemptions"], 6)
                self.assertEqual(w["dispatches"], 6)
            self.assertIn(POST_IRQ, output)

    def broken(self, name, source, old, new, reason, hardware=None):
        with tempfile.TemporaryDirectory(prefix="sched-fault-", dir=ROOT / "build") as tmp:
            fixture = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (fixture / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, fixture / filename)
            path = fixture / source
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(contents.count(old), 1)
            path.write_text(contents.replace(old, new), encoding="utf-8")
            build_image(fixture)
            logs = ROOT / "build/sched-tests" / name
            try:
                with self.assertRaisesRegex(RuntimeError, "(timed out|\\[SCHED\\] failure=)"):
                    boot_image(fixture / "build/rynoros.img", logs, timeout=3)
            finally:
                self.cleanup(logs)
            output = (logs / "serial.log").read_bytes()
            # A crash earlier in PMM/VM/heap cannot masquerade as this failure.
            self.assertIn(SCHED_START, output)
            self.assertIn(reason.encode(), output)
            self.assertNotIn(SCHED_END, output)
            self.assertNotIn(POST_IRQ, output)
            if hardware:
                address, error, symbol = hardware
                match = re.search(
                    rb"\[VM\] fault_address=0x([0-9a-f]{16}) error=0x([0-9a-f]{16}) rip=0x([0-9a-f]{16})",
                    output.partition(SCHED_START)[2])
                self.assertIsNotNone(match)
                cr2, actual_error, rip = (int(x, 16) for x in match.groups())
                self.assertEqual((cr2, actual_error), (address, error))
                self.assertEqual(rip, elf_symbol(fixture / "build/rynorkernel.elf", symbol) if symbol else address)
                self.assertIn(b"[VM] page fault action=halt reason=unexpected", output)

    def test_no_genuine_preemption_cannot_pass(self):
        self.broken("no-preemption", "kernel/core/thread.c",
                    "if (!initialized) return frame;", "return frame; /* omit scheduling */",
                    "[SCHED] failure=timer_preemption")

    def test_pmm_imbalance_after_join_cannot_pass(self):
        self.broken("pmm-leak", "kernel/mm/kstack.c",
                    "require(pmm_release(r->frames[i]) == PMM_OK);",
                    "/* deliberately leak the unmapped stack frame */",
                    "[SCHED] failure=resource_balance")

    def test_partial_mapping_rollback_leak_cannot_pass(self):
        self.broken("rollback-leak", "kernel/mm/kstack.c",
                    "require(pmm_release(r.frames[mapped]) == PMM_OK);\n            break;",
                    "/* deliberately leak the frame after failed map */\n            break;",
                    "[SCHED] failure=resource_balance")

    def test_guard_access_is_real_fatal_page_fault(self):
        self.broken("guard-fault", "kernel/core/scheduler-test.c",
                    'cpu_u64 lo = 0, hi = 0;',
                    'extern void vm_test_read(cpu_u64); vm_test_read(KSTACK_BASE);\n        cpu_u64 lo = 0, hi = 0;',
                    "[VM] page fault action=halt reason=unexpected",
                    (0xffffe00000000000, 0, "vm_test_read_fault"))

    def test_stack_payload_is_hardware_non_executable(self):
        self.broken("stack-nx", "kernel/core/scheduler-test.c",
                    'cpu_u64 lo = 0, hi = 0;',
                    'extern void vm_test_execute(cpu_u64); vm_test_execute(KSTACK_BASE+VM_PAGE_SIZE);\n        cpu_u64 lo = 0, hi = 0;',
                    "[VM] page fault action=halt reason=unexpected",
                    (0xffffe00000001000, 17, None))

    def test_copied_stack_cannot_gain_ownership(self):
        self.broken("copied-owner", "kernel/mm/kstack.c",
                    "records[h->slot].owner == h &&", "records[h->slot].owner != 0 &&",
                    "[SCHED] failure=copied_stack_owner")

    def test_corrupted_current_is_rejected_before_dereference(self):
        self.broken("bad-current", "kernel/core/thread.c",
                    "unsigned int slot = current_slot();",
                    "current = (struct thread *)1; unsigned int slot = current_slot();",
                    "[SCHED] failure=invalid_current")

    def test_invalid_resume_stack_is_rejected_before_iret(self):
        self.broken("bad-rsp", "kernel/core/thread.c",
                    'require(frame_valid(next, &next->saved), "invalid_resume");',
                    'next->saved.rsp = 1; require(frame_valid(next, &next->saved), "invalid_resume");',
                    "[SCHED] failure=invalid_resume")

    def test_invalid_irq_handoff_is_rejected_before_rsp_change(self):
        self.broken("bad-handoff", "kernel/interrupts/irq.c",
                    "dispatching = 0;\n    return resume;",
                    "dispatching = 0;\n    if (resume != frame) resume = 0;\n    return resume;",
                    "[SCHED] failure=handoff_pointer")

    def test_stale_id_cannot_alias_recycled_thread(self):
        self.broken("stale-id", "kernel/core/thread.c",
                    "threads[i].id == id) return &threads[i];",
                    "(threads[i].id == id || (id == 2 && i == 1))) return &threads[i];",
                    "[SCHED] failure=invalid_lifecycle")

    def test_live_join_is_rejected(self):
        self.broken("live-join", "kernel/core/thread.c",
                    "t->state != THREAD_EXITED) return 0;",
                    "0) return 0; /* permit live stack destruction */",
                    "[SCHED] failure=invalid_lifecycle")

    def test_preempted_register_corruption_is_detected(self):
        self.broken("bad-register", "kernel/core/thread.c",
                    "return &next->saved;",
                    "next->saved.r12 ^= 1; return &next->saved;",
                    "[SCHED] failure=register_or_flags_restore")

    def test_preempted_arithmetic_flag_corruption_is_detected(self):
        self.broken("bad-arithmetic-flag", "kernel/core/thread.c",
                    "return &next->saved;",
                    "next->saved.rflags ^= 0x10; return &next->saved;",
                    "[SCHED] failure=register_or_flags_restore")

    def test_bootstrap_cannot_exit(self):
        self.broken("bootstrap-exit", "kernel/core/scheduler-test.c",
                    "stack_tests(); allocation_failure_tests();",
                    "thread_exit(); stack_tests(); allocation_failure_tests();",
                    "[SCHED] failure=bootstrap_exit")

    def test_present_guard_is_rejected(self):
        self.broken("mapped-guard", "kernel/mm/kstack.c",
                    "r.owner = out; r.generation = ++generation;",
                    "require(vm_map(vm_kernel_space(), base_of(slot), r.frames[0], VM_WRITE) == VM_OK);\n    r.owner = out; r.generation = ++generation;",
                    "[SCHED] failure=stack_permissions")

    def test_invalid_code_selector_is_rejected(self):
        self.broken("bad-cs", "kernel/core/thread.c",
                    'require(frame_valid(next, &next->saved), "invalid_resume");',
                    'next->saved.cs = 0x10; require(frame_valid(next, &next->saved), "invalid_resume");',
                    "[SCHED] failure=invalid_resume")

    def test_yield_must_not_enable_interrupts(self):
        self.broken("yield-if", "kernel/arch/x86_64/switch.asm",
                    "mov [rdi + 152], rax", "mov qword [rdi + 152], 0x202",
                    "[SCHED] failure=yield_return")

    def test_interrupt_enable_with_held_lock_is_rejected(self):
        self.broken("locked-sti", "kernel/core/scheduler-test.c",
                    "spinlock_t copy = a;", "irq_restore(0x200); spinlock_t copy = a;",
                    "[SCHED] failure=restore_with_lock")

    def test_non_timer_irq_return_must_remain_supported(self):
        self.broken("timer-only-handoff", "kernel/core/thread.c",
                    "(f->vector >= IRQ_BASE && f->vector < IRQ_BASE + IRQ_COUNT)",
                    "f->vector == IRQ_BASE",
                    "[SCHED] failure=handoff_frame")

    def test_pmm_allocation_rejects_real_irq_context(self):
        self.broken("pmm-irq-context", "kernel/mm/pmm.c",
                    "static enum pmm_result context(void)\n{\n    if (!cpu_interrupts_disabled() || irq_in_context()) return PMM_WRONG_CONTEXT;",
                    "static enum pmm_result context(void)\n{\n    if (!cpu_interrupts_disabled()) return PMM_WRONG_CONTEXT;",
                    "[SCHED] failure=irq_memory_context")

    def test_vm_creation_rejects_real_irq_context(self):
        self.broken("vm-irq-context", "kernel/mm/vm.c",
                    "if (!cpu_interrupts_disabled() || irq_in_context()) return VM_CONTEXT;\n    if (!active) return VM_NOT_READY;\n    if (!s || s->identity || s->root || s->table_pages) return VM_INVALID;",
                    "if (!cpu_interrupts_disabled()) return VM_CONTEXT;\n    if (!active) return VM_NOT_READY;\n    if (!s || s->identity || s->root || s->table_pages) return VM_INVALID;",
                    "[SCHED] failure=irq_memory_context")

    def test_heap_allocation_rejects_real_irq_context(self):
        self.broken("heap-irq-context", "kernel/mm/heap.c",
                    "static int context_ok(void) { return cpu_interrupts_disabled() && !irq_in_context(); }",
                    "static int context_ok(void) { return cpu_interrupts_disabled(); }",
                    "[SCHED] failure=irq_memory_context")
