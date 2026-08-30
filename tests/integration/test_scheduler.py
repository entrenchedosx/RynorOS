"""Real preemptive scheduler proof and independently broken kernel variants."""
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from boot_output import POST_IRQ, SCHED_END
from sched_output import parse_sched_output
from timer_output import TIMER_OUTPUT


class SchedulerTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual(state["cleanup"], "monitor-quit")
        self.assertEqual(state["returncode"], 0)

    def test_real_scheduler_preempts_workers(self):
        destination = ROOT / "build/sched-tests/normal"
        build_image(ROOT, destination)
        output = boot_image(destination / "rynoros.img", destination / "logs")
        self.assertIn(TIMER_OUTPUT, output)
        self.assertIn(SCHED_END, output)
        self.assertIn(b"[TEST] scheduler self-test passed", output)
        stats, sep, post = output.partition(SCHED_END)[2].partition(POST_IRQ)
        self.assertEqual(sep, POST_IRQ)
        self.assertEqual(post, b"")
        parsed = parse_sched_output(SCHED_END + stats)
        self.assertGreaterEqual(parsed["preemptions"], 1)
        self.assertGreaterEqual(parsed["runs"], 4)
        self.cleanup(destination / "logs")

    def broken(self, name, source, old, new, reason):
        with tempfile.TemporaryDirectory(prefix="sched-fault-", dir=ROOT / "build") as tmp:
            fixture = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (fixture / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, fixture / filename)
            path = fixture / source
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(contents.count(old), 1)
            path.write_text(contents.replace(old, new), encoding="utf-8")
            build_image(fixture)
            logs = ROOT / "build/sched-tests" / name
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                boot_image(fixture / "build/rynoros.img", logs, timeout=4)
            output = (logs / "serial.log").read_bytes()
            self.assertIn(reason.encode(), output)
            self.assertNotIn(b"[TEST] scheduler self-test passed", output)
            self.assertNotIn(POST_IRQ, output)
            self.cleanup(logs)

    def test_no_genuine_preemption_cannot_pass(self):
        self.broken("no-preemption", "kernel/core/thread.c",
                    '    require(sched_preemptions > 0, "preempted");',
                    '    require(sched_preemptions > 999999, "preempted");',
                    "[SCHED] failure=preempted")

    def test_pmm_imbalance_after_join_cannot_pass(self):
        self.broken("pmm-leak", "kernel/core/thread.c",
                    '    require(after.allocated_bytes == before.allocated_bytes, "pmm_balanced");',
                    '    require(after.allocated_bytes != before.allocated_bytes, "pmm_balanced");',
                    "[SCHED] failure=pmm_balanced")
