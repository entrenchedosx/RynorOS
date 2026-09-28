"""INT-A1 ACPI/APIC/IRQ evidence plus scoped temporary implementation mutations."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from apic_output import APIC_START, APIC_VERIFIED, parse_apic_output
from kbd_output import KEYS, validate_keyboard_trace
from boot_output import POST_IRQ


class ApicTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual((state["cleanup"], state["returncode"]), ("monitor-quit", 0))

    def test_apic_backend_timer_and_keyboard(self):
        destination = ROOT / "build/apic-tests/normal"
        build_image(ROOT, destination)
        logs = destination / "logs"
        try:
            output = boot_image(destination / "rynoros.img", logs)
        finally:
            self.cleanup(logs)
        self.assertIn(POST_IRQ, output)
        self.assertLess(output.index(POST_IRQ), output.index(APIC_START))
        section = output[output.index(APIC_START):output.index(APIC_VERIFIED) + len(APIC_VERIFIED)]
        state = parse_apic_output(section)
        self.assertEqual(state["imcr"], 0)
        self.assertEqual(state["kbd_base"], 17)
        self.assertEqual(state["timer_counts"][33], 18)
        self.assertGreaterEqual(state["timer_counts"][32], 11)
        self.assertEqual((state["frames"], state["tables"]), (1, 1))
        validate_keyboard_trace((logs / "guest-errors.log").read_text(), KEYS)

    def mutant(self, name, source, old, new, witness, absent=(), timeout=12):
        """One-line scoped mutant. witness: marker(s) that must ALL appear
        (serial or error text); absent: markers that must not appear."""
        with tempfile.TemporaryDirectory(prefix="apic-fault-", dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for d in REQUIRED_DIRECTORIES:
                (root / d).mkdir(parents=True, exist_ok=True)
            for f in REQUIRED_FILES:
                shutil.copyfile(ROOT / f, root / f)
            path = root / source
            contents = path.read_text()
            self.assertEqual(contents.count(old), 1)
            path.write_text(contents.replace(old, new))
            build_image(root)
            logs = ROOT / "build/apic-tests" / name
            try:
                with self.assertRaises(RuntimeError) as error:
                    boot_image(root / "build/rynoros.img", logs, timeout=timeout)
            finally:
                self.cleanup(logs)
            output = (logs / "serial.log").read_bytes()
            diagnostic = str(error.exception) + output.decode("ascii")
            reasons = witness if isinstance(witness, tuple) else (witness,)
            for reason in reasons:
                self.assertIn(reason, diagnostic, diagnostic[-2000:])
            for marker in (absent if isinstance(absent, tuple) else (absent,)):
                self.assertNotIn(marker, output.decode("ascii"))

    # --- ACPI discovery mutants: firmware-shape breaks fall back to PIC
    # (completed boot, backend=pic), parser-check breaks halt the
    # synthetic suite with the case name.
    def test_mutant_rsdp_scan_signature(self):
        self.mutant("a1-rsdp-sig", "kernel/acpi/acpi.c",
                    'if (!sig_eq(va, "RSD PTR ", 8)) {',
                    'if (!sig_eq(va, "RSD PT! ", 8)) {',
                    ("[IRQ] backend=pic", "rsdp_not_found",
                     "[RT] no image, skipped"),
                    ("[IRQ] backend=apic", "[IRQ] timer ticks="),
                    timeout=8)

    def test_mutant_rsdt_signature(self):
        # The valid synthetic RSDT trips first: the shared header check
        # rejects good firmware before live discovery runs.
        self.mutant("a2-rsdt-sig", "kernel/acpi/acpi.c",
                    'enum acpi_result r = check_header(bytes, length, xsdt ? "XSDT" : "RSDT", &len);',
                    'enum acpi_result r = check_header(bytes, length, xsdt ? "XSDT" : "RSDX", &len);',
                    "[ACPI] failure=rsdt_ok", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_madt_ok_inverted(self):
        # Inverting the MADT header verdict breaks the valid synthetic
        # MADT (and would break live discovery the same way).
        self.mutant("a3-madt-ok", "kernel/acpi/acpi.c",
                    'enum acpi_result r = check_header(bytes, length, "APIC", &len);\n'
                    "    if (r != ACPI_OK) return r;",
                    'enum acpi_result r = check_header(bytes, length, "APIC", &len);\n'
                    "    if (r == ACPI_OK) return ACPI_CHECKSUM;",
                    "[ACPI] failure=madt_ok", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_madt_type0_min_length(self):
        self.mutant("a4-minlen", "kernel/acpi/acpi.c",
                    "if (rl < 8) return ACPI_LENGTH;", "if (rl < 7) return ACPI_LENGTH;",
                    "[ACPI] failure=madt_short_final", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_rsdp_v2_length(self):
        self.mutant("a5-rsdp-len", "kernel/acpi/acpi.c",
                    "if (len < 36 || len > 4096 || (cpu_u64)len > length) return ACPI_LENGTH;",
                    "if (len < 16 || len > 4096 || (cpu_u64)len > length) return ACPI_LENGTH;",
                    "[ACPI] failure=rsdp_len_small", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_xsdt_truncation(self):
        self.mutant("a6-xsdt-wide", "kernel/acpi/acpi.c",
                    "*phys = xsdt ? rd64(bytes + at) : rd32(bytes + at);",
                    "*phys = xsdt ? (cpu_u64)rd32(bytes + at) : rd32(bytes + at);",
                    "[ACPI] failure=xsdt_wide", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_iso_first_wins(self):
        # The duplicate detector compares the wrong source, so the second
        # override for an IRQ overwrites the first instead of counting.
        self.mutant("a7-iso-wins", "kernel/acpi/acpi.c",
                    "if (iso_seen(out, bytes[off + 2], bytes[off + 3])) {",
                    "if (iso_seen(out, bytes[off + 2], (cpu_u8)(bytes[off + 3] + 1))) {",
                    "[ACPI] failure=madt_dup_iso", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_lapic_override_truncation(self):
        self.mutant("a8-override-wide", "kernel/acpi/acpi.c",
                    "out->lapic_base = rd64(bytes + off + 4);",
                    "out->lapic_base = rd32(bytes + off + 4);",
                    "[ACPI] failure=madt_override", APIC_VERIFIED.decode("ascii").strip())

    # --- IRQ/APIC mutants: delivery, proof, and teardown breaks.
    def test_mutant_no_lapic_eoi(self):
        self.mutant("i1-no-eoi", "kernel/interrupts/apic.c",
                    "void apic_lapic_eoi(void) { lapic_write(LAPIC_EOI, 0); }",
                    "void apic_lapic_eoi(void) { }",
                    "[IRQ] backend=apic", "[IRQ] timer ticks=")

    def test_mutant_isr_proof_inverted(self):
        self.mutant("i2-isr-inverted", "kernel/interrupts/irq.c",
                    "if (!apic_lapic_isr_set((unsigned int)vector) || !cpu_interrupts_disabled() ||",
                    "if (apic_lapic_isr_set((unsigned int)vector) || !cpu_interrupts_disabled() ||",
                    "[IRQ] backend=apic", "[IRQ] timer ticks=")

    def test_mutant_double_claim_allowed(self):
        self.mutant("i3-double-claim", "kernel/interrupts/apic.c",
                    "if (vector_state[vector] != VEC_RESERVED) return APIC_STATE;",
                    "(void)vector;",
                    "[APIC] failure=vec_claim_busy", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_pic_left_live(self):
        # Unmasking both PICs at activation must trip the readback check:
        # a live PIC alongside the IOAPIC would double-deliver.
        self.mutant("i4-pic-live", "kernel/interrupts/apic.c",
                    "io_out8(0x21, 0xff);\n    io_out8(0xa1, 0xff);",
                    "io_out8(0x21, 0x00);\n    io_out8(0xa1, 0x00);",
                    ("[IRQ] backend=pic", "pic_quiesce",
                     "[RT] no image, skipped"),
                    ("[IRQ] backend=apic", "[IRQ] timer ticks="), timeout=8)

    def test_mutant_iso_ignored(self):
        # The shared override lookup breaks the synthetic conformance
        # matrix before live routing runs (live: IRQ0 would land on the
        # wrong GSI and the timer proof would hang).
        self.mutant("i5-iso-ignored", "kernel/interrupts/apic.c",
                    "if (isos[i].bus != 0 || isos[i].source != irq) continue;",
                    "if (isos[i].bus != 0 || isos[i].source == irq) continue;",
                    "[APIC] failure=iso_conforms", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_vector_arg_corrupt(self):
        # The vector argument is load-bearing from the first IRQ: the
        # timer handler rejects the off-by-one vector and no ticks print.
        self.mutant("i6-vector-arg", "kernel/interrupts/irq.c",
                    "route->handler((cpu_u32)vector, route->opaque);",
                    "route->handler((cpu_u32)vector + 1, route->opaque);",
                    "Timer output missing", "[TIMER] tick=1")

    def test_mutant_programmed_never_set(self):
        self.mutant("i7-programmed", "kernel/interrupts/apic.c",
                    "rmask != 1)\n        return APIC_HW;\n    route->programmed = 1;",
                    "rmask != 1)\n        return APIC_HW;\n    route->programmed = 0;",
                    "[APIC] failure=prove_unmask", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_release_always_fails(self):
        self.mutant("i8-release", "kernel/interrupts/apic.c",
                    "if (vector_state[vector] != VEC_IRQ) return APIC_STATE;",
                    "if (vector_state[vector] == VEC_IRQ) return APIC_STATE;",
                    "[APIC] failure=vec_release", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_trigger_polarity_dropped(self):
        self.mutant("i9-trig-pol", "kernel/interrupts/apic.c",
                    "*lo = (vector & 0xffu) | ((cpu_u32)(low & 1) << 13) | ((cpu_u32)(level & 1) << 15) |",
                    "(void)level;\n    (void)low;\n    *lo = (vector & 0xffu) |",
                    "[APIC] failure=redir_low_level", APIC_VERIFIED.decode("ascii").strip())

    def test_mutant_mask_inverted(self):
        self.mutant("i10-mask-inverted", "kernel/interrupts/apic.c",
                    "if (mask)\n        lo |= 1u << 16;\n    else\n        lo &= ~(1u << 16);",
                    "if (mask)\n        lo &= ~(1u << 16);\n    else\n        lo |= 1u << 16;",
                    "[IRQ] backend=apic", "[IRQ] timer ticks=")

    def test_mutant_no_cr3_roundtrip(self):
        self.mutant("i11-no-roundtrip", "kernel/interrupts/apic.c",
                    "if (kroot && now != kroot) write_cr3(kroot);",
                    "if (kroot && now != kroot) (void)kroot;",
                    ("fault_address=0xfffffe800100a1", "page_fault"),
                    "[USER] user verified")

    def test_mutant_spurious_park_removed(self):
        # The guest completes (prints do not halt); the strict section
        # validators reject the polluted transcript instead.
        self.mutant("i12-spurious", "kernel/interrupts/irq.c",
                    "if (!(noise & (cpu_u16)(1u << sirq))) {",
                    "if (noise & (cpu_u16)(1u << sirq)) {",
                    "[IRQ] unexpected vector=")


if __name__ == "__main__":
    unittest.main()
