"""P1-A3 repository pins: syscalls 11/12/13, sys_err 13 (END),
the frozen stat/dir-entry layouts, the rt_fs.h mirrors, and resume
codes 14/15/16.

Historical numbers (syscalls 0-10, sys_err 0-12) are pinned by exact
value: any renumbering fails here, not silently in-guest. The
syscall-number and sys_err namespaces are SEPARATE (SYS_UNLINK = 13
is a call, SYS_END = 13 is a status); this file pins both so the
documented collision can never drift silently.
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


class P1A3AbiTests(unittest.TestCase):
    def test_syscall_numbers_append_only(self):
        # 0-10 frozen (any renumber fails); 11/12/13 are the P1-A3
        # take and the next free number moves to 14.
        want = (("SYS_EXIT", 0), ("SYS_YIELD", 1), ("SYS_WRITE", 2),
                ("SYS_READ", 3), ("SYS_SPAWN", 4), ("SYS_WAIT", 5),
                ("SYS_TERMINATE", 6), ("SYS_FREAD", 7),
                ("SYS_SPAWN_PIPE", 8), ("SYS_FCREATE", 9),
                ("SYS_FWRITE", 10), ("SYS_FSTAT", 11),
                ("SYS_READDIR", 12), ("SYS_UNLINK", 13))
        for name, num in want:
            with self.subTest(call=name):
                self.assertEqual(number(SYSCALL, name), num)
        self.assertIn("Next free\n   number is 14", SYSCALL)

    def test_sys_err_append_only(self):
        # 0-12 frozen (P1-A2 take included); 13 is the P1-A3 END.
        want = (("SYS_OK", 0), ("SYS_AGAIN", 1), ("SYS_INVAL", 2),
                ("SYS_NOTFOUND", 3), ("SYS_MALFORMED", 4),
                ("SYS_BADHANDLE", 5), ("SYS_BUSY", 6), ("SYS_NOMEM", 7),
                ("SYS_BADARG", 8), ("SYS_ALREADY_GONE", 9),
                ("SYS_IOERR", 10), ("SYS_EXISTS", 11), ("SYS_NOSPC", 12),
                ("SYS_END", 13))
        for name, num in want:
            with self.subTest(err=name):
                self.assertEqual(enum_value(UAPI, name), num)

    def test_namespaces_documented_distinct(self):
        # The SYS_UNLINK call id and the SYS_END status share the
        # number 13 across SEPARATE namespaces (like SYS_FREAD = 7 /
        # SYS_NOMEM = 7); the audit table in the design doc is the
        # contract, not an accident.
        doc = (ROOT / "docs/design/p1a3-lifecycle-abi.md").read_text(
            encoding="utf-8")
        self.assertIn("SYS_UNLINK", doc)
        self.assertIn("SYS_END", doc)
        self.assertIn("SEPARATE", doc)
        self.assertEqual(number(SYSCALL, "SYS_UNLINK"),
                         enum_value(UAPI, "SYS_END"))

    def test_fs_result_end_appended(self):
        match = re.search(r"\bFS_END\s*=\s*(-\d+)", FS_H)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), -13)
        match = re.search(r"\bFS_NOSPC\s*=\s*(-\d+)", FS_H)
        self.assertEqual(int(match.group(1)), -12)

    def test_stat_struct_layout_frozen(self):
        # 32 bytes: type@0 size@8 reserved@16; compile-time asserts
        # in uapi.h are the first line, this is the second.
        self.assertIn("sizeof(struct user_stat) == 32", UAPI)
        self.assertIn('__builtin_offsetof(struct user_stat, type) == 0',
                      UAPI)
        self.assertIn('__builtin_offsetof(struct user_stat, size) == 8',
                      UAPI)
        self.assertIn('__builtin_offsetof(struct user_stat, reserved)'
                      ' == 16', UAPI)
        self.assertEqual(number(UAPI, "UAPI_FTYPE_FILE"),
                         enum_value(FS_H, "FS_TYPE_FILE"))
        self.assertEqual(number(UAPI, "UAPI_FTYPE_DIR"),
                         enum_value(FS_H, "FS_TYPE_DIR"))

    def test_dirent_struct_layout_frozen(self):
        # 64 bytes: name@0 type@40 size@48 reserved@56.
        self.assertIn("sizeof(struct user_dirent) == 64", UAPI)
        self.assertIn('__builtin_offsetof(struct user_dirent, name) == 0',
                      UAPI)
        self.assertIn('__builtin_offsetof(struct user_dirent, type)'
                      ' == 40', UAPI)
        self.assertIn('__builtin_offsetof(struct user_dirent, size)'
                      ' == 48', UAPI)
        self.assertIn('__builtin_offsetof(struct user_dirent, reserved)'
                      ' == 56', UAPI)
        self.assertRegex(UAPI, r"name\[40\]")

    def test_rt_fs_mirrors_match_kernel(self):
        self.assertEqual(number(RT_FS, "RT_SYS_FSTAT"),
                         number(SYSCALL, "SYS_FSTAT"))
        self.assertEqual(number(RT_FS, "RT_SYS_READDIR"),
                         number(SYSCALL, "SYS_READDIR"))
        self.assertEqual(number(RT_FS, "RT_SYS_UNLINK"),
                         number(SYSCALL, "SYS_UNLINK"))
        self.assertEqual(number(RT_FS, "RT_SYS_END"),
                         enum_value(UAPI, "SYS_END"))
        self.assertEqual(number(RT_FS, "RT_FTYPE_FILE"),
                         number(UAPI, "UAPI_FTYPE_FILE"))
        self.assertEqual(number(RT_FS, "RT_FTYPE_DIR"),
                         number(UAPI, "UAPI_FTYPE_DIR"))
        self.assertEqual(number(RT_FS, "RT_SYS_FSTAT"), 11)
        self.assertEqual(number(RT_FS, "RT_SYS_READDIR"), 12)
        self.assertEqual(number(RT_FS, "RT_SYS_UNLINK"), 13)
        self.assertIn("struct rt_stat", RT_FS)
        self.assertIn("struct rt_dirent", RT_FS)
        self.assertRegex(RT_FS, r"name\[40\]")

    def test_rt_h_stays_frozen(self):
        # rt.h keeps its surface; P1-A3 wrappers live in the rt_fs.h
        # companion (the rt_pipe.h precedent).
        self.assertNotIn("RT_SYS_FSTAT", RT_H)
        self.assertNotIn("RT_SYS_READDIR", RT_H)
        self.assertNotIn("RT_SYS_UNLINK", RT_H)
        self.assertNotIn("rt_fstat", RT_H)
        self.assertNotIn("rt_readdir", RT_H)
        self.assertNotIn("rt_unlink", RT_H)

    def test_wrapper_collapse_rule_matches_fread(self):
        # Thin wrappers collapse like rt_fread: OK -> RT_OK, every
        # other sys_err -> RT_INVAL (exact codes go through rt_gate6;
        # END-sensitive callers use the raw gate by design).
        for fn in ("rt_fstat", "rt_readdir", "rt_unlink"):
            self.assertIn(fn, RT_FS)
        self.assertIn("rt_gate6(RT_SYS_FSTAT", RT_FS)
        self.assertIn("rt_gate6(RT_SYS_READDIR", RT_FS)
        self.assertIn("rt_gate6(RT_SYS_UNLINK", RT_FS)
        # Reserved words cross the gate explicitly (fstat takes
        # path_ptr/path_len/out_ptr; readdir ordinal/out_ptr; unlink
        # path_ptr/path_len).
        self.assertIn("(unsigned long long)out, 0, 0, 0", RT_FS)
        self.assertIn("path_len,\n                  0, 0, 0, 0", RT_FS)

    def test_resume_codes_extended(self):
        self.assertIn("#define USER_RUN_FSTAT 14u", USER_H)
        self.assertIn("#define USER_RUN_READDIR 15u", USER_H)
        self.assertIn("#define USER_RUN_UNLINK 16u", USER_H)
        self.assertIn("#define USER_RUN_FWRITE 13u", USER_H)
        # Every resume-code consumer admits the new codes.
        self.assertIn("USER_RUN_FSTAT", USER_C)
        self.assertIn("USER_RUN_READDIR", USER_C)
        self.assertIn("USER_RUN_UNLINK", USER_C)
        self.assertIn("USER_RUN_FSTAT", PROC_C)
        self.assertIn("USER_RUN_READDIR", PROC_C)
        self.assertIn("USER_RUN_UNLINK", PROC_C)
        self.assertIn("USER_RUN_FSTAT", SHD_C)
        self.assertIn("USER_RUN_READDIR", SHD_C)
        self.assertIn("USER_RUN_UNLINK", SHD_C)
        self.assertIn("retcode <= USER_RUN_UNLINK", THREAD_C)

    def test_reserved_words_rejected(self):
        # G2: unused argument registers must be 0 (dispatcher checks,
        # mirroring fcreate/spawn).
        self.assertIn("reason == SYS_FSTAT", USER_C)
        self.assertIn("reason == SYS_READDIR", USER_C)
        self.assertIn("reason == SYS_UNLINK", USER_C)
        self.assertIn("sys_fstat(c, f->rbx, f->rcx, f->rdx)", USER_C)
        self.assertIn("sys_readdir(c, f->rbx, f->rcx)", USER_C)
        self.assertIn("sys_unlink(c, f->rbx, f->rcx)", USER_C)

    def test_load_h_declares_cores(self):
        self.assertIn("sys_fstat(struct user_context *c,", LOAD_H)
        self.assertIn("sys_readdir(struct user_context *c,", LOAD_H)
        self.assertIn("sys_unlink(struct user_context *c,", LOAD_H)
        self.assertIn("kern_fstat(const char *kpath,", LOAD_H)
        self.assertIn("kern_readdir(cpu_u64 ordinal,", LOAD_H)
        self.assertIn("kern_unlink(const char *kpath)", LOAD_H)

    def test_g4_justification_recorded(self):
        doc = (ROOT / "docs/design/p1a3-lifecycle-abi.md").read_text(
            encoding="utf-8")
        for name, num in (("SYS_END", 13), ("SYS_FSTAT", 11),
                          ("SYS_READDIR", 12), ("SYS_UNLINK", 13)):
            self.assertRegex(doc, r"\b%s\s+=\s+%d\b" % (name, num))
        for token in ("G4", "END", "enumeration"):
            self.assertIn(token, doc)


if __name__ == "__main__":
    unittest.main()
