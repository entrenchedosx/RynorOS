"""P1-A: filesystem mutation (create/write/extend/relocate) in QEMU.

The marker-gated kernel driver (fs-test.c p1a_cases, runs only on
images carrying /p1a-go) creates files, grows them across block
boundaries, remounts, and reads everything back; every number below
is recomputed on the host from the payload formula
(byte[p] == (p * 13 + seed) & 0xff, seeds per file) and from an
independent first-fit simulation over the decoded image. Guest writes
reach real host bytes only on private (path, False) drive copies, so
host corroboration reads the mutated image, never test-harness state.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT / "tests/integration"))
from image import build_image  # noqa: E402
from qemu import boot_image  # noqa: E402
from fs_image import build as fs_build  # noqa: E402
from fs_image import corrupt_entry  # noqa: E402
from fs_output import decode, file_bytes, block_sum, block_wsum  # noqa: E402
from fs_output import parse_serial  # noqa: E402
from test_filesystem import GOOD_ENTRIES  # noqa: E402

P1A_ENTRIES = GOOD_ENTRIES + [("/p1a-go", b"")]
P1A_GAPS = {"b512": 1, "bin/test": 2, "hello": 4}
P1A_SLACK = 48

# path -> (seed, size, blocks). Blocks are asserted, not derived: they
# pin the exact placement the first-fit simulation predicts below.
P1A_FILES = {
    "/p1a-new": (0x41, 1500, 3),
    "/docs/p1a-nested": (0x42, 511, 1),
    "/p1a-big": (0x43, 3500, 7),
    "/p1a-exact": (0x44, 1024, 2),
    "/p1a-1byte": (0x45, 1, 1),
}
P1A_NEGS = {
    "empty": "invalid", "root": "exists", "dslash": "invalid",
    "toolong": "invalid", "dot": "invalid", "dotdot": "invalid",
    "noparent": "notfound", "parentfile": "notdir",
    "dupfile": "exists", "dupdir": "exists", "dupnew": "exists",
}
P1A31 = "/p1a-123456789012345678901234567"


def pattern(seed, size):
    return bytes(((i * 13 + seed) & 0xFF) for i in range(size))


def first_fit(live, data_start, data_end, nblocks):
    """Independent placement model: lowest gap start fitting nblocks,
    or None. live = [(first, count)] nonzero file extents."""
    cur = data_start
    while True:
        nxt = data_end
        for first, count in live:
            if count and first >= cur and first < nxt:
                nxt = first
        if nblocks <= nxt - cur:
            return cur
        if nxt >= data_end:
            return None
        adv = [count for first, count in live if first == nxt]
        assert len(adv) == 1 and adv[0] > 0
        cur = nxt + adv[0]


def simulate_layout(image):
    """Replay the driver's exact op order through first_fit, starting
    from the decoded image. Returns {key: (first, count)} for P1 files
    plus the freed old extent of /p1a-big (for gap accounting)."""
    fs = decode(image)
    live = [(e.first, e.count) for e in fs.entries.values()
            if e.ftype == 1 and e.count]
    data_end = fs.data_start + fs.data_blocks
    placed = {}

    def place(key, nblocks):
        spot = first_fit(live, fs.data_start, data_end, nblocks)
        assert spot is not None, key
        live.append((spot, nblocks))
        placed[key] = (spot, nblocks)
        return spot

    place("p1a-new", 3)
    place("docs/p1a-nested", 1)
    old_big = place("p1a-big", 3)
    new_big = place("p1a-big-new", 7)
    live.remove((old_big, 3))
    placed["p1a-big"] = (new_big, 7)
    del placed["p1a-big-new"]
    place("p1a-exact", 2)
    place("p1a-1byte", 1)
    return placed


class P1AMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/p1a-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination)
        cls.rynoros = cls.destination / "rynoros.img"

    def _boot(self, name, *drives, timeout=60):
        logs = self.work / name
        out = boot_image(self.rynoros, logs, timeout=timeout,
                         extra_drives=tuple(drives))
        evidence = parse_serial(out)
        self.assertEqual(evidence.failures, [])
        self.assertTrue(evidence.verified)
        return evidence

    def _main_image(self):
        return fs_build(P1A_ENTRIES, data_slack=P1A_SLACK, gaps=P1A_GAPS,
                        dir_slack=1)

    def _expect_writes(self):
        want = {}
        for path, (seed, size, _blocks) in P1A_FILES.items():
            blob = pattern(seed, size)
            want[path] = (size, (size + 511) // 512,
                          block_sum(blob), block_wsum(blob))
        return want

    def test_p1a_main_evidence(self):
        drive = self.work / "p1a-main.img"
        drive.write_bytes(self._main_image())
        evidence = self._boot("main", drive)
        self.assertEqual(evidence.p1a_negs, P1A_NEGS)
        self.assertEqual(evidence.p1a_creates, [("/p1a-new", 0, 0)])
        self.assertEqual(evidence.p1a_writes, self._expect_writes_with_extra())
        self.assertEqual(evidence.p1a_remounts, self._expect_remounts())
        self.assertEqual(evidence.p1a_reboots, {})
        self.assertEqual(evidence.p1a_degraded, [])

    def _expect_writes_with_extra(self, prefix511=b"a" * 511):
        want = self._expect_writes()
        blob511 = prefix511 + bytes((((511 * 13 + 0x46) & 0xFF),))
        want["/b511"] = (512, 1, block_sum(blob511), block_wsum(blob511))
        want[P1A31] = (0, 0, 0, 0)
        return want

    def _expect_remounts(self, prefix511=b"a" * 511):
        want = self._expect_writes_with_extra(prefix511)
        want["/one"] = (2, 1, block_sum(b"OK"), block_wsum(b"OK"))
        return want

    def _prefix511_of(self, image):
        return file_bytes(image, decode(image), "b511")[:511]

    def _expect_negs10(self):
        return {k: v for k, v in P1A_NEGS.items() if k != "dupnew"}

    def test_p1a_tight_disk_full(self):
        drive = self.work / "p1a-tight.img"
        drive.write_bytes(fs_build(P1A_ENTRIES))
        evidence = self._boot("tight", drive)
        self.assertEqual(evidence.p1a_negs, self._expect_negs10())
        self.assertEqual(evidence.p1a_creates, [("/p1a-new", 0, 0)])
        self.assertEqual(evidence.p1a_writes, {})
        self.assertEqual(evidence.p1a_remounts, {})
        self.assertEqual(evidence.p1a_degraded,
                         [("tight", 1, 0, 2)])

    def test_p1a_dir_full(self):
        entries = list(P1A_ENTRIES)
        need = 512 - len(entries)
        for i in range(need):
            entries.append((f"/z{i:03d}", b""))
        self.assertEqual(len(entries), 512)
        drive = self.work / "p1a-dirfull.img"
        drive.write_bytes(fs_build(entries))
        evidence = self._boot("dirfull", drive)
        self.assertEqual(evidence.p1a_negs, self._expect_negs10())
        self.assertEqual(evidence.p1a_degraded,
                         [("dirfull", 0, 0, 2)])
        self.assertEqual(evidence.p1a_creates, [])
        self.assertEqual(evidence.p1a_writes, {})

    def test_p1a_marker_gate(self):
        drive = self.work / "fs-plain.img"
        drive.write_bytes(fs_build(GOOD_ENTRIES))
        evidence = self._boot("plain", drive)
        self.assertEqual(evidence.p1a_creates, [])
        self.assertEqual(evidence.p1a_writes, {})
        self.assertEqual(evidence.p1a_remounts, {})
        self.assertEqual(evidence.p1a_reboots, {})
        self.assertEqual(evidence.p1a_negs, {})
        self.assertEqual(evidence.p1a_degraded, [])

    def _check_persisted_drive(self, data, original, evidence):
        fs = decode(data)  # raises on any overlap/range/format violation
        before = decode(original)
        # The 17c battery overwrites fixed ranges first; patch those into
        # the baseline from the printed write rows (same discipline as
        # the 17b phased validator).
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
                    # Entry untouched by P1-A; the 17c patch lands
                    # verbatim. Bytes [0,512) are fault-injection
                    # leftovers (f-partial writes the then-current
                    # staging buffer, f-after patches tiny[] whose
                    # tail is earlier tiny reuse across hdl/acct
                    # reads); 17c pins only counts/codes there, and
                    # the determinism test pins the full bytes
                    # boot-vs-boot. Assert everything else exactly.
                    self.assertEqual((now.first, now.count, now.length),
                                     (entry.first, entry.count, entry.length))
                    self.assertEqual(file_bytes(data, fs, key)[512:],
                                     bytes(patched[key][512:]))
                    continue
                if key == "b511":
                    self.assertEqual((now.count, now.length), (1, 512))
                    expect = (patched[key][:511] +
                              bytes((((511 * 13 + 0x46) & 0xFF),)))
                    self.assertEqual(file_bytes(data, fs, key), expect)
                elif key == "one":
                    self.assertEqual((now.count, now.length), (1, 2))
                    expect = bytearray(patched[key])
                    expect[0:2] = b"OK"
                    self.assertEqual(file_bytes(data, fs, key), bytes(expect))
                else:
                    self.assertEqual((now.first, now.count, now.length),
                                     (entry.first, entry.count, entry.length))
                    self.assertEqual(file_bytes(data, fs, key),
                                     bytes(patched[key]))
        for path, (seed, size, _blocks) in P1A_FILES.items():
            key = path[1:]
            with self.subTest(entry=key):
                self.assertEqual(file_bytes(data, fs, key), pattern(seed, size))
        self.assertEqual(fs.entries[P1A31[1:]].length, 0)
        self.assertEqual((fs.entries[P1A31[1:]].first, fs.entries[P1A31[1:]].count),
                         (0, 0))
        # Exact first-fit placement per the independent simulation.
        for key, (first, count) in simulate_layout(original).items():
            with self.subTest(layout=key):
                entry = fs.entries[key]
                self.assertEqual((entry.first, entry.count), (first, count))

    def test_p1a_persist_to_disk(self):
        drive = self.work / "p1a-persist.img"
        original = self._main_image()
        drive.write_bytes(original)
        evidence = self._boot("persist1", (drive, False))
        self.assertEqual(evidence.p1a_writes, self._expect_writes_with_extra())
        self._check_persisted_drive(drive.read_bytes(), original, evidence)

    def test_p1a_reboot_readback(self):
        drive = self.work / "p1a-reboot.img"
        drive.write_bytes(self._main_image())
        first = self._boot("reboot1", (drive, False))
        self.assertEqual(first.p1a_writes, self._expect_writes_with_extra())
        second = self._boot("reboot2", (drive, False))
        # Verify-only: reboot rows present, no new writes or creates.
        self.assertEqual(second.p1a_negs, self._expect_negs10())
        self.assertEqual(second.p1a_writes, {})
        self.assertEqual(second.p1a_creates, [])
        self.assertEqual(second.p1a_reboots, self._expect_remounts())
        self.assertEqual(second.p1a_remounts, {})
        self.assertEqual(second.p1a_degraded, [])
        # Same on-disk contract after the second boot (byte-identity is
        # NOT expected: fault-injection leftovers legitimately differ
        # once the image carries first-boot 17c overwrites).
        self._check_persisted_drive(drive.read_bytes(), self._main_image(),
                                    second)

    def test_p1a_deterministic_layout(self):
        first = self.work / "p1a-det-a.img"
        second = self.work / "p1a-det-b.img"
        original = self._main_image()
        first.write_bytes(original)
        second.write_bytes(original)
        self._boot("det-a", (first, False))
        self._boot("det-b", (second, False))
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_p1a_shuffled_dir_order(self):
        base = self._main_image()
        fs = decode(base)
        order = list(fs.entries)
        ones = [i for i, n in enumerate(order)
                if fs.entries[n].ftype == 1 and fs.entries[n].count == 1]
        first_a = fs.entries[order[ones[0]]].first
        first_b = fs.entries[order[ones[1]]].first
        swapped = corrupt_entry(base, ones[0], "first", first_b)
        swapped = corrupt_entry(swapped, ones[1], "first", first_a)
        decode(swapped)  # still mount-valid: disjoint, in range
        drive = self.work / "p1a-shuffled.img"
        drive.write_bytes(swapped)
        evidence = self._boot("shuffled", (drive, False))
        prefix = self._prefix511_of(swapped)
        self.assertEqual(evidence.p1a_writes, self._expect_writes_with_extra(prefix))
        self.assertEqual(evidence.p1a_remounts, self._expect_remounts(prefix))
        self._check_persisted_drive(drive.read_bytes(), swapped, evidence)


if __name__ == "__main__":
    unittest.main()
