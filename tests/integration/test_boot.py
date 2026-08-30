"""Real QEMU execution and reproducible native builds. No mocked emulator."""

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import ARTIFACTS, build_image
from qemu import EXPECTED_OUTPUT, boot_image
from exception_output import validate_exception_output
import re


def elf_symbol(path, name):
    """Read the actual linked ELF64 symbol, without adding a host binary tool."""
    data = path.read_bytes()
    section_offset = struct.unpack_from("<Q", data, 40)[0]
    section_size, section_count = struct.unpack_from("<HH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, section_offset + i * section_size)
                for i in range(section_count)]
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        strings = sections[section[6]]
        strings_data = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name_offset, _, _, _, value, _ = struct.unpack_from("<IBBHQQ", data, offset)
            symbol = strings_data[name_offset:].split(b"\0", 1)[0].decode("ascii")
            if symbol == name:
                return value
    raise AssertionError(f"Missing ELF symbol: {name}")


class BootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = build_image(ROOT)  # Always build, never rely on old binaries.
        cls.image = ROOT / "build/rynoros.img"

    def test_actual_kernel_boots_and_qemu_is_reaped(self):
        logs = ROOT / "build/boot-test"
        before = hashlib.sha256(self.image.read_bytes()).hexdigest()
        observed = boot_image(self.image, logs)
        self.assertTrue(observed.startswith(EXPECTED_OUTPUT))
        self.assertEqual(validate_exception_output(observed), [])
        self.assert_rip_matches_elf(observed, ROOT / "build/rynorkernel.elf", 3)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).hexdigest(), before)
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["reaped"])
        self.assertEqual(summary["returncode"], 0)
        self.assertEqual(summary["cleanup"], "monitor-quit")

    def assert_rip_matches_elf(self, output, elf, vector):
        saved_rip = int(re.search(rb"\[STATE\] rip=0x([0-9a-f]{16})", output)[1], 16)
        symbol = "cpu_test_after" if vector in (1, 3) else "cpu_test_fault"
        self.assertEqual(saved_rip, elf_symbol(elf, symbol))
        self.assertEqual(output.count(b"[EXCEPTION] vector="), 1)
        self.assertEqual(output.count(b"[TEST] exception handling verified"), 1)

    def verify_vector(self, vector):
        destination = ROOT / f"build/cpu-tests/vector-{vector:02d}"
        build_image(ROOT, destination, test_vector=vector)
        output = boot_image(destination / "rynoros.img", destination / "logs", test_vector=vector)
        self.assertEqual(validate_exception_output(output, vector), [])
        self.assert_rip_matches_elf(output, destination / "rynorkernel.elf", vector)
        summary = json.loads((destination / "logs/run.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["reaped"])
        self.assertEqual(summary["returncode"], 0)
        self.assertEqual(summary["cleanup"], "monitor-quit")

    def test_divide_error_diagnostics(self):
        self.verify_vector(0)

    def test_debug_diagnostics(self):
        self.verify_vector(1)

    def test_invalid_opcode_diagnostics(self):
        self.verify_vector(6)

    def test_general_protection_cpu_error_code(self):
        self.verify_vector(13)

    def test_page_fault_cpu_error_code_and_cr2(self):
        self.verify_vector(14)

    def test_unarmed_breakpoint_is_fatal_not_false_success(self):
        destination = ROOT / "build/cpu-tests/unarmed-breakpoint"
        build_image(ROOT, destination, test_armed=False)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            boot_image(destination / "rynoros.img", destination / "logs", timeout=2)
        output = (destination / "logs/serial.log").read_bytes()
        self.assertIn(b"vector=03 name=breakpoint", output)
        self.assertIn(b"[EXCEPTION] action=halt reason=unexpected\r\n", output)
        self.assertNotIn(b"[TEST] exception handling verified", output)
        summary = json.loads((destination / "logs/run.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["reaped"])
        self.assertEqual(summary["cleanup"], "monitor-quit")

    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix="repro-", dir=ROOT / "build") as temporary:
            other = Path(temporary)
            manifest = build_image(ROOT, other)
            self.assertEqual(manifest["artifacts"], self.manifest["artifacts"])
            for name in ARTIFACTS:
                with self.subTest(artifact=name):
                    self.assertEqual((ROOT / "build" / name).read_bytes(), (other / name).read_bytes())

    def test_linked_elf_is_x86_64_with_entry_in_payload(self):
        elf = (ROOT / "build/rynorkernel.elf").read_bytes()
        self.assertEqual(elf[:6], b"\x7fELF\x02\x01")
        self.assertEqual(struct.unpack_from("<H", elf, 18)[0], 62)
        entry = struct.unpack_from("<Q", elf, 24)[0]
        self.assertGreaterEqual(entry, 0x8000)
        self.assertLess(entry, 0x10000)

    def assert_timeout_and_cleanup(self, image, logs):
        logs.mkdir(parents=True, exist_ok=True)
        # Plant a stale success log: the runner must truncate it before launch.
        (logs / "serial.log").write_bytes(EXPECTED_OUTPUT)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            boot_image(image, logs, timeout=2)
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["reaped"])
        self.assertEqual(summary["cleanup"], "monitor-quit")
        self.assertLess(summary["elapsed_seconds"], 8)

    def test_nonbootable_image_times_out_without_stale_success(self):
        with tempfile.TemporaryDirectory(prefix="bad-boot-", dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            image = directory / "blank.img"
            image.write_bytes(bytes(1024 * 1024))
            self.assert_timeout_and_cleanup(image, directory / "logs")
            self.assertEqual((directory / "logs/serial.log").read_bytes(), b"")

    def test_wrong_second_line_fails_even_after_real_entry(self):
        with tempfile.TemporaryDirectory(prefix="bad-version-", dir=ROOT / "build") as temporary:
            directory = Path(temporary)
            image = directory / "wrong-version.img"
            data = self.image.read_bytes()
            self.assertEqual(data.count(b"RynorOS 0.1.0"), 1)
            image.write_bytes(data.replace(b"RynorOS 0.1.0", b"RynorOS 9.9.9"))
            self.assert_timeout_and_cleanup(image, directory / "logs")
            observed = (directory / "logs/serial.log").read_bytes()
            self.assertTrue(observed.startswith(b"Rynorkernel booted.\r\n"))
            self.assertIn(b"RynorOS 9.9.9", observed)
