"""Stage 19e BE-F1 bounded list storage: literals, len, copies, params,
returns (sret), and nested calls.

Layout (frozen 19a): len@0, elems@1+i*ew, padding-free 8-byte slots;
value copies everywhere; aggregate returns use caller sret at stack
slot 0; frames reserve one unit-max-width temp area for call staging.
Index/push/unwrap/is_ok/== stay backend-25 (deferred to F2); elem
values are therefore projected through len here, with value checking
deferred to F2 index proof.

QEMU/native proof deferred to F2; here execution is proven by the
test-only emulator (BE-E forms only: no new x86 forms in F1).
Reference semantics come from the trusted host oracle.
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit_data as bed  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_record as ber  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


Emu = ber.Emu


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
    return ber.run_image_data(raw)


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


def _oracle_exit(src):
    return bea._oracle_exit(src)


ACCEPT_CASES = [
    ("list-empty", "fn main(): int { let l: list<int,2> = []; return len(l); }\n"),
    ("list-one", "fn main(): int { let l: list<int,2> = [5]; return len(l); }\n"),
    ("list-full-2", "fn main(): int { let l: list<int,2> = [1, 2]; return len(l); }\n"),
    ("list-full-3", "fn main(): int { let l: list<int,3> = [1, 2, 3]; return len(l); }\n"),
    ("list-bool", "fn main(): int { let l: list<bool,2> = [true, false]; return len(l); }\n"),
    ("list-bool-3", "fn main(): int { let l: list<bool,3> = [true, false, true]; return len(l); }\n"),
    ("list-expr", "fn main(): int { let l: list<int,3> = [1 + 2, 3 * 4, 10 - 3]; return len(l); }\n"),
    ("list-copy", "fn main(): int { let l: list<int,2> = [1, 2]; let m: list<int,2> = l; return len(m); }\n"),
    ("list-copy-both", "fn main(): int { let l: list<int,2> = [1, 2]; let m: list<int,2> = l; return len(l) + len(m); }\n"),
    ("list-two", "fn main(): int { let a: list<int,2> = [1, 2]; let b: list<int,2> = [3, 4]; return len(a) + len(b); }\n"),
    ("list-nested-outer", "fn main(): int { let l: list<list<int,2>,2> = [[1, 2]]; return len(l); }\n"),
    ("list-nested-full", "fn main(): int { let l: list<list<int,2>,2> = [[1, 2], [3, 4]]; return len(l); }\n"),
    ("list-rec-elem", "record Pair { a: int, b: int }\nfn main(): int { let l: list<Pair,2> = [Pair(a: 1, b: 2)]; return len(l); }\n"),
    ("list-param", "fn getlen(l: list<int,2>): int { return len(l); }\nfn main(): int { let l: list<int,2> = [1, 2]; return getlen(l); }\n"),
    ("list-param-mix", "fn f(x: int, l: list<int,2>, y: int): int { return x + len(l) + y; }\nfn main(): int { let l: list<int,2> = [1, 2]; return f(1, l, 4); }\n"),
    ("list-ret-lit", "fn mk(): list<int,2> { return [3, 4]; }\nfn main(): int { let l: list<int,2> = mk(); return len(l); }\n"),
    ("list-ret-var", "fn idl(l: list<int,2>): list<int,2> { return l; }\nfn main(): int { let a: list<int,2> = [1, 2]; let b: list<int,2> = idl(a); return len(b); }\n"),
    ("list-nestcall", "fn llen(l: list<int,2>): int { return len(l); }\nfn mk(): list<int,2> { return [1, 2]; }\nfn main(): int { return llen(mk()); }\n"),
    ("list-if-len", "fn main(): int { let l: list<int,2> = [1, 2]; if len(l) == 2 { return 17; } else { return 93; } }\n"),
    ("list-while-len", "fn main(): int { let l: list<int,2> = [1, 2]; while len(l) == 0 { return 1; } return 2; }\n"),
    ("list-temp-clobber", "fn mk(): list<int,2> { return [20, 22]; }\nfn llen(l: list<int,2>): int { return len(l); }\nfn main(): int { let z: int = 5; let s: int = llen(mk()); return s + z; }\n"),
    ("list-bound-ok", "fn main(): int { " + " ".join("let v%d: list<int,2> = [%d, %d];" % (i, i, i) for i in range(20)) + " return len(v19); }\n"),
]

REJECT25_CASES = [
    ("list-index", "fn main(): int { let l: list<int,2> = [1, 2]; let s: status<int> = l[0]; return 0; }\n"),
    ("list-push", "fn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = push(l, 2); return 0; }\n"),
    ("list-unwrap", "fn main(): int { let s: status<int> = byte_at(\"ab\", 9); return unwrap_or(s, 0); }\n"),
    ("list-is-ok", "fn main(): int { let s: status<int> = byte_at(\"ab\", 9); if is_ok(s) { return 1; } else { return 0; } }\n"),
    ("list-eq", "fn main(): int { let a: list<int,2> = [1, 2]; let b: list<int,2> = [1, 2]; if a == b { return 1; } else { return 0; } }\n"),
    ("list-field", "record L { l: list<int,2> }\nfn main(): int { let r: L = L(l: [1, 2]); return 0; }\n"),
    ("list-str-elem", "fn main(): int { let l: list<str,2> = [\"a\", \"b\"]; return 0; }\n"),
]

REJECT_CHECK_CASES = [
    ("over-cap", "fn main(): int { let l: list<int,2> = [1, 2, 3]; return 0; }\n", "SEM_LIMIT_EXCEEDED"),
    ("elem-mismatch", "fn main(): int { let l: list<int,2> = [1, true]; return 0; }\n", "SEM_TYPE_MISMATCH"),
    ("undeclared-rec", "fn main(): int { let l: list<Q,2> = [Q(x: 1)]; return 0; }\n", "SEM_UNDECLARED"),
    ("print-list", "fn main(): int { let l: list<int,2> = [1, 2]; print(l); return 0; }\n", "SEM_PROFILE_EXCLUDED"),
]

BOUND_CASES_BIG = ("frames-list-130", "fn main(): int { " + " ".join("let v%d: list<int,2> = [%d, %d];" % (i, i, i) for i in range(45)) + " return len(v44); }\n")
BOUND_CASES_WIDE = ("frames-list1-130", "fn main(): int { " + " ".join("let w%d: list<int,1> = [%d];" % (i, i) for i in range(70)) + " return len(w69); }\n")


class BEListAcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 20)

    def test_02_determinism(self):
        again = _run_be(self.combo, ACCEPT_CASES)
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w, _e in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        outs = [(w, e) for _n, _h, w, e in self.images]
        self.assertGreater(len(set(outs)), 3)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_len_values(self):
        by_name = {n: e for n, _h, _w, e in self.images}
        self.assertEqual(by_name["list-empty"], 0)
        self.assertEqual(by_name["list-one"], 1)
        self.assertEqual(by_name["list-full-2"], 2)
        self.assertEqual(by_name["list-copy-both"], 4)


class BEListRejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, REJECT_CHECK_CASES)
        cls.rb1 = _run_be(cls.combo, [BOUND_CASES_BIG])
        cls.rb2 = _run_be(cls.combo, [BOUND_CASES_WIDE])

    def test_06_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_07_checker_passthrough(self):
        want_code = {"SEM_UNDECLARED": 9, "SEM_ARITY_MISMATCH": 12, "SEM_DUPLICATE": 10,
                     "SEM_TYPE_MISMATCH": 11, "SEM_PROFILE_EXCLUDED": 15, "SEM_LIMIT_EXCEEDED": 14}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src = case[0], case[1]
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertFalse(hr.ok)
            self.assertEqual(code, want_code[hr.diagnostic.code], name)
            self.assertEqual(hexstr, "", name)

    def test_08_resource_bounds(self):
        code, _off, hexstr = self.rb1[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")
        code, _off, hexstr = self.rb2[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


class BEListMutantTests(unittest.TestCase):
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

    def _idx(self, name):
        for i, (n, _s) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_be_m1_len_plus1(self):
        combo = _mut(_combo_text(),
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let cp: Tok = pgm_tok(src, nx->s, end);\n  if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { return BZ(p: cp->p, n: sz_mov_rax_home(base), c: 0, o: 0); } else { } } else { } } else { }",
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let cp: Tok = pgm_tok(src, nx->s, end);\n  if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { return BZ(p: cp->p, n: sz_mov_rax_home(base + 1), c: 0, o: 0); } else { } } else { } } else { }")
        combo = _mut(combo,
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let cp: Tok = pgm_tok(src, nx->s, end);\n  if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { return BZ(p: cp->p, n: e_mov_rax_home(base, acc), c: 0, o: 0); } else { } } else { } } else { }",
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let cp: Tok = pgm_tok(src, nx->s, end);\n  if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { return BZ(p: cp->p, n: e_mov_rax_home(base + 1, acc), c: 0, o: 0); } else { } } else { } } else { }")
        self.assertTrue(self._red_on(combo, self._idx("list-full-2")))

    def test_be_m2_len_always_zero(self):
        combo = _mut(_combo_text(),
                     "  let a0: int = e_mov_rax_imm(count, acc);",
                     "  let a0: int = e_mov_rax_imm(0, acc);")
        self.assertTrue(self._red_on(combo, self._idx("list-full-2")))

    def test_be_m3_copy_src_plus1(self):
        combo = _mut(_combo_text(),
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_size(base, dk, ds, w), c: 0, o: 0);\n}\nfn be_is_push",
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_size(base + 1, dk, ds, w), c: 0, o: 0);\n}\nfn be_is_push")
        combo = _mut(combo,
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_emit(base, dk, ds, w, acc), c: 0, o: 0);\n}\nfn be_e_aggex_call",
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_emit(base + 1, dk, ds, w, acc), c: 0, o: 0);\n}\nfn be_e_aggex_call")
        self.assertTrue(self._red_on(combo, self._idx("list-copy")))

    def test_be_m4_frame_bytes_omitted(self):
        combo = _mut(_combo_text(),
                     "  let fr: int = nl + be_unit_maxrec(src, f);",
                     "  let fr: int = nl - be_unit_maxrec(src, f);")
        combo = _mut(combo,
                     "  let fr: int = e_frame(nl + be_unit_maxrec(src, f), acc);",
                     "  let fr: int = e_frame(nl - be_unit_maxrec(src, f), acc);")
        self.assertTrue(self._red_on(combo, self._idx("list-temp-clobber")))

    def test_be_m5_param_wrong_offset(self):
        combo = _mutated_combo(
            "  if v->k == 1 { return be_param_base(src, f, fs, fe, v->slot) + 1; } else { }",
            "  if v->k == 1 { return be_param_base(src, f, fs, fe, v->slot) + 2; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("list-param")))

    def test_be_m6_arg_copy_removed(self):
        combo = _mut(_combo_text(),
                     "  let e: BZ = be_s_aggex(src, f, fs, fe, 0, tb, pt->t, pos, end);",
                     "  let e: BZ = be_s_aggex(src, f, fs, fe, 0, tb + w, pos, end);")
        combo = _mut(combo,
                     "  let e: BZ = be_e_aggex(src, f, fs, fe, 0, tb, pt->t, pos, end, acc);",
                     "  let e: BZ = be_e_aggex(src, f, fs, fe, 0, tb + w, pt->t, pos, end, acc);")
        self.assertTrue(self._red_on(combo, self._idx("list-param-mix")))

    def test_be_m7_wrong_ret_dst(self):
        combo = _mut(_combo_text(),
                     "  let e: BZ = be_s_aggex(src, f, fs, fe, 1, 0, rt, nx->s, end);",
                     "  let e: BZ = be_s_aggex(src, f, fs, fe, 0, 0, rt, nx->s, end);")
        combo = _mut(combo,
                     "  let e: BZ = be_e_aggex(src, f, fs, fe, 1, 0, rt, nx->s, end, acc);",
                     "  let e: BZ = be_e_aggex(src, f, fs, fe, 0, 0, rt, nx->s, end, acc);")
        self.assertTrue(self._red_on(combo, self._idx("list-ret-lit")))


if __name__ == "__main__":
    unittest.main()
