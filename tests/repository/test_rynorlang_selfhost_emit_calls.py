"""Stage 19e BE-B scalar locals and direct function calls.

Extends the baby backend (`rynorlang/selfhost/emit.rl`, core dialect)
with scalar local homes, integer parameters (SysV-subset: rdi, rsi,
rdx, rcx, r8, r9, max 6, return rax), and direct same-unit calls with
stack-balanced argument marshaling. No control flow, aggregates,
modules, or recursion (direct self-calls are backend-25).

QEMU and a native toolchain are unavailable here, so execution is
proven by the test-only emulator below (subclass of the BE-A emulator
plus exactly the call/frame forms the backend emits: pop rdi/rsi/rdx,
pop r8/r9, 48-form param spills, 4C-form r8/r9 spills, E8 call). It
EXECUTES baby bytes; it never generates code. Reference semantics
come from the trusted host oracle (exit low 32 bits, EBX width).
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402


MASK64 = bea.MASK64


class Emu(bea.Emu):
    def step(self):
        b0 = self._fetch(1)[0]
        R = self.reg
        if b0 == 0x5A:
            R["rdx"] = self._pop()
        elif b0 == 0x5E:
            R["rsi"] = self._pop()
        elif b0 == 0x5F:
            R["rdi"] = self._pop()
        elif b0 == 0x41:
            b1 = self._fetch(1)[0]
            if b1 == 0x58:
                R["r8"] = self._pop()
            elif b1 == 0x59:
                R["r9"] = self._pop()
            else:
                raise RuntimeError(f"bad 41 {b1:#x}")
        elif b0 == 0x4C:
            self._rex4c()
        else:
            self.rip -= 1
            super().step()

    def _rex(self):
        off = self.rip - bea.CODE_BASE
        b1 = self.code[off]
        if b1 == 0x89 and self.code[off + 1] in (0x7D, 0x75, 0x55, 0x4D):
            R = self.reg
            self._fetch(1)
            b2 = self._fetch(1)[0]
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            if b2 == 0x7D:
                val = R["rdi"]
            elif b2 == 0x75:
                val = R["rsi"]
            elif b2 == 0x55:
                val = R["rdx"]
            else:
                val = R["rcx"]
            self._swrite((R["rbp"] + d) & MASK64, 8, val)
        else:
            super()._rex()

    def _rex4c(self):
        R = self.reg
        b1 = self._fetch(1)[0]
        if b1 != 0x89:
            raise RuntimeError(f"bad rex4c {b1:#x}")
        b2 = self._fetch(1)[0]
        if b2 == 0x45:
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            self._swrite((R["rbp"] + d) & MASK64, 8, R["r8"])
        elif b2 == 0x4D:
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            self._swrite((R["rbp"] + d) & MASK64, 8, R["r9"])
        else:
            raise RuntimeError(f"bad rex4c89 {b2:#x}")


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


ACCEPT_CASES = [
    ("loc-1", "fn main(): int { let x: int = 5; return x; }\n"),
    ("loc-2", "fn main(): int { let x: int = 5; let y: int = 7; return x + y; }\n"),
    ("loc-3", "fn main(): int { let x: int = 2; let y: int = 3; let z: int = 4; return x * y + z; }\n"),
    ("loc-expr", "fn main(): int { let x: int = (2 + 3) * 4; return x - 1; }\n"),
    ("loc-reuse", "fn main(): int { let x: int = 3; return x * x + x; }\n"),
    ("loc-first", "fn main(): int { let x: int = 11; let y: int = 22; return x; }\n"),
    ("loc-second", "fn main(): int { let x: int = 11; let y: int = 22; return y; }\n"),
    ("loc-bool", "fn main(): int { let b: bool = 1 == 1; let x: int = 9; return x; }\n"),
    ("id-1", "fn id(x: int): int { return x; }\nfn main(): int { return id(7); }\n"),
    ("add-2", "fn add(a: int, b: int): int { return a + b; }\nfn main(): int { return add(2, 3); }\n"),
    ("sub-order", "fn sub(a: int, b: int): int { return a - b; }\nfn main(): int { return sub(9, 2); }\n"),
    ("sub-rev", "fn sub(a: int, b: int): int { return a - b; }\nfn main(): int { return sub(2, 9); }\n"),
    ("three-params", "fn f(a: int, b: int, c: int): int { return a + b * c; }\nfn main(): int { return f(1, 2, 3); }\n"),
    ("six-params", "fn f(a: int, b: int, c: int, d: int, e: int, g: int): int { return a + b + c + d + e + g; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6); }\n"),
    ("param-reuse", "fn f(a: int, b: int): int { return a * a + b; }\nfn main(): int { return f(3, 4); }\n"),
    ("param-reorder", "fn f(a: int, b: int): int { return b - a; }\nfn main(): int { return f(1, 10); }\n"),
    ("arg-order", "fn f(a: int, b: int): int { return a - b; }\nfn main(): int { return f(1, 10); }\n"),
    ("one-helper", "fn neg(x: int): int { return 0 - x; }\nfn main(): int { return neg(5); }\n"),
    ("zero-arg", "fn seven(): int { return 7; }\nfn main(): int { return seven(); }\n"),
    ("two-helpers", "fn a(x: int): int { return x + 1; }\nfn b(x: int): int { return x * 2; }\nfn main(): int { return a(1) + b(2); }\n"),
    ("nested-call", "fn id(x: int): int { return x; }\nfn add(a: int, b: int): int { return a + b; }\nfn main(): int { return add(id(3), 4); }\n"),
    ("call-in-arith", "fn add(a: int, b: int): int { return a + b; }\nfn main(): int { let x: int = add(2, 3); return x * 4; }\n"),
    ("multi-call", "fn inc(x: int): int { return x + 1; }\nfn main(): int { return inc(1) + inc(10); }\n"),
    ("call-discarded", "fn f(x: int): int { return x; }\nfn main(): int { f(3); return 8; }\n"),
    ("helper-after", "fn main(): int { return 0; }\nfn f(): int { return 1; }\n"),
    ("deep-3", "fn h(x: int): int { return x * 2; }\nfn g(x: int): int { return h(x) + 1; }\nfn f(x: int): int { return g(x) * 3; }\nfn main(): int { return f(2); }\n"),
    ("param-plus-local", "fn f(a: int): int { let y: int = a * 2; return y + a; }\nfn main(): int { return f(5); }\n"),
    ("two-params-local", "fn f(a: int, b: int): int { let s: int = a + b; return s * s; }\nfn main(): int { return f(3, 4); }\n"),
    ("depth-8", "fn f1(x: int): int { return x + 1; }\nfn f2(x: int): int { return f1(x) + 1; }\nfn f3(x: int): int { return f2(x) + 1; }\nfn f4(x: int): int { return f3(x) + 1; }\nfn f5(x: int): int { return f4(x) + 1; }\nfn f6(x: int): int { return f5(x) + 1; }\nfn f7(x: int): int { return f6(x) + 1; }\nfn main(): int { return f7(0); }\n"),
]

REJECT25_CASES = [
    ("recur", "fn foo(x: int): int { return foo(x); }\nfn main(): int { return foo(1); }\n"),
    ("bool-param", "fn f(b: bool): int { return 1; }\nfn main(): int { return f(true); }\n"),
    ("seven-params", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int): int { return a; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6, 7); }\n"),
    ("bool-ret-helper", "fn f(): bool { return true; }\nfn main(): int { return 1; }\n"),
    ("if-helper", "fn f(x: int): int { if true { return x; } return 0; }\nfn main(): int { return f(1); }\n"),
    ("while-helper", "fn f(x: int): int { while true { break; } return x; }\nfn main(): int { return f(1); }\n"),
    ("str-helper", "fn f(): int { let s: str = \"ab\"; return 0; }\nfn main(): int { return f(); }\n"),
    ("main-with-param", "fn main(a: int): int { return a; }\n"),
]

REJECT_CHECK_CASES = [
    ("undeclared-call", "fn main(): int { return nosuch(); }\n", "SEM_UNKNOWN_FUNCTION"),
    ("arity", "fn id(x: int): int { return x; }\nfn main(): int { return id(1, 2); }\n", "SEM_ARITY_MISMATCH"),
    ("dup-helper-let", "fn f(x: int): int { let x: int = 1; return x; }\nfn main(): int { return f(1); }\n", "SEM_DUPLICATE"),
]

TRAP_CASES = [
    ("helper-empty", "fn h(): int {}\nfn main(): int { return h(); }\n"),
]

BOUND_CASES_128 = ("frames-128", "fn big(a: int, b: int, c: int, d: int, e: int, g: int): int { " + " ".join("let v%d: int = %d;" % (i, i) for i in range(122)) + " return v121; }\nfn main(): int { return big(1, 2, 3, 4, 5, 6); }\n")
BOUND_CASES_129 = ("frames-129", "fn big(a: int, b: int, c: int, d: int, e: int, g: int): int { " + " ".join("let v%d: int = %d;" % (i, i) for i in range(123)) + " return v122; }\nfn main(): int { return big(1, 2, 3, 4, 5, 6); }\n")


class BEBAcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 20)

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

    def test_05_arg_order_matters(self):
        by_name = {n: w for n, _h, _s, w in self.images}
        self.assertNotEqual(by_name["sub-order"], by_name["sub-rev"])
        self.assertNotEqual(by_name["param-reorder"], by_name["arg-order"])


class BEBRejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, REJECT_CHECK_CASES)
        cls.rtrap = _run_be(cls.combo, TRAP_CASES)
        cls.rb129 = _run_be(cls.combo, [BOUND_CASES_129])
        cls.rb128 = _run_be(cls.combo, [BOUND_CASES_128])
        cls.r17 = _run_be(cls.combo, [("seventeen", "".join("fn f%d(): int { return %d; }\n" % (i, i) for i in range(16)) + "fn main(): int { return 0; }\n")])

    def test_06_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_07_checker_passthrough(self):
        want_code = {"SEM_UNKNOWN_FUNCTION": 13, "SEM_ARITY_MISMATCH": 12, "SEM_DUPLICATE": 10}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src = case[0], case[1]
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertFalse(hr.ok)
            self.assertEqual(code, want_code[hr.diagnostic.code], name)
            self.assertEqual(hexstr, "", name)

    def test_08_helper_empty_traps(self):
        (code, _off, hexstr), (_name, _src) = self.rtrap[0], TRAP_CASES[0]
        self.assertEqual(code, 0)
        raw = bytes.fromhex(hexstr)
        code_bytes, _sz = _parse_rnyx(raw)
        with self.assertRaises(RuntimeError):
            run_image(code_bytes)

    def test_09_frame_bounds(self):
        code, _off, hexstr = self.rb129[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")
        code, _off, hexstr = self.rb128[0]
        self.assertEqual(code, 0)
        raw = bytes.fromhex(hexstr)
        code_bytes, _sz = _parse_rnyx(raw)
        self.assertEqual(run_image(code_bytes), 121)

    def test_10_fn_count_bound(self):
        code, _off, hexstr = self.r17[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


class BEBMutantTests(unittest.TestCase):
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

    def test_bb_m1_home_disp(self):
        combo = _mutated_combo(
            "fn be_home_disp(slot: int): int {\n  return slot * 8;\n}",
            "fn be_home_disp(slot: int): int {\n  return (slot + 1) * 8;\n}")
        self.assertTrue(self._red_on(combo, 1))

    def test_bb_m2_swap_args(self):
        combo = _mutated_combo(
            "  if i == 0 { return e_spill_4(72, 125, d, acc); } else { }",
            "  if i == 0 { return e_spill_4(72, 117, d, acc); } else { }")
        self.assertTrue(self._red_on(combo, 10))

    def test_bb_m3_ignore_disp(self):
        combo = _mutated_combo(
            "  let dp: int = 28 + co - (q0 + sz_call_op());",
            "  let dp: int = 0;")
        self.assertTrue(self._red_on(combo, 19))

    def test_bb_m4_disp_off_by_one(self):
        combo = _mutated_combo(
            "  let dp: int = 28 + co - (q0 + sz_call_op());",
            "  let dp: int = 29 + co - (q0 + sz_call_op());")
        self.assertTrue(self._red_on(combo, 8))

    def test_bb_m5_spill_omitted(self):
        combo = _mutated_combo(
            "  let sp: int = be_e_spills(fr, np, 0);",
            "  let sp: int = fr;")
        self.assertTrue(self._red_on(combo, 8))

    def test_bb_m6_frame_small(self):
        combo = _mutated_combo(
            "  return ((nlets * 8 + 15) / 16) * 16;",
            "  return (nlets - 1) * 8;")
        self.assertTrue(self._red_on(combo, 2))

    def test_bb_m7_callers_push_lost(self):
        combo = _mutated_combo(
            "  let r: BZ = be_e_level(src, f, fs, fe, 0, pos, end, acc);\n  if r->c == 0 { } else { return r; }\n  let a0: int = e_push_rax(r->n);",
            "  let r: BZ = be_e_level(src, f, fs, fe, 0, pos, end, acc);\n  if r->c == 0 { } else { return r; }\n  let a0: int = r->n;")
        self.assertTrue(self._red_on(combo, 20))

    def test_bb_m8_first_helper(self):
        combo = _mutated_combo(
            "  let co: int = be_fn_codeoff(src, f, ci);",
            "  let co: int = be_fn_codeoff(src, f, 0);")
        self.assertTrue(self._red_on(combo, 19))

    def test_bb_m9_size_no_prologue(self):
        combo = _mutated_combo(
            "  let b: BZ = be_s_block(src, f, cs, ce, bo + 1, be, sz_frame(nl) + 4 * np);",
            "  let b: BZ = be_s_block(src, f, cs, ce, bo + 1, be, 4 * np);")
        self.assertTrue(self._red_on(combo, 8))


if __name__ == "__main__":
    unittest.main()
