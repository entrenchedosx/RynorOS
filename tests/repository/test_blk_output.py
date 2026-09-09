"""Stage 17a host-side tests: image tool determinism and serial validator.

No QEMU here: the image builder and the evidence validator are pure host
code. Real device I/O is covered by tests/integration/test_storage.py.
"""
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from blk_image import BLOCK, MAGIC, block_sum, block_wsum, create, create_zeroed, pattern, read_block, writeback_pattern
from blk_output import file_block_sums, parse_serial, validate


class BlkImageTests(unittest.TestCase):
    def test_01_create_is_deterministic(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            first = Path(work) / "a.img"
            second = Path(work) / "b.img"
            create(first, 1)
            create(second, 1)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_02_header_layout(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            path = Path(work) / "h.img"
            create(path, 1)
            head = read_block(path, 0)
            self.assertEqual(head[:8], MAGIC)
            (blksz,) = struct.unpack("<I", head[8:12])
            (nblocks,) = struct.unpack("<Q", head[12:20])
            self.assertEqual(blksz, BLOCK)
            self.assertEqual(nblocks, 2048)
            self.assertEqual(path.stat().st_size, 1048576)

    def test_03_patterns_unique_per_block(self):
        seen = set()
        for block_no in (0, 1, 2, 7, 100, 2047):
            blob = pattern(block_no)
            self.assertEqual(len(blob), BLOCK)
            self.assertNotIn(blob, seen)
            seen.add(blob)
        self.assertEqual(block_sum(pattern(1)), sum(pattern(1)))

    def test_04_zeroed_has_no_magic(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            path = Path(work) / "z.img"
            create_zeroed(path, 1)
            self.assertEqual(path.stat().st_size, 1048576)
            self.assertEqual(read_block(path, 0), b"\x00" * BLOCK)

    def test_05_read_block_out_of_range(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            path = Path(work) / "r.img"
            create(path, 1)
            with self.assertRaises(ValueError):
                read_block(path, 2048)

    def test_06_cli_create_and_zero(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            maker = Path(work) / "c.img"
            proc = subprocess.run([sys.executable, str(ROOT / "tools/host/blk_image.py"),
                                   "create", str(maker), "1"],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(read_block(maker, 0)[:8], MAGIC)
            proc = subprocess.run([sys.executable, str(ROOT / "tools/host/blk_image.py"),
                                   "create", str(maker), "0"],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 1)
            self.assertNotIn("Traceback", proc.stderr)

    def test_06b_cli_zero(self):
        # Regression for the zero-subcommand NameError (create_zero vs
        # create_zeroed): the CLI must behave like the library call.
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            maker = Path(work) / "z.img"
            proc = subprocess.run([sys.executable, str(ROOT / "tools/host/blk_image.py"),
                                   "zero", str(maker), "1"],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(read_block(maker, 0), b"\x00" * BLOCK)
            proc = subprocess.run([sys.executable, str(ROOT / "tools/host/blk_image.py"),
                                   "zero", str(maker), "0"],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 1)
            self.assertNotIn("Traceback", proc.stderr)

    def test_07_writeback_pattern_differs(self):
        self.assertNotEqual(writeback_pattern(9), pattern(9))
        self.assertEqual(len(writeback_pattern(9)), BLOCK)


class BlkOutputTests(unittest.TestCase):
    def _evidence(self, image, wblk=1024):
        lines = [b"[BLK] devices=2 test=1 blocks=2048"]
        for blk in (0, 1, 2, 2047):
            want_sum, want_wsum = file_block_sums(image, blk)
            lines.append(b"[BLK] read blk=%d sum=%d wsum=%d" % (blk, want_sum, want_wsum))
        lines.append(b"[BLK] bootsec aa55=1")
        wb = writeback_pattern(wblk)
        lines.append(b"[BLK] writeback blk=%d sum=%d wsum=%d" % (wblk, block_sum(wb), block_wsum(wb)))
        for neighbor in (wblk - 1, wblk + 1):
            want_sum, want_wsum = file_block_sums(image, neighbor)
            lines.append(b"[BLK] neighbor blk=%d sum=%d wsum=%d" % (neighbor, want_sum, want_wsum))
        lines.append(b"[BLK] storage verified")
        return b"\r\n".join(lines) + b"\r\n"

    def test_08_parse_serial_fields(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            image = Path(work) / "p.img"
            create(image, 1)
            evidence = parse_serial(self._evidence(image))
            self.assertEqual((evidence.devices, evidence.test_id, evidence.blocks), (2, 1, 2048))
            self.assertEqual(len(evidence.reads), 4)
            self.assertTrue(evidence.bootsec)
            self.assertTrue(evidence.verified)
            self.assertEqual(evidence.failures, [])

    def test_09_validate_accepts_genuine_evidence(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            image = Path(work) / "v.img"
            create(image, 1)
            self.assertEqual(validate(parse_serial(self._evidence(image)), image), [])

    def test_10_validate_rejects_tampering(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            image = Path(work) / "t.img"
            create(image, 1)
            good = self._evidence(image)
            self.assertTrue(validate(parse_serial(good.replace(b"storage verified", b"")), image))
            mutated = good.replace(b"sum=%d" % block_sum(read_block(image, 1)), b"sum=1", 1)
            self.assertTrue(validate(parse_serial(mutated), image))
            failure = good + b"[BLK] failure=editor_reject\r\n"
            self.assertTrue(validate(parse_serial(failure), image))

    def test_10b_wrong_block_caught_by_wsum(self):
        # Every patterned data block shares one byte-sum by construction;
        # the weighted sum must still distinguish a rotated wrong block.
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            image = Path(work) / "w.img"
            create(image, 1)
            self.assertEqual(block_sum(read_block(image, 1)), block_sum(read_block(image, 2)))
            self.assertNotEqual(file_block_sums(image, 1), file_block_sums(image, 2))
            good = self._evidence(image)
            s1, w1 = file_block_sums(image, 1)
            _s2, w2 = file_block_sums(image, 2)
            honest = b"[BLK] read blk=1 sum=%d wsum=%d" % (s1, w1)
            forged = b"[BLK] read blk=1 sum=%d wsum=%d" % (s1, w2)
            self.assertIn(honest, good)
            mislabeled = good.replace(honest, forged, 1)
            self.assertTrue(validate(parse_serial(mislabeled), image))

    def test_11_validate_rejects_wrong_image(self):
        with tempfile.TemporaryDirectory(prefix="blkimg-") as work:
            image = Path(work) / "w.img"
            create(image, 1)
            other = Path(work) / "o.img"
            create_zeroed(other, 1)
            evidence = parse_serial(self._evidence(image))
            self.assertTrue(validate(evidence, other))

    def test_12_error_codes_distinct(self):
        codes = {"ok": 0, "invalid": -1, "range": -2, "unsupported": -3,
                 "nodev": -4, "init-fail": -5, "ioerr": -6, "timeout": -7, "denied": -8}
        self.assertEqual(len(set(codes.values())), len(codes))
        self.assertEqual(codes["ok"], 0)
        self.assertTrue(all(v < 0 for k, v in codes.items() if k != "ok"))


if __name__ == "__main__":
    unittest.main()
