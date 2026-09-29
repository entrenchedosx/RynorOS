"""INT-A2 MSI/MSI-X integration tests: synthetic matrices plus live
QEMU proofs (edu MSI, xHCI MSI-X, e1000e multi-vector MSI-X,
pci-testdev negative control) and C/M/X/B mutants."""

import shutil
import tempfile
import unittest
from pathlib import Path

from image import build_image
from msi_output import MSIError, verify_msi_section
from qemu import boot_image

ROOT = Path(__file__).resolve().parents[2]

STOCK_WALKS = [
    "[MSI] walk bdf=0:0.0 msi=0 msix=0 malformed=1",
    "[MSI] walk bdf=0:1.0 msi=0 msix=0 malformed=1",
    "[MSI] walk bdf=0:1.1 msi=0 msix=0 malformed=1",
    "[MSI] walk bdf=0:1.3 msi=0 msix=0 malformed=1",
    "[MSI] walk bdf=0:2.0 msi=0 msix=0 malformed=1",
]


class MsiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image_dir = ROOT / "build" / "msi-tests" / "image"
        if cls.image_dir.exists():
            shutil.rmtree(cls.image_dir)
        build_image(ROOT, cls.image_dir, msi_test=True)
        cls.image = cls.image_dir / "rynoros.img"

    def _boot(self, name, extra_args=()):
        logs = ROOT / "build" / "msi-tests" / name
        if logs.exists():
            shutil.rmtree(logs)
        try:
            boot_image(self.image, logs, timeout=60,
                       extra_args=list(extra_args))
        except RuntimeError:
            # The MSI section extends past the harness's expected
            # terminator; the completed serial still lands in serial.log.
            pass
        return logs.joinpath("serial.log").read_text(
            encoding="ascii", errors="replace")

    def _section(self, text, devices):
        rows = verify_msi_section(text, expected_devices=devices)
        return rows

    def _raws(self, rows):
        return [r.get("raw", "") for r in rows]

    def test_stock_topology(self):
        text = self._boot("logs-stock")
        rows = self._section(text, 5)
        raws = self._raws(rows)
        for walk in STOCK_WALKS:
            self.assertIn(walk, text)
        self.assertIn("[MSI] edu none", text)
        self.assertIn("[MSI] xhci none", text)
        self.assertIn("[MSI] e1000e none", text)
        self.assertIn("[MSI] testdev none", text)
        self.assertFalse([r for r in rows if r.get("kind") == "enable"],
                         "stock boot must not enable MSI/MSI-X")
        self.assertIn("[MSI] live ok devices=5", text)

    def test_edu_proof(self):
        text = self._boot("logs-edu", ["-device", "edu"])
        rows = self._section(text, 6)
        self.assertIn("[MSI] walk bdf=0:3.0 msi=1@40 msix=0 malformed=0",
                      text)
        self.assertIn("[MSI] map bdf=0:3.0 entry=0 va=fffffe8010000000 "
                      "pages=256", text)
        self.assertIn("[MSI] msi bdf=0:3.0 mmc=0 mme=0 is64=1 mask=0 "
                      "enabled=0 len=14", text)
        self.assertIn("[MSI] refuse op=msi bdf=0:3.0 reason=no-busmaster",
                      text)
        self.assertIn("[MSI] msg vec=48 addr=fee00000 hi=0 data=30 apic=0",
                      text)
        self.assertIn("[MSI] enable bdf=0:3.0 kind=msi nvec=1 vec=48 ok",
                      text)
        self.assertIn("[MSI] irq vec=48 count=1 isr=1", text)
        self.assertIn("[MSI] fact value=1", text)
        self.assertIn("[MSI] intx bdf=0:3.0 intxoff=1 silent=1", text)
        self.assertIn("[MSI] disable handle=0 kind=msi ok", text)
        self.assertIn("[MSI] live ok devices=6", text)

    def test_xhci_proof(self):
        text = self._boot("logs-xhci", ["-device", "nec-usb-xhci"])
        rows = self._section(text, 6)
        self.assertIn("[MSI] walk bdf=0:3.0 msi=1@70 msix=1@90 malformed=0",
                      text)
        self.assertIn("[MSI] map bdf=0:3.0 entry=0 va=fffffe8010000000 "
                      "pages=4", text)
        self.assertIn("[MSI] xhci caplen=40 dboff=2000 rtsoff=1000", text)
        self.assertIn("[MSI] msi bdf=0:3.0 mmc=4 mme=0 is64=1 mask=0 "
                      "enabled=0 len=14", text)
        self.assertIn("[MSI] mme bdf=0:3.0 mmc=4 wrote=4 read=4 restored=1",
                      text)
        self.assertIn("[MSI] msix bdf=0:3.0 n=16 tbl=0:3000 pba=0:3800",
                      text)
        self.assertIn("[MSI] enable bdf=0:3.0 kind=msix nvec=1 vec=48 ok",
                      text)
        self.assertIn("[MSI] entry bdf=0:3.0 i=0 addr=fee00000 data=30 "
                      "ctrl=0", text)
        self.assertIn("[MSI] irq vec=48 count=1 isr=1", text)
        self.assertIn("[MSI] pba bdf=0:3.0 vec=0 pending=1", text)
        self.assertIn("[MSI] pba bdf=0:3.0 vec=0 pending=0", text)
        self.assertIn("[MSI] intx bdf=0:3.0 intxoff=1 silent=1", text)
        self.assertIn("[MSI] disable handle=0 kind=msix ok", text)
        self.assertIn("[MSI] live ok devices=6", text)
        _ = rows

    def test_e1000e_proof(self):
        text = self._boot("logs-e1000e", ["-device", "e1000e"])
        rows = self._section(text, 6)
        self.assertIn("[MSI] walk bdf=0:3.0 msi=1@d0 msix=1@a0 malformed=0",
                      text)
        self.assertIn("[MSI] msi bdf=0:3.0 mmc=0 mme=0 is64=1 mask=0 "
                      "enabled=0 len=14", text)
        self.assertIn("[MSI] msix bdf=0:3.0 n=5 tbl=3:0 pba=3:2000", text)
        self.assertIn("[MSI] refuse op=auto bdf=0:3.0 reason=no-busmaster",
                      text)
        self.assertIn("[MSI] enable bdf=0:3.0 kind=msix nvec=1 vec=48 ok",
                      text)
        self.assertIn("[MSI] refuse op=msi-while-x bdf=0:3.0 reason=fn-busy",
                      text)
        self.assertIn("[MSI] map bdf=0:3.0 entry=0 va=fffffe8010004000 "
                      "pages=32", text)
        self.assertIn("[MSI] enable bdf=0:3.0 kind=msix nvec=3 "
                      "vec=48,49,50 ok", text)
        self.assertIn("[MSI] irq vec=49 count=1 isr=1", text)
        self.assertIn("[MSI] irq vec=50 count=1 isr=1", text)
        self.assertIn("[MSI] irq vec=48 count=1 isr=1", text)
        self.assertIn("[MSI] intx bdf=0:3.0 intxoff=1 silent=1", text)
        self.assertIn("[MSI] enable bdf=0:3.0 kind=msi nvec=1 vec=48 ok",
                      text)
        self.assertIn("[MSI] live ok devices=6", text)
        _ = rows

    def test_testdev_negative(self):
        text = self._boot("logs-testdev", ["-device", "pci-testdev"])
        rows = self._section(text, 6)
        self.assertIn("[MSI] walk bdf=0:3.0 msi=0 msix=0 malformed=1",
                      text)
        self.assertIn("[MSI] refuse op=auto bdf=0:3.0 reason=no-msi-x-cap",
                      text)
        self.assertFalse([r for r in rows if r.get("kind") == "enable"],
                         "negative control must not enable anything")
        self.assertIn("[MSI] live ok devices=6", text)

    def test_combined_topology(self):
        text = self._boot("logs-all", ["-device", "edu", "-device",
                                       "nec-usb-xhci", "-device", "e1000e",
                                       "-device", "pci-testdev"])
        rows = self._section(text, 9)
        self.assertIn("[MSI] walk bdf=0:3.0 msi=1@40 msix=0 malformed=0",
                      text)
        self.assertIn("[MSI] walk bdf=0:4.0 msi=1@70 msix=1@90 malformed=0",
                      text)
        self.assertIn("[MSI] walk bdf=0:5.0 msi=1@d0 msix=1@a0 malformed=0",
                      text)
        self.assertIn("[MSI] walk bdf=0:6.0 msi=0 msix=0 malformed=1",
                      text)
        self.assertIn("[MSI] map bdf=0:3.0 entry=0 va=fffffe8010000000 "
                      "pages=256", text)
        self.assertIn("[MSI] map bdf=0:4.0 entry=0 va=fffffe8010100000 "
                      "pages=4", text)
        self.assertIn("[MSI] map bdf=0:5.0 entry=0 va=fffffe8010108000 "
                      "pages=32", text)
        self.assertIn("[MSI] refuse op=auto bdf=0:6.0 reason=no-msi-x-cap",
                      text)
        self.assertIn("[MSI] live ok devices=9", text)
        _ = rows


class MsiMutantCase(unittest.TestCase):
    """Each mutant must turn the stock boot RED with its own tag."""

    MUTANTS = {
        # Walker misses MSI caps (inverted first-find guard).
        "walker": ("kernel/interrupts/msi.c",
                   "if (id == MSI_CAP_ID_MSI && !out->has_msi) {",
                   "if (id == MSI_CAP_ID_MSI && out->has_msi) {",
                   "w-msi"),
        # MSI DATA low bits set for multi-vector (spec: zero).
        "data": ("kernel/interrupts/msi.c",
                 "(msg.data & ~((1u << order) - 1u))",
                 "(msg.data | ((1u << order) - 1u))",
                 "d-datazero"),
        # MSI-X table/PBA overlap accepted.
        "overlap": ("kernel/interrupts/msi.c",
                    'return fail(MSI_REFUSED, "msix-overlap");',
                    "(void)0;",
                    "x-overlap"),
        # Bus-master refusal neutered (both enable paths).
        "busmaster": ("kernel/interrupts/msi.c",
                      'return fail(MSI_REFUSED, "no-busmaster");',
                      "(void)cmd;",
                      "d-nobm"),
    }

    def _run_mutant(self, name):
        rel, old, new, tag = self.MUTANTS[name]
        tmp = Path(tempfile.mkdtemp(prefix="rynor-msi-mutant-"))
        try:
            shutil.copytree(ROOT, tmp / "tree",
                            ignore=shutil.ignore_patterns(
                                "build", ".git", "__pycache__",
                                "rynoros"))
            target = tmp / "tree" / rel
            text = target.read_text(encoding="utf-8")
            self.assertIn(old, text, "mutant anchor missing: %s" % name)
            target.write_text(text.replace(old, new), encoding="utf-8")
            image_dir = tmp / "image"
            build_image(tmp / "tree", image_dir, msi_test=True)
            logs = tmp / "logs"
            with self.assertRaises(RuntimeError) as ctx:
                boot_image(image_dir / "rynoros.img", logs, timeout=60)
            self.assertIn("[MSI] failure=%s" % tag, str(ctx.exception),
                          "mutant %s did not trap %s" % (name, tag))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mutant_walker(self):
        self._run_mutant("walker")

    def test_mutant_data(self):
        self._run_mutant("data")

    def test_mutant_overlap(self):
        self._run_mutant("overlap")

    def test_mutant_busmaster(self):
        self._run_mutant("busmaster")


if __name__ == "__main__":
    unittest.main()
