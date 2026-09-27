"""PCI-A1: discovery + BAR resources + safe MMIO mapping in QEMU.

The gated kernel self-test (RYNOR_PCI_TEST images) proves the mock
transport, the synthetic topology (13 devices / 3 buses), and live
hardware; this suite pins the live evidence against the independent
QEMU HMP reference (SeaBIOS-configured `info pci` captures, not
memory): exact BDF/vendor/class/BAR tables per topology, exact
mapping VAs, restoration rows, stats, and 14 check-removal mutants.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image  # noqa: E402
from pci_output import parse_pci_section, validate_pci_section  # noqa: E402

EXT_ARGS = ("-device", "pci-bridge,chassis_nr=2,id=br0",
            "-device", "e1000,bus=br0,addr=1",
            "-device", "virtio-net-pci,bus=br0,addr=2",
            "-device", "e1000,bus=br0,addr=4.0,multifunction=on",
            "-device", "e1000,bus=br0,addr=4.1")

VA_BASE = 0xFFFFFE8010000000

# (bus, dev, fn, vendor, device, class, sub, prog, rev, hdr, mf,
#  cmd, status, irql, irqp, nbar). Command words are SeaBIOS firmware
# state (0x103 = IO+MEM+SERR); the restore rows prove PCI-A1 preserves
# them bit-for-bit and never sets BUS_MASTER itself.
DEF_DEVS = [
    (0, 0, 0, 0x8086, 0x1237, 0x06, 0x00, 0x00, 0x02, 0, 0,
     0x0103, 0x0000, 0x00, 0, 0),
    (0, 1, 0, 0x8086, 0x7000, 0x06, 0x01, 0x00, 0x00, 0, 1,
     0x0103, 0x0200, 0x00, 0, 0),
    (0, 1, 1, 0x8086, 0x7010, 0x01, 0x01, 0x80, 0x00, 0, 1,
     0x0103, 0x0280, 0x00, 0, 1),
    (0, 1, 3, 0x8086, 0x7113, 0x06, 0x80, 0x00, 0x03, 0, 1,
     0x0103, 0x0280, 0x09, 1, 0),
    (0, 2, 0, 0x1234, 0x1111, 0x03, 0x00, 0x00, 0x02, 0, 0,
     0x0103, 0x0000, 0x00, 0, 2),
]
# (bus, dev, fn, index, kind, base, size, prefetch, rawhi).
DEF_BARS = [
    (0, 1, 1, 4, "io", 0xC000, 0x10, 0, 0),
    (0, 2, 0, 0, "mmio32", 0xFD000000, 0x1000000, 1, 0),
    (0, 2, 0, 2, "mmio32", 0xFEBF0000, 0x1000, 0, 0),
]
DEF_STATS = {"reads": 1468, "writes": 155, "buses": 1, "functions": 5,
             "bars": 3, "registry": 3072}

EXT_DEVS = [
    (0, 0, 0, 0x8086, 0x1237, 0x06, 0x00, 0x00, 0x02, 0, 0,
     0x0103, 0x0000, 0x00, 0, 0),
    (0, 1, 0, 0x8086, 0x7000, 0x06, 0x01, 0x00, 0x00, 0, 1,
     0x0103, 0x0200, 0x00, 0, 0),
    (0, 1, 1, 0x8086, 0x7010, 0x01, 0x01, 0x80, 0x00, 0, 1,
     0x0103, 0x0280, 0x00, 0, 1),
    (0, 1, 3, 0x8086, 0x7113, 0x06, 0x80, 0x00, 0x03, 0, 1,
     0x0103, 0x0280, 0x09, 1, 0),
    (0, 2, 0, 0x1234, 0x1111, 0x03, 0x00, 0x00, 0x02, 0, 0,
     0x0103, 0x0000, 0x00, 0, 2),
    (0, 3, 0, 0x1B36, 0x0001, 0x06, 0x04, 0x00, 0x00, 1, 0,
     0x0103, 0x00B0, 0x0B, 1, 1),
    (1, 1, 0, 0x8086, 0x100E, 0x02, 0x00, 0x00, 0x03, 0, 0,
     0x0103, 0x0000, 0x0B, 1, 2),
    (1, 2, 0, 0x1AF4, 0x1000, 0x02, 0x00, 0x00, 0x00, 0, 0,
     0x0103, 0x0010, 0x0A, 1, 3),
    (1, 4, 0, 0x8086, 0x100E, 0x02, 0x00, 0x00, 0x03, 0, 1,
     0x0103, 0x0000, 0x0B, 1, 2),
    (1, 4, 1, 0x8086, 0x100E, 0x02, 0x00, 0x00, 0x03, 0, 1,
     0x0103, 0x0000, 0x0B, 1, 2),
]
EXT_BARS = [
    (0, 1, 1, 4, "io", 0xD000, 0x10, 0, 0),
    (0, 2, 0, 0, "mmio32", 0xFD000000, 0x1000000, 1, 0),
    (0, 2, 0, 2, "mmio32", 0xFEA10000, 0x1000, 0, 0),
    (0, 3, 0, 0, "mmio64", 0xFEA11000, 0x100, 0, 0),
    (1, 1, 0, 0, "mmio32", 0xFE900000, 0x20000, 0, 0),
    (1, 1, 0, 1, "io", 0xC000, 0x40, 0, 0),
    (1, 2, 0, 0, "io", 0xC0C0, 0x20, 0, 0),
    (1, 2, 0, 1, "mmio32", 0xFE960000, 0x1000, 0, 0),
    (1, 2, 0, 4, "mmio64", 0xFE000000, 0x4000, 1, 0),
    (1, 4, 0, 0, "mmio32", 0xFE920000, 0x20000, 0, 0),
    (1, 4, 0, 1, "io", 0xC040, 0x40, 0, 0),
    (1, 4, 1, 0, "mmio32", 0xFE940000, 0x20000, 0, 0),
    (1, 4, 1, 1, "io", 0xC080, 0x40, 0, 0),
]
EXT_STATS = {"reads": 1815, "writes": 243, "buses": 2, "functions": 10,
             "bars": 13, "registry": 3072}


def _expect_maps(devs, bars):
    """Sequential VA allocation from VA_BASE in registry/map order
    (device order, entry order; I/O entries refused, no map row).
    All firmware bases here are page-aligned (offset 0)."""
    entries = {}
    for bus, dev, fn, index, kind, base, size, _, _ in bars:
        entries.setdefault((bus, dev, fn), []).append((kind, base, size))
    want = []
    cursor = VA_BASE
    fb_done = False
    for dev in devs:
        key = dev[:3]
        for entry, (kind, base, size) in enumerate(entries.get(key, [])):
            if kind == "io":
                continue
            pages = (size + 0xFFF) // 0x1000
            xcheck = 0
            if (not fb_done and kind == "mmio32" and size >= 0x100000 and
                    base == 0xFD000000):
                fb_done = True
                xcheck = 1
            want.append((key[0], key[1], key[2], entry, cursor, pages,
                         xcheck))
            cursor += pages * 0x1000
    return want


class PciTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/pci-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination, pci_test=True)
        cls.rynoros = cls.destination / "rynoros.img"

    def _boot(self, name, extra_args=()):
        from qemu import boot_image
        logs = self.work / name
        out = boot_image(self.rynoros, logs, timeout=60,
                         extra_args=tuple(extra_args))
        self.assertEqual(validate_pci_section(out), [])
        evidence = parse_pci_section(out)
        self.assertEqual(evidence.failures, [])
        self.assertTrue(evidence.transport_ok)
        self.assertEqual(evidence.synthetic, (14, 3))
        self.assertTrue(evidence.rescan_ok)
        self.assertTrue(evidence.verified)
        return evidence

    def _check_topology(self, evidence, devs, bars, stats):
        got_devs = [(d.bus, d.dev, d.fn, d.vendor, d.device,
                     d.class_code, d.subclass, d.prog_if, d.revision,
                     d.header, d.multifunction, d.command, d.status,
                     d.irq_line, d.irq_pin, d.nbar)
                    for d in evidence.devs]
        self.assertEqual(got_devs, devs)
        got_bars = [(b.bus, b.dev, b.fn, b.index, b.kind, b.base,
                     b.size, b.prefetch, b.rawhi)
                    for b in evidence.bars]
        self.assertEqual(got_bars, bars)
        # Every enumerated function proves BAR+COMMAND restoration.
        self.assertEqual(sorted(evidence.restores),
                         sorted(d[:3] for d in devs))
        self.assertEqual(evidence.live_devices, len(devs))
        self.assertEqual(evidence.stats, stats)
        got_maps = [(m.bus, m.dev, m.fn, m.entry, m.va, m.pages,
                     m.xcheck) for m in evidence.maps]
        self.assertEqual(got_maps, _expect_maps(devs, bars))
        # Exactly one framebuffer cross-read, on the VGA aperture.
        xchecks = [m for m in evidence.maps if m.xcheck == 1]
        self.assertEqual(len(xchecks), 1)
        self.assertEqual((xchecks[0].bus, xchecks[0].dev,
                          xchecks[0].fn, xchecks[0].entry), (0, 2, 0, 0))

    def test_default_topology(self):
        evidence = self._boot("default")
        self._check_topology(evidence, DEF_DEVS, DEF_BARS, DEF_STATS)

    def test_extended_topology(self):
        evidence = self._boot("extended", EXT_ARGS)
        self._check_topology(evidence, EXT_DEVS, EXT_BARS, EXT_STATS)

    def test_enumeration_deterministic(self):
        first = self._boot("det1")
        second = self._boot("det2")
        one = [(d.bus, d.dev, d.fn, d.vendor, d.device) for d in first.devs]
        two = [(d.bus, d.dev, d.fn, d.vendor, d.device)
               for d in second.devs]
        self.assertEqual(one, two)
        one_bars = [(b.bus, b.dev, b.fn, b.index, b.base, b.size)
                    for b in first.bars]
        two_bars = [(b.bus, b.dev, b.fn, b.index, b.base, b.size)
                    for b in second.bars]
        self.assertEqual(one_bars, two_bars)


class PciMutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/pci-tests"
        cls.work.mkdir(parents=True, exist_ok=True)

    def _mutant_boot(self, name, edits):
        """Copy the tree, apply source edits, build a pci_test image,
        boot the extended topology. Returns parsed PCI evidence; guest
        self-test failures raise RuntimeError carrying the tag."""
        import shutil
        import tempfile
        from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
        from qemu import boot_image
        with tempfile.TemporaryDirectory(prefix="pci-mut-",
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
            build_image(root, dest, pci_test=True)
            logs = self.work / name
            out = boot_image(dest / "rynoros.img", logs, timeout=60,
                             extra_args=EXT_ARGS)
            return parse_pci_section(out)

    def test_mutant_absent_fn_check_goes_red(self):
        """M1: function-level absent check removed: multifunction
        parents sprout vendor-ffff phantoms on every empty function,
        overflowing the registry already in the mock phase (s-init)."""
        with self.assertRaisesRegex(RuntimeError, "s-init"):
            self._mutant_boot(
                "mut-absent",
                [("kernel/drivers/pci.c", "if (v == PCI_VENDOR_ABSENT)",
                  "if (0)", 1)])

    def test_mutant_multifunction_ignored_goes_red(self):
        """M2: MF bit ignored: functions 1-7 never scanned; the mock
        loses both fn1 fixtures and dies at s-count (live hardware
        would lose the IDE, power-management, and second-e1000
        functions the same way)."""
        with self.assertRaisesRegex(RuntimeError, "s-count"):
            self._mutant_boot(
                "mut-mf",
                [("kernel/drivers/pci.c",
                  "maxfn = (header & 0x80u) ? PCI_MAX_FUNCTION : 0;",
                  "maxfn = 0;", 1)])

    def test_mutant_function_shift_goes_red(self):
        """M3: function number shifted wrong in the CF8 address: real
        functions >= 1 vanish into neighbor space while fn4's slipped
        bit aliases fn0 of the same device, conjuring a phantom (0,1,4)
        with the ISA-bridge identity (8 devices, not 7 or 10)."""
        evidence = self._mutant_boot(
            "mut-fnshift",
            [("kernel/drivers/pci.c", "(fn << 8)", "(fn << 9)", 1)])
        self.assertEqual(len(evidence.devs), 8)
        phantom = [d for d in evidence.devs
                   if (d.bus, d.dev, d.fn) == (0, 1, 4)][0]
        self.assertEqual((phantom.vendor, phantom.device),
                         (0x8086, 0x7000))

    def test_mutant_class_lane_swap_goes_red(self):
        """M4: class/subclass byte lanes swapped: the mock fixture's
        class assertion dies at s-f0cls (live hardware would report
        the VGA as class 0 sub 3)."""
        with self.assertRaisesRegex(RuntimeError, "s-f0cls"):
            self._mutant_boot(
                "mut-classlane",
                [("kernel/drivers/pci.c",
                  "out->subclass = pci_cfg_read8(bus, dev, fn, PCI_CFG_SUBCLASS);\n"
                  "    out->class_code = pci_cfg_read8(bus, dev, fn, PCI_CFG_CLASS);",
                  "out->subclass = pci_cfg_read8(bus, dev, fn, PCI_CFG_CLASS);\n"
                  "    out->class_code = pci_cfg_read8(bus, dev, fn, PCI_CFG_SUBCLASS);",
                  1)])

    def test_mutant_bar64_high_ignored_goes_red(self):
        """M5: 64-bit BAR high half dropped from the base: live QEMU
        BARs sit below 4G (invisible there), but the synthetic
        above-4G fixture dies at s-f0b64."""
        with self.assertRaisesRegex(RuntimeError, "s-f0b64"):
            self._mutant_boot(
                "mut-high",
                [("kernel/drivers/pci.c",
                  "base = ((cpu_u64)high << 32) | (raw & PCI_BAR_ADDR32);",
                  "base = (cpu_u64)(raw & PCI_BAR_ADDR32);", 1)])

    def test_mutant_bar_flag_mask_goes_red(self):
        """M6: flag mask removed from the 32-bit base: the prefetch
        flag bit lands in the base, misalignment rejection drops the
        VGA aperture, and the framebuffer cross-check has no candidate
        (live-fbxcheck)."""
        with self.assertRaisesRegex(RuntimeError, "live-fbxcheck"):
            self._mutant_boot(
                "mut-flagmask",
                [("kernel/drivers/pci.c",
                  "        base = (cpu_u64)(raw & PCI_BAR_ADDR32);",
                  "        base = (cpu_u64)raw;", 1)])

    def test_mutant_size_plus_one_goes_red(self):
        """M7: size-mask +1 dropped: every 32-bit mask decodes to a
        non-power-of-two and the synthetic fixture dies at s-f0bars."""
        with self.assertRaisesRegex(RuntimeError, "s-f0bars"):
            self._mutant_boot(
                "mut-sizemath",
                [("kernel/drivers/pci.c",
                  "((~(mask_low & PCI_BAR_ADDR32)) + 1u)",
                  "((~(mask_low & PCI_BAR_ADDR32)))", 1)])

    def test_mutant_bar_not_restored_goes_red(self):
        """M8: BAR restore write removed: the post-probe re-read
        mismatches, every probed BAR is dropped (s-f0bars)."""
        with self.assertRaisesRegex(RuntimeError, "s-f0bars"):
            self._mutant_boot(
                "mut-norestore",
                [("kernel/drivers/pci.c",
                  "    pci_cfg_write32(bus, dev, fn, off, raw_low);",
                  "    (void)raw_low;", 1)])

    def test_mutant_command_not_restored_goes_red(self):
        """M9: COMMAND restore removed: decode stays off after the
        probe, the re-read mismatches (s-f0bars)."""
        with self.assertRaisesRegex(RuntimeError, "s-f0bars"):
            self._mutant_boot(
                "mut-nocmd",
                [("kernel/drivers/pci.c",
                  "    pci_cfg_write16(bus, dev, fn, PCI_CFG_COMMAND, cmd);",
                  "    (void)cmd;", 1)])

    def test_mutant_bar64_slot_reuse_goes_red(self):
        """M10: consumed 64-bit neighbor re-decoded as an independent
        BAR: the high half (value 1) decodes as a phantom 4-byte I/O
        BAR that displaces the real last entry (s-f0b5)."""
        with self.assertRaisesRegex(RuntimeError, "s-f0b5"):
            self._mutant_boot(
                "mut-slotreuse",
                [("kernel/drivers/pci.c", "        slot += used;",
                  "        slot += 1;", 1)])

    def test_mutant_visited_guard_goes_red(self):
        """M11: bridge visited-guard removed: the mock cycle fixture
        re-enqueues buses until the registry overflows (s-init)."""
        with self.assertRaisesRegex(RuntimeError, "s-init"):
            self._mutant_boot(
                "mut-visited",
                [("kernel/drivers/pci.c",
                  "if ((visited[sec / 64u] >> (sec % 64u)) & 1u)",
                  "if (0)", 1)])

    def test_mutant_bus_shift_goes_red(self):
        """M13: bus shifted wrong in the CF8 address: bus-1 reads land
        on phantom bus-0 functions, the whole second bus vanishes."""
        evidence = self._mutant_boot(
            "mut-busshift",
            [("kernel/drivers/pci.c", "(bus << 16)", "(bus << 15)", 1)])
        self.assertEqual(len(evidence.devs), 6)
        self.assertFalse(any(d.bus == 1 for d in evidence.devs))

    def test_mutant_device_shift_goes_red(self):
        """M14: device shifted wrong: slot 1 reads slot 2's space, so
        bus 0 dev 1 reports the VGA identity (vendor 1234)."""
        evidence = self._mutant_boot(
            "mut-devshift",
            [("kernel/drivers/pci.c", "(dev << 11)", "(dev << 12)", 1)])
        slot1 = [d for d in evidence.devs
                 if (d.bus, d.dev, d.fn) == (0, 1, 0)][0]
        self.assertEqual(slot1.vendor, 0x1234)

    def test_mutant_enable_bit_goes_red(self):
        """M15: CF8 enable bit missing: every config read floats high,
        no device is found (live-nonempty)."""
        with self.assertRaisesRegex(RuntimeError, "live-nonempty"):
            self._mutant_boot(
                "mut-enable",
                [("kernel/drivers/pci.c",
                  "return PCI_CF8_ENABLE | (bus << 16) | (dev << 11) | (fn << 8) |",
                  "return (bus << 16) | (dev << 11) | (fn << 8) |", 1)])
