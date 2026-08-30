"""Synthetic parser fixtures only, never presented as emulator execution."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from timer_output import TIMER_OUTPUT
from boot_output import validate_boot_output, POST_IRQ
from test_exception_output import parser_fixture
from test_pmm_output import fixture as pmm_fixture
from test_vm_output import fixture as vm_fixture
from test_heap_output import fixture as heap_fixture


def complete_transcript():
    return parser_fixture() + pmm_fixture() + vm_fixture() + heap_fixture() + TIMER_OUTPUT + POST_IRQ


def base_prefix():
    return parser_fixture() + pmm_fixture() + vm_fixture() + heap_fixture()


class TimerOutputTests(unittest.TestCase):
    def test_complete_transcript(self):
        self.assertEqual(validate_boot_output(complete_transcript()), [])

    def test_every_timer_line_required(self):
        for line in TIMER_OUTPUT.splitlines(keepends=True):
            with self.subTest(line=line):
                timer = TIMER_OUTPUT.replace(line, b"")
                self.assertTrue(validate_boot_output(base_prefix() + timer + POST_IRQ))

    def test_bad_ticks_order_duplicates_and_trailing_data(self):
        for timer in (TIMER_OUTPUT.replace(b"tick=2", b"tick=4"),
                      TIMER_OUTPUT.replace(b"tick=1", b"tick=2"),
                      TIMER_OUTPUT + b"[TIMER] tick=4\r\n", TIMER_OUTPUT + b"\xff",
                      TIMER_OUTPUT.replace(b"divisor=11932", b"divisor=11931")):
            self.assertTrue(validate_boot_output(base_prefix() + timer + POST_IRQ))

    def test_timer_success_cannot_hide_cpu_failure(self):
        self.assertTrue(validate_boot_output(TIMER_OUTPUT))
        self.assertTrue(validate_boot_output(parser_fixture().replace(b"cs=0x0000000000000008",
                                                               b"cs=0x0000000000000018") + TIMER_OUTPUT))
