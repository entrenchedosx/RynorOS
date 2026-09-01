"""Hardware paging proof and independently broken kernel variants."""
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
from pmm_output import PMM_END, parse_pmm_output
from vm_output import VM_END, parse_vm_output
from timer_output import EXCEPTION_END
from boot_output import POST_IRQ
from test_boot import elf_symbol


class VirtualMemoryTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual(state["cleanup"], "monitor-quit")
        self.assertEqual(state["returncode"], 0)

    def test_real_paging_fault_rips_and_accounting(self):
        destination = ROOT / "build/vm-tests/normal"
        manifest = build_image(ROOT, destination)
        self.assertGreater(manifest["payload_sectors"], 64)  # Real multi-read loader regression.
        output = boot_image(destination / "rynoros.img", destination / "logs")
        pmm = parse_pmm_output(output.partition(EXCEPTION_END)[2].partition(PMM_END)[0] + PMM_END)
        vm = parse_vm_output(output.partition(PMM_END)[2].partition(VM_END)[0] + VM_END, pmm)
        elf = destination / "rynorkernel.elf"
        self.assertEqual(vm["faults"][0][2], elf_symbol(elf, "vm_test_write_fault"))
        self.assertEqual(vm["faults"][2][2], elf_symbol(elf, "vm_test_read_fault"))
        self.assertEqual(vm["faults"][1][2], 0x40000000)
        self.assertEqual(vm["allocated"], 7 * 4096)
        self.assertIsNotNone(re.search(re.escape(POST_IRQ) + b"$",
                                       output))
        for name in ("__text_end", "__rodata_end", "__data_start"):
            self.assertEqual(elf_symbol(elf, name) % 4096, 0)
        self.cleanup(destination / "logs")

    def broken(self, name, source, old, new, reason):
        with tempfile.TemporaryDirectory(prefix="vm-fault-", dir=ROOT / "build") as tmp:
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
            logs = ROOT / "build/vm-tests" / name
            with self.assertRaisesRegex(RuntimeError, "(timed out|\\[VM\\] failure=)"):
                boot_image(fixture / "build/rynoros.img", logs, timeout=6)
            output = (logs / "serial.log").read_bytes()
            self.assertIn(PMM_END, output)
            self.assertIn(reason.encode(), output)
            self.assertNotIn(VM_END, output)
            self.assertNotIn(b"[TIMER] tick=", output)
            self.cleanup(logs)

    def test_skipped_cr3_load_cannot_pass(self):
        self.broken("no-cr3", "kernel/mm/vm.c",
                    '__asm__ volatile ("mov %0,%%cr3" : : "r"(kernel_space.root) : "memory");',
                    '/* Intentionally broken CR3 activation. */', "[VM] failure=initialization")

    def test_unarmed_page_fault_is_fatal(self):
        self.broken("unarmed", "kernel/mm/vm-test.c", "fault.armed = 1;", "fault.armed = 0;",
                    "[VM] page fault action=halt reason=unexpected")

    def test_wrong_fault_address_is_fatal(self):
        self.broken("wrong-address", "kernel/mm/vm-test.c", "fault.address = va;", "fault.address = va + 4096;",
                    "[VM] page fault action=halt reason=unexpected")

    def test_missing_permission_tlb_invalidation_cannot_pass(self):
        self.broken("stale-tlb", "kernel/mm/vm.c",
                    "write_entry(path[0], page_index(va, 0), e);\n    if (s == &kernel_space) page_invalidate(va);",
                    "write_entry(path[0], page_index(va, 0), e); /* Intentionally stale permissions. */",
                    "[VM] failure=expected_hardware_page_fault_missing")

    def test_missing_table_zeroing_cannot_pass(self):
        self.broken("not-zeroed", "kernel/mm/vm.c",
                    "for (unsigned int i = 0; i < VM_ENTRIES; ++i) t->entry[i].value = 0;",
                    "if (!active) for (unsigned int i = 0; i < VM_ENTRIES; ++i) t->entry[i].value = 0;",
                    "[VM] failure=table_zeroing")

    def test_missing_unmap_invalidation_cannot_pass(self):
        self.broken("stale-unmap", "kernel/mm/vm.c",
                    "if (s == &kernel_space) page_invalidate(va);\n    for (unsigned int level = 0; level < detached;",
                    "/* Deliberately stale unmapped leaf. */\n    for (unsigned int level = 0; level < detached;",
                    "[VM] failure=expected_hardware_page_fault_missing")
