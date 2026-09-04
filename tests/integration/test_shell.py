"""Opt-in shell image: real QEMU keyboard input and complete make/break ownership.
Mutation variants prove causality through the shell/queue/tokenizer/service path,
not just ISR byte consumption. Host-variable per-run SCRIPT proves canned transcript
cannot match by luck."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from shell_output import SHELL_END, SHELL_KEYS, SCANS, SCRIPT


def _build_fixture():
    tmp = tempfile.TemporaryDirectory(prefix="shell-fault-", dir=ROOT / "build")
    root = Path(tmp.name)
    for d in REQUIRED_DIRECTORIES:
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_FILES:
        shutil.copyfile(ROOT / f, root / f)
    return tmp, root


def _mutate(root, source, pairs):
    path = root / source
    contents = path.read_text()
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents)
    return root


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
            self.assertEqual(summary["shell_inputs_sent"], len(SHELL_KEYS))
            self.assertLess(summary["elapsed_seconds"], 15)
        self.assertIn(SHELL_END, output)
        self.assertIn(("[SHELL] keys=%d received_scan_bytes=%d\r\n" %
                       (len(SHELL_KEYS), 2 * len(SHELL_KEYS))).encode(), output)

    def _run_shell_failure(self, expected_reason, pairs, source="kernel/shell/shell.c"):
        tmp, root = _build_fixture()
        self.addCleanup(tmp.cleanup)
        _mutate(root, source, pairs)
        build_image(root, shell_interactive=True)
        logs = ROOT / f"build/shell-tests/{self._testMethodName}"
        try:
            with self.assertRaises(RuntimeError) as err:
                boot_image(root / "build/rynoros.img", logs, timeout=30, shell_interactive=True)
        finally:
            summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["reaped"])
            self.assertEqual(summary["cleanup"], "monitor-quit")
            self.assertEqual(summary["returncode"], 0)
        msg = str(err.exception) + (logs / "serial.log").read_bytes().decode("ascii", errors="replace")
        failure_field = summary.get("failure", "")
        self.assertIn(expected_reason, msg, f"expected reason {expected_reason!r} not in {msg[:800]}")
        self.assertIn(expected_reason, failure_field, f"expected reason not in run.json failure {failure_field!r}")

    def test_shell_canned_output_is_rejected(self):
        canned = (
            'static void interactive_session(cpu_u64 key_budget)\n{\n'
            '    (void)key_budget;\n'
            '    say_line("[SHELL] interactive session started");\n'
            '    for (cpu_u64 i = 0; i < 39; ++i) {\n'
            '        say("[SHELL] waiting for input="); say_number(i); say("\\r\\n");\n'
            '        say("[SHELL] key=0 scan=0x16 ascii=\'u\' line=\\"\\"\\r\\n");\n'
            '    }\n'
            '    say_line("[SHELL] interactive session complete");\n'
            '    say("[SHELL] keys=39 received_scan_bytes=78\\r\\n");\n'
            '    say_line("[SHELL] real keyboard session verified");\n'
            '    return;\n'
            '}\n'
        )
        self._run_shell_failure("kb_counts", [
            ("static void interactive_session(cpu_u64 key_budget)\n{", canned + "static void __attribute__((unused)) interactive_session_unused(cpu_u64 key_budget)\n{")
        ])

    def test_shell_dispatch_bypass_is_rejected(self):
        # A shell_execute early-return makes every command claim success, so the
        # long-upper rejection require (which precedes the bogus recovery check)
        # is the first witness; r_bogus would also fire but is never reached.
        self._run_shell_failure("r_upper_long", [
            ("    if (!line || cap == 0) return SHELL_INVALID;", "    if (!line || cap == 0) return SHELL_INVALID;\n    return SHELL_OK;"),
        ])

    def test_shell_service_bypass_is_rejected(self):
        self._run_shell_failure("ABC123", [
            ("    int r = krst_call(KRST_SVC_UPPER, text, len, out, sizeof out, &n);", "    int r = KRST_OK; (void)text; (void)len; (void)out; (void)n;"),
        ])

    def test_shell_tokenizer_bypass_is_rejected(self):
        self._run_shell_failure("token_ok", [
            ("    if (!line || !tokens || cap == 0) return SHELL_INVALID;", "    if (!line || !tokens || cap == 0) return SHELL_INVALID;\n    return 0;"),
        ])

    def test_shell_count_low_byte_decoder_is_rejected(self):
        self._run_shell_failure("decode300", [
            ("cpu_u64 shell_decode_u64_le(const cpu_u8 *buf, cpu_u64 len)\n{\n    if (!buf || len != 8) return 0;\n    cpu_u64 v = 0;\n    for (unsigned int i = 0; i < 8; ++i) v |= (cpu_u64)buf[i] << (i * 8);\n    return v;\n}", "cpu_u64 shell_decode_u64_le(const cpu_u8 *buf, cpu_u64 len)\n{\n    if (!buf || len != 8) return 0;\n    return buf[0];\n}"),
        ])

    def test_malformed_pause_sequence_must_recover_immediately(self):
        self._run_shell_failure("prefix_recovery", [
            ("n + 1 < sizeof pause_tail ? state->pause + 1 : 0;",
             "n + 1 < sizeof pause_tail ? state->pause + 1 : state->pause + 1;"),
        ])

    def test_shell_realistic_canned_bypass_is_rejected(self):
        realistic = (
            'static void interactive_session(cpu_u64 key_budget)\n{\n'
            '    (void)key_budget;\n'
            '    say_line("[SHELL] interactive session started");\n'
            '    if (!irq_set_enabled(1, 1)) { say_line("[SHELL] failure=irq_enable"); cpu_halt(); }\n'
            '    struct shell_line line = { {0}, 0 }; (void)line;\n'
            '    for (cpu_u64 n = 0; n < 39; ++n) {\n'
            '        say("[SHELL] waiting for input="); say_number(n); say("\\r\\n");\n'
            '        (void)serial_flush();\n'
            '        struct kbd_event _e; unsigned int _events = 0;\n'
            '        while (_events < 2) { enum kbd_result _r; while ((_r = kbd_poll(&_e)) == KBD_EMPTY) __asm__ volatile ("sti; hlt; cli" ::: "memory"); if (_r == KBD_EVENT) ++_events; }\n'
            '        say("[SHELL] key=0 scan=0x16 ascii=\'u\' line=\\"\\"\\r\\n");\n'
            '    }\n'
            '    say_line("[SHELL] interactive session complete");\n'
            '    say("[SHELL] keys=39 received_scan_bytes=78\\r\\n");\n'
            '    say_line("[SHELL] real keyboard session verified");\n'
            '    return;\n'
            '}\n'
        )
        self._run_shell_failure("interactive mismatch", [
            ("static void interactive_session(cpu_u64 key_budget)\n{", realistic + "static void __attribute__((unused)) interactive_session_unused2(cpu_u64 key_budget)\n{")
        ])

    def test_shell_host_selected_variant_proves_causality(self):
        commands = ("echo hi", "upper hello", "digest abc", "bogusxx")
        alternate = tuple("spc" if ch == " " else ch
                          for command in commands for ch in (*command, "ret"))
        self.assertEqual(len(alternate), len(SHELL_KEYS))
        self.assertNotEqual(alternate, tuple(SHELL_KEYS))
        logs = ROOT / "build/shell-tests/host-variable"
        try:
            output = boot_image(self.destination / "rynoros.img", logs, timeout=30,
                                shell_interactive=True, shell_keys=alternate)
        finally:
            summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["reaped"])
            self.assertEqual(summary["shell_keys"], list(alternate))
            self.assertEqual(summary["shell_inputs_sent"], len(alternate))
        interactive = output.partition(b"[SHELL] interactive session started\r\n")[2]
        self.assertIn(b'[SHELL] exec=echo arg="hi"\r\nhi\r\n', interactive)
        self.assertIn(b'[SHELL] exec=upper arg="hello"\r\nHELLO\r\n', interactive)
        self.assertIn(b'[SHELL] exec=digest arg="abc"\r\n', interactive)
        self.assertIn(b'[SHELL] exec=bogusxx\r\nerror: unknown command\r\n', interactive)


if __name__ == "__main__":
    unittest.main()
