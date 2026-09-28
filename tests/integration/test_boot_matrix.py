"""BOOT-A1 oversized-kernel matrix: multi-MiB kernels boot from high memory.

Each case builds a fixture image with controlled file/mem bloat and
boots it end to end. boot_image validates the whole transcript against
the fixture's own ELF layout (PMM reservation, VM tables, user
tables), so a returned transcript IS the proof; the manifest numbers
pin the demonstrated capacity class. The old ceiling was 384 KiB
total; every case below exceeds it by multiples.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image  # noqa: E402
from qemu import boot_image  # noqa: E402
from boot_layout import elf_boot_layout  # noqa: E402
from user_output import parse_serial, validate as validate_user  # noqa: E402

BLOAT_TU = "kernel/mm/heap-test.c"


def _bloated_copy(file_bytes, mem_bytes):
    tmp = tempfile.TemporaryDirectory(prefix="boot-matrix-", dir=ROOT / "build")
    root = Path(tmp.name)
    from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
    for d in REQUIRED_DIRECTORIES:
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_FILES:
        shutil.copyfile(ROOT / f, root / f)
    path = root / BLOAT_TU
    source = path.read_text(encoding="utf-8")
    source += ("\n/* BOOT-A1 matrix bloat: sized file/mem growth. */\n"
               "__attribute__((used)) static const unsigned char boot_matrix_file[%d] = {0x5A};\n"
               "__attribute__((used)) static unsigned char boot_matrix_mem[%d];\n"
               % (file_bytes, mem_bytes))
    path.write_text(source, encoding="utf-8")
    manifest = build_image(root, root / "build" / "img")
    return tmp, root, manifest


class BootMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/boot-matrix"
        cls.work.mkdir(parents=True, exist_ok=True)

    def _boot_case(self, name, file_bytes, mem_bytes, timeout):
        tmp, root, manifest = _bloated_copy(file_bytes, mem_bytes)
        self.addCleanup(tmp.cleanup)
        dest = root / "build" / "img"
        output = boot_image(dest / "rynoros.img", self.work / name, timeout=timeout)
        layout = elf_boot_layout(dest / "rynorkernel.elf")
        self.assertEqual(validate_user(parse_serial(output), layout["user_tables"]), [])
        header = manifest["boot_header"]
        print(f"[matrix] {name}: file_sectors={header['file_sectors']} "
              f"mem_pages={header['mem_pages']} vm_tables={layout['vm_tables']} "
              f"user_tables={layout['user_tables']}")
        return header, layout

    def test_b1_one_megabyte_file(self):
        # ~1.3 MB file (multi-chunk copy: 4 staging chunks), one 2 MiB region.
        header, layout = self._boot_case("b1", 1024 * 1024, 4096, timeout=60)
        self.assertGreater(header["file_sectors"], 2000)
        self.assertEqual(layout["vm_tables"], 8)
        self.assertEqual(layout["user_tables"], 7)

    def test_b2_two_regions(self):
        # ~2.9 MB mem crosses the 10 MiB line: PD4+PD5, tables 9/8.
        header, layout = self._boot_case("b2", 4096, 2560 * 1024, timeout=60)
        self.assertGreater(header["mem_pages"], 600)
        self.assertLess(header["mem_pages"], 900)
        self.assertEqual(layout["vm_tables"], 9)
        self.assertEqual(layout["user_tables"], 8)

    def test_b3_five_regions(self):
        # ~8.4 MB mem: PD4..PD8, tables 12/11.
        header, layout = self._boot_case("b3", 4096, 8 * 1024 * 1024, timeout=60)
        self.assertGreater(header["mem_pages"], 2000)
        self.assertLess(header["mem_pages"], 2300)
        self.assertEqual(layout["vm_tables"], 12)
        self.assertEqual(layout["user_tables"], 11)

    def test_b4_near_capacity(self):
        # ~6.3 MB file (23 chunks) + ~15.6 MB mem: all 8 regions, 15/14.
        header, layout = self._boot_case("b4", 6 * 1024 * 1024, 9 * 1024 * 1024,
                                         timeout=60)
        self.assertGreater(header["file_sectors"], 12000)
        self.assertGreater(header["mem_pages"], 3800)
        self.assertLessEqual(header["mem_pages"], 4096)
        self.assertEqual(layout["vm_tables"], 15)
        self.assertEqual(layout["user_tables"], 14)


if __name__ == "__main__":
    unittest.main()
