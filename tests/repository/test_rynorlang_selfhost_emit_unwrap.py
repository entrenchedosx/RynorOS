"""Stage 19e G3 nested status-call `unwrap_or` in compiler-source shapes.

G3 extends the backend (baby `rynorlang/selfhost/emit.rl`, core dialect)
so `unwrap_or` accepts compiler-source scrutinees that evaluate to
`status<list<..>>` — inline `push(l, v)` and user calls returning
`status<list<..>>` — alongside the previously supported status variable.
Scalar `unwrap_or` (G2) is untouched: `byte_at` scrutinees stay backend-25,
`str` payloads stay 25, and the `return-push-sret` (dk=1) gate stays 25.

Semantics (frozen): pure local select — the scrutinee runs first, then the
default; the ok path yields the scrutinee payload, the err path yields the
default, in place. No enclosing-function propagation (no `?` operator).

New machine forms (caller-saved only): sub/add rsp,imm32, lea rax,[rsp+ib],
mov rax,[rsp+ib], mov [rsp+ib],rax, mov rax,[rdi+ib]. The nested status is
staged in a home-free machine-stack buffer; RSP balances on both paths.

QEMU/native proof lives in tests/integration/test_native_backend.py;
here execution is proven by the test-only emulator below (it EXECUTES baby
bytes; it never generates code and cannot mask a broken backend).
Reference semantics come from the trusted host oracle (exit low 32 bits).
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


MASK64 = bea.MASK64
CODE_BASE = bea.CODE_BASE
STACK_TOP = bea.STACK_TOP


class Emu(bel.Emu):
    def _rex(self):
        R = self.reg
        off = self.rip - CODE_BASE
        b1 = self.code[off]
        if b1 == 0x81 and self.code[off + 1] in (0xEC, 0xC4):
            sub = self.code[off + 1] == 0xEC
            self._fetch(2)
            imm = int.from_bytes(self._fetch(4), "little", signed=True)
            if sub:
                R["rsp"] = (R["rsp"] - imm) & MASK64
            else:
                R["rsp"] = self._flags_add(R["rsp"], imm & MASK64)
            return
        if b1 == 0x8D and self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            R["rax"] = (R["rsp"] + d) & MASK64
            return
        if b1 == 0x8B and self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            R["rax"] = self._sread((R["rsp"] + d) & MASK64, 8)
            return
        if b1 == 0x89 and self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            self._swrite((R["rsp"] + d) & MASK64, 8, R["rax"])
            return
        if b1 == 0x83 and self.code[off + 1] == 0xC4:
            self._fetch(2)
            imm = self._fetch(1)[0]
            R["rsp"] = self._flags_add(R["rsp"], imm & MASK64)
            return
        if b1 == 0x8B and self.code[off + 1] == 0x47:
            self._fetch(2)
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            R["rax"] = self._sread((R["rdi"] + d) & MASK64, 8)
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
    out = em.run()
    assert em.reg["rsp"] == STACK_TOP, f"RSP imbalance {em.reg['rsp']:#x}"
    return out, bytes(em.stdout)


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


ACCEPT_CASES = [
    ("g3-push-int-ok", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); return len(m); }\n"),
    ("g3-push-int-err", "fn main(): int { let l: list<int,2> = [1, 2]; let m: list<int,2> = unwrap_or(push(l, 9), l); return len(m); }\n"),
    ("g3-push-cap1-ok", "fn main(): int { let l: list<int,1> = []; let m: list<int,1> = unwrap_or(push(l, 5), l); return len(m); }\n"),
    ("g3-push-cap1-err", "fn main(): int { let l: list<int,1> = [4]; let m: list<int,1> = unwrap_or(push(l, 5), l); return len(m); }\n"),
    ("g3-push-bool-ok", "fn main(): int { let l: list<bool,2> = [true]; let m: list<bool,2> = unwrap_or(push(l, false), l); return len(m); }\n"),
    ("g3-push-bool-err", "fn main(): int { let l: list<bool,2> = [true, false]; let m: list<bool,2> = unwrap_or(push(l, true), l); return len(m); }\n"),
    ("g3-push-cap3-ok", "fn main(): int { let l: list<int,3> = [5, 6, 7]; let m: list<int,3> = unwrap_or(push(l, 8), l); return unwrap_or(m[3], 0 - 1); }\n"),
    ("g3-push-cap3-full-err", "fn main(): int { let l: list<int,3> = [5, 6, 0]; let m: list<int,3> = unwrap_or(push(l, 8), l); return len(m); }\n"),
    ("g3-pay-elem2", "fn main(): int { let l: list<int,3> = [5, 6, 7]; let m: list<int,3> = unwrap_or(push(l, 8), l); return unwrap_or(m[2], 0); }\n"),
    ("g3-pay-elem01", "fn main(): int { let l: list<int,3> = [5, 6, 7]; let m: list<int,3> = unwrap_or(push(l, 8), l); return unwrap_or(m[0], 0) + unwrap_or(m[1], 0) * 10; }\n"),
    ("g3-pay-mid", "fn main(): int { let l: list<int,2> = [10, 20]; let m: list<int,2> = unwrap_or(push(l, 30), l); return unwrap_or(m[1], 0); }\n"),
    ("g3-call-ok", "fn mk(l: list<int,2>): status<list<int,2>> { let s: status<list<int,2>> = push(l, 3); return s; }\nfn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(mk(l), l); return len(m); }\n"),
    ("g3-call-err", "fn mk(l: list<int,2>): status<list<int,2>> { let s: status<list<int,2>> = push(l, 3); return s; }\nfn main(): int { let l: list<int,2> = [1, 2]; let m: list<int,2> = unwrap_or(mk(l), l); return len(m); }\n"),
    ("g3-var-ok", "fn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = push(l, 2); let m: list<int,2> = unwrap_or(s, l); return len(m); }\n"),
    ("g3-var-err", "fn main(): int { let l: list<int,2> = [1, 2]; let s: status<list<int,2>> = push(l, 2); let m: list<int,2> = unwrap_or(s, l); return len(m); }\n"),
    ("g3-nest-default", "fn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = push(l, 2); let m: list<int,2> = unwrap_or(push(l, 9), unwrap_or(s, l)); return len(m); }\n"),
    ("g3-listlit-default", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 9), [9, 9]); return len(m); }\n"),
    ("g3-use-both-lens", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); return len(m) + len(l); }\n"),
    ("g3-trailing-lets", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); let z1: int = 111; let z2: int = 222; return z1 + z2; }\n"),
    ("g3-scalar-ret", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); return 7; }\n"),
    ("g3-len-m-plus-z", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); let z: int = 111; return len(m) + z; }\n"),
    ("g3-var-use-len", "fn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = push(l, 2); let m: list<int,2> = unwrap_or(s, l); let z: int = len(m); return z; }\n"),
    ("g3-nest-use-len", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); let z: int = len(m); return z; }\n"),
    ("g3-arg-use", "fn id2(x: list<int,2>): int { return len(x); }\nfn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); return id2(m); }\n"),
    ("g3-isok-uses", "fn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = push(l, 2); let m: list<int,2> = unwrap_or(s, l); if is_ok(s) { return len(m); } else { return 0 - 1; } }\n"),
    ("g3-scalar-frozen-call", "fn at(l: list<int,3>, i: int): status<int> { return l[i]; }\nfn main(): int { let l: list<int,3> = [5, 6, 7]; return unwrap_or(at(l, 2), 0); }\n"),
    ("g3-scalar-frozen-idx", "fn main(): int { let l: list<int,3> = [5, 6, 7]; return unwrap_or(l[1], 0); }\n"),
    ("g3-scalar-frozen-idxerr", "fn main(): int { let l: list<int,3> = [5, 6, 7]; return unwrap_or(l[9], 42); }\n"),
    ("g3-push-neg", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 0 - 4), l); return unwrap_or(m[1], 0); }\n"),
    ("g3-err-default-used", "fn main(): int { let l: list<int,2> = [8, 9]; let m: list<int,2> = unwrap_or(push(l, 1), l); return unwrap_or(m[0], 0) + unwrap_or(m[1], 0) * 100; }\n"),
]

REJECT25_CASES = [
    ("byteat-agg-scrutinee", "fn main(): int { let l: list<int,2> = [1]; let m: list<int,2> = unwrap_or(push(l, 2), l); return unwrap_or(byte_at(\"hi\", 9), 7) + len(m); }\n"),
    ("byteat-scalar-scrutinee", "fn main(): int { return unwrap_or(byte_at(\"hi\", 0), 7); }\n"),
    ("return-push-sret", "fn mk(l: list<int,2>): list<int,2> { return unwrap_or(push(l, 7), l); }\nfn main(): int { let l: list<int,2> = [1]; return len(mk(l)); }\n"),
    # NOTE (G4): `unwrap_or(byte_at(s, ...))` with a str-variable scrutinee
    # is now supported; its coverage lives in
    # test_rynorlang_selfhost_emit_strbyte.py (g4-byte-*, g4-str-payload).
]


class G3AcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 30)

    def test_02_determinism(self):
        again = _run_be(self.combo, ACCEPT_CASES)
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w, _e in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        outs = [(w, e) for _n, _h, w, e in self.images]
        self.assertGreater(len(set(outs)), 8)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_value_spots(self):
        by_name = {n: e for n, _h, _w, e in self.images}
        self.assertEqual(by_name["g3-push-int-ok"], 2)
        self.assertEqual(by_name["g3-push-int-err"], 2)
        self.assertEqual(by_name["g3-pay-elem01"], 65)
        self.assertEqual(by_name["g3-pay-mid"], 20)
        self.assertEqual(by_name["g3-trailing-lets"], 333)
        self.assertEqual(by_name["g3-err-default-used"], 908)
        self.assertEqual(by_name["g3-scalar-frozen-call"], 7)

    def test_06_var_form_uses_no_new_forms(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            if name in ("g3-var-ok", "g3-var-err"):
                for i in range(len(code) - 4):
                    if code[i] == 0x48 and code[i + 1] in (0x8B, 0x89, 0x8D) and code[i + 2] == 0x44 and code[i + 3] == 0x24:
                        self.fail(f"rsp-relative form in pre-existing {name}")


class G3RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)

    def test_07_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


class G3MutantTests(unittest.TestCase):
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

    def test_g3_m1_ok_size_drop_pop(self):
        combo = _mut(_combo_text(),
                     "  return sz_pop_rax() + be_store_size(dk, ds, i) + be_s_unwrap_agg_ok_size2(src, f, dk, ds, w, i + 1);",
                     "  return be_store_size(dk, ds, i) + be_s_unwrap_agg_ok_size2(src, f, dk, ds, w, i + 1);")
        self.assertTrue(self._red_on(combo, self._idx("g3-push-int-ok")))

    def test_g3_m2_ok_emit_extra_pop(self):
        combo = _mut(_combo_text(),
                     "  let a0: int = e_pop_rax(acc);\n  let a1: int = be_store_emit(dk, ds, i, a0);\n  return be_e_unwrap_agg_ok(src, f, dk, ds, w, i + 1, a1);",
                     "  let a0: int = e_pop_rax(acc);\n  let a00: int = e_pop_rax(a0);\n  let a1: int = be_store_emit(dk, ds, i, a00);\n  return be_e_unwrap_agg_ok(src, f, dk, ds, w, i + 1, a1);")
        self.assertTrue(self._red_on(combo, self._idx("g3-push-int-ok")))

    def test_g3_m3_select_inverted(self):
        combo = _mut(_combo_text(),
                     "  let a2: int = e_jcc_z(jzd, a1);\n  let a3: int = e_add_rsp_w(w + 1, a2);\n  let a4: int = e_jmp_rel(jmpd, a3);",
                     "  let a2: int = e_jmp_rel(jzd, a1);\n  let a3: int = e_add_rsp_w(w + 1, a2);\n  let a4: int = e_jmp_rel(jmpd, a3);")
        self.assertTrue(self._red_on(combo, self._idx("g3-push-int-err")))

    def test_g3_m4_spill_drops_word(self):
        combo = _mut(_combo_text(),
                     "  let a0: int = be_e_unwrap_agg_pushw(src, f, dk, ds, w, w + 1, acc);",
                     "  let a0: int = be_e_unwrap_agg_pushw(src, f, dk, ds, w, w, acc);")
        self.assertTrue(self._red_on(combo, self._idx("g3-push-int-ok")))

    def test_g3_m5_tmpcopy_disp_shift(self):
        combo = _mut(_combo_text(),
                     "  let a1: int = e_store_rax_rsp(16 - 8 * i, a0);",
                     "  let a1: int = e_store_rax_rsp(8 - 8 * i, a0);")
        self.assertTrue(self._red_on(combo, self._idx("g3-scalar-frozen-idx")))

    def test_g3_m6_payload_reads_code(self):
        combo = _mut(_combo_text(),
                     "  let a8: int = e_load_rax_rsp(8, a7);",
                     "  let a8: int = e_load_rax_rsp(16, a7);")
        self.assertTrue(self._red_on(combo, self._idx("g3-scalar-frozen-call")))

    def test_g3_m7_payload_reads_tag(self):
        combo = _mut(_combo_text(),
                     "  let a8: int = e_load_rax_rsp(8, a7);",
                     "  let a8: int = e_load_rax_rsp(24, a7);")
        self.assertTrue(self._red_on(combo, self._idx("g3-scalar-frozen-call")))

    def test_g3_m8_sret_close_leaks(self):
        combo = _mut(_combo_text(),
                     "fn be_sret_close_dk(dk: int, acc: int, ragg: int): int {\n  if ragg == 0 { return acc; } else { }\n  if dk == 2 { return acc; } else { }",
                     "fn be_sret_close_dk(dk: int, acc: int, ragg: int): int {\n  if ragg == 0 { return acc; } else { }\n  if dk == 2 { return e_add_rsp_n(8, acc); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("g3-scalar-frozen-call")))

    def test_g3_m9_sretptr_shifted(self):
        combo = _mut(_combo_text(),
                     "fn be_unwrap_stack_open(w: int, acc: int): int {\n  let a0: int = e_sub_rsp_n(8 * w + 8, acc);\n  let a1: int = e_lea_rax_rsp(8, a0);",
                     "fn be_unwrap_stack_open(w: int, acc: int): int {\n  let a0: int = e_sub_rsp_n(8 * w + 8, acc);\n  let a1: int = e_lea_rax_rsp(16, a0);")
        self.assertTrue(self._red_on(combo, self._idx("g3-scalar-frozen-call")))

    def test_g3_m10_size_drops_push(self):
        combo = _mut(_combo_text(),
                     "  if dk == 2 { return sz_sub_rsp_n() + sz_lea_rax_rsp() + sz_push_rax(); } else { }",
                     "  if dk == 2 { return sz_sub_rsp_n() + sz_lea_rax_rsp(); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("g3-scalar-frozen-call")))

    def test_g3_m11_ok_store_slides_up(self):
        combo = _mut(_combo_text(),
                     "  let a0: int = e_pop_rax(acc);\n  let a1: int = be_store_emit(dk, ds, i, a0);\n  return be_e_unwrap_agg_ok(src, f, dk, ds, w, i + 1, a1);",
                     "  let a0: int = e_pop_rax(acc);\n  let a1: int = be_store_emit(dk, ds, i + 1, a0);\n  return be_e_unwrap_agg_ok(src, f, dk, ds, w, i + 1, a1);")
        self.assertTrue(self._red_on(combo, self._idx("g3-push-int-ok")))

    def test_g3_m12_ok_target_short(self):
        combo = _mut(_combo_text(),
                     "fn be_e_unwrap_agg_cgo(src: str, f: int, dk: int, ds: int, w: int, acc: int, pos: int): BZ {\n  let ok: int = sz_pop_rax() + be_e_unwrap_agg_ok_size(src, f, dk, ds, w, 0);",
                     "fn be_e_unwrap_agg_cgo(src: str, f: int, dk: int, ds: int, w: int, acc: int, pos: int): BZ {\n  let ok: int = be_e_unwrap_agg_ok_size(src, f, dk, ds, w, 0);")
        self.assertTrue(self._red_on(combo, self._idx("g3-push-int-err")))


if __name__ == "__main__":
    unittest.main()
