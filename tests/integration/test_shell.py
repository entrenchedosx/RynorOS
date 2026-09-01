"""Opt-in shell image: real QEMU keyboard input and complete make/break ownership."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from shell_output import SHELL_END, SHELL_KEYS


class ShellIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.destination = ROOT / "build/shell-tests/image"
        build_image(ROOT, cls.destination, shell_interactive=True)

    def test_real_session_consumes_every_make_and_break(self):
        logs = ROOT / "build/shell-tests/interactive"
        try:
            output = boot_image(self.destination / "rynoros.img", logs,
                                timeout=30, shell_interactive=True)
        finally:
            summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["reaped"])
            self.assertEqual((summary["cleanup"], summary["returncode"]),
                             ("monitor-quit", 0))
        self.assertIn(SHELL_END, output)
        self.assertIn(("[SHELL] keys=%d received_scan_bytes=%d\r\n" %
                       (len(SHELL_KEYS), 2 * len(SHELL_KEYS))).encode(), output)
        self.assertEqual(summary["shell_inputs_sent"], len(SHELL_KEYS))


if __name__ == "__main__":
    unittest.main()
