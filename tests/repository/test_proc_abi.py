"""Stage 18d Slice C repository pins: syscall numbers, UAPI layouts,
RYNX v2 converter rules, and proc-validator fixtures."""
import struct
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT))
from proc_output import PROC_VERIFIED, validate_proc_section
from tools.host import rnyx

UAPI = (ROOT / "kernel/include/uapi.h").read_text()
SYSCALL = (ROOT / "kernel/include/syscall.h").read_text()
PROC_H = (ROOT / "kernel/include/proc.h").read_text()
LOAD_H = (ROOT / "kernel/include/load.h").read_text()


class ProcAbiTests(unittest.TestCase):
    def test_syscall_numbers_frozen(self):
        for num, name in ((0, "SYS_EXIT"), (1, "SYS_YIELD"), (2, "SYS_WRITE"),
                          (3, "SYS_READ"), (4, "SYS_SPAWN"), (5, "SYS_WAIT"),
                          (6, "SYS_TERMINATE"), (7, "SYS_FREAD"),
                          (8, "SYS_SPAWN_PIPE")):
            self.assertRegex(SYSCALL, r"#define %s %du" % (name, num))

    def test_sys_err_complete(self):
        for num, name in ((0, "SYS_OK"), (1, "SYS_AGAIN"), (2, "SYS_INVAL"),
                          (3, "SYS_NOTFOUND"), (4, "SYS_MALFORMED"),
                          (5, "SYS_BADHANDLE"), (6, "SYS_BUSY"), (7, "SYS_NOMEM"),
                          (8, "SYS_BADARG"), (9, "SYS_ALREADY_GONE"),
                          (10, "SYS_IOERR")):
            self.assertRegex(UAPI, r"%s = %d" % (name, num))

    def test_proc_layouts(self):
        self.assertIn("#define PROC_MAX UAPI_MAX_PROCS", PROC_H)
        self.assertIn("PROC_OWNER_KERNEL (-1)", PROC_H)
        for name in ("PL_FREE", "PL_LOADING", "PL_ACTIVE",
                     "PL_EXITED", "PL_FAULTED", "PL_ABORTED"):
            self.assertIn(name, PROC_H)
        for fn in ("proc_check", "proc_spawn_image", "proc_wait",
                   "proc_terminate", "proc_initialize"):
            self.assertIn(fn, PROC_H)

    def test_rynx_v2_bounds_frozen(self):
        self.assertIn("#define RNYX_VERSION2 2u", LOAD_H)
        self.assertIn("RNYX_V2_CODE_MAX (8u * 4096u)", LOAD_H)
        self.assertIn("RNYX_V2_DATA_MAX (4u * 4096u)", LOAD_H)
        self.assertIn("#define USER_MAX_CODE_PAGES 8u",
                      (ROOT / "kernel/include/user.h").read_text())
        self.assertIn("#define USER_MAX_DATA_PAGES 4u",
                      (ROOT / "kernel/include/user.h").read_text())

    def test_argv_caps_frozen(self):
        self.assertIn("#define UAPI_MAX_ARGC 8u", UAPI)
        self.assertIn("#define UAPI_MAX_ARGV_BYTES 256u", UAPI)
        self.assertIn("#define UAPI_MAX_PROCS 3u", UAPI)

    def test_rnyx_v1_unchanged(self):
        code = bytes(range(1, 101))
        v1a = rnyx.build_envelope(code, 0, 0, b"")
        v1b = rnyx.build_envelope(code, 0, 0, b"", version=1)
        self.assertEqual(v1a, v1b)
        self.assertEqual(v1a[4:6], struct.pack("<H", 1))

    def test_rnyx_v2_envelope_rules(self):
        code = bytes(100)
        v2 = rnyx.build_envelope(code, 0, 0, b"", version=2)
        self.assertEqual(v2[4:6], struct.pack("<H", 2))
        big = bytes(32768)
        env = rnyx.build_envelope(big, 0, 0, b"", version=2)
        self.assertEqual(len(env), 28 + 32768)
        with self.assertRaises(ValueError):
            rnyx.build_envelope(bytes(32769), 0, 0, b"", version=2)
        with self.assertRaises(ValueError):
            rnyx.build_envelope(code, 0, 16385, b"", version=2)
        with self.assertRaises(ValueError):
            rnyx.build_envelope(code, 0, 0, b"", version=3)
        # v1 rejects v2 sizes.
        with self.assertRaises(ValueError):
            rnyx.build_envelope(bytes(4097), 0, 0, b"", version=1)

    def test_rnyx_converter_version_gate(self):
        self.assertEqual(rnyx.CODE_MAX2, 32768)
        self.assertEqual(rnyx.DATA_MAX2, 16384)
        with self.assertRaises(ValueError):
            rnyx.elf_to_rnyx(b"not an elf", version=2)
        with self.assertRaises(ValueError):
            rnyx.elf_to_rnyx(b"not an elf", version=3)

    def test_validator_accepts_good_section(self):
        good = (b"[PROC] self-test started\r\n"
                b"[PROC] abi pins ok\r\n"
                b"[PROC] spawn slot=0 gen=1\r\n"
                b"[PROC] wait state=1 code=42\r\n"
                b"[LOAD] write slot=0 fd=1 len=2 nwritten=2 hex=6869\r\n"
                b"[PROC] accounting balanced\r\n"
                + PROC_VERIFIED)
        self.assertEqual(validate_proc_section(good), [])

    def test_validator_rejects_wrong_and_marks_incomplete(self):
        bad = (b"[PROC] self-test started\r\n"
               b"[PROC] bogus line\r\n" + PROC_VERIFIED)
        errors = validate_proc_section(bad)
        self.assertTrue(any("mismatch" in e for e in errors))
        partial = b"[PROC] self-test started\r\n[PROC] abi pins ok\r\n"
        errors = validate_proc_section(partial)
        self.assertTrue(errors and not any("mismatch" in e for e in errors))
        self.assertEqual(validate_proc_section(b""), [])

    def test_lost_marker_documented(self):
        abi = (ROOT / "docs/design/stage18d-abi.md").read_text()
        self.assertIn("reserved zero scan", abi)
