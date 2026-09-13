"""Stage 19e G1 text parameters: str values crossing call boundaries.

Representation (frozen, discovered from repo truth, NOT invented here):
str is a (ptr,len) home pair: word0 = absolute RYNX data VA
(DATA_BASE 0x600000 + literal offset), word1 = decoded byte length.
No NUL terminator; bytes immutable in the data section; literals are
appended in source order with NO dedup; copies are shallow 2-word
copies (sound: bytes immutable). Empty string contributes zero bytes.
Calls pass consecutive home slots (ptr,len) via RDI,RSI,RDX,RCX,R8,R9
(slot j -> reg j); the callee spills regs to homes identically.
Str returns stay backend-25 (scalar return path carries one word).

G1 implements: str params (gate), str lets (home materialize/copy),
str call args (direct 2-push, no temp area needed), data collection
for value-position literals in all three data walks. Deferred: len(str),
byte_at, ==, print(str-var/call), match, list<str>, str fields/returns.

Execution observability note: no current backend op reads str bytes,
so content is proven structurally (data bytes, code immediates, home
layout) plus int-side execution (scalars sharing frames/calls stay
exact). M10-style execution observability belongs to byte-access.
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_list as bel  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


Emu = bel.Emu


def _oracle_run(src):
    result = analyzer.analyze(src, "t.rl", profile="core")
    assert result.ok, result.diagnostic
    module, error = rir.build_rir(result.ast, "t.rl")
    assert error is None, error
    emitted = []
    outcome = oracle.run_rir(module, out=emitted)
    assert outcome["trapped"] is None, outcome
    assert outcome["exit"] is not None
    writes = []
    for e in emitted:
        writes.append(e if isinstance(e, bytes) else str(e).encode("utf-8"))
    return outcome["exit"] & 0xFFFFFFFF, b"".join(writes)


def run_image_data(raw: bytes):
    return bel.run_image_data(raw)


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


def _parse_rnyx_data(raw: bytes):
    ver, arch, hlen, res, entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
    assert (ver, arch, hlen, res, entry) == (2, 1, 28, 0, 0)
    assert fsz == msz and 1 <= csz <= 65536 and 0 <= fsz <= 32768
    assert len(raw) == 28 + csz + fsz
    return raw[28:28 + csz], raw[28 + csz:28 + csz + fsz]


ACCEPT_CASES = [
    # G1-A: one text parameter (exits const; artifacts distinguish)
    ("str-ret7", "fn f(s: str): int { return 7; }\nfn main(): int { return f(\"abc\"); }\n"),
    ("str-empty", "fn f(s: str): int { return 7; }\nfn main(): int { return f(\"\"); }\n"),
    ("str-one", "fn f(s: str): int { return 7; }\nfn main(): int { return f(\"x\"); }\n"),
    ("str-long", "fn f(s: str): int { return 7; }\nfn main(): int { return f(\"hello-world-0123456789\"); }\n"),
    ("str-ab", "fn f(s: str): int { return 7; }\nfn main(): int { return f(\"ab\"); }\n"),
    ("str-cd", "fn f(s: str): int { return 7; }\nfn main(): int { return f(\"cd\"); }\n"),
    ("str-dupcall", "fn f(s: str): int { return 7; }\nfn main(): int { f(\"ab\"); return f(\"ab\"); }\n"),
    # G1-B: multiple text parameters
    ("str-two", "fn f(a: str, b: str): int { return 5; }\nfn main(): int { return f(\"x\", \"yy\"); }\n"),
    ("str-three", "fn f(a: str, b: str, c: str): int { return 6; }\nfn main(): int { return f(\"x\", \"yy\", \"zzz\"); }\n"),
    ("str-sixslots", "fn f(a: str, b: str, c: str): int { return 8; }\nfn main(): int { return f(\"a\", \"bb\", \"ccc\"); }\n"),
    ("str-repeated", "fn f(a: str, b: str): int { return 4; }\nfn main(): int { let t: str = \"qq\"; return f(t, t); }\n"),
    # G1-C: mixed scalar/text permutations (distinct int sentinels)
    ("mix-str-int", "fn f(s: str, x: int): int { return x; }\nfn main(): int { return f(\"hello-world\", 41); }\n"),
    ("mix-int-str", "fn f(x: int, s: str): int { return x; }\nfn main(): int { return f(42, \"hello\"); }\n"),
    ("mix-int-str-int", "fn f(x: int, s: str, y: int): int { return x + y; }\nfn main(): int { return f(20, \"mid\", 22); }\n"),
    ("mix-str-str-int", "fn f(a: str, b: str, y: int): int { return y; }\nfn main(): int { return f(\"a\", \"bb\", 43); }\n"),
    ("mix-int-str-str", "fn f(x: int, a: str, b: str): int { return x; }\nfn main(): int { return f(44, \"a\", \"bb\"); }\n"),
    ("mix-expr", "fn f(s: str, x: int): int { return x; }\nfn main(): int { return f(\"a\", 2 * 21); }\n"),
    # G1-D: forwarding
    ("fwd", "fn b(s: str, x: int): int { return x; }\nfn a(s: str, x: int): int { return b(s, x); }\nfn main(): int { return a(\"hello\", 9); }\n"),
    ("fwd-swap", "fn b(x: str, y: str): int { return 3; }\nfn a(p: str, q: str): int { return b(q, p); }\nfn main(): int { return a(\"aa\", \"bb\"); }\n"),
    ("fwd-chain", "fn c(s: str, x: int): int { return x; }\nfn b(s: str, x: int): int { return c(s, x); }\nfn a(s: str, x: int): int { return b(s, x); }\nfn main(): int { return a(\"deep\", 15); }\n"),
    ("fwd-reorder", "fn c(x: str, y: str): int { return 16; }\nfn b(p: str, q: str): int { return c(q, p); }\nfn a(p: str, q: str): int { return b(q, p); }\nfn main(): int { return a(\"m\", \"n\"); }\n"),
    # G1-E: nested calls beside text args (temp/stack disjointness)
    ("nest-after", "fn add(a: int, b: int): int { return a + b; }\nfn f(s: str, x: int): int { return x; }\nfn main(): int { return f(\"ab\", add(20, 22)); }\n"),
    ("nest-before", "fn add(a: int, b: int): int { return a + b; }\nfn f(x: int, s: str): int { return x; }\nfn main(): int { return f(add(20, 22), \"ab\"); }\n"),
    ("nest-deep", "fn h(x: int): int { return x + 1; }\nfn g(x: int): int { return h(x) + 1; }\nfn f(s: str, x: int): int { return x; }\nfn main(): int { return f(\"xy\", g(1)); }\n"),
    # lets, branches, parens
    ("str-let", "fn f(s: str): int { return 7; }\nfn main(): int { let t: str = \"hi\"; return f(t); }\n"),
    ("str-let-copy", "fn f(s: str, x: int): int { return x; }\nfn main(): int { let t: str = \"hi\"; let u: str = t; return f(u, 13); }\n"),
    ("str-branch", "fn f(s: str, x: int): int { return x; }\nfn main(): int { let t: str = \"ab\"; if 1 == 1 { return f(t, 2); } return 0; }\n"),
    ("str-loop", "fn f(s: str): int { return 7; }\nfn main(): int { while true { f(\"x\"); break; } return 5; }\n"),
    ("str-paren", "fn f(s: str): int { return 7; }\nfn main(): int { return f((\"zz\")); }\n"),
    ("str-in-branch", "fn f(s: str): int { return 7; }\nfn main(): int { if 1 == 1 { return f(\"inbr\"); } return 0; }\n"),
]


def _want_exit(src):
    return _oracle_run(src)[0]


class G1AcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, ACCEPT_CASES)
        cls.images = []
        for (code, off, hexstr), (name, src) in zip(cls.results, ACCEPT_CASES):
            assert code == 0, (name, code, off)
            raw = bytes.fromhex(hexstr)
            want_exit, want_out = _oracle_run(src)
            got_exit, got_out = run_image_data(raw)
            assert got_exit == want_exit, (name, got_exit, want_exit)
            assert got_out == want_out, (name, got_out, want_out)
            cls.images.append((name, hexstr, want_out, want_exit))

    def test_01_exit_behavior(self):
        self.assertGreaterEqual(len(self.images), 25)

    def test_02_determinism(self):
        again = _run_be(self.combo, ACCEPT_CASES)
        self.assertEqual([(c, o, h) for c, o, h in again], [(c, o, h) for c, o, h in self.results])

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w, _e in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        outs = [(w, e) for _n, _h, w, e in self.images]
        self.assertGreater(len(set(outs)), 5)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_int_side_exact(self):
        by_name = {n: e for n, _h, _w, e in self.images}
        self.assertEqual(by_name["mix-str-int"], 41)
        self.assertEqual(by_name["mix-int-str"], 42)
        self.assertEqual(by_name["mix-int-str-int"], 42)
        self.assertEqual(by_name["fwd"], 9)
        self.assertEqual(by_name["nest-after"], 42)
        self.assertEqual(by_name["str-let-copy"], 13)

    def test_06_data_bytes(self):
        by_hex = {n: h for n, h, _w, _e in self.images}
        _, data = _parse_rnyx_data(bytes.fromhex(by_hex["str-ret7"]))
        self.assertEqual(data, b"abc")
        _, data = _parse_rnyx_data(bytes.fromhex(by_hex["str-empty"]))
        self.assertEqual(data, b"")
        _, data = _parse_rnyx_data(bytes.fromhex(by_hex["str-dupcall"]))
        self.assertEqual(data, b"abab")
        _, data = _parse_rnyx_data(bytes.fromhex(by_hex["str-ab"]))
        _, data2 = _parse_rnyx_data(bytes.fromhex(by_hex["str-cd"]))
        self.assertEqual(data, b"ab")
        self.assertEqual(data2, b"cd")
        self.assertNotEqual(by_hex["str-ab"], by_hex["str-cd"])


REJECT25_CASES = [
    ("str-eq", "fn f(s: str): int { return 7; }\nfn main(): int { let a: str = \"x\"; let b: str = \"x\"; if a == b { return 1; } else { return 0; } }\n"),
    ("str-byteat", "fn main(): int { let s: status<int> = byte_at(\"ab\", 0); return unwrap_or(s, 0); }\n"),
    ("str-len", "fn main(): int { let s: str = \"ab\"; return len(s); }\n"),
    ("str-ret", "fn h(): str { return \"x\"; }\nfn main(): int { return 0; }\n"),
    ("print-strvar", "fn main(): int { let s: str = \"ab\"; print(s); return 0; }\n"),
    ("print-strcall", "fn h(): str { return \"x\"; }\nfn main(): int { print(h()); return 0; }\n"),
    ("str-bare", "fn main(): int { let s: str = \"ab\"; s; return 0; }\n"),
    ("str-ret-helper", "fn g(x: int): str { return \"q\"; }\nfn main(): int { return 0; }\n"),
]

REJECT_CHECK_CASES = [
    ("arity", "fn f(s: str): int { return 0; }\nfn main(): int { return f(\"a\", \"b\"); }\n", "SEM_ARITY_MISMATCH"),
    ("int-for-str", "fn f(s: str): int { return 0; }\nfn main(): int { return f(1); }\n", "SEM_TYPE_MISMATCH"),
    ("str-for-int", "fn f(x: int): int { return 0; }\nfn main(): int { return f(\"a\"); }\n", "SEM_TYPE_MISMATCH"),
    ("print-list", "fn main(): int { let l: list<int,2> = [1, 2]; print(l); return 0; }\n", "SEM_PROFILE_EXCLUDED"),
]

BOUND_FRAMES = ("frames-str-130", "fn main(): int { " + " ".join("let v%d: str = \"x%d\";" % (i, i) for i in range(65)) + " return 0; }\n")


class G1RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, REJECT_CHECK_CASES)
        cls.rb = _run_be(cls.combo, [BOUND_FRAMES])

    def test_07_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_08_checker_passthrough(self):
        want_code = {"SEM_UNDECLARED": 9, "SEM_ARITY_MISMATCH": 12, "SEM_DUPLICATE": 10,
                     "SEM_TYPE_MISMATCH": 11, "SEM_PROFILE_EXCLUDED": 15, "SEM_LIMIT_EXCEEDED": 14}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src = case[0], case[1]
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertFalse(hr.ok)
            self.assertEqual(code, want_code[hr.diagnostic.code], name)
            self.assertEqual(hexstr, "", name)

    def test_09_resource_bounds(self):
        code, _off, hexstr = self.rb[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


class G1MutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()

    def _red_on(self, mutant_combo, idx):
        name, src = ACCEPT_CASES[idx]
        want_exit, want_out = _oracle_run(src)
        try:
            (code, _off, hexstr) = _run_be(mutant_combo, [(name, src)])[0]
        except (AssertionError, RuntimeError):
            return True
        if code != 0:
            return True
        raw = bytes.fromhex(hexstr)
        try:
            got_exit, got_out = run_image_data(raw)
        except (RuntimeError, AssertionError):
            return True
        return got_exit != want_exit or got_out != want_out

    def _red_on_hex(self, mutant_combo, idx):
        name, src = ACCEPT_CASES[idx]
        want_exit, want_out = _oracle_run(src)
        (_, _, want_hex) = _run_be(self.combo, [(name, src)])[0]
        try:
            (code, _off, hexstr) = _run_be(mutant_combo, [(name, src)])[0]
        except (AssertionError, RuntimeError):
            return True
        if code != 0:
            return True
        if hexstr != want_hex:
            return True
        raw = bytes.fromhex(hexstr)
        try:
            got_exit, got_out = run_image_data(raw)
        except (RuntimeError, AssertionError):
            return True
        return got_exit != want_exit or got_out != want_out

    def _idx(self, name):
        for i, (n, _s) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_g1_m1_size_drop_len_push(self):
        combo = _mut(_combo_text(),
                     "return BZ(p: t->p, n: sz_mov_rax_home(base) + sz_push_rax() + sz_mov_rax_home(base + 1) + sz_push_rax(), c: 0, o: 0);",
                     "return BZ(p: t->p, n: sz_mov_rax_home(base) + sz_push_rax(), c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("str-let")))

    def test_g1_m2_swap_store_order(self):
        combo = _mut(_combo_text(),
                     "  let a1: int = be_store_emit(dk, ds, 0, a0);\n  let a2: int = e_mov_rax_imm(len, a1);\n  let a3: int = be_store_emit(dk, ds, 1, a2);\n  return BZ(p: t->p, n: a3, c: 0, o: 0);\n}\nfn be_e_strvar",
                     "  let a1: int = be_store_emit(dk, ds, 1, a0);\n  let a2: int = e_mov_rax_imm(len, a1);\n  let a3: int = be_store_emit(dk, ds, 0, a2);\n  return BZ(p: t->p, n: a3, c: 0, o: 0);\n}\nfn be_e_strvar")
        self.assertTrue(self._red_on_hex(combo, self._idx("str-let")))

    def test_g1_m3_first_literal(self):
        combo = _mut(_combo_text(),
                     "fn be_e_strval_lit(src: str, f: int, t: Tok, acc: int): BZ {\n  let off: int = be_data_off(src, f, t->s);",
                     "fn be_e_strval_lit(src: str, f: int, t: Tok, acc: int): BZ {\n  let off: int = 0;")
        self.assertTrue(self._red_on_hex(combo, self._idx("str-dupcall")))

    def test_g1_m4_size_drop_lit_push(self):
        combo = _mut(_combo_text(),
                     "return BZ(p: t->p, n: sz_mov_rax_imm(0) + sz_push_rax() + sz_mov_rax_imm(0) + sz_push_rax(), c: 0, o: 0);",
                     "return BZ(p: t->p, n: sz_mov_rax_imm(0) + sz_push_rax(), c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("str-three")))

    def test_g1_m5_data_addr_plus1(self):
        combo = _mut(_combo_text(),
                     "  let a0: int = e_mov_rax_imm(be_data_base() + off, acc);",
                     "  let a0: int = e_mov_rax_imm(be_data_base() + off + 1, acc);",
                     count=2)
        self.assertTrue(self._red_on_hex(combo, self._idx("str-ret7")))

    def test_g1_m6_slot_check_removed(self):
        combo = _mut(_combo_text(),
                     "if be_param_slots(src, f, hs, he) <= 12 { } else { return derr(25, f, hs); }",
                     "if be_param_slots(src, f, hs, he) <= 12 { } else { }")
        name, src = ("str-8slots", "fn f(a: str, b: str, c: str, d: str): int { return 0; }\nfn main(): int { return 0; }\n")
        (code, _off, _hexstr) = _run_be(combo, [(name, src)])[0]
        self.assertEqual(code, 0)

    def test_g1_m7_param_base_plus2(self):
        combo = _mutated_combo(
            "  if v->k == 1 { return be_param_base(src, f, fs, fe, v->slot) + 1; } else { }",
            "  if v->k == 1 { return be_param_base(src, f, fs, fe, v->slot) + 2; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("fwd")))

    def test_g1_m8_empty_len_one(self):
        combo = _mut(_combo_text(),
                     "fn be_str_len(src: str, pos: int, end: int): int {\n  if pos >= end { return 0; } else { }",
                     "fn be_str_len(src: str, pos: int, end: int): int {\n  if pos >= end { return 1; } else { }")
        self.assertTrue(self._red_on_hex(combo, self._idx("str-empty")))

    def test_g1_m9_emit_drop_store(self):
        combo = _mut(_combo_text(),
                     "  let a3: int = be_store_emit(dk, ds, 1, a2);\n  return BZ(p: t->p, n: a3, c: 0, o: 0);\n}\nfn be_e_strvar",
                     "  let a3: int = a2;\n  return BZ(p: t->p, n: a3, c: 0, o: 0);\n}\nfn be_e_strvar")
        self.assertTrue(self._red_on(combo, self._idx("str-let")))


if __name__ == "__main__":
    unittest.main()
