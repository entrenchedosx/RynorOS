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


class BootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = build_image(ROOT)  # Always build, never rely on old binaries.
        cls.image = ROOT / "build/rynoros.img"

    def test_actual_kernel_boots_and_qemu_is_reaped(self):
        logs = ROOT / "build/boot-test"
        before = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.assertEqual(boot_image(self.image, logs), EXPECTED_OUTPUT)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).hexdigest(), before)
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["reaped"])
        self.assertEqual(summary["returncode"], 0)
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
