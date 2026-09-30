"""USB-A1 integration tests: live EP0 descriptor proof, absent and
no-device paths, XHCI/USB event separation, the no-second-SET_ADDRESS
pin, the CPL3 preemption regression, and P/S/T/D/C source mutants
(each must fail loud, never green)."""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image  # noqa: E402
from qemu import boot_image  # noqa: E402
from usb_output import (USBError, verify_absent_section,  # noqa: E402
                        verify_nodevice_section, verify_usb_section)

USB_DEVICE = ["-device", "nec-usb-xhci", "-device", "usb-mouse"]
XHCI_DEVICE = ["-device", "nec-usb-xhci"]
TIMEOUT = 60

FIXTURE_DIRS = ("kernel", "boot", "tools", "assets/branding")
FIXTURE_FILES = ("project.json",)


def _mutate_copy(pairs):
    tmp = tempfile.TemporaryDirectory(prefix="usb-mut-", dir=ROOT / "build")
    root = Path(tmp.name)
    for d in FIXTURE_DIRS:
        (root / d).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / d, root / d)
    for f in FIXTURE_FILES:
        src = ROOT / f
        if src.is_file():
            shutil.copyfile(src, root / f)
    path = root / "kernel/drivers/xhci-usb.c"
    contents = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents, encoding="utf-8")
    return tmp, root


class UsbLiveCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "build" / "usb-tests"
        cls.image_dir = cls.work / "image"
        if cls.image_dir.exists():
            shutil.rmtree(cls.image_dir)
        build_image(ROOT, cls.image_dir, usb_test=True)
        cls.image = cls.image_dir / "rynoros.img"

    def _boot(self, name, extra_args=()):
        logs = self.work / name
        if logs.exists():
            shutil.rmtree(logs)
        try:
            boot_image(self.image, logs, timeout=TIMEOUT,
                       extra_args=list(extra_args))
        except RuntimeError:
            # The USB section extends past the harness's expected
            # terminator (same as MSI/XHCI); the completed serial
            # still lands in serial.log.
            pass
        return logs.joinpath("serial.log").read_text(
            encoding="ascii", errors="replace")

    def test_live_proof(self):
        text = self._boot("logs-live", USB_DEVICE)
        rows = verify_usb_section(text)
        self.assertTrue(rows)
        type(self).live_text = text

    def test_event_separation(self):
        # One transcript carries all three event classes: [XHCI]
        # command completions, [USB] transfer events, [USB]
        # port-change. Both validators must pass on it.
        from xhci_output import verify_xhci_section
        text = getattr(type(self), "live_text", None)
        if text is None:
            text = self._boot("logs-live", USB_DEVICE)
        verify_xhci_section(text)
        verify_usb_section(text)
        self.assertIn("[USB] portev port=", text)
        self.assertIn("[USB] xferdone slot=", text)

    def test_cpl3_preemption_regression(self):
        # Every USB boot still preempts CPL3 workers: the driver
        # never disables the timer path it shares the IRQ with.
        text = getattr(type(self), "live_text", None)
        if text is None:
            text = self._boot("logs-live", USB_DEVICE)
        workers = re.findall(r"\[SCHED\] worker=(\d+) preemptions=(\d+)",
                             text)
        self.assertTrue(workers)
        for _, pre in workers:
            self.assertGreater(int(pre), 0)

    def test_absent(self):
        text = self._boot("logs-absent")
        rows = verify_absent_section(text)
        self.assertTrue(rows)

    def test_no_device(self):
        text = self._boot("logs-nodev", XHCI_DEVICE)
        rows = verify_nodevice_section(text)
        self.assertTrue(rows)

    def test_no_second_set_address_pin(self):
        # Static pin: USB_REQ_SET_ADDRESS is defined but never
        # emitted; the only Address Device call site passes BSR=0.
        prod = (ROOT / "kernel/drivers/xhci-usb.c").read_text(
            encoding="utf-8")
        for line in prod.splitlines():
            if "SET_ADDRESS" in line:
                self.assertTrue(
                    "itself (BSR=0)" in line or "never issue" in line,
                    line)
        setup = (ROOT / "kernel/drivers/usb.c").read_text(
            encoding="utf-8")
        self.assertNotIn("SET_ADDRESS", setup)
        self.assertIn("USB_REQ_GET_DESCRIPTOR", setup)
        calls = re.findall(r"xusb_address_device\(dev, (\d+)\)", prod)
        self.assertEqual(calls, ["0"])


class UsbMutantCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "build" / "usb-mutants"
        cls.work.mkdir(parents=True, exist_ok=True)

    def _boot_mutant(self, name, pairs):
        tmp, root = _mutate_copy(pairs)
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img", usb_test=True)
        logs = self.work / name
        if logs.exists():
            shutil.rmtree(logs)
        with self.assertRaises(RuntimeError):
            boot_image(root / "build" / "img" / "rynoros.img", logs,
                       timeout=TIMEOUT, extra_args=list(USB_DEVICE))
        serial = logs.joinpath("serial.log").read_text(
            encoding="ascii", errors="replace")
        self.assertIn("[USB] failure=", serial)
        self.assertNotIn("[USB] usb verified", serial)
        with self.assertRaises(USBError):
            verify_usb_section(serial)
        return serial

    def test_mutant_slot_ctx_dword(self):
        # Speed/entries back into dword 1 (the A1 bring-up bug):
        # the synth context matrix guards the layout before any
        # live code runs, so the mutant dies at u-ctx-s0.
        serial = self._boot_mutant(
            "logs-m-sctx",
            [("s[0] = ((cpu_u32)speed_id", "s[1] = ((cpu_u32)speed_id")])
        self.assertIn("[USB] failure=u-ctx-s0", serial)

    def test_mutant_bsr_set(self):
        # BSR=1: the xHC skips SET_ADDRESS, so the output
        # validation (Addressed + nonzero address) must refuse.
        serial = self._boot_mutant(
            "logs-m-bsr",
            [("xusb_address_device(dev, 0)", "xusb_address_device(dev, 1)")])
        self.assertIn("[USB] failure=u-enum", serial)

    def test_mutant_ep0_pcs(self):
        # EP0 ring PCS 0: the xHC never fetches; the transfer
        # wait must time out instead of hanging the boot.
        serial = self._boot_mutant(
            "logs-m-pcs", [("dev->ep0.pcs = 1;", "dev->ep0.pcs = 0;")])
        self.assertIn("[USB] failure=u-enum", serial)

    def test_mutant_doorbell_target(self):
        # Ringing doorbell 0 for an EP0 transfer targets the
        # command ring; the transfer must time out, never run.
        serial = self._boot_mutant(
            "logs-m-db",
            [("xhci_doorbell(h, dev->slot, XHCI_DCI_EP0)",
              "xhci_doorbell(h, 0, XHCI_DCI_EP0)")])
        self.assertIn("[USB] failure=u-enum", serial)

    def test_mutant_desc_length(self):
        # Full-descriptor read shortened to 8 bytes: the parser
        # must refuse the truncated image (zero VID/CFGS), never
        # bless a short read as a complete descriptor.
        # (A flipped-Setup-TRT mutant stayed green: QEMU ignores
        # the TRT mismatch, same class of emulator gap as the
        # xHCI-A1 RCS precedent. Direction is proven by the
        # descriptor payload, not by the emulator checking TRT.)
        serial = self._boot_mutant(
            "logs-m-len", [("USB_DT_DEVICE_SIZE);", "8);")])
        self.assertIn("[USB] failure=u-enum", serial)
