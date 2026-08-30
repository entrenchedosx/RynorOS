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
from timer_output import EXCEPTION_END


class AuditRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.destination = ROOT / "build/audit-tests/image"
        build_image(ROOT, cls.destination)

    def run_guest(self, name, **kwargs):
        logs = ROOT / "build/audit-tests" / name
        try:
            return boot_image(self.destination / "rynoros.img", logs, **kwargs)
        finally:
            state = json.loads((logs / "run.json").read_text())
            self.assertTrue(state["reaped"])
            self.assertEqual(state["cleanup"], "monitor-quit")
            self.assertEqual(state["returncode"], 0)

    def test_small_and_larger_ram(self):
        for size in (8, 512):
            with self.subTest(memory=size):
                output = self.run_guest(f"ram-{size}", memory_mib=size)
                self.assertIn(HEAP_END, output)

    def test_real_firmware_hole_and_ram_above_four_gib(self):
        output = self.run_guest("high-ram", memory_mib=64, max_ram_below_4g_mib=32)
        pmm = parse_pmm_output(output.partition(EXCEPTION_END)[2].partition(PMM_END)[0] + PMM_END)
        self.assertTrue(any(a >= 1 << 32 and kind == 1 for a, b, kind in pmm["regions"]))
        self.assertGreater(pmm["last_frame"], 1 << 32)
        vm = parse_vm_output(output.partition(PMM_END)[2].partition(VM_END)[0] + VM_END, pmm)
        heap = parse_heap_output(output.partition(VM_END)[2].partition(HEAP_END)[0] + HEAP_END, vm)
        self.assertEqual(heap["allocated"], 26 * 4096)

    def test_additional_emulated_cpu(self):
        self.assertIn(HEAP_END, self.run_guest("cpu-max", cpu_model="max"))

    def test_missing_nx_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.run_guest("no-nx", cpu_model="qemu64,-nx", timeout=2)
        output = (ROOT / "build/audit-tests/no-nx/serial.log").read_bytes()
        self.assertIn(PMM_END, output)
        self.assertIn(b"[VM] init_error=11", output)
        self.assertNotIn(VM_END, output)
        self.assertNotIn(HEAP_END, output)
