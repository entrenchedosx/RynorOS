"""Stage 19e BE-C structured control flow: comparisons, if/else, while,
break, and continue with exact two-pass branch sizing.

The baby backend (`rynorlang/selfhost/emit.rl`, core dialect) lowers
conditions to canonical bool values (setcc+movzx, 0/1) tested with
`test eax,eax`, and control flow to fixed-width rel32 branches
(`0F 84` jz, `E9` jmp) with displacements computed from pass-1 sizes.
`&&`/`||` stay eager bitwise operations per the frozen semantics (the
host oracle evaluates both sides; no observable side channel exists
in this subset). RynorLang has no assignment: loop progress comes
from return/break, so taken `continue` on a true condition diverges
by construction (proven by step-cap cases, not hidden).

QEMU and a native toolchain are unavailable here, so execution is
proven by the test-only emulator below (BE-B emulator plus exactly
the branch forms the backend emits: `85 C0` test, `0F 84` jz rel32,
`E9` jmp rel32, with ZF/SF/CF/OF modeled for those instructions).
Reference semantics come from the trusted host oracle (exit low 32
bits, EBX width).
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit_calls as beb  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402


MASK64 = bea.MASK64


class Emu(beb.Emu):
    def step(self):
        b0 = self._fetch(1)[0]
        R = self.reg
        if b0 == 0x85:
            b1 = self._fetch(1)[0]
            assert b1 == 0xC0, f"bad test {b1:#x}"
            r = R["rax"] & 0xFFFFFFFF
            self.cf = 0
            self.of = 0
            self.zf = 1 if r == 0 else 0
            self.sf = 1 if r >= (1 << 31) else 0
        elif b0 == 0xE9:
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            self.rip = (self.rip + disp) & MASK64
        elif b0 == 0x0F:
            b1 = self._fetch(1)[0]
            if b1 != 0x84:
                self.rip -= 2
                super().step()
                return
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            if self.zf:
                self.rip = (self.rip + disp) & MASK64
        else:
            self.rip -= 1
            super().step()


def run_image(code: bytes):
    return Emu(code).run()


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


def _oracle_exit(src):
    return bea._oracle_exit(src)


def _parse_rnyx(raw):
    return bea._parse_rnyx(raw)


def _mkmain(main: bytes):
    call = b"\xe8" + struct.pack("<i", 14 - 5)
    start = call + bytes([0x89, 0xC3, 0xB8, 0, 0, 0, 0, 0xCD, 0x80])
    assert len(start) == 14
    return start + bytes([0x55, 0x48, 0x89, 0xE5]) + main


ACCEPT_CASES = [
    ("cmp-eq-t", "fn main(): int { if 1 == 1 { return 17; } else { return 93; } }\n"),
    ("cmp-eq-f", "fn main(): int { if 1 == 2 { return 17; } else { return 93; } }\n"),
    ("cmp-ne-t", "fn main(): int { if 1 != 2 { return 17; } else { return 93; } }\n"),
    ("cmp-ne-f", "fn main(): int { if 1 != 1 { return 17; } else { return 93; } }\n"),
    ("cmp-lt-t", "fn main(): int { if 1 < 2 { return 17; } else { return 93; } }\n"),
    ("cmp-lt-f", "fn main(): int { if 2 < 2 { return 17; } else { return 93; } }\n"),
    ("cmp-le-t", "fn main(): int { if 2 <= 2 { return 17; } else { return 93; } }\n"),
    ("cmp-le-f", "fn main(): int { if 3 <= 2 { return 17; } else { return 93; } }\n"),
    ("cmp-gt-t", "fn main(): int { if 3 > 2 { return 17; } else { return 93; } }\n"),
    ("cmp-gt-f", "fn main(): int { if 2 > 3 { return 17; } else { return 93; } }\n"),
    ("cmp-ge-t", "fn main(): int { if 3 >= 3 { return 17; } else { return 93; } }\n"),
    ("cmp-ge-f", "fn main(): int { if 2 >= 3 { return 17; } else { return 93; } }\n"),
    ("cmp-neg", "fn main(): int { if (0 - 1) < 1 { return 17; } else { return 93; } }\n"),
    ("cmp-eq-neg", "fn main(): int { if (0 - 3) == (0 - 3) { return 17; } else { return 93; } }\n"),
    ("cmp-wide-f", "fn main(): int { if (0 - 1) == 5 { return 17; } else { return 93; } }\n"),
    ("bool-eq-t", "fn main(): int { let a: bool = true; let b: bool = true; if a == b { return 17; } else { return 93; } }\n"),
    ("bool-eq-f", "fn main(): int { let a: bool = true; let b: bool = false; if a == b { return 17; } else { return 93; } }\n"),
    ("bool-ne-t", "fn main(): int { let a: bool = true; let b: bool = false; if a != b { return 17; } else { return 93; } }\n"),
    ("if-true", "fn main(): int { if true { return 17; } return 93; }\n"),
    ("if-false", "fn main(): int { if false { return 17; } return 93; }\n"),
    ("if-bool-local", "fn main(): int { let b: bool = 1 == 1; if b { return 17; } else { return 93; } }\n"),
    ("if-not", "fn main(): int { if !false { return 17; } else { return 93; } }\n"),
    ("if-and", "fn main(): int { if true && true { return 17; } else { return 93; } }\n"),
    ("if-or", "fn main(): int { if false || true { return 17; } else { return 93; } }\n"),
    ("if-empty-then", "fn main(): int { if false { } return 41; }\n"),
    ("if-nested-tt", "fn main(): int { if true { if true { return 1; } else { return 2; } } else { return 3; } }\n"),
    ("if-nested-tf", "fn main(): int { if true { if false { return 1; } else { return 2; } } else { return 3; } }\n"),
    ("if-nested-f", "fn main(): int { if false { return 1; } else { if true { return 2; } else { return 3; } } }\n"),
    ("if-helper", "fn pick(x: int): int { if x == 0 { return 7; } else { return 9; } }\nfn main(): int { return pick(0) + pick(5); }\n"),
    ("if-call-cond", "fn dbl(x: int): int { return x * 2; }\nfn main(): int { if dbl(2) == 4 { return 17; } else { return 93; } }\n"),
    ("if-call-then", "fn f(x: int): int { return x + 1; }\nfn main(): int { if true { return f(3); } else { return 0; } }\n"),
    ("if-call-else", "fn f(x: int): int { return x + 1; }\nfn main(): int { if false { return 0; } else { return f(3); } }\n"),
    ("if-elif-1", "fn main(): int { if 1 < 0 { return 1; } else { if 1 == 0 { return 2; } else { return 3; } } }\n"),
    ("if-elif-2", "fn main(): int { if 0 < 0 { return 1; } else { if 0 == 0 { return 2; } else { return 3; } } }\n"),
    ("if-elif-3", "fn main(): int { if 5 < 0 { return 1; } else { if 5 == 0 { return 2; } else { return 3; } } }\n"),
    ("while-false", "fn main(): int { while false { return 99; } return 7; }\n"),
    ("while-cmp-false", "fn main(): int { while 1 < 0 { return 99; } return 8; }\n"),
    ("while-call-false", "fn id0(x: int): int { return x; }\nfn main(): int { while id0(0) == 1 { return 99; } return 6; }\n"),
    ("while-true-ret", "fn main(): int { while true { return 5; } return 6; }\n"),
    ("while-true-brk", "fn main(): int { while true { break; } return 8; }\n"),
    ("while-empty-false", "fn main(): int { while false { } return 6; }\n"),
    ("while-nested-ret", "fn main(): int { while true { while true { return 11; } } return 12; }\n"),
    ("while-in-helper", "fn f(x: int): int { while x == 0 { return 3; } return 4; }\nfn main(): int { return f(0) + f(1); }\n"),
    ("while-call-body", "fn f(x: int): int { return x + 10; }\nfn main(): int { while true { return f(5); } return 0; }\n"),
    ("brk-immediate", "fn main(): int { while true { break; return 1; } return 2; }\n"),
    ("brk-in-if-taken", "fn main(): int { while true { if 1 == 1 { break; } return 1; } return 2; }\n"),
    ("brk-in-if-skip", "fn main(): int { while true { if 1 == 2 { break; } return 3; } return 4; }\n"),
    ("nested-inner-brk", "fn main(): int { while true { while true { break; } break; } return 13; }\n"),
    ("nested-inner-ret", "fn main(): int { while true { while true { return 14; } } return 15; }\n"),
    ("cont-after-brk", "fn main(): int { while true { break; continue; } return 4; }\n"),
    ("cont-guarded", "fn main(): int { while true { if false { continue; } break; } return 5; }\n"),
    ("inner-cont-outer-brk", "fn main(): int { while true { while true { if false { continue; } break; } break; } return 16; }\n"),
    ("ret-in-if", "fn pick(x: int): int { if x == 1 { return 21; } return 22; }\nfn main(): int { return pick(1) + pick(2); }\n"),
    ("ret-in-loop", "fn main(): int { while true { while true { return 23; } } return 24; }\n"),
    ("cond-call-mixed", "fn dbl(x: int): int { return x * 2; }\nfn main(): int { let y: int = dbl(3); if y == 6 { return 25; } else { return 26; } }\n"),
    ("else-fallthrough", "fn main(): int { if true { 1 + 2; } else { return 93; } return 17; }\n"),
    ("branch-then-call", "fn f(x: int): int { return x * 3; }\nfn g(x: int): int { if x == 1 { return f(4); } return f(5); }\nfn main(): int { return g(1) + g(2); }\n"),
]

DIVERGE_CASES = [
    ("div-empty-true", "fn main(): int { while true { } return 5; }\n"),
    ("div-continue-true", "fn main(): int { while true { continue; } return 5; }\n"),
]

REJECT25_CASES = [
    ("while-match", "fn main(): int { while true { match 1 { 1 => { break; }, _ => { return 0; } } } return 2; }\n"),
    ("loop-recur", "fn foo(x: int): int { while x == 0 { return foo(x); } return 1; }\nfn main(): int { return foo(0); }\n"),
    ("cond-div", "fn main(): int { if 4 / 2 == 2 { return 1; } else { return 0; } }\n"),
]

REJECT_CHECK_CASES = [
    ("break-outside", "fn main(): int { break; return 0; }\n", "SEM_TYPE_MISMATCH"),
    ("continue-outside", "fn main(): int { continue; return 0; }\n", "SEM_TYPE_MISMATCH"),
    ("if-int-cond", "fn main(): int { if 1 { return 0; } return 1; }\n", "SEM_TYPE_MISMATCH"),
    ("while-int-cond", "fn main(): int { while 2 { return 0; } return 1; }\n", "SEM_TYPE_MISMATCH"),
    ("ret-bool-in-if", "fn main(): int { if true { return true; } return 0; }\n", "SEM_TYPE_MISMATCH"),
]


class BECAcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, ACCEPT_CASES)
        cls.images = []
        for (code, off, hexstr), (name, src) in zip(cls.results, ACCEPT_CASES):
            assert code == 0, (name, code, off)
            raw = bytes.fromhex(hexstr)
            code_bytes, code_sz = _parse_rnyx(raw)
            want = _oracle_exit(src)
            got = run_image(code_bytes)
            assert got == want, (name, got, want)
            cls.images.append((name, hexstr, code_sz, want))

    def test_01_exit_behavior(self):
        self.assertGreaterEqual(len(self.images), 40)

    def test_02_determinism(self):
        again = _run_be(self.combo, ACCEPT_CASES)
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _s, _w in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        exits = set(w for _n, _h, _s, w in self.images)
        self.assertGreater(len(exits), 10)

    def test_04_code_sizes_bounded(self):
        for name, _h, sz, _w in self.images:
            self.assertGreaterEqual(sz, 1)
            self.assertLessEqual(sz, 65536)

    def test_05_branch_direction_matters(self):
        by_name = {n: w for n, _h, _s, w in self.images}
        self.assertEqual(by_name["cmp-eq-t"], 17)
        self.assertEqual(by_name["cmp-eq-f"], 93)
        self.assertNotEqual(by_name["if-nested-tt"], by_name["if-nested-tf"])


class BECRejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, REJECT_CHECK_CASES)
        cls.rdiv = _run_be(cls.combo, DIVERGE_CASES)

    def test_06_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_07_checker_passthrough(self):
        want_code = {"SEM_TYPE_MISMATCH": 11}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src = case[0], case[1]
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertFalse(hr.ok)
            self.assertEqual(code, want_code[hr.diagnostic.code], name)
            self.assertEqual(hexstr, "", name)

    def test_08_divergence_proves_backedge(self):
        for (code, _off, hexstr), (name, _src) in zip(self.rdiv, DIVERGE_CASES):
            self.assertEqual(code, 0, name)
            raw = bytes.fromhex(hexstr)
            code_bytes, _sz = _parse_rnyx(raw)
            with self.assertRaises(RuntimeError, msg=name):
                run_image(code_bytes)


class BECOpcodeTests(unittest.TestCase):
    def test_09_test_jz_taken(self):
        main = (bytes([0xB8, 0, 0, 0, 0])
                + bytes([0x85, 0xC0])
                + bytes([0x0F, 0x84, 5, 0, 0, 0])
                + bytes([0xB8, 0x6F, 0, 0, 0])
                + bytes([0xC9, 0xC3, 0xCC]))
        self.assertEqual(run_image(_mkmain(main)), 0)

    def test_10_test_jz_not_taken(self):
        main = (bytes([0xB8, 1, 0, 0, 0])
                + bytes([0x85, 0xC0])
                + bytes([0x0F, 0x84, 5, 0, 0, 0])
                + bytes([0xB8, 0x6F, 0, 0, 0])
                + bytes([0xC9, 0xC3, 0xCC]))
        self.assertEqual(run_image(_mkmain(main)), 111)

    def test_11_jmp_skips(self):
        main = (bytes([0xE9, 5, 0, 0, 0])
                + bytes([0xB8, 0x6F, 0, 0, 0])
                + bytes([0xC9, 0xC3, 0xCC]))
        self.assertEqual(run_image(_mkmain(main)), 0)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


class BECMutantTests(unittest.TestCase):
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
        code_bytes, _sz = _parse_rnyx(raw)
        try:
            return run_image(code_bytes) != want
        except RuntimeError:
            return True

    def _idx(self, name):
        for i, (n, _s) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_bc_m1_invert_cond(self):
        combo = _mutated_combo(
            "fn e_jcc_z(disp: int, acc: int): int {\n  let a0: int = e_b(15, acc);\n  let a1: int = e_b(132, a0);",
            "fn e_jcc_z(disp: int, acc: int): int {\n  let a0: int = e_b(15, acc);\n  let a1: int = e_b(133, a0);")
        self.assertTrue(self._red_on(combo, self._idx("cmp-eq-t")))

    def test_bc_m2_rel32_origin(self):
        combo = _mutated_combo(
            "fn be_rel32(targ: int, pos: int, len: int): int {\n  return targ - (pos + len);\n}",
            "fn be_rel32(targ: int, pos: int, len: int): int {\n  return targ - pos;\n}")
        self.assertTrue(self._red_on(combo, self._idx("cmp-eq-f")))

    def test_bc_m3_fwd_plus_one(self):
        combo = _mutated_combo(
            "  let jo: int = e_jcc_z(be_rel32(tt + sz_jcc() + th->n + hasel * sz_jmp(), tt, sz_jcc()), tt);",
            "  let jo: int = e_jcc_z(be_rel32(tt + sz_jcc() + th->n + hasel * sz_jmp(), tt, sz_jcc()) + 1, tt);")
        self.assertTrue(self._red_on(combo, self._idx("cmp-eq-f")))

    def test_bc_m4_backedge_omitted(self):
        combo = _mut(_combo_text(),
                     "  return BZ(p: be, n: acc + c->n + sz_test() + sz_jcc() + bd->n + sz_jmp(), c: 0, o: 0);",
                     "  return BZ(p: be, n: acc + c->n + sz_test() + sz_jcc() + bd->n, c: 0, o: 0);")
        combo = _mut(combo,
                     "  let ko: int = e_jmp_rel(be_rel32(acc, bb->n, sz_jmp()), bb->n);",
                     "  let ko: int = bb->n;")
        name, src = ("div-empty-true", "fn main(): int { while true { } return 5; }\n")
        (code, _off, hexstr) = _run_be(combo, [(name, src)])[0]
        self.assertEqual(code, 0)
        raw = bytes.fromhex(hexstr)
        code_bytes, _sz = _parse_rnyx(raw)
        self.assertEqual(run_image(code_bytes), 5)

    def test_bc_m5_cond_ignored(self):
        combo = _mutated_combo(
            "  let jo: int = e_jcc_z(be_rel32(eo, tt, sz_jcc()), tt);",
            "  let jo: int = e_jcc_z(be_rel32(tt + sz_jcc(), tt, sz_jcc()), tt);")
        self.assertTrue(self._red_on(combo, self._idx("while-false")))

    def test_bc_m6_break_outer(self):
        combo = _mutated_combo(
            "  return BZ(p: nx->p, n: e_jmp_rel(be_rel32(bx, acc, sz_jmp()), acc), c: 0, o: 0);",
            "  return BZ(p: nx->p, n: e_jmp_rel(be_rel32(bc, acc, sz_jmp()), acc), c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("nested-inner-brk")))

    def test_bc_m7_continue_outer(self):
        combo = _mutated_combo(
            "  return BZ(p: nx->p, n: e_jmp_rel(be_rel32(bc, acc, sz_jmp()), acc), c: 0, o: 0);",
            "  return BZ(p: nx->p, n: e_jmp_rel(be_rel32(bx, acc, sz_jmp()), acc), c: 0, o: 0);")
        name, src = ("nest-cont", "fn main(): int { while true { while true { continue; } break; } return 9; }\n")
        (code, _off, hexstr) = _run_be(combo, [(name, src)])[0]
        self.assertEqual(code, 0)
        raw = bytes.fromhex(hexstr)
        code_bytes, _sz = _parse_rnyx(raw)
        self.assertEqual(run_image(code_bytes), 9)

    def test_bc_m8_else_jmp_omitted(self):
        combo = _mut(_combo_text(),
                     "  return BZ(p: be, n: acc + cn + sz_test() + sz_jcc() + tn + sz_jmp() + el->n, c: 0, o: 0);",
                     "  return BZ(p: be, n: acc + cn + sz_test() + sz_jcc() + tn + el->n, c: 0, o: 0);")
        combo = _mut(combo,
                     "  let jp: int = e_jmp_rel(be_rel32(eo, acc, sz_jmp()), acc);",
                     "  let jp: int = acc;")
        self.assertTrue(self._red_on(combo, self._idx("else-fallthrough")))

    def test_bc_m9_cmp_swapped(self):
        combo = _mutated_combo(
            "  if op == 8 { return 156; } else { }",
            "  if op == 8 { return 158; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("cmp-lt-f")))

    def test_bc_m10_no_normalize(self):
        combo = _combo_text()
        for op in (6, 7, 8, 9, 10, 11):
            combo = _mut(combo,
                         "  if op == %d { return 14; } else { }" % op,
                         "  if op == %d { return 11; } else { }" % op)
        combo = _mut(combo,
                     "fn be_combine_cmp(op: int, acc: int): int {\n  let a0: int = e_cmp_rax_rcx(acc);\n  let a1: int = e_setcc(be_cc(op), a0);\n  let a2: int = e_movzx_eax_al(a1);\n  return a2;\n}",
                     "fn be_combine_cmp(op: int, acc: int): int {\n  let a0: int = e_cmp_rax_rcx(acc);\n  let a1: int = e_setcc(be_cc(op), a0);\n  return a1;\n}")
        self.assertTrue(self._red_on(combo, self._idx("cmp-wide-f")))

    def test_bc_m11_jcc_size_wrong(self):
        combo = _mutated_combo(
            "fn sz_jcc(): int {\n  return 6;\n}",
            "fn sz_jcc(): int {\n  return 5;\n}")
        self.assertTrue(self._red_on(combo, self._idx("cmp-eq-t")))

    def test_bc_m12_ctx_pop_omitted(self):
        combo = _mutated_combo(
            "  let bb: BZ = be_e_block(src, f, fs, fe, bo->p, be, jo, eo, acc);",
            "  let bb: BZ = be_e_block(src, f, fs, fe, bo->p, be, jo, acc, acc);")
        self.assertTrue(self._red_on(combo, self._idx("nested-inner-brk")))


if __name__ == "__main__":
    unittest.main()
