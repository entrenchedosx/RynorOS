"""Stage 19e M7 bare byte_at in operand positions (if/while conditions, call args).

M7 connects the operand-level `unwrap_or(byte_at(s, i), d)` shape in the baby
backend (`rynorlang/selfhost/emit.rl`, core dialect): `be_s_unwrap_call` /
`be_e_unwrap_call` route `byte_at` to the PROVEN G4 fused template
(`be_[se]_unwrap_agg_byteat` with dk=0/ds=0) instead of failing closed at 25.
The fused template leaves the int in rax with balanced caller stack and no
home writes, which is exactly the operand contract, so one connection covers
if conditions, while conditions, call arguments, and nested defaults.

Unblocks 23 census rows at first-blocker (util beq/find_last/line_of, lex
scan_ident/next_tok, 15 check scanners, 3 emit fns), including beq, the most
called helper in the bundle. No new machine forms: every emitted byte uses
G4-era forms the strbyte emulator already executes (push/pop/test/js/jae/jmp,
mov/movzx/cmp). The G4-orphaned `be_[se]_unwrap_byteat*` helpers stay dead
(their emit side never emits the default and passes sizes as offsets);
a separate cleanup may remove them.

Out of scope (stay backend-25): literal-str scrutinee in conditions (fused
var-only rule), `fread`/`fjoin`/`argv`/`push` calls in operand unwrap,
standalone `byte_at` statements.

QEMU/native proof lives in probe_m7native.py (MATCH at M7 commit); here
execution is proven by the test-only G4 strbyte emulator (it EXECUTES baby
bytes; it never generates code). Reference semantics come from the trusted
host oracle (exit low 32 bits). RYNX extent (`len(raw) == 28 + csz + fsz`)
is asserted per case; every branch/call target must land on a swept
instruction boundary (fail-closed sweep imported from the M6 suite).
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_strbyte as g4  # noqa: E402
from tests.repository.test_rynorlang_selfhost_emit_fjoin import _sweep_starts  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402


MASK64 = bea.MASK64
CODE_BASE = bea.CODE_BASE
STACK_TOP = bea.STACK_TOP

Emu = g4.Emu


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


def _oracle_exit(src):
    return bea._oracle_exit(src)


def run_image_data(raw: bytes):
    ver, arch, hlen, res, entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
    assert (ver, arch, hlen, res, entry) == (2, 1, 28, 0, 0)
    assert fsz == msz, (fsz, msz)
    assert 1 <= csz <= 65536 and 0 <= fsz <= 32768, (csz, fsz)
    code, data = raw[28:28 + csz], raw[28 + csz:28 + csz + fsz]
    assert len(code) == csz and len(data) == fsz and len(raw) == 28 + csz + fsz
    em = Emu(code, data)
    out = em.run()
    assert em.reg["rsp"] == STACK_TOP, "RSP imbalance"
    return out


ACCEPT_CASES = [
    ("m7-if-hit", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 0), 0) == 97 { return 1; } return 0; }\n'),
    ("m7-if-miss", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 1), 0) == 97 { return 1; } return 0; }\n'),
    ("m7-if-oob", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 9), 7) == 7 { return 1; } return 0; }\n'),
    ("m7-if-neg", 'fn main(): int { let s: str = "abc"; if unwrap_or(byte_at(s, 0 - 1), 0 - 2) == 0 - 2 { return 1; } return 0; }\n'),
    ("m7-beq-twin-eq", 'fn main(): int { let s: str = "ab"; let t: str = "ab"; if unwrap_or(byte_at(s, 0), 0 - 1) == unwrap_or(byte_at(t, 0), 0 - 2) { return 1; } return 0; }\n'),
    ("m7-beq-twin-ne", 'fn main(): int { let s: str = "ab"; let t: str = "cb"; if unwrap_or(byte_at(s, 0), 0 - 1) == unwrap_or(byte_at(t, 0), 0 - 2) { return 1; } return 0; }\n'),
    ("m7-while-hit", 'fn main(): int { let s: str = "ab"; let i: int = 0; while unwrap_or(byte_at(s, i), 0) != 0 { return 1; } return 0; }\n'),
    ("m7-while-skip", 'fn main(): int { let s: str = "ab"; let i: int = 9; while unwrap_or(byte_at(s, i), 0) != 0 { return 1; } return 0; }\n'),
    ("m7-call-arg", 'fn id(x: int): int { return x; }\nfn main(): int { let s: str = "ab"; return id(unwrap_or(byte_at(s, 1), 0)); }\n'),
    ("m7-nested-default", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 9), unwrap_or(byte_at(s, 0), 0)) == 97 { return 1; } return 0; }\n'),
    ("m7-param", 'fn find_ch(s: str, ch: int, i: int): int { if unwrap_or(byte_at(s, i), 0) == ch { return i; } return 0 - 1; }\nfn main(): int { return find_ch("ab", 98, 1); }\n'),
    ("m7-expr-idx", 'fn main(): int { let s: str = "abc"; let i: int = 1; if unwrap_or(byte_at(s, i + 1), 0) == 99 { return 1; } return 0; }\n'),
    ("m7-elseif", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 0), 0) == 98 { return 1; } else { if unwrap_or(byte_at(s, 0), 0) == 97 { return 2; } else { } } return 0; }\n'),
    ("m7-lt", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 0), 0) < 100 { return 1; } return 0; }\n'),
    ("m7-ne", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 0), 0) != 98 { return 1; } return 0; }\n'),
    ("m7-str-param", 'fn f(s: str): int { if unwrap_or(byte_at(s, 0), 0) == 104 { return 1; } return 0; }\nfn main(): int { return f("hi"); }\n'),
]

REJECT25_CASES = [
    ("r7-lit-cond", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at("hi", 0), 7) == 104 { return 1; } return 0; }\n'),
    ("r7-fread-cond", 'fn main(): int { let s: str = "ab"; if unwrap_or(fread("f", 0, 1), "d") == s { return 1; } return 0; }\n'),
]


class M7AcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, [(n, s) for n, s in ACCEPT_CASES])
        cls.images = []
        for (code, off, hexstr), (name, src) in zip(cls.results, ACCEPT_CASES):
            assert code == 0, (name, code, off)
            raw = bytes.fromhex(hexstr)
            want = _oracle_exit(src)
            got = run_image_data(raw)
            assert got == want, (name, got, want)
            cls.images.append((name, hexstr, want))

    def test_01_exit_behavior(self):
        self.assertGreaterEqual(len(self.images), 16)

    def test_02_determinism(self):
        again = _run_be(self.combo, [(n, s) for n, s in ACCEPT_CASES])
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        exits = set(w for _n, _h, w in self.images)
        self.assertGreaterEqual(len(exits), 4)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_value_spots(self):
        by_name = {n: w for n, _h, w in self.images}
        self.assertEqual(by_name["m7-if-hit"], 1)
        self.assertEqual(by_name["m7-if-miss"], 0)
        self.assertEqual(by_name["m7-if-oob"], 1)
        self.assertEqual(by_name["m7-if-neg"], 1)
        self.assertEqual(by_name["m7-beq-twin-eq"], 1)
        self.assertEqual(by_name["m7-beq-twin-ne"], 0)
        self.assertEqual(by_name["m7-while-hit"], 1)
        self.assertEqual(by_name["m7-while-skip"], 0)
        self.assertEqual(by_name["m7-call-arg"], 98)
        self.assertEqual(by_name["m7-nested-default"], 1)
        self.assertEqual(by_name["m7-param"], 1)
        self.assertEqual(by_name["m7-expr-idx"], 1)
        self.assertEqual(by_name["m7-elseif"], 2)
        self.assertEqual(by_name["m7-lt"], 1)
        self.assertEqual(by_name["m7-ne"], 1)
        self.assertEqual(by_name["m7-str-param"], 1)

    def test_06_no_callee_saved_scratch(self):
        for name, hexstr, _w in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            for i, b in enumerate(code):
                self.assertNotIn(b, (0x53, 0x5B), f"rbx push/pop in {name} at {i:#x}")

    def test_07_rnyx_extent_exact(self):
        for name, hexstr, _w in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertEqual(fsz, msz, name)
            self.assertEqual(len(raw), 28 + csz + fsz, name)

    def test_08_branch_targets_on_boundaries(self):
        # G4 lesson, enforced: every static branch/call displacement
        # lands on a swept instruction boundary inside the code
        # segment; every call lands on a 0x55 prologue. The sweep is
        # fail-closed (unknown opcode raises, it never skips).
        for name, hexstr, _w in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            starts = _sweep_starts(code)
            for s in sorted(starts):
                b0 = code[s]
                if b0 == 0xE8 or b0 == 0xE9:
                    disp = int.from_bytes(code[s + 1:s + 5], "little", signed=True)
                    target = s + 5 + disp
                    self.assertIn(target, starts, f"{name} branch@{s:#x}->{target:#x}")
                    if b0 == 0xE8:
                        self.assertEqual(code[target], 0x55, f"{name} call@{s:#x}")
                elif b0 == 0x0F and code[s + 1] in (0x84, 0x85, 0x83, 0x88):
                    disp = int.from_bytes(code[s + 2:s + 6], "little", signed=True)
                    target = s + 6 + disp
                    self.assertIn(target, starts, f"{name} jcc@{s:#x}->{target:#x}")


class M7RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)

    def test_09_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


SIZE_ROUTE_OLD = "  if be_is_byteat(src, a) == 1 { return BZ(p: t->s, n: 0, c: 25, o: a->s); } else { }"
EMIT_ROUTE_OLD = "  if be_is_byteat(src, a) == 1 { return BZ(p: t->s, n: acc, c: 25, o: a->s); } else { }"


class M7MutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()

    def _red_on(self, mutant_combo, idx):
        name, src = ACCEPT_CASES[idx]
        want = _oracle_exit(src)
        try:
            (code, _off, hexstr) = _run_be(mutant_combo, [(name, src)])[0]
        except (AssertionError, RuntimeError):
            return True
        if code != 0:
            return True
        raw = bytes.fromhex(hexstr)
        try:
            return run_image_data(raw) != want
        except (RuntimeError, AssertionError):
            return True

    def _idx(self, name):
        for i, (n, _s) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_m7_m1_size_route_reverted(self):
        # Revert the size-side connection: cond cases fail closed at 25.
        combo = _mut(_combo_text(),
                     "  if be_is_byteat(src, a) == 1 { return be_s_unwrap_agg_byteat(src, f, fs, fe, 0, 0, tscal(1), t, t, end); } else { }",
                     SIZE_ROUTE_OLD)
        self.assertTrue(self._red_on(combo, self._idx("m7-if-hit")))

    def test_m7_m2_emit_route_reverted(self):
        # Revert the emit-side connection: size accepts, emit fails at 25.
        combo = _mut(_combo_text(),
                     "  if be_is_byteat(src, a) == 1 { return be_e_unwrap_agg_byteat(src, f, fs, fe, 0, 0, tscal(1), t, t, end, acc); } else { }",
                     EMIT_ROUTE_OLD)
        self.assertTrue(self._red_on(combo, self._idx("m7-if-hit")))

    def test_m7_m3_size_wrong_ds(self):
        # Size side stores to home 1 while emit leaves rax: size/emit
        # disagree, so be_main fails closed at 28.
        combo = _mut(_combo_text(),
                     "  if be_is_byteat(src, a) == 1 { return be_s_unwrap_agg_byteat(src, f, fs, fe, 0, 0, tscal(1), t, t, end); } else { }",
                     "  if be_is_byteat(src, a) == 1 { return be_s_unwrap_agg_byteat(src, f, fs, fe, 0, 1, tscal(1), t, t, end); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m7-if-hit")))

    def test_m7_m4_size_route_to_index(self):
        # Route byte_at at the index handler (expects a list variable,
        # gets a builtin call): resolution fails fast at 29.
        combo = _mut(_combo_text(),
                     "  if be_is_byteat(src, a) == 1 { return be_s_unwrap_agg_byteat(src, f, fs, fe, 0, 0, tscal(1), t, t, end); } else { }",
                     "  if be_is_byteat(src, a) == 1 { return be_s_unwrap_index(src, f, fs, fe, t, a, end); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m7-if-hit")))

    def test_m7_m5_emit_acc_shifted(self):
        # Emit one byte past the true offset: every later displacement
        # is wrong, so execution diverges or the image is malformed.
        combo = _mut(_combo_text(),
                     "  if be_is_byteat(src, a) == 1 { return be_e_unwrap_agg_byteat(src, f, fs, fe, 0, 0, tscal(1), t, t, end, acc); } else { }",
                     "  if be_is_byteat(src, a) == 1 { return be_e_unwrap_agg_byteat(src, f, fs, fe, 0, 0, tscal(1), t, t, end, acc + 1); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m7-beq-twin-eq")))

