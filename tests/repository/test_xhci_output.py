"""Repository tests for the xHCI-A1 host validator (xhci_output)."""

import unittest

from xhci_output import (ROLLBACK_STAGES, XHCIError, verify_absent_section,
                         verify_xhci_section, xwork_expected)

ACC_GOLDEN = (0xc0de000002001000, 0xc0de000003fff001,
              0xa0de000e904c8802, 0xa0de000175780003,
              0x90de45c790f42804)

FIXED_HEAD = """\
[XHCI] synth ok cases=43
"""

FIXED_BRINGUP = """\
[XHCI] found bdf=0:3.0 vendor=1033 device=194 class=c.3.30 rev=3
[XHCI] bar ord=0 base=febf0000 size=4000 mem64=1
[XHCI] caps caplen=40 hciver=100 slots=64 intrs=16 ports=8 spads=0 csz=0 ac64=1 xecp=8 dboff=2000 rtsoff=1000 pagesize=1
[XHCI] xcap id=2 off=20 next=4
[XHCI] xcap id=2 off=30 next=0
[XHCI] legacy absent
[XHCI] halted usbsts=1
[XHCI] reset ok cnr=0 usbcmd=0
[XHCI] dmaa dcbba=122000 cmd=126000 evt=127000 erst=128000 spads=0 maxbus=ANY
[XHCI] slots maxen=8 csz=0
[XHCI] ring cmd pcs=1 enq=0 wraps=0
[XHCI] ring evt ccs=1 deq=0 wraps=0
[XHCI] erst sz=1 ba=128000 erdp=127000
[XHCI] intr ie=1 imod=0 erstsz=1
[XHCI] cmd saved=107 now=107
[XHCI] msix vec=48 entry=0 ok
[XHCI] started usbsts=0
"""

FIXED_TAIL = """\
[XHCI] erdp deq=127000 wraps=2
[XHCI] intx silent=1
[XHCI] irq vec=48 count=65 isr=1 user=1 rip=400066
[XHCI] cpl3 yields=8 snap0=4096 snap1=5120 final=8192 acc=c0de000002001000,c0de000003fff001,a0de000e904c8802,a0de000175780003,90de45c790f42804 regs=ok cs=23
[XHCI] teardown ok cmd_restored=1 dma_free=1 vec_free=1
[XHCI] noop token=0 trb=12e000
[XHCI] db rung=0
[XHCI] noop done token=0 count=1
[XHCI] wrap cmd=2 evt=2 cmds=66 cmpls=66
[XHCI] cost alloc0=20000 alloc1=20000
[XHCI] live ok devices=1
[XHCI] xhci verified
"""

ABSENT = """\
[XHCI] synth ok cases=43
[XHCI] absent
[XHCI] cost alloc0=20000 alloc1=20000
[XHCI] live ok devices=0
[XHCI] xhci verified
"""


def build_series(cmd_base=0x126000, vec=48):
    lines = []
    enq, pcs = 0, 1
    deq, ccs = 0, 1
    for i in range(1, 65):
        trb = cmd_base + enq * 16
        lines.append("[XHCI] noop token=0 trb=%x" % trb)
        lines.append("[XHCI] db rung=0")
        lines.append("[XHCI] irq vec=%d count=%d isr=1 user=0 rip=811a28"
                     % (vec, i))
        lines.append("[XHCI] cmpl token=0 ptr=%x cc=1 slot=0 type=33 "
                     "cycle=%d ok" % (trb, ccs))
        lines.append("[XHCI] noop done token=0 count=%d" % i)
        enq += 1
        if enq == 31:
            enq, pcs = 0, pcs ^ 1
        deq += 1
        if deq == 32:
            deq, ccs = 0, ccs ^ 1
    return "\n".join(lines) + "\n"


def build_golden():
    out = [FIXED_HEAD]
    for stage in ROLLBACK_STAGES:
        out.append("[XHCI] rollback stage=%s ok leaks=0\n" % stage)
    out.append(FIXED_BRINGUP)
    out.append(build_series())
    out.append(FIXED_TAIL)
    return "".join(out)


GOLDEN = build_golden()


class AccCase(unittest.TestCase):
    def test_xwork_golden(self):
        self.assertEqual(xwork_expected(), list(ACC_GOLDEN))


class GoldenCase(unittest.TestCase):
    def test_valid_live(self):
        rows = verify_xhci_section(GOLDEN)
        self.assertEqual(len(rows), 364)

    def test_valid_absent(self):
        rows = verify_absent_section(ABSENT)
        self.assertEqual(len(rows), 5)

    def test_live_rejected_by_absent(self):
        with self.assertRaises(XHCIError):
            verify_absent_section(GOLDEN)

    def test_absent_rejected_by_live(self):
        with self.assertRaises(XHCIError):
            verify_xhci_section(ABSENT)


class TamperCase(unittest.TestCase):
    def _bad(self, text):
        with self.assertRaises(XHCIError):
            verify_xhci_section(text)

    def test_missing_terminator(self):
        self._bad(GOLDEN.replace("[XHCI] xhci verified\n", ""))

    def test_trailing_row(self):
        self._bad(GOLDEN + "[XHCI] noop done token=0 count=2\n")

    def test_guest_failure(self):
        self._bad(GOLDEN + "[XHCI] failure=s-tokend\n")

    def test_other_tag_failure(self):
        self._bad(GOLDEN.replace(
            "[XHCI] xhci verified\n",
            "[SCHED] failure=irq_frame\n[XHCI] xhci verified\n"))

    def test_synth_cases(self):
        self._bad(GOLDEN.replace("synth ok cases=43", "synth ok cases=42"))

    def test_rollback_missing(self):
        self._bad(GOLDEN.replace(
            "[XHCI] rollback stage=MSIX ok leaks=0\n", ""))

    def test_rollback_order(self):
        bad = GOLDEN.replace("[XHCI] rollback stage=BAR ok leaks=0\n", "")
        bad = bad.replace("[XHCI] found",
                          "[XHCI] rollback stage=BAR ok leaks=0\n[XHCI] found")
        self._bad(bad)

    def test_wrong_class(self):
        self._bad(GOLDEN.replace("class=c.3.30", "class=c.3.20"))

    def test_bar_too_small(self):
        self._bad(GOLDEN.replace("size=4000", "size=1000"))

    def test_pagesize_bit_clear(self):
        self._bad(GOLDEN.replace("rtsoff=1000 pagesize=1",
                                 "rtsoff=1000 pagesize=0"))

    def test_cmd_trb_skew(self):
        self._bad(GOLDEN.replace("[XHCI] noop token=0 trb=126010",
                                 "[XHCI] noop token=0 trb=126020", 1))

    def test_link_slot_submit(self):
        self._bad(GOLDEN.replace("[XHCI] noop token=0 trb=126000",
                                 "[XHCI] noop token=0 trb=1261f0", 1))

    def test_cmpl_cycle_flip(self):
        self._bad(GOLDEN.replace("type=33 cycle=1 ok",
                                 "type=33 cycle=0 ok", 1))

    def test_cmpl_ptr_mismatch(self):
        self._bad(GOLDEN.replace(
            "[XHCI] cmpl token=0 ptr=126000 cc=1",
            "[XHCI] cmpl token=0 ptr=126010 cc=1", 1))

    def test_cmpl_cc_error(self):
        self._bad(GOLDEN.replace("ptr=126000 cc=1 slot",
                                 "ptr=126000 cc=13 slot", 1))

    def test_irq_count_skip(self):
        self._bad(GOLDEN.replace("vec=48 count=2 isr",
                                 "vec=48 count=3 isr", 1))

    def test_irq_isr_clear(self):
        self._bad(GOLDEN.replace("vec=48 count=1 isr=1",
                                 "vec=48 count=1 isr=0", 1))

    def test_irq_wrong_vec(self):
        self._bad(GOLDEN.replace("vec=48 count=1 isr",
                                 "vec=49 count=1 isr", 1))

    def test_msix_vec_pool(self):
        self._bad(GOLDEN.replace("msix vec=48", "msix vec=47"))

    def test_erdp_wraps(self):
        self._bad(GOLDEN.replace("erdp deq=127000 wraps=2",
                                 "erdp deq=127000 wraps=1"))

    def test_intx_noisy(self):
        self._bad(GOLDEN.replace("intx silent=1", "intx silent=0"))

    def test_cpl3_kernel_frame(self):
        self._bad(GOLDEN.replace("count=65 isr=1 user=1",
                                 "count=65 isr=1 user=0"))

    def test_cpl3_rip_outside(self):
        self._bad(GOLDEN.replace("user=1 rip=400066",
                                 "user=1 rip=500000"))

    def test_cpl3_snap(self):
        self._bad(GOLDEN.replace("snap0=4096", "snap0=4097"))

    def test_cpl3_acc_flip(self):
        self._bad(GOLDEN.replace("90de45c790f42804", "90de45c790f42805"))

    def test_wrap_cmd(self):
        self._bad(GOLDEN.replace("wrap cmd=2 evt=2",
                                 "wrap cmd=1 evt=2"))

    def test_wrap_cmds(self):
        self._bad(GOLDEN.replace("cmds=66 cmpls=66", "cmds=65 cmpls=66"))

    def test_cost_leak(self):
        self._bad(GOLDEN.replace("alloc0=20000 alloc1=20000",
                                 "alloc0=20000 alloc1=21000"))

    def test_missing_msix(self):
        self._bad(GOLDEN.replace("[XHCI] msix vec=48 entry=0 ok\n", ""))
