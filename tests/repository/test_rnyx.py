"""Stage 18b host-side tests: ELF to RYNX converter.

Pure host checks with hand-built ELF bytes (no toolchain) plus one real
backend round-trip. QEMU loading runs in tests/integration/test_load.py.
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from rnyx import elf_to_rnyx, build_envelope


def _elf(entry=0x400000, loads=(), etype=2, machine=62):
    blob = bytearray(b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8))
    blob += struct.pack("<HHIQQQIHHHHHH", etype, machine, 1, entry,
                        64, 0, 0, 64, 56, len(loads), 0, 0, 0)
    content = b""
    for flags, vaddr, filesz, memsz in loads:
        content += bytes(((len(content) * 37 + 11) & 0xFF
                          for _ in range(filesz)))
    offset = 64 + len(loads) * 56
    for index, (flags, vaddr, filesz, memsz) in enumerate(loads):
        start = sum(load[2] for load in loads[:index])
        blob += struct.pack("<IIQQQQQQ", 1, flags, offset + start,
                            vaddr, 0, filesz, memsz, 0x1000)
    return bytes(blob) + content


GOOD = _elf(loads=[(5, 0x400000, 16, 16)])
GOOD_DATA = _elf(loads=[(5, 0x400000, 16, 16), (6, 0x600000, 8, 32)])


class RnyxConverterTests(unittest.TestCase):
    def test_good_minimal_envelope(self):
        out = elf_to_rnyx(GOOD)
        self.assertEqual(out[:28], build_envelope(out[28:], 0, 0, b"")[:28])
        self.assertEqual(len(out), 28 + 16)

    def test_good_data_tiling(self):
        out = elf_to_rnyx(GOOD_DATA)
        magic, ver, arch, hlen, res, entry, code, fsz, msz = \
            struct.unpack("<4sHHHHIIII", out[:28])
        self.assertEqual((magic, ver, arch, hlen, res, entry), (b"RYNX", 1, 1, 28, 0, 0))
        self.assertEqual((code, fsz, msz), (16, 8, 32))
        self.assertEqual(len(out), 28 + 16 + 8)

    def test_bad_magic_rejected(self):
        bad = bytearray(GOOD)
        bad[0:4] = b"BAD!"
        with self.assertRaises(ValueError):
            elf_to_rnyx(bytes(bad))

    def test_non_exec_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(etype=3, loads=[(5, 0x400000, 16, 16)]))

    def test_wrong_machine_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(machine=3, loads=[(5, 0x400000, 16, 16)]))

    def test_wx_segment_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(loads=[(7, 0x400000, 16, 16)]))

    def test_entry_off_base_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(entry=0x400010, loads=[(5, 0x400000, 16, 16)]))

    def test_code_outside_window_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(loads=[(5, 0x500000, 16, 16)]))

    def test_data_outside_window_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(loads=[(5, 0x400000, 16, 16), (6, 0x700000, 8, 8)]))

    def test_data_gap_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(loads=[(5, 0x400000, 16, 16), (6, 0x600020, 8, 8)]))

    def test_filesz_exceeds_memsz_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(loads=[(5, 0x400000, 16, 16), (6, 0x600000, 16, 8)]))

    def test_no_code_rejected(self):
        with self.assertRaises(ValueError):
            elf_to_rnyx(_elf(loads=[(6, 0x600000, 8, 8)]))

    def test_real_backend_round_trip(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rynorlang_stage18b_program",
            ROOT / "tools" / "rynorlang" / "program.py")
        rynor_program = importlib.util.module_from_spec(spec)
        sys.modules["rynorlang_stage18b_program"] = rynor_program
        spec.loader.exec_module(rynor_program)
        work = Path(tempfile.mkdtemp(prefix="rnyx-real-", dir=ROOT / "build"))
        arts, error = rynor_program.build_rynor_program(
            "fn main(): int { return 42; }", "exit42.rl", work, prog="exit42")
        self.assertIsNone(error, error)
        blob = elf_to_rnyx(Path(arts["exe"]).read_bytes())
        magic, ver, arch, hlen, res, entry, code, fsz, msz = \
            struct.unpack("<4sHHHHIIII", blob[:28])
        self.assertEqual((magic, ver, arch, hlen, res, entry), (b"RYNX", 1, 1, 28, 0, 0))
        self.assertTrue(0 < code <= 4096)
        self.assertEqual((fsz, msz), (0, 0))
        self.assertEqual(len(blob), 28 + code)
        self.assertIn(b"\xcd\x80", blob[28:28 + code])


if __name__ == "__main__":
    unittest.main()
