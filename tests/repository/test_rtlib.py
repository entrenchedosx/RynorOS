"""Stage 18c print-rebind pins (host-side, no QEMU).

The in-OS RYNX runtime (runtime="rtlib") must resolve RIR print helpers
through the library (rt_write), while the default runtime keeps the
direct-gate helpers byte-identical to Stage 18b and host targets keep
Linux helpers. Frozen numbers must agree across rt.h, the kernel
header, and the ABI doc.
"""

import inspect
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/rynorlang"))
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT))
from tools.rynorlang import program as rynor_program
from tools.host import rnyx

RTLIB = ROOT / "user/lib/rt"
SRC = 'fn main(): int { print(42); print(true); print("hi"); return 0; }'


class RtlibRebindTests(unittest.TestCase):
    def test_both_runtimes_build(self):
        with tempfile.TemporaryDirectory(prefix="rtlib-pin-", dir=ROOT / "build") as tmp:
            work = Path(tmp)
            default, error = rynor_program.build_rynor_program(
                SRC, "p.rl", work / "rynor", "p")
            self.assertIsNone(error, error)
            self.assertEqual(Path(default["rt_obj"]).name, "rt_rynor.o")
            lib, error = rynor_program.build_rynor_program(
                SRC, "p.rl", work / "rtlib", "p", runtime="rtlib")
            self.assertIsNone(error, error)
            self.assertEqual(Path(lib["rt_obj"]).name, "rt.o")
            # Distinct runtime objects: the rebind is real, not a rename.
            default_blob = rnyx.elf_to_rnyx(Path(default["exe"]).read_bytes())
            lib_blob = rnyx.elf_to_rnyx(Path(lib["exe"]).read_bytes())
            self.assertNotEqual(default_blob, lib_blob)
            # Link-script identity: default keeps rynoros.ld, rtlib uses
            # rynoros_rt.ld (placement-only merge, no permission change).
            self.assertTrue((work / "rynor" / "rynoros.ld").is_file())
            self.assertFalse((work / "rynor" / "rynoros_rt.ld").exists())
            self.assertTrue((work / "rtlib" / "rynoros_rt.ld").is_file())
            self.assertTrue((work / "rynor" / "rt_rynor.asm").is_file())

    def test_default_runtime_is_rynor(self):
        self.assertEqual(
            inspect.signature(rynor_program.build_rynor_program)
            .parameters["runtime"].default, "rynor")

    def test_rtlib_print_goes_through_library(self):
        shim = (RTLIB / "rt_rl.c").read_text(encoding="utf-8")
        self.assertIn("rt_write", shim)
        self.assertNotIn("0x80", shim)
        self.assertNotIn("syscall", shim)
        gate = (RTLIB / "rt_gate.asm").read_text(encoding="utf-8")
        # Exactly two gate instructions in the stub file: the _start exit
        # plus the Stage 18d six-argument gate (rt_gate6, frozen register
        # file, audited placement). A third gate would fail this pin.
        self.assertEqual(len(re.findall(r"int 0x80", gate)), 2)
        # The C gate holds the only other int $0x80 (validated wrapper);
        # the RIR shim itself contains no gate.
        c_src = (RTLIB / "rt.c").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"int \$0x80", c_src)), 1)
        # Host target stays Linux: rt_linux.asm still uses syscall/exit 60,
        # never int 0x80; OS targets never use syscall.
        host = (ROOT / "tools/rynorlang/runtime/rt_linux.asm").read_text(encoding="utf-8")
        self.assertIn("syscall", host)
        self.assertIn("mov rax, 60", host)
        self.assertNotIn("int 0x80", host)

    def test_frozen_numbers_match_kernel(self):
        header = (RTLIB / "rt.h").read_text(encoding="utf-8")
        kernel = (ROOT / "kernel/include/syscall.h").read_text(encoding="utf-8")

        def number(text, name):
            match = re.search(r"#define\s+" + name + r"\s+(\d+)u?", text)
            self.assertIsNotNone(match, name)
            return int(match.group(1))

        self.assertEqual(number(header, "RT_SYS_EXIT"), number(kernel, "SYS_EXIT"))
        self.assertEqual(number(header, "RT_SYS_YIELD"), number(kernel, "SYS_YIELD"))
        self.assertEqual(number(header, "RT_SYS_WRITE"), number(kernel, "SYS_WRITE"))
        # Stage 18d Slice A extends the surface by exactly one entry.
        self.assertEqual(number(header, "RT_SYS_READ"), number(kernel, "SYS_READ"))
        self.assertEqual(number(header, "RT_READ_MAX"), number(kernel, "SYSCALL_READ_MAX"))
        self.assertEqual(number(header, "RT_FD_STDIN"), number(kernel, "SYS_STDIN"))
        self.assertEqual(number(header, "RT_WRITE_MAX"), number(kernel, "SYSCALL_WRITE_MAX"))
        self.assertEqual(number(header, "RT_FD_STDOUT"), number(kernel, "SYS_STDOUT"))
        doc = (ROOT / "docs/design/syscall-abi.md").read_text(encoding="utf-8")
        self.assertIn("| 0 | exit |", doc)
        self.assertIn("| 1 | yield |", doc)
        self.assertIn("| 2 | write |", doc)
        # rt_err order frozen: OK 0 through NOMEM 5.
        names = re.findall(r"^\s*(RT_\w+)(?:\s*=\s*\d+)?,?\s*$", header, re.M)
        self.assertEqual(names[:6],
                         ["RT_OK", "RT_INVAL", "RT_RANGE", "RT_NOSYS", "RT_AGAIN", "RT_NOMEM"])
        # Frozen surface: 16 functions (13 + 2 evidence channels + the
        # Stage 18d Slice A fd-read entry; the 18c rt_open/rt_read NOSYS
        # stubs keep their names and behavior, so the new entry is
        # rt_fd_read, not a repurposed stub).
        decls = re.findall(r"^\s*(?:void|enum rt_err|unsigned long long|long long)\s+(rt_\w+)\s*\(", header, re.M)
        self.assertEqual(len(decls), 16, decls)
        for fn in ("rt_exit", "rt_write", "rt_print", "rt_print_bytes", "rt_fmt",
                   "rt_alloc", "rt_free", "rt_arena_watermark", "rt_live_count",
                   "rt_ptr_off", "rt_nap", "rt_set_flag", "rt_wait_flag",
                   "rt_open", "rt_read", "rt_fd_read"):
            self.assertIn(fn, decls, fn)
        # Doc discloses the evidence channel and the non-NUL contract.
        native = (ROOT / "docs/design/native-runtime.md").read_text(encoding="utf-8")
        self.assertIn("rt_ptr_off", native)
        self.assertIn("NUL", native)
        self.assertIn("terminat", native)


if __name__ == "__main__":
    unittest.main()
