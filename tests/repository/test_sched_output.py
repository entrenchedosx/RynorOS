"""Synthetic parser fixtures: not evidence of CPU execution."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from sched_output import SCHED_END, parse_sched_output, validate_sched_output, SCHED_GOOD


def fixture(preemptions=48, runs=1200000):
    return SCHED_GOOD.replace(b"[TEST] preemptions=48 runs=1200000",
                             f"[TEST] preemptions={preemptions} runs={runs}".encode())


class SchedOutputTests(unittest.TestCase):
    def test_valid_parser_fixture(self):
        self.assertEqual(validate_sched_output(fixture()), [])
        parsed = parse_sched_output(fixture())
        self.assertEqual(parsed["preemptions"], 48)
        self.assertEqual(parsed["runs"], 1200000)
        self.assertEqual(validate_sched_output(SCHED_GOOD), [])

    def test_every_line_required(self):
        for line in fixture().splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_sched_output(fixture().replace(line, b"", 1)))

    def test_real_switch_quantities_required(self):
        for kwargs in ({"preemptions": 0}, {"preemptions": -1},
                       {"runs": 0}, {"runs": 2}, {"runs": 1 << 64}):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(validate_sched_output(fixture(**kwargs)))

    def test_no_preemption_cannot_claim_scheduler_success(self):
        self.assertTrue(validate_sched_output(b"[TEST] scheduler self-test passed\r\n"))

    def test_unbounded_duplicate_and_bad_encoding(self):
        for output in (fixture() + fixture(), fixture() + b"\xff", fixture() * 100,
                       fixture().replace(b"preemptions", b"PREEMPTIONS"),
                       fixture().replace(b"runs=", b"Runs="), b"\x00" + SCHED_END):
            self.assertTrue(validate_sched_output(output))

    def test_worker_state_and_resource_accounting(self):
        heap = {"allocated": 106496, "free": 937984, "tables": 10}
        self.assertEqual(validate_sched_output(fixture(), heap), [])
        for old, new in ((b"worker=2", b"worker=1"), (b"preemptions=6", b"preemptions=0"),
                         (b"dispatches=6", b"dispatches=99"), (b"irq_rip=35000", b"irq_rip=0"),
                         (b"free_bytes=937984", b"free_bytes=942080")):
            self.assertTrue(validate_sched_output(fixture().replace(old, new), heap))

    def test_missing_completion_and_identity(self):
        for token in (SCHED_END, b"[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage7 kernel execution\r\n"):
            self.assertTrue(validate_sched_output(fixture().replace(token, b"")))
