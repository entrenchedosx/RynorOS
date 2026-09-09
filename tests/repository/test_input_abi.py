"""Stage 18d Slices A/B repository pins: frozen UAPI values, layout
assertions, runtime mirror declarations, and input-validator fixtures."""
import re
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from input_output import (CANONICAL_KEYS, INPUT_SCANS, INPUT_VERIFIED,
                          expected_input_bytes, validate_input_section)

UAPI = (ROOT / "kernel/include/uapi.h").read_text()
SYSCALL = (ROOT / "kernel/include/syscall.h").read_text()
RT = (ROOT / "user/lib/rt/rt.h").read_text()


class InputAbiTests(unittest.TestCase):
    def test_syscall_numbers_frozen(self):
        for num, name in ((0, "SYS_EXIT"), (1, "SYS_YIELD"), (2, "SYS_WRITE"),
                          (3, "SYS_READ"), (4, "SYS_SPAWN"), (5, "SYS_WAIT"),
                          (6, "SYS_TERMINATE"), (7, "SYS_FREAD"),
                          (8, "SYS_SPAWN_PIPE")):
            self.assertRegex(SYSCALL, r"#define %s %du" % (name, num))
        self.assertIn("SYSCALL_READ_MAX 4096u", SYSCALL)
        self.assertIn("SYS_STDIN 0u", SYSCALL)

    def test_sys_err_values_frozen(self):
        for num, name in ((0, "SYS_OK"), (1, "SYS_AGAIN"), (2, "SYS_INVAL"),
                          (3, "SYS_NOTFOUND"), (4, "SYS_MALFORMED"),
                          (5, "SYS_BADHANDLE"), (6, "SYS_BUSY"), (7, "SYS_NOMEM"),
                          (8, "SYS_BADARG"), (9, "SYS_ALREADY_GONE"),
                          (10, "SYS_IOERR")):
            self.assertRegex(UAPI, r"%s = %d" % (name, num))

    def test_proc_state_separate_domain(self):
        for num, name in ((0, "PROC_RUNNING"), (1, "PROC_EXITED"),
                          (2, "PROC_FAULTED"), (3, "PROC_ABORTED")):
            self.assertRegex(UAPI, r"%s = %d" % (name, num))
        self.assertIn("never compare", UAPI)

    def test_selector_values_frozen(self):
        for num, name in ((0, "STDIN_CLOSED"), (1, "STDIN_KBD"),
                          (2, "STDIN_PIPE"), (3, "STDIN_FILE")):
            self.assertRegex(UAPI, r"%s = %d" % (name, num))
        for num, name in ((0, "STDOUT_SERIAL"), (1, "STDOUT_PIPE"),
                          (2, "STDOUT_FILE")):
            self.assertRegex(UAPI, r"%s = %d" % (name, num))

    def test_layout_sizes_pinned(self):
        self.assertIn("sizeof(struct user_arg) == 16", UAPI)
        self.assertIn("sizeof(struct spawn_spec) == 96", UAPI)
        self.assertIn("sizeof(struct proc_status) == 16", UAPI)

    def test_layout_offsets_pinned(self):
        for field, off in (("path_ptr", 0), ("path_len", 8), ("args_ptr", 16),
                           ("nargs", 24), ("stdin_sel", 28), ("stdout_sel", 32),
                           ("stderr_sel", 36), ("file_in", 40), ("file_out", 48),
                           ("file_err", 56), ("reserved", 64)):
            self.assertIn("offsetof(struct spawn_spec, %s) == %d" % (field, off), UAPI)
        for field, off in (("state", 0), ("code", 4), ("detail", 8),
                           ("reserved", 12)):
            self.assertIn("offsetof(struct proc_status, %s) == %d" % (field, off), UAPI)

    def test_runtime_mirrors_kernel(self):
        self.assertIn("#define RT_SYS_READ 3u", RT)
        self.assertIn("#define RT_FD_STDIN 0u", RT)
        self.assertIn("#define RT_READ_MAX 4096u", RT)
        self.assertIn("rt_fd_read(unsigned int fd, void *buf", RT)
        sys_ok = re.search(r"#define SYS_READ (\d+)u", SYSCALL)
        self.assertIsNotNone(sys_ok)
        self.assertIn("#define RT_SYS_READ %su" % sys_ok.group(1), RT)

    def test_validator_accepts_good_section(self):
        good = (b"[INPUT] self-test started\r\n"
                b"[INPUT] decode matrix ok\r\n"
                b"[INPUT] copy matrix pass=3 fail=7\r\n"
                b"[INPUT] waiting for input=0\r\n"
                b"[INPUT] payload n=4 hex=1e9e30b0\r\n"
                b"[INPUT] window attempt=0 keys=a,ctrl,b,ret\r\n"
                b"[INPUT] window parked attempt=0\r\n"
                b"[INPUT] window bytes=8\r\n"
                b"[INPUT] lost dropped=37\r\n"
                b"[INPUT] accounting balanced\r\n"
                + INPUT_VERIFIED)
        self.assertEqual(validate_input_section(good), [])

    def test_validator_rejects_wrong_and_marks_incomplete(self):
        bad = (b"[INPUT] self-test started\r\n"
               b"[INPUT] bogus line\r\n" + INPUT_VERIFIED)
        errors = validate_input_section(bad)
        self.assertTrue(any("mismatch" in e for e in errors))
        partial = b"[INPUT] self-test started\r\n[INPUT] decode matrix ok\r\n"
        errors = validate_input_section(partial)
        self.assertTrue(errors and not any("mismatch" in e for e in errors))
        self.assertEqual(validate_input_section(b""), [])

    def test_canonical_key_budget(self):
        self.assertEqual(len(CANONICAL_KEYS), 8 + 34)
        total = sum(len(INPUT_SCANS[k]) for k in CANONICAL_KEYS)
        # P2 8x2B + P4 34x2B positionally consumed; park windows name
        # their own keys per attempt outside this tuple.
        self.assertEqual(total, 16 + 68)
        self.assertEqual(bytes(expected_input_bytes(("ctrl",))), b"\x1d\x9d")
        self.assertEqual(bytes(expected_input_bytes(("ctrl_r",))), b"\xe0\x1d\xe0\x9d")
