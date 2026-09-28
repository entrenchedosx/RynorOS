"""P1-A3: CPL3 file discovery + lifecycle (syscalls 11/12/13) in QEMU.

Slice plan: the marker-gated kernel driver (fs-test.c p1a3_cases, runs
only on images carrying /p1a3-go) pins the kern_fstat/kern_readdir/
kern_unlink cores -- kernel-buffer shapes, unmounted mappings, and the
unlink fault-injection leg. Everything else -- the stat content
matrix, dense-ordinal enumeration, deletion lifecycles, slot/block
reuse, reboot persistence of deletion, exhaustion cycles, and the
ls/stat/rm shell surface -- runs through the real gate in CPL3 probe
and shell tests below (image bytes are free; the kernel loads high
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
from fs_output import decode, file_bytes  # noqa: E402
from fs_output import parse_serial  # noqa: E402
from test_filesystem import GOOD_ENTRIES  # noqa: E402

P1A3_ENTRIES = GOOD_ENTRIES + [("/p1a3-go", b"")]

# Kernel-battery rows: kern-only shapes (null kernel buffers,
# staged-path rule, unmounted mappings). Numeric sys_err codes.
P1A3_NEGS = {("stat", "nullout"): 2, ("stat", "nullpath"): 2,
             ("stat", "badpath"): 8, ("readdir", "nullout"): 2,
             ("unlink", "nullpath"): 2, ("unlink", "badpath"): 8,
             ("stat", "unmounted"): 10, ("readdir", "unmounted"): 10,
             ("unlink", "unmounted"): 10}
P1A3_FAULTS = [("f-unlink", 10)]


def _dir_slots(data):
    """Raw directory slots in order: [(name|None, ftype, first, count,
    length)] indexed by slot. decode() validates; this exposes slot
    positions for the reuse assertions (which entry sits where)."""
    import struct
    fs = decode(data)
    out = []
    for slot in range(fs.dir_blocks * 8):
        base = (fs.dir_start + slot // 8) * 512 + (slot % 8) * 64
        record = data[base:base + 64]
        if not any(record):
            out.append(None)
            continue
        nul = record.find(b"\x00")
        name = record[:nul].decode("ascii")
        first, count, length = struct.unpack("<QQQ", record[40:64])
        out.append((name, record[32], first, count, length))
    return fs, out


def _slot_of(slots, name):
    for index, record in enumerate(slots):
        if record is not None and record[0] == name:
            return index
    return None


def _lowest_free_block(pristine):
    """First free data block on a packed image: one past the highest
    live extent end (the allocator's answer for a 1-block file)."""
    fs = decode(pristine)
    end = fs.data_start
    for entry in fs.entries.values():
        if entry.ftype == 1 and entry.count:
            end = max(end, entry.first + entry.count)
    return end


class P1A3KernTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a3-tests"
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
        return fs_build(P1A3_ENTRIES, data_slack=48, dir_slack=1)

    def test_p1a3_kern_evidence(self):
        drive = self.work / "p1a3-kern.img"
        drive.write_bytes(self._main_image())
        evidence = self._boot("kern", drive)
        self.assertEqual(evidence.p1a3_negs, P1A3_NEGS)
        self.assertEqual(evidence.p1a3_faults, P1A3_FAULTS)

    def test_p1a3_marker_gate(self):
        drive = self.work / "p1a3-plain.img"
        drive.write_bytes(fs_build(GOOD_ENTRIES))
        evidence = self._boot("plain", drive)
        self.assertEqual(evidence.p1a3_negs, {})
        self.assertEqual(evidence.p1a3_faults, [])

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
                    # Same fault-leftover discipline as the P1-A2
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
        # The fault leg's transient is always gone: fresh boots delete
        # it via the retry, reboot boots redo the identical cycle.
        self.assertNotIn("p1a3-f", fs.entries)
        self.assertEqual(set(fs.entries), set(before.entries))

    def test_p1a3_kern_persist_to_disk(self):
        drive = self.work / "p1a3-kern-persist.img"
        original = self._main_image()
        drive.write_bytes(original)
        evidence = self._boot("persist1", (drive, False))
        self.assertEqual(evidence.p1a3_negs, P1A3_NEGS)
        self.assertEqual(evidence.p1a3_faults, P1A3_FAULTS)
        self._check_kern_drive(drive.read_bytes(), original, evidence)

    def test_p1a3_kern_reboot_readback(self):
        import hashlib
        drive = self.work / "p1a3-kern-reboot.img"
        drive.write_bytes(self._main_image())
        first = self._boot("reboot1", (drive, False))
        after1 = drive.read_bytes()
        self.assertEqual(first.p1a3_negs, P1A3_NEGS)
        self.assertEqual(first.p1a3_faults, P1A3_FAULTS)
        second = self._boot("reboot2", (drive, False))
        # Idempotent battery: the reboot redo prints the identical
        # rows (create/write/fault/unlink cycle over the absent
        # transient) and the drive keeps the identical shape.
        self.assertEqual(second.p1a3_negs, P1A3_NEGS)
        self.assertEqual(second.p1a3_faults, P1A3_FAULTS)
        after2 = drive.read_bytes()
        self._check_kern_drive(after2, self._main_image(), second)
        fs1 = decode(after1)
        first_block = fs1.entries["b1500"].first
        mask1 = bytearray(after1)
        mask2 = bytearray(after2)
        mask1[first_block * 512:first_block * 512 + 512] = b"\0" * 512
        mask2[first_block * 512:first_block * 512 + 512] = b"\0" * 512
        self.assertEqual(hashlib.sha256(mask1).digest(),
                         hashlib.sha256(mask2).digest())


P1A3_SCRIPT = """echo P1A3 > /banner
cat /banner
stat /banner
stat /one
stat /docs
stat /missing
ls
echo Z > /gas
cat /gas
rm /gas
stat /gas
cat /gas
echo small > /sb
stat /sb
cat /sb
statprobe
ls
"""
# Boot 1 (fresh): everything lands; missing stats fail 3, the cat of
# the deleted file fails 1. Boot 2 (reboot): the banner/sb redirects
# collide (11, no truncate), the deleted file recreates then deletes
# again, the probe takes its verify-only path.
P1A3_DONES_BOOT1 = [0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 3, 1, 0, 0, 0, 0, 0]
P1A3_DONES_BOOT2 = [11, 0, 0, 0, 0, 3, 0, 0, 0, 0, 3, 1, 11, 0, 0, 0, 0]

# Shell-created files (small texts through echo redirect). The probe
# owns the multi-block /w/u3 lifecycle; sizes here stay 1 block.
_SHELL_FILES = {"/banner": b"P1A3", "/gas": b"Z", "/sb": b"small"}


def _shell_entries(blobs):
    return (list(GOOD_ENTRIES) + [("/bin/sh", blobs["sh"]),
                ("/bin/echo", blobs["echo"]),
                ("/bin/cat", blobs["cat"]),
                ("/bin/fput", blobs["fput"]),
                ("/bin/ls", blobs["ls"]),
                ("/bin/stat", blobs["stat"]),
                ("/bin/rm", blobs["rm"]),
                ("/bin/statprobe", blobs["statprobe"]),
                ("/test", None),
                ("/test/session.sh", P1A3_SCRIPT.encode()),
                ("/w", None)])


def _ls_want(names):
    return b"".join(b"/" + name.encode() + b"\n" for name in names)


def _stat_want(path, ftype, size):
    return ("path: %s\ntype: %s\nsize: %d\n"
            % (path, ftype, size)).encode()


def _slot1_want(pristine_names, boot):
    """Exact serial slot-1 bytes: every stat/cat/ls child in script
    order (redirected echos are pipe-captured; rm/statprobe are
    silent). Boot 2 replays the cats/stats and both listings show the
    post-boot-1 shape (banner, sb, w/u3; gas deleted again)."""
    listing_a = _ls_want(pristine_names + ["banner"])
    listing_b = _ls_want(pristine_names + ["banner", "sb", "w/u3"])
    first_ls = listing_a if boot == 1 else listing_b
    return (b"P1A3"
            + _stat_want("/banner", "file", 4)
            + _stat_want("/one", "file", 2)
            + _stat_want("/docs", "dir", 0)
            + first_ls
            + b"Z"
            + _stat_want("/sb", "file", 5)
            + b"small"
            + listing_b)


def _compile_p1a3(work, root=ROOT):
    """Compile the P1-A3 shell tree: sh (v2) + echo/cat/fput/ls/stat/
    rm (v1) + the statprobe/dircycle/diskcycle probes (v2, image
    bytes are free)."""
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
               ("sh_fput", "fput", 1, True), ("sh_ls", "ls", 1, True),
               ("sh_stat", "stat", 1, True), ("sh_rm", "rm", 1, True),
               ("p_statprobe", "statprobe", 2, True),
               ("p_dircycle", "dircycle", 2, True),
               ("p_diskcycle", "diskcycle", 2, True))
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


class P1A3ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a3-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.blobs = _compile_p1a3(cls.work / "progs")
        cls.dest = cls.work / "sh-image"
        build_image(ROOT, cls.dest, shell_boot=True,
                    shell_script="/test/session.sh")
        cls.rynoros = cls.dest / "rynoros.img"
        entries = _shell_entries(cls.blobs)
        cls.drive = cls.work / "p1a3-shell.img"
        cls.drive.write_bytes(fs_build(entries, data_slack=16, dir_slack=1))
        cls.original = cls.drive.read_bytes()
        before = decode(cls.original)
        cls.pristine_names = sorted(before.entries)
        cls.n0 = len(before.entries)
        cls.f0 = _lowest_free_block(cls.original)

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
        # No marker on this drive: the kern battery stays off.
        self.assertEqual(parse_serial(out).p1a3_negs, {})
        self.assertEqual(parse_serial(out).p1a3_faults, [])
        return out, rows, collect_done_statuses(out), collect_load_writes(out)

    def _check_shell_drive(self, data, original, writes):
        fs = decode(data)
        before = decode(original)
        # The 17c battery overwrites fixed ranges on every boot; patch
        # those into the baseline from the printed write rows (same
        # discipline as test_p1a._check_persisted_drive). The
        # p1a/p1a2/p1a3 batteries are marker-gated off this drive, so
        # /b511 stays pristine here (no P1-A growth).
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
                else:
                    self.assertEqual((now.first, now.count, now.length),
                                     (entry.first, entry.count, entry.length))
                    self.assertEqual(file_bytes(data, fs, key),
                                     bytes(patched[key]))
        return fs

    def _check_reuse_shape(self, data):
        """Slot + first-fit block reuse from the raw directory: the
        script's create/delete order pins exact slots and extents
        (banner first, gas deleted, sb retaking gas's slot+block, the
        probe's u3 retaking sa's slot+blocks)."""
        fs, slots = _dir_slots(data)
        n0 = self.n0
        f0 = self.f0
        banner = _slot_of(slots, "banner")
        sb = _slot_of(slots, "sb")
        u3 = _slot_of(slots, "w/u3")
        self.assertEqual(banner, n0)
        self.assertEqual(sb, n0 + 1)
        self.assertEqual(u3, n0 + 2)
        self.assertEqual(slots[banner][2:], (f0, 1, 4))
        self.assertEqual(slots[sb][2:], (f0 + 1, 1, 5))
        self.assertEqual(slots[u3][2:], (f0 + 2, 3, 1500))
        self.assertEqual(file_bytes(data, fs, "banner"), b"P1A3")
        self.assertEqual(file_bytes(data, fs, "sb"), b"small")
        expect_u3 = bytes(((i * 13 + 0x63) & 0xFF) for i in range(1500))
        self.assertEqual(file_bytes(data, fs, "w/u3"), expect_u3)
        # Every transient is gone: the shell's gas plus the probe's
        # sa/sb/u1 (deleted mid-run, never resurrected).
        for gone in ("gas", "w/sa", "w/sb", "w/u1"):
            self.assertNotIn(gone, fs.entries)
            self.assertIsNone(_slot_of(slots, gone))

    def test_shell_lifecycle_reuse_e2e(self):
        from test_cplshell import _slot_blob
        from sh_output import collect_load_writes
        self.drive.write_bytes(self.original)
        out1, rows1, dones1, _ = self._shell_boot("shell1", self.drive)
        self.assertEqual(dones1, P1A3_DONES_BOOT1)
        blob1 = _slot_blob(collect_load_writes(out1), 1)
        self.assertEqual(bytes.fromhex(blob1),
                         _slot1_want(self.pristine_names, 1))
        after1 = self.drive.read_bytes()
        self._check_shell_drive(after1, self.original,
                                parse_serial(out1).writes)
        self._check_reuse_shape(after1)
        out2, rows2, dones2, _ = self._shell_boot("shell2", self.drive)
        self.assertEqual(dones2, P1A3_DONES_BOOT2)
        blob2 = _slot_blob(collect_load_writes(out2), 1)
        self.assertEqual(bytes.fromhex(blob2),
                         _slot1_want(self.pristine_names, 2))
        after2 = self.drive.read_bytes()
        self._check_shell_drive(after2, self.original,
                                parse_serial(out2).writes)
        self._check_reuse_shape(after2)
        # Boot 2 wrote nothing durable: redirects collided, gas
        # recreated then deleted again, the probe verified only. Two
        # free-space leftovers legitimately differ and are masked:
        # b1500[0,512) (fault-injection staging, like P1-A2) and the
        # transient block both boots' deleted files shared. Boot 1's
        # probe wrote 100 seed-0x62 bytes there (via /w/u1); boot 2's
        # recreated /gas overwrote byte 0 with b"Z" before its own
        # deletion. The pinned bytes below prove exactly that.
        import hashlib
        fs1 = decode(after1)
        first = fs1.entries["b1500"].first
        transient = _lowest_free_block(after1)
        expect_u1 = bytes(((i * 13 + 0x62) & 0xFF) for i in range(100))
        self.assertEqual(after1[transient * 512:transient * 512 + 100],
                         expect_u1)
        self.assertEqual(after2[transient * 512:transient * 512 + 100],
                         b"\x5A" + b"\x00" * 99)
        mask1 = bytearray(after1)
        mask2 = bytearray(after2)
        mask1[first * 512:first * 512 + 512] = b"\0" * 512
        mask2[first * 512:first * 512 + 512] = b"\0" * 512
        mask1[transient * 512:transient * 512 + 512] = b"\0" * 512
        mask2[transient * 512:transient * 512 + 512] = b"\0" * 512
        self.assertEqual(hashlib.sha256(mask1).digest(),
                         hashlib.sha256(mask2).digest())


class P1A3CycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a3-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        if not hasattr(P1A3ShellTests, "blobs"):
            P1A3ShellTests.setUpClass()
        cls.blobs = P1A3ShellTests.blobs
        cls.rynoros = P1A3ShellTests.rynoros

    def _cycle_entries(self, probe):
        return (list(GOOD_ENTRIES) + [("/bin/sh", self.blobs["sh"]),
                ("/bin/" + probe, self.blobs[probe]), ("/test", None),
                ("/test/session.sh", (probe + "\n").encode()),
                ("/w", None)])

    def _shell_boot(self, name, drive):
        from qemu import boot_image
        from sh_output import (validate_sh_section, collect_sh_rows,
                               collect_done_statuses)
        logs = self.work / name
        out = boot_image(self.rynoros, logs, timeout=60,
                         extra_drives=((drive, False),), require_sh=True,
                         sh_done=b"[SHD] halt code=0\r\n")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        self.assertIn("[SH] script /test/session.sh", rows)
        self.assertIn("[SHD] halt code=0", rows)
        return collect_done_statuses(out)

    def test_dir_cycle_through_cpl3(self):
        drive = self.work / "p1a3-dircycle.img"
        original = fs_build(self._cycle_entries("dircycle"), data_slack=2,
                            dir_slack=1)
        drive.write_bytes(original)
        dones = self._shell_boot("dircycle", drive)
        self.assertEqual(dones, [0])
        fs, slots = _dir_slots(drive.read_bytes())
        # Exactly full: every slot live, the last create refused.
        self.assertTrue(all(record is not None for record in slots))
        self.assertEqual(len(fs.entries), len(slots))
        self.assertNotIn("zy", fs.entries)
        self.assertNotIn("z0", fs.entries)
        # /zx occupies /z0's old slot: the first free slot of the
        # pristine image (entries pack slots 0..N0-1).
        n0 = len(decode(original).entries)
        self.assertEqual(_slot_of(slots, "zx"), n0)
        self.assertEqual(slots[n0][2:], (0, 0, 0))

    def test_disk_cycle_through_cpl3(self):
        drive = self.work / "p1a3-diskcycle.img"
        original = fs_build(self._cycle_entries("diskcycle"), data_slack=0,
                            dir_slack=1)
        drive.write_bytes(original)
        f_one = decode(original).entries["one"].first
        dones = self._shell_boot("diskcycle", drive)
        self.assertEqual(dones, [0])
        data = drive.read_bytes()
        fs = decode(data)
        # /q lands exactly where /one was: first-fit reuse of the
        # freed block, then the disk is full again (/q2 empty).
        self.assertNotIn("one", fs.entries)
        self.assertEqual((fs.entries["q"].first, fs.entries["q"].count,
                          fs.entries["q"].length), (f_one, 1, 1))
        self.assertEqual(file_bytes(data, fs, "q"), b"\x51")
        self.assertEqual((fs.entries["q2"].first, fs.entries["q2"].count,
                          fs.entries["q2"].length), (0, 0, 0))


# P1-A3 sources the REQUIRED_FILES manifest predates (copied into the
# mutant tree alongside the manifest).
P1A3_EXTRA_FILES = ("user/proc-tests/sh_fput.c",
                    "user/proc-tests/sh_ls.c", "user/proc-tests/sh_stat.c",
                    "user/proc-tests/sh_rm.c",
                    "user/proc-tests/p_statprobe.c",
                    "user/proc-tests/p_dircycle.c",
                    "user/proc-tests/p_diskcycle.c")


class P1A3MutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a3-tests"
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
        """Build a mutant tree, boot it against the e2e/dircycle/
        diskcycle drive+script. Returns (dones, drive)."""
        import shutil
        import tempfile
        from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
        from qemu import boot_image
        from sh_output import collect_done_statuses
        with tempfile.TemporaryDirectory(prefix="p1a3-mut-",
                                        dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES + P1A3_EXTRA_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            for source, old, new, count in edits:
                path = root / source
                contents = path.read_text(encoding="utf-8")
                self.assertEqual(contents.count(old), count, (name, source))
                path.write_text(contents.replace(old, new), encoding="utf-8")
            blobs = _compile_p1a3(root / "build" / "progs", root=root)
            dest = root / "build" / "img"
            build_image(root, dest, shell_boot=True,
                        shell_script="/test/session.sh")
            drive = root / "drive.img"
            if kind == "e2e":
                entries = _shell_entries(blobs)
                drive.write_bytes(fs_build(entries, data_slack=16,
                                           dir_slack=1))
            elif kind == "dircycle":
                entries = (list(GOOD_ENTRIES)
                           + [("/bin/sh", blobs["sh"]),
                              ("/bin/dircycle", blobs["dircycle"]),
                              ("/test", None),
                              ("/test/session.sh", b"dircycle\n"),
                              ("/w", None)])
                drive.write_bytes(fs_build(entries, data_slack=2,
                                           dir_slack=1))
            else:
                entries = (list(GOOD_ENTRIES)
                           + [("/bin/sh", blobs["sh"]),
                              ("/bin/diskcycle", blobs["diskcycle"]),
                              ("/test", None),
                              ("/test/session.sh", b"diskcycle\n"),
                              ("/w", None)])
                drive.write_bytes(fs_build(entries, data_slack=0,
                                           dir_slack=1))
            logs = self.work / name
            out = boot_image(dest / "rynoros.img", logs, timeout=60,
                             extra_drives=((drive, False),), require_sh=True,
                             sh_done=("[SHD] halt code=%d\r\n" % halt).encode())
            return collect_done_statuses(out), drive.read_bytes()

    def test_mutant_readdir_end_as_ok_goes_red(self):
        """M1: the readdir core reporting OK instead of END past the
        last entry: ls walks into unfilled stack garbage past any
        bound and dies at 65; the probe's first walk dies at 220."""
        dones, _ = self._mutant_boot(
            "mut-end-ok", halt=65,
            edits=[("kernel/core/load.c",
                    "    if (rc == FS_END) return SYS_END;",
                    "    if (rc == FS_END) return SYS_OK;", 1)])
        want = list(P1A3_DONES_BOOT1)
        want[6] = 65
        want[15] = 220
        want[16] = 65
        self.assertEqual(dones, want)

    def test_mutant_unlink_dir_allowed_goes_red(self):
        """M2: the unlink core reporting OK instead of MALFORMED for
        directories: the probe's dir-unlink row dies at 231. The lie
        is the bug (fs_unlink itself refuses before zeroing anything),
        so the drive keeps /docs and its children intact."""
        dones, data = self._mutant_boot(
            "mut-unlink-dir",
            [("kernel/core/load.c",
              "    if (rc == FS_NOTDIR) return SYS_MALFORMED;\n"
              "    if (rc == FS_NOTFILE) return SYS_MALFORMED;",
              "    if (rc == FS_NOTDIR) return SYS_MALFORMED;\n"
              "    if (rc == FS_NOTFILE) return SYS_OK;", 1)])
        want = list(P1A3_DONES_BOOT1)
        want[15] = 231
        self.assertEqual(dones, want)
        fs = decode(data)
        self.assertEqual(fs.entries["docs"].ftype, 2)
        self.assertIn("docs/a.txt", fs.entries)

    def test_mutant_fstat_size_zero_goes_red(self):
        """M3: the stat core publishing size 0 for every file: stat
        rows lie and the probe's first content row dies at 201."""
        dones, _ = self._mutant_boot(
            "mut-size-zero",
            [("kernel/core/load.c",
              "        kstat->size = st.size;",
              "        kstat->size = 0;", 1)])
        want = list(P1A3_DONES_BOOT1)
        want[15] = 201
        self.assertEqual(dones, want)

    def test_mutant_readdir_free_slots_goes_red(self):
        """M4: readdir no longer skipping free slots: the first free
        slot reads as a corrupt empty name, ls dies at 10, and the
        probe's first walk dies at 220."""
        dones, _ = self._mutant_boot(
            "mut-free-nos", halt=10,
            edits=[("kernel/storage/fs.c",
                    "        if (entry_free(e)) continue;\n"
                    "        if (seen != ordinal) { ++seen; continue; }",
                    "        if (seen != ordinal) { ++seen; continue; }",
                    1)])
        want = list(P1A3_DONES_BOOT1)
        want[6] = 10
        want[15] = 220
        want[16] = 10
        self.assertEqual(dones, want)

    def test_mutant_readdir_end_as_notfound_goes_red(self):
        """M5: the readdir core reporting NOTFOUND instead of END:
        ls mistakes end-of-directory for a real error (exit 3) and
        the probe's first walk dies at 220."""
        dones, _ = self._mutant_boot(
            "mut-end-nf", halt=3,
            edits=[("kernel/core/load.c",
                    "    if (rc == FS_END) return SYS_END;",
                    "    if (rc == FS_END) return SYS_NOTFOUND;", 1)])
        want = list(P1A3_DONES_BOOT1)
        want[6] = 3
        want[15] = 220
        want[16] = 3
        self.assertEqual(dones, want)

    def test_mutant_create_nospc_collapse_goes_red(self):
        """M6: the fcreate core reporting IOERR instead of NOSPC on a
        full directory: dircycle never sees 12 and dies at 201."""
        dones, _ = self._mutant_boot(
            "mut-nospc-create", halt=201, kind="dircycle",
            edits=[("kernel/core/load.c",
                    "    if (rc == FS_EXISTS) return SYS_EXISTS;\n"
                    "    if (rc == FS_NOSPC) return SYS_NOSPC;",
                    "    if (rc == FS_EXISTS) return SYS_EXISTS;\n"
                    "    if (rc == FS_NOSPC) return SYS_IOERR;", 1)])
        self.assertEqual(dones, [201])

    def test_mutant_write_nospc_collapse_goes_red(self):
        """M7: the fwrite core reporting IOERR instead of NOSPC on a
        full disk: diskcycle never sees 12 and dies at 210."""
        dones, _ = self._mutant_boot(
            "mut-nospc-write", halt=210, kind="diskcycle",
            edits=[("kernel/core/load.c",
                    "        if (rc == FS_NOSPC) return SYS_NOSPC;",
                    "        if (rc == FS_NOSPC) return SYS_IOERR;", 1)])
        self.assertEqual(dones, [210])
