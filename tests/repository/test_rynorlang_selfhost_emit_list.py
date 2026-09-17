"""Stage 19e BE-F2 bounded list index/push: dynamic reads, growing writes,
status observation (is_ok/is_err/unwrap_or), status returns, and calls.

Layout (frozen 19a): len@0, elems@1+i*ew; status tag@0/code@1/payload@2;
value copies; sret for aggregate/status returns; one temp area.
New x86 (all caller-saved, no string ops): test rax,rax(64), js, imul
rax,imm32, lea rsi,home, sub rsi,rax, sub rsi,ib, mov rax,[rsi],
mov [rsi],rax, mov ecx,imm32, add rax,ib. Emulator extended below.
`==` on lists, call-temp index, and non-scalar-elem index/push stay 25.
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


MASK64 = bea.MASK64
CODE_BASE = bea.CODE_BASE


class Emu(ber.Emu):
    def step(self):
        b0 = self._fetch(1)[0]
        R = self.reg
        if b0 == 0xB9:
            R["rcx"] = int.from_bytes(self._fetch(4), "little") & 0xFFFFFFFF
            return
        if b0 == 0x0F:
            b1 = self._fetch(1)[0]
            if b1 == 0x88:
                disp = int.from_bytes(self._fetch(4), "little", signed=True)
                if self.sf:
                    self.rip = (self.rip + disp) & MASK64
                return
            if b1 == 0x83:
                disp = int.from_bytes(self._fetch(4), "little", signed=True)
                if self.cf == 0:
                    self.rip = (self.rip + disp) & MASK64
                return
            self.rip -= 2
            super().step()
            return
        self.rip -= 1
        super().step()

    def _rex(self):
        R = self.reg
        off = self.rip - CODE_BASE
        b1 = self.code[off]
        if b1 == 0x85 and self.code[off + 1] == 0xC0:
            self._fetch(2)
            self._flags_logic(R["rax"])
            return
        if b1 == 0x83 and self.code[off + 1] == 0xC0:
            self._fetch(2)
            imm = self._fetch(1)[0]
            imm = imm - 256 if imm >= 128 else imm
            R["rax"] = self._flags_add(R["rax"], imm & MASK64)
            return
        if b1 == 0x8B and self.code[off + 1] == 0x8D:
            self._fetch(2)
            d = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rcx"] = self._sread((R["rbp"] + d) & MASK64, 8)
            return
        if b1 == 0x69 and self.code[off + 1] == 0xC0:
            self._fetch(2)
            imm = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rax"] = (R["rax"] * (imm & MASK64)) & MASK64
            self.cf = self.of = 0
            return
        if b1 == 0x8D and self.code[off + 1] == 0xB5:
            self._fetch(2)
            d = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rsi"] = (R["rbp"] + d) & MASK64
            return
        if b1 == 0x29 and self.code[off + 1] == 0xC6:
            self._fetch(2)
            R["rsi"] = self._flags_sub(R["rsi"], R["rax"])
            return
        if b1 == 0x83 and self.code[off + 1] == 0xEE:
            self._fetch(2)
            imm = self._fetch(1)[0]
            R["rsi"] = self._flags_sub(R["rsi"], imm & MASK64)
            return
        if b1 == 0x8B and self.code[off + 1] == 0x06:
            self._fetch(2)
            R["rax"] = self._sread(R["rsi"] & MASK64, 8)
            return
        if b1 == 0x89 and self.code[off + 1] == 0x06:
            self._fetch(2)
            self._swrite(R["rsi"] & MASK64, 8, R["rax"])
            return
        super()._rex()


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
    ver, arch, hlen, res, entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
    assert (ver, arch, hlen, res, entry) == (2, 1, 28, 0, 0)
    assert fsz == msz, (fsz, msz)
    assert 1 <= csz <= 65536 and 0 <= fsz <= 32768, (csz, fsz)
    code, data = raw[28:28 + csz], raw[28 + csz:28 + csz + fsz]
    assert len(code) == csz and len(data) == fsz and len(raw) == 28 + csz + fsz
    em = Emu(code, data)
    return em.run(), bytes(em.stdout)


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
    ("list-copy", "fn main(): int { let l: list<int,2> = [1, 2]; let m: list<int,2> = l; return len(m); }\n"),
    ("list-param", "fn getlen(l: list<int,2>): int { return len(l); }\nfn main(): int { let l: list<int,2> = [1, 2]; return getlen(l); }\n"),
    ("list-ret-lit", "fn mk(): list<int,2> { return [3, 4]; }\nfn main(): int { let l: list<int,2> = mk(); return len(l); }\n"),
    ("idx-0", "fn main(): int { let l: list<int,2> = [10, 20]; let s: status<int> = l[0]; return unwrap_or(s, 0); }\n"),
    ("idx-1", "fn main(): int { let l: list<int,2> = [10, 20]; let s: status<int> = l[1]; return unwrap_or(s, 0); }\n"),
    ("idx-dyn", "fn main(): int { let l: list<int,3> = [7, 8, 9]; let i: int = 1 + 1; let s: status<int> = l[i]; return unwrap_or(s, 0); }\n"),
    ("idx-bool", "fn main(): int { let l: list<bool,2> = [true, false]; let s: status<bool> = l[1]; if unwrap_or(s, true) { return 1; } else { return 0; } }\n"),
    ("idx-oob", "fn main(): int { let l: list<int,2> = [10, 20]; let s: status<int> = l[9]; if is_err(s) { return 1; } else { return 0; } }\n"),
    ("idx-neg", "fn main(): int { let l: list<int,2> = [10, 20]; let i: int = 0 - 1; let s: status<int> = l[i]; if is_err(s) { return 1; } else { return 0; } }\n"),
    ("idx-ok-flag", "fn main(): int { let l: list<int,2> = [10, 20]; let s: status<int> = l[0]; if is_ok(s) { return 7; } else { return 0; } }\n"),
    ("idx-err-default", "fn main(): int { let l: list<int,2> = [10, 20]; let s: status<int> = l[9]; return unwrap_or(s, 99); }\n"),
    ("idx-param", "fn at(l: list<int,3>, i: int): status<int> { return l[i]; }\nfn main(): int { let l: list<int,3> = [5, 6, 7]; let s: status<int> = at(l, 2); return unwrap_or(s, 0); }\n"),
    ("idx-ret-status", "fn fetch(l: list<int,2>): status<int> { return l[1]; }\nfn main(): int { let l: list<int,2> = [4, 9]; let s: status<int> = fetch(l); return unwrap_or(s, 0); }\n"),
    ("push-ok-len", "fn main(): int { let l: list<int,2> = [10]; let s: status<list<int,2>> = push(l, 20); let m: list<int,2> = unwrap_or(s, l); return len(m); }\n"),
    ("push-ok-val", "fn main(): int { let l: list<int,2> = [10]; let s: status<list<int,2>> = push(l, 20); let m: list<int,2> = unwrap_or(s, l); let t: status<int> = m[1]; return unwrap_or(t, 0); }\n"),
    ("push-full", "fn main(): int { let l: list<int,2> = [1, 2]; let s: status<list<int,2>> = push(l, 3); if is_err(s) { return 1; } else { return 0; } }\n"),
    ("push-dyn", "fn main(): int { let l: list<int,3> = [1]; let x: int = 2 + 3; let s: status<list<int,3>> = push(l, x); let m: list<int,3> = unwrap_or(s, l); let t: status<int> = m[1]; return unwrap_or(t, 0); }\n"),
    ("push-status-ret", "fn put(l: list<int,2>, v: int): status<list<int,2>> { let s: status<list<int,2>> = push(l, v); return s; }\nfn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = put(l, 2); let m: list<int,2> = unwrap_or(s, l); return len(m); }\n"),
    ("unwrap-bool-err", "fn main(): int { let l: list<bool,2> = [true]; let s: status<bool> = l[5]; if unwrap_or(s, false) { return 1; } else { return 0; } }\n"),
]

REJECT25_CASES = [
    ("list-eq", "fn main(): int { let a: list<int,2> = [1, 2]; let b: list<int,2> = [1, 2]; if a == b { return 1; } else { return 0; } }\n"),
    ("list-field", "record L { l: list<int,2> }\nfn main(): int { let r: L = L(l: [1, 2]); return 0; }\n"),
    ("list-str-elem", "fn main(): int { let l: list<str,2> = [\"a\", \"b\"]; return 0; }\n"),
    ("call-temp-index", "fn mk(): list<int,2> { return [1, 2]; }\nfn main(): int { let s: status<int> = mk()[0]; return 0; }\n"),
    ("return-push-sret", "fn put(l: list<int,2>, v: int): status<list<int,2>> { return push(l, v); }\nfn main(): int { return 0; }\n"),
    ("nested-index", "fn main(): int { let o: list<list<int,2>,2> = [[1, 2], [3, 4]]; let s: status<list<int,2>> = o[1]; return 0; }\n"),
    ("push-nested-val", "fn main(): int { let o: list<list<int,2>,2> = []; let s: status<list<list<int,2>,2>> = push(o, [1, 2]); return 0; }\n"),
    ("idx-rec-elem", "record Pair { a: int, b: int }\nfn main(): int { let l: list<Pair,2> = [Pair(a: 1, b: 2)]; let s: status<Pair> = l[0]; return 0; }\n"),
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
        self.assertGreater(len(set(outs)), 5)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_value_spots(self):
        by_name = {n: e for n, _h, _w, e in self.images}
        self.assertEqual(by_name["idx-0"], 10)
        self.assertEqual(by_name["idx-1"], 20)
        self.assertEqual(by_name["push-ok-val"], 20)
        self.assertEqual(by_name["push-full"], 1)


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
                     "if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { if tbase(vt->t) == 3 { return be_s_len_str(src, f, fs, fe, t, v, cp); } else { } return BZ(p: cp->p, n: sz_mov_rax_home(base), c: 0, o: 0); } else { } } else { } }",
                     "if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { if tbase(vt->t) == 3 { return be_s_len_str(src, f, fs, fe, t, v, cp); } else { } return BZ(p: cp->p, n: sz_mov_rax_home(base + 1), c: 0, o: 0); } else { } } else { } }")
        combo = _mut(combo,
                     "if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { if tbase(vt->t) == 3 { return be_e_len_str(src, f, fs, fe, v, acc, cp->p); } else { } return BZ(p: cp->p, n: e_mov_rax_home(base, acc), c: 0, o: 0); } else { } } else { } }",
                     "if cp->k == 4 { if cp->l == 1 { if tok_byte(src, cp->s) == 41 { if tbase(vt->t) == 3 { return be_e_len_str(src, f, fs, fe, v, acc, cp->p); } else { } return BZ(p: cp->p, n: e_mov_rax_home(base + 1, acc), c: 0, o: 0); } else { } } else { } }")
        self.assertTrue(self._red_on(combo, self._idx("list-full-2")))

    def test_be_m2_idx_no_imul(self):
        combo = _mut(_combo_text(),
                     "fn be_e_index_load(dk: int, ds: int, sbase: int, acc: int): int {\n  let a0: int = e_imul_rax_imm(8, acc);",
                     "fn be_e_index_load(dk: int, ds: int, sbase: int, acc: int): int {\n  let a0: int = acc;")
        self.assertTrue(self._red_on(combo, self._idx("idx-1")))

    def test_be_m3_idx_no_bound_check(self):
        combo = _mut(_combo_text(),
                     "  let a4: int = e_jae(jaed, a3);\n  let a5: int = be_e_index_ok(dk, ds, sbase, a4);",
                     "  let a4: int = a3;\n  let a5: int = be_e_index_ok(dk, ds, sbase, a4);")
        self.assertTrue(self._red_on(combo, self._idx("idx-oob")))

    def test_be_m4_push_no_full_check(self):
        combo = _mut(_combo_text(),
                     "  let a4: int = e_jae(jaed, a3);\n  let a5: int = be_e_push_ok(src, f, dk, ds, w, sbase, a4);",
                     "  let a4: int = a3;\n  let a5: int = be_e_push_ok(src, f, dk, ds, w, sbase, a4);")
        self.assertTrue(self._red_on(combo, self._idx("push-full")))

    def test_be_m5_unwrap_swapped(self):
        combo = _mut(_combo_text(),
                     "  let a4: int = e_pop_rax(a3);\n  let a5: int = e_jmp_rel(jmpd, a4);\n  let a6: int = e_pop_rcx(a5);",
                     "  let a4: int = e_pop_rcx(a3);\n  let a5: int = e_jmp_rel(jmpd, a4);\n  let a6: int = e_pop_rcx(a5);")
        self.assertTrue(self._red_on(combo, self._idx("idx-err-default")))

    def test_be_m6_frame_bytes_omitted(self):
        combo = _mut(_combo_text(),
                     "  let fr: int = nl + be_unit_maxrec(src, f);",
                     "  let fr: int = nl - be_unit_maxrec(src, f);")
        combo = _mut(combo,
                     "  let fr: int = e_frame(nl + be_unit_maxrec(src, f), acc);",
                     "  let fr: int = e_frame(nl - be_unit_maxrec(src, f), acc);")
        self.assertTrue(self._red_on(combo, self._idx("list-ret-lit")))

    def test_be_m7_wrong_ret_dst(self):
        combo = _mut(_combo_text(),
                     "  let e: BZ = be_s_aggex(src, f, fs, fe, 1, 0, rt, nx->s, end);",
                     "  let e: BZ = be_s_aggex(src, f, fs, fe, 0, 0, rt, nx->s, end);")
        combo = _mut(combo,
                     "  let e: BZ = be_e_aggex(src, f, fs, fe, 1, 0, rt, nx->s, end, acc);",
                     "  let e: BZ = be_e_aggex(src, f, fs, fe, 0, 0, rt, nx->s, end, acc);")
        self.assertTrue(self._red_on(combo, self._idx("list-ret-lit")))

    def test_be_m8_copy_src_plus1(self):
        combo = _mut(_combo_text(),
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_size(base, dk, ds, w), c: 0, o: 0);\n}\nfn be_is_push",
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_size(base + 1, dk, ds, w), c: 0, o: 0);\n}\nfn be_is_push")
        combo = _mut(combo,
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_emit(base, dk, ds, w, acc), c: 0, o: 0);\n}\nfn be_e_aggex_call",
                     "  let base: int = be_home_base(src, f, fs, fe, v);\n  let w: int = tslots(ty, src, f);\n  return BZ(p: t->p, n: be_copy_emit(base + 1, dk, ds, w, acc), c: 0, o: 0);\n}\nfn be_e_aggex_call")
        self.assertTrue(self._red_on(combo, self._idx("list-copy")))


if __name__ == "__main__":
    unittest.main()
