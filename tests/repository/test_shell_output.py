"""Synthetic Stage 11 shell parser fixtures, not hardware execution evidence."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from shell_output import (SHELL_START, SHELL_END, SHELL_GOOD, SHELL_SKIP, SCRIPT,
                          SHELL_KEYS, KEY_BUDGET, digest_hex,
                          parse_shell_output, validate_shell_output)
from boot_output import validate_boot_output
from test_exception_output import parser_fixture as exception_fixture
from test_pmm_output import fixture as pmm_fixture
from test_vm_output import fixture as vm_fixture
from test_heap_output import fixture as heap_fixture
from timer_output import TIMER_OUTPUT
from sched_output import SCHED_GOOD
from kbd_output import KBD_GOOD
from display_output import DISPLAY_GOOD
from runtime_output import RUNTIME_GOOD
from boot_output import POST_IRQ


class ShellOutputTests(unittest.TestCase):
    def test_valid_synthetic_fixture(self):
        self.assertEqual(validate_shell_output(SHELL_GOOD), [])
        self.assertEqual(parse_shell_output(SHELL_GOOD)["interactive"], False)

    def test_every_line_required(self):
        for line in SHELL_GOOD.splitlines(keepends=True):
            self.assertTrue(validate_shell_output(SHELL_GOOD.replace(line, b"", 1)),
                            "line should be required: %r" % line)

    def test_parser_rejects_structural_damage(self):
        for output in (b"", b"garbage\r\n", SHELL_GOOD + b"extra\r\n",
                       SHELL_START + SHELL_END, SHELL_GOOD.replace(SHELL_END, b""),
                       SHELL_GOOD.replace(b"RynorOS 0.1.0", b"RynorOS 9.9.9"),
                       SHELL_GOOD.replace(b"free_bytes=65802240", b"free_bytes=1"),
                       SHELL_GOOD.replace(b"[SHELL] interactive session skipped (host did not request input)\r\n", b""),
                       SHELL_GOOD.replace(b"[SHELL] self-test started", b"[SHELL] self-test missing"),
                       SHELL_GOOD * 2, SHELL_GOOD + b"\xff"):
            self.assertTrue(validate_shell_output(output), output[:80])

    def test_accounting_must_match_prior_baseline(self):
        previous = dict(allocated=122880, free=65802240, tables=14)
        self.assertEqual(validate_shell_output(SHELL_GOOD, previous), [])
        for key, value in (("free", 65802239), ("tables", 13), ("allocated", 122881)):
            wrong = previous.copy(); wrong[key] = value
            self.assertTrue(validate_shell_output(SHELL_GOOD, wrong))

    def test_script_keys_are_wellformed(self):
        self.assertEqual(KEY_BUDGET, len(SCRIPT))
        self.assertEqual(len(SHELL_KEYS), len(SCRIPT))
        self.assertEqual(digest_hex(b"ab"), "6A9845B507449C08")

    def test_python_optimized_mode_still_fails_closed(self):
        # The parser must raise ValueError, never rely on assert statements that
        # python -O disables.
        self.assertNotIn("assert", open(Path(__file__).resolve().parents[2] /
                                        "tools/host/shell_output.py").read())
        with self.assertRaises(ValueError):
            parse_shell_output(b"not a shell section\r\n")

    def test_interactive_contract_cannot_accept_stage10_early(self):
        transcript = (exception_fixture() + pmm_fixture() + vm_fixture() +
                      heap_fixture() + TIMER_OUTPUT + SCHED_GOOD + KBD_GOOD +
                      DISPLAY_GOOD + RUNTIME_GOOD + POST_IRQ)
        self.assertEqual(validate_boot_output(transcript), [])
        self.assertIn("Required interactive shell output missing",
                      validate_boot_output(transcript, require_shell=True))
