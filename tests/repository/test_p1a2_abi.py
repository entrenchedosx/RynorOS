"""P1-A2 repository pins: syscalls 9/10, sys_err 11/12, the rt_fs.h
mirrors, caps, resume codes, and the G4 justification record.

Historical numbers (syscalls 0-8, sys_err 0-10) are pinned by exact
value: any renumbering fails here, not silently in-guest.
"""
import re
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

UAPI = (ROOT / "kernel/include/uapi.h").read_text()
SYSCALL = (ROOT / "kernel/include/syscall.h").read_text()
LOAD_H = (ROOT / "kernel/include/load.h").read_text()
USER_H = (ROOT / "kernel/include/user.h").read_text()
USER_C = (ROOT / "kernel/core/user.c").read_text()
PROC_C = (ROOT / "kernel/core/proc.c").read_text(encoding="utf-8")
SHD_C = (ROOT / "kernel/core/shd.c").read_text()
THREAD_C = (ROOT / "kernel/core/thread.c").read_text()
FS_H = (ROOT / "kernel/include/fs.h").read_text()
RT_FS = (ROOT / "user/lib/rt/rt_fs.h").read_text(encoding="utf-8")
RT_H = (ROOT / "user/lib/rt/rt.h").read_text(encoding="utf-8")


def number(text, name):
    match = re.search(r"#define\s+" + name + r"\s+(\d+)u?", text)
    assert match is not None, name
    return int(match.group(1))


def enum_value(text, name):
    match = re.search(r"\b" + name + r"\s*=\s*(\d+)", text)
    assert match is not None, name
    return int(match.group(1))


class P1A2AbiTests(unittest.TestCase):
    def test_syscall_numbers_append_only(self):
        # 0-8 frozen (any renumber fails); 9/10 are the P1-A2 take and
        # the next free number moves to 11.
        want = (("SYS_EXIT", 0), ("SYS_YIELD", 1), ("SYS_WRITE", 2),
                ("SYS_READ", 3), ("SYS_SPAWN", 4), ("SYS_WAIT", 5),
                ("SYS_TERMINATE", 6), ("SYS_FREAD", 7),
                ("SYS_SPAWN_PIPE", 8), ("SYS_FCREATE", 9),
                ("SYS_FWRITE", 10))
        for name, num in want:
            with self.subTest(call=name):
                self.assertEqual(number(SYSCALL, name), num)
        # P1-A3 consumes 11-13 (FSTAT/READDIR/UNLINK); next free is 14.
        self.assertIn("Next free\n   number is 14", SYSCALL)

    def test_sys_err_append_only(self):
        want = (("SYS_OK", 0), ("SYS_AGAIN", 1), ("SYS_INVAL", 2),
                ("SYS_NOTFOUND", 3), ("SYS_MALFORMED", 4),
                ("SYS_BADHANDLE", 5), ("SYS_BUSY", 6), ("SYS_NOMEM", 7),
                ("SYS_BADARG", 8), ("SYS_ALREADY_GONE", 9),
                ("SYS_IOERR", 10), ("SYS_EXISTS", 11), ("SYS_NOSPC", 12),
                # P1-A3 appends SYS_END (end of directory) per G4.
                ("SYS_END", 13))
        for name, num in want:
            with self.subTest(err=name):
                self.assertEqual(enum_value(UAPI, name), num)

    def test_rt_fs_mirrors_match_kernel(self):
        self.assertEqual(number(RT_FS, "RT_SYS_FCREATE"),
                         number(SYSCALL, "SYS_FCREATE"))
        self.assertEqual(number(RT_FS, "RT_SYS_FWRITE"),
                         number(SYSCALL, "SYS_FWRITE"))
        self.assertEqual(number(RT_FS, "RT_FWRITE_MAX"),
                         number(UAPI, "UAPI_FWRITE_MAX"))
        self.assertEqual(number(UAPI, "UAPI_FWRITE_MAX"),
                         number(FS_H, "FS_MAX_WRITE_BYTES"))
        self.assertEqual(number(RT_FS, "RT_FCREATE_PATH_MAX"),
                         number(FS_H, "FS_MAX_PATH"))
        self.assertEqual(number(RT_FS, "RT_SYS_FCREATE"), 9)
        self.assertEqual(number(RT_FS, "RT_SYS_FWRITE"), 10)

    def test_rt_h_stays_frozen(self):
        # rt.h keeps its 15-function surface; P1-A2 wrappers live in
        # the rt_fs.h companion (the rt_pipe.h precedent).
        self.assertNotIn("RT_SYS_FCREATE", RT_H)
        self.assertNotIn("RT_SYS_FWRITE", RT_H)
        self.assertNotIn("rt_fcreate", RT_H)
        self.assertNotIn("rt_fwrite", RT_H)

    def test_wrapper_collapse_rule_matches_fread(self):
        # Thin wrappers collapse like rt_fread: OK -> RT_OK, every
        # other sys_err -> RT_INVAL (exact codes go through rt_gate6).
        for fn in ("rt_fcreate", "rt_fwrite"):
            self.assertIn(fn, RT_FS)
        self.assertIn("rt_gate6(RT_SYS_FCREATE", RT_FS)
        self.assertIn("rt_gate6(RT_SYS_FWRITE", RT_FS)
        # Reserved words cross the gate explicitly (fcreate takes
        # path_ptr/path_len only; four words stay zero).
        self.assertIn("path_len,\n                  0, 0, 0, 0", RT_FS)

    def test_resume_codes_extended(self):
        self.assertIn("#define USER_RUN_FCREATE 12u", USER_H)
        self.assertIn("#define USER_RUN_FWRITE 13u", USER_H)
        self.assertIn("#define USER_RUN_SPAWN_PIPE 11u", USER_H)
        # Every resume-code consumer admits the new codes.
        self.assertIn("USER_RUN_FCREATE", USER_C)
        self.assertIn("USER_RUN_FWRITE", USER_C)
        self.assertIn("USER_RUN_FCREATE", PROC_C)
        self.assertIn("USER_RUN_FWRITE", PROC_C)
        self.assertIn("USER_RUN_FCREATE", SHD_C)
        self.assertIn("USER_RUN_FWRITE", SHD_C)
        # P1-A3 extends the bound to the newest resume code.
        self.assertIn("retcode <= USER_RUN_UNLINK", THREAD_C)

    def test_fcreate_reserved_words_rejected(self):
        # G2: unused fcreate argument registers must be 0 (dispatcher
        # check, mirroring spawn); fwrite uses all six registers.
        self.assertIn("reason == SYS_FCREATE", USER_C)
        self.assertIn("reason == SYS_FWRITE", USER_C)
        self.assertIn("sys_fcreate(c, f->rbx, f->rcx)", USER_C)
        self.assertIn("sys_fwrite(c, f->rbx, f->rcx, f->rdx, f->rsi, f->rdi,\n"
                      "                                f->rbp)", USER_C)

    def test_kern_core_declarations(self):
        self.assertIn("int kern_fcreate(const char *kpath);", LOAD_H)
        self.assertIn("int sys_fcreate(struct user_context *c, cpu_u64 path_ptr,"
                      " cpu_u64 path_len);", LOAD_H)
        self.assertIn("int kern_fwrite(const char *kpath, cpu_u64 offset,"
                      " const cpu_u8 *kbuf,", LOAD_H)
        self.assertIn("int sys_fwrite(struct user_context *c, cpu_u64 path_ptr,"
                      " cpu_u64 path_len,", LOAD_H)

    def test_g4_justification_recorded(self):
        doc = (ROOT / "docs/design/p1a2-cpl3-abi.md").read_text(encoding="utf-8")
        for name, num in (("SYS_EXISTS", 11), ("SYS_NOSPC", 12),
                          ("SYS_FCREATE", 9), ("SYS_FWRITE", 10)):
            self.assertRegex(doc, r"\b%s\s+=\s+%d\b" % (name, num))
        for token in ("G4", "EXISTS", "NOSPC"):
            self.assertIn(token, doc)


if __name__ == "__main__":
    unittest.main()
