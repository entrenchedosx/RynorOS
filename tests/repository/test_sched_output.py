"""Synthetic parser fixtures: not evidence of CPU execution."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from sched_output import SCHED_END, parse_sched_output, validate_sched_output, SCHED_GOOD


def fixture(preemptions=60, runs=1200000):
    return SCHED_END + f"[TEST] preemptions={preemptions} runs={runs}\r\n".encode()


class SchedOutputTests(unittest.TestCase):
    def test_valid_parser_fixture(self):
        self.assertEqual(validate_sched_output(fixture()), [])
        parsed = parse_sched_output(fixture())
        self.assertEqual(parsed["preemptions"], 60)
        self.assertEqual(parsed["runs"], 1200000)
        self.assertEqual(validate_sched_output(SCHED_GOOD), [])

    def test_every_line_required(self):
        for line in fixture().splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_sched_output(fixture().replace(line, b"", 1)))

    def test_real_switch_quantities_required(self):
        for kwargs in ({"preemptions": 0}, {"preemptions": -1},
                       {"runs": 0}, {"runs": 3}):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(validate_sched_output(fixture(**kwargs)))

    def test_no_preemption_cannot_claim_scheduler_success(self):
        self.assertTrue(validate_sched_output(b"[TEST] scheduler self-test passed\r\n"))

    def test_unbounded_duplicate_and_bad_encoding(self):
        for output in (fixture() + fixture(), fixture() + b"\xff", fixture() * 100,
                       fixture().replace(b"preemptions", b"PREEMPTIONS"),
                       fixture().replace(b"runs=", b"Runs="), b"\x00" + SCHED_END):
            self.assertTrue(validate_sched_output(output))
