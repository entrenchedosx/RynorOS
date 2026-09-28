"""DMA-A1: contiguous DMA buffers + bus-address ownership in QEMU.

The gated kernel self-test (RYNOR_DMA_TEST images) proves the
synthetic first-fit scan (17 fixtures) and the live dma_alloc/free
API; this suite pins the live evidence exactly (deterministic
first-fit PFNs, bump-allocated VAs, reuse identities), replays an
independent Python scan model over every synthetic row, and runs 14
check-removal mutants.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image  # noqa: E402
from dma_output import (parse_dma_section, validate_dma_section,  # noqa: E402
                        model_scan)

VA_BASE = 0xFFFFC08000000000

# (index, n, align, limit, b0, p0, b1, p1, bits, found, start).
# Not-found rows print the 0xdead sentinel (scan left it untouched).
SCASES = [
    (0, 1, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x0, 1, 0x0),
    (1, 1, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0xFF, 0, 0xDEAD),
    (2, 2, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x7, 1, 0x3),
    (3, 4, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x8, 1, 0x4),
    (4, 2, 0x2000, 0x0, 0x101000, 8, 0x0, 0, 0x0, 1, 0x1),
    (5, 1, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x7F, 1, 0x7),
    (6, 3, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x24, 0, 0xDEAD),
    (7, 3, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x20, 1, 0x0),
    (8, 2, 0x1000, 0x0, 0x100000, 4, 0x200000, 4, 0xF, 1, 0x4),
    (9, 3, 0x1000, 0x0, 0x100000, 2, 0x200000, 2, 0x0, 0, 0xDEAD),
    (10, 1, 0x1000, 0x100000000, 0x100000000, 4, 0x0, 0, 0x0, 0, 0xDEAD),
    (11, 2, 0x1000, 0x103000, 0x100000, 8, 0x0, 0, 0x3, 0, 0xDEAD),
    (12, 2, 0x1000, 0x104000, 0x100000, 8, 0x0, 0, 0x3, 1, 0x2),
    (13, 3, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x1F, 1, 0x5),
    (14, 3, 0x1000, 0x0, 0x100000, 8, 0x0, 0, 0x3F, 0, 0xDEAD),
    (15, 1, 0x2000, 0x0, 0x101000, 8, 0x0, 0, 0x2, 1, 0x3),
    (16, 5, 0x1000, 0x0, 0x100000, 4, 0x0, 0, 0x0, 0, 0xDEAD),
]

# (id, virt_offset, phys, size, alloc, align); bus == phys always.
# BOOT-A1 moved the first-fit base one page up (0x11f000 -> 0x120000):
# the high kernel image costs one more page-table page below the DMA
# pool. Ids 9-11 keep their frames (alignment-driven holes).
# INT-A1: the DMA phase runs after the APIC phase, whose persistent ACPI +
# LAPIC/IOAPIC window owns one page-table page; every DMA base moved +0x1000.
ALLOCS = [
    (0, 0x00000, 0x121000, 0x1, 0x1000, 0x1000),
    (1, 0x01000, 0x121000, 0xFFF, 0x1000, 0x1000),
    (2, 0x02000, 0x121000, 0x1000, 0x1000, 0x1000),
    (3, 0x03000, 0x121000, 0x1001, 0x2000, 0x1000),
    (4, 0x05000, 0x121000, 0x3000, 0x3000, 0x1000),
    (5, 0x08000, 0x121000, 0x3000, 0x3000, 0x1000),
    (6, 0x0B000, 0x121000, 0x2000, 0x2000, 0x1000),
    (7, 0x0D000, 0x121000, 0x2000, 0x2000, 0x1000),
    (8, 0x0F000, 0x121000, 0x1000, 0x1000, 0x1000),
    (9, 0x10000, 0x126000, 0x2000, 0x2000, 0x2000),
    (10, 0x12000, 0x128000, 0x4000, 0x4000, 0x1000),
    (11, 0x16000, 0x126000, 0x2000, 0x2000, 0x2000),
    (12, 0x18000, 0x121000, 0x1000, 0x1000, 0x1000),
    (13, 0x19000, 0x125000, 0x2000, 0x2000, 0x1000),
    (14, 0x1B000, 0x127000, 0x1000, 0x1000, 0x1000),
    (15, 0x1C000, 0x121000, 0x1000, 0x1000, 0x1000),
    (16, 0x1D000, 0x121000, 0x2000, 0x2000, 0x1000),
    (17, 0x1F000, 0x121000, 0x10000, 0x10000, 0x1000),
    (18, 0x2F000, 0x121000, 0x1000, 0x1000, 0x1000),
    (19, 0x30000, 0x125000, 0x1000, 0x1000, 0x1000),
    (20, 0x31000, 0x126000, 0x1000, 0x1000, 0x1000),
    (21, 0x32000, 0x127000, 0x1000, 0x1000, 0x1000),
    (22, 0x33000, 0x128000, 0x1000, 0x1000, 0x1000),
    (23, 0x34000, 0x129000, 0x1000, 0x1000, 0x1000),
    (24, 0x35000, 0x12a000, 0x1000, 0x1000, 0x1000),
    (25, 0x36000, 0x12b000, 0x1000, 0x1000, 0x1000),
    (26, 0x37000, 0x12c000, 0x1000, 0x1000, 0x1000),
    (27, 0x38000, 0x12d000, 0x1000, 0x1000, 0x1000),
    (28, 0x39000, 0x12e000, 0x1000, 0x1000, 0x1000),
    (29, 0x3A000, 0x12f000, 0x1000, 0x1000, 0x1000),
    (30, 0x3B000, 0x130000, 0x1000, 0x1000, 0x1000),
    (31, 0x3C000, 0x131000, 0x1000, 0x1000, 0x1000),
    (32, 0x3D000, 0x132000, 0x1000, 0x1000, 0x1000),
    (33, 0x3E000, 0x133000, 0x1000, 0x1000, 0x1000),
    (34, 0x3F000, 0x121000, 0x1000, 0x1000, 0x1000),
]


class DmaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/dma-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination, dma_test=True)
        cls.rynoros = cls.destination / "rynoros.img"

    def _boot(self, name):
        from qemu import boot_image
        logs = self.work / name
        out = boot_image(self.rynoros, logs, timeout=60)
        self.assertEqual(validate_dma_section(out), [])
        evidence = parse_dma_section(out)
        self.assertEqual(evidence.failures, [])
        self.assertTrue(evidence.verified)
        return evidence

    def test_synthetic_model_agreement(self):
        evidence = self._boot("synthetic")
        self.assertEqual(evidence.scan_cases, 17)
        got = [(c.index, c.n, c.align, c.limit, c.b0, c.p0, c.b1,
                c.p1, c.bits, c.found, c.start) for c in evidence.scases]
        self.assertEqual(got, SCASES)
        # Independent model replays every row from its printed inputs.
        for case in evidence.scases:
            spans = [(case.b0, case.p0)]
            if case.p1:
                spans.append((case.b1, case.p1))
            found, start = model_scan(spans, case.bits, case.n,
                                      case.align, case.limit)
            with self.subTest(case=case.index):
                self.assertEqual(found, bool(case.found))
                self.assertEqual(start if found else 0xDEAD, case.start)

    def test_live_alloc_table(self):
        evidence = self._boot("live")
        self.assertTrue(evidence.validation_ok)
        got = [(a.id, a.virt - VA_BASE, a.phys, a.size, a.alloc,
                a.align) for a in evidence.allocs]
        self.assertEqual(got, ALLOCS)
        for alloc in evidence.allocs:
            with self.subTest(id=alloc.id):
                # Frozen DMA-A1 rule, observed per buffer.
                self.assertEqual(alloc.bus, alloc.phys)
                self.assertNotEqual(alloc.virt, alloc.phys)
                self.assertEqual(alloc.virt % 0x1000, 0)
                self.assertEqual(alloc.phys % alloc.align, 0)
                self.assertEqual(alloc.alloc % 0x1000, 0)
                self.assertTrue(alloc.size <= alloc.alloc <
                                alloc.size + 0x1000)
                self.assertTrue(alloc.align != 0 and
                                (alloc.align & (alloc.align - 1)) == 0)
        # Bump-only VA: strictly increasing, globally non-overlapping.
        ranges = sorted((a.virt, a.virt + a.alloc) for a in evidence.allocs)
        for (first_end, second) in zip([end for _, end in ranges],
                                       [start for start, _ in ranges[1:]]):
            self.assertLessEqual(first_end, second)
        self.assertEqual(evidence.frees, [5])
        self.assertTrue(evidence.double_free)
        self.assertEqual(evidence.pattern_pages, 3)
        self.assertTrue(evidence.crosspage_ok)
        # Reuse identities: same frames, exact PFNs.
        self.assertEqual(evidence.reuses, [0x121000, 0x126000])
        self.assertEqual(evidence.reuses[0], evidence.allocs[7].phys)
        self.assertEqual(evidence.reuses[1], evidence.allocs[11].phys)
        self.assertTrue(evidence.rollback_ok)
        self.assertEqual(evidence.dma32, 0x121000)
        self.assertTrue(evidence.oom_ok)
        self.assertTrue(evidence.busy_ok)
        self.assertTrue(evidence.guard_ok)
        self.assertTrue(evidence.sync_ok)

    def test_determinism(self):
        first = self._boot("det1")
        second = self._boot("det2")
        one = [(a.id, a.virt, a.phys, a.bus, a.size, a.alloc, a.align)
               for a in first.allocs]
        two = [(a.id, a.virt, a.phys, a.bus, a.size, a.alloc, a.align)
               for a in second.allocs]
        self.assertEqual(one, two)
        one_scans = [(c.index, c.found, c.start) for c in first.scases]
        two_scans = [(c.index, c.found, c.start) for c in second.scases]
        self.assertEqual(one_scans, two_scans)


class DmaMutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/dma-tests"
        cls.work.mkdir(parents=True, exist_ok=True)

    def _mutant_boot(self, name, edits):
        """Copy the tree, apply source edits, build a dma_test image,
        boot the default topology. Returns parsed DMA evidence; guest
        self-test failures raise RuntimeError carrying the tag."""
        import shutil
        import tempfile
        from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
        from qemu import boot_image
        with tempfile.TemporaryDirectory(prefix="dma-mut-",
                                        dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            for source, old, new, count in edits:
                path = root / source
                contents = path.read_text(encoding="utf-8")
                self.assertEqual(contents.count(old), count, (name, source))
                path.write_text(contents.replace(old, new), encoding="utf-8")
            dest = root / "build" / "img"
            build_image(root, dest, dma_test=True)
            logs = self.work / name
            out = boot_image(dest / "rynoros.img", logs, timeout=60)
            return parse_dma_section(out)

    def test_mutant_run_clear_skips_last_goes_red(self):
        """M1: contiguous check skips the last frame: the all-used
        single (i=1) reports a phantom free frame (s-found)."""
        with self.assertRaisesRegex(RuntimeError, "s-found"):
            self._mutant_boot(
                "mut-m1",
                [("kernel/mm/pmm.c",
                  "for (cpu_u64 j = 0; j < n; ++j) {",
                  "for (cpu_u64 j = 0; j + 1 < n; ++j) {", 1)])

    def test_mutant_alignment_ignored_goes_red(self):
        """M2: alignment start ignored: the 8K fixture (i=4) reports
        the misaligned frame 0 instead of 1 (s-start)."""
        with self.assertRaisesRegex(RuntimeError, "s-start"):
            self._mutant_boot(
                "mut-m2",
                [("kernel/mm/pmm.c", "if (step > 1) {",
                  "if (0) {", 1)])

    def test_mutant_first_fit_late_goes_red(self):
        """M3: scan starts one frame late: the all-free single (i=0)
        reports 1 instead of 0 (s-start)."""
        with self.assertRaisesRegex(RuntimeError, "s-start"):
            self._mutant_boot(
                "mut-m3",
                [("kernel/mm/pmm.c",
                  "for (cpu_u64 k = first; k <= span - n; k += step)",
                  "for (cpu_u64 k = first + 1; k <= span - n; k += step)",
                  1)])

    def test_mutant_final_frame_boundary_goes_red(self):
        """M4: loop bound off by one: the exact-fit run (i=3) vanishes
        because its last candidate is never tried (s-found)."""
        with self.assertRaisesRegex(RuntimeError, "s-found"):
            self._mutant_boot(
                "mut-m4",
                [("kernel/mm/pmm.c",
                  "for (cpu_u64 k = first; k <= span - n; k += step)",
                  "for (cpu_u64 k = first; k < span - n; k += step)",
                  1)])

    def test_mutant_marks_n_minus_1_goes_red(self):
        """M5: allocation marks N-1 frames: the first live single is
        never marked, the mapping preflight rejects the unowned
        frame, and the rollback's release trips the PMM-contradiction
        diagnostic (pmm-rollback)."""
        with self.assertRaisesRegex(RuntimeError, "pmm-rollback"):
            self._mutant_boot(
                "mut-m5",
                [("kernel/mm/pmm.c",
                  "for (cpu_u64 j = 0; j < frames; ++j) {",
                  "for (cpu_u64 j = 0; j + 1 < frames; ++j) {", 1)])

    def test_mutant_marks_n_plus_1_goes_red(self):
        """M6: allocation marks N+1 frames: the stray mark breaks the
        PMM bit/statistic recount immediately (v-round)."""
        with self.assertRaisesRegex(RuntimeError, "v-round"):
            self._mutant_boot(
                "mut-m6",
                [("kernel/mm/pmm.c",
                  "for (cpu_u64 j = 0; j < frames; ++j) {",
                  "for (cpu_u64 j = 0; j <= frames; ++j) {", 1)])

    def test_mutant_free_n_minus_1_goes_red(self):
        """M7: free releases N-1 frames: the freed single still reads
        allocated (v-refree)."""
        with self.assertRaisesRegex(RuntimeError, "v-refree"):
            self._mutant_boot(
                "mut-m7",
                [("kernel/mm/dma.c",
                  "for (cpu_u64 i = 0; i < pages; ++i)",
                  "for (cpu_u64 i = 0; i + 1 < pages; ++i)", 1)])

    def test_mutant_unmap_skipped_goes_red(self):
        """M8: free skips the virtual unmap (leaked mapping): the
        post-free VA still queries mapped (v-unmapped). A literal
        N+1-frame free is unkillable-cleanly here (the first freed run
        abuts free space, so the extra release halts instead of
        failing), so the free-path mutant covers the mapping leak."""
        with self.assertRaisesRegex(RuntimeError, "v-unmapped"):
            self._mutant_boot(
                "mut-m8",
                [("kernel/mm/dma.c",
                  "if (!space || vm_unmap_range(space, entry->virt, entry->pages) != VM_OK) cpu_halt();",
                  "if (!space) cpu_halt();", 1)])

    def test_mutant_zeroing_removed_goes_red(self):
        """M9: zero-on-alloc removed: virgin-frame zero checks
        false-pass on QEMU-zeroed RAM, but the 0xA5 fill/free/realloc
        proof observes the stale bytes (r-zero)."""
        with self.assertRaisesRegex(RuntimeError, "r-zero"):
            self._mutant_boot(
                "mut-m9",
                [("kernel/mm/dma.c",
                  "for (cpu_u64 i = 0; i < alloc_size; ++i) zero[i] = 0;",
                  "(void)zero;", 1)])

    def test_mutant_wrong_pfn_goes_red(self):
        """M10: physical address translated one frame past the marked
        run: the mapping preflight rejects the unowned frame, and the
        rollback's release of that never-allocated frame trips the
        PMM-contradiction diagnostic (pmm-rollback)."""
        with self.assertRaisesRegex(RuntimeError, "pmm-rollback"):
            self._mutant_boot(
                "mut-m10",
                [("kernel/mm/pmm.c",
                  "*physical = r->base + remaining * PMM_PAGE_SIZE;\n"
                  "        for (cpu_u64 j = 0; j < frames; ++j) {",
                  "*physical = r->base + (remaining + 1) * PMM_PAGE_SIZE;\n"
                  "        for (cpu_u64 j = 0; j < frames; ++j) {", 1)])

    def test_mutant_va_repeats_first_pfn_goes_red(self):
        """M11: every VA page maps the first PFN: the first multi-page
        buffer's page-1 PTE mismatches (v-round)."""
        with self.assertRaisesRegex(RuntimeError, "v-round"):
            self._mutant_boot(
                "mut-m11",
                [("kernel/mm/dma.c",
                  "if (vm_map_range(space, va, phys, frames, VM_WRITE) != VM_OK) {",
                  "enum vm_result m11r = VM_OK;\n"
                  "    for (cpu_u64 m11i = 0; m11i < frames && m11r == VM_OK; ++m11i)\n"
                  "        m11r = vm_map(space, va + m11i * DMA_PAGE_SIZE, phys, VM_WRITE);\n"
                  "    if (m11r != VM_OK) {", 1)])

    def test_mutant_bus_from_virt_goes_red(self):
        """M12: bus address derived from virt: live virt != phys, so
        the bus==phys assertion fails on the first buffer (v-round)."""
        with self.assertRaisesRegex(RuntimeError, "v-round"):
            self._mutant_boot(
                "mut-m12",
                [("kernel/mm/dma.c", "dma_phys_to_bus(phys)",
                  "(cpu_u64)va", 1)])

    def test_mutant_limit_ignored_goes_red(self):
        """M13: address limit ignored: the only-high run (i=10)
        reports found under a 4G cap (s-found)."""
        with self.assertRaisesRegex(RuntimeError, "s-found"):
            self._mutant_boot(
                "mut-m13",
                [("kernel/mm/pmm.c", "if (limit_end) {",
                  "if (0) {", 1)])

    def test_mutant_rollback_leaks_frames_goes_red(self):
        """M14: VA-failure rollback forgets the frames: PMM statistics
        stay debited after the forced failure (rb-leak)."""
        with self.assertRaisesRegex(RuntimeError, "rb-leak"):
            self._mutant_boot(
                "mut-m14",
                [("kernel/mm/dma.c",
                  "release_frames(phys, frames); /* VA failure rolls physical back. */",
                  "(void)phys;", 1)])
