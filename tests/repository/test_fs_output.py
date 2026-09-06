"""Stage 17b host-side tests: image builder, independent decoder, validator.

No QEMU here: builder determinism, format layout, decoder rejection codes,
and validator accept/reject are pure host logic. Real device reads run in
tests/integration/test_filesystem.py.
"""
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
import fs_image
from fs_image import MAGIC, VERSION, BLOCK, build, corrupt, corrupt_entry, write_image
from fs_output import FS_CODES, decode, file_bytes, block_sum, block_wsum, parse_serial, validate


GOOD_ENTRIES = [
    ("/hello", b"Hello, RynorOS!\n"),
    ("/readme.txt", b"RynorOS filesystem test image.\nSecond line.\n"),
    ("/docs", None),
    ("/docs/a.txt", b"alpha\n"),
    ("/docs/b.txt", b"beta\n"),
    ("/bin", None),
    ("/bin/test", b"#!/bin/test\n"),
    ("/nested", None),
    ("/nested/deep", None),
    ("/nested/deep/file", b"deep content here\n"),
    ("/empty", b""),
    ("/one", b"X"),
    ("/b511", b"a" * 511),
    ("/b512", b"b" * 512),
    ("/b513", b"c" * 513),
    ("/b1500", b"d" * 1500),
    ("/bigfile", b"e" * 65536),
    ("/maxname-31-chars-abcdefg1234567", b"31\n"),
]


def _good_image():
    return build(GOOD_ENTRIES)


class FsImageTests(unittest.TestCase):
    def test_01_builder_deterministic(self):
        self.assertEqual(build(GOOD_ENTRIES), build(list(GOOD_ENTRIES)))
        subset = [("/hello", b"a"), ("/docs/a.txt", b"b"), ("/bin/test", b"c")]
        self.assertEqual(build(subset), build(list(reversed(subset))))

    def test_02_header_layout(self):
        image = _good_image()
        self.assertEqual(image[0:8], MAGIC)
        self.assertEqual(struct.unpack("<I", image[8:12])[0], VERSION)
        self.assertEqual(struct.unpack("<I", image[12:16])[0], BLOCK)
        total = struct.unpack("<Q", image[16:24])[0]
        self.assertEqual(len(image), total * BLOCK)
        self.assertEqual(struct.unpack("<Q", image[24:32])[0], 1)
        self.assertEqual(image[56:64], b"\x00" * 8)
        self.assertEqual(image[64:BLOCK], b"\x00" * (BLOCK - 64))

    def test_03_empty_filesystem(self):
        image = build([])
        fs = decode(image)
        self.assertEqual(fs.entries, {})
        self.assertEqual(len(image), 3 * BLOCK)

    def test_04_nested_dirs_autocreated(self):
        fs = decode(build([("/a/b/c.txt", b"x")]))
        self.assertEqual(sorted(fs.entries), ["a", "a/b", "a/b/c.txt"])
        self.assertEqual(fs.entries["a"].ftype, 2)

    def test_05_builder_rejections(self):
        with self.assertRaises(ValueError):
            build([("dup", b"1"), ("dup", b"2")])
        with self.assertRaises(ValueError):
            build([("x", b"1"), ("x", None)])
        with self.assertRaises(ValueError):
            build([("x", None), ("x/y", b"1")])
        with self.assertRaises(ValueError):
            build([("y" * 32, b"1")])
        with self.assertRaises(ValueError):
            build([("no-leading", b"1"), ("bad", b"1"), ("nodir/x", b"1"), ("nodir", b"1")])
        with self.assertRaises(ValueError):
            build([("trail/", b"1")])

    def test_06_cli_create_and_errors(self):
        with tempfile.TemporaryDirectory(prefix="fsimg-") as work:
            manifest = Path(work) / "m.json"
            out = Path(work) / "fs.img"
            manifest.write_text(json.dumps({"files": [
                {"path": "/hello", "content": "hi"},
                {"path": "/docs", "content": None}]}), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(ROOT / "tools/host/fs_image.py"),
                                   "create", str(manifest), str(out)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            fs = decode(out.read_bytes())
            self.assertIn("hello", fs.entries)
            self.assertIn("docs", fs.entries)
            manifest.write_text("{bad json", encoding="utf-8")
            proc = subprocess.run([sys.executable, str(ROOT / "tools/host/fs_image.py"),
                                   "create", str(manifest), str(out)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 1)
            self.assertNotIn("Traceback", proc.stderr)

    def test_07_maxname_boundary(self):
        fs = decode(build([("/" + "z" * 31, b"v")]))
        self.assertIn("z" * 31, fs.entries)
        with self.assertRaises(ValueError):
            build([("/" + "z" * 32, b"v")])

    def test_08_bigfile_layout(self):
        fs = decode(_good_image())
        entry = fs.entries["bigfile"]
        self.assertEqual(entry.length, 65536)
        self.assertEqual(entry.count, 128)
        self.assertEqual(len(file_bytes(_good_image(), fs, "bigfile")), 65536)


class FsDecoderTests(unittest.TestCase):
    def test_09_good_image_decodes(self):
        fs = decode(_good_image())
        self.assertEqual(len([e for e in fs.entries.values() if e.ftype == 1]), 14)
        self.assertEqual(len([e for e in fs.entries.values() if e.ftype == 2]), 4)
        self.assertEqual(file_bytes(_good_image(), fs, "hello"), b"Hello, RynorOS!\n")
        self.assertEqual(file_bytes(_good_image(), fs, "empty"), b"")

    def test_10_superblock_corruption_codes(self):
        image = _good_image()
        for kind, code in (("magic", "invalid"), ("version", "unsupported"),
                           ("blksize", "unsupported"), ("reserved", "corrupt"),
                           ("total0", "corrupt"), ("dir_oob", "corrupt"),
                           ("dir_big", "unsupported"), ("data_overlap", "corrupt"),
                           ("trunc", "corrupt")):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError) as ctx:
                    decode(corrupt(image, kind))
                self.assertEqual(ctx.exception.args[0], code)

    def test_11_entry_corruption_codes(self):
        image = _good_image()
        fs = decode(image)
        # entries dict preserves slot order: first file and first dir slots.
        order = list(fs.entries)
        file_index = next(i for i, name in enumerate(order) if fs.entries[name].ftype == 1)
        dir_index = next(i for i, name in enumerate(order) if fs.entries[name].ftype == 2)
        cases = [("dup", 1, None, "corrupt"),
                 ("name_garbage", file_index, None, "corrupt"),
                 ("type", file_index, 9, "corrupt"),
                 ("first", file_index, 2**40, "corrupt"),
                 ("length", file_index, 2**40, "corrupt"),
                 ("length", dir_index, 5, "corrupt")]
        for field, index, value, code in cases:
            with self.subTest(field=field, index=index):
                if field == "dup":
                    bad = corrupt_entry(image, 1, "dup", 0)
                elif field == "name_garbage":
                    bad = corrupt_entry(image, index, "name_garbage", 0)
                else:
                    bad = corrupt_entry(image, index, field, value)
                with self.assertRaises(ValueError) as ctx:
                    decode(bad)
                self.assertEqual(ctx.exception.args[0], code)

    def test_12_error_codes_documented(self):
        self.assertEqual(FS_CODES, {
            "ok", "invalid", "notfound", "notfile", "notdir", "badhandle",
            "range", "ioerr", "corrupt", "unsupported", "busy",
        })


class FsValidatorTests(unittest.TestCase):
    def _evidence(self, image, files):
        from fs_output import parse_serial as ps
        lines = []
        fs = decode(image)
        lines.append(b"[FS] mounted dev=1 blocks=%d" % fs.total)
        for name in files:
            blob = file_bytes(image, fs, name)
            lines.append(b"[FS] file path=/%s size=%d sum=%d wsum=%d"
                         % (name.encode(), len(blob), block_sum(blob), block_wsum(blob)))
        lines += [b"[FS] handles ok", b"[FS] accounting balanced", b"[FS] fs verified"]
        return ps(b"\r\n".join(lines) + b"\r\n")

    def test_13_accepts_genuine_evidence(self):
        image = _good_image()
        fs = decode(image)
        names = [n for n, e in fs.entries.items() if e.ftype == 1]
        self.assertEqual(validate(self._evidence(image, names), image), [])

    def test_14_rejects_tampering(self):
        image = _good_image()
        fs = decode(image)
        names = [n for n, e in fs.entries.items() if e.ftype == 1]
        good = self._evidence(image, names)
        self.assertTrue(validate(good, image) == [])
        import copy
        forged = copy.copy(good)
        forged.events = [e if not (e[0] == "file" and e[1] == "/hello")
                         else ("file", "/hello", e[2], e[3] + 1, e[4])
                         for e in good.events]
        self.assertTrue(validate(forged, image))
        missing = copy.copy(good)
        missing.events = [e for e in good.events if e[0] != "file"]
        missing.verified = False
        self.assertTrue(validate(missing, image))

    def test_15_rejects_wrong_image(self):
        image = _good_image()
        fs = decode(image)
        names = [n for n, e in fs.entries.items() if e.ftype == 1]
        other = build([("/other", b"data")])
        self.assertTrue(validate(self._evidence(image, names), other))

    def test_16_part_slices_recomputed(self):
        image = _good_image()
        fs = decode(image)
        from fs_output import FsEvidence
        blob = file_bytes(image, fs, "b1500")
        window = blob[500:1100]
        files = {}
        events = [("mount", 1, fs.total)]
        for name in sorted(n for n, e in fs.entries.items() if e.ftype == 1):
            content = file_bytes(image, fs, name)
            row = (len(content), block_sum(content), block_wsum(content))
            files["/" + name] = row
            events.append(("file", "/" + name) + row)
        events.append(("part", "/b1500", 500, 600, block_sum(window), block_wsum(window)))
        evidence = FsEvidence(
            mounted=(1, fs.total),
            files=files,
            parts=[("/b1500", 500, 600, block_sum(window), block_wsum(window))],
            handles=True, accounting=True, verified=True,
            events=events)
        self.assertEqual(validate(evidence, image), [])
        import copy
        forged = copy.copy(evidence)
        forged.events = [e if e[0] != "part"
                         else ("part", "/b1500", 500, 600, block_sum(window) + 1,
                               block_wsum(window))
                         for e in evidence.events]
        self.assertTrue(validate(forged, image))
        forged = copy.copy(evidence)
        forged.events = [e for e in evidence.events if e[0] != "part"]
        forged.events.append(("part", "/b1500", 1400, 600, 0, 0))
        self.assertTrue(validate(forged, image))

    def _write_evidence(self, image):
        from fs_output import FsEvidence
        fs = decode(image)
        files = {}
        events = [("mount", 1, fs.total)]
        for name in sorted(n for n, e in fs.entries.items() if e.ftype == 1):
            content = file_bytes(image, fs, name)
            row = (len(content), block_sum(content), block_wsum(content))
            files["/" + name] = row
            events.append(("file", "/" + name) + row)
        return FsEvidence(mounted=(1, fs.total), files=files, handles=True,
                          accounting=True, verified=True, events=events)

    def test_17_writes_patch_expectations(self):
        image = _good_image()
        evidence = self._write_evidence(image)
        base = [e for e in evidence.events if e[0] != "file"]
        files = [e for e in evidence.events if e[0] == "file"]
        # Stale (pre-write) file evidence no longer matches once writes apply.
        evidence.events = (base + [("write", "/hello", 0, 5, b"HELLO".hex().upper()),
                                   ("write", "/b512", 511, 1, b"Z".hex())] + files)
        self.assertTrue(validate(evidence, image))
        # Fresh post-write lines match patched content.
        patched_hello = b"HELLO, RynorOS!\n"
        patched_b512 = b"b" * 511 + b"Z"
        evidence.events = (base + [("write", "/hello", 0, 5, b"HELLO".hex().upper()),
                                   ("write", "/b512", 511, 1, b"Z".hex())]
                           + [e for e in files if e[1] not in ("/hello", "/b512")]
                           + [("file", "/hello", 16, block_sum(patched_hello),
                                block_wsum(patched_hello)),
                              ("file", "/b512", 512, block_sum(patched_b512),
                               block_wsum(patched_b512))])
        self.assertEqual(validate(evidence, image), [])
        evidence.events.append(("file", "/hello", 16, 0, 0))
        self.assertTrue(validate(evidence, image))

    def test_18_malformed_write_lines_rejected(self):
        image = _good_image()
        evidence = self._write_evidence(image)
        base = [e for e in evidence.events if e[0] != "file"]
        files = [e for e in evidence.events if e[0] == "file"]
        evidence.events = base + [("write", "/hello", 0, 5, "ZZZ")] + files
        self.assertTrue(validate(evidence, image))
        evidence.events = base + [("write", "/missing", 0, 1, "41")] + files
        self.assertTrue(validate(evidence, image))
        evidence.events = base + [("write", "/hello", 14, 5, "4141414141")] + files
        self.assertTrue(validate(evidence, image))

    def test_19_write_order_applies_sequentially(self):
        image = _good_image()
        evidence = self._write_evidence(image)
        base = [e for e in evidence.events if e[0] != "file"]
        files = [e for e in evidence.events if e[0] == "file"]
        blob = b"B"
        evidence.events = (base + [("write", "/one", 0, 1, "41"),
                                   ("write", "/one", 0, 1, "42")]
                           + [e for e in files if e[1] != "/one"]
                           + [("file", "/one", 1, block_sum(blob), block_wsum(blob))])
        self.assertEqual(validate(evidence, image), [])

    def test_20_fault_lines_parsed(self):
        image = _good_image()
        evidence = self._write_evidence(image)
        self.assertEqual(evidence.faults, [])
        from fs_output import parse_serial
        parsed = parse_serial(b"[FS] fault case=partial written=512 code=ioerr\r\n")
        self.assertEqual(parsed.faults, [("partial", 512, "ioerr")])


if __name__ == "__main__":
    unittest.main()
