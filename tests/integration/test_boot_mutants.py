"""BOOT-A1 loader mutants M1-M12: every loader check must fail loud.

Image mutants patch a built good image; source mutants rebuild a
fixture tree. Each case must time out (never reach the kernel) with
the exact COM1 diagnostic for its check on serial. A missing or wrong
diagnostic means the check is dead, miswired, or silent.
"""
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image  # noqa: E402
from qemu import boot_image  # noqa: E402

HEADER_OFF = 9 * 512
TIMEOUT = 10


def _mutate_copy(pairs, source):
    tmp = tempfile.TemporaryDirectory(prefix="boot-mut-", dir=ROOT / "build")
    root = Path(tmp.name)
    from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
    for d in REQUIRED_DIRECTORIES:
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_FILES:
        shutil.copyfile(ROOT / f, root / f)
    path = root / source
    contents = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents, encoding="utf-8")
    return tmp, root


class BootMutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/boot-mutants"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.base = cls.work / "base"
        build_image(ROOT, cls.base)
        cls.image = (cls.base / "rynoros.img").read_bytes()

    def _boot_corrupt(self, name, patch, diagnostic):
        logs = self.work / name
        image = bytearray(self.image)
        patch(image)
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "rynoros.img").write_bytes(bytes(image))
        with self.assertRaises(RuntimeError) as error:
            boot_image(logs / "rynoros.img", logs, timeout=TIMEOUT)
        serial = (logs / "serial.log").read_bytes()
        self.assertIn("timed out", str(error.exception))
        self.assertIn(diagnostic, serial)
        self.assertNotIn(b"Rynorkernel booted.", serial)

    def _boot_source_mutant(self, name, pairs, diagnostic):
        tmp, root = _mutate_copy(pairs, "boot/transition.asm")
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / name
        with self.assertRaises(RuntimeError) as error:
            boot_image(root / "build" / "img" / "rynoros.img", logs,
                       timeout=TIMEOUT)
        serial = (logs / "serial.log").read_bytes()
        self.assertIn("timed out", str(error.exception))
        self.assertIn(diagnostic, serial)
        self.assertNotIn(b"Rynorkernel booted.", serial)

    @staticmethod
    def _field(offset, data):
        def patch(image):
            image[offset:offset + len(data)] = data
        return patch

    def test_m1_bad_magic(self):
        self._boot_corrupt("m1", self._field(HEADER_OFF, b"XXXX"),
                           b"Rynor boot: bad header.")

    def test_m2_bad_version(self):
        self._boot_corrupt("m2", self._field(HEADER_OFF + 4, struct.pack("<H", 2)),
                           b"Rynor boot: bad header.")

    def test_m3_bad_flags_and_reserved(self):
        self._boot_corrupt("m3a", self._field(HEADER_OFF + 6, struct.pack("<H", 1)),
                           b"Rynor boot: bad header.")
        self._boot_corrupt("m3b", self._field(HEADER_OFF + 28, struct.pack("<I", 1)),
                           b"Rynor boot: bad header.")

    def test_m4_file_bounds(self):
        self._boot_corrupt("m4a", self._field(HEADER_OFF + 8, struct.pack("<I", 0)),
                           b"Rynor boot: bad header.")
        self._boot_corrupt("m4b", self._field(HEADER_OFF + 8, struct.pack("<I", 16385)),
                           b"Rynor boot: bad header.")

    def test_m5_mem_bounds(self):
        self._boot_corrupt("m5a", self._field(HEADER_OFF + 12, struct.pack("<I", 1)),
                           b"Rynor boot: bad header.")
        self._boot_corrupt("m5b", self._field(HEADER_OFF + 12, struct.pack("<I", 4097)),
                           b"Rynor boot: bad header.")

    def test_m6_bad_load_base(self):
        self._boot_corrupt("m6", self._field(HEADER_OFF + 20, struct.pack("<I", 0x100000)),
                           b"Rynor boot: bad header.")

    def test_m7_bad_entry(self):
        self._boot_corrupt("m7", self._field(HEADER_OFF + 24, struct.pack("<I", 0x100000)),
                           b"Rynor boot: bad header.")

    def test_m8_corrupt_byte(self):
        def flip(image):
            image[10 * 512 + 64] ^= 0xFF
        self._boot_corrupt("m8", flip, b"Rynor boot: kernel checksum mismatch.")

    def test_m9_bogus_header_lba(self):
        self._boot_source_mutant("m9", [("HDR_LBA equ 9", "HDR_LBA equ 999999")],
                                 b"Rynor boot: BIOS disk read failed.")

    def test_m10_tiny_ram(self):
        logs = self.work / "m10"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "rynoros.img").write_bytes(self.image)
        with self.assertRaises(RuntimeError) as error:
            boot_image(logs / "rynoros.img", logs, timeout=TIMEOUT, memory_mib=8)
        serial = (logs / "serial.log").read_bytes()
        self.assertIn("timed out", str(error.exception))
        self.assertIn(b"Rynor boot: kernel exceeds usable RAM.", serial)
        self.assertNotIn(b"Rynorkernel booted.", serial)

    def test_m11_inverted_checksum_branch(self):
        self._boot_source_mutant("m11", [("    jne checksum_fail", "    je checksum_fail")],
                                 b"Rynor boot: kernel checksum mismatch.")

    def test_m12_broken_a20_expectation(self):
        self._boot_source_mutant("m12", [("    cmp eax, 0xA55A00FF", "    cmp eax, 0xA55A00FE")],
                                 b"Rynor boot: A20 gate failed.")


if __name__ == "__main__":
    unittest.main()
