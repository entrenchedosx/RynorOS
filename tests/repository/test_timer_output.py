"""Synthetic parser fixtures only, never presented as emulator execution."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from timer_output import TIMER_OUTPUT, validate_boot_output
from test_exception_output import parser_fixture


class TimerOutputTests(unittest.TestCase):
    def test_complete_transcript(self):
        self.assertEqual(validate_boot_output(parser_fixture() + TIMER_OUTPUT), [])

    def test_every_timer_line_required(self):
        for line in TIMER_OUTPUT.splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_boot_output(parser_fixture() + TIMER_OUTPUT.replace(line, b"")))

    def test_bad_ticks_order_duplicates_and_trailing_data(self):
        for timer in (TIMER_OUTPUT.replace(b"tick=2", b"tick=4"),
                      TIMER_OUTPUT.replace(b"tick=1", b"tick=2"),
                      TIMER_OUTPUT + b"[TIMER] tick=4\r\n", TIMER_OUTPUT + b"\xff",
                      TIMER_OUTPUT.replace(b"divisor=11932", b"divisor=11931")):
            self.assertTrue(validate_boot_output(parser_fixture() + timer))

    def test_timer_success_cannot_hide_cpu_failure(self):
        self.assertTrue(validate_boot_output(TIMER_OUTPUT))
        self.assertTrue(validate_boot_output(parser_fixture().replace(b"cs=0x0000000000000008",
                                                               b"cs=0x0000000000000018") + TIMER_OUTPUT))
