"""Stage 19e G4 scalar text reads: len(str) and unwrap_or(byte_at(s, i), default).

G4 extends the backend (baby `rynorlang/selfhost/emit.rl`, core dialect)
with the scalar-int text path that unblocks the compiler-source census
cone (`tok_byte`/`next_tok` callers): `len(s)` on str variables/params,
and `unwrap_or(byte_at(s, i), default)` with a str-variable scrutinee
and scalar-int default, in `return` position (value in RAX) and scalar
`let` position (value stored to the let home).

Semantics (frozen oracle): `len` is the decoded length; `byte_at`
yields `(1, ERR_OORANGE, 0)` when `index < 0 or index >= len(text)`,
else `(0, 0, ord(text[index]))`; `unwrap_or` selects payload/default.
No enclosing-function propagation (no `?` operator).

Representation (frozen G1): str is a (ptr,len) home pair; bytes live in
the RYNX data section at DATA_BASE + offset, immutable, no NUL.

New machine forms (caller-saved only): push rcx, pop rsi,
movzx eax,byte [rsi+rax] (0F B6 04 06, data-segment read). The sequence
spills idx/default on the machine stack, bounds-checks (js/jae to err),
loads ptr into RSI, reads the byte, and drops the dead arm's word; RSP
balances on both paths. RBX is never touched (frozen register model;
host `compile.py` forbids callee-saved scratch).

Out of scope (stay backend-25): literal-str scrutinee, standalone
`byte_at` (statement or `let status<int>`), `str` returns, `list<str>`,
`print(str-var)`, `==`/match on str, forward/self-call relaxation.

QEMU/native proof lives in tests/integration/test_native_backend.py;
here execution is proven by the test-only emulator below (it EXECUTES
baby bytes; it never generates code and cannot mask a broken backend).
Reference semantics come from the trusted host oracle (exit low 32
bits). RYNX extent (`len(raw) == 28 + csz + fsz`) is asserted per case.
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_list as bel  # noqa: E402
from tests.repository.test_rynorlang_selfhost_emit_data import DATA_BASE  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


MASK64 = bea.MASK64
CODE_BASE = bea.CODE_BASE
STACK_TOP = bea.STACK_TOP


class Emu(bel.Emu):
    def step(self):
        off = self.rip - CODE_BASE
        if self.code[off:off + 4] == bytes([0x0F, 0xB6, 0x04, 0x06]):
            self._fetch(4)
            addr = (self.reg["rsi"] + self.reg["rax"]) & MASK64
            doff = addr - DATA_BASE
            if not (0 <= doff < len(self.data)):
                raise RuntimeError(f"byte read out of data {addr:#x}")
            self.reg["rax"] = self.data[doff]
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
    ("g4-len-basic", 'fn main(): int { let s: str = "abc"; return len(s); }\n'),
    ("g4-len-empty", 'fn main(): int { let s: str = ""; return len(s); }\n'),
    ("g4-len-one", 'fn main(): int { let s: str = "x"; return len(s); }\n'),
    ("g4-len-long", 'fn main(): int { let s: str = "hello-world-0123456789"; return len(s); }\n'),
    ("g4-len-param", 'fn clen(src: str): int { return len(src); }\nfn main(): int { return clen("abcd"); }\n'),
    ("g4-len-param-empty", 'fn clen(src: str): int { return len(src); }\nfn main(): int { return clen(""); }\n'),
    ("g4-len-let-copy", 'fn main(): int { let s: str = "qq"; let t: str = s; return len(t); }\n'),
    ("g4-len-arith", 'fn main(): int { let s: str = "abcde"; return len(s) * 2 + 1; }\n'),
    ("g4-byte-first", 'fn main(): int { let s: str = "ABC"; return unwrap_or(byte_at(s, 0), 0 - 1); }\n'),
    ("g4-byte-mid", 'fn main(): int { let s: str = "ABC"; return unwrap_or(byte_at(s, 1), 0 - 1); }\n'),
    ("g4-byte-last", 'fn main(): int { let s: str = "ABC"; return unwrap_or(byte_at(s, 2), 0 - 1); }\n'),
    ("g4-byte-oob", 'fn main(): int { let s: str = "ABC"; return unwrap_or(byte_at(s, 3), 0 - 1); }\n'),
    ("g4-byte-oob-big", 'fn main(): int { let s: str = "ABC"; return unwrap_or(byte_at(s, 99), 41); }\n'),
    ("g4-byte-neg", 'fn main(): int { let s: str = "ABC"; return unwrap_or(byte_at(s, 0 - 1), 0 - 2); }\n'),
    ("g4-byte-empty", 'fn main(): int { let s: str = ""; return unwrap_or(byte_at(s, 0), 77); }\n'),
    ("g4-byte-dyn", 'fn main(): int { let s: str = "ABC"; let i: int = 1 + 1; return unwrap_or(byte_at(s, i), 0 - 1); }\n'),
    ("g4-byte-dyn-oob", 'fn main(): int { let s: str = "ABC"; let i: int = 1 + 1 + 1; return unwrap_or(byte_at(s, i), 43); }\n'),
    ("g4-byte-let", 'fn main(): int { let s: str = "ABC"; let b: int = unwrap_or(byte_at(s, 0), 0 - 1); return b; }\n'),
    ("g4-byte-let-err", 'fn main(): int { let s: str = "ABC"; let b: int = unwrap_or(byte_at(s, 9), 42); return b; }\n'),
    ("g4-byte-let-use", 'fn main(): int { let s: str = "ABC"; let b: int = unwrap_or(byte_at(s, 2), 0); return b * 2; }\n'),
    ("g4-byte-param", 'fn gb(src: str, i: int): int { return unwrap_or(byte_at(src, i), 0 - 1); }\nfn main(): int { return gb("ABC", 1); }\n'),
    ("g4-byte-param-oob", 'fn gb(src: str, i: int): int { return unwrap_or(byte_at(src, i), 44); }\nfn main(): int { return gb("ABC", 9); }\n'),
    ("g4-byte-param-expr", 'fn gb(src: str, i: int): int { return unwrap_or(byte_at(src, i + 1), 0 - 1); }\nfn main(): int { return gb("ABC", 0); }\n'),
    ("g4-byte-defexpr", 'fn main(): int { let s: str = "ABC"; let d: int = 40 + 2; return unwrap_or(byte_at(s, 9), d); }\n'),
    ("g4-byte-defvar", 'fn main(): int { let s: str = "ABC"; let d: int = 7; return unwrap_or(byte_at(s, 1), d); }\n'),
    ("g4-byte-two-lets", 'fn main(): int { let s: str = "Az"; let a: int = unwrap_or(byte_at(s, 0), 0); let b: int = unwrap_or(byte_at(s, 1), 0); return a + b * 100; }\n'),
    ("g4-len-plus-byte-let", 'fn main(): int { let s: str = "ABC"; let b: int = unwrap_or(byte_at(s, 0), 0); return len(s) + b; }\n'),
    ("g4-str-payload", 'fn main(): int { let s: str = "hi"; return unwrap_or(byte_at(s, 0), 7); }\n'),
    ("g4-byte-nonzero-def", 'fn main(): int { let s: str = "q"; return unwrap_or(byte_at(s, 5), 200); }\n'),
    ("g4-len-escape", 'fn main(): int { let s: str = "a\\nb"; return len(s); }\n'),
]

REJECT25_CASES = [
    ("standalone-stmt", 'fn main(): int { let s: str = "ab"; byte_at(s, 0); return 1; }\n'),
    ("standalone-let-status", 'fn main(): int { let s: str = "ab"; let b: status<int> = byte_at(s, 0); return unwrap_or(b, 1); }\n'),
    ("lit-scrutinee-ret", 'fn main(): int { return unwrap_or(byte_at("hi", 0), 7); }\n'),
    ("lit-scrutinee-let", 'fn main(): int { let b: int = unwrap_or(byte_at("ab", 0), 7); return b; }\n'),
]


class G4AcceptTests(unittest.TestCase):
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
        self.assertEqual(by_name["g4-len-basic"], 3)
        self.assertEqual(by_name["g4-len-empty"], 0)
        self.assertEqual(by_name["g4-len-long"], 22)
        self.assertEqual(by_name["g4-len-param"], 4)
        self.assertEqual(by_name["g4-len-arith"], 11)
        self.assertEqual(by_name["g4-byte-first"], 65)
        self.assertEqual(by_name["g4-byte-mid"], 66)
        self.assertEqual(by_name["g4-byte-last"], 67)
        self.assertEqual(by_name["g4-byte-oob"], 0xFFFFFFFF)
        self.assertEqual(by_name["g4-byte-neg"], 0xFFFFFFFE)
        self.assertEqual(by_name["g4-byte-empty"], 77)
        self.assertEqual(by_name["g4-byte-dyn"], 67)
        self.assertEqual(by_name["g4-byte-let"], 65)
        self.assertEqual(by_name["g4-byte-let-err"], 42)
        self.assertEqual(by_name["g4-byte-two-lets"], 65 + 122 * 100)
        self.assertEqual(by_name["g4-str-payload"], 104)
        self.assertEqual(by_name["g4-len-escape"], 3)

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


class G4RejectTests(unittest.TestCase):
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


class G4MutantTests(unittest.TestCase):
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

    def test_g4_m1_len_reads_ptr_word(self):
        combo = _mut(_combo_text(),
                     "fn be_s_len_str(src: str, f: int, fs: int, fe: int, t: Tok, v: VS, cp: Tok): BZ {\n  let base: int = be_home_base(src, f, fs, fe, v);\n  return BZ(p: cp->p, n: sz_mov_rax_home(base + 1), c: 0, o: 0);",
                     "fn be_s_len_str(src: str, f: int, fs: int, fe: int, t: Tok, v: VS, cp: Tok): BZ {\n  let base: int = be_home_base(src, f, fs, fe, v);\n  return BZ(p: cp->p, n: sz_mov_rax_home(base), c: 0, o: 0);")
        combo = _mut(combo,
                     "fn be_e_len_str(src: str, f: int, fs: int, fe: int, v: VS, acc: int, pos: int): BZ {\n  let base: int = be_home_base(src, f, fs, fe, v);\n  return BZ(p: pos, n: e_mov_rax_home(base + 1, acc), c: 0, o: 0);",
                     "fn be_e_len_str(src: str, f: int, fs: int, fe: int, v: VS, acc: int, pos: int): BZ {\n  let base: int = be_home_base(src, f, fs, fe, v);\n  return BZ(p: pos, n: e_mov_rax_home(base, acc), c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("g4-len-basic")))

    def test_g4_m2_pre_drops_len_reload(self):
        combo = _mut(_combo_text(),
                     "  let a7: int = e_mov_rax_home(base + 1, a6);",
                     "  let a7: int = a6;")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-oob")))

    def test_g4_m3_pre_drops_rcx_move(self):
        combo = _mut(_combo_text(),
                     "  let a8: int = e_mov_rcx_rax(a7);",
                     "  let a8: int = a7;")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-oob")))

    def test_g4_m4_js_targets_jae(self):
        combo = _mut(_combo_text(),
                     "  let jsd: int = be_rel32(errstart, acc + sz_push_rax() + sz_pop_rcx() + sz_pop_rax() + sz_push_rcx() + sz_test_rax(), sz_js());",
                     "  let jsd: int = be_rel32(errstart, acc + pre - sz_jae(), sz_jae());")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-neg")))

    def test_g4_m5_jae_short(self):
        combo = _mut(_combo_text(),
                     "  let jaed: int = be_rel32(errstart, acc + pre - sz_jae(), sz_jae());",
                     "  let jaed: int = be_rel32(errstart, acc + pre, sz_jae());")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-oob")))

    def test_g4_m6_jmp_lands_in_ok(self):
        combo = _mut(_combo_text(),
                     "  let jaed: int = be_rel32(errstart, acc + pre - sz_jae(), sz_jae());\n  let jmpd: int = be_rel32(done, errstart - sz_jmp(), sz_jmp());",
                     "  let jaed: int = be_rel32(errstart, acc + pre - sz_jae(), sz_jae());\n  let jmpd: int = be_rel32(errstart, errstart - sz_jmp(), sz_jmp());")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-mid")))

    def test_g4_m7_ok_drops_ptr_load(self):
        combo = _mut(_combo_text(),
                     "fn be_e_byteat_ok_full(base: int, ds: int, acc: int): int {\n  let a0: int = e_push_rax(acc);\n  let a1: int = e_mov_rax_home(base, a0);",
                     "fn be_e_byteat_ok_full(base: int, ds: int, acc: int): int {\n  let a0: int = e_push_rax(acc);\n  let a1: int = a0;")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-mid")))

    def test_g4_m8_ok_drops_movzx(self):
        combo = _mut(_combo_text(),
                     "  let a5: int = e_movzx_eax_sib(a4);",
                     "  let a5: int = a4;")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-mid")))

    def test_g4_m9_ok_drops_pop_rsi(self):
        combo = _mut(_combo_text(),
                     "  let a3: int = e_pop_rsi(a2);",
                     "  let a3: int = a2;")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-mid")))

    def test_g4_m10_size_drops_pre_push(self):
        combo = _mut(_combo_text(),
                     "  return sz_push_rax() + sz_pop_rcx() + sz_pop_rax() + sz_push_rcx() + sz_test_rax() + sz_js() + sz_push_rax() + sz_mov_rax_home(base + 1) + sz_mov_rcx_rax() + sz_pop_rax() + sz_cmp_rax_rcx() + sz_jae();",
                     "  return sz_pop_rcx() + sz_pop_rax() + sz_push_rcx() + sz_test_rax() + sz_js() + sz_push_rax() + sz_mov_rax_home(base + 1) + sz_mov_rcx_rax() + sz_pop_rax() + sz_cmp_rax_rcx() + sz_jae();")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-mid")))

    def test_g4_m11_let_store_slides(self):
        combo = _mut(_combo_text(),
                     "  return e_mov_home_rax(ds, a6);",
                     "  return e_mov_home_rax(ds + 1, a6);")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-let")))

    def test_g4_m12_err_drops_pop(self):
        combo = _mut(_combo_text(),
                     "fn be_e_byteat_err_full(ds: int, acc: int): int {\n  let a0: int = e_pop_rax(acc);",
                     "fn be_e_byteat_err_full(ds: int, acc: int): int {\n  let a0: int = acc;")
        self.assertTrue(self._red_on(combo, self._idx("g4-byte-oob")))


if __name__ == "__main__":
    unittest.main()
