"""Stage 18d Slice E repository pins: shell userspace numbers, bounds,
status policy, placement guard (no kernel evaluator), and validator."""
import re
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT))
from sh_output import validate_sh_section

SYSCALL = (ROOT / "kernel/include/syscall.h").read_text(encoding="utf-8")
UAPI = (ROOT / "kernel/include/uapi.h").read_text(encoding="utf-8")
RT_PIPE = (ROOT / "user/lib/rt/rt_pipe.h").read_text(encoding="utf-8")
SH_C = (ROOT / "user/shell/sh.c").read_text(encoding="utf-8")
SH_PARSE_H = (ROOT / "user/shell/sh_parse.h").read_text(encoding="utf-8")
SH_KEY_C = (ROOT / "user/shell/sh_key.c").read_text(encoding="utf-8")
SHD_C = (ROOT / "kernel/core/shd.c").read_text(encoding="utf-8")


def number(text, name):
    match = re.search(r"#define\s+" + name + r"\s+(\d+)u?", text)
    assert match is not None, name
    return int(match.group(1))


class ShellAbiTests(unittest.TestCase):
    def test_spawn_numbers_match_kernel(self):
        self.assertEqual(number(RT_PIPE, "RT_SYS_SPAWN"),
                         number(SYSCALL, "SYS_SPAWN"))
        self.assertEqual(number(RT_PIPE, "RT_SYS_WAIT"),
                         number(SYSCALL, "SYS_WAIT"))
        self.assertEqual(number(RT_PIPE, "RT_SYS_TERMINATE"),
                         number(SYSCALL, "SYS_TERMINATE"))
        self.assertEqual(
            (number(SYSCALL, "SYS_SPAWN"), number(SYSCALL, "SYS_WAIT"),
             number(SYSCALL, "SYS_TERMINATE")), (4, 5, 6))

    def test_shell_status_policy_pinned(self):
        self.assertIn("#define SH_ST_FAULT 129u", SH_C)
        self.assertIn("#define SH_ST_ABORT 130u", SH_C)
        self.assertIn("#define SH_ST_NOTFOUND 127u", SH_C)
        self.assertIn("#define SH_ST_MALFORMED 126u", SH_C)
        self.assertIn("#define SH_ST_SPAWNERR 125u", SH_C)
        self.assertIn("#define SH_ST_SYNTAX 2u", SH_C)

    def test_shell_bounds_pinned(self):
        self.assertIn("#define SH_LINE_MAX 256", SH_C)
        self.assertIn("#define SH_SCRIPT_MAX 4096", SH_C)
        self.assertIn("#define SHP_MAX_CMDS 2", SH_PARSE_H)
        self.assertIn("#define SHP_MAX_ARGS 8", SH_PARSE_H)
        self.assertIn("#define SHP_MAX_ARGBYTES 256", SH_PARSE_H)
        self.assertIn("#define SHP_MAX_WORD 64", SH_PARSE_H)
        self.assertIn("#define SHP_MAX_LINE 256", SH_PARSE_H)
        # Slice F frozen evaluator bounds (session/symbols/depth).
        rl_sem = (ROOT / "user/shell/rl_sem.h").read_text(encoding="utf-8")
        self.assertIn("#define RL_SESS_MAX 8192u", rl_sem)
        self.assertIn("#define RL_SYM_MAX 128u", rl_sem)
        self.assertIn("#define RL_DEPTH_MAX 64u",
                      (ROOT / "user/shell/rl_parse.h").read_text(
                          encoding="utf-8"))

    def test_parser_limits_match_uapi(self):
        self.assertEqual(number(SH_PARSE_H, "SHP_MAX_ARGS"),
                         number(UAPI, "UAPI_MAX_ARGC"))
        self.assertEqual(number(SH_PARSE_H, "SHP_MAX_ARGBYTES"),
                         number(UAPI, "UAPI_MAX_ARGV_BYTES"))
        self.assertIn("#define UAPI_PIPE_BUF 4096u", UAPI)

    def test_no_policy_syscalls(self):
        for token in ("SYS_SHELL", "SYS_EXEC", "SYS_SCRIPT", "SYS_COMMAND",
                      "SYS_STATUS", "SYS_PARSE"):
            self.assertNotIn(token, SYSCALL)

    def test_placement_no_kernel_shell(self):
        # The ring-0 monitor directory keeps exactly its frozen set.
        names = sorted(p.name for p in (ROOT / "kernel/shell").iterdir())
        self.assertEqual(names, ["shell-internal.h", "shell-test.c",
                                 "shell.c"])
        # No shell transcript rows or ASCII policy in ring 0 (E-M1/E-M16
        # tripwires: a kernel "[SH] " print or input translation table
        # fails here without any QEMU run).
        for path in list((ROOT / "kernel").rglob("*.c")) + \
                list((ROOT / "kernel").rglob("*.h")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("[SH] ", text, str(path))
        kbd = (ROOT / "kernel/drivers/keyboard.c").read_text(encoding="utf-8")
        self.assertNotIn("ascii", kbd)
        load = (ROOT / "kernel/core/load.c").read_text(encoding="utf-8")
        self.assertNotIn("ascii", load)
        # Slice F: no evaluator placement in ring 0 either (structural
        # rule over files and symbols, not exact filenames; `repl`
        # matches whole words only so ordinary words like the
        # pre-existing "replica" do not trip it).
        for path in list((ROOT / "kernel").rglob("*.c")) + \
                list((ROOT / "kernel").rglob("*.h")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("[RL] ", text, str(path))
            for token in ("rl_lex", "rl_parse", "rl_sem", "rl_eval",
                          "rl_submit", "lang_parse", "lang_check",
                          "lang_value"):
                self.assertNotIn(token, text, str(path))
            self.assertIsNone(
                re.search(r"(?<![A-Za-z0-9_])repl(?![A-Za-z0-9_])",
                          text),
                str(path))

    def test_shell_sources_present_and_evaluator_free(self):
        names = sorted(p.name for p in (ROOT / "user/shell").iterdir()
                       if p.name != ".gitkeep")
        # Slice F evaluator lives here (CPL3 only, see placement test).
        self.assertEqual(names, ["rl_eval.c", "rl_eval.h", "rl_lex.c",
                                 "rl_lex.h", "rl_mem.h", "rl_parse.c",
                                 "rl_parse.h", "rl_sem.c", "rl_sem.h",
                                 "sh.c", "sh_key.c", "sh_key.h",
                                 "sh_parse.c", "sh_parse.h"])
        blob = "\n".join((ROOT / "user/shell" / n).read_text(encoding="utf-8")
                         for n in names)
        self.assertIn("CPL3", blob)
        # Host compiler/analyzer must never leak into the guest image
        # (parity by independent implementation, never code sharing).
        for token in ("RIR", "analyze(", "compile.py",
                      "tools/rynorlang"):
            self.assertNotIn(token, blob, token)

    def test_boot_markers_defined(self):
        self.assertIn("[SHD] missing /bin/sh", SHD_C)
        self.assertIn("[SHD] malformed /bin/sh", SHD_C)
        self.assertIn("[SHD] halt code=", SHD_C)
        self.assertIn("[SHD] balanced", SHD_C)

    def test_keymap_covers_grammar(self):
        for token in ("{0x27, ';', ':'}", '{0x28,', "{0x2b, '\\\\', '|'}",
                      "{0x35, '/', '?'}", "{0x0c, '-', '_'}",
                      "{0x34, '.', '>'}"):
            self.assertIn(token, SH_KEY_C)

    def test_validators_accept_and_reject(self):
        good = (b"[SH] ready\r\n"
                b"[SH] prompt\r\n"
                b"[SH] done status=0\r\n"
                b"[SHD] halt code=0\r\n")
        self.assertEqual(validate_sh_section(good), [])
        bad = b"[SH] ready\r\n[SH] frobnicate 1\r\n"
        errors = validate_sh_section(bad)
        self.assertTrue(any("mismatch" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
