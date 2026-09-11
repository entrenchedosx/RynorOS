"""Stage 18d Slice G: len(expr) builtin on the frozen Slice F evaluator.

Slice F is frozen. The single new language feature is `len(expr)`:
a pure builtin expression returning the byte length of a string
(decoded runtime bytes, excluding any C NUL; byte length, never
Unicode/code-point/source-spelling length).

Conventions (inherited from Slice F, unchanged):
- statuses: language syntax/semantic/NOTIMPL -> 2, eval trap -> 129,
  success -> 0; commands/pipelines/Ctrl-C follow Slice E exactly.
- rows: `[SH] error syntax` (lexer/parser, incl. the frozen
  zero-arg-call rule: `len()` parses exactly like `print()`),
  `[RL] reject <class>` (semantic/bounds, incl. len arity/type in
  the existing SEM families), `[RL] stats ...` after evaluator
  submissions only.
- hex goldens use per-slot blobs; numeric goldens need >= 4
  characters (F golden rule: short digit strings occur in
  wantkey/done rows). Small len values are therefore asserted via
  >= 4-char compositions on the keyboard path and exactly in-guest
  via /bin/rltest `len` (ebuf byte asserts, no transcript ambiguity).
- scale/exact checks (200-char literal, len-depth 64/65, 50x
  ownership loop, heavy len-error leak loop) run through
  /bin/rltest; keyboard tests pin integration. Both assert guest
  transcripts only; the host never computes answers.
- differential: the frozen host analyzer knows no `len`, so every
  len-call fixture records the raw host verdict (SEM_UNKNOWN_FUNCTION)
  as a DOCUMENTED intentional-addition divergence; value/error-class
  expectations are hand-pinned and cross-checked by a tiny in-test
  byte-length oracle (independent Python decode of the frozen escape
  rules). Non-len aspects keep FULL parity.
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
from sh_output import (validate_sh_section, collect_sh_rows,
                       collect_load_writes, collect_done_statuses,
                       terminal_stream)
from test_cplshell import (CTRL_C, _slot_blob, _drive_entries)
from test_rleval import KF as _KF, _compile_shell, _compile_rltest, host_check
from test_rleval import RL_COMMANDS as _RL_COMMANDS
from test_rleval import host_analyze as _host_analyze


# Slice G key map: Slice F punctuation plus comma (probed live in
# G0 before any comma-bearing line is typed).
_GEXTRA = {",": "comma"}


def KF(text):
    out = []
    for ch in text:
        if ch in _GEXTRA:
            out.append(_GEXTRA[ch])
        else:
            out += _KF(ch)
    return out


def _gdecode(body):
    """Independent byte-length oracle: decode one string body with
    the frozen escape rules (\\ \" \\n \\t only, ASCII) and return
    the decoded bytes. Raises on anything outside the fixture
    shapes so the oracle fails loud, never silent."""
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            if i + 1 >= len(body):
                raise AssertionError("oracle: trailing backslash")
            e = body[i + 1]
            if e == "\\":
                out.append(0x5C)
            elif e == '"':
                out.append(0x22)
            elif e == "n":
                out.append(0x0A)
            elif e == "t":
                out.append(0x09)
            else:
                raise AssertionError("oracle: bad escape %r" % e)
            i += 2
        else:
            o = ord(c)
            if o > 126 or o < 32:
                raise AssertionError("oracle: non-print %r" % c)
            out.append(o)
            i += 1
    return bytes(out)


class RlLenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/rlen"
        cls.work.mkdir(parents=True, exist_ok=True)
        blobs = _compile_shell(cls.work)
        cls.blobs = blobs
        rltest = _compile_rltest(cls.work)
        cls.token = "tok-g-%s" % uuid.uuid4().hex[:8]
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
        parts = re.split(rb"\[SH\] done status=\d+\r\n",
                         terminal_stream(out))
        self.assertEqual(len(parts), nparts + 1, parts[-1][:200])
        return parts[:-1]

    # ---- G0: placement, size, independence ----

    def test_g0_keyprobe_comma(self):
        # The comma key must deliver+decode before any comma-bearing
        # len line is typed: the done marker itself carries one, so
        # the boot cannot complete without it.
        keys = KF("echo A,B-DONE-0a\n")
        out = self._boot(keys, b"A,B-DONE-0a", tag="g0comma")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0])
        self.assertIn(b"A,B-DONE-0a", terminal_stream(out))

    def test_g0_len_stays_cpl3(self):
        names = ("rl_lex.c", "rl_parse.c", "rl_sem.c", "rl_eval.c",
                 "sh.c")
        blob = "\n".join((ROOT / "user/shell" / n).read_text(
            encoding="utf-8") for n in names)
        for token in ("tools/rynorlang", "analyze(", "compile.py",
                      "RIR", "interp.py"):
            self.assertNotIn(token, blob, token)
        for token in ("rir_", "compile_", "codegen", "bytecode",
                      "jit", "fn_execute", "block_execute"):
            self.assertNotIn(token, blob, token)
        rltest = (ROOT / "user/proc-tests/rltest.c").read_text(
            encoding="utf-8")
        for token in ("tools/rynorlang", "analyze(", "compile.py",
                      "RIR"):
            self.assertNotIn(token, rltest, token)
        code, fsz, msz = struct.unpack("<III", self.blobs["sh"][16:28])
        self.assertLessEqual(code, 65536)
        self.assertLessEqual(msz, 32768)
        self.assertLessEqual(msz, 32768 - 1024)
        print("shell code=%d data_memsz=%d" % (code, msz))

    # ---- G1: literals and escapes ----

    def test_g1_len_literals(self):
        # Small values ride >= 4-char compositions (golden rule);
        # exact small values are pinned in-guest (rltest len).
        keys = []
        keys += KF('len("")\n')  # 0 (status-pinned here)
        keys += KF('len("a")\n')  # 1 (status-pinned here)
        keys += KF('len("abc")\n')  # 3 (status-pinned here)
        keys += KF('len("abcd") + 1000\n')  # 1004 proves 4
        keys += KF("echo G1-DONE-1a\n")
        out = self._boot(keys, b"G1-DONE-1a", tag="g1lit")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0, 0, 0, 0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        self.assertIn(b"1004".hex(), kids)
        stats = self._rl_stats(out)
        self.assertEqual(len(stats), 4)
        for st in stats:
            self.assertEqual(st["syms"], 0)
            self.assertEqual(st["src"], 0)
            self.assertEqual(st["sub_live"], 0)

    def test_g1_len_escapes(self):
        # Every escape decodes to one runtime byte (3 each).
        keys = []
        keys += KF('len("a\\nb") + 1000\n')  # 1003
        keys += KF('100 * len("a\\tb")\n')  # 300
        keys += KF('len("a\\"b") + 2000\n')  # 2003
        keys += KF('len("a\\\\b") + 4000\n')  # 4003
        keys += KF("echo G1-DONE-1b\n")
        out = self._boot(keys, b"G1-DONE-1b", tag="g1esc")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0, 0, 0, 0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        for golden in (b"1003", b"300", b"2003", b"4003"):
            self.assertIn(golden.hex(), kids)

    # ---- G2: session, composition, determinism ----

    def test_g2_len_session(self):
        keys = []
        keys += KF('let s: str = "hello"\n')
        keys += KF("len(s) * 1000 + 7\n")  # 5007 proves 5
        keys += KF('let n: int = len("abcd")\n')
        keys += KF("n * 1000 + 9\n")  # 4009 proves 4
        keys += KF("print(len(s))\n")  # prints 5, unit result
        keys += KF("len(s) == 5\n")  # true
        keys += KF("echo G2-DONE-2a\n")
        out = self._boot(keys, b"G2-DONE-2a", tag="g2sess")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [0, 0, 0, 0, 0, 0, 0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        for golden in (b"5007", b"4009", b"true"):
            self.assertIn(golden.hex(), kids)
        stats = self._rl_stats(out)
        self.assertEqual(stats[-1]["syms"], 2)
        for st in stats:
            self.assertEqual(st["sub_live"], 0)

    def test_g2_len_rejected_twins(self):
        # Session A: straight successes.
        akeys = KF('let s: str = "abc"\n') + \
            KF("len(s) * 1000\n") + KF("echo TWINAG-DONE-2b\n")
        aout = self._boot(akeys, b"TWINAG-DONE-2b", tag="g2twina")
        # Session B: same final bytes via a rejected len detour.
        bkeys = KF('let s: str = "abc"\n') + KF("len(123)\n") + \
            KF("len(s) * 1000\n") + KF("echo TWINBG-DONE-2b\n")
        bout = self._boot(bkeys, b"TWINBG-DONE-2b", tag="g2twinb")
        for out in (aout, bout):
            self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(aout), [0, 0, 0])
        self.assertEqual(collect_done_statuses(bout), [0, 2, 0, 0])
        astats = self._rl_stats(aout)
        bstats = self._rl_stats(bout)
        self.assertEqual(astats[-1]["src"], bstats[-1]["src"])
        self.assertEqual(astats[-1]["syms"], bstats[-1]["syms"])
        self.assertEqual(bstats[1]["src"], bstats[2]["src"])
        self.assertEqual(bstats[1]["syms"], bstats[2]["syms"])
        for blob in (terminal_stream(aout), terminal_stream(bout)):
            self.assertIn(b"3000", blob)
        self.assertIn("[RL] reject SEM_TYPE_MISMATCH",
                      self._rl_rows(bout))

    # ---- G3: errors and rollback ----

    def test_g3_len_rejections(self):
        keys = []
        keys += KF('len("a", "b")\n')  # arity
        keys += KF("len(1)\n")  # type
        keys += KF("len(true)\n")  # type
        keys += KF('len(len("abc"))\n')  # inner int -> type
        keys += KF('banana("x")\n')  # unknown intact
        keys += KF('length("ab")\n')  # prefix word intact
        keys += KF("echo G3-DONE-3a\n")
        out = self._boot(keys, b"G3-DONE-3a", tag="g3rej")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out),
                         [2, 2, 2, 2, 2, 2, 0])
        rows = self._rl_rows(out)
        self.assertEqual(rows.count("[RL] reject SEM_ARITY_MISMATCH"),
                         1)
        self.assertEqual(rows.count("[RL] reject SEM_TYPE_MISMATCH"),
                         3)
        self.assertEqual(rows.count("[RL] reject SEM_UNKNOWN_FUNCTION"),
                         2)
        stats = self._rl_stats(out)
        self.assertEqual(len(stats), 6)
        for st in stats:
            self.assertEqual(st["syms"], 0)
            self.assertEqual(st["src"], 0)
            self.assertEqual(st["sess_live"], 0)
            self.assertEqual(st["sub_live"], 0)

    def test_g3_len_call_syntax(self):
        # Frozen zero-arg-call rule: `len()` parses exactly like
        # `print()` (both loud syntax, never a semantic row); a
        # mistyped let commits nothing.
        keys = []
        keys += KF("len()\n")
        keys += KF("print()\n")
        keys += KF('let n: bool = len("abc")\n')
        keys += KF("echo G3-DONE-3b\n")
        out = self._boot(keys, b"G3-DONE-3b", tag="g3syn")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [2, 2, 2, 0])
        self.assertEqual(collect_sh_rows(out).count("[SH] error syntax"),
                         2)
        rows = self._rl_rows(out)
        self.assertIn("[RL] reject SEM_TYPE_MISMATCH", rows)
        stats = self._rl_stats(out)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["syms"], 0)
        self.assertEqual(stats[0]["src"], 0)
        self.assertEqual(stats[0]["sub_live"], 0)

    # ---- G4: identifier and command interplay ----

    def test_g4_len_identifier(self):
        # `len` is a builtin ONLY in call position: a declared `len`
        # variable keeps working, and calls still hit the builtin.
        keys = []
        keys += KF("let len: int = 7\n")
        keys += KF("len * 1000 + 11\n")  # 7011 proves bare len == 7
        keys += KF("(len + 1) * 1000\n")  # 8000
        keys += KF('len("abc") * 1000 + 3\n')  # 3003, builtin wins
        keys += KF("echo G4-DONE-4a\n")
        out = self._boot(keys, b"G4-DONE-4a", tag="g4ident")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0, 0, 0, 0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        for golden in (b"7011", b"8000", b"3003"):
            self.assertIn(golden.hex(), kids)

    def test_g4_len_command_position(self):
        # Command position stays command position: `len` with
        # arguments is an external command attempt (no /bin/len),
        # and a bare undeclared `len` is the frozen unknown-command
        # path. Neither prints an [RL] row.
        keys = []
        keys += KF('len "abc"\n')
        keys += KF("len\n")
        keys += KF("echo G4-DONE-4b\n")
        out = self._boot(keys, b"G4-DONE-4b", tag="g4cmd")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [127, 127, 0])
        stream = terminal_stream(out)
        self.assertNotIn(b"[RL]", stream)

    # ---- G5: Ctrl-C ----

    def test_g5_len_ctrlc(self):
        keys = []
        keys += KF('let s: str = "hello"\n')
        keys += KF("len(s")
        keys += [CTRL_C]
        keys += KF("len(s) * 1000 + 5\n")  # 5005: session survived
        keys += KF("echo G5-DONE-5a\n")
        out = self._boot(keys, b"G5-DONE-5a", tag="g5c")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        self.assertEqual(collect_done_statuses(out), [0, 0, 0])
        self.assertIn("[SH] abort line", rows)
        writes = collect_load_writes(out)
        self.assertIn(b"5005".hex(), _slot_blob(writes, 0))
        stats = self._rl_stats(out)
        self.assertEqual(stats[-1]["syms"], 1)
        for st in stats:
            self.assertEqual(st["sub_live"], 0)

    # ---- G6: rltest scale run ----

    def test_g6_rltest_len(self):
        keys = KF("rltest len\n") + KF("echo RLTEST-DONE-LEN\n")
        out = self._boot(keys, b"RLTEST-DONE-LEN", tag="g6len")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0])
        stream = terminal_stream(out)
        self.assertIn(b"[RLT] done len pass", stream)
        self.assertNotIn(b"FAIL", stream)

    # ---- G7: differential conformance ----

    def _check_gfix(self, out, fixtures, prelude_dones=0,
                    prelude_lines=()):
        # Same shape as the Slice F fixture checker: the host
        # verdict is asserted live. Since 19a the host knows `len`
        # (aggregate builtin), so arity/type rows agree with the
        # guest (FULL) and value rows carry guest bytes the host
        # oracle must reproduce (asserted by the callers for FULL
        # value rows). Genuine divergences stay DOCUMENTED: the
        # guest evaluates what the host rejects and vice versa.
        chunks = self._chunks(out, len(fixtures) + prelude_dones + 1)
        chunks = chunks[prelude_dones:prelude_dones + len(fixtures)]
        self.assertEqual(len(chunks), len(fixtures))
        for chunk, fix in zip(chunks, fixtures):
            line, h_ok, h_code, g_ok, g_class, g_bytes, parity = fix
            host = host_check(line, prelude_lines)
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
                self.assertIn(g_bytes, chunk, line)
            self.assertIn(parity, ("FULL", "DOCUMENTED"))

    def _host_value(self, expr, prelude=()):
        # Host oracle value for one REPL expression (guest bytes live
        # in the fx rows; this closes the FULL-agreement loop).
        from tools.rynorlang import interp as _oracle
        from tools.rynorlang import rir as _rir
        body = "".join(f"{line};\n" for line in prelude)
        prog = "fn main(): int {\n" + body + f"print({expr});\nreturn 0;\n}}\n"
        res = _host_analyze.analyze(prog, filename="<repl>",
                                    edition="shell", commands=_RL_COMMANDS)
        self.assertTrue(res.ok, res.diagnostic)
        module, error = _rir.build_rir(res.ast, "<repl>")
        self.assertIsNone(error)
        emitted: list = []
        outcome = _oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"])
        return "".join(emitted).encode("ascii")

    def test_g7_diff_pure(self):
        fx = [
            ('len("abc") + 1000', True, None,
             True, None, b"1003", "FULL"),
            ('100 * len("a\\nb")', True, None,
             True, None, b"300", "FULL"),
            ('len("") + 7001', True, None,
             True, None, b"7001", "FULL"),
            ('len("a", "b")', False, "SEM_ARITY_MISMATCH", False,
             "SEM_ARITY_MISMATCH", None, "FULL"),
            ("len(1)", False, "SEM_TYPE_MISMATCH", False,
             "SEM_TYPE_MISMATCH", None, "FULL"),
            ('len(len("abc"))', False, "SEM_TYPE_MISMATCH",
             False, "SEM_TYPE_MISMATCH", None, "FULL"),
            ('banana("x")', False, "SEM_UNKNOWN_FUNCTION", False,
             "SEM_UNKNOWN_FUNCTION", None, "FULL"),
        ]
        keys = []
        for line, *_ in fx:
            keys += KF(line + "\n")
        keys += KF("echo G7-DONE-7a\n")
        out = self._boot(keys, b"G7-DONE-7a", tag="g7a")
        self.assertEqual(validate_sh_section(out), [])
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 0, 0, 2, 2, 2, 2, 0])
        self._check_gfix(out, fx)
        # FULL value rows: the host oracle must reproduce the guest
        # bytes exactly (the fixture format pins guest bytes only).
        for line, want in (('len("abc") + 1000', b"1003"),
                           ('100 * len("a\\nb")', b"300"),
                           ('len("") + 7001', b"7001")):
            self.assertEqual(self._host_value(line), want, line)

    _G7_PRELUDE_SRC = ("let q9: int = 7000", 'let s9: str = "abc"')

    def _g7_prelude(self):
        return (KF("let q9: int = 7000\n") +
                KF('let s9: str = "abc"\n'))

    def test_g7_diff_session(self):
        fx = [
            ("len(s9) * 1000", True, None, True,
             None, b"3000", "FULL"),
            ("len(q9)", False, "SEM_TYPE_MISMATCH", False,
             "SEM_TYPE_MISMATCH", None, "FULL"),
            ("len(nosuch)", False, "SHELL_UNKNOWN_COMMAND", False,
             "SEM_UNDECLARED", None, "DOCUMENTED"),
            ("let n9: int = len(s9)", True, None,
             True, None, None, "FULL"),
            # Knock-on divergence: the host check runs each line
            # against the fixed prelude (n9 never bound there), so
            # the use reads undeclared to the host while the guest
            # evaluates the bound n9 from the earlier row.
            ("n9 * 1000 + 1", False, "SEM_UNDECLARED", True, None,
             b"3001", "DOCUMENTED"),
            ("len(s9, q9)", False, "SEM_ARITY_MISMATCH", False,
             "SEM_ARITY_MISMATCH", None, "DOCUMENTED"),
        ]
        keys = self._g7_prelude()
        for line, *_ in fx:
            keys += KF(line + "\n")
        keys += KF("echo G7-DONE-7b\n")
        out = self._boot(keys, b"G7-DONE-7b", tag="g7b")
        self.assertEqual(validate_sh_section(out), [])
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 0, 0, 2, 2, 0, 0, 2, 0])
        self._check_gfix(out, fx, prelude_dones=2,
                         prelude_lines=self._G7_PRELUDE_SRC)
        self.assertEqual(
            self._host_value("len(s9) * 1000", self._G7_PRELUDE_SRC), b"3000")

    def test_g7_oracle_crosscheck(self):
        # The in-test byte-length oracle agrees with every
        # hand-pinned differential value (independent computation of
        # the same decoded-byte rule, incl. all four escapes).
        cases = [("", 0), ("a", 1), ("abc", 3), ("abcd", 4),
                 ("hello", 5), ("a\\nb", 3), ("a\\tb", 3),
                 ('a\\"b', 3), ("a\\\\b", 3),
                 ("x" * 200, 200)]
        for body, want in cases:
            self.assertEqual(len(_gdecode(body)), want, body)

    # ---- G8: purity census (no spawn, no pipe) ----

    def test_g8_len_pure_no_spawn(self):
        # A pure len boot leaves no child output behind: the only
        # slot-1 bytes in the whole boot are the done-marker echo
        # itself, and the pipeline-consumer slot stays empty.
        keys = []
        keys += KF('len("abcdef") * 1000 + 77\n')  # 6077 proves 6
        keys += KF("echo G8-DONE-8a\n")
        out = self._boot(keys, b"G8-DONE-8a", tag="g8pure")
        self.assertEqual(validate_sh_section(out), [])
        self.assertEqual(collect_done_statuses(out), [0, 0])
        writes = collect_load_writes(out)
        self.assertIn(b"6077".hex(), _slot_blob(writes, 0))
        self.assertEqual(_slot_blob(writes, 1), b"G8-DONE-8a\n".hex())
        self.assertEqual(_slot_blob(writes, 2), "")

    # ---- Slice G mutants (each executed RED, restored, green-rerun) ----

    def _gmutant_boot(self, name, edits, keys, done):
        """Builtin mutant run: copy the tree, apply source edits,
        rebuild kernel + shell + rltest images, boot. The working
        tree is never mutated (restored by fixture deletion)."""
        with tempfile.TemporaryDirectory(prefix="rl-gfault-",
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
            _bi(root, dest, shell_boot=True)
            blobs = _compile_shell(root / "build" / "progs",
                                   root=root)
            rlt = _compile_rltest(root / "build" / "progs-rl",
                                  root=root)
            entries = _drive_entries(blobs, self.token)
            entries.append(("/bin/rltest", rlt))
            image = root / "drive.img"
            image.write_bytes(fs_build(entries))
            logs = ROOT / "build/rlen" / name
            return boot_image(dest / "rynoros.img", logs, timeout=60,
                              extra_drives=(image,), require_sh=True,
                              sh_keys=tuple(keys),
                              sh_done=done)

    def _rltest_gmutant(self, name, edits):
        keys = KF("rltest len\n") + KF("echo RLMG-DONE\n")
        return self._gmutant_boot(name, edits, keys, b"RLMG-DONE")

    def test_mutant_len_source_spelling_goes_red(self):
        """G-M1: length counts source spelling (control bytes double)
        instead of decoded runtime bytes."""
        out = self._gmutant_boot(
            "len-spelling",
            [("user/shell/rl_eval.c",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                rl_vint(&v, (unsigned long long)a.len);",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                {\n"
              "                    unsigned int zi, extra = 0u;\n"
              "                    const char *ap = (const char *)a.a;\n"
              "                    for (zi = 0; zi < a.len; ++zi)\n"
              "                        if ((unsigned char)ap[zi] < 0x20u)\n"
              "                            ++extra;\n"
              "                    rl_vint(&v, (unsigned long long)(a.len +\n"
              "                                                  extra));\n"
              "                }",
              1)],
            keys=KF('len("abc") * 1000\n') +
                 KF('len("a\\nb") * 1000\n') + KF("echo finmg1\n"),
            done=b"finmg1")
        # Plain strings unaffected (control chunk emits 3000); the
        # escape chunk miscounts to 4000. Decoded-chunk raw asserts:
        # neither chunk's typed echo contains those digit runs.
        self.assertEqual(collect_done_statuses(out), [0, 0, 0])
        chunks = self._chunks(out, 3)
        self.assertIn(b"3000", chunks[0])
        self.assertIn(b"4000", chunks[1])
        self.assertNotIn(b"3000", chunks[1])

    def test_mutant_len_counts_nul_goes_red(self):
        """G-M2: length includes the C terminator (len+1)."""
        out = self._gmutant_boot(
            "len-nul",
            [("user/shell/rl_eval.c",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                rl_vint(&v, (unsigned long long)a.len);",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                rl_vint(&v, (unsigned long long)(a.len + 1u));",
              1)],
            keys=KF('len("abc") * 1000\n') + KF("echo finmg2\n"),
            done=b"finmg2")
        self.assertEqual(collect_done_statuses(out), [0, 0])
        chunks = self._chunks(out, 2)
        self.assertIn(b"4000", chunks[0])
        self.assertNotIn(b"3000", chunks[0])

    def test_mutant_len_no_type_check_goes_red(self):
        """G-M3: string type check removed; len(1) slips to eval and
        wedges INTERNAL instead of the type row."""
        out = self._gmutant_boot(
            "len-no-type",
            [("user/shell/rl_sem.c",
              "                if (at != RLV_STR) {\n"
              "                    *diag = RL_D_SEM_TYPE;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                types[i] = RLV_INT;",
              "                types[i] = RLV_INT;",
              1)],
            keys=KF("len(1)\n") + KF("echo finmg3\n"),
            done=b"finmg3")
        rows = self._rl_rows(out)
        self.assertNotIn("[RL] reject SEM_TYPE_MISMATCH", rows)
        self.assertIn("[RL] error RL_INTERNAL", rows)

    def test_mutant_len_ignores_arity_goes_red(self):
        """G-M4: arity check removed; two-arg calls evaluate the
        first argument instead of rejecting."""
        out = self._gmutant_boot(
            "len-no-arity",
            [("user/shell/rl_sem.c",
              "                if (nd->aux != 1u) {\n"
              "                    *diag = RL_D_SEM_ARITY;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                if (nd->k1 == RL_NONODE || nd->k1 >= RL_POOL_MAX) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                at = types[nd->k1];\n"
              "                if (at == RLV_UNKNOWN) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                if (at != RLV_STR) {",
              "                if (nd->k1 == RL_NONODE || nd->k1 >= RL_POOL_MAX) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                at = types[nd->k1];\n"
              "                if (at == RLV_UNKNOWN) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                if (at != RLV_STR) {",
              1)],
            keys=KF('len("a", "b") + 7000\n') + KF("echo finmg4\n"),
            done=b"finmg4")
        # Evaluated (done 0) with the first argument's length, and
        # no arity row: the RED is acceptance itself.
        self.assertEqual(collect_done_statuses(out), [0, 0])
        chunks = self._chunks(out, 2)
        self.assertIn(b"7001", chunks[0])
        self.assertNotIn("[RL] reject SEM_ARITY_MISMATCH",
                         self._rl_rows(out))

    def test_mutant_len_eval_mutates_session_goes_red(self):
        """G-M5: pure evaluation mutates committed session bytes; the
        length still reports 3 but the stored string is corrupted."""
        out = self._gmutant_boot(
            "len-mutates",
            [("user/shell/rl_eval.c",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                rl_vint(&v, (unsigned long long)a.len);",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                if (ctx->sess_arena)\n"
              "                    ((char *)ctx->sess_arena)[0] ^= 0x20;\n"
              "                rl_vint(&v, (unsigned long long)a.len);",
              1)],
            keys=KF('let s: str = "abc"\n') + KF("len(s)\n") +
                 KF("print(s)\n") + KF("echo finmg5\n"),
            done=b"finmg5")
        self.assertEqual(collect_done_statuses(out), [0, 0, 0, 0])
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 0)
        # "Abc" is never typed: its presence proves the stored
        # string was corrupted through the supposedly pure eval.
        self.assertIn(b"Abc".hex(), kids)

    def test_mutant_len_no_reset_reject_goes_red(self):
        """G-M6: submission arena not reset on bare-expression
        analysis rejects; heavy len-error traffic exhausts the
        carve (rltest len)."""
        out = self._rltest_gmutant(
            "len-no-reset-reject",
            [("user/shell/rl_sem.c",
              "        if (t == RLV_UNKNOWN) {\n"
              "            int k = (rl_streq(diag, RL_D_ARENA_FULL) ||\n"
              "                     rl_streq(diag, RL_D_INTERNAL))\n"
              "                        ? RL_SUB_ARENAFULL\n"
              "                        : RL_SUB_REJECT;\n"
              "            if (rl_streq(diag, RL_D_INTERNAL)) k = RL_SUB_INTERNAL;\n"
              "            rl_arena_reset(&rl_sub);\n"
              "            return rl_fail(k, diag);",
              "        if (t == RLV_UNKNOWN) {\n"
              "            int k = (rl_streq(diag, RL_D_ARENA_FULL) ||\n"
              "                     rl_streq(diag, RL_D_INTERNAL))\n"
              "                        ? RL_SUB_ARENAFULL\n"
              "                        : RL_SUB_REJECT;\n"
              "            if (rl_streq(diag, RL_D_INTERNAL)) k = RL_SUB_INTERNAL;\n"
              "            return rl_fail(k, diag);",
              1)])
        self.assertEqual(collect_done_statuses(out), [1, 0])
        self.assertIn(b"FAIL", terminal_stream(out))

    def test_mutant_len_reserved_goes_red(self):
        """G-M7: `len` made globally reserved; a previously valid
        Slice F program using `len` as an identifier breaks."""
        out = self._gmutant_boot(
            "len-reserved",
            [("user/shell/rl_sem.c",
              "    if (n == 6 && s[0] == 's' && s[1] == 't' && s[2] == 'a' &&\n"
              "        s[3] == 't' && s[4] == 'u' && s[5] == 's')\n"
              "        return 1;\n"
              "    return 0;",
              "    if (n == 6 && s[0] == 's' && s[1] == 't' && s[2] == 'a' &&\n"
              "        s[3] == 't' && s[4] == 'u' && s[5] == 's')\n"
              "        return 1;\n"
              "    if (n == 3 && s[0] == 'l' && s[1] == 'e' && s[2] == 'n')\n"
              "        return 1;\n"
              "    return 0;",
              1)],
            keys=KF("let len: int = 7\n") + KF("echo finmg7\n"),
            done=b"finmg7")
        rows = self._rl_rows(out)
        self.assertIn("[RL] reject SEM_DUPLICATE", rows)
        self.assertEqual(collect_done_statuses(out), [2, 0])

    def test_mutant_len_via_command_goes_red(self):
        """G-M8: the classifier routes len to the external-command
        executor instead of the evaluator; the builtin value never
        materializes (E syntax rejection, no [RL] rows)."""
        out = self._gmutant_boot(
            "len-via-cmd",
            [("user/shell/sh.c",
              "        if (k == RLN_LET) {\n"
              "            rl_sub_reset();\n"
              "            return RLF_LANG;\n"
              "        }",
              "        if (k == RLN_LET) {\n"
              "            rl_sub_reset();\n"
              "            return RLF_LANG;\n"
              "        }\n"
              "        if (k == RLN_LEN) {\n"
              "            rl_sub_reset();\n"
              "            return RLF_E;\n"
              "        }",
              1)],
            keys=KF('len("abc")\n') + KF("echo finmg8\n"),
            done=b"finmg8")
        # Command dispatch: E syntax error, no builtin value, no rows.
        self.assertEqual(collect_done_statuses(out), [2, 0])
        stream = terminal_stream(out)
        self.assertNotIn(b"[RL]", stream)
        self.assertIn("[SH] error syntax", collect_sh_rows(out))

    def test_mutant_len_prefix_match_goes_red(self):
        """G-M9: exact-name check weakened to a prefix match;
        `length(...)` is misaccepted as the builtin."""
        out = self._gmutant_boot(
            "len-prefix",
            [("user/shell/rl_parse.c",
              "    if (len != 3u) return 0;\n"
              "    return src[off] == 'l' && src[off + 1u] == 'e' &&\n"
              "        src[off + 2u] == 'n';",
              "    (void)len;\n"
              "    return src[off] == 'l' && src[off + 1u] == 'e' &&\n"
              "        src[off + 2u] == 'n';",
              1)],
            keys=KF('length("ab") + 7000\n') + KF("echo finmg9\n"),
            done=b"finmg9")
        self.assertEqual(collect_done_statuses(out), [0, 0])
        chunks = self._chunks(out, 2)
        self.assertIn(b"7002", chunks[0])
        self.assertNotIn("[RL] reject SEM_UNKNOWN_FUNCTION",
                         self._rl_rows(out))

    def test_mutant_len_history_bias_goes_red(self):
        """G-M10: rejected len submissions poison later values
        (history-dependent cache); twin sessions diverge."""
        out = self._gmutant_boot(
            "len-bias",
            [("user/shell/rl_sem.c",
              "static struct rl_sess rl_sess;",
              "static struct rl_sess rl_sess;\n"
              "unsigned int rl_len_poison;",
              1),
             ("user/shell/rl_sem.c",
              "                if (nd->aux != 1u) {\n"
              "                    *diag = RL_D_SEM_ARITY;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                if (nd->k1 == RL_NONODE || nd->k1 >= RL_POOL_MAX) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                at = types[nd->k1];\n"
              "                if (at == RLV_UNKNOWN) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                if (at != RLV_STR) {\n"
              "                    *diag = RL_D_SEM_TYPE;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }",
              "                if (nd->aux != 1u) {\n"
              "                    *diag = RL_D_SEM_ARITY;\n"
              "                    ++rl_len_poison;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                if (nd->k1 == RL_NONODE || nd->k1 >= RL_POOL_MAX) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                at = types[nd->k1];\n"
              "                if (at == RLV_UNKNOWN) {\n"
              "                    *diag = RL_D_INTERNAL;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                if (at != RLV_STR) {\n"
              "                    *diag = RL_D_SEM_TYPE;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }",
              1),
             ("user/shell/rl_sem.c",
              "                if (at != RLV_STR) {\n"
              "                    *diag = RL_D_SEM_TYPE;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                types[i] = RLV_INT;",
              "                if (at != RLV_STR) {\n"
              "                    *diag = RL_D_SEM_TYPE;\n"
              "                    ++rl_len_poison;\n"
              "                    return RLV_UNKNOWN;\n"
              "                }\n"
              "                types[i] = RLV_INT;",
              1),
             ("user/shell/rl_eval.c",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                rl_vint(&v, (unsigned long long)a.len);",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                {\n"
              "                    extern unsigned int rl_len_poison;\n"
              "                    rl_vint(&v, (unsigned long long)(a.len +\n"
              "                                                  rl_len_poison));\n"
              "                }",
              1)],
            keys=KF('let s: str = "abc"\n') + KF("len(1)\n") +
                 KF("len(s) * 1000\n") + KF("echo finmg10\n"),
            done=b"finmg10")
        self.assertEqual(collect_done_statuses(out), [0, 2, 0, 0])
        stream = terminal_stream(out)
        self.assertIn(b"4000", stream)
        self.assertNotIn(b"3000", stream)

    def test_mutant_len_depth_bypass_goes_red(self):
        """G-M11: len calls skip the call-group depth charge at the
        opening paren; a 65-deep len expression evaluates (rltest
        len) while pure deep lines still reject."""
        out = self._rltest_gmutant(
            "len-depth-bypass",
            [("user/shell/rl_parse.c",
              "        if (k == RLT_LPAREN && have_value) {\n"
              "            /* Postfix call on the value just parsed. */\n"
              "            unsigned int now = parens + unpend + 1u;\n"
              "            if (now > RL_DEPTH_MAX || oused >= ocap) {\n"
              "                p->err = now > RL_DEPTH_MAX ? RLP_TOODEEP : RLP_NOMEM;\n"
              "                return RLN_NONE;\n"
              "            }",
              "        if (k == RLT_LPAREN && have_value) {\n"
              "            /* Postfix call on the value just parsed. */\n"
              "            unsigned int now = parens + unpend + 1u;\n"
              "            unsigned int skip = 0u;\n"
              "            if (vused > 0u) {\n"
              "                unsigned short tc = vals[vused - 1u];\n"
              "                if (tc != RLN_NONE && tc < p->pool->cap &&\n"
              "                    p->pool->nodes[tc].kind == RLN_VAR &&\n"
              "                    p_is_len_call(src, p->pool->nodes[tc].e1,\n"
              "                                  p->pool->nodes[tc].e2))\n"
              "                    skip = 1u;\n"
              "            }\n"
              "            if ((!skip && now > RL_DEPTH_MAX) ||\n"
              "                oused >= ocap) {\n"
              "                p->err = (!skip && now > RL_DEPTH_MAX) ?\n"
              "                    RLP_TOODEEP : RLP_NOMEM;\n"
              "                return RLN_NONE;\n"
              "            }",
              1)])
        self.assertEqual(collect_done_statuses(out), [1, 0])
        self.assertIn(b"FAIL", terminal_stream(out))

    def test_mutant_len_cstring_scan_goes_red(self):
        """G-M12: length scans for a C NUL instead of using the
        committed length; packed session strings misreport."""
        out = self._gmutant_boot(
            "len-cstring",
            [("user/shell/rl_eval.c",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                rl_vint(&v, (unsigned long long)a.len);",
              "                if (a.type != RLV_STR) return RL_EV_INTERNAL;\n"
              "                {\n"
              "                    unsigned int ci = 0;\n"
              "                    const char *ap = (const char *)a.a;\n"
              "                    while (ci < 8192u && ap[ci] != 0) ++ci;\n"
              "                    rl_vint(&v, (unsigned long long)ci);\n"
              "                }",
              1)],
            keys=KF('let s: str = "abc"\n') +
                 KF('let t: str = "de"\n') +
                 KF("len(s) * 1000\n") + KF("echo finmg12\n"),
            done=b"finmg12")
        self.assertEqual(collect_done_statuses(out), [0, 0, 0, 0])
        stream = terminal_stream(out)
        self.assertIn(b"5000", stream)
        self.assertNotIn(b"3000", stream)


if __name__ == "__main__":
    unittest.main()
