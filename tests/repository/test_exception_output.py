"""Synthetic parser fixtures ONLY. Actual exception execution is tested in QEMU."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from exception_output import BOOT_PREFIX, GPR_ROWS, validate_exception_output


def parser_fixture() -> bytes:
    # Made-up RIP/RSP inside the documented window; not claimed execution data.
    output = BOOT_PREFIX + (
        b"[CPU] GDT initialized\r\n[CPU] IDT initialized\r\n"
        b"[TEST] triggering controlled exception\r\n"
        b"[EXCEPTION] vector=03 name=breakpoint error_source=synthetic error=0x0000000000000000\r\n"
        b"[STATE] rip=0x0000000000008200 cs=0x0000000000000008 rflags=0x0000000000000402 "
        b"rsp=0x000000000007fff8 ss=0x0000000000000010\r\n"
    )
    value = 0x101
    for row in GPR_ROWS:
        output += b"[GPR]"
        for name in row:
            output += f" {name}=0x{value:016x}".encode("ascii")
            value += 1
        output += b"\r\n"
    return output + b"[EXCEPTION] action=resume\r\n[TEST] exception handling verified\r\n"


class ExceptionOutputTests(unittest.TestCase):
    def test_valid_parser_fixture(self):
        self.assertEqual(validate_exception_output(parser_fixture()), [])

    def test_every_line_is_required(self):
        lines = parser_fixture().splitlines(keepends=True)
        for index in range(len(lines)):
            with self.subTest(line=index):
                self.assertTrue(validate_exception_output(b"".join(lines[:index] + lines[index + 1:])))

    def test_real_state_fields_are_checked(self):
        fixture = parser_fixture()
        for old, new in (
            (b"vector=03", b"vector=06"), (b"name=breakpoint", b"name=debug"),
            (b"error_source=synthetic", b"error_source=cpu"),
            (b"error=0x0000000000000000", b"error=0x0000000000000001"),
            (b"cs=0x0000000000000008", b"cs=0x0000000000000018"),
            (b"rflags=0x0000000000000402", b"rflags=0x0000000000000002"),
            (b"rip=0x0000000000008200", b"rip=0x0000000000000000"),
            (b"rsp=0x000000000007fff8", b"rsp=0x0000000000000000"),
            (b"r15=0x000000000000010f", b"r15=0x0000000000000000"),
            (b"action=resume", b"action=halt"),
        ):
            with self.subTest(field=old):
                self.assertTrue(validate_exception_output(fixture.replace(old, new)))

    def test_duplicate_extra_and_reordered_output_is_rejected(self):
        fixture = parser_fixture()
        self.assertTrue(validate_exception_output(fixture + fixture))
        self.assertTrue(validate_exception_output(fixture + b"unexpected\r\n"))
        self.assertTrue(validate_exception_output(fixture.replace(
            b"[CPU] GDT initialized\r\n[CPU] IDT initialized",
            b"[CPU] IDT initialized\r\n[CPU] GDT initialized")))

    def test_legacy_boot_prefix_is_not_stage2_success(self):
        self.assertTrue(validate_exception_output(BOOT_PREFIX))

    def test_invalid_bytes_and_vector(self):
        self.assertTrue(validate_exception_output(b"\xff"))
        for vector in (True, 2, 32, "3"):
            with self.subTest(vector=vector):
                self.assertTrue(validate_exception_output(parser_fixture(), vector))
