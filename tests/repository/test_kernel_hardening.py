"""Kernel hardening regression tests — prove deep fixes for A05/A10."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class KernelHardeningTests(unittest.TestCase):
    def test_pmm_search_cursor_bounded(self):
        # A10: PMM OOB search_cursor must be bounded to frame_count in allocate path.
        text = (ROOT / "kernel/mm/pmm.c").read_text(encoding="utf-8")
        alloc = text[text.find("enum pmm_result pmm_allocate"):text.find("enum pmm_result pmm_allocate") + 2000]
        self.assertIn("search_cursor > frame_count", alloc, "A10 fix missing in pmm_allocate: search_cursor bound")
        self.assertIn("index < frame_count && bit(index)", alloc, "A10 fallback must also bound in pmm_allocate")
        # The allocate fallback must not contain the unbounded form.
        self.assertNotIn("index < search_cursor && bit(index)", alloc)
        # Both scan loops must be strictly bounded: primary while + fallback for.
        self.assertIn("while (index < frame_count && bit(index))", alloc)
        self.assertIn("for (index = 0; index < search_cursor && index < frame_count && bit(index)", alloc)
        self.assertNotIn("index <= frame_count", alloc)
        # Final OOM guard must require either OOB index or set bit (||, not &&).
        self.assertIn("if (index >= frame_count || bit(index)) return PMM_OUT_OF_MEMORY;", alloc)
        self.assertNotIn("if (index >= frame_count && bit(index))", alloc)

    def test_pmm_context_checks_irq(self):
        text = (ROOT / "kernel/mm/pmm.c").read_text(encoding="utf-8")
        self.assertIn("irq_in_context", text, "PMM context must check IRQ")
        self.assertIn('#include "irq.h"', text)

    def test_heap_context_checks_irq(self):
        text = (ROOT / "kernel/mm/heap.c").read_text(encoding="utf-8")
        self.assertIn("irq_in_context", text, "Heap context_ok must check IRQ")
        self.assertIn('#include "irq.h"', text)
        self.assertIn("cpu_interrupts_disabled() && !irq_in_context()", text)

    def test_vm_context_checks_irq(self):
        text = (ROOT / "kernel/mm/vm.c").read_text(encoding="utf-8")
        self.assertIn("irq_in_context", text, "VM context must check IRQ")
        self.assertIn('#include "irq.h"', text)

    def test_no_new_eval_in_kernel(self):
        # Ensure no host-side eval leaks into kernel
        for path in (ROOT / "kernel").rglob("*.c"):
            txt = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("eval(", txt)
            self.assertNotIn("exec(", txt)

if __name__ == "__main__":
    unittest.main()
