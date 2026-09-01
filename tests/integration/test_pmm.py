"""Real E820/PMM QEMU runs at differing RAM sizes and corrupted handoffs."""

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
from pmm_output import parse_pmm_output, PMM_END
from timer_output import EXCEPTION_END
from boot_output import POST_IRQ
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from test_boot import elf_symbol


def pmm_section(output):
    return output.partition(EXCEPTION_END)[2].partition(PMM_END)[0] + PMM_END


class PhysicalMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.destination = ROOT / "build/pmm-tests/normal"
        build_image(ROOT, cls.destination)

    def verify_cleanup(self, logs):
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["reaped"])
        self.assertEqual(summary["cleanup"], "monitor-quit")
        self.assertEqual(summary["returncode"], 0)

    def test_real_map_changes_with_qemu_ram_and_reserves_linked_objects(self):
        results = []
        for memory in (16, 64, 128, 256):
            with self.subTest(memory_mib=memory):
                logs = ROOT / f"build/pmm-tests/ram-{memory}"
                output = boot_image(self.destination / "rynoros.img", logs, memory_mib=memory)
                data = parse_pmm_output(pmm_section(output))
                self.assertIsNotNone(re.search(re.escape(POST_IRQ) + b"$",
                                               output))
                self.assertGreater(data["last_frame"], 0x200000)
                self.assertEqual(data["exhausted_frames"] * 4096, data["free_bytes"])
                # Check every frame occupied by actual linked live objects, not
                # just a hardcoded kernel base or an expected serial marker.
                elf = self.destination / "rynorkernel.elf"
                for name in ("kernel", "kernel_stack", "boot_stack", "boot_sector", "boot_map", "fb_info", "page_tables"):
                    start, end = (elf_symbol(elf, f"__{name}_{edge}") for edge in ("start", "end"))
                    for address in range(start // 4096 * 4096, (end + 4095) // 4096 * 4096, 4096):
                        self.assertFalse(any(kind == 1 and a <= address < b for a, b, kind in data["regions"]),
                                         f"{name} frame {address:#x} was allocatable")
                self.verify_cleanup(logs)
                results.append(data)
        self.assertLess(results[0]["free_bytes"], results[1]["free_bytes"])
        self.assertLess(results[1]["free_bytes"], results[2]["free_bytes"])
        self.assertGreater(results[3]["free_bytes"], results[2]["free_bytes"])
        self.assertGreater(results[3]["metadata_bytes"], results[0]["metadata_bytes"])
        self.assertNotEqual(results[0]["raw"], results[2]["raw"])

    def corrupted_handoff(self, name, injection, reason):
        with tempfile.TemporaryDirectory(prefix="bad-e820-", dir=ROOT / "build") as temporary:
            fixture = Path(temporary)
            for directory in REQUIRED_DIRECTORIES:
                (fixture / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, fixture / filename)
            source = fixture / "boot/transition.asm"
            contents = source.read_text(encoding="utf-8")
            original = "    call acquire_e820\n"
            self.assertEqual(contents.count(original), 1)
            source.write_text(contents.replace(original, original + injection), encoding="utf-8")
            build_image(fixture)
            logs = ROOT / "build/pmm-tests" / name
            # Six-second budget: BIOS-phase boot variance (SMM cycles, timer
            # probing) must not outrun the deadline on a busy host; the guest
            # halts immediately after printing the expected failure marker.
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                boot_image(fixture / "build/rynoros.img", logs, timeout=6)
            output = (logs / "serial.log").read_bytes()
            self.assertIn(b"[TEST] exception handling verified", output)
            self.assertIn(reason.encode("ascii"), output)
            self.assertNotIn(b"[MM] allocator initialized", output)
            self.assertNotIn(b"[TEST] PMM self-test passed", output)
            self.assertNotIn(b"[TIMER] tick=", output)
            self.verify_cleanup(logs)

    def test_incomplete_firmware_map_is_rejected(self):
        self.corrupted_handoff("incomplete", "    mov dword [__boot_map_start + 20], 2\n",
                               "invalid_handoff_header")

    def test_invalid_record_size_is_rejected(self):
        self.corrupted_handoff("bad-stride", "    mov dword [__boot_map_start + 32 + 24], 21\n",
                               "invalid_entry_size")

    def test_overflowing_firmware_region_is_rejected(self):
        self.corrupted_handoff("overflow", "    mov dword [__boot_map_start + 32 + 8], -1\n"
                               "    mov dword [__boot_map_start + 32 + 12], -1\n",
                               "invalid_physical_range")

    def test_firmware_reserved_kernel_memory_is_rejected(self):
        self.corrupted_handoff("kernel-not-ram", "    mov dword [__boot_map_start + 32 + 16], 2\n",
                               "[MM] init_error=8")

    def allocator_variant(self, name, old, new, reason):
        """Mutate the allocator itself, not the firmware handoff: the guest's
        own accounting and uniqueness checks must reject the defect."""
        with tempfile.TemporaryDirectory(prefix="bad-pmm-", dir=ROOT / "build") as temporary:
            fixture = Path(temporary)
            for directory in REQUIRED_DIRECTORIES:
                (fixture / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, fixture / filename)
            source = fixture / "kernel/mm/pmm.c"
            contents = source.read_text(encoding="utf-8")
            self.assertEqual(contents.count(old), 1)
            source.write_text(contents.replace(old, new), encoding="utf-8")
            build_image(fixture)
            logs = ROOT / "build/pmm-tests" / name
            # Six-second budget for BIOS-phase boot variance; see corrupted_handoff.
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                boot_image(fixture / "build/rynoros.img", logs, timeout=6)
            output = (logs / "serial.log").read_bytes()
            self.assertIn(b"[TEST] exception handling verified", output)
            self.assertIn(reason.encode("ascii"), output)
            self.assertNotIn(b"[TEST] PMM self-test passed", output)
            self.verify_cleanup(logs)

    def test_allocator_bitmap_set_cannot_be_omitted(self):
        self.allocator_variant("skip-bitmap",
            "        allocated[index / 8] |= (cpu_u8)(1u << (index % 8));",
            "        /* Bitmap set deliberately omitted. */",
            "[MM] failure=allocate_unique_frames")

    def test_allocator_cursor_pull_cannot_be_omitted(self):
        self.allocator_variant("release-cursor",
            "    if (index < search_cursor) search_cursor = index;",
            "    /* Cursor pull deliberately omitted. */",
            "[MM] failure=reuse")
