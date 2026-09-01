"""Synthetic host parser fixtures, never claimed as real firmware/allocator data."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from pmm_output import validate_pmm_output, firmware_regions
from boot_output import validate_boot_output, POST_IRQ
from timer_output import TIMER_OUTPUT
from sched_output import SCHED_GOOD
from kbd_output import KBD_GOOD
from display_output import DISPLAY_GOOD
from test_exception_output import parser_fixture
from qemu import boot_image
from test_vm_output import fixture as vm_fixture
from test_heap_output import fixture as heap_fixture


def fixture():
    # Deliberately invented tiny map for parser testing only: 2 MiB reported RAM.
    return ("[MM] firmware memory map acquired\r\n[MM] entries=1\r\n[MM] physical_bits=36\r\n"
            "[MM] raw base=0 length=2097152 type=1 attributes=1 size=24\r\n"
            "[MM] regions=3\r\n[MM] region base=0 end=1048576 kind=8\r\n"
            "[MM] region base=1048576 end=1052672 kind=9\r\n"
            "[MM] region base=1052672 end=2097152 kind=1\r\n"
            "[MM] firmware_usable_bytes=2097152\r\n[MM] described_bytes=2097152\r\n"
            "[MM] usable_bytes=1044480\r\n[MM] reserved_bytes=1052672\r\n"
            "[MM] free_bytes=1044480\r\n[MM] allocated_bytes=0\r\n"
            "[MM] metadata base=1048576 bytes=4096\r\n[MM] allocator initialized\r\n"
            "[TEST] PMM self-test started\r\n[TEST] PMM map validation passed\r\n"
            "[TEST] PMM reservations verified\r\n" +
            "".join(f"[TEST] PMM allocated frame={1052672 + 4096 * i}\r\n" for i in range(8)) +
            "[TEST] PMM physical RAM write verified\r\n[TEST] PMM reused frame=1052672\r\n"
            "[TEST] PMM exhausted frames=255 last=2093056\r\n"
            "[MM] final free_bytes=1044480 allocated_bytes=0\r\n[TEST] PMM self-test passed\r\n").encode()


class PmmOutputTests(unittest.TestCase):
    def test_valid_fixture_and_complete_boot(self):
        from runtime_output import RUNTIME_GOOD
        self.assertEqual(validate_pmm_output(fixture()), [])
        self.assertEqual(validate_boot_output(parser_fixture() + fixture() + vm_fixture() + heap_fixture()
                                              + TIMER_OUTPUT + SCHED_GOOD + KBD_GOOD + DISPLAY_GOOD
                                              + RUNTIME_GOOD + POST_IRQ), [])
        self.assertTrue(validate_boot_output(parser_fixture() + fixture() + TIMER_OUTPUT + SCHED_GOOD + POST_IRQ))
        self.assertTrue(validate_boot_output(parser_fixture() + TIMER_OUTPUT))

    def test_every_pmm_line_is_required(self):
        for line in fixture().splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_pmm_output(fixture().replace(line, b"")))

    def test_bad_accounting_reservations_and_allocations(self):
        for old, new in ((b"free_bytes=1044480", b"free_bytes=4096"),
                         (b"kind=9", b"kind=1"),
                         (b"allocated frame=1052672", b"allocated frame=1048576"),
                         (b"allocated frame=1056768", b"allocated frame=1052672"),
                         (b"reused frame=1052672", b"reused frame=1056768"),
                         (b"frames=255", b"frames=254"),
                         (b"bytes=4096", b"bytes=8192"),
                         (b"physical_bits=36", b"physical_bits=65")):
            with self.subTest(old=old):
                self.assertTrue(validate_pmm_output(fixture().replace(old, new)))

    def test_duplicate_garbage_and_unbounded_records_rejected(self):
        for output in (fixture() + fixture(), fixture() + b"\xff", fixture() * 100,
                       fixture().replace(b"[MM] entries=1", b"[MM] entries=65")):
            self.assertTrue(validate_pmm_output(output))

    def test_independent_normalization_rules(self):
        raw = [(0x1001, 0x6fff, 1, 1, 24), (0x4001, 1, 4, 1, 24), (0x5000, 0x4000, 1, 1, 20)]
        self.assertEqual(firmware_regions(raw, 36), [(0x1000, 0x2000, 2), (0x2000, 0x4000, 1),
                                                    (0x4000, 0x5000, 4), (0x5000, 0x9000, 1)])
        self.assertEqual(firmware_regions([(0x1000, 4096, 1, 0, 24)], 36), [])
        self.assertEqual(firmware_regions([(0x1000, 4096, 1, 3, 24)], 36), [(0x1000, 0x2000, 2)])
        with self.assertRaises(ValueError):
            firmware_regions([(1 << 36, 4096, 1, 1, 24)], 36)

    def test_qemu_memory_option_validated_before_launch(self):
        for memory in (True, 0, 4097, "64M"):
            with self.assertRaises(ValueError):
                boot_image(Path("missing.img"), Path("unused-logs"), memory_mib=memory)
        for cpu in (None, True, "host", "max,unexpected=on"):
            with self.assertRaises(ValueError):
                boot_image(Path("missing.img"), Path("unused-logs"), cpu_model=cpu)
        for limit in (True, 0, 4097, "32M"):
            with self.assertRaises(ValueError):
                boot_image(Path("missing.img"), Path("unused-logs"), max_ram_below_4g_mib=limit)
