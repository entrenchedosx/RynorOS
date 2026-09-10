"""Stage 18d Slice D repository pins: fread/spawn_pipe register files,
caps, discovery bound, pipe error discipline, and file/pipe validators.
"""
import re
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT))
from pipe_output import (FREAD_VERIFIED, PIPE_VERIFIED,
                         validate_fread_section, validate_pipe_section)

UAPI = (ROOT / "kernel/include/uapi.h").read_text()
SYSCALL = (ROOT / "kernel/include/syscall.h").read_text()
PIPE_H = (ROOT / "kernel/include/pipe.h").read_text()
USER_H = (ROOT / "kernel/include/user.h").read_text()
RT_PIPE = (ROOT / "user/lib/rt/rt_pipe.h").read_text(encoding="utf-8")
# Explicit UTF-8: the frozen register table uses U+2264 (<=), which the
# Windows locale default (cp1252) would mojibake into three characters.
ABI = (ROOT / "docs/design/stage18d-abi.md").read_text(encoding="utf-8")


class PipeAbiTests(unittest.TestCase):
    def test_fread_register_file_frozen(self):
        row = [line for line in ABI.splitlines()
               if re.match(r"\|\s*7\s*fread\s*\|", line)]
        self.assertEqual(len(row), 1, row)
        cells = [c.strip() for c in row[0].strip().strip("|").split("|")]
        self.assertEqual(cells[1:], ["path_ptr", "path_len≤32", "offset",
                                    "buf", "len≤16384", "nread_out",
                                    "sys_err"])

    def test_spawn_pipe_register_file_frozen(self):
        row = [line for line in ABI.splitlines()
               if re.match(r"\|\s*8\s*spawn_pipe\s*\|", line)]
        self.assertEqual(len(row), 1, row)
        cells = [c.strip() for c in row[0].strip().strip("|").split("|")]
        self.assertEqual(cells[1:], ["spec_a", "spec_b", "handle_a_out",
                                    "handle_b_out", "0", "0", "sys_err"])

    def test_fread_cap_frozen(self):
        self.assertIn("#define UAPI_FREAD_MAX 16384u", UAPI)
        self.assertIn("#define RT_FREAD_MAX 16384u", RT_PIPE)
        self.assertIn("no flags word", ABI)

    def test_pipe_cap_frozen(self):
        self.assertIn("#define UAPI_PIPE_BUF 4096u", UAPI)
        self.assertIn("#define UAPI_PIPE_MAX 1u", UAPI)
        self.assertIn("#define PIPE_CAP UAPI_PIPE_BUF", PIPE_H)
        self.assertIn("#define PIPE_MAX UAPI_PIPE_MAX", PIPE_H)
        self.assertIn("#define RT_PIPE_BUF 4096u", RT_PIPE)

    def test_bin_name_bound_frozen(self):
        # "/bin/" (5) + max bare name (27) == FS path cap (32): the
        # frozen discovery bound, never truncated.
        self.assertIn("#define UAPI_MAX_BIN_NAME 27u", UAPI)
        self.assertIn("#define FS_MAX_PATH 32u",
                      (ROOT / "kernel/include/fs.h").read_text())

    def test_rt_pipe_numbers_match_kernel(self):
        def number(text, name):
            match = re.search(r"#define\s+" + name + r"\s+(\d+)u?", text)
            self.assertIsNotNone(match, name)
            return int(match.group(1))
        self.assertEqual(number(RT_PIPE, "RT_SYS_FREAD"),
                         number(SYSCALL, "SYS_FREAD"))
        self.assertEqual(number(RT_PIPE, "RT_SYS_SPAWN_PIPE"),
                         number(SYSCALL, "SYS_SPAWN_PIPE"))
        self.assertEqual(number(SYSCALL, "SYS_FREAD"), 7)
        self.assertEqual(number(SYSCALL, "SYS_SPAWN_PIPE"), 8)

    def test_no_pipe_errors_in_sys_err(self):
        # The freeze removed provisional PIPE_* errors: short-zero and
        # AGAIN/EOF carry the semantics; resurrecting them breaks this.
        for token in ("PIPE_FULL", "PIPE_EMPTY", "PIPE_BROKEN", "PIPE_EOF"):
            self.assertNotIn(token, UAPI)
            self.assertNotIn(token, SYSCALL)
            self.assertNotIn(token, PIPE_H)
        names = re.findall(r"SYS_\w+", UAPI)
        self.assertEqual(sorted(set(names)),
                         sorted(["SYS_OK", "SYS_AGAIN", "SYS_INVAL",
                                 "SYS_NOTFOUND", "SYS_MALFORMED",
                                 "SYS_BADHANDLE", "SYS_BUSY", "SYS_NOMEM",
                                 "SYS_BADARG", "SYS_ALREADY_GONE",
                                 "SYS_IOERR"]))

    def test_resume_codes_extended(self):
        self.assertIn("#define USER_RUN_FREAD 10u", USER_H)
        self.assertIn("#define USER_RUN_SPAWN_PIPE 11u", USER_H)
        self.assertIn("#define USER_RUN_TERMINATED 9u", USER_H)

    def test_validators_accept_good_sections(self):
        good_f = (b"[FREAD] self-test started\r\n"
                  b"[FREAD] abi pins ok\r\n"
                  b"[LOAD] write slot=0 fd=1 len=2 nwritten=2 hex=6869\r\n"
                  b"[FREAD] accounting balanced\r\n"
                  + FREAD_VERIFIED)
        self.assertEqual(validate_fread_section(good_f), [])
        good_p = (b"[PIPE] self-test started\r\n"
                  b"[PIPE] abi pins ok\r\n"
                  b"[PIPE] spawn_pipe a=1 b=2\r\n"
                  b"[PIPE] transfer wrap bytes=8192 turns=4 full=1 empty=2 overlap=1\r\n"
                  b"[PIPE] accounting balanced\r\n"
                  + PIPE_VERIFIED)
        self.assertEqual(validate_pipe_section(good_p), [])

    def test_validators_reject_wrong_and_mark_incomplete(self):
        bad = (b"[FREAD] self-test started\r\n"
               b"[FREAD] bogus line\r\n" + FREAD_VERIFIED)
        errors = validate_fread_section(bad)
        self.assertTrue(any("mismatch" in e for e in errors))
        bad = (b"[PIPE] self-test started\r\n"
               b"[PIPE] bogus line\r\n" + PIPE_VERIFIED)
        errors = validate_pipe_section(bad)
        self.assertTrue(any("mismatch" in e for e in errors))
        partial = b"[PIPE] self-test started\r\n[PIPE] abi pins ok\r\n"
        errors = validate_pipe_section(partial)
        self.assertTrue(errors and not any("mismatch" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
