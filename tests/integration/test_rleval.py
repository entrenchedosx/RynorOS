"""Stage 18d Slice F: resident CPL3 RynorLang evaluator, session,
transactional commit, dual arenas, NOTIMPL, differential conformance.

Conventions (frozen by the Slice F audit):
- statuses: language syntax/semantic/NOTIMPL -> 2, eval trap -> 129,
  success -> 0; commands/pipelines/Ctrl-C follow Slice E exactly.
- rows: `[SH] error syntax` (lexer/parser), `[RL] reject <class>`
  (semantic/bounds), `[RL] notimpl <kw>`, `[RL] trap div0`,
  `[RL] error <class>` (resource), `[RL] ownership-fail`,
  `[RL] stats ...` (after evaluator submissions only; E paths print
  no new bytes, so Slice E transcripts are byte-identical).
- hex goldens use per-slot blobs (shell=0, single children=1,
  pipeline consumer=2): child output races shell markers in the
  full blob (Slice E lesson).
- scale tests (session/symbol caps, string stress, near-8K perf,
  50x leak walks) run through /bin/rltest, an in-guest driver that
  links the same rl_* sources and calls rl_submit directly: no
  keyboard session could type 8K/128-sym inputs inside a 60s boot.
  Keyboard tests pin integration; rltest pins scale. Both assert
  guest transcripts only; the host never computes answers.
"""
import re
import shutil
import struct
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT / "tools/rynorlang"))
sys.path.insert(0, str(ROOT))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from fs_image import build as fs_build
from tools.rynorlang import program as rynor_program
from tools.host import rnyx
from sh_output import (validate_sh_section, collect_sh_rows,
                       collect_load_writes, collect_done_statuses,
                       terminal_stream)
from test_cplshell import (_compile_shell, CTRL_C, BSPACE,
                           _slot_blob, _drive_entries,
                           pattern_upper, pattern_raw)
from tools.rynorlang import analyze as host_analyze
from test_cplshell import K as _EK

# Extended key map (Slice E names plus evaluator punctuation; all
# QEMU names probed live in F0 before use).
_FEXTRA = {"+": ("shift", "equal"), "!": ("shift", "1"),
           "%": ("shift", "5"), "=": "equal",
           ":": ("shift", "semicolon"),
           "{": ("shift", "bracket_left"),
           "}": ("shift", "bracket_right")}


def KF(text):
    out = []
    for ch in text:
        if ch in _FEXTRA:
            out.append(_FEXTRA[ch])
        else:
            out += _EK(ch)
    return out


def _compile_rltest(work, root=ROOT):
    """Build /bin/rltest from the (possibly mutated) tree."""
    sources = {}
    for name in ("rltest.c", "rl_lex.c", "rl_parse.c", "rl_sem.c",
                 "rl_eval.c"):
        sub = "proc-tests" if name == "rltest.c" else "shell"
        sources[name] = (root / "user" / sub / name).read_text(
            encoding="utf-8")
    for name in ("rl_lex.h", "rl_parse.h", "rl_sem.h", "rl_eval.h",
                 "rl_mem.h", "rt_pipe.h"):
        sub = "lib/rt" if name == "rt_pipe.h" else "shell"
        sources[name] = (root / "user" / sub / name).read_text(
            encoding="utf-8")
    progdir = Path(work) / "prog-rltest"
    progdir.mkdir(parents=True, exist_ok=True)
    arts, error = rynor_program.build_rynor_c_program(
        sources, progdir, prog="rltest", link_script="rynoros_v2.ld")
    assert error is None, ("rltest", error)
    return rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes(), version=2)


# Host reference commands overlapping /bin programs (accept/reject
# parity only; values differ real-vs-stub and are hand-pinned).
RL_COMMANDS = {"upper": (["str"], "str"), "echo": (["str"], "str"),
               "cat": (["str"], "str")}


def host_check(line, prelude=(), wrap=True):
    """Reference verdict for one REPL line: (ok, class-or-None).

    The host analyzes whole programs, so accepted prelude lines plus
    the candidate line are wrapped as fn-body statements (the REPL
    canonical form carries trailing-`;`-free lines; the wrapper adds
    them). `fn` definitions analyze at top level (wrap=False).
    `status` and bare commands are excluded by callers (guest-shell
    namespace, not language).
    """
    def stmt(s):
        # Block-tailed lines take no semicolon (host grammar).
        return s + "\n" if s.rstrip().endswith("}") else s + ";\n"
    if not wrap:
        prog = line + "\n"
    else:
        body = "".join(stmt(p) for p in prelude) + stmt(line)
        prog = "fn __s() {\n" + body + "}\n"
    try:
        res = host_analyze.analyze(prog, filename="<repl>",
                                   edition="shell",
                                   commands=RL_COMMANDS)
    except Exception as exc:  # defensive: reference must not hang us
        return (False, "HOST_%s" % type(exc).__name__)
    if res.ok:
        return (True, None)
    return (False, res.diagnostic.code)


class RlEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/rleval"
        cls.work.mkdir(parents=True, exist_ok=True)
        blobs = _compile_shell(cls.work)
        cls.blobs = blobs
        rltest = _compile_rltest(cls.work)
        cls.token = "tok-%s" % uuid.uuid4().hex[:8]
        entries = _drive_entries(blobs, cls.token)
        entries.append(("/bin/rltest", rltest))
        cls.image = cls.work / "rl.img"
        cls.image.write_bytes(fs_build(entries))
        cls.dest = cls.work / "image"
        build_image(ROOT, cls.dest, shell_boot=True)

    @classmethod
    def _boot(cls, keys, done, tag="boot", image=None):
        logs = cls.work / ("%s-%s" % (tag, len(list(cls.work.glob(tag + "-*")))))
        return boot_image(cls.dest / "rynoros.img", logs,
                          timeout=60, extra_drives=(image or cls.image,),
                          require_sh=True, sh_keys=tuple(keys),
                          sh_done=done)

    def _rl_rows(self, out):
        return [m.group(0).decode("ascii", "replace")
                for m in re.finditer(rb"\[(RL|RLT)\] [^\r\n]*",
                                     terminal_stream(out))]

    def _rl_stats(self, out):
        stats = []
        for row in self._rl_rows(out):
            m = re.fullmatch(
                r"\[RL\] stats sess_live=(\d+) sess_high=(\d+) "
                r"sub_live=(\d+) sub_high=(\d+) syms=(\d+) src=(\d+)",
                row)
            if m:
                stats.append({k: int(v) for k, v in zip(
                    ("sess_live", "sess_high", "sub_live",
                     "sub_high", "syms", "src"), m.groups())})
        return stats

    def _chunks(self, out, nparts):
        """Split the terminal stream per submitted part (done rows)."""
        parts = re.split(rb"\[SH\] done status=\d+\r\n",
                         terminal_stream(out))
        self.assertEqual(len(parts), nparts + 1, parts[-1][:200])
        return parts[:-1]

    # ---- F0: placement, size, independence ----

    def test_f0_keyprobe_new_chars(self):
        # Every evaluator punctuation key must deliver+decode (echo
        # proves it); the line itself is E-syntax (done 2), then the
        # token echo closes the boot.
        keys = KF("!%=+{}\n") + KF("echo KP-DONE-1a\n")
        out = self._boot(keys, b"KP-DONE-1a", tag="f0keys")
        self.assertEqual(validate_sh_section(out), [])
        stream = terminal_stream(out)
        for ch in (b"!", b"%", b"=", b"+", b"{", b"}"):
            self.assertIn(ch, stream)
        self.assertEqual(collect_done_statuses(out), [2, 0])

    def test_f0_shell_fits_v2(self):
        code, fsz, msz = struct.unpack("<III", self.blobs["sh"][16:28])
        # Amended RYNX v2 caps (64K code / 32K data) with a pinned 1K
        # data headroom so growth fails fast instead of at the cap.
        self.assertLessEqual(code, 65536)
        self.assertLessEqual(msz, 32768)
        self.assertLessEqual(msz, 32768 - 1024)
        self.assertGreater(code, 0)
        # Actuals for the report (informational, never weakened).
        print("shell code=%d data_memsz=%d" % (code, msz))

    def test_f0_no_host_runtime(self):
        # No host compiler/analyzer source may reach the guest image:
        # parity is by independent implementation, never code sharing.
        names = ("rl_lex.c", "rl_parse.c", "rl_sem.c", "rl_eval.c",
                 "rl_lex.h", "rl_parse.h", "rl_sem.h", "rl_eval.h",
                 "rl_mem.h", "sh.c")
        blob = "\n".join((ROOT / "user/shell" / n).read_text(
            encoding="utf-8") for n in names)
        for token in ("tools/rynorlang", "analyze(", "compile.py",
                      "RIR", "interp.py"):
            self.assertNotIn(token, blob, token)
        # rltest helper is guest-side too.
        rltest = (ROOT / "user/proc-tests/rltest.c").read_text(
            encoding="utf-8")
        for token in ("tools/rynorlang", "analyze(", "compile.py",
                      "RIR"):
            self.assertNotIn(token, rltest, token)

    def test_f0_no_compiler_link(self):
        # The shell image links no compiler/RIR machinery: the only
        # evaluator entry points are the rl_* resident routines.
        names = ("rl_lex.c", "rl_parse.c", "rl_sem.c", "rl_eval.c",
                 "sh.c")
        blob = "\n".join((ROOT / "user/shell" / n).read_text(
            encoding="utf-8") for n in names)
        for token in ("rir_", "compile_", "codegen", "bytecode",
                      "jit", "fn_execute", "block_execute"):
            self.assertNotIn(token, blob, token)

    # ---- F1: literals ----

    def test_f1_literals_values(self):
        # Golden rule (throughout this module): numeric goldens need
        # >=4 characters or letters. Shorter digit strings occur
        # inside [SH] wantkey/done rows, so they prove nothing.
        keys = []
        keys += KF("4242\n")
        keys += KF('"hi"\n')
        keys += KF("true\n")
        keys += KF("false\n")
        keys += KF("1000\n")
        keys += KF("0007000\n")
        keys += KF('"a\\nb"\n')
        keys += KF("9223372036854775807\n")
        keys += KF("echo F1-DONE-2a\n")
        out = self._boot(keys, b"F1-DONE-2a", tag="f1lit")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [0] * 8 + [0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        for golden in (b"4242", b"hi", b"true", b"false", b"1000",
                       b"7000", b"a\nb", b"9223372036854775807"):
            self.assertIn(golden.hex(), kids)

    def test_f1_literal_rejections(self):
        keys = []
        keys += KF("9223372036854775808\n")  # int overflow: E word->127
        keys += KF('"abc\n')  # unterminated
        keys += KF('"bad\\q"\n')  # bad escape
        keys += KF("1.5\n")  # dotted text: E command attempt -> 127
        keys += KF("echo F1-DONE-2b\n")
        out = self._boot(keys, b"F1-DONE-2b", tag="f1rej")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        # Overflowing digits lex-fail into the E fallback (bare-word
        # spawn, NOTFOUND); quote failures are E syntax; dotted text
        # is a bare-word spawn attempt by frozen E behavior.
        self.assertEqual(collect_done_statuses(out), [127, 2, 2, 127, 0])
        self.assertEqual(rows.count("[SH] error syntax"), 2)

    # ---- F2: expressions ----

    def test_f2a_precedence_assoc(self):
        keys = []
        keys += KF("1000 + 2000 * 30\n")  # 61000
        keys += KF("(1000 + 2000) * 30\n")  # 90000
        keys += KF("10000 - 3000 - 2000\n")  # 5000, left assoc
        keys += KF("100000 / 10 / 2\n")  # 5000
        keys += KF("- -5500\n")  # 5500
        keys += KF("2000 * 30 % 7000\n")  # 4000
        keys += KF("-200 * 30\n")  # -6000
        keys += KF("echo F2A-DONE-3a\n")
        out = self._boot(keys, b"F2A-DONE-3a", tag="f2a")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0] * 7 + [0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        for golden in (b"61000", b"90000", b"5000", b"5000", b"5500",
                       b"4000", b"-6000"):
            self.assertIn(golden.hex(), kids)

    def test_f2b_division_traps(self):
        # Split for the per-boot key budget (QEMU int-trace cap).
        keys = []
        keys += KF("7000 % 3000\n")  # 1000
        keys += KF("-5000 % 3000\n")  # -2000, dividend-signed
        keys += KF("1 / 0\n")  # trap
        keys += KF("5 % 0\n")  # trap
        keys += KF("echo F2B-DONE-3b\n")
        out = self._boot(keys, b"F2B-DONE-3b", tag="f2b1")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0, 129, 129, 0])
        stream = terminal_stream(out)
        self.assertEqual(stream.count(b"[RL] trap div0"), 2)
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        self.assertIn(b"1000".hex(), kids)
        self.assertIn(b"-2000".hex(), kids)

    def test_f2b_overflow_edges(self):
        keys = []
        keys += KF("(-9223372036854775807 - 1) / -1\n")  # INT_MIN trap
        keys += KF("9223372036854775807 + 1\n")  # wraps to INT_MIN
        keys += KF("-9223372036854775807 - 1\n")  # INT_MIN, no trap
        keys += KF("3037000500 * 3037000501\n")  # wraps (host-computed)
        keys += KF("echo F2B-DONE-3c\n")
        out = self._boot(keys, b"F2B-DONE-3c", tag="f2b2")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [129, 0, 0, 0, 0])
        stream = terminal_stream(out)
        self.assertEqual(stream.count(b"[RL] trap div0"), 1)
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        self.assertIn(b"-9223372036854775808".hex(), kids)
        wrap = (3037000500 * 3037000501) & ((1 << 64) - 1)
        if wrap >= (1 << 63):
            wrap -= 1 << 64
        self.assertIn(str(wrap).encode().hex(), kids)

    def test_f2c_comparisons_logic(self):
        keys = []
        keys += KF("1 < 2 == true\n")  # true
        keys += KF("true || false && false\n")  # true (&& tighter)
        keys += KF("2 == 2 == true\n")  # true
        keys += KF('"a" == "a"\n')  # true
        keys += KF('"a" != "b"\n')  # true
        keys += KF("!true\n")  # false
        keys += KF("1 + true\n")  # type error
        keys += KF('"a" + "b"\n')  # type error (no str concat)
        keys += KF('1 < "a"\n')  # type error
        keys += KF("1 && true\n")  # type error
        keys += KF("echo F2C-DONE-3c\n")
        out = self._boot(keys, b"F2C-DONE-3c", tag="f2c")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [0] * 6 + [2] * 4 + [0])
        rows = self._rl_rows(out)
        self.assertEqual(rows.count("[RL] reject SEM_TYPE_MISMATCH"), 4)

    # ---- F3: lets and session ----

    def test_f3_first_let_lookup(self):
        keys = []
        keys += KF("let x: int = 1000\n")
        keys += KF("x + 5\n")  # 1005
        keys += KF("let y: bool = true\n")
        keys += KF("print(y)\n")  # true
        keys += KF('let s: str = "hi"\n')
        keys += KF("print(s)\n")  # hi
        keys += KF('s == "hi"\n')  # true
        keys += KF("echo F3-DONE-4a\n")
        out = self._boot(keys, b"F3-DONE-4a", tag="f3a")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0] * 7 + [0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        for golden in (b"1005", b"true", b"hi", b"true"):
            self.assertIn(golden.hex(), kids)
        stats = self._rl_stats(out)
        self.assertEqual(stats[-2]["syms"], 3)
        for st in stats:
            self.assertEqual(st["sub_live"], 0)

    def test_f3_duplicates_rollback(self):
        keys = []
        keys += KF("let x: int = 1000\n")
        keys += KF("let x: int = 2\n")  # duplicate: reject, x intact
        keys += KF("x\n")  # 1000
        keys += KF("let y: int = nosuch\n")  # undeclared init: reject
        keys += KF("y\n")  # undeclared lone word: E command -> 127
        keys += KF("let z: int = true\n")  # type mismatch: reject
        keys += KF("z\n")  # E command -> 127
        keys += KF("let q: int = q\n")  # self-ref: undeclared
        keys += KF("let y: int = 5000\n")  # y was never registered
        keys += KF("y\n")  # 5000
        keys += KF("echo F3-DONE-4b\n")
        out = self._boot(keys, b"F3-DONE-4b", tag="f3b")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [0, 2, 0, 2, 127, 2, 127, 2, 0, 0, 0])
        rows = self._rl_rows(out)
        self.assertIn("[RL] reject SEM_DUPLICATE", rows)
        self.assertEqual(rows.count("[RL] reject SEM_UNDECLARED"), 2)
        self.assertIn("[RL] reject SEM_TYPE_MISMATCH", rows)
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        self.assertIn(b"1000".hex(), kids)
        self.assertIn(b"5000".hex(), kids)
        stats = self._rl_stats(out)
        # Failed names never registered (y commits cleanly after);
        # every prompt reset the submission arena.
        self.assertEqual(len(stats), 8)
        for st in stats[:6]:
            self.assertEqual(st["syms"], 1)
        self.assertEqual(stats[-2]["syms"], 2)
        self.assertEqual(stats[-2]["src"], len(b"let x: int = 1000\n"
                                                b"let y: int = 5000\n"))
        for st in stats:
            self.assertEqual(st["sub_live"], 0)

    def test_f3_failed_let_prints_nothing(self):
        # A rejected let emits no value bytes (eval never runs).
        keys = []
        keys += KF('let w: int = print("aaa")\n')  # unit init: reject
        keys += KF("echo F3-DONE-4c\n")
        out = self._boot(keys, b"F3-DONE-4c", tag="f3c")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [2, 0])
        stream = terminal_stream(out)
        self.assertNotIn(b"aaa", stream)

    def test_f3_semicolon_sequence(self):
        keys = []
        keys += KF("let s1: int = 1; s1 + 1000\n")
        keys += KF("echo F3-DONE-4d\n")
        out = self._boot(keys, b"F3-DONE-4d", tag="f3d")
        self.assertEqual(validate_sh_section(out), [])
        # Two parts, two dones: commit then evaluate.
        self.assertEqual(collect_done_statuses(out), [0, 0, 0])
        writes = collect_load_writes(out)
        self.assertIn(b"1001".hex(), _slot_blob(writes, 0))

    def test_f3_determinism_twins(self):
        # Session A: straight successes.
        akeys = KF("let a: int = 1000\n") + KF("let b: int = 2000\n") + \
            KF("a + b\n") + KF("echo TWINA-DONE-4e\n")
        aout = self._boot(akeys, b"TWINA-DONE-4e", tag="f3twina")
        # Session B: same final bytes via a rejected detour.
        bkeys = KF("let a: int = 1000\n") + KF("let a: int = 2\n") + \
            KF("let b: int = 2000\n") + KF("a + b\n") + \
            KF("echo TWINB-DONE-4e\n")
        bout = self._boot(bkeys, b"TWINB-DONE-4e", tag="f3twinb")
        for out in (aout, bout):
            self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(aout), [0, 0, 0, 0])
        self.assertEqual(collect_done_statuses(bout),
                         [0, 2, 0, 0, 0])
        astats = self._rl_stats(aout)
        bstats = self._rl_stats(bout)
        # Same accepted bytes, symbols, and observable output: the
        # rejected duplicate left zero semantic effect.
        self.assertEqual(astats[-1]["src"], bstats[-1]["src"])
        self.assertEqual(astats[-1]["syms"], bstats[-1]["syms"])
        self.assertEqual(astats[-1]["src"], len(b"let a: int = 1000\n"
                                                b"let b: int = 2000\n"))
        for blob in (terminal_stream(aout), terminal_stream(bout)):
            self.assertIn(b"3000", blob)

    # ---- F4: limits (keyboard-feasible boundaries) ----

    def test_f4_depth_smoke(self):
        # Deep nesting through the full keyboard path (40-deep;
        # exact 63/64/65 boundaries run in-guest via rltest, since a
        # 65-deep line alone exceeds the per-boot key budget).
        keys = []
        keys += KF("(" * 40 + "1000" + ")" * 40 + "\n")
        keys += KF("echo F4-DONE-5a\n")
        out = self._boot(keys, b"F4-DONE-5a", tag="f4depth")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        self.assertIn(b"1000".hex(), kids)
        stats = self._rl_stats(out)
        for st in stats:
            self.assertEqual(st["syms"], 0)
            self.assertEqual(st["sub_live"], 0)

    # ---- F5: arenas, reset, ownership ----

    def test_f5_success_reset_stats(self):
        keys = []
        keys += KF("1000 + 2000\n")
        keys += KF("echo F5-DONE-6a\n")
        out = self._boot(keys, b"F5-DONE-6a", tag="f5a")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0])
        stats = self._rl_stats(out)
        self.assertEqual(len(stats), 1)
        st = stats[0]
        self.assertEqual(st["syms"], 0)
        self.assertEqual(st["src"], 0)
        self.assertEqual(st["sub_live"], 0)
        # Submission high-water stays inside the arena envelope.
        self.assertLessEqual(st["sub_high"], 6688)
        writes = collect_load_writes(out)
        self.assertIn(b"3000".hex(), _slot_blob(writes, 0))

    def test_f5_failure_resets_stats(self):
        keys = []
        keys += KF("let broken\n")  # syntax (let-head claims it)
        keys += KF("nosuchvar + 1000\n")  # semantic
        keys += KF("if stillhere\n")  # NOTIMPL
        keys += KF("echo F5-DONE-6b\n")
        out = self._boot(keys, b"F5-DONE-6b", tag="f5b")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [2, 2, 2, 0])
        rows = self._rl_rows(out)
        self.assertIn("[RL] reject SEM_UNDECLARED", rows)
        self.assertIn("[RL] notimpl if", rows)
        self.assertEqual(collect_sh_rows(out).count("[SH] error syntax"),
                         1)
        stats = self._rl_stats(out)
        self.assertEqual(len(stats), 3)
        for st in stats:
            self.assertEqual(st["syms"], 0)
            self.assertEqual(st["src"], 0)
            self.assertEqual(st["sess_live"], 0)
            self.assertEqual(st["sub_live"], 0)

    def test_f5_command_ctrlc_reset_session(self):
        keys = []
        keys += KF("let q9: int = 7000\n")
        keys += KF("nope\n")  # command failure, then continuity
        keys += KF("spin\n")
        keys += [CTRL_C]
        keys += KF("q9 + 1000\n")  # 8000: session survived the abort
        keys += KF("echo F5-DONE-6c\n")
        out = self._boot(keys, b"F5-DONE-6c", tag="f5c")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        # Aborts emit abort rows, not dones.
        self.assertEqual(collect_done_statuses(out), [0, 127, 0, 0])
        self.assertIn("[SH] abort child status=130", rows)
        writes = collect_load_writes(out)
        self.assertIn(b"8000".hex(), _slot_blob(writes, 0))
        stats = self._rl_stats(out)
        # Stats rows only for evaluator parts (let, q9-expr).
        self.assertEqual(len(stats), 2)
        self.assertEqual(stats[-1]["syms"], 1)
        self.assertEqual(stats[-1]["sub_live"], 0)

    def test_f5_string_ownership_keyboard(self):
        keys = []
        keys += KF('let s: str = "persistent-value"\n')
        keys += KF("nosuch + 1\n")
        keys += KF('let s: str = "other"\n')
        keys += KF("print(s)\n")
        keys += KF("1 + 2 * 30\n")
        keys += KF("print(s)\n")
        keys += KF("echo F5-DONE-6d\n")
        out = self._boot(keys, b"F5-DONE-6d", tag="f5d")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [0, 2, 2, 0, 0, 0, 0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        # Exact original bytes twice, failed submits between.
        self.assertEqual(kids.count(b"persistent-value".hex()), 2)
        self.assertNotIn(b"other".hex(), kids)
        stream = terminal_stream(out)
        self.assertNotIn(b"[RL] ownership-fail", stream)

    # ---- F6: NOTIMPL ----

    def test_f6_notimpl_all(self):
        keys = []
        keys += KF("let x: int = 1000\n")
        keys += KF("fn f() : int { return 1; }\n")
        keys += KF("if x\n")
        keys += KF("while x\n")
        keys += KF("return x\n")
        keys += KF("{ x; }\n")
        keys += KF("x\n")  # 1000: x intact, nothing committed
        keys += KF("echo F6-DONE-7a\n")
        out = self._boot(keys, b"F6-DONE-7a", tag="f6")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [0, 2, 2, 2, 2, 2, 0, 0])
        rows = self._rl_rows(out)
        for kw in ("fn", "if", "while", "return", "block"):
            self.assertIn("[RL] notimpl " + kw, rows)
        writes = collect_load_writes(out)
        self.assertIn(b"1000".hex(), _slot_blob(writes, 0))
        stats = self._rl_stats(out)
        for st in stats:
            self.assertEqual(st["syms"], 1)
            self.assertEqual(st["sub_live"], 0)

    # ---- F7: command integration ----

    def test_f7_commands_basic_status(self):
        keys = []
        keys += KF("echo hello\n")
        keys += KF("echo a b\n")
        keys += KF("nope\n")
        keys += KF("status\n")  # 127: last failure cached
        keys += KF("exitcode 5\n")
        keys += KF("status\n")  # 5
        keys += KF('echo "a|>b"\n')  # literal pipe, not a pipeline
        keys += KF("echo F7-DONE-8a\n")
        out = self._boot(keys, b"F7-DONE-8a", tag="f7a")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [0, 0, 127, 127, 5, 5, 0, 0])
        writes = collect_load_writes(out)
        # status values ride the done rows (numeric slot-0 goldens
        # would collide with wantkey numbers); byte outputs pinned
        # on the child slot (single-writer subsequence: the
        # shell/child serial race cannot split them there).
        self.assertIn(b"hello\n".hex(), _slot_blob(writes, 1))
        self.assertIn(b"a b\n".hex(), _slot_blob(writes, 1))
        self.assertIn(b"a|>b\n".hex(), _slot_blob(writes, 1))

    def test_f7_pipeline_stream_big(self):
        keys = []
        keys += KF("prod 256 0 |> upper\n")
        keys += KF("prod 8192 0 |> upper\n")
        keys += KF("echo F7-DONE-8b\n")
        out = self._boot(keys, b"F7-DONE-8b", tag="f7b")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        self.assertEqual(collect_done_statuses(out), [0, 0, 0])
        self.assertIn("[SH] overlap 1", rows)
        writes = collect_load_writes(out)
        self.assertIn(pattern_upper(8192).hex(), _slot_blob(writes, 2))
        self.assertIn(pattern_upper(256).hex(), _slot_blob(writes, 2))
        blob = "".join(h for _, h in writes)
        self.assertNotIn(pattern_raw(8192).hex(), blob)

    def test_f7_aborts_ctrlc(self):
        keys = []
        keys += KF("let q9: int = 7000\n")
        keys += KF("spin\n")
        keys += [CTRL_C]
        keys += KF("spin |> cat\n")
        keys += [CTRL_C]
        keys += KF("q9\n")  # 7000: session survived both aborts
        keys += KF("echo F7-DONE-8c\n")
        out = self._boot(keys, b"F7-DONE-8c", tag="f7c")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        self.assertEqual(collect_done_statuses(out), [0, 0, 0])
        self.assertIn("[SH] abort child status=130", rows)
        self.assertIn("[SH] abort pipeline status=130", rows)
        writes = collect_load_writes(out)
        self.assertIn(b"7000".hex(), _slot_blob(writes, 0))

    # ---- F8: differential conformance vs host analyzer ----
    #
    # Each fixture runs one line in a shared session (failures never
    # pollute: transactional rollback), segmented per done row. The
    # host analyzes the fn-wrapped equivalent with edition=shell.
    # Parity classes: FULL (same accept + same class) or DOCUMENTED
    # (recorded divergence with frozen rationale: E discovery wins,
    # no capture channel, NOTIMPL-by-design, static-vs-dynamic).
    # Byte goldens cover shell-emitted values only (single [LOAD]
    # rows, never split); child multi-write output races shell
    # markers in the full stream, so command bytes live in F7 slot
    # blobs instead.

    def _check_fixtures(self, out, fixtures, prelude_dones=0,
                        prelude_lines=()):
        # One trailing echo-done boot marker rides every corpus boot.
        chunks = self._chunks(out, len(fixtures) + prelude_dones + 1)
        chunks = chunks[prelude_dones:prelude_dones + len(fixtures)]
        self.assertEqual(len(chunks), len(fixtures))
        for chunk, fix in zip(chunks, fixtures):
            if len(fix) == 8:
                line, h_ok, h_code, g_ok, g_class, g_bytes, parity, \
                    nowrap = fix
            else:
                line, h_ok, h_code, g_ok, g_class, g_bytes, parity = fix
                nowrap = False
            host = host_check(line, prelude_lines, wrap=not nowrap)
            self.assertEqual(host[0], h_ok, (line, host))
            if h_code is not None:
                self.assertEqual(host[1], h_code, (line, host))
            has_syntax = b"[SH] error syntax" in chunk
            m = re.search(rb"\[RL\] (reject ([A-Z_]+)|notimpl|trap div0)",
                          chunk)
            if g_ok:
                self.assertNotIn(b"[SH] error syntax", chunk, line)
                self.assertIsNone(m, (line, chunk[-160:]))
            else:
                if g_class == "E-SYNTAX":
                    self.assertTrue(has_syntax, line)
                elif g_class == "E-CMD":
                    self.assertFalse(has_syntax, line)
                    self.assertIsNone(m, (line, chunk[-160:]))
                else:
                    self.assertIsNotNone(m, (line, chunk[-160:]))
                    got = m.group(2).decode() if m.group(2) else (
                        "RL_NOTIMPL" if b"notimpl" in m.group(0)
                        else "RL_EVAL_TRAP")
                    self.assertEqual(got, g_class, (line, chunk[-160:]))
            if g_bytes is not None:
                # Raw stream bytes (row boundaries vanish in the
                # decoded concatenation; single-write outputs and
                # same-writer runs stay contiguous).
                self.assertIn(g_bytes, chunk, line)
            self.assertIn(parity, ("FULL", "DOCUMENTED"))

    def test_f8_differential_pure_a(self):
        # Split for the per-boot key budget (QEMU int-trace cap).
        fx = [
            ("1000 + 2000", True, None, True, None, b"3000", "FULL"),
            ("10 - 3 - 1020", True, None, True, None, b"-1013",
             "FULL"),
            ("20 * 30 + 4000", True, None, True, None, b"4600",
             "FULL"),
            ("true && false", True, None, True, None, b"false",
             "FULL"),
            ("!false", True, None, True, None, b"true", "FULL"),
            ('"ab" == "ab"', True, None, True, None, b"true", "FULL"),
            ("1 + true", False, "SEM_TYPE_MISMATCH", False,
             "SEM_TYPE_MISMATCH", None, "FULL"),
            ('"a" + "b"', False, "SEM_TYPE_MISMATCH", False,
             "SEM_TYPE_MISMATCH", None, "FULL"),
        ]
        keys = []
        for line, *_ in fx:
            keys += KF(line + "\n")
        keys += KF("echo F8A-DONE-9a\n")
        out = self._boot(keys, b"F8A-DONE-9a", tag="f8a")
        self.assertEqual(validate_sh_section(out), [])
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 0, 0, 0, 0, 0, 2, 2, 0])
        self._check_fixtures(out, fx)

    def test_f8_differential_pure_b(self):
        fx = [
            ("1000 < 2000", True, None, True, None, b"true", "FULL"),
            ("1 / 0", True, None, False, "RL_EVAL_TRAP", None,
             "DOCUMENTED"),
            ("nosuchvar", False, "SHELL_UNKNOWN_COMMAND", False,
             "E-CMD", None, "DOCUMENTED"),
            ("9223372036854775808", False, "PAR_LEX_ERROR", False,
             "E-CMD", None, "DOCUMENTED"),
            ('"unterminated', False, "PAR_LEX_ERROR", False,
             "E-SYNTAX", None, "DOCUMENTED"),
            ("if true { }", True, None, False, "RL_NOTIMPL", None,
             "DOCUMENTED"),
            ("fn f() : int { return 1; }", True, None, False,
             "RL_NOTIMPL", None, "DOCUMENTED", True),
            ("while true { }", True, None, False, "RL_NOTIMPL", None,
             "DOCUMENTED"),
        ]
        keys = []
        for line, *_ in fx:
            keys += KF(line + "\n")
        keys += KF("echo F8D-DONE-9d\n")
        out = self._boot(keys, b"F8D-DONE-9d", tag="f8d")
        self.assertEqual(validate_sh_section(out), [])
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 129, 127, 127, 2, 2, 2, 2, 0])
        self._check_fixtures(out, fx)

    _F8_PRELUDE_SRC = ("let q9: int = 7000", 'let s9: str = "abc"')

    def _f8_prelude(self):
        return (KF("let q9: int = 7000\n") +
                KF('let s9: str = "abc"\n'))

    def test_f8_differential_session_a(self):
        # Split for the per-boot key budget (QEMU int-trace cap).
        fx = [
            ("q9 + 1000", True, None, True, None, b"8000", "FULL"),
            ("let q9: int = 1", False, "SEM_DUPLICATE", False,
             "SEM_DUPLICATE", None, "FULL"),
            ("let q9: bool = true", False, "SEM_DUPLICATE", False,
             "SEM_DUPLICATE", None, "FULL"),
            ('s9 == "abc"', True, None, True, None, b"true", "FULL"),
            ("print(q9)", True, None, True, None, b"7000", "FULL"),
            ("print(nosuch)", False, "SHELL_UNKNOWN_COMMAND",
             False, "SEM_UNDECLARED", None, "DOCUMENTED"),
        ]
        keys = self._f8_prelude()
        for line, *_ in fx:
            keys += KF(line + "\n")
        keys += KF("echo F8E-DONE-9e\n")
        out = self._boot(keys, b"F8E-DONE-9e", tag="f8e")
        self.assertEqual(validate_sh_section(out), [])
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 0, 0, 2, 2, 0, 0, 2, 0])
        self._check_fixtures(out, fx, prelude_dones=2, prelude_lines=self._F8_PRELUDE_SRC)

    def test_f8_differential_session_b(self):
        fx = [
            ("q9 + s9", False, "SEM_TYPE_MISMATCH", False,
             "SEM_TYPE_MISMATCH", None, "FULL"),
            ("let r9: str = q9", False, "SEM_TYPE_MISMATCH", False,
             "SEM_TYPE_MISMATCH", None, "FULL"),
            ("let print: int = 1", True, None, False,
             "SEM_DUPLICATE", None, "DOCUMENTED"),
            ("q9", True, None, True, None, b"7000", "FULL"),
            ("s9", True, None, True, None, b"abc", "FULL"),
        ]
        keys = self._f8_prelude()
        for line, *_ in fx:
            keys += KF(line + "\n")
        keys += KF("echo F8F-DONE-9f\n")
        out = self._boot(keys, b"F8F-DONE-9f", tag="f8f")
        self.assertEqual(validate_sh_section(out), [])
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 0, 2, 2, 2, 0, 0, 0])
        self._check_fixtures(out, fx, prelude_dones=2,
                             prelude_lines=self._F8_PRELUDE_SRC)

    def test_f8_differential_commands(self):
        fx = [
            ('upper "hello"', True, None, True, "E-CMD", None,
             "DOCUMENTED"),
            ("echo hello", False, "SEM_UNDECLARED", True, "E-CMD",
             None, "DOCUMENTED"),
            ("cat", False, "SHELL_COMMAND_ARITY", True, "E-CMD", None,
             "DOCUMENTED"),
            ("ls", False, "SHELL_UNKNOWN_COMMAND", False, "E-CMD",
             None, "DOCUMENTED"),
            ('frobnicate "hi"', False, "SHELL_UNKNOWN_COMMAND", False,
             "E-CMD", None, "DOCUMENTED"),
            ('take "hello" 2', False, "SHELL_UNKNOWN_COMMAND", False,
             "E-CMD", None, "DOCUMENTED"),
            ('upper "a" > "f"', True, None, False, "E-SYNTAX", None,
             "DOCUMENTED"),
            ("echo a b", False, "SEM_UNDECLARED", True, "E-CMD",
             None, "DOCUMENTED"),
            ("upper 65", False, "SHELL_COMMAND_TYPE_MISMATCH", True,
             "E-CMD", None, "DOCUMENTED"),
            ("nope", False, "SHELL_UNKNOWN_COMMAND", False, "E-CMD",
             None, "DOCUMENTED"),
        ]
        keys = []
        for line, *_ in fx:
            keys += KF(line + "\n")
        keys += KF("echo F8C-DONE-9c\n")
        out = self._boot(keys, b"F8C-DONE-9c", tag="f8c")
        self.assertEqual(validate_sh_section(out), [])
        dones = collect_done_statuses(out)
        # upper(empty ok), echo ok, cat ok, ls 127, frobnicate 127,
        # take 127, redirect syntax 2, echo-ok, upper-ok, nope 127.
        self.assertEqual(dones, [0, 0, 0, 127, 127, 127, 2, 0, 0, 127,
                                 0])
        self._check_fixtures(out, fx)
        # Anti-stub pin: real /bin/upper ignores argv (stdin-driven),
        # so no stub-computed HELLO may ever appear.
        self.assertNotIn(b"HELLO", terminal_stream(out))

    # ---- F9: rltest scale runs ----

    def _rltest_boot(self, runs, tag):
        keys = []
        for cmd, args in runs:
            keys += KF("rltest %s%s\n" % (cmd, args))
        keys += KF("echo RLTEST-DONE-%s\n" % tag.upper())
        return self._boot(keys, ("RLTEST-DONE-%s" % tag.upper()).encode(),
                          tag="f9" + tag)

    def _rltest_solo(self, cmd, args, tag):
        out = self._rltest_boot([(cmd, args)], tag)
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0])
        stream = terminal_stream(out)
        self.assertIn(b"[RLT] done " + cmd.encode() + b" pass", stream)
        self.assertNotIn(b"FAIL", stream)
        return out

    def test_f9_rltest_session_8191(self):
        # Solo boots: QEMU int-trace caps total runtime (~12s), so
        # each scale run gets its own boot.
        self._rltest_solo("cap-session", " 8191", "s8191")

    def test_f9_rltest_session_8192(self):
        self._rltest_solo("cap-session", " 8192", "s8192")

    def test_f9_rltest_session_8193(self):
        self._rltest_solo("cap-session", " 8193", "s8193")

    def test_f9_rltest_str_life(self):
        self._rltest_solo("str-life", "", "strlife")

    def test_f9_rltest_perf(self):
        self._rltest_solo("perf", "", "perf")

    def test_f9_rltest_depth(self):
        self._rltest_solo("depth", "", "depth")

    def test_f9_rltest_leak(self):
        self._rltest_solo("leak", "", "leak")

    # ---- Slice F mutants (each executed RED, restored, green-rerun) ----

    def _fmutant_boot(self, name, edits, keys, done, burst=(),
                      image_kind="good", shell_script=None,
                      expect_timeout=False):
        """Evaluator mutant run: copy the tree, apply source edits,
        rebuild kernel + shell + rltest images, boot. The working
        tree is never mutated (restored by fixture deletion)."""
        with tempfile.TemporaryDirectory(prefix="rl-fault-",
                                         dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            for source, old, new, count in edits:
                path = root / source
                contents = path.read_text(encoding="utf-8")
                self.assertEqual(contents.count(old), count,
                                 (name, source))
                path.write_text(contents.replace(old, new),
                                encoding="utf-8")
            from image import build_image as _bi
            dest = root / "build" / "img"
            _bi(root, dest, shell_boot=True, shell_script=shell_script)
            if any(source.startswith("user/")
                   for source, _, _, _ in edits):
                blobs = _compile_shell(root / "build" / "progs",
                                       root=root)
                rlt = _compile_rltest(root / "build" / "progs-rl",
                                      root=root)
                entries = _drive_entries(blobs, self.token)
                entries.append(("/bin/rltest", rlt))
                if image_kind == "nosh":
                    entries = [(p, c) for (p, c) in entries
                               if p != "/bin/sh"]
                image = root / "drive.img"
                image.write_bytes(fs_build(entries))
            else:
                image = self.image
            logs = ROOT / "build/rleval" / name
            if expect_timeout:
                with self.assertRaises(RuntimeError):
                    boot_image(dest / "rynoros.img", logs, timeout=60,
                               extra_drives=(image,), require_sh=True,
                               sh_keys=tuple(keys),
                               sh_burst=tuple(burst), sh_done=done)
                return None
            return boot_image(dest / "rynoros.img", logs, timeout=60,
                              extra_drives=(image,), require_sh=True,
                              sh_keys=tuple(keys),
                              sh_burst=tuple(burst), sh_done=done)

    def _rltest_mutant(self, name, edits, cmd, args=""):
        """Run one rltest subcommand on a mutant image; return output
        (boot completes; rltest itself reports pass/FAIL + exit)."""
        keys = KF("rltest %s%s\n" % (cmd, args))
        keys += KF("echo RLM-DONE\n")
        return self._fmutant_boot(name, edits, keys, b"RLM-DONE")

    def test_mutant_history_state_goes_red(self):
        """F-M1: symbol values depend on submission history instead of
        rebuilt candidate state (commit adds a submission counter)."""
        out = self._fmutant_boot(
            "hist-state",
            [("user/shell/rl_sem.c",
              "static struct rl_sess rl_sess;",
              "static struct rl_sess rl_sess;\n"
              "static unsigned long long rl_hist;",
              1),
             ("user/shell/rl_sem.c",
              "    /* No reset here by design: every completion path below resets,\n"
              "     * so a skipped reset (F-M4/F-M5 shape) accumulates observably\n"
              "     * instead of being masked at the next start. */\n"
              "    if (!rl_carve(&cv)) {",
              "    /* No reset here by design: every completion path below resets,\n"
              "     * so a skipped reset (F-M4/F-M5 shape) accumulates observably\n"
              "     * instead of being masked at the next start. */\n"
              "    ++rl_hist;\n"
              "    if (!rl_carve(&cv)) {",
              1),
             ("user/shell/rl_sem.c",
              "                row->pay = ev.a;\n"
              "            } else if (dt == RLV_BOOL) {",
              "                row->pay = ev.a + rl_hist;\n"
              "            } else if (dt == RLV_BOOL) {",
              1)],
            keys=KF("let a: int = 1000\n") + KF("let a: int = 2\n") +
                 KF("let b: int = 2000\n") + KF("a + b\n") +
                 KF("echo finm1\n"),
            done=b"finm1")
        # Statuses are unaffected (all succeed); only values drift
        # with history, so the twin output never materializes.
        self.assertEqual(collect_done_statuses(out), [0, 2, 0, 0, 0])
        self.assertNotIn(b"3000", terminal_stream(out))

    def test_mutant_commit_before_analyze_goes_red(self):
        """F-M2: candidate source appended after rebuild but before
        candidate analysis; the failed candidate persists and the
        next submission chokes on the corrupt session."""
        out = self._fmutant_boot(
            "commit-early",
            [("user/shell/rl_sem.c",
              "    /* Candidate item. */",
              "    if (islet) {\n"
              "        unsigned int k;\n"
              "        for (k = 0; k < canonlen; ++k)\n"
              "            rl_sess.src[rl_sess.srclen + k] = canon[k];\n"
              "        rl_sess.srclen += canonlen;\n"
              "        rl_sess.src[rl_sess.srclen] = 0;\n"
              "    }\n"
              "    /* Candidate item. */",
              1)],
            keys=KF("let x: int = 41\n") + KF("let y: int = nosuch\n") +
                 KF("x + 1\n") + KF("echo finm2\n"),
            done=b"finm2")
        rows = self._rl_rows(out)
        # y-submit rejects (row), then x+1 hits the persisted corrupt
        # bytes (INTERNAL, never the rollback value).
        self.assertEqual(collect_done_statuses(out), [0, 2, 2, 0])
        self.assertIn("[RL] error RL_INTERNAL", rows)

    def test_mutant_append_before_success_goes_red(self):
        """F-M3: candidate source appended between analysis and eval
        (and not again at commit); a trapped submission persists its
        bytes without a row."""
        out = self._fmutant_boot(
            "append-mid",
            [("user/shell/rl_sem.c",
              "        evrc = rl_eval(&ectx, init, &ev);\n"
              "        if (evrc == RL_EV_TRAP) {",
              "        {\n"
              "            unsigned int k;\n"
              "            for (k = 0; k < canonlen; ++k)\n"
              "                rl_sess.src[rl_sess.srclen + k] = canon[k];\n"
              "            rl_sess.srclen += canonlen;\n"
              "            rl_sess.src[rl_sess.srclen] = 0;\n"
              "        }\n"
              "        evrc = rl_eval(&ectx, init, &ev);\n"
              "        if (evrc == RL_EV_TRAP) {",
              1),
             ("user/shell/rl_sem.c",
              "            row->flags = 0;\n"
              "            for (k = 0; k < canonlen; ++k)\n"
              "                rl_sess.src[old_src + k] = canon[k];\n"
              "            rl_sess.srclen = old_src + canonlen;\n"
              "            rl_sess.src[rl_sess.srclen] = 0;",
              "            row->flags = 0;",
              1)],
            keys=KF("let x: int = 41\n") + KF("let w: int = 1/0\n") +
                 KF("echo finm3\n"),
            done=b"finm3")
        # Trap row present, but the failed bytes persisted: session
        # grew past the single committed let.
        rows = self._rl_rows(out)
        self.assertIn("[RL] trap div0", rows)
        stats = self._rl_stats(out)
        self.assertEqual(stats[0]["src"], 16)
        self.assertGreater(stats[1]["src"], 16)

    def test_mutant_no_reset_syntax_goes_red(self):
        """F-M4: submission arena not reset on syntax errors; heavy
        failing submissions exhaust the node pool (rltest leak)."""
        out = self._rltest_mutant(
            "no-reset-syntax",
            [("user/shell/rl_sem.c",
              "            int k = (proot.rc == RLP_NOMEM) ? RL_SUB_ARENAFULL\n"
              "                                            : RL_SUB_SYNTAX;\n"
              "            rl_arena_reset(&rl_sub);\n"
              "            return rl_fail(k, d);",
              "            int k = (proot.rc == RLP_NOMEM) ? RL_SUB_ARENAFULL\n"
              "                                            : RL_SUB_SYNTAX;\n"
              "            return rl_fail(k, d);",
              1)],
            "leak")
        # rltest's own checks fail (exit 1): the leak is observable.
        self.assertEqual(collect_done_statuses(out), [1, 0])
        self.assertIn(b"FAIL", terminal_stream(out))

    def test_mutant_no_reset_success_goes_red(self):
        """F-M5: submission arena not reset on success; repeated
        heavy expressions exhaust the pool (rltest leak)."""
        out = self._rltest_mutant(
            "no-reset-success",
            [("user/shell/rl_sem.c",
              "        o = rl_fail(RL_SUB_OK, 0);\n"
              "        o.val.type = RLV_UNIT;\n"
              "        rl_arena_reset(&rl_sub);\n"
              "        return o;\n"
              "    }\n"
              "}",
              "        o = rl_fail(RL_SUB_OK, 0);\n"
              "        o.val.type = RLV_UNIT;\n"
              "        return o;\n"
              "    }\n"
              "}",
              1)],
            "leak")
        self.assertEqual(collect_done_statuses(out), [1, 0])
        self.assertIn(b"FAIL", terminal_stream(out))

    def test_mutant_no_promotion_goes_red(self):
        """F-M6: persistent string kept in submission memory (no
        promotion, staged address retained); the ownership checker
        blocks the commit."""
        out = self._fmutant_boot(
            "no-promotion",
            [("user/shell/rl_sem.c",
              "            if (row->type == RLV_STR) {\n"
              "                /* Promote the staged bytes into session ownership\n"
              "                 * (fit pre-verified, so this cannot fail partway). */\n"
              "                const char *sp = (const char *)row->pay;\n"
              "                for (k = 0; k < row->str_len; ++k)\n"
              "                    rl_sess.sess_arena[rl_sess.sess_used + k] = sp[k];\n"
              "                row->pay = old_used;\n"
              "                rl_sess.sess_used += row->str_len;\n"
              "                if (rl_sess.sess_used > rl_sess.sess_high)\n"
              "                    rl_sess.sess_high = rl_sess.sess_used;\n"
              "            }",
              "            if (row->type == RLV_STR) {\n"
              "                /* (mutant: no promotion; staged submission\n"
              "                 * address retained) */\n"
              "            }",
              1),
             ("user/shell/rl_sem.c",
              "            row->name_off = (unsigned short)(old_src +\n"
              "                                             (row->name_off - trim));\n"
              "            row->flags = 0;",
              "            row->name_off = (unsigned short)(old_src +\n"
              "                                             (row->name_off - trim));\n"
              "            if (row->type != RLV_STR) row->flags = 0;",
              1)],
            keys=KF('let s: str = "persistent-value"\n') +
                 KF("print(s)\n") + KF("echo finm6\n"),
            done=b"finm6")
        rows = self._rl_rows(out)
        self.assertIn("[RL] ownership-fail", rows)
        self.assertEqual(collect_done_statuses(out), [2, 2, 0])

    def test_mutant_duplicate_allowed_goes_red(self):
        """F-M7: duplicate lets allowed; the second commits."""
        out = self._fmutant_boot(
            "dup-allowed",
            [("user/shell/rl_sem.c",
              "        if (rl_lookup(rl_sess.cand, ncand, rl_sess.src, nm, nl) >= 0) {\n"
              "            rl_arena_reset(&rl_sub);\n"
              "            return rl_fail(RL_SUB_REJECT, RL_D_SEM_DUPLICATE);\n"
              "        }\n"
              "        if (rl_sess.nsyms >= RL_SYM_MAX) {",
              "        if (rl_sess.nsyms >= RL_SYM_MAX) {",
              1)],
            keys=KF("let x: int = 1000\n") + KF("let x: int = 2000\n") +
                 KF("echo finm7\n"),
            done=b"finm7")
        # Second let commits (done 0, no duplicate row) instead of
        # rejecting. (A third language submit would wedge on the
        # residue; the two-commit proof is the RED marker.)
        self.assertEqual(collect_done_statuses(out), [0, 0, 0])
        self.assertNotIn("[RL] reject SEM_DUPLICATE",
                         self._rl_rows(out))

    def test_mutant_precedence_swap_goes_red(self):
        """F-M8: swapped +/- precedence evaluates 100+2000*30 as
        (100+2000)*30."""
        out = self._fmutant_boot(
            "prec-swap",
            [("user/shell/rl_parse.c",
              "    case RLT_PLUS: *prec = 5; *op = RLOP_ADD; return 1;\n"
              "    case RLT_MINUS: *prec = 5; *op = RLOP_SUB; return 1;",
              "    case RLT_PLUS: *prec = 6; *op = RLOP_ADD; return 1;\n"
              "    case RLT_MINUS: *prec = 6; *op = RLOP_SUB; return 1;",
              1)],
            keys=KF("100 + 2000 * 30\n") + KF("echo finm8\n"),
            done=b"finm8")
        self.assertEqual(collect_done_statuses(out), [0, 0])
        self.assertIn(b"63000", terminal_stream(out))

    def test_mutant_type_accept_goes_red(self):
        """F-M9: analyzer accepts int+bool; evaluation hits the
        defensive type guard (wrong error class, never a value)."""
        out = self._fmutant_boot(
            "type-accept",
            [("user/shell/rl_sem.c",
              "                if (nd->op >= RLOP_ADD && nd->op <= RLOP_MOD) {\n"
              "                    if (lt != RLV_INT || rt != RLV_INT) {\n"
              "                        *diag = RL_D_SEM_TYPE;\n"
              "                        return RLV_UNKNOWN;\n"
              "                    }\n"
              "                    types[i] = RLV_INT;",
              "                if (nd->op >= RLOP_ADD && nd->op <= RLOP_MOD) {\n"
              "                    types[i] = RLV_INT;",
              1)],
            keys=KF("1 + true\n") + KF("echo finm9\n"),
            done=b"finm9")
        rows = self._rl_rows(out)
        self.assertIn("[RL] error RL_INTERNAL", rows)
        self.assertNotIn("[RL] reject SEM_TYPE_MISMATCH", rows)

    def test_mutant_if_accepted_goes_red(self):
        """F-M10: `if` not rejected as NOTIMPL (falls to E command
        attempt instead)."""
        out = self._fmutant_boot(
            "if-accepted",
            [("user/shell/sh.c",
              "    if (t0.kind == RLT_FN || t0.kind == RLT_IF ||\n"
              "        t0.kind == RLT_WHILE || t0.kind == RLT_RETURN) {",
              "    if (t0.kind == RLT_FN ||\n"
              "        t0.kind == RLT_WHILE || t0.kind == RLT_RETURN) {",
              1)],
            keys=KF("if true\n") + KF("echo finm10\n"),
            done=b"finm10")
        rows = self._rl_rows(out)
        self.assertNotIn("[RL] notimpl if", rows)
        self.assertEqual(collect_done_statuses(out), [127, 0])

    def test_mutant_if_as_command_goes_red(self):
        """F-M11: `if` routed to external-command execution (spawn
        footprint) instead of loud NOTIMPL."""
        out = self._fmutant_boot(
            "if-command",
            [("user/shell/sh.c",
              "    if (t0.kind == RLT_FN || t0.kind == RLT_IF ||\n"
              "        t0.kind == RLT_WHILE || t0.kind == RLT_RETURN) {",
              "    if (t0.kind == RLT_IF) return RLF_E;\n"
              "    if (t0.kind == RLT_FN ||\n"
              "        t0.kind == RLT_WHILE || t0.kind == RLT_RETURN) {",
              1)],
            keys=KF("if true\n") + KF("echo finm11\n"),
            done=b"finm11")
        rows = collect_sh_rows(out)
        self.assertTrue(any(r.startswith("[SH] error spawn") for r in rows),
                        rows)
        self.assertNotIn("[RL] notimpl if", self._rl_rows(out))

    def test_mutant_depth_off_goes_red(self):
        """F-M12: depth bound raised absurdly; 65-deep input
        evaluates instead of rejecting."""
        out = self._fmutant_boot(
            "depth-off",
            [("user/shell/rl_parse.h",
              "#define RL_DEPTH_MAX 64u",
              "#define RL_DEPTH_MAX 1000000u",
              1)],
            keys=KF("(" * 65 + "1000" + ")" * 65 + "\n") +
                 KF("echo finm12\n"),
            done=b"finm12")
        self.assertEqual(collect_done_statuses(out), [0, 0])
        writes = collect_load_writes(out)
        self.assertIn(b"1000".hex(), _slot_blob(writes, 0))

    def test_mutant_session_truncate_goes_red(self):
        """F-M13: session cap truncates instead of rejecting; the
        partial bytes persist (rltest cap-session 8193)."""
        out = self._rltest_mutant(
            "cap-truncate",
            [("user/shell/rl_sem.c",
              "        canonlen = rl_canon(part, len, canon, sizeof(canon), &trim);\n"
              "        if (canonlen == ~0u ||\n"
              "            canonlen > RL_SESS_MAX ||\n"
              "            rl_sess.srclen > RL_SESS_MAX - canonlen) {\n"
              "            rl_arena_reset(&rl_sub);\n"
              "            return rl_fail(RL_SUB_REJECT, RL_D_SESSION_LIMIT);\n"
              "        }",
              "        canonlen = rl_canon(part, len, canon, sizeof(canon), &trim);\n"
              "        if (canonlen == ~0u ||\n"
              "            canonlen > RL_SESS_MAX ||\n"
              "            rl_sess.srclen > RL_SESS_MAX - canonlen) {\n"
              "            canonlen = RL_SESS_MAX - rl_sess.srclen;\n"
              "        }",
              1)],
            "cap-session", " 8193")
        self.assertEqual(collect_done_statuses(out), [1, 0])
        self.assertIn(b"FAIL", terminal_stream(out))

    def test_mutant_symbol_partial_goes_red(self):
        """F-M14: symbol cap lowered to 3 (129 live submissions are
        beyond the QEMU time budget, so the cap mechanism is pinned
        at small scale while the 128 value is pinned by repo test).
        The 4th let must reject with rollback and intact priors."""
        out = self._fmutant_boot(
            "sym-partial",
            [("user/shell/rl_sem.c",
              "        if (rl_sess.nsyms >= RL_SYM_MAX) {",
              "        if (rl_sess.nsyms >= 3u) {",
              1)],
            keys=KF("let m0: int = 1000\n") + KF("let m1: int = 2000\n") +
                 KF("let m2: int = 3000\n") + KF("let m3: int = 4000\n") +
                 KF("m0 + m1\n") + KF("echo finm14\n"),
            done=b"finm14")
        self.assertEqual(collect_done_statuses(out),
                         [0, 0, 0, 2, 0, 0])
        rows = self._rl_rows(out)
        self.assertIn("[RL] reject RL_SYMBOL_LIMIT", rows)
        writes = collect_load_writes(out)
        self.assertIn(b"3000".hex(), _slot_blob(writes, 0))

    def test_mutant_sequential_pipe_goes_red(self):
        """F-M15: evaluator pipelines must stream through spawn_pipe
        (sequential spawn/wait leaks raw producer bytes, no overlap)."""
        seq = ("    build_spec(&sa, va, aa[0], lena, a->argc, aa,\n"
               "               SH_STDIN_CLOSED, SH_STDOUT_SERIAL);\n"
               "    rc = sh_sys_spawn(&sa, &ha);\n"
               "    if (rc != SH_OK) {\n"
               "        row_begin();\n"
               "        row_str(\"[SH] error spawn \");\n"
               "        row_num(rc);\n"
               "        row_str(\"\\r\\n\");\n"
               "        row_flush();\n"
               "        last_status = map_spawn(rc);\n"
               "        final_status = last_status;\n"
               "        final_kind = 1;\n"
               "        return;\n"
               "    }\n"
               "    row_begin();\n"
               "    row_str(\"[SH] spawned a=\");\n"
               "    row_handle(ha);\n"
               "    row_str(\"\\r\\n\");\n"
               "    row_flush();\n"
               "    fh[0] = ha;\n"
               "    nfh = 1;\n"
               "    fh_abort = 0;\n"
               "    reap_children();\n"
               "    build_spec(&sb, vb, ab[0], lenb, b->argc, ab,\n"
               "               SH_STDIN_CLOSED, SH_STDOUT_SERIAL);\n"
               "    rc = sh_sys_spawn(&sb, &hb);\n"
               "    if (rc != SH_OK) {\n"
               "        row_begin();\n"
               "        row_str(\"[SH] error spawn \");\n"
               "        row_num(rc);\n"
               "        row_str(\"\\r\\n\");\n"
               "        row_flush();\n"
               "        last_status = map_spawn(rc);\n"
               "        final_status = last_status;\n"
               "        final_kind = 1;\n"
               "        return;\n"
               "    }\n"
               "    row_begin();\n"
               "    row_str(\"[SH] spawned a=\");\n"
               "    row_handle(hb);\n"
               "    row_str(\"\\r\\n\");\n"
               "    row_flush();\n"
               "    fh[0] = hb;\n"
               "    nfh = 1;\n"
               "    fh_abort = 0;\n"
               "    (void)sh_sys_spawn_pipe;")
        out = self._fmutant_boot(
            "sequential-pipe-f",
            [("user/shell/sh.c",
              "    build_spec(&sa, va, aa[0], lena, a->argc, aa,\n"
              "               SH_STDIN_CLOSED, SH_STDOUT_PIPE);\n"
              "    build_spec(&sb, vb, ab[0], lenb, b->argc, ab,\n"
              "               SH_STDIN_PIPE, SH_STDOUT_SERIAL);\n"
              "    rc = sh_sys_spawn_pipe(&sa, &sb, &ha, &hb);",
              seq,
              1)],
            keys=KF("prod 8192 0 |> upper\n") + KF("echo finm15\n"),
            done=b"finm15")
        writes = collect_load_writes(out)
        blob = "".join(h for _, h in writes)
        self.assertIn(pattern_raw(8192).hex(),
                      _slot_blob(writes, 1))
        self.assertNotIn("[SH] overlap 1", collect_sh_rows(out))

    def test_mutant_ctrlc_preserves_goes_red(self):
        """F-M16: editing abort submits the line buffer instead of
        discarding it (candidate commits across Ctrl-C)."""
        out = self._fmutant_boot(
            "ctrlc-preserves",
            [("user/shell/sh.c",
              "    rl_sub_reset();\n"
              "    if (nfh == 0) {\n"
              "        clear_line();\n"
              "        sh_print(\"[SH] abort line\\r\\n\");",
              "    rl_sub_reset();\n"
              "    if (nfh == 0) {\n"
              "        sh_print(\"\\r\\n\");\n"
              "        line_submitted = 1;\n"
              "        line_pos = 0;\n"
              "        sh_print(\"[SH] abort line\\r\\n\");",
              1)],
            keys=KF("let y: int = 99\n")[:-1] + [CTRL_C] +
                 KF("y\n") + KF("echo finm16\n"),
            done=b"finm16")
        # The unsubmitted buffer committed: y evaluates to 99.
        writes = collect_load_writes(out)
        self.assertIn(b"99", terminal_stream(out))
        self.assertEqual(collect_done_statuses(out)[1], 0)

    def test_mutant_kernel_eval_goes_red(self):
        """F-M17: evaluator code moved/duplicated into the kernel
        trips the placement guard (no QEMU needed)."""
        with tempfile.TemporaryDirectory(prefix="rl-m17-",
                                         dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            probe = (root / "kernel/core/rl_eval_probe.c")
            probe.write_text(
                "/* resident evaluator fallback (ring 0 placement "
                "violation) */\n"
                "static int rl_eval_probe(const char *s) {\n"
                "    (void)s;\n"
                "    return 0;\n"
                "}\n",
                encoding="utf-8")
            hits = []
            for path in list((root / "kernel").rglob("*.c")) + \
                    list((root / "kernel").rglob("*.h")):
                text = path.read_text(encoding="utf-8",
                                      errors="ignore")
                if ("[RL] " in text or "rl_eval" in text or
                        "lang_parse" in text or "repl" in text):
                    hits.append(path.name)
            self.assertIn("rl_eval_probe.c", hits)

    def test_mutant_host_answer_goes_red(self):
        """F-M18: print emits a canned constant instead of evaluating;
        value tests go red (guest-independence tripwire)."""
        out = self._fmutant_boot(
            "canned-print",
            [("user/shell/rl_eval.c",
              "                rn = rl_render(&a, rbuf, sizeof(rbuf));\n"
              "                if (a.type == RLV_STR) {",
              "                rn = 6u;\n"
              "                rbuf[0] = 'C';\n"
              "                rbuf[1] = 'A';\n"
              "                rbuf[2] = 'N';\n"
              "                rbuf[3] = 'N';\n"
              "                rbuf[4] = 'E';\n"
              "                rbuf[5] = 'D';\n"
              "                if (a.type == RLV_STR) {",
              1)],
            keys=KF("print(1000 + 2000)\n") + KF("echo finm18\n"),
            done=b"finm18")
        stream = terminal_stream(out)
        self.assertIn(b"CANNED", stream)
        self.assertEqual(collect_done_statuses(out), [0, 0])

    def test_mutant_status_poll_goes_red(self):
        """F-M19: status cached on RUNNING polls leaks into the
        builtin (overlap guarantees the poll window)."""
        out = self._fmutant_boot(
            "status-poll-f",
            [("user/shell/sh.c",
              "        if (st.state == SH_RUNNING) {\n"
              "            live++;\n"
              "            continue;\n"
              "        }",
              "        if (st.state == SH_RUNNING) {\n"
              "            live++;\n"
              "            last_status = 7777;\n"
              "            continue;\n"
              "        }",
              1),
             ("user/shell/sh.c",
              "    final_kind = fh_abort ? (nfh == 2 ? 3 : 2) : 1;\n"
              "    last_status = final_status;\n"
              "    nfh = 0;",
              "    final_kind = fh_abort ? (nfh == 2 ? 3 : 2) : 1;\n"
              "    nfh = 0;",
              1)],
            keys=KF("prod 8192 0 |> upper\n") + KF("status\n") +
                 KF("echo finm19\n"),
            done=b"finm19")
        stream = terminal_stream(out)
        self.assertIn(b"7777", stream)

    def test_mutant_skip_prefix_goes_red(self):
        """F-M20: reanalysis skipping the first declaration corrupts
        the row correspondence (positions are load-bearing: skipping
        silently is architecturally impossible, so the failure mode
        is a loud wedge, still a history-dependence violation)."""
        out = self._fmutant_boot(
            "skip-prefix",
            [("user/shell/rl_sem.c",
              "    /* Whole-buffer rebuild over accepted items. */\n"
              "    ncand = 0;\n"
              "    rpos = 0;\n"
              "    ridx = 0;",
              "    /* Whole-buffer rebuild over accepted items. */\n"
              "    ncand = 0;\n"
              "    rpos = 0;\n"
              "    ridx = 0;\n"
              "    while (rpos < rl_sess.srclen &&\n"
              "           rl_sess.src[rpos] != '\\n')\n"
              "        ++rpos;\n"
              "    if (rpos < rl_sess.srclen) ++rpos;\n"
              "    if (ridx < rl_sess.nsyms) ++ridx;",
              1)],
            keys=KF("let a: int = 1000\n") + KF("let b: int = 2000\n") +
                 KF("let a: int = 3\n") + KF("echo finm20\n"),
            done=b"finm20")
        # The skipped prefix clobbers row correspondence: the third
        # submission wedges (INTERNAL) instead of cleanly rejecting
        # the duplicate, and no DUPLICATE row appears.
        self.assertEqual(collect_done_statuses(out), [0, 0, 2, 0])
        rows = self._rl_rows(out)
        self.assertIn("[RL] error RL_INTERNAL", rows)
        self.assertNotIn("[RL] reject SEM_DUPLICATE", rows)
