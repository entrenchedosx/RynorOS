"""Synthetic parser fixtures for the INT-A1 section, never hardware evidence."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from apic_output import (APIC_GOOD, APIC_START, APIC_VERIFIED, fixture,
                         extract_apic_section, parse_apic_output,
                         strip_apic_section, validate_apic_output)


class ApicOutputTests(unittest.TestCase):
    def test_valid_fixture(self):
        self.assertEqual(validate_apic_output(APIC_GOOD), [])
        self.assertEqual(validate_apic_output(fixture()), [])
        state = parse_apic_output(APIC_GOOD)
        self.assertEqual(state["imcr"], 0)
        self.assertEqual(state["kbd_base"], 17)
        self.assertEqual(state["timer_counts"], {32: 152, 33: 18})
        self.assertEqual((state["frames"], state["tables"]), (1, 1))

    def test_every_line_required(self):
        for line in APIC_GOOD.splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_apic_output(APIC_GOOD.replace(line, b"", 1)))

    def test_pinned_facts_reject_drift(self):
        for old, new in ((b"synthetic acpi=43 apic=120", b"synthetic acpi=42 apic=120"),
                         (b"synthetic acpi=43 apic=120", b"synthetic acpi=43 apic=119"),
                         (b"rsdp rev=0 xsdt=0 entries=4", b"rsdp rev=2 xsdt=0 entries=4"),
                         (b"rsdp rev=0 xsdt=0 entries=4", b"rsdp rev=0 xsdt=0 entries=5"),
                         (b"lapic=4276092928", b"lapic=4276092929"),
                         (b"isos=5", b"isos=4"),
                         (b"skipped=0 dups=0", b"skipped=1 dups=0"),
                         (b"cpu uid=0 apic=0 flags=1", b"cpu uid=0 apic=1 flags=1"),
                         (b"ioapic id=0 base=4273995776 gsi=0",
                          b"ioapic id=0 base=4273995776 gsi=1"),
                         (b"iso bus=0 irq=0 gsi=2 flags=0", b"iso bus=0 irq=0 gsi=0 flags=0"),
                         (b"lapic id=0 version=20 maxlvt=5", b"lapic id=0 version=21 maxlvt=5"),
                         (b"ioapic idx=0 maxredir=23", b"ioapic idx=0 maxredir=24"),
                         (b"route irq=0 gsi=2 vector=32", b"route irq=0 gsi=0 vector=32"),
                         (b"route irq=1 gsi=1 vector=33", b"route irq=1 gsi=1 vector=34"),
                         (b"trigger=edge polarity=high", b"trigger=level polarity=high"),
                         (b"backend=apic imcr=0", b"backend=apic imcr=2"),
                         (b"timer ticks=11", b"timer ticks=10"),
                         (b"kbd echo byte=238 vector=33", b"kbd echo byte=250 vector=33"),
                         (b"kbd echo byte=238 vector=33", b"kbd echo byte=238 vector=32"),
                         (b"dyn vector=48 gsi=7", b"dyn vector=49 gsi=7"),
                         (b"cost frames=1 tables=1", b"cost frames=2 tables=1")):
            with self.subTest(old=old):
                self.assertTrue(validate_apic_output(APIC_GOOD.replace(old, new)))

    def test_backend_must_be_apic(self):
        bad = APIC_GOOD.replace(b"[IRQ] backend=apic imcr=0 pic_masked=1",
                                b"[IRQ] backend=pic")
        self.assertTrue(validate_apic_output(bad))

    def test_kbd_quiet_is_delta_and_nonvacuous(self):
        noisy = APIC_GOOD.replace(b"[IRQ] kbd base=17 count=17", b"[IRQ] kbd base=17 count=19")
        self.assertTrue(validate_apic_output(noisy))
        # count=18 also breaks the vector census cross-check below; keep it.
        vacuous = APIC_GOOD.replace(b"[IRQ] kbd base=17 count=17", b"[IRQ] kbd base=0 count=0")
        self.assertTrue(validate_apic_output(vacuous))

    def test_vector_census_cross_checks(self):
        # vector=33 must equal kbd base + 1 (the echo delivery).
        bad33 = APIC_GOOD.replace(b"[IRQ] vector=33 count=18", b"[IRQ] vector=33 count=17")
        self.assertTrue(validate_apic_output(bad33))
        # vector=32 must include at least the 11 proof ticks.
        bad32 = APIC_GOOD.replace(b"[IRQ] vector=32 count=152", b"[IRQ] vector=32 count=10")
        self.assertTrue(validate_apic_output(bad32))
        # Census rows are order-insensitive (both must still be present
        # with exact values); every other line is position-pinned.
        swapped = APIC_GOOD.replace(b"[IRQ] vector=32 count=152\r\n[IRQ] vector=33 count=18",
                                    b"[IRQ] vector=33 count=18\r\n[IRQ] vector=32 count=152")
        self.assertEqual(validate_apic_output(swapped), [])

    def test_fallback_shape_rejected(self):
        fallback = (APIC_START + b"[APIC] synthetic acpi=43 apic=120\r\n"
                    b"[APIC] unavailable reason=rsdp_not_found\r\n"
                    b"[IRQ] backend=pic\r\n"
                    b"[APIC] cost frames=0 tables=0\r\n" + APIC_VERIFIED)
        errors = validate_apic_output(fallback)
        self.assertTrue(errors)
        self.assertIn("unavailable", errors[0])

    def test_truncation_and_trailing_data_rejected(self):
        self.assertTrue(validate_apic_output(APIC_GOOD + b"[APIC] extra\r\n"))
        self.assertTrue(validate_apic_output(APIC_GOOD + b"\xff"))
        self.assertTrue(validate_apic_output(APIC_GOOD * 2))
        self.assertTrue(validate_apic_output(APIC_START))
        self.assertTrue(validate_apic_output(b""))

    def test_strip_removes_section(self):
        block = b"head" + APIC_GOOD + b"tail"
        rest, errors = strip_apic_section(block)
        self.assertEqual(errors, [])
        self.assertEqual(rest, b"headtail")
        self.assertIsNone(extract_apic_section(b"no section"))
        _, errors = strip_apic_section(b"no section")
        self.assertTrue(errors)
        _, errors = strip_apic_section(APIC_START + b"partial")
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
