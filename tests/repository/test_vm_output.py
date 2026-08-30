"""Synthetic parser fixtures: not evidence of CPU execution."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from vm_output import parse_vm_output, validate_vm_output


def fixture(free=1015808):
    lines = ["[VM] paging subsystem initialized", "[VM] kernel address space created", "[VM] CR3 loaded",
             "[VM] root=1052672 table_pages=7", "[VM] kernel mappings verified", "[TEST] VM self-test started",
             "[VM] mapping va=1073741824 physical=1081344 offset_physical=1085432",
             "[TEST] VM mapping verified", "[TEST] VM invalid mappings rejected"]
    def fault(marker, code, rip):
        lines.extend(["[TEST] " + marker, "[VM] page fault",
            f"[VM] fault_address=0x0000000040000000 error=0x{code:016x} rip=0x{rip:016x}",
            f"[VM] present={code & 1} write={(code >> 1) & 1} user=0 reserved=0 fetch={(code >> 4) & 1} cpl=0",
            "[VM] page fault action=resume_test"])
    fault("triggering read-only page fault", 3, 0x8512)
    fault("triggering non-executable page fault", 17, 0x40000000)
    lines.extend(["[TEST] VM permissions verified", "[TEST] VM unmapping verified"])
    fault("triggering controlled page fault", 0, 0x8507)
    lines.extend("[TEST] " + label for label in ("controlled page fault verified", "page fault diagnostics verified",
        "VM TLB invalidation verified", "VM ranges and high addresses verified", "VM address-space destruction verified",
        "VM real OOM rollback verified"))
    lines.extend([f"[VM] final table_pages=7 allocated_bytes=28672 free_bytes={free}", "[TEST] VM self-test passed"])
    return ("\r\n".join(lines) + "\r\n").encode()


class VmOutputTests(unittest.TestCase):
    def test_valid_parser_fixture(self):
        self.assertEqual(validate_vm_output(fixture()), [])
        self.assertEqual(len(parse_vm_output(fixture())["faults"]), 3)

    def test_every_line_required(self):
        for line in fixture().splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_vm_output(fixture().replace(line, b"", 1)))

    def test_real_fault_fields_required(self):
        for old, new in ((b"error=0x0000000000000003", b"error=0x0000000000000000"),
                         (b"fault_address=0x0000000040000000", b"fault_address=0x0000000040001000"),
                         (b"fetch=1", b"fetch=0"), (b"user=0", b"user=1"), (b"cpl=0", b"cpl=3"),
                         (b"rip=0x0000000000008507", b"rip=0x0000800000000000")):
            with self.subTest(old=old):
                self.assertTrue(validate_vm_output(fixture().replace(old, new)))

    def test_accounting_and_translation(self):
        for old, new in ((b"table_pages=7", b"table_pages=6"), (b"allocated_bytes=28672", b"allocated_bytes=0"),
                         (b"offset_physical=1085432", b"offset_physical=1081344"),
                         (b"root=1052672", b"root=0"), (b"root=1052672", b"root=1052673")):
            self.assertTrue(validate_vm_output(fixture().replace(old, new)))
        pmm = {"free_bytes": 1044480, "regions": [(1052672, 2097152, 1)]}
        self.assertEqual(validate_vm_output(fixture(), pmm), [])
        pmm["regions"] = [(1052672, 2097152, 2)]
        self.assertTrue(validate_vm_output(fixture(), pmm))
        self.assertTrue(validate_vm_output(fixture(123), pmm))

    def test_unbounded_duplicate_and_bad_encoding(self):
        for output in (fixture() * 10, fixture() + b"extra\r\n", fixture() + b"\xff", fixture().replace(b"\r\n", b"\n")):
            self.assertTrue(validate_vm_output(output))
