"""Bounded kernel-heap proof and independently broken kernel variants."""
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
from boot_output import HEAP_END, POST_IRQ
from timer_output import TIMER_OUTPUT


class KernelHeapTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual(state["cleanup"], "monitor-quit")
        self.assertEqual(state["returncode"], 0)

    def test_real_bounded_alloc_reuse_and_coalescing(self):
        destination = ROOT / "build/heap-tests/normal"
        manifest = build_image(ROOT, destination)
        self.assertEqual(manifest["payload_sectors"],
                         (manifest["artifacts"]["rynorkernel.bin"]["bytes"] + 511) // 512)
        output = boot_image(destination / "rynoros.img", destination / "logs")
        heap = output.partition(HEAP_END)[0]
        self.assertIn(b"[HEAP] initialize arena=65536 mapped=65536", heap)
        self.assertIn(b"[TEST] HEAP self-test passed", output)
        self.assertTrue(output.endswith(TIMER_OUTPUT + POST_IRQ))
        self.cleanup(destination / "logs")

    def broken(self, name, source, old, new, reason, compile_error=False):
        with tempfile.TemporaryDirectory(prefix="heap-fault-", dir=ROOT / "build") as tmp:
            fixture = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (fixture / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, fixture / filename)
            path = fixture / source
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(contents.count(old), 1)
            path.write_text(contents.replace(old, new), encoding="utf-8")
            if compile_error:
                with self.assertRaisesRegex(RuntimeError, reason):
                    build_image(fixture)
                self.assertFalse((fixture / "build/rynoros.img").exists())
                return
            build_image(fixture)
            logs = ROOT / "build/heap-tests" / name
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                boot_image(fixture / "build/rynoros.img", logs, timeout=2)
            output = (logs / "serial.log").read_bytes()
            self.assertIn(reason.encode(), output)
            self.assertNotIn(b"[TEST] HEAP self-test passed", output)
            self.assertNotIn(b"[TIMER] tick=", output)
            self.cleanup(logs)

    def test_indistinguishable_free_magic_cannot_compile(self):
        self.broken("free-equals-used", "kernel/mm/heap.c",
                    "#define HEAP_MAGIC 0x524e484541504f42ULL",
                    "#define HEAP_MAGIC 0x524e484541504f43ULL",
                    "distinct used/free tags", compile_error=True)

    def test_misaligned_payload_cannot_pass(self):
        self.broken("misaligned-payload", "kernel/mm/heap.c",
                    "*out = (void *)block_payload(chosen);",
                    "*out = (void *)(block_payload(chosen) + 1);",
                    "[HEAP] failure=tail_alignment")

    def test_lost_small_tail_cannot_pass(self):
        self.broken("lost-tail", "kernel/mm/heap.c",
                    "if (tail < HEAP_MIN) { used += tail; tail = 0; }",
                    "if (tail < HEAP_MIN) { tail = 0; }",
                    "[HEAP] failure=tail_coverage")

    def test_forged_interior_free_cannot_pass(self):
        self.broken("interior-free", "kernel/mm/heap.c",
                    "if (cur != target) return HEAP_INVALID;", "cur = target;",
                    "[HEAP] failure=interior_pointer")
