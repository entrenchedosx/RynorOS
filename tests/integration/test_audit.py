"""Extra execution coverage: RAM holes/high frames and CPU feature contracts."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from pmm_output import parse_pmm_output, PMM_END
from vm_output import parse_vm_output, VM_END
from heap_output import parse_heap_output, HEAP_END
from boot_layout import elf_boot_layout
from timer_output import EXCEPTION_END


class AuditRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.destination = ROOT / "build/audit-tests/image"
        build_image(ROOT, cls.destination)
        cls.layout = elf_boot_layout(cls.destination / "rynorkernel.elf")

    def run_guest(self, name, **kwargs):
        logs = ROOT / "build/audit-tests" / name
        succeeded = False
        try:
            output = boot_image(self.destination / "rynoros.img", logs, **kwargs)
            succeeded = True
            return output
        finally:
            state_file = logs / "run.json"
            if succeeded or state_file.exists():
                self.assertTrue(state_file.exists(), "successful QEMU run omitted run.json")
                state = json.loads(state_file.read_text())
                self.assertTrue(state["reaped"])
                self.assertEqual(state["cleanup"], "monitor-quit")
                self.assertEqual(state["returncode"], 0)

    def test_tiny_ram_is_rejected_by_loader(self):
        # The kernel loads at 8 MiB, so 8 MiB of RAM cannot hold it. The
        # loader must refuse with its RAM diagnostic and never enter a
        # partial kernel (fail-closed, then the boot deadline expires).
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.run_guest("ram-8", memory_mib=8, timeout=6)
        output = (ROOT / "build/audit-tests/ram-8/serial.log").read_bytes()
        self.assertIn(b"Rynor boot: kernel exceeds usable RAM.\r\n", output)
        self.assertNotIn(b"Rynorkernel booted.", output)

    def test_small_and_larger_ram(self):
        for size in (16, 512):
            with self.subTest(memory=size):
                # Positive matrix boots perform every subsystem self-test.
                # 16 MiB is the smallest proven boot; the PMM suite also
                # covers 16/64/128/256/4096 MiB end to end.
                output = self.run_guest(f"ram-{size}", memory_mib=size, timeout=30)
                self.assertIn(HEAP_END, output)

    def test_real_firmware_hole_and_ram_above_four_gib(self):
        output = self.run_guest("high-ram", memory_mib=64, max_ram_below_4g_mib=32)
        kernel = (self.layout["kernel_start"], self.layout["kernel_end"])
        pmm = parse_pmm_output(output.partition(EXCEPTION_END)[2].partition(PMM_END)[0] + PMM_END, kernel)
        self.assertTrue(any(a >= 1 << 32 and kind == 1 for a, b, kind in pmm["regions"]))
        self.assertGreater(pmm["last_frame"], 1 << 32)
        vm = parse_vm_output(output.partition(PMM_END)[2].partition(VM_END)[0] + VM_END, pmm, self.layout)
        heap = parse_heap_output(output.partition(VM_END)[2].partition(HEAP_END)[0] + HEAP_END, vm)
        self.assertEqual(heap["allocated"], 27 * 4096)

    def test_additional_emulated_cpu(self):
        self.assertIn(HEAP_END, self.run_guest("cpu-max", cpu_model="max"))

    def test_missing_nx_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "(timed out|\\[VM\\] failure=)"):
            self.run_guest("no-nx", cpu_model="qemu64,-nx", timeout=6)
        output = (ROOT / "build/audit-tests/no-nx/serial.log").read_bytes()
        self.assertIn(PMM_END, output)
        self.assertIn(b"[VM] init_error=11", output)
        self.assertNotIn(VM_END, output)
        self.assertNotIn(HEAP_END, output)
