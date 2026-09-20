"""Stage 19e M4 direct self-call (recursion) lowering.

M4 resolves direct self-calls in the baby backend
(`rynorlang/selfhost/emit.rl`, core dialect): a call whose name
matches the enclosing function resolves to the enclosing function's
own index (backward-scan `be_find_hit` previously rejected any hit
at/after the enclosing span start with -1/25). The E8 relative
displacement machinery (`be_fn_codeoff` + `dp = 28 + co - ...`) is
unchanged: self-offset math holds for ci == own index exactly like
any backward callee. Forward references and mutual recursion stay
backend-25 (single-pass codeoff: the callee size is unknown at the
call site); the checker already rejects undeclared callees (13).

Semantics (frozen host truth): native x86-64 CALL pushes the return
address and jumps; each invocation gets a fresh frame (`push rbp /
mov rbp,rsp / sub rsp,frame`); args marshal per call; RAX returns.
Unbounded self-call without a base case diverges (host oracle traps
on depth; the guest would fault on stack exhaustion): only
base-cased recursion is in the accept corpus.

QEMU/native proof lives in probe_m4native.py (MATCH at M4 commit);
here execution is proven by the test-only emulator below (the BE-B
emulator plus the M1 branch forms; E8 is already executed).
Reference semantics come from the trusted host oracle (exit low 32
bits). RYNX extent (`len(raw) == 28 + csz + fsz`) is asserted per
case.
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit_calls as beb  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402


MASK64 = bea.MASK64

Emu = beb.Emu


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


def _oracle_exit(src):
    return bea._oracle_exit(src)


def _parse_rnyx(raw):
    return bea._parse_rnyx(raw)


def run_image(code: bytes):
    return Emu(code).run()


ACCEPT_CASES = [
    ("m4-base", 'fn foo(x: int): int { if x == 0 { return 0; } return foo(x - 1); }\nfn main(): int { return foo(3); }\n'),
    ("m4-fact", 'fn fact(x: int): int { if x == 0 { return 1; } return x * fact(x - 1); }\nfn main(): int { return fact(5); }\n'),
    ("m4-fib", 'fn fib(x: int): int { if x == 0 { return 0; } if x == 1 { return 1; } return fib(x - 1) + fib(x - 2); }\nfn main(): int { return fib(6); }\n'),
    ("m4-sum", 'fn sum(x: int): int { if x == 0 { return 0; } return x + sum(x - 1); }\nfn main(): int { return sum(10); }\n'),
    ("m4-armed", 'fn foo(x: int): int { if x == 0 { return 7; } return foo(x - 1); }\nfn main(): int { return foo(4); }\n'),
    ("m4-letbind", 'fn foo(x: int): int { if x == 0 { return 0; } let y: int = foo(x - 1); return y; }\nfn main(): int { return foo(2); }\n'),
    ("m4-deep", 'fn foo(x: int): int { if x == 0 { return 0; } return foo(x - 1); }\nfn main(): int { return foo(50); }\n'),
    ("m4-backward-then-self", 'fn h(x: int): int { return x + 1; }\nfn foo(x: int): int { if x == 0 { return h(0); } return foo(x - 1); }\nfn main(): int { return foo(2); }\n'),
]

REJECT25_CASES = [
    # Undeclared callee: the checker resolves through pgm_check first
    # (13 unknown-function), so the backend 25 for unknown names is
    # unreachable from well-formed drivers. Kept as a backend-level
    # pin via the direct be_main path is impossible here (the driver
    # runs pgm_check first); document with an empty pin list.
]

REJECT_CHECK_CASES = [
    # Forward reference: the HOST checker collects all functions
    # first (ok), but the BABY checker resolves callees backward-only
    # from the use site (def-before-use, frozen host rule for the
    # baby), so a callee declared after the caller fails closed at 13
    # (unknown-function) before the backend ever sees it. The test
    # asserts the BABY code (13) against the HOST diagnostic code:
    # they share the SEM_UNKNOWN_FUNCTION code string; the host
    # itself accepts (global collection) while the baby rejects.
    # Host-ok is asserted implicitly: _oracle_exit is never called
    # for reject cases (only _run_be + host diagnostic-code match).
    ("fwd-ref", 'fn a(x: int): int { return b(x); }\nfn b(x: int): int { return x; }\nfn main(): int { return a(1); }\n', "SEM_UNKNOWN_FUNCTION", True),
    # Mutual recursion: same rule in both directions (a sees no b,
    # b sees no a at their respective use sites).
    ("mutual", 'fn a(x: int): int { return b(x); }\nfn b(x: int): int { return a(x); }\nfn main(): int { return a(1); }\n', "SEM_UNKNOWN_FUNCTION", True),
]


class M4AcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, [(n, s) for n, s in ACCEPT_CASES])
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
        self.assertGreaterEqual(len(self.images), 8)

    def test_02_determinism(self):
        again = _run_be(self.combo, [(n, s) for n, s in ACCEPT_CASES])
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _s, _w in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        exits = set(w for _n, _h, _s, w in self.images)
        self.assertGreater(len(exits), 4)

    def test_04_code_sizes_bounded(self):
        for name, _h, sz, _w in self.images:
            self.assertGreaterEqual(sz, 1)
            self.assertLessEqual(sz, 65536)

    def test_05_value_spots(self):
        by_name = {n: w for n, _h, _s, w in self.images}
        self.assertEqual(by_name["m4-base"], 0)
        self.assertEqual(by_name["m4-fact"], 120)
        self.assertEqual(by_name["m4-fib"], 8)
        self.assertEqual(by_name["m4-sum"], 55)
        self.assertEqual(by_name["m4-armed"], 7)
        self.assertEqual(by_name["m4-letbind"], 0)
        self.assertEqual(by_name["m4-deep"], 0)
        self.assertEqual(by_name["m4-backward-then-self"], 1)

    def test_06_no_callee_saved_scratch(self):
        for name, hexstr, _s, _w in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            for i, b in enumerate(code):
                self.assertNotIn(b, (0x53, 0x5B), f"rbx push/pop in {name} at {i:#x}")

    def test_07_rnyx_extent_exact(self):
        for name, hexstr, _s, _w in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertEqual(fsz, msz, name)
            self.assertEqual(len(raw), 28 + csz + fsz, name)

    def test_08_e8_targets_fn_entry(self):
        # Branch audit: every E8 displacement lands on a function
        # entry (0x55 push rbp prologue). Self-call targets equal the
        # caller's own entry (probe_m4e8 shows E8 -> code+14 for the
        # first fn); every E8 target decoding to 0x55 proves all
        # calls (self + backward + start-stub) land on entries.
        for (name, src), (_code, _off, hexstr) in zip(ACCEPT_CASES, self.results):
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, _fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            for i in range(len(code) - 5):
                if code[i] == 0xE8:
                    disp = int.from_bytes(code[i + 1:i + 5], "little", signed=True)
                    target = i + 5 + disp
                    self.assertGreaterEqual(target, 0, f"{name} E8@{i}")
                    self.assertLess(target, len(code), f"{name} E8@{i}")
                    self.assertEqual(code[target], 0x55, f"{name} E8@{i}->{target}")


class M4RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, [(n, s) for n, s, _c, _h in REJECT_CHECK_CASES])

    def test_09_unsupported_subset(self):
        # No live backend-25 pins: self-calls compile (accepts), and
        # forward/mutual references fail at the checker (13) before
        # the backend. Empty pin list documents the moved boundary.
        self.assertEqual(REJECT25_CASES, [])

    def test_10_checker_passthrough(self):
        want_code = {"SEM_UNKNOWN_FUNCTION": 13}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src, want, host_ok = case[0], case[1], case[2], case[3]
            # Baby code matches the shared diagnostic code string.
            self.assertEqual(code, want_code[want], name)
            self.assertEqual(hexstr, "", name)
            # Host accepts (global collection) exactly when flagged.
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertEqual(hr.ok, host_ok, name)
            if not host_ok:
                self.assertEqual(hr.diagnostic.code, want, name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


class M4MutantTests(unittest.TestCase):
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

    def _idx(self, name):
        for i, (n, _s) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_m4_m1_self_resolve_off(self):
        # Break self-resolution: the enclosing-name match returns -1,
        # so self-calls fail closed at 25.
        combo = _mut(_combo_text(),
                     "  if nm->l == nl { if beq(src, nm->s, src, ns, nl) { return be_fn_selfidx(src, f, fs); } else { } } else { }",
                     "  if nm->l == nl { if beq(src, nm->s, src, ns, nl) { return 0 - 1; } else { } } else { }")
        self.assertTrue(self._red_on(combo, self._idx("m4-fact")))

    def test_m4_m2_self_idx_zero(self):
        # Resolve self to fn 0 instead of self: fact calls the wrong
        # entry (main-start region), diverging from the oracle.
        combo = _mut(_combo_text(),
                     "fn be_fn_selfidx(src: str, f: int, fs: int): int {",
                     "fn be_fn_selfidx_UNUSED(src: str, f: int, fs: int): int {")
        self.assertTrue(self._red_on(combo, self._idx("m4-fact")))

    def test_m4_m3_call_disp_zero(self):
        # Zero the E8 displacement: the call lands mid-prologue and
        # the image diverges from the oracle (or traps).
        combo = _mut(_combo_text(),
                     "  let dp: int = 28 + co - (q0 + sz_call_op());",
                     "  let dp: int = 0;")
        self.assertTrue(self._red_on(combo, self._idx("m4-fact")))

    def test_m4_m4_call_disp_off_by_one(self):
        # Off-by-one displacement: lands one byte off the entry.
        combo = _mut(_combo_text(),
                     "  let dp: int = 28 + co - (q0 + sz_call_op());",
                     "  let dp: int = 29 + co - (q0 + sz_call_op());")
        self.assertTrue(self._red_on(combo, self._idx("m4-fact")))


if __name__ == "__main__":
    unittest.main()
