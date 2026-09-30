"""Repository tests for the USB-A1 host validator (usb_output)."""

import unittest

from usb_output import (ROLLBACK_STAGES, USBError, verify_absent_section,
                        verify_nodevice_section, verify_usb_section)

HEAD = """\
[USB] synth ok cases=101
[USB] proto rev=2.0 ports=5..8 psi=0
[USB] proto rev=3.0 ports=1..4 psi=0
[USB] port 1 proto=1 s=disconn ccs=0 ped=0 pls=5 spd=0 ??
[USB] port 2 proto=1 s=disconn ccs=0 ped=0 pls=5 spd=0 ??
[USB] port 3 proto=1 s=disconn ccs=0 ped=0 pls=5 spd=0 ??
[USB] port 4 proto=1 s=disconn ccs=0 ped=0 pls=5 spd=0 ??
[USB] port 5 proto=0 s=conn ccs=1 ped=0 pls=7 spd=3 HS
[USB] port 6 proto=0 s=disconn ccs=0 ped=0 pls=5 spd=0 ??
[USB] port 7 proto=0 s=disconn ccs=0 ped=0 pls=5 spd=0 ??
[USB] port 8 proto=0 s=disconn ccs=0 ped=0 pls=5 spd=0 ??
"""

TAIL = """\
[USB] slot id=1 port=5 addr=1
[USB] ctx slot=1 dctx=129000 ictx=12a000 ep0ring=12b000 mps=64
[USB] addr slot=1 ctl=1002c00 ok
[USB] out slot=1 st=2 ep0=1 mps=64
[USB] eval slot=1 mps=64 ok
[USB] portev port=5 ok
[USB] desc slot=1 len=18 raw=120100020000004027060100000001020901
[USB] parsed vid=0627 pid=0001 bcd=0200 cls=0 mps0=64 cfgs=1
[USB] xferdone slot=1 ep=1 cc=1 resid=0 ptr=12b080 ok
[USB] xferdone slot=1 ep=1 cc=1 resid=0 ptr=12b020 ok
[USB] xferdone slot=1 ep=1 cc=1 resid=0 ptr=12b050 ok
[USB] xferdone slot=1 ep=1 cc=1 resid=0 ptr=12b080 ok
[USB] xferdone slot=1 ep=1 cc=1 resid=0 ptr=12b020 ok
[USB] wrap ep0=2 reads=5 identical=1
[USB] noopalive ok
[USB] cleanup slot=0 ok
[USB] cost alloc0=20000 alloc1=20000
[USB] live ok devices=1
[USB] usb verified
"""

NODEV_HEAD = HEAD.replace(
    "[USB] port 5 proto=0 s=conn ccs=1 ped=0 pls=7 spd=3 HS\n",
    "[USB] port 5 proto=0 s=disconn ccs=0 ped=0 pls=5 spd=0 ??\n")

NODEV = NODEV_HEAD + """\
[USB] no-device
[USB] cost alloc0=20000 alloc1=20000
[USB] live ok devices=0
[USB] usb verified
"""

ABSENT = """\
[USB] synth ok cases=101
[USB] absent
[USB] cost alloc0=20000 alloc1=20000
[USB] live ok devices=0
[USB] usb verified
"""


def build_golden():
    out = [HEAD]
    for stage in ROLLBACK_STAGES:
        out.append("[USB] rollback stage=%s ok\n" % stage)
    out.append(TAIL)
    return "".join(out)


GOLDEN = build_golden()


class GoldenCase(unittest.TestCase):
    def test_valid_live(self):
        rows = verify_usb_section(GOLDEN)
        self.assertEqual(len(rows), 41)

    def test_valid_nodevice(self):
        rows = verify_nodevice_section(NODEV)
        self.assertEqual(len(rows), 15)

    def test_valid_absent(self):
        rows = verify_absent_section(ABSENT)
        self.assertEqual(len(rows), 5)

    def test_live_rejected_by_absent(self):
        with self.assertRaises(USBError):
            verify_absent_section(GOLDEN)

    def test_live_rejected_by_nodevice(self):
        with self.assertRaises(USBError):
            verify_nodevice_section(GOLDEN)

    def test_absent_rejected_by_live(self):
        with self.assertRaises(USBError):
            verify_usb_section(ABSENT)

    def test_nodevice_rejected_by_live(self):
        with self.assertRaises(USBError):
            verify_usb_section(NODEV)


class TamperCase(unittest.TestCase):
    def _bad(self, text):
        with self.assertRaises(USBError):
            verify_usb_section(text)

    def test_missing_terminator(self):
        self._bad(GOLDEN.replace("[USB] usb verified\n", ""))

    def test_trailing_row(self):
        self._bad(GOLDEN + "[USB] noopalive ok\n")

    def test_guest_failure(self):
        self._bad(GOLDEN + "[USB] failure=u-enum\n")

    def test_other_tag_failure(self):
        self._bad(GOLDEN.replace(
            "[USB] usb verified\n",
            "[XHCI] failure=s-wait\n[USB] usb verified\n"))

    def test_synth_cases(self):
        self._bad(GOLDEN.replace("synth ok cases=101",
                                 "synth ok cases=100"))

    def test_proto_overlap(self):
        self._bad(GOLDEN.replace("ports=1..4", "ports=4..7", 1))

    def test_proto_rev(self):
        self._bad(GOLDEN.replace("rev=2.0", "rev=1.0", 1))

    def test_port_order(self):
        bad = GOLDEN.replace("[USB] port 3 proto=1 s=disconn ccs=0 "
                             "ped=0 pls=5 spd=0 ??\n", "")
        self._bad(bad)

    def test_port_owner(self):
        self._bad(GOLDEN.replace("port 5 proto=0 s=conn",
                                 "port 5 proto=1 s=conn"))

    def test_port_speed_name(self):
        self._bad(GOLDEN.replace("spd=3 HS", "spd=3 FS"))

    def test_disconn_with_link(self):
        self._bad(GOLDEN.replace("port 6 proto=0 s=disconn ccs=0",
                                 "port 6 proto=0 s=disconn ccs=1", 1))

    def test_disconn_pls(self):
        self._bad(GOLDEN.replace("port 6 proto=0 s=disconn ccs=0 ped=0 "
                                 "pls=5", "port 6 proto=0 s=disconn ccs=0 "
                                 "ped=0 pls=0", 1))

    def test_two_connected(self):
        bad = GOLDEN.replace(
            "port 6 proto=0 s=disconn ccs=0 ped=0 pls=5 spd=0 ??",
            "port 6 proto=0 s=conn ccs=1 ped=0 pls=7 spd=3 HS", 1)
        self._bad(bad)

    def test_rollback_missing(self):
        self._bad(GOLDEN.replace(
            "[USB] rollback stage=ADDR ok\n", ""))

    def test_rollback_order(self):
        bad = GOLDEN.replace("[USB] rollback stage=PROTO ok\n", "")
        bad = bad.replace("[USB] slot id=",
                          "[USB] rollback stage=PROTO ok\n[USB] slot id=")
        self._bad(bad)

    def test_slot_wrong_port(self):
        self._bad(GOLDEN.replace("slot id=1 port=5 addr=1",
                                 "slot id=1 port=6 addr=1"))

    def test_slot_zero_addr(self):
        self._bad(GOLDEN.replace("slot id=1 port=5 addr=1",
                                 "slot id=1 port=5 addr=0"))

    def test_ctx_misaligned(self):
        self._bad(GOLDEN.replace("dctx=129000", "dctx=129010"))

    def test_ctx_alias(self):
        self._bad(GOLDEN.replace("ictx=12a000", "ictx=129000"))

    def test_addr_bsr_set(self):
        # BSR flips on: xHC no longer owns SET_ADDRESS.
        self._bad(GOLDEN.replace("ctl=1002c00", "ctl=1002e00"))

    def test_addr_wrong_type(self):
        self._bad(GOLDEN.replace("ctl=1002c00", "ctl=1003000"))

    def test_addr_wrong_slot(self):
        self._bad(GOLDEN.replace("ctl=1002c00", "ctl=2002c00"))

    def test_out_not_addressed(self):
        self._bad(GOLDEN.replace("st=2 ep0=1", "st=1 ep0=1"))

    def test_out_ep0_not_running(self):
        self._bad(GOLDEN.replace("st=2 ep0=1", "st=2 ep0=0"))

    def test_out_mps_skew(self):
        self._bad(GOLDEN.replace("st=2 ep0=1 mps=64",
                                 "st=2 ep0=1 mps=8"))

    def test_portev_wrong_port(self):
        self._bad(GOLDEN.replace("portev port=5", "portev port=6"))

    def test_desc_short(self):
        self._bad(GOLDEN.replace("len=18 raw=1201", "len=8 raw=1201"))

    def test_desc_bad_header(self):
        self._bad(GOLDEN.replace("raw=1201", "raw=1202", 1))

    def test_desc_mps0_skew(self):
        # bMaxPacketSize0 byte (offset 7) 0x40 -> 0x08.
        self._bad(GOLDEN.replace("raw=1201000200000040",
                                 "raw=1201000200000008", 1))

    def test_parsed_vid_skew(self):
        self._bad(GOLDEN.replace("vid=0627", "vid=0628"))

    def test_parsed_mps0_skew(self):
        self._bad(GOLDEN.replace("mps0=64", "mps0=8", 1))

    def test_xfer_cc_error(self):
        self._bad(GOLDEN.replace("ptr=12b080 ok", "ptr=12b080 ok", 1)
                  .replace("cc=1 resid=0 ptr=12b080",
                           "cc=13 resid=0 ptr=12b080", 1))

    def test_xfer_resid(self):
        self._bad(GOLDEN.replace("cc=1 resid=0 ptr=12b080",
                                 "cc=1 resid=4 ptr=12b080", 1))

    def test_xfer_wrong_ep(self):
        self._bad(GOLDEN.replace("slot=1 ep=1 cc=1 resid=0 ptr=12b080",
                                 "slot=1 ep=3 cc=1 resid=0 ptr=12b080", 1))

    def test_xfer_ptr_skew(self):
        self._bad(GOLDEN.replace("ptr=12b080 ok", "ptr=12b090 ok", 1))

    def test_xfer_ptr_off_ring(self):
        self._bad(GOLDEN.replace("ptr=12b080 ok", "ptr=12c080 ok", 1))

    def test_xfer_ptr_unaligned(self):
        self._bad(GOLDEN.replace("ptr=12b080 ok", "ptr=12b081 ok", 1))

    def test_wrap_count(self):
        self._bad(GOLDEN.replace("wrap ep0=2", "wrap ep0=1"))

    def test_wrap_not_identical(self):
        self._bad(GOLDEN.replace("identical=1", "identical=0"))

    def test_cleanup_missing(self):
        self._bad(GOLDEN.replace("[USB] cleanup slot=0 ok\n", ""))

    def test_cost_leak(self):
        self._bad(GOLDEN.replace("alloc0=20000 alloc1=20000",
                                 "alloc0=20000 alloc1=21000"))

    def test_live_devices(self):
        self._bad(GOLDEN.replace("live ok devices=1",
                                 "live ok devices=0"))
