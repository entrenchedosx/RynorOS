"""Stage 19e M8 string returns through the frozen sret ABI.

M8 lowers `-> str` in the baby backend (`rynorlang/selfhost/emit.rl`, core
dialect) by reusing the EXISTING aggregate-return machinery (no second
return ABI): a string descriptor is 2 machine words (ptr, len); the
callee writes them through the hidden sret pointer at [rbp+16]
(AT-slot-0, descending words like every aggregate return) into
caller-owned storage (home pair, outer sret forward, or temp pair).

Lowering added (size + emit twins, all pre-existing machine forms):
(A) gate accepts str helper-returns; be_s/be_e_return route tbase==3
    through return_agg (dk=1); be_callee_ret_agg counts str (caller
    reserves the sret slot). be_subset_ty itself untouched.
(B) str-valued calls as str sources (`return g()`, `let u: str = g()`)
    via be_[se]_aggex_call with tscal(3).
(C) list<str> literals via listelem_agg (element width 2).
(D) `unwrap_or(h[i], default)` over list<str> (hexch shape): NEW
    be_[se]_unwrap_agg_index with a default-first-then-overwrite layout
    (the host evaluates the default eagerly, so the default lowers to
    (dk,ds) first and the ok path overwrites it with the element
    descriptor). Zero frame temps; rax/rcx/rsi only.
(E) str-typed call args that are calls (`f(g(x))`): lower into the temp
    pair, push ptr+len in descriptor order (callarg_agg mirror).
(F) `print(f(x))` for str callees: call into the temp pair, then the
    proven M3 str-home print template. Non-str calls stay 25.

Lifetime: only DESCRIPTORS are copied; bytes always live in immortal
RYNX .rodata (literals) or ancestor frames (params/locals outlive the
call), so no returned descriptor can dangle. Empty strings behave
(len 0, no deref; byte access stays length-guarded).

Out of scope (stay backend-25): `len(f(x))` / `byte_at(f(x),..)`
direct (use a let-bound var), materialized `status<str>` from indexing
(`let st: status<str> = h[i]`), `fn main(): str` (main stays int),
non-str print-calls.

QEMU/native proof lives in probe_m8native.py (MATCH at M8 commit); here
execution is proven by the test-only MI emulator (fjoin x print layers:
it EXECUTES baby bytes; it never generates code). Reference semantics
come from the trusted host oracle (exit low 32 bits + stdout bytes).
RYNX extent (`len(raw) == 28 + csz + fsz`) is asserted per case; every
branch/call target must land on a swept instruction boundary
(fail-closed sweep imported from the M6 suite).
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_fjoin as bef  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_print as bepr  # noqa: E402
from tests.repository.test_rynorlang_selfhost_emit_fjoin import _sweep_starts  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


MASK64 = bea.MASK64
CODE_BASE = bea.CODE_BASE
STACK_TOP = bea.STACK_TOP


class Emu(bef.Emu, bepr.Emu):
    """MI: fjoin layer claims str/call/sret forms first, print layer
    claims int80 + print templates, both fall back cooperatively."""


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


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
    out = em.run()
    assert em.reg["rsp"] == STACK_TOP, f"RSP imbalance {em.reg['rsp']:#x}"
    return out, bytes(em.stdout)


HEXCH_DEF = ('fn hexch(d: int): str { let h: list<str,16> = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f"]; '
             'return unwrap_or(h[d], "?"); }\n')

ACCEPT_CASES = [
    ("m8-lit-ret", 'fn f(): str { return "ab"; }\nfn main(): int { let u: str = f(); return len(u); }\n', b"", 2),
    ("m8-empty-ret", 'fn f(): str { return ""; }\nfn main(): int { let u: str = f(); return len(u); }\n', b"", 0),
    ("m8-param-ret", 'fn f(s: str): str { return s; }\nfn main(): int { let u: str = f("hello"); return len(u); }\n', b"", 5),
    ("m8-local-ret", 'fn f(): str { let s: str = "xyz"; return s; }\nfn main(): int { let u: str = f(); return len(u); }\n', b"", 3),
    ("m8-paren-ret", 'fn f(): str { return ("pq"); }\nfn main(): int { let u: str = f(); return len(u); }\n', b"", 2),
    ("m8-direct-call", 'fn g(): str { return "ab"; }\nfn main(): int { let u: str = g(); return unwrap_or(byte_at(u, 1), 0); }\n', b"", 98),
    ("m8-nested-call", 'fn g(): str { return "q"; }\nfn f(): str { return g(); }\nfn main(): int { let u: str = f(); return len(u); }\n', b"", 1),
    ("m8-ifelse-str", 'fn f(x: int): str { if x == 0 { return "aa"; } return "b"; }\nfn main(): int { let a: str = f(0); let b: str = f(1); return len(a) + len(b); }\n', b"", 3),
    ("m8-assign", 'fn f(): str { return "ab"; }\nfn main(): int { let u: str = f(); let v: str = u; return len(v); }\n', b"", 2),
    ("m8-pass-onward", 'fn g(): str { return "abcd"; }\nfn f(s: str): int { return len(s); }\nfn main(): int { return f(g()); }\n', b"", 4),
    ("m8-caller-byte", 'fn f(): str { return "hello"; }\nfn main(): int { let u: str = f(); return unwrap_or(byte_at(u, 4), 0); }\n', b"", 111),
    ("m8-byteat-mid", 'fn f(): str { return "abc"; }\nfn main(): int { let u: str = f(); return unwrap_or(byte_at(u, 0), 0) + unwrap_or(byte_at(u, 2), 0); }\n', b"", 196),
    ("m8-multi-fns", 'fn a(): str { return "x"; }\nfn b(): str { return "yz"; }\nfn main(): int { let u: str = a(); let v: str = b(); return len(u) + len(v); }\n', b"", 3),
    ("m8-mixed-params", 'fn f(x: int, s: str, b: bool): str { return s; }\nfn main(): int { let u: str = f(1, "wxyz", true); return len(u); }\n', b"", 4),
    ("m8-wide-call", 'fn h(a: int, b: int, c: int, d: int, e: int, f: int, g: int): str { return "q"; }\nfn main(): int { let u: str = h(1, 2, 3, 4, 5, 6, 7); return len(u); }\n', b"", 1),
    ("m8-hexch", HEXCH_DEF + 'fn main(): int { let a: str = hexch(10); let b: str = hexch(15); return len(a) + unwrap_or(byte_at(b, 0), 0); }\n', b"", 103),
    ("m8-hexch-oob", HEXCH_DEF + 'fn main(): int { let u: str = hexch(16); return unwrap_or(byte_at(u, 0), 0); }\n', b"", 63),
    ("m8-print-call", 'fn h(): str { return "hi"; }\nfn main(): int { print(h()); return 0; }\n', b"hi", 0),
    ("m8-print-hexch", HEXCH_DEF + 'fn main(): int { print(hexch(11)); return 0; }\n', b"b", 0),
    ("m8-idx-hit", 'fn main(): int { let h: list<str,2> = ["a", "bb"]; let u: str = unwrap_or(h[1], "d"); return len(u); }\n', b"", 2),
    ("m8-idx-len", 'fn main(): int { let h: list<str,3> = ["a", "bb", "ccc"]; let u: str = unwrap_or(h[2], "dddd"); return len(u); }\n', b"", 3),
    ("m8-idx-err", 'fn main(): int { let h: list<str,2> = ["a", "bb"]; let u: str = unwrap_or(h[5], "dddd"); return len(u); }\n', b"", 4),
    ("m8-idx-neg", 'fn main(): int { let h: list<str,2> = ["a", "bb"]; let u: str = unwrap_or(h[0 - 1], "dddd"); return len(u); }\n', b"", 4),
]

REJECT25_CASES = [
    ("r8-len-direct", 'fn g(): str { return "ab"; }\nfn main(): int { return len(g()); }\n'),
    ("r8-byteat-direct", 'fn g(): str { return "ab"; }\nfn main(): int { return unwrap_or(byte_at(g(), 0), 0); }\n'),
    ("r8-status-str-idx", 'fn main(): int { let h: list<str,2> = ["a", "b"]; let st: status<str> = h[0]; return 0; }\n'),
    ("r8-int-call-print", 'fn g(x: int): int { return x; }\nfn main(): int { print(g(7)); return 0; }\n'),
    ("r8-main-strret", 'fn main(): str { return "x"; }\n'),
]


class M8AcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, [(n, s) for n, s, _o, _w in ACCEPT_CASES])
        cls.images = []
        for (code, off, hexstr), (name, src, want_out, want_exit) in zip(cls.results, ACCEPT_CASES):
            assert code == 0, (name, code, off)
            raw = bytes.fromhex(hexstr)
            got_exit, got_out = run_image_data(raw)
            want_exit2, want_out2 = _oracle_run(src)
            assert got_exit == want_exit2, (name, got_exit, want_exit2)
            assert got_out == want_out2, (name, got_out, want_out2)
            assert got_exit == want_exit, (name, got_exit, want_exit)
            assert got_out == want_out, (name, got_out, want_out)
            cls.images.append((name, hexstr, want_exit, want_out))

    def test_01_exit_behavior(self):
        self.assertGreaterEqual(len(self.images), 23)

    def test_02_determinism(self):
        again = _run_be(self.combo, [(n, s) for n, s, _o, _w in ACCEPT_CASES])
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w, _o in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        exits = set(w for _n, _h, w, _o in self.images)
        self.assertGreaterEqual(len(exits), 8)
        pairs = set((w, o) for _n, _h, w, o in self.images)
        self.assertGreaterEqual(len(pairs), 8)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w, _o in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_value_spots(self):
        by_name = {n: (w, o) for n, _h, w, o in self.images}
        self.assertEqual(by_name["m8-lit-ret"], (2, b""))
        self.assertEqual(by_name["m8-empty-ret"], (0, b""))
        self.assertEqual(by_name["m8-param-ret"], (5, b""))
        self.assertEqual(by_name["m8-local-ret"], (3, b""))
        self.assertEqual(by_name["m8-paren-ret"], (2, b""))
        self.assertEqual(by_name["m8-direct-call"], (98, b""))
        self.assertEqual(by_name["m8-nested-call"], (1, b""))
        self.assertEqual(by_name["m8-ifelse-str"], (3, b""))
        self.assertEqual(by_name["m8-assign"], (2, b""))
        self.assertEqual(by_name["m8-pass-onward"], (4, b""))
        self.assertEqual(by_name["m8-caller-byte"], (111, b""))
        self.assertEqual(by_name["m8-byteat-mid"], (196, b""))
        self.assertEqual(by_name["m8-multi-fns"], (3, b""))
        self.assertEqual(by_name["m8-mixed-params"], (4, b""))
        self.assertEqual(by_name["m8-wide-call"], (1, b""))
        self.assertEqual(by_name["m8-hexch"], (103, b""))
        self.assertEqual(by_name["m8-hexch-oob"], (63, b""))
        self.assertEqual(by_name["m8-print-call"], (0, b"hi"))
        self.assertEqual(by_name["m8-print-hexch"], (0, b"b"))
        self.assertEqual(by_name["m8-idx-hit"], (2, b""))
        self.assertEqual(by_name["m8-idx-len"], (3, b""))
        self.assertEqual(by_name["m8-idx-err"], (4, b""))
        self.assertEqual(by_name["m8-idx-neg"], (4, b""))

    def test_06_no_callee_saved_scratch(self):
        for name, hexstr, _w, _o in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            for i, b in enumerate(code):
                self.assertNotIn(b, (0x53, 0x5B), f"rbx push/pop in {name} at {i:#x}")

    def test_07_rnyx_extent_exact(self):
        for name, hexstr, _w, _o in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertEqual(fsz, msz, name)
            self.assertEqual(len(raw), 28 + csz + fsz, name)

    def test_08_branch_targets_on_boundaries(self):
        # G4 lesson, enforced: every static branch/call displacement
        # lands on a swept instruction boundary inside the code
        # segment; every call lands on a 0x55 prologue. The sweep is
        # fail-closed (unknown opcode raises, it never skips).
        for name, hexstr, _w, _o in self.images:
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


class M8RejectTests(unittest.TestCase):
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


M8_OK_FN = (
    "fn be_e_straidx_ok(dk: int, ds: int, sbase: int, acc: int): int {\n"
    "  let a0: int = e_imul_rax_imm(16, acc);\n"
    "  let a1: int = e_lea_rsi_home(sbase + 1, a0);\n"
    "  let a2: int = e_sub_rsi_rax(a1);\n"
    "  let a3: int = e_load_rax_rsi(a2);\n"
    "  let a4: int = be_store_emit(dk, ds, 0, a3);\n"
    "  let a5: int = e_sub_rsi_ib(8, a4);\n"
    "  let a6: int = e_load_rax_rsi(a5);\n"
    "  let a7: int = be_store_emit(dk, ds, 1, a6);\n"
    "  return a7;\n"
    "}"
)


class M8MutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()

    def _red_on(self, mutant_combo, idx):
        name, src, _o, _w = ACCEPT_CASES[idx]
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
            return (got_exit, got_out) != (want_exit, want_out)
        except (RuntimeError, AssertionError):
            return True

    def _idx(self, name):
        for i, (n, _s, _o, _w) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_m8_m1_gate_str_ret_reverted(self):
        combo = _mut(_combo_text(),
                     "  if tbase(hr) == 3 { } else { if be_subset_ty(hr, src, f, 0, 1) == 1 { } else { return derr(25, f, hs); } }",
                     "  if be_subset_ty(hr, src, f, 0, 1) == 1 { } else { return derr(25, f, hs); }")
        self.assertTrue(self._red_on(combo, self._idx("m8-lit-ret")))

    def test_m8_m2_size_return_route_neutered(self):
        combo = _mut(_combo_text(),
                     "  if tbase(rt) == 3 { return be_s_return_agg(src, f, fs, fe, pos, end, acc, t, nx, rt); } else { }",
                     "  if tbase(rt) == 7 { return be_s_return_agg(src, f, fs, fe, pos, end, acc, t, nx, rt); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m8-lit-ret")))

    def test_m8_m3_callee_ret_agg_reverted(self):
        combo = _mut(_combo_text(),
                     "fn be_callee_ret_agg(src: str, f: int, ci: int): int {\n"
                     "  let rt: list<int,24> = be_callee_ret(src, f, ci);\n"
                     "  if tbase(rt) == 3 { return 1; } else { }\n",
                     "fn be_callee_ret_agg(src: str, f: int, ci: int): int {\n"
                     "  let rt: list<int,24> = be_callee_ret(src, f, ci);\n")
        self.assertTrue(self._red_on(combo, self._idx("m8-lit-ret")))

    def test_m8_m4_strvar_call_reverted(self):
        combo = _mut(_combo_text(),
                     "  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return be_s_aggex_call(src, f, fs, fe, dk, ds, tscal(3), t, end); } else { } } else { } } else { }",
                     "  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return BZ(p: t->s, n: 0, c: 25, o: t->s); } else { } } else { } } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m8-nested-call")))

    def test_m8_m5_listelem_str_neutered(self):
        combo = _mut(_combo_text(),
                     "  if b == 3 { return be_s_listelem_agg(src, f, fs, fe, dk, ds, ty, et, cap, ew, pos, end, idx, acc); } else { }",
                     "  if b == 7 { return be_s_listelem_agg(src, f, fs, fe, dk, ds, ty, et, cap, ew, pos, end, idx, acc); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m8-hexch")))

    def test_m8_m6_ok_drops_len_store(self):
        # Two-sided (sizes stay consistent): the ok path keeps the eager
        # default's len, so m8-idx-len reads 4 ("dddd") instead of 3.
        combo = _combo_text()
        combo = _mut(combo, M8_OK_FN,
                     M8_OK_FN.replace("  let a7: int = be_store_emit(dk, ds, 1, a6);\n", ""))
        combo = _mut(combo,
                     "  return sz_imul_rax_imm() + sz_lea_rsi_home() + sz_sub_rsi_rax() + sz_load_rax_rsi() + be_store_size(dk, ds, 0) + sz_sub_rsi_ib() + sz_load_rax_rsi() + be_store_size(dk, ds, 1);",
                     "  return sz_imul_rax_imm() + sz_lea_rsi_home() + sz_sub_rsi_rax() + sz_load_rax_rsi() + be_store_size(dk, ds, 0) + sz_sub_rsi_ib() + sz_load_rax_rsi();")
        self.assertTrue(self._red_on(combo, self._idx("m8-idx-len")))

    def test_m8_m7_ok_swaps_ptr_len(self):
        combo = _mut(_combo_text(), M8_OK_FN,
                     M8_OK_FN.replace("be_store_emit(dk, ds, 0, a3)", "be_store_emit(dk, ds, 1, a3)").replace(
                         "be_store_emit(dk, ds, 1, a6)", "be_store_emit(dk, ds, 0, a6)"))
        self.assertTrue(self._red_on(combo, self._idx("m8-idx-hit")))

    def test_m8_m8_default_lowering_dropped(self):
        combo = _mut(_combo_text(),
                     "fn be_s_straidx_def2(src: str, f: int, fs: int, fe: int, dk: int, ds: int, ty: list<int,24>, t: Tok, idxn: int, pos: int, end: int): BZ {\n"
                     "  let d: BZ = be_s_aggex(src, f, fs, fe, dk, ds, ty, pos, end);",
                     "fn be_s_straidx_def2(src: str, f: int, fs: int, fe: int, dk: int, ds: int, ty: list<int,24>, t: Tok, idxn: int, pos: int, end: int): BZ {\n"
                     "  let d: BZ = BZ(p: pos, n: 0, c: 0, o: pos);")
        self.assertTrue(self._red_on(combo, self._idx("m8-idx-hit")))

    def test_m8_m9_strcallarg_drops_len_push(self):
        # Two-sided (sizes stay consistent): only the ptr reaches the
        # callee; the len home picks up caller-stack garbage, so the
        # length diverges from 4.
        combo = _combo_text()
        combo = _mut(combo,
                     "  return be_s_callsep(src, f, fs, fe, e->p, end, np, i, acc + e->n + be_push_home_size(tb, 2), ci, tb);",
                     "  return be_s_callsep(src, f, fs, fe, e->p, end, np, i, acc + e->n + be_push_home_size(tb, 1), ci, tb);")
        combo = _mut(combo,
                     "  return be_e_callsep(src, f, fs, fe, e->p, end, np, i, be_push_home(tb, 2, e->n), ci, tb);",
                     "  return be_e_callsep(src, f, fs, fe, e->p, end, np, i, be_push_home(tb, 1, e->n), ci, tb);")
        self.assertTrue(self._red_on(combo, self._idx("m8-pass-onward")))

    def test_m8_m10_print_strcall_reverted(self):
        combo = _mut(_combo_text(),
                     "  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return be_e_print_strcall(src, f, fs, fe, t, st, cp, end, acc); } else { } } else { } } else { }",
                     "  if nx->k == 4 { if nx->l == 1 { if tok_byte(src, nx->s) == 40 { return BZ(p: t->s, n: acc, c: 25, o: t->s); } else { } } else { } } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m8-print-hexch")))

    def test_m8_m11_ok_wrong_stride(self):
        combo = _mut(_combo_text(), M8_OK_FN,
                     M8_OK_FN.replace("e_imul_rax_imm(16, acc)", "e_imul_rax_imm(8, acc)"))
        self.assertTrue(self._red_on(combo, self._idx("m8-idx-hit")))

    def test_m8_m12_sign_check_neutered(self):
        # Same-size swap (js->jae, both rel32): test always clears CF,
        # so jae is always taken and the ok path becomes unreachable;
        # m8-idx-hit then yields the default length 1 instead of 2.
        combo = _mut(_combo_text(),
                     "fn be_e_straidx_go(src: str, f: int, fs: int, fe: int, dk: int, ds: int, v: VS, acc: int, pos: int): BZ {\n"
                     "  let sbase: int = be_home_base(src, f, fs, fe, v);\n"
                     "  let ok: int = be_s_straidx_ok_size(dk, ds);\n"
                     "  let pre: int = acc + sz_pop_rax() + sz_test_rax() + sz_js() + sz_load_rcx_home() + sz_cmp_rax_rcx() + sz_jae();\n"
                     "  let done: int = pre + ok;\n"
                     "  let jsd: int = be_rel32(done, acc + sz_pop_rax() + sz_test_rax(), sz_js());\n"
                     "  let jaed: int = be_rel32(done, acc + sz_pop_rax() + sz_test_rax() + sz_js() + sz_load_rcx_home() + sz_cmp_rax_rcx(), sz_jae());\n"
                     "  let a0: int = e_pop_rax(acc);\n"
                     "  let a1: int = e_test_rax(a0);\n"
                     "  let a2: int = e_js(jsd, a1);",
                     "fn be_e_straidx_go(src: str, f: int, fs: int, fe: int, dk: int, ds: int, v: VS, acc: int, pos: int): BZ {\n"
                     "  let sbase: int = be_home_base(src, f, fs, fe, v);\n"
                     "  let ok: int = be_s_straidx_ok_size(dk, ds);\n"
                     "  let pre: int = acc + sz_pop_rax() + sz_test_rax() + sz_js() + sz_load_rcx_home() + sz_cmp_rax_rcx() + sz_jae();\n"
                     "  let done: int = pre + ok;\n"
                     "  let jsd: int = be_rel32(done, acc + sz_pop_rax() + sz_test_rax(), sz_js());\n"
                     "  let jaed: int = be_rel32(done, acc + sz_pop_rax() + sz_test_rax() + sz_js() + sz_load_rcx_home() + sz_cmp_rax_rcx(), sz_jae());\n"
                     "  let a0: int = e_pop_rax(acc);\n"
                     "  let a1: int = e_test_rax(a0);\n"
                     "  let a2: int = e_jae(jsd, a1);")
        self.assertTrue(self._red_on(combo, self._idx("m8-idx-hit")))

