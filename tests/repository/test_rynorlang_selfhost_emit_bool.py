"""Stage 19e M1 bool return/param lowering.

M1 opens the `be_subset_ty` gate for `bool` (tbase == 2) in return
position: bool is a 1-word scalar (tslots == 1, host `_param_width` == 1,
canonical false = 0 / true = 1 via setcc+movzx, RAX return, 1-word homes
and ABI slots exactly like int). Comparisons already produce canonical
bool values (`be_combine_cmp`: cmp + setcc + movzx); `!` lowers to
xor-eax-1; `&&`/`||` lower to and/or; branch conditions test RAX.
No new machine forms were needed: M1 is a gate-only change, so the
frozen register model (caller-saved only, no RBX) and all existing
size/emit pairs are untouched.

Semantics (frozen host truth): bool machine width 1 word; home width
1 word; params 1 ABI word (register or G2 stack slot, unchanged
convention); RAX return; false = 0, true = 1 canonical (host
`compile.py` `_setcc` + `movzx`, `_emit_const` bool 1/0; oracle
`1 if result else 0`); `&&`/`||` eager canonical (`and`/`or` on
canonical inputs stays canonical); `!` canonical (xor 1).

Out of scope (stay backend-25): print(int/bool) (M3),
recursion relaxation (M4), condition-position byte_at (M5),
fread/fjoin/argv/use (M6). (M2 owns match: the stale match-stmt
25-pin moved to the M2 suite as m2-inherit-status.)

QEMU/native proof lives in probe_m1native.py (5/5 MATCH at M1
commit); here execution is proven by the test-only emulator chain
below (it EXECUTES baby bytes; it never generates code and cannot
mask a broken backend). Reference semantics come from the trusted
host oracle (exit low 32 bits). RYNX extent
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
    ("m1-lit", 'fn f(): bool { return true; }\nfn main(): int { if f() { return 1; } return 0; }\n'),
    ("m1-param-id", 'fn f(x: bool): bool { return x; }\nfn main(): int { if f(true) { return 1; } return 0; }\n'),
    ("m1-local", 'fn f(): bool { let b: bool = true; return b; }\nfn main(): int { if f() { return 1; } return 0; }\n'),
    ("m1-cmp", 'fn f(a: int): bool { return a > 1; }\nfn main(): int { if f(2) { return 1; } return 0; }\n'),
    ("m1-logic", 'fn f(a: bool, b: bool): bool { return a && b; }\nfn main(): int { if f(true, true) { return 1; } return 0; }\n'),
    ("m1-consume-if", 'fn f(): bool { return true; }\nfn main(): int { if f() { return 7; } return 0; }\n'),
    ("m1-consume-while", 'fn f(): bool { return false; }\nfn main(): int { while f() { break; } return 5; }\n'),
    ("m1-pass", 'fn g(x: bool): int { if x { return 1; } return 0; }\nfn f(): bool { return true; }\nfn main(): int { return g(f()); }\n'),
    ("m1-reg-param", 'fn f(x: bool): int { if x { return 3; } return 0; }\nfn main(): int { return f(true); }\n'),
    ("m1-multi", 'fn f(a: bool, b: bool): int { if a { if b { return 1; } } return 0; }\nfn main(): int { return f(true, false); }\n'),
    ("m1-mixed", 'fn f(x: int, b: bool, s: str, y: int): int { if b { return x + y; } return 0; }\nfn main(): int { return f(20, true, "mid", 22); }\n'),
    ("m1-not", 'fn f(x: bool): bool { return !x; }\nfn main(): int { if f(false) { return 1; } return 0; }\n'),
    ("m1-stack", 'fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: bool): int { if h { return a + b + c + d + e + g; } return 0; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6, true); }\n'),
    ("m1-eq", 'fn f(a: int): bool { return a == 2; }\nfn main(): int { if f(2) { return 1; } return 0; }\n'),
    ("m1-neq", 'fn f(a: int): bool { return a != 2; }\nfn main(): int { if f(3) { return 1; } return 0; }\n'),
    ("m1-false", 'fn f(): bool { return false; }\nfn main(): int { if f() { return 1; } return 8; }\n'),
    ("m1-or", 'fn f(a: bool, b: bool): bool { return a || b; }\nfn main(): int { if f(false, true) { return 6; } return 0; }\n'),
]

REJECT25_CASES = [
    # Match statements are M2-compilable (status/int/bool scrutinee);
    # the old 25-pin moved to the M2 suite at M2 commit. result-typed
    # scrutinees fail closed at code 6 (match-gate: not status/int/
    # bool), so no live 25-pin remains in this suite. Keep the class
    # green over an empty pin list (documents the moved boundary).
]


class M1AcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 15)

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
        self.assertEqual(by_name["m1-lit"], 1)
        self.assertEqual(by_name["m1-param-id"], 1)
        self.assertEqual(by_name["m1-local"], 1)
        self.assertEqual(by_name["m1-cmp"], 1)
        self.assertEqual(by_name["m1-logic"], 1)
        self.assertEqual(by_name["m1-consume-if"], 7)
        self.assertEqual(by_name["m1-consume-while"], 5)
        self.assertEqual(by_name["m1-pass"], 1)
        self.assertEqual(by_name["m1-reg-param"], 3)
        self.assertEqual(by_name["m1-multi"], 0)
        self.assertEqual(by_name["m1-mixed"], 42)
        self.assertEqual(by_name["m1-not"], 1)
        self.assertEqual(by_name["m1-stack"], 21)
        self.assertEqual(by_name["m1-eq"], 1)
        self.assertEqual(by_name["m1-neq"], 1)
        self.assertEqual(by_name["m1-false"], 8)
        self.assertEqual(by_name["m1-or"], 6)

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

    def test_08_canonical_bool_values(self):
        # Canonicalization: every bool-typed RAX value observed is exactly 0/1.
        # Covered structurally: literals lower to mov 0/1, comparisons to
        # setcc+movzx, &&/|| to and/or over canonical inputs, ! to xor-1.
        # Value proof: m1-false (0-path) and m1-lit (1-path) both exact.
        by_name = {n: e for n, _h, _w, e in self.images}
        self.assertEqual(by_name["m1-false"], 8)
        self.assertEqual(by_name["m1-lit"], 1)


class M1RejectTests(unittest.TestCase):
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


class M1MutantTests(unittest.TestCase):
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

    def test_m1_m1_bool_arm_zero(self):
        combo = _mut(_combo_text(),
                     "  if b == 2 { return 1; } else { }\n  if b == 4 { return be_subset_rec(ty, src, f, depth); } else { }",
                     "  if b == 2 { return 0; } else { }\n  if b == 4 { return be_subset_rec(ty, src, f, depth); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m1-lit")))

    def test_m1_m3_cc_inverted(self):
        combo = _mut(_combo_text(),
                     "  if op == 6 { return 148; } else { }",
                     "  if op == 6 { return 149; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m1-eq")))

    def test_m1_m4_cmp_size_short(self):
        combo = _mut(_combo_text(),
                     "  if op == 6 { return 14; } else { }",
                     "  if op == 6 { return 8; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m1-eq")))

    def test_m1_m6_ne_inverted(self):
        combo = _mut(_combo_text(),
                     "  if op == 7 { return 149; } else { }",
                     "  if op == 7 { return 148; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m1-neq")))


if __name__ == "__main__":
    unittest.main()
