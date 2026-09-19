"""Stage 19e M2 match statement lowering.

M2 lowers `match` on status/int/bool scrutinees (variable/param homes,
or calls evaluated once into a temp home below the first binding slot)
with ok(v)/err(e)/int-lit/bool-lit/_/bind arms. Status uses a fixed
two-path layout (tag test + jz to the ok path, err path inline, jmp to
a common done); int/bool uses a per-arm reload + cmp-imm/jne chain
(arm bodies clobber rax, so every arm reloads the value first).

Machine forms: mov rax,[home] (existing), test rax,rax (existing),
jz rel32 (existing), jmp rel32 (existing), home-to-home payload/code
copies (existing be_copy_size/emit), plus two new audited emitters:
cmp rax,imm32 (48 3D ib32) and jne rel32 (0F 85 cd). Caller-saved
only; no RBX; G2 ABI untouched.

Scope (stay backend-25): str scrutinee (no str-== primitive),
result scrutinee, out-of-range int literals, negative-lit edge cases
beyond span_int, status-whole bindings, arm bodies that write
call-temp homes (record/list construction, push, unwrap_*, calls
with aggregate args/returns). Frame budget extended by be_fn_maxarm
(binding slots total+armidx+mdepth*8, widths gated to the stride);
call-scrutinee temps gated to 7 words (below the first binding).

QEMU/native proof lives in probe_m2native.py (5/5 MATCH at M2
commit); here execution is proven by the test-only emulator chain
extended below with exactly the two new instructions (it EXECUTES
baby bytes; it never generates code). Reference semantics come from
the trusted host oracle (exit low 32 bits). RYNX extent
(`len(raw) == 28 + csz + fsz`) is asserted per case.
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_list as bel  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_stackargs as bes  # noqa: E402
from tests.repository.test_rynorlang_selfhost_emit_data import DATA_BASE  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


MASK64 = bea.MASK64
CODE_BASE = bea.CODE_BASE
STACK_TOP = bea.STACK_TOP


class Emu(bes.Emu):
    def __init__(self, code, data=b""):
        super().__init__(code, data)

    def step(self):
        off = self.rip - CODE_BASE
        if self.code[off:off + 1] == bytes([0x48]) and self.code[off + 1:off + 2] == bytes([0x3D]):
            self._fetch(2)
            imm = int.from_bytes(self._fetch(4), "little", signed=True)
            self._flags_sub(self.reg["rax"], imm & MASK64)
            return
        if self.code[off:off + 2] == bytes([0x0F, 0x85]):
            self._fetch(2)
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            if not self.zf:
                self.rip = (self.rip + disp) & MASK64
            return
        super().step()


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
    ("m2-status-ok", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[0]; }\nfn main(): int { let s: status<int> = w(5); match s { ok(v) => { return v; }, err(e) => { return 0 - 1; } }\n}\n'),
    ("m2-status-err", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[2]; }\nfn main(): int { let s: status<int> = w(5); match s { ok(v) => { return v; }, err(e) => { return e; } }\n}\n'),
    ("m2-status-wild", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[0]; }\nfn main(): int { let s: status<int> = w(5); match s { ok(v) => { return v; }, _ => { return 77; } }\n}\n'),
    ("m2-int-lit", 'fn main(): int { let x: int = 2; match x { 1 => { return 1; }, 2 => { return 2; }, _ => { return 0; } }\n}\n'),
    ("m2-int-bind", 'fn main(): int { let x: int = 7; match x { y => { return y; } }\n}\n'),
    ("m2-bool-arms", 'fn f(): bool { return true; }\nfn main(): int { let b: bool = f(); match b { true => { return 1; }, false => { return 0; } }\n}\n'),
    ("m2-call-scrut", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[0]; }\nfn main(): int { match w(5) { ok(v) => { return v; }, err(e) => { return 0 - 1; } }\n}\n'),
    ("m2-param-scrut", 'fn f(s: status<int>): int { match s { ok(v) => { return v; }, err(e) => { return e; } }\n}\nfn w(x: int): status<int> { let l: list<int,1> = [x]; return l[2]; }\nfn main(): int { return f(w(5)); }\n'),
    ("m2-nested", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[0]; }\nfn main(): int { let s: status<int> = w(5); match s { ok(v) => { match v { 5 => { return 1; }, _ => { return 2; } } }, err(e) => { return 3; } }\n}\n'),
    ("m2-scalar-call", 'fn h(x: int): int { return x + 1; }\nfn main(): int { match h(1) { 1 => { return 10; }, 2 => { return 20; }, _ => { return 30; } }\n}\n'),
    ("m2-err-first", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[2]; }\nfn main(): int { let s: status<int> = w(5); match s { err(e) => { return e; }, ok(v) => { return v; } }\n}\n'),
    ("m2-wild-only", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[0]; }\nfn main(): int { let s: status<int> = w(5); match s { _ => { return 9; } }\n}\n'),
]

REJECT25_CASES = [
    ("str-scrutinee", 'fn f(s: str): int { match s { x => { return 1; } }\n}\nfn main(): int { return f("a"); }\n'),
]


class M2AcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 10)

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
        self.assertEqual(by_name["m2-status-ok"], 5)
        self.assertEqual(by_name["m2-status-err"], 3)
        self.assertEqual(by_name["m2-status-wild"], 5)
        self.assertEqual(by_name["m2-int-lit"], 2)
        self.assertEqual(by_name["m2-int-bind"], 7)
        self.assertEqual(by_name["m2-bool-arms"], 1)
        self.assertEqual(by_name["m2-call-scrut"], 5)
        self.assertEqual(by_name["m2-param-scrut"], 3)
        self.assertEqual(by_name["m2-nested"], 1)
        self.assertEqual(by_name["m2-scalar-call"], 20)
        self.assertEqual(by_name["m2-err-first"], 3)
        self.assertEqual(by_name["m2-wild-only"], 9)

    def test_06_no_callee_saved_scratch(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            for i, b in enumerate(code):
                self.assertNotIn(b, (0x53, 0x5B), f"rbx push/pop in {name} at {i:#x}")

    def test_07_rnyx_extent_exact(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertEqual(fsz, msz, name)
            self.assertEqual(len(raw), 28 + csz + fsz, name)


class M2RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)

    def test_08_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


class M2MutantTests(unittest.TestCase):
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

    def test_m2_m1_tag_load_dropped(self):
        combo = _mut(_combo_text(),
                     "  let t0: int = e_mov_rax_home(base, acc);",
                     "  let t0: int = acc;")
        self.assertTrue(self._red_on(combo, self._idx("m2-status-ok")))

    def test_m2_m2_jz_becomes_jnz(self):
        combo = _mut(_combo_text(),
                     "  let t2: int = e_jcc_z(be_rel32(okat, t1, sz_jcc()), t1);",
                     "  let t2: int = e_jcc_nz(be_rel32(okat, t1, sz_jcc_nz()), t1);")
        self.assertTrue(self._red_on(combo, self._idx("m2-status-ok")))

    def test_m2_m3_err_bind_shifted(self):
        combo = _mut(_combo_text(),
                     "  if side == 1 { return BZ(p: bd->off, n: be_copy_emit(base + 1, 0, dh, 1, acc), c: 0, o: 0); } else { }",
                     "  if side == 1 { return BZ(p: bd->off, n: be_copy_emit(base + 2, 0, dh, 1, acc), c: 0, o: 0); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m2-status-err")))

    def test_m2_m4_cmp_imm_dropped(self):
        combo = _mut(_combo_text(),
                     "  let c0: int = e_cmp_rax_imm(vv, l0);",
                     "  let c0: int = l0;")
        self.assertTrue(self._red_on(combo, self._idx("m2-int-lit")))

    def test_m2_m5_lit_size_short(self):
        combo = _mut(_combo_text(),
                     "  return be_s_mchain(src, f, fs, fe, nx, me, end, stbase, base, acc + sz_mov_rax_home(base) + sz_cmp_rax_imm() + sz_jcc_nz() + bb->n + sz_jmp());",
                     "  return be_s_mchain(src, f, fs, fe, nx, me, end, stbase, base, acc + sz_mov_rax_home(base) + sz_jcc_nz() + bb->n + sz_jmp());")
        self.assertTrue(self._red_on(combo, self._idx("m2-int-lit")))


if __name__ == "__main__":
    unittest.main()
