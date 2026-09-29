"""xHCI-A1 integration tests: live NOOP+MSI-X proof, absent path,
and X/R/C/E/I/O source mutants (each must fail loud, never green)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image  # noqa: E402
from qemu import boot_image  # noqa: E402
from xhci_output import (XHCIError, verify_absent_section,  # noqa: E402
                         verify_xhci_section)

XHCI_DEVICE = ["-device", "nec-usb-xhci"]
TIMEOUT = 60

FIXTURE_DIRS = ("kernel", "boot", "tools", "assets/branding")
FIXTURE_FILES = ("project.json",)


def _mutate_copy(pairs):
    tmp = tempfile.TemporaryDirectory(prefix="xhci-mut-", dir=ROOT / "build")
    root = Path(tmp.name)
    for d in FIXTURE_DIRS:
        (root / d).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / d, root / d)
    for f in FIXTURE_FILES:
        src = ROOT / f
        if src.is_file():
            shutil.copyfile(src, root / f)
    path = root / "kernel/drivers/xhci.c"
    contents = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents, encoding="utf-8")
    return tmp, root


class XhciLiveCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "build" / "xhci-tests"
        cls.image_dir = cls.work / "image"
        if cls.image_dir.exists():
            shutil.rmtree(cls.image_dir)
        build_image(ROOT, cls.image_dir, xhci_test=True)
        cls.image = cls.image_dir / "rynoros.img"

    def _boot(self, name, extra_args=()):
        logs = self.work / name
        if logs.exists():
            shutil.rmtree(logs)
        try:
            boot_image(self.image, logs, timeout=TIMEOUT,
                       extra_args=list(extra_args))
        except RuntimeError:
            # The XHCI section extends past the harness's expected
            # terminator (same as MSI); the completed serial still
            # lands in serial.log.
            pass
        return logs.joinpath("serial.log").read_text(
            encoding="ascii", errors="replace")

    def test_live_proof(self):
        text = self._boot("logs-live", XHCI_DEVICE)
        rows = verify_xhci_section(text)
        self.assertTrue(rows)

    def test_absent(self):
        text = self._boot("logs-absent")
        rows = verify_absent_section(text)
        self.assertTrue(rows)

    def test_msi_xhci_separation(self):
        from msi_output import verify_msi_section
        if not hasattr(type(self), "combined_image"):
            combined_dir = self.work / "combined"
            if combined_dir.exists():
                shutil.rmtree(combined_dir)
            build_image(ROOT, combined_dir, msi_test=True, xhci_test=True)
            type(self).combined_image = combined_dir / "rynoros.img"
        logs = self.work / "logs-combined"
        if logs.exists():
            shutil.rmtree(logs)
        try:
            boot_image(type(self).combined_image, logs, timeout=TIMEOUT,
                       extra_args=list(XHCI_DEVICE))
        except RuntimeError:
            pass
        text = logs.joinpath("serial.log").read_text(
            encoding="ascii", errors="replace")
        verify_msi_section(text, expected_devices=6)
        verify_xhci_section(text)


class XhciMutantCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "build" / "xhci-mutants"
        cls.work.mkdir(parents=True, exist_ok=True)

    def _boot_mutant(self, name, pairs):
        tmp, root = _mutate_copy(pairs)
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img", xhci_test=True)
        logs = self.work / name
        if logs.exists():
            shutil.rmtree(logs)
        with self.assertRaises(RuntimeError):
            boot_image(root / "build" / "img" / "rynoros.img", logs,
                       timeout=TIMEOUT, extra_args=list(XHCI_DEVICE))
        serial = logs.joinpath("serial.log").read_text(
            encoding="ascii", errors="replace")
        self.assertIn("[XHCI] failure=", serial)
        self.assertNotIn("[XHCI] xhci verified", serial)
        with self.assertRaises(XHCIError):
            verify_xhci_section(serial)
        return serial

    def test_mutant_ccs_init(self):
        serial = self._boot_mutant(
            "logs-m-ccs", [("h->evt.ccs = 1;", "h->evt.ccs = 0;")])
        self.assertIn("[XHCI] failure=s-wait", serial)

    def test_mutant_erst_size(self):
        serial = self._boot_mutant(
            "logs-m-erst",
            [("erst[1] = (cpu_u64)XHCI_EVT_TRBS;", "erst[1] = 31;")])
        self.assertIn("[XHCI] failure=s-wait", serial)

    def test_mutant_rcs_drop(self):
        # QEMU ignores CRCR.RCS on fetch (proven by a double-fault
        # mutant that stayed green); the driver enforces RCS itself
        # via CRCR readback, which trips in the rollback matrix here.
        serial = self._boot_mutant("logs-m-rcs", [
            ("h->cmd.dma.bus | XHCI_CRCR_RCS) != XHCI_OK)",
             "h->cmd.dma.bus) != XHCI_OK)"),
        ])
        self.assertIn("[XHCI] failure=rb-refused", serial)

    def test_mutant_ie_drop(self):
        serial = self._boot_mutant(
            "logs-m-ie",
            [("xh_rt_w(h, ib + 0x00, XHCI_IMAN_IE)",
              "xh_rt_w(h, ib + 0x00, 0)")])
        self.assertIn("[XHCI] failure=s-wait", serial)

    def test_mutant_doorbell_target(self):
        serial = self._boot_mutant(
            "logs-m-db", [("xh_db_w(h, 0, 0)", "xh_db_w(h, 0, 1)")])
        self.assertIn("[XHCI] failure=", serial)
