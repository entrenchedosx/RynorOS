"""Stage 17b integration: real filesystem reads in QEMU through blk_read.

One shared good-filesystem boot feeds the evidence assertions; corrupt and
mutant configurations boot separately. No fixture bypasses the block
layer: the guest parses real disk bytes, and every number is recomputed
from the image file on the host.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from fs_image import build, corrupt, corrupt_entry, write_image
from fs_output import decode, file_bytes, block_sum, block_wsum, parse_serial, validate


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


def _mutate_copy(pairs, source="kernel/storage/fs.c"):
    tmp = tempfile.TemporaryDirectory(prefix="fs-fault-", dir=ROOT / "build")
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


class FilesystemIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/fs-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.good = cls.work / "fs-good.img"
        cls.good.write_bytes(build(GOOD_ENTRIES))
        cls.good_bytes = cls.good.read_bytes()
        cls.hashes = {cls.good: hashlib.sha256(cls.good_bytes).hexdigest()}
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination)
        logs = cls.work / "shared-good"
        cls.output = boot_image(cls.destination / "rynoros.img", logs, timeout=60,
                                extra_drives=(cls.good,))
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        assert summary["reaped"], summary

    def test_good_fs_full_evidence(self):
        self.assertEqual(validate(parse_serial(self.output), self.good_bytes), [])

    def test_nested_and_boundary_files_explicit(self):
        evidence = parse_serial(self.output)
        fs = decode(self.good_bytes)
        patched = {}
        for name in fs.entries:
            if fs.entries[name].ftype == 1:
                patched[name] = bytearray(file_bytes(self.good_bytes, fs, name))
        for path, off, length, hexdata in evidence.writes:
            blob = patched[path[1:]]
            blob[off:off + length] = bytes.fromhex(hexdata)
        for name in ("nested/deep/file", "docs/a.txt", "docs/b.txt", "b511",
                     "b512", "b513", "b1500", "bigfile",
                     "maxname-31-chars-abcdefg1234567", "empty", "one"):
            with self.subTest(path=name):
                blob = bytes(patched[name])
                got = evidence.files.get("/" + name)
                self.assertIsNotNone(got, name)
                self.assertEqual(got[0], len(blob))
                self.assertEqual((got[1], got[2]), (block_sum(blob), block_wsum(blob)))

    def test_handles_and_accounting_markers(self):
        evidence = parse_serial(self.output)
        self.assertTrue(evidence.handles)
        self.assertTrue(evidence.accounting)
        self.assertTrue(evidence.verified)

    def test_host_images_pristine(self):
        for path, digest in sorted(self.hashes.items(), key=lambda kv: kv[0].name):
            with self.subTest(image=path.name):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_fs_on_larger_device(self):
        padded = self.work / "fs-padded.img"
        padded.write_bytes(build(GOOD_ENTRIES, pad_blocks=16))
        logs = self.work / "padded"
        output = boot_image(self.destination / "rynoros.img", logs, timeout=60,
                            extra_drives=(padded,))
        self.assertEqual(validate(parse_serial(output), padded.read_bytes()), [])

    def _corrupt_boot(self, name, first, second):
        first_path = self.work / f"corrupt-{name}-0.img"
        second_path = self.work / f"corrupt-{name}-1.img"
        first_path.write_bytes(first)
        second_path.write_bytes(second)
        logs = self.work / f"corrupt-{name}"
        output = boot_image(self.destination / "rynoros.img", logs, timeout=60,
                            extra_drives=(self.good, first_path, second_path))
        return parse_serial(output)

    def test_corrupt_boot_a(self):
        base = build(GOOD_ENTRIES)
        evidence = self._corrupt_boot("a", corrupt(base, "magic"), corrupt(base, "version"))
        # slots: 0 boot disk (invalid), 2 bad-magic (invalid), 3 bad-version (unsupported)
        self.assertEqual(evidence.corrupts.get(0), "invalid")
        self.assertEqual(evidence.corrupts.get(2), "invalid")
        self.assertEqual(evidence.corrupts.get(3), "unsupported")
        self.assertEqual(validate(parse_serial(self._last_output("a")), self.good_bytes), [])

    def test_corrupt_boot_b(self):
        base = build(GOOD_ENTRIES)
        fs = decode(base)
        order = list(fs.entries)
        file_slot = next(i for i, n in enumerate(order) if fs.entries[n].ftype == 1)
        dup = corrupt_entry(base, 1, "dup", 0)
        extent = corrupt_entry(base, file_slot, "first", 2**40)
        for blob, code in ((dup, "corrupt"), (extent, "corrupt")):
            with self.assertRaises(ValueError) as ctx:
                decode(blob)
            self.assertEqual(ctx.exception.args[0], code)
        evidence = self._corrupt_boot("b", dup, extent)
        self.assertEqual(evidence.corrupts.get(2), "corrupt")
        self.assertEqual(evidence.corrupts.get(3), "corrupt")
        self.assertEqual(validate(parse_serial(self._last_output("b")), self.good_bytes), [])

    def test_corrupt_boot_c(self):
        base = build(GOOD_ENTRIES)
        fs = decode(base)
        order = list(fs.entries)
        with_data = [i for i, n in enumerate(order)
                     if fs.entries[n].ftype == 1 and fs.entries[n].count]
        first_a = fs.entries[order[with_data[0]]].first
        overlap = corrupt_entry(base, with_data[1], "first", first_a)
        badname = bytearray(base)
        badname[1 * 512 + 0] = 0x01
        for blob, code in ((bytes(overlap), "corrupt"), (bytes(badname), "corrupt")):
            with self.assertRaises(ValueError) as ctx:
                decode(bytes(blob))
            self.assertEqual(ctx.exception.args[0], code)
        evidence = self._corrupt_boot("c", bytes(overlap), bytes(badname))
        self.assertEqual(evidence.corrupts.get(2), "corrupt")
        self.assertEqual(evidence.corrupts.get(3), "corrupt")
        self.assertEqual(validate(parse_serial(self._last_output("c")), self.good_bytes), [])

    def _last_output(self, name):
        return (self.work / f"corrupt-{name}" / "serial.log").read_bytes()

    def _run_fs_mutation(self, pairs, source="kernel/storage/fs.c"):
        tmp, root = _mutate_copy(pairs, source)
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / self._testMethodName
        try:
            output = boot_image(root / "build" / "img" / "rynoros.img", logs, timeout=60,
                                extra_drives=(self.good,))
            return output, None
        except RuntimeError as err:
            serial = (logs / "serial.log").read_bytes()
            return serial, str(err)

    def test_mut_magic_check_removed(self):
        bad = self.work / "mut-magic.img"
        bad.write_bytes(corrupt(build(GOOD_ENTRIES), "magic"))
        tmp, root = _mutate_copy([(
            "    for (unsigned k = 0; k < FS_MAGIC_LEN; ++k)\n"
            "        if (sb[k] != (cpu_u8)FS_MAGIC[k]) { fs_stage = \"bad-magic\"; return FS_INVALID; }",
            "    { (void)sb; fs_stage = \"none\"; }",
        )])
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / "mut-magic"
        output = boot_image(root / "build" / "img" / "rynoros.img", logs, timeout=60,
                            extra_drives=(bad,))
        self.assertIn(b"[FS] mounted", output,
                      "mutant must wrongly mount the bad-magic image")
        base_logs = self.work / "mut-magic-base"
        base = boot_image(self.destination / "rynoros.img", base_logs, timeout=60,
                          extra_drives=(bad,))
        self.assertNotIn(b"[FS] mounted", base)

    def test_mut_extent_check_removed(self):
        base = build(GOOD_ENTRIES)
        fs = decode(base)
        order = list(fs.entries)
        slot = next(i for i, n in enumerate(order) if fs.entries[n].ftype == 1)
        oob = self.work / "mut-extent.img"
        oob.write_bytes(corrupt_entry(base, slot, "first", 2**40))
        tmp, root = _mutate_copy([(
            "            cpu_u64 data_end = data_start + data_blocks;\n"
            "            if (first < data_start || first >= data_end || count > data_end - first) {\n"
            "                fs_stage = \"extent-range\";\n"
            "                return FS_CORRUPT;\n"
            "            }",
            "            { (void)first; (void)data_start; (void)count; (void)data_blocks; }",
        )])
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / "mut-extent"
        with self.assertRaises(RuntimeError):
            boot_image(root / "build" / "img" / "rynoros.img", logs, timeout=60,
                       extra_drives=(self.good, oob))
        serial = (logs / "serial.log").read_bytes()
        self.assertNotIn(b"[FS] fs verified", serial)

    def test_mut_blk_bypass_detected(self):
        output, _error = self._run_fs_mutation([(
            "            int rc = blk_read(fs_dev, cur, chunk, out, (cpu_u64)chunk * 512u);\n"
            "            if (rc) { fs_stage = \"io-data\"; return FS_IOERR; }",
            "            { (void)cur; (void)chunk; (void)out; }",
        )])
        self.assertTrue(validate(parse_serial(output), self.good_bytes))

    def test_write_readback_evidence(self):
        evidence = parse_serial(self.output)
        self.assertEqual(len(evidence.writes), 8)
        paths = [w[0] for w in evidence.writes]
        self.assertEqual(paths, ["/hello", "/b512", "/b513", "/b1500",
                                 "/nested/deep/file", "/bigfile", "/bigfile",
                                 "/bigfile"])
        for path, off, length, hexdata in evidence.writes:
            with self.subTest(path=path):
                self.assertEqual(len(hexdata), 2 * length)
                bytes.fromhex(hexdata)

    def test_fault_lines_exact(self):
        evidence = parse_serial(self.output)
        self.assertEqual(evidence.faults, [("first", 0, "ioerr"),
                                           ("partial", 512, "ioerr"),
                                           ("after", 64, "ok")])

    def test_host_file_corroboration(self):
        # Snapshot OFF on a private copy: guest writes land in the file,
        # and the independent decoder reads them back from disk bytes.
        from fs_output import decode as fs_decode, file_bytes as fs_file_bytes
        owned = self.work / "owned-fs.img"
        owned.write_bytes(self.good_bytes)
        logs = self.work / "owned"
        output = boot_image(self.destination / "rynoros.img", logs, timeout=60,
                            extra_drives=[(owned, False)])
        evidence = parse_serial(output)
        self.assertEqual(validate(evidence, self.good_bytes), [])
        disk = owned.read_bytes()
        fs = fs_decode(disk)
        for path, off, length, hexdata in evidence.writes:
            with self.subTest(path=path):
                blob = fs_file_bytes(disk, fs, path[1:])
                self.assertEqual(blob[off:off + length], bytes.fromhex(hexdata))

    def test_mut_always_open_detected(self):
        output, _error = self._run_fs_mutation([(
            "    int slot = resolve(path, len);\n"
            "    if (slot < 0) return slot;\n"
            "    if (slot == FS_ROOT_SENTINEL || entry_at((cpu_u32)slot)[FS_D_TYPE] != FS_TYPE_FILE) {",
            "    int slot = 0;\n"
            "    if (0) return slot;\n"
            "    if (0) {",
        )])
        self.assertTrue(validate(parse_serial(output), self.good_bytes))

    def test_mut_write_wrong_block(self):
        output, _error = self._run_fs_mutation([(
            "    cpu_u64 first = rd64le(e + FS_D_FIRST);\n"
            "    cpu_u64 cur = first + offset / 512u;\n"
            "    cpu_u64 pos = offset % 512u;\n"
            "    const cpu_u8 *in = (const cpu_u8 *)buf;",
            "    cpu_u64 first = rd64le(e + FS_D_FIRST);\n"
            "    cpu_u64 cur = first + offset / 512u + 1;\n"
            "    cpu_u64 pos = offset % 512u;\n"
            "    const cpu_u8 *in = (const cpu_u8 *)buf;",
        )], source="kernel/storage/fs.c")
        self.assertTrue(validate(parse_serial(output), self.good_bytes))

    def test_mut_write_ignore_error(self):
        output, _error = self._run_fs_mutation([(
            "            for (cpu_u32 i = 0; i < chunk; ++i) {\n"
            "                int rc = data_write(cur + i, (const cpu_u16 *)(in + (cpu_u64)i * 512u));\n"
            "                if (rc) {\n"
            "                    if (nwritten) *nwritten = done + (cpu_u64)i * 512u;\n"
            "                    fs_stage = \"io-data\";\n"
            "                    return FS_IOERR;\n"
            "                }\n"
            "            }",
            "            for (cpu_u32 i = 0; i < chunk; ++i) {\n"
            "                (void)data_write(cur + i, (const cpu_u16 *)(in + (cpu_u64)i * 512u));\n"
            "            }",
        )], source="kernel/storage/fs.c")
        serial = output
        self.assertNotIn(b"[FS] fs verified", serial)

    def test_mut_write_always_success(self):
        output, _error = self._run_fs_mutation([(
            "    cpu_u64 first = rd64le(e + FS_D_FIRST);\n"
            "    cpu_u64 cur = first + offset / 512u;\n"
            "    cpu_u64 pos = offset % 512u;\n"
            "    const cpu_u8 *in = (const cpu_u8 *)buf;",
            "    if (len) { if (nwritten) *nwritten = len; return FS_OK; }\n"
            "    cpu_u64 first = rd64le(e + FS_D_FIRST);\n"
            "    cpu_u64 cur = first + offset / 512u;\n"
            "    cpu_u64 pos = offset % 512u;\n"
            "    const cpu_u8 *in = (const cpu_u8 *)buf;",
        )], source="kernel/storage/fs.c")
        self.assertTrue(validate(parse_serial(output), self.good_bytes))

    def test_mut_write_range_dropped(self):
        output, error = self._run_fs_mutation([(
            "    if (offset > size || len > size - offset) { fs_stage = \"past-end\"; return FS_RANGE; }",
            "    if (offset > size) { fs_stage = \"past-end\"; return FS_RANGE; }",
        )], source="kernel/storage/fs.c")
        self.assertIsNotNone(error)
        self.assertNotIn(b"[FS] fs verified", output)


if __name__ == "__main__":
    unittest.main()
