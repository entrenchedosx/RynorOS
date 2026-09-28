"""P1-A2: CPL3 file create/write exposure (syscalls 9/10) in QEMU.

Slice plan: the marker-gated kernel driver (fs-test.c p1a2_cases, runs
only on images carrying /p1a2-go) pins the kern_fcreate/kern_fwrite
cores -- fault injection, unmounted mappings, kernel-buffer checks,
content, and reboot legs. The full create/write error matrix, binary
and multi-write coverage, relocation, adversarial probes, and the
shell echo-redirect path run through the real gate in CPL3 probe and
shell tests below (image bytes are free; the kernel loads high
under BOOT-A1 with a 16 MiB memory budget, not the old link window).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT / "tests/integration"))
from image import build_image  # noqa: E402
from fs_image import build as fs_build  # noqa: E402
from fs_output import decode, file_bytes, block_sum, block_wsum  # noqa: E402
from fs_output import parse_serial  # noqa: E402
from test_filesystem import GOOD_ENTRIES  # noqa: E402

P1A2_ENTRIES = GOOD_ENTRIES + [("/p1a2-go", b"")]

# /k-new segments: (start, end, seed) with byte[p] == (p*13+seed)&0xff
# (absolute-offset formula: crossing + relocate + tail; pure
# overwrite rides the CPL3 probe instead).
KNEW_SEGS = [(0, 1450, 0x61), (1450, 1550, 0x63),
             (1550, 6550, 0x64), (6550, 6650, 0x66)]
KNEW_SIZES = (1500, 6550, 6650)

P1A2_NEGS_MAIN = {("write", "toolen"): 2, ("write", "oddbuf"): 2,
                  ("write", "nullbuf"): 2, ("create", "unmounted"): 10,
                  ("write", "unmounted"): 3}
P1A2_NEGS_REBOOT = {("write", "toolen"): 2, ("write", "oddbuf"): 2,
                    ("write", "nullbuf"): 2}
P1A2_FAULTS = [("f-reloc", 10), ("f-data", 10), ("f-dir", 10)]


def knew_bytes(size):
    # The 1500-byte row predates the overwrite (single seed 0x61).
    segs = [(0, size, 0x61)] if size <= 1500 else KNEW_SEGS
    out = bytearray()
    for start, end, seed in segs:
        if start >= size:
            break
        for pos in range(start, min(end, size)):
            out.append((pos * 13 + seed) & 0xFF)
    assert len(out) == size
    return bytes(out)


def knew_row(size):
    blob = knew_bytes(size)
    return (size, (size + 511) // 512, block_sum(blob), block_wsum(blob))


class P1A2KernTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a2-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination)
        cls.rynoros = cls.destination / "rynoros.img"

    def _boot(self, name, *drives, timeout=60):
        from qemu import boot_image
        logs = self.work / name
        out = boot_image(self.rynoros, logs, timeout=timeout,
                         extra_drives=tuple(drives))
        evidence = parse_serial(out)
        self.assertEqual(evidence.failures, [])
        self.assertTrue(evidence.verified)
        return evidence

    def _main_image(self):
        return fs_build(P1A2_ENTRIES, data_slack=48, dir_slack=1)

    def test_p1a2_kern_evidence(self):
        drive = self.work / "p1a2-kern.img"
        drive.write_bytes(self._main_image())
        evidence = self._boot("kern", drive)
        self.assertEqual(evidence.p1a2_negs, P1A2_NEGS_MAIN)
        self.assertEqual(evidence.p1a2_faults, P1A2_FAULTS)
        self.assertEqual(evidence.p1a2_reboots, {})
        want_writes = [("/k-new",) + knew_row(size) for size in KNEW_SIZES]
        self.assertEqual(evidence.p1a2_writes, want_writes)

    def test_p1a2_marker_gate(self):
        drive = self.work / "p1a2-plain.img"
        drive.write_bytes(fs_build(GOOD_ENTRIES))
        evidence = self._boot("plain", drive)
        self.assertEqual(evidence.p1a2_negs, {})
        self.assertEqual(evidence.p1a2_writes, [])
        self.assertEqual(evidence.p1a2_reboots, {})
        self.assertEqual(evidence.p1a2_faults, [])

    def _check_kern_drive(self, data, original, evidence):
        fs = decode(data)  # raises on any overlap/range/format violation
        before = decode(original)
        patched = {}
        for name in before.entries:
            if before.entries[name].ftype == 1:
                patched[name] = bytearray(file_bytes(original, before, name))
        for path, off, length, hexdata in evidence.writes:
            blob = patched[path[1:]]
            blob[off:off + length] = bytes.fromhex(hexdata)
        for key, entry in before.entries.items():
            if entry.ftype != 1:
                continue
            with self.subTest(entry=key):
                now = fs.entries[key]
                if key == "b1500":
                    # Same fault-leftover discipline as the P1-A1
                    # checker: bytes [0,512) are 17c f-partial/f-after
                    # staging reuse, pinned only by counts/codes there
                    # and by boot-vs-boot determinism.
                    self.assertEqual((now.first, now.count, now.length),
                                     (entry.first, entry.count, entry.length))
                    self.assertEqual(file_bytes(data, fs, key)[512:],
                                     bytes(patched[key][512:]))
                    continue
                if key == "one":
                    # The 17c section grows /one to "OK" on every image.
                    self.assertEqual((now.count, now.length), (1, 2))
                    expect = bytearray(patched[key])
                    expect[0:2] = b"OK"
                    self.assertEqual(file_bytes(data, fs, key), bytes(expect))
                else:
                    self.assertEqual((now.first, now.count, now.length),
                                     (entry.first, entry.count, entry.length))
                    self.assertEqual(file_bytes(data, fs, key),
                                     bytes(patched[key]))
        with self.subTest(entry="k-new"):
            self.assertEqual(file_bytes(data, fs, "k-new"), knew_bytes(6650))
            self.assertEqual((fs.entries["k-new"].count,
                              fs.entries["k-new"].length), (13, 6650))

    def test_p1a2_kern_persist_to_disk(self):
        drive = self.work / "p1a2-kern-persist.img"
        original = self._main_image()
        drive.write_bytes(original)
        evidence = self._boot("persist1", (drive, False))
        self.assertEqual(evidence.p1a2_negs, P1A2_NEGS_MAIN)
        self._check_kern_drive(drive.read_bytes(), original, evidence)

    def test_p1a2_kern_reboot_readback(self):
        drive = self.work / "p1a2-kern-reboot.img"
        drive.write_bytes(self._main_image())
        first = self._boot("reboot1", (drive, False))
        self.assertEqual(first.p1a2_faults, P1A2_FAULTS)
        second = self._boot("reboot2", (drive, False))
        # Verify-only: reboot rows present, no new writes or faults.
        self.assertEqual(second.p1a2_negs, P1A2_NEGS_REBOOT)
        self.assertEqual(second.p1a2_writes, [])
        self.assertEqual(second.p1a2_faults, [])
        self.assertEqual(second.p1a2_reboots,
                         {"/k-new": knew_row(6650)})
        self._check_kern_drive(drive.read_bytes(), self._main_image(),
                               second)


def _fmt(off, size, seed):
    return bytes(((off + i) * 13 + seed) & 0xFF for i in range(size))


def _seg_a():
    return _fmt(0, 100, 0xA2) + _fmt(100, 1350, 0xA1) + _fmt(1450, 100, 0xA3)


def _seg_b16k():
    return _fmt(0, 16384, 0xB0)


def _seg_c():
    head = _fmt(0, 400, 0xC1) + _fmt(400, 600, 0xC2)
    return head + bytes((i * 7 + 3) & 0xFF for i in range(256))


def _seg_rb():
    return (_fmt(0, 100, 0xD2) + _fmt(100, 100, 0xD5)
            + _fmt(200, 2000, 0xD3))


P1A2_SCRIPT = """echo rynoros-persist-123 > /proof.txt
cat /proof.txt
echo second > /proof.txt
echo hello world > /a
cat /a
echo > /b
cat /b
echo    spaced    > /c
cat /c
echo hello>/d
cat /d
fcwprobe
cat /w/a
cat /w/rb
"""
P1A2_DONES_BOOT1 = [0, 0, 11, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
P1A2_DONES_BOOT2 = [11, 0, 11, 11, 0, 11, 0, 11, 0, 11, 0, 0, 0, 0]


def _slot1_want():
    # Only cat children write to serial slot 1: every redirected echo
    # is pipe-captured, so its bytes never reach serial — slot 1 is
    # the cat file bytes in script order (fcwprobe is silent). Identical
    # on both boots: the files persist, so the cats replay the bytes.
    return (b"rynoros-persist-123" + b"hello world" + b"" + b"spaced"
            + b"hello" + b"" + _seg_a() + _seg_rb())


def _compile_p1a2(work, root=ROOT):
    """Compile the P1-A2 shell tree: sh (v2) + echo/cat/fput (v1) +
    the fcwprobe/fcfull probes (v2, image bytes are free)."""
    import sys as _sys
    _sys.path.insert(0, str(root / "tools/rynorlang"))
    _sys.path.insert(0, str(root))
    from tools.rynorlang import program as rynor_program
    from tools.host import rnyx
    from test_cplshell import SHELL_SOURCES, SHELL_HEADERS
    out = {}
    rtpipe = (root / "user/lib/rt/rt_pipe.h").read_text(encoding="utf-8")
    rtfs = (root / "user/lib/rt/rt_fs.h").read_text(encoding="utf-8")
    sources = {}
    for name in SHELL_SOURCES + SHELL_HEADERS:
        sources[name] = (root / "user/shell" / name).read_text(encoding="utf-8")
    sources["rt_pipe.h"] = rtpipe
    progdir = work / "prog-sh"
    progdir.mkdir(parents=True, exist_ok=True)
    arts, error = rynor_program.build_rynor_c_program(
        sources, progdir, prog="sh", link_script="rynoros_v2.ld")
    assert error is None, ("sh", error)
    out["sh"] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes(), version=2)
    helpers = (("sh_echo", "echo", 1, False), ("sh_cat", "cat", 1, True),
               ("sh_fput", "fput", 1, True), ("p_fcwprobe", "fcwprobe", 2, True),
               ("p_fcwfull", "fcfull", 2, True))
    for src_name, bin_name, version, want_fs in helpers:
        src = (root / "user/proc-tests" / (src_name + ".c")).read_text(encoding="utf-8")
        progdir = work / f"prog-{bin_name}"
        progdir.mkdir(parents=True, exist_ok=True)
        extra = {"rt_fs.h": rtfs} if want_fs else {}
        kw = {"link_script": "rynoros_v2.ld"} if version == 2 else {}
        arts, error = rynor_program.build_rynor_c_program(
            {src_name + ".c": src, "rt_pipe.h": rtpipe, **extra},
            progdir, prog=bin_name, **kw)
        assert error is None, (bin_name, error)
        out[bin_name] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes(),
                                         version=version)
    return out


class P1A2ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a2-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.blobs = _compile_p1a2(cls.work / "progs")
        cls.dest = cls.work / "sh-image"
        build_image(ROOT, cls.dest, shell_boot=True,
                    shell_script="/test/session.sh")
        cls.rynoros = cls.dest / "rynoros.img"
        entries = (list(GOOD_ENTRIES) + [("/bin/sh", cls.blobs["sh"]),
                   ("/bin/echo", cls.blobs["echo"]),
                   ("/bin/cat", cls.blobs["cat"]),
                   ("/bin/fput", cls.blobs["fput"]),
                   ("/bin/fcwprobe", cls.blobs["fcwprobe"]), ("/test", None),
                   ("/test/session.sh", P1A2_SCRIPT.encode()), ("/w", None)])
        cls.drive = cls.work / "p1a2-shell.img"
        cls.drive.write_bytes(fs_build(entries, data_slack=128, dir_slack=4))
        cls.original = cls.drive.read_bytes()

    def _shell_boot(self, name, drive):
        from qemu import boot_image
        from sh_output import (validate_sh_section, collect_sh_rows,
                               collect_load_writes, collect_done_statuses)
        logs = self.work / name
        out = boot_image(self.rynoros, logs, timeout=60,
                         extra_drives=((drive, False),), require_sh=True,
                         sh_done=b"[SHD] halt code=0\r\n")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        self.assertIn("[SH] script /test/session.sh", rows)
        self.assertIn("[SHD] halt code=0", rows)
        return out, rows, collect_done_statuses(out), collect_load_writes(out)

    def _check_shell_drive(self, data, original, writes):
        fs = decode(data)
        before = decode(original)
        # The 17c battery overwrites fixed ranges on every boot; patch
        # those into the baseline from the printed write rows (same
        # discipline as test_p1a._check_persisted_drive). The p1a/p1a2
        # batteries are marker-gated off this drive, so /b511 stays
        # pristine here (no P1-A growth).
        patched = {}
        for name in before.entries:
            if before.entries[name].ftype == 1:
                patched[name] = bytearray(file_bytes(original, before, name))
        for path, off, length, hexdata in writes:
            blob = patched[path[1:]]
            blob[off:off + length] = bytes.fromhex(hexdata)
        for key, entry in before.entries.items():
            if entry.ftype != 1:
                continue
            with self.subTest(entry=key):
                now = fs.entries[key]
                if key == "b1500":
                    # Fault-injection leftovers in [0,512), pinned only
                    # boot-vs-boot by the determinism leg; the 17c patch
                    # at [750,758) lands verbatim. Mirror test_p1a.
                    self.assertEqual((now.first, now.count, now.length),
                                     (entry.first, entry.count, entry.length))
                    self.assertEqual(file_bytes(data, fs, key)[512:],
                                     bytes(patched[key][512:]))
                    continue
                if key == "one":
                    self.assertEqual((now.count, now.length), (1, 2))
                    expect = bytearray(patched[key])
                    expect[0:2] = b"OK"
                    self.assertEqual(file_bytes(data, fs, key), bytes(expect))
                    continue
                self.assertEqual((now.first, now.count, now.length),
                                 (entry.first, entry.count, entry.length))
                self.assertEqual(file_bytes(data, fs, key),
                                 bytes(patched[key]))
        want = {"/proof.txt": b"rynoros-persist-123",
                "/a": b"hello world", "/b": b"", "/c": b"spaced",
                "/d": b"hello", "/w/a": _seg_a(), "/w/b": _seg_b16k(),
                "/w/c": _seg_c(), "/w/ra": _fmt(0, 1500, 0xD1),
                "/w/rb": _seg_rb(), "/w/rc": _fmt(0, 1500, 0xD4),
                "/test/w-nested": _fmt(0, 100, 0xE0),
                "/w/w1": _fmt(0, 10, 0xE1),
                "/1234567890123456789012345678901": b"",
                "/123456789012345678901234567890": b""}
        for path, content in want.items():
            with self.subTest(entry=path):
                key = path[1:]
                self.assertEqual(file_bytes(data, fs, key), content)
                entry = fs.entries[key]
                self.assertEqual(entry.length, len(content))
                if not content:
                    self.assertEqual((entry.first, entry.count), (0, 0))
        for missing in ("w/zz", "w/nope"):
            with self.subTest(entry=missing):
                self.assertNotIn(missing, fs.entries)
        five = {0x00, 0x01, 0x7F, 0x80, 0xFF}
        self.assertTrue(five <= set(file_bytes(data, fs, "w/c")[1000:1256]))

    def test_shell_redirect_probe_e2e(self):
        from test_cplshell import _slot_blob
        from sh_output import collect_load_writes
        self.drive.write_bytes(self.original)
        out1, rows1, dones1, _ = self._shell_boot("shell1", self.drive)
        self.assertEqual(dones1, P1A2_DONES_BOOT1)
        self.assertEqual(rows1.count("[SH] error redirect 11"), 1)
        blob1 = _slot_blob(collect_load_writes(out1), 1)
        self.assertEqual(bytes.fromhex(blob1), _slot1_want())
        after1 = self.drive.read_bytes()
        self._check_shell_drive(after1, self.original,
                                parse_serial(out1).writes)
        out2, rows2, dones2, _ = self._shell_boot("shell2", self.drive)
        self.assertEqual(dones2, P1A2_DONES_BOOT2)
        self.assertEqual(rows2.count("[SH] error redirect 11"), 6)
        blob2 = _slot_blob(collect_load_writes(out2), 1)
        self.assertEqual(bytes.fromhex(blob2), _slot1_want())
        after2 = self.drive.read_bytes()
        self._check_shell_drive(after2, self.original,
                                parse_serial(out2).writes)
        # Same drive object across boots (no regeneration) and the
        # second boot wrote nothing new: every redirect collided and
        # the probe took its verify-only path. Byte-identity is NOT
        # expected (test_p1a.py: fault-injection leftovers in
        # b1500[0,512) legitimately differ once the image carries
        # first-boot 17c overwrites), so that one block is masked.
        import hashlib
        fs1 = decode(after1)
        first = fs1.entries["b1500"].first
        mask1 = bytearray(after1)
        mask2 = bytearray(after2)
        mask1[first * 512:first * 512 + 512] = b"\0" * 512
        mask2[first * 512:first * 512 + 512] = b"\0" * 512
        self.assertEqual(hashlib.sha256(mask1).digest(),
                         hashlib.sha256(mask2).digest())


class P1A2FullTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a2-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        if not hasattr(P1A2ShellTests, "blobs"):
            P1A2ShellTests.setUpClass()
        cls.blobs = P1A2ShellTests.blobs
        cls.rynoros = P1A2ShellTests.rynoros

    def _shell_boot(self, name, drive):
        from qemu import boot_image
        from sh_output import (validate_sh_section, collect_sh_rows,
                               collect_done_statuses)
        logs = self.work / name
        out = boot_image(self.rynoros, logs, timeout=60,
                         extra_drives=((drive, False),), require_sh=True,
                         sh_done=b"[SHD] halt code=0\r\n")
        self.assertEqual(validate_sh_section(out), [])
        return out, collect_sh_rows(out), collect_done_statuses(out)

    def test_directory_full_through_cpl3(self):
        fixed = (list(GOOD_ENTRIES) + [("/bin/sh", self.blobs["sh"]),
                 ("/bin/fcfull", self.blobs["fcfull"]), ("/test", None),
                 ("/test/session.sh", b"fcfull dir\n"), ("/w", None),
                 ("/w/last", bytes([0xAB]) * 64)])
        nfixed = len(decode(fs_build(fixed)).entries)
        fills = [("/w/s%03d" % i, bytes([i & 0xFF]) * 64)
                 for i in range(500 - nfixed)]
        self.assertGreater(len(fills), 400)
        drive = self.work / "p1a2-dirfull.img"
        # 500 entries in a 512-slot directory: exactly 12 creates fit.
        drive.write_bytes(fs_build(fixed + fills, data_slack=8,
                                   dir_slack=64 - (500 + 7) // 8))
        _, _, dones = self._shell_boot("dirfull", drive)
        self.assertEqual(dones, [0])
        fs = decode(drive.read_bytes())
        self.assertEqual(len(fs.entries), 512)
        made = sorted(k for k in fs.entries if k.startswith("w/f"))
        self.assertEqual(len(made), 12)
        for key in made:
            entry = fs.entries[key]
            self.assertEqual((entry.first, entry.count, entry.length),
                             (0, 0, 0))
        last = fs.entries["w/last"]
        self.assertEqual((last.count, last.length), (2, 600))
        expect = bytes([0xAB]) * 64 + _fmt(64, 536, 0xF1)
        self.assertEqual(file_bytes(drive.read_bytes(), fs, "w/last"),
                         expect)
        for i in (0, 1, 100, len(fills) - 1):
            key = "w/s%03d" % i
            self.assertEqual(file_bytes(drive.read_bytes(), fs, key),
                             bytes([i & 0xFF]) * 64)

    def test_disk_full_through_cpl3(self):
        entries = (list(GOOD_ENTRIES) + [("/bin/sh", self.blobs["sh"]),
                   ("/bin/fcfull", self.blobs["fcfull"]), ("/test", None),
                   ("/test/session.sh", b"fcfull disk\n"), ("/w", None)])
        drive = self.work / "p1a2-diskfull.img"
        drive.write_bytes(fs_build(entries, data_slack=10, dir_slack=1))
        _, _, dones = self._shell_boot("diskfull", drive)
        self.assertEqual(dones, [0])
        fs = decode(drive.read_bytes())
        big = fs.entries["w/big"]
        self.assertEqual((big.count, big.length), (8, 4096))
        self.assertEqual(file_bytes(drive.read_bytes(), fs, "w/big"),
                         _fmt(0, 4096, 0xB1))


# P1-A2 sources the REQUIRED_FILES manifest predates (copied into the
# mutant tree alongside the manifest).
P1A2_EXTRA_FILES = ("user/proc-tests/sh_fput.c",
                    "user/proc-tests/p_fcwprobe.c",
                    "user/proc-tests/p_fcwfull.c", "user/lib/rt/rt_fs.h")


class P1A2MutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a2-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        # Pre-bind the toolchain to the real tree: build helpers
        # resolve runtimes from the program module's location, so the
        # first mutant compile must not bind it to a deleted tempdir
        # (mutated SOURCES still come from each copy explicitly).
        import sys as _sys
        if str(ROOT) not in _sys.path:
            _sys.path.insert(0, str(ROOT))
        from tools.rynorlang import program as _prog  # noqa: F401
        from tools.host import rnyx as _rnyx  # noqa: F401

    def _mutant_boot(self, name, edits, halt=0, kind="e2e"):
        """P1-A2 mutant run: copy the tree, apply source edits, build a
        shell image + drive, boot once. The working tree is never
        mutated (fixture deletion restores). kind selects the
        e2e/dirfull/diskfull drive+script. Returns (dones, drive)."""
        import shutil
        import tempfile
        from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
        from qemu import boot_image
        from sh_output import collect_done_statuses
        with tempfile.TemporaryDirectory(prefix="p1a2-mut-",
                                        dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES + P1A2_EXTRA_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            for source, old, new, count in edits:
                path = root / source
                contents = path.read_text(encoding="utf-8")
                self.assertEqual(contents.count(old), count, (name, source))
                path.write_text(contents.replace(old, new), encoding="utf-8")
            blobs = _compile_p1a2(root / "build" / "progs", root=root)
            dest = root / "build" / "img"
            build_image(root, dest, shell_boot=True,
                        shell_script="/test/session.sh")
            drive = root / "drive.img"
            if kind == "e2e":
                entries = (list(GOOD_ENTRIES) + [("/bin/sh", blobs["sh"]),
                           ("/bin/echo", blobs["echo"]),
                           ("/bin/cat", blobs["cat"]),
                           ("/bin/fput", blobs["fput"]),
                           ("/bin/fcwprobe", blobs["fcwprobe"]),
                           ("/test", None),
                           ("/test/session.sh", P1A2_SCRIPT.encode()),
                           ("/w", None)])
                drive.write_bytes(fs_build(entries, data_slack=128,
                                           dir_slack=4))
            elif kind == "dirfull":
                fixed = (list(GOOD_ENTRIES) + [("/bin/sh", blobs["sh"]),
                         ("/bin/fcfull", blobs["fcfull"]), ("/test", None),
                         ("/test/session.sh", b"fcfull dir\n"), ("/w", None),
                         ("/w/last", bytes([0xAB]) * 64)])
                nfixed = len(decode(fs_build(fixed)).entries)
                fills = [("/w/s%03d" % i, bytes([i & 0xFF]) * 64)
                         for i in range(500 - nfixed)]
                drive.write_bytes(fs_build(fixed + fills, data_slack=8,
                                           dir_slack=64 - (500 + 7) // 8))
            else:
                entries = (list(GOOD_ENTRIES) + [("/bin/sh", blobs["sh"]),
                           ("/bin/fcfull", blobs["fcfull"]), ("/test", None),
                           ("/test/session.sh", b"fcfull disk\n"),
                           ("/w", None)])
                drive.write_bytes(fs_build(entries, data_slack=10,
                                           dir_slack=1))
            logs = self.work / name
            out = boot_image(dest / "rynoros.img", logs, timeout=60,
                             extra_drives=((drive, False),), require_sh=True,
                             sh_done=("[SHD] halt code=%d\r\n" % halt).encode())
            return collect_done_statuses(out), drive.read_bytes()

    def test_mutant_redirect_consumer_swap_goes_red(self):
        """M1: the shell pipes echo into cat instead of fput: the
        consumer reads the missing target (exit 1) instead of the
        pipe, no file is ever created, and every cat fails."""
        dones, data = self._mutant_boot(
            "mut-consumer",
            [("user/shell/sh.c", '    synth.argv[0] = "fput";',
              '    synth.argv[0] = "cat";', 1)])
        self.assertEqual(dones, [1] * 11 + [0, 0, 0])
        fs = decode(data)
        for missing in ("proof.txt", "a", "b", "c", "d"):
            self.assertNotIn(missing, fs.entries)

    def test_mutant_fput_exists_swallow_goes_red(self):
        """M2: fput swallowing EXISTS overwrites the victim's head and
        reports success instead of the honest kernel reason."""
        dones, data = self._mutant_boot(
            "mut-exists",
            [("user/proc-tests/sh_fput.c",
              "                  0, 0, 0, 0);\n"
              "    if (rc != 0)\n"
              "        rt_exit((int)rc);",
              "                  0, 0, 0, 0);\n"
              "    if (rc != 0 && rc != 11)\n"
              "        rt_exit((int)rc);", 1)])
        self.assertEqual(dones, [0] * 14)
        fs = decode(data)
        self.assertEqual(file_bytes(data, fs, "proof.txt"),
                         b"seconds-persist-123")

    def test_mutant_reserved_lax_goes_red(self):
        """M3: the dispatcher accepting nonzero reserved words on
        fcreate lets the reserved-word probe row create instead of
        failing INVAL; the probe dies at 159 with /w/a empty."""
        dones, _ = self._mutant_boot(
            "mut-reserved", halt=1,
            edits=[("kernel/core/user.c",
              "            if (reason == SYS_FCREATE) {\n"
              "                if (f->rdx != 0 || f->rsi != 0 || "
              "f->rdi != 0 || f->rbp != 0)",
              "            if (reason == SYS_FCREATE) {\n"
              "                if (0)", 1)])
        self.assertEqual(dones, [0, 0, 11, 0, 0, 0, 0, 0, 0, 0, 0, 159, 0, 1])

    def test_mutant_short_count_goes_red(self):
        """M4: publishing a count on a failed fwrite (POSIX short-count
        shape) violates untouched-on-error; the first SENT-pinned
        error row dies at 166 with /w/rb never created."""
        dones, _ = self._mutant_boot(
            "mut-shortcount", halt=1,
            edits=[("kernel/core/load.c",
              "        if (rc != SYS_OK) {\n"
              "            /* Any error here "
              "(NOTFOUND/MALFORMED/BADARG/NOSPC/IOERR)",
              "        if (rc != SYS_OK) {\n"
              "            copy_to_user(c, nwritten_out, "
              "(const cpu_u8 *)&done,\n"
              "                         sizeof(done));\n"
              "            /* Any error here "
              "(NOTFOUND/MALFORMED/BADARG/NOSPC/IOERR)", 1)])
        self.assertEqual(dones, [0, 0, 11, 0, 0, 0, 0, 0, 0, 0, 0, 166, 0, 1])

    def test_mutant_wrapper_collapse_goes_red(self):
        """M5: the rt_fcreate wrapper reporting OK on any code hides
        the duplicate create the wrapper row pins at 192."""
        dones, _ = self._mutant_boot(
            "mut-collapse",
            [("user/lib/rt/rt_fs.h",
              "    rc = rt_gate6(RT_SYS_FCREATE, (unsigned long long)path, "
              "path_len,\n"
              "                  0, 0, 0, 0);\n"
              "    if (rc == 0)\n"
              "        return RT_OK;\n"
              "    return RT_INVAL;",
              "    rc = rt_gate6(RT_SYS_FCREATE, (unsigned long long)path, "
              "path_len,\n"
              "                  0, 0, 0, 0);\n"
              "    if (rc == 0)\n"
              "        return RT_OK;\n"
              "    return RT_OK;", 1)])
        self.assertEqual(dones, [0, 0, 11, 0, 0, 0, 0, 0, 0, 0, 0, 192, 0, 0])

    def test_mutant_create_nospc_collapse_goes_red(self):
        """M6a: the fcreate core reporting IOERR instead of NOSPC on a
        full directory: fcfull never sees 12 and dies at 210."""
        dones, _ = self._mutant_boot(
            "mut-nospc-create", halt=210, kind="dirfull",
            edits=[("kernel/core/load.c",
                    "    if (rc == FS_EXISTS) return SYS_EXISTS;\n"
                    "    if (rc == FS_NOSPC) return SYS_NOSPC;",
                    "    if (rc == FS_EXISTS) return SYS_EXISTS;\n"
                    "    if (rc == FS_NOSPC) return SYS_IOERR;", 1)])
        self.assertEqual(dones, [210])

    def test_mutant_write_nospc_collapse_goes_red(self):
        """M6b: the fwrite core reporting IOERR instead of NOSPC on a
        full disk: fcfull never sees 12 and dies at 223."""
        dones, _ = self._mutant_boot(
            "mut-nospc-write", halt=223, kind="diskfull",
            edits=[("kernel/core/load.c",
                    "        if (rc == FS_NOSPC) return SYS_NOSPC;",
                    "        if (rc == FS_NOSPC) return SYS_IOERR;", 1)])
        self.assertEqual(dones, [223])

    def test_mutant_staged_path_check_removed_goes_red(self):
        """M7: skipping staged-pathname validation in kern_fcreate lets
        the malformed-shape row reach fs_create, which answers IOERR
        instead of BADARG; the probe dies at 147."""
        dones, _ = self._mutant_boot(
            "mut-nopathcheck", halt=1,
            edits=[("kernel/core/load.c",
                    "    if (!fs_path_ok(kpath)) return SYS_BADARG;\n"
                    "    rc = fs_create(kpath);",
                    "    rc = fs_create(kpath);", 1)])
        self.assertEqual(dones, [0, 0, 11, 0, 0, 0, 0, 0, 0, 0, 0, 147, 0, 1])

    def test_mutant_exists_misclassified_goes_red(self):
        """M8: the fcreate core reporting OK instead of EXISTS on a
        collision: the redirect overwrites its victim like M2 and the
        probe's duplicate row dies at 142."""
        dones, data = self._mutant_boot(
            "mut-exists-ok", halt=1,
            edits=[("kernel/core/load.c",
                    "    if (rc == FS_EXISTS) return SYS_EXISTS;",
                    "    if (rc == FS_EXISTS) return SYS_OK;", 1)])
        self.assertEqual(dones, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 142, 0, 1])
        fs = decode(data)
        self.assertEqual(file_bytes(data, fs, "proof.txt"),
                         b"seconds-persist-123")


if __name__ == "__main__":
    unittest.main()
