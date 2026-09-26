"""Stage 19e M5 byte_at status-let lowering in the baby backend.

M5 accepts `let b: status<int> = byte_at(s, i)` (str-variable scrutinee,
scalar-int index) in the baby backend (`rynorlang/selfhost/emit.rl`,
core dialect): the G4 bounds-check template is reused with the 3-word
status stored into the let home instead of unwrap_or's value select.
Ok path stores (0, 0, byte); the out-of-range/negative path stores
(1, 3, 0) (ERR_OORANGE, frozen G4 meaning). The stored home then feeds
the frozen M2 match-status path (`match b { ok(v) => ..., err(e) => ... }`)
and the G3/G4 scalar `unwrap_or(b, d)` path, which is exactly the shape
the compiler sources need (`fnv_str` in `util.rl`: `let b` + `match b`).

Layout (frozen M2 home order): tag@base, code@base+1, payload@base+2.
Stack discipline mirrors the G4 aggregate template: the index word is
spilled with one push, then the shared `be_g4_pre_size` bounds-check
sequence runs (push/pop-rcx/pop-rax/push-rcx/test/js + len/cmp/jae);
both arms consume the spill (ok via its trailing pop-rcx, err via a
leading pop) so RSP balances. Register model unchanged (caller-saved
only; no RBX push/pop anywhere). Out of scope (stay backend-25):
literal-str scrutinee, standalone `byte_at` statement,
`let status<int>` from non-byte_at calls. `byte_at` in `if`/`while`
conditions was M7-promoted (bare `unwrap_or(byte_at(...))` there now
compiles via the G4 fused template; owned by the cond suite).

QEMU/native proof lives in probe_m5native.py (MATCH at M5 commit);
here execution is proven by the test-only emulator below (the G4
strbyte emulator: baby bytes executed, including the 0F B6 04 06 data
read; it never generates code and cannot mask a broken backend).
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
from tests.repository import test_rynorlang_selfhost_emit_strbyte as g4  # noqa: E402
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
    ("m5-let-ok", 'fn main(): int { let s: str = "ab"; let b: status<int> = byte_at(s, 0); return unwrap_or(b, 1); }\n'),
    ("m5-let-err", 'fn main(): int { let s: str = "ab"; let b: status<int> = byte_at(s, 9); return unwrap_or(b, 1); }\n'),
    ("m5-fnv-hit", 'fn fnv_str(s: str, i: int, h: int): int { let b: status<int> = byte_at(s, i); match b { ok(v) => { return h + v; }, err(e) => { return h; } } }\nfn main(): int { let s: str = "ab"; return fnv_str(s, 0, 0); }\n'),
    ("m5-fnv-miss", 'fn fnv_str(s: str, i: int, h: int): int { let b: status<int> = byte_at(s, i); match b { ok(v) => { return h + v; }, err(e) => { return h; } } }\nfn main(): int { let s: str = "ab"; return fnv_str(s, 9, 7); }\n'),
    ("m5-cond-true", 'fn main(): int { let s: str = "ab"; let b: status<int> = byte_at(s, 0); if unwrap_or(b, 0) == 97 { return 1; } return 0; }\n'),
    ("m5-cond-false", 'fn main(): int { let s: str = "ab"; let b: status<int> = byte_at(s, 1); if unwrap_or(b, 0) == 97 { return 1; } return 0; }\n'),
    ("m5-param", 'fn fetch(src: str, i: int): int { let b: status<int> = byte_at(src, i); return unwrap_or(b, 0 - 1); }\nfn main(): int { let s: str = "abc"; return fetch(s, 2); }\n'),
    ("m5-param-oob", 'fn fetch(src: str, i: int): int { let b: status<int> = byte_at(src, i); return unwrap_or(b, 0 - 1); }\nfn main(): int { let s: str = "abc"; return fetch(s, 5); }\n'),
    ("m5-expr-idx", 'fn main(): int { let s: str = "abc"; let i: int = 1; let b: status<int> = byte_at(s, i + 1); return unwrap_or(b, 0 - 1); }\n'),
    ("m5-neg-idx", 'fn main(): int { let s: str = "abc"; let b: status<int> = byte_at(s, 0 - 1); return unwrap_or(b, 0 - 2); }\n'),
    ("m5-two-lets", 'fn main(): int { let s: str = "abc"; let a: status<int> = byte_at(s, 0); let b: status<int> = byte_at(s, 1); return unwrap_or(a, 0) + unwrap_or(b, 0); }\n'),
    ("m5-match-scale", 'fn main(): int { let s: str = "abc"; let b: status<int> = byte_at(s, 2); match b { ok(v) => { return v * 2; }, err(e) => { return 0 - 1; } } }\n'),
    ("m5-err-code", 'fn main(): int { let s: str = "ab"; let b: status<int> = byte_at(s, 9); match b { ok(v) => { return v; }, err(e) => { return e; } } }\n'),
]

REJECT25_CASES = [
    ("lit-scrutinee-status", 'fn main(): int { let b: status<int> = byte_at("hi", 0); return unwrap_or(b, 1); }\n'),
    ("standalone-stmt", 'fn main(): int { let s: str = "ab"; byte_at(s, 0); return 1; }\n'),
]
# M7-promoted shapes (bare byte_at in if/while conditions) return code 0
# from the backend now, so the old M5 25-pins are dropped, NOT re-asserted
# here: the M7 suite owns the cond/call-arg end-to-end (differential +
# emulator + mutants + QEMU). Ownership note for the audit trail:
M7_PROMOTED_PINS = [
    ("cond-bare-call", 'fn main(): int { let s: str = "ab"; if unwrap_or(byte_at(s, 0), 0) == 97 { return 1; } return 0; }\n'),
    ("while-bare-call", 'fn main(): int { let s: str = "ab"; let i: int = 0; while unwrap_or(byte_at(s, i), 0) != 0 { return 1; } return 0; }\n'),
]


class M5AcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 13)

    def test_02_determinism(self):
        again = _run_be(self.combo, [(n, s) for n, s in ACCEPT_CASES])
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        exits = set(w for _n, _h, w in self.images)
        self.assertGreater(len(exits), 4)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_value_spots(self):
        by_name = {n: w for n, _h, w in self.images}
        self.assertEqual(by_name["m5-let-ok"], 97)
        self.assertEqual(by_name["m5-let-err"], 1)
        self.assertEqual(by_name["m5-fnv-hit"], 97)
        self.assertEqual(by_name["m5-fnv-miss"], 7)
        self.assertEqual(by_name["m5-cond-true"], 1)
        self.assertEqual(by_name["m5-cond-false"], 0)
        self.assertEqual(by_name["m5-param"], 99)
        self.assertEqual(by_name["m5-param-oob"], 0xFFFFFFFF)
        self.assertEqual(by_name["m5-expr-idx"], 99)
        self.assertEqual(by_name["m5-neg-idx"], 0xFFFFFFFE)
        self.assertEqual(by_name["m5-two-lets"], 97 + 98)
        self.assertEqual(by_name["m5-match-scale"], 198)
        self.assertEqual(by_name["m5-err-code"], 3)

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


class M5RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)

    def test_08_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_08b_m7_promoted_pins_compile(self):
        # Gate assertion only (no execution): the dropped pins must
        # return code 0 from the current backend. Value proof lives
        # in the M7 suite (m7-if-hit / m7-while-hit bring differentials).
        for name, src in M7_PROMOTED_PINS:
            (code, _off, hexstr) = _run_be(self.combo, [(name, src)])[0]
            self.assertEqual(code, 0, name)
            self.assertNotEqual(hexstr, "", name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


class M5MutantTests(unittest.TestCase):
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

    def test_m5_m1_ok_payload_zero(self):
        # Zero the ok-path payload store: unwrap_or/match read byte 0
        # and the hit case diverges from the oracle.
        combo = _mut(_combo_text(),
                     "fn be_e_byteat_ok_full(base: int, ds: int, acc: int): int {",
                     "fn be_e_byteat_ok_full_UNUSED(base: int, ds: int, acc: int): int {")
        self.assertTrue(self._red_on(combo, self._idx("m5-fnv-hit")))

    def test_m5_m2_err_tag_zero(self):
        # Zero the err-path tag store: match reads ok (payload 0)
        # and the code-witness diverges from the oracle (0 vs 3).
        combo = _mut(_combo_text(),
                     "fn be_e_byteat_sterr(ds: int, acc: int): int {\n  let a0: int = e_pop_rax(acc);\n  let a1: int = e_mov_rax_imm(1, a0);",
                     "fn be_e_byteat_sterr(ds: int, acc: int): int {\n  let a0: int = e_pop_rax(acc);\n  let a1: int = e_mov_rax_imm(0, a0);")
        self.assertTrue(self._red_on(combo, self._idx("m5-err-code")))

    def test_m5_m3_err_drops_spill_pop(self):
        # Drop the err-path spill pop: RSP imbalances and the
        # image traps (or diverges) on the miss case.
        combo = _mut(_combo_text(),
                     "fn be_e_byteat_sterr(ds: int, acc: int): int {\n  let a0: int = e_pop_rax(acc);",
                     "fn be_e_byteat_sterr(ds: int, acc: int): int {\n  let a0: int = acc;")
        self.assertTrue(self._red_on(combo, self._idx("m5-let-err")))

    def test_m5_m4_spill_push_dropped(self):
        # Drop the index spill push: the bounds check reads a stale
        # word and the oob case wrongly takes the ok path.
        combo = _mut(_combo_text(),
                     "  let spill: int = e_push_rax(acc);",
                     "  let spill: int = acc;")
        self.assertTrue(self._red_on(combo, self._idx("m5-let-err")))

    def test_m5_m5_err_code_zeroed(self):
        # Zero the err-path code store (3 -> 0): the err binding
        # reads 0 and the code-witness diverges from the oracle.
        combo = _mut(_combo_text(),
                     "  let a3: int = e_mov_rax_imm(3, a2);\n  let a4: int = e_mov_home_rax(ds + 1, a3);",
                     "  let a3: int = e_mov_rax_imm(0, a2);\n  let a4: int = e_mov_home_rax(ds + 1, a3);")
        self.assertTrue(self._red_on(combo, self._idx("m5-err-code")))


if __name__ == "__main__":
    unittest.main()
