"""Stage 18a host-side tests: userspace serial validator.

No QEMU here: section splitting, line grammars, and accept/reject logic
are pure host checks against a canonical transcript. Real boots run in
tests/integration/test_userspace.py.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from user_output import parse_serial, split_user_sections, validate, \
    validate_user_section


def _lines(*rows):
    return b"\r\n".join(rows) + b"\r\n"


GOOD = _lines(
    b"[USER] initialized",
    b"[USER] smep=0 smap=0",
    b"[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage18a protected userspace",
    b"[USER] self-test started",
    b"[USER] create slot=0 code_size=14 tables=6",
    b"[USER] map slot=0 kind=code va=0x0000000000400000 pa=0x000000000011f000 perm=rx",
    b"[USER] map slot=0 kind=data va=0x0000000000600000 pa=0x0000000000120000 perm=rw",
    b"[USER] map slot=0 kind=stack va=0x00000000007ff000 pa=0x0000000000121000 perm=rw",
    b"[USER] create slot=1 code_size=14 tables=6",
    b"[USER] admission rejected slots=2",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=14 tables=6",
    b"[USER] destroy slot=1",
    b"[USER] destroy slot=0",
    b"[USER] accounting balanced",
    b"[USER] lifecycle verified",
    b"[USER] accounting balanced",
    b"[USER] accounting balanced",
    b"[USER] accounting balanced",
    b"[USER] oom rollback verified",
    b"[USER] create slot=0 code_size=14 tables=6",
    b"[USER] exit slot=0 code=42",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=57 tables=6",
    b"[USER] yield slot=0 count=1",
    b"[USER] exit slot=0 code=9",
    b"[USER] destroy slot=0",
    b"[USER] accounting balanced",
    b"[USER] gate verified",
    b"[USER] create slot=0 code_size=2 tables=6",
    b"[USER] fault slot=0 vector=6 error=0x0000000000000000 rip=0x0000000000400000 cr2=0x0000000040000000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=10 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000005 rip=0x0000000000400005 cr2=0x0000000000008000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=12 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000004 rip=0x0000000000400007 cr2=0xffffffff80000000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=14 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000007 rip=0x0000000000400005 cr2=0x0000000000400000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=9 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000015 rip=0x0000000000600000 cr2=0x0000000000600000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=3 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000000 rip=0x0000000000400000 cr2=0x0000000000600000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=10 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000004 rip=0x0000000000400005 cr2=0x0000000000000000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=18 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000005 rip=0x000000000040000d cr2=0x0000000000020db0",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=22 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000007 rip=0x000000000040000d cr2=0x000000000004a560",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=17 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000015 rip=0x0000000000020db0 cr2=0x0000000000020db0",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=9 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000015 rip=0x00000000007ff000 cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=5 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000000 rip=0x0000000000400000 cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=8 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000010 rip=0x0000000000400003 cr2=0x0000000000600000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=8 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000040 rip=0x0000000000400003 cr2=0x0000000000600000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=8 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x000000000000001c rip=0x0000000000400004 cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=37 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000008 rip=0x000000000040001c cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=37 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000018 rip=0x000000000040001c cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=8 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000020 rip=0x0000000000400003 cr2=0x0000000000600000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=11 tables=6",
    b"[USER] fault slot=0 vector=0 error=0x0000000000000000 rip=0x0000000000400007 cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=4 tables=6",
    b"[USER] fault slot=0 vector=6 error=0x0000000000000000 rip=0x0000000000400000 cr2=0x0000000000600000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=13 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000000 rip=0x000000000040000a cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=16 tables=6",
    b"[USER] fault slot=0 vector=14 error=0x0000000000000007 rip=0x000000000040000d cr2=0x000000000004a558",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=32 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000008 rip=0x000000000040001c cr2=0x00000000007ff000",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=20 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000008 rip=0x0000000000400010 cr2=0x000000000004a558",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=9 tables=6",
    b"[USER] fault slot=0 vector=13 error=0x0000000000000000 rip=0x0000000000400005 cr2=0x000000000004a558",
    b"[USER] destroy slot=0",
    b"[USER] create slot=0 code_size=9 tables=6",
    b"[USER] fault slot=0 vector=128 error=0x0000000000000099 rip=0x0000000000400007 cr2=0x0000000000000000",
    b"[USER] destroy slot=0",
    b"[USER] accounting balanced",
    b"[USER] faults verified",
    b"[USER] create slot=0 code_size=249 tables=6",
    b"[USER] create slot=1 code_size=249 tables=6",
    b"[USER] exit slot=0 code=7",
    b"[USER] exit slot=1 code=7",
    b"[USER] preempt slot=0 preemptions=13",
    b"[USER] preempt worker preemptions=13",
    b"[USER] cpl3_ticks=26 ticks=26 switches=28",
    b"[USER] gprs stable slot=0 counter=7838442",
    b"[USER] gprs stable slot=1 counter=7623993",
    b"[USER] destroy slot=1",
    b"[USER] destroy slot=0",
    b"[USER] accounting balanced",
    b"[USER] preemption verified",
    b"[TEST] userspace self-test passed",
    b"[USER] user verified",
)


class UserSectionTests(unittest.TestCase):
    def test_01_accepts_genuine_evidence(self):
        self.assertEqual(validate_user_section(GOOD), [])
        self.assertEqual(validate(parse_serial(GOOD)), [])

    def test_02_split_carves_trailing_section(self):
        pre, part = split_user_sections(b"[FS] fs verified\r\n" + GOOD)
        self.assertEqual(part, GOOD)
        self.assertTrue(pre.endswith(b"[FS] fs verified\r\n"))

    def test_03_split_empty_without_section(self):
        pre, part = split_user_sections(b"[FS] fs verified\r\n")
        self.assertEqual(part, b"")

    def test_04_rejects_failure_line(self):
        bad = GOOD + b"[USER] failure=boom\r\n"
        self.assertTrue(validate_user_section(bad))
        self.assertTrue(validate(parse_serial(bad)))

    def test_05_rejects_malformed_line(self):
        bad = GOOD.replace(b"[USER] user verified\r\n", b"[USER] user verif ed\r\n")
        self.assertTrue(validate_user_section(bad))

    def test_06_rejects_wrong_start(self):
        bad = GOOD.replace(b"[USER] initialized\r\n", b"", 1)
        self.assertTrue(validate_user_section(bad))

    def test_07_rejects_missing_verified(self):
        bad = GOOD.replace(b"[USER] user verified\r\n", b"")
        self.assertTrue(validate_user_section(bad))

    def test_08_rejects_exit_code_tamper(self):
        bad = GOOD.replace(b"[USER] exit slot=0 code=42", b"[USER] exit slot=0 code=43")
        self.assertEqual(validate_user_section(bad), [])
        self.assertTrue(validate(parse_serial(bad)))

    def test_09_rejects_fault_error_tamper(self):
        cases = [(b"vector=6 error=0x0000000000000000",
                  b"vector=6 error=0x0000000000000001"),
                 (b"vector=14 error=0x0000000000000005",
                  b"vector=14 error=0x0000000000000006"),
                 (b"vector=14 error=0x0000000000000015",
                  b"vector=14 error=0x0000000000000005"),
                 (b"vector=13 error=0x0000000000000000",
                  b"vector=13 error=0x0000000000000001"),
                 (b"vector=13 error=0x0000000000000010",
                  b"vector=13 error=0x0000000000000011"),
                 (b"vector=13 error=0x000000000000001c",
                  b"vector=13 error=0x000000000000001d"),
                 (b"rip=0x000000000040000d cr2=0x0000000000020db0",
                  b"rip=0x000000000040000d cr2=0x0000000000600000"),
                 (b"rip=0x00000000007ff000 cr2=0x00000000007ff000",
                  b"rip=0x00000000007ff008 cr2=0x00000000007ff000"),
                 (b"vector=128 error=0x0000000000000099",
                  b"vector=128 error=0x0000000000000098")]
        for old, new in cases:
            with self.subTest(old=old):
                bad = GOOD.replace(old, new)
                self.assertTrue(validate(parse_serial(bad)), old)

    def test_10_rejects_fault_cr2_tamper(self):
        bad = GOOD.replace(b"rip=0x0000000000400005 cr2=0x0000000000008000",
                           b"rip=0x0000000000400005 cr2=0x0000000000008001")
        self.assertTrue(validate(parse_serial(bad)))

    def test_11_rejects_tick_count_tamper(self):
        bad = GOOD.replace(b"cpl3_ticks=26 ticks=26 switches=28",
                           b"cpl3_ticks=25 ticks=25 switches=27")
        self.assertTrue(validate(parse_serial(bad)))

    def test_12_rejects_switch_count_tamper(self):
        bad = GOOD.replace(b"cpl3_ticks=26 ticks=26 switches=28",
                           b"cpl3_ticks=26 ticks=26 switches=27")
        self.assertTrue(validate(parse_serial(bad)))

    def test_13_rejects_preempt_tamper(self):
        bad = GOOD.replace(b"[USER] preempt slot=0 preemptions=13",
                           b"[USER] preempt slot=0 preemptions=12")
        self.assertTrue(validate(parse_serial(bad)))
        bad = GOOD.replace(b"[USER] preempt worker preemptions=13",
                           b"[USER] preempt worker preemptions=14")
        self.assertTrue(validate(parse_serial(bad)))

    def test_14_rejects_perm_tamper(self):
        bad = GOOD.replace(b"kind=code va=0x0000000000400000 pa=0x000000000011f000 perm=rx",
                           b"kind=code va=0x0000000000400000 pa=0x000000000011f000 perm=rw")
        self.assertTrue(validate(parse_serial(bad)))

    def test_15_rejects_va_tamper(self):
        bad = GOOD.replace(b"va=0x0000000000400000 pa=",
                           b"va=0x0000000000401000 pa=", 1)
        self.assertTrue(validate(parse_serial(bad)))

    def test_16_rejects_missing_balanced(self):
        bad = GOOD.replace(b"[USER] accounting balanced\r\n", b"", 1)
        self.assertEqual(validate_user_section(bad), [])
        self.assertTrue(validate(parse_serial(bad)))

    def test_17_rejects_missing_marker(self):
        bad = GOOD.replace(b"[USER] gate verified\r\n", b"")
        self.assertEqual(validate_user_section(bad), [])
        self.assertTrue(validate(parse_serial(bad)))

    def test_18_rejects_create_destroy_mismatch(self):
        bad = GOOD.replace(b"[USER] destroy slot=1\r\n", b"", 1)
        self.assertEqual(validate_user_section(bad), [])
        self.assertTrue(validate(parse_serial(bad)))

    def test_19_rejects_zero_counter(self):
        bad = GOOD.replace(b"gprs stable slot=0 counter=7838442",
                           b"gprs stable slot=0 counter=0")
        self.assertTrue(validate(parse_serial(bad)))

    def test_20_rejects_rip_outside_code(self):
        bad = GOOD.replace(b"vector=6 error=0x0000000000000000 rip=0x0000000000400000",
                           b"vector=6 error=0x0000000000000000 rip=0x0000000000800000")
        self.assertTrue(validate(parse_serial(bad)))

    def test_21_rejects_tables_mismatch(self):
        bad = GOOD.replace(b"code_size=14 tables=6", b"code_size=14 tables=7", 1)
        self.assertTrue(validate(parse_serial(bad)))

    def test_22_empty_section_valid(self):
        self.assertEqual(validate_user_section(b""), [])


if __name__ == "__main__":
    unittest.main()
