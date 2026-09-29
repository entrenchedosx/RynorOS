"""Repository tests for the INT-A2 host validator (msi_output)."""

import unittest

from msi_output import MSIError, build_message, verify_msi_section

MINIMAL = """\
[MSI] walk ok cases=18
[MSI] parse ok cases=35
[MSI] msg ok cases=16
[MSI] msix ok cases=16
[MSI] alloc ok cases=20
[MSI] synth ok
[MSI] synthetic ok cases=193
[MSI] lapic base=fee00000
[MSI] walk bdf=0:0.0 msi=0 msix=0 malformed=1
[MSI] edu none
[MSI] xhci none
[MSI] e1000e none
[MSI] testdev none
[MSI] cost alloc0=100000 alloc1=100000
[MSI] live ok devices=1
[MSI] msi verified
"""

LIVE = """\
[MSI] walk ok cases=18
[MSI] parse ok cases=35
[MSI] msg ok cases=16
[MSI] msix ok cases=16
[MSI] alloc ok cases=20
[MSI] synth ok
[MSI] synthetic ok cases=193
[MSI] lapic base=fee00000
[MSI] walk bdf=0:3.0 msi=1@40 msix=0 malformed=0
[MSI] map bdf=0:3.0 entry=0 va=fffffe8010000000 pages=256
[MSI] msi bdf=0:3.0 mmc=0 mme=0 is64=1 mask=0 enabled=0 len=14
[MSI] refuse op=msi bdf=0:3.0 reason=no-busmaster
[MSI] msg vec=48 addr=fee00000 hi=0 data=30 apic=0
[MSI] enable bdf=0:3.0 kind=msi nvec=1 vec=48 ok
[MSI] irq vec=48 count=1 isr=1
[MSI] intx bdf=0:3.0 intxoff=1 silent=1
[MSI] disable handle=0 kind=msi ok
[MSI] cost alloc0=100000 alloc1=104000
[MSI] live ok devices=1
[MSI] msi verified
"""


class BuilderCase(unittest.TestCase):
    def test_goldens(self):
        self.assertEqual(build_message(0, 48), (0xFEE00000, 0, 0x30))
        self.assertEqual(build_message(1, 255), (0xFEE01000, 0, 0xFF))
        self.assertEqual(build_message(255, 32), (0xFEEFF000, 0, 0x20))

    def test_apic_refused(self):
        with self.assertRaises(MSIError):
            build_message(256, 48)
        with self.assertRaises(MSIError):
            build_message(0xFFFFFFFF, 48)

    def test_vector_refused(self):
        with self.assertRaises(MSIError):
            build_message(0, 256)


class SectionCase(unittest.TestCase):
    def test_valid_minimal(self):
        rows = verify_msi_section(MINIMAL, expected_devices=1)
        self.assertTrue(rows)

    def test_valid_live(self):
        rows = verify_msi_section(LIVE, expected_devices=1)
        kinds = [r["kind"] for r in rows]
        self.assertIn("enable", kinds)
        self.assertIn("irq", kinds)

    def test_missing_terminator(self):
        with self.assertRaises(MSIError):
            verify_msi_section(MINIMAL.replace("[MSI] msi verified\n", ""))

    def test_guest_failure(self):
        with self.assertRaises(MSIError):
            verify_msi_section(MINIMAL + "[MSI] failure=e-count detail=x\n")

    def test_tampered_msg(self):
        bad = LIVE.replace("data=30 apic=0", "data=31 apic=0")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_entry_mismatch(self):
        bad = LIVE.replace(
            "[MSI] msi bdf=0:3.0 mmc=0",
            "[MSI] entry bdf=0:3.0 i=0 addr=fee00000 data=99 ctrl=0\n"
            "[MSI] msi bdf=0:3.0 mmc=0")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_irq_no_isr(self):
        bad = LIVE.replace("count=1 isr=1", "count=1 isr=0")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_imbalance(self):
        bad = LIVE.replace("[MSI] disable handle=0 kind=msi ok\n", "")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_unknown_refusal(self):
        bad = LIVE.replace("reason=no-busmaster", "reason=bogus")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_intx_broken(self):
        bad = LIVE.replace("intxoff=1 silent=1", "intxoff=1 silent=0")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_devices_mismatch(self):
        with self.assertRaises(MSIError):
            verify_msi_section(MINIMAL, expected_devices=2)

    def test_cost_missing(self):
        bad = MINIMAL.replace("[MSI] cost alloc0=100000 alloc1=100000\n",
                              "")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_cost_backwards(self):
        bad = MINIMAL.replace("alloc0=100000 alloc1=100000",
                              "alloc0=100000 alloc1=ff000")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)

    def test_synth_count(self):
        bad = MINIMAL.replace("walk ok cases=18", "walk ok cases=17")
        with self.assertRaises(MSIError):
            verify_msi_section(bad)


if __name__ == "__main__":
    unittest.main()
