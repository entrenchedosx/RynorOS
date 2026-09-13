"""Stage 19e BE-E record aggregate lowering: nominal layout, construction,
field access, record parameters, record returns (sret), and nested calls.

Layout (frozen 19a): declaration order, padding-free, 8-byte slots
(int/bool 1 slot); value copies everywhere; nominal identity by
declaration ordinal. Calls use by-value SysV slots (register-only,
total 6 or fewer slots); aggregate returns always use a caller sret
slot; frames reserve one unit-max-width temp area for call staging.
`==` on records stays backend-25 (byte-compare deferred); field
access on call temporaries (`f().x`) and paren bases stays 25.

QEMU/native proof lives in tests/integration/test_native_backend.py;
here execution is proven by the test-only emulator below (BE-D
emulator plus exactly the aggregate forms the backend emits: lea
rax,[rbp+disp32], mov [rdi+disp], mov rdi,[rbp+16], add rsp,imm32).
Reference semantics come from the trusted host oracle (exit low 32
bits, EBX width).
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit_data as bed  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


MASK64 = bea.MASK64


class Emu(bed.Emu):
    def _rex(self):
        off = self.rip - bea.CODE_BASE
        b1 = self.code[off]
        R = self.reg
        if b1 == 0x8D:
            self._fetch(1)
            b2 = self._fetch(1)[0]
            assert b2 == 0x85, f"bad lea {b2:#x}"
            d = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rax"] = (R["rbp"] + d) & MASK64
        elif b1 == 0x89 and self.code[off + 1] in (0x47, 0x87):
            self._fetch(1)
            b2 = self._fetch(1)[0]
            if b2 == 0x47:
                d = self._fetch(1)[0]
                d = d - 256 if d >= 128 else d
            else:
                d = int.from_bytes(self._fetch(4), "little", signed=True)
            self._swrite((R["rdi"] + d) & MASK64, 8, R["rax"])
        elif b1 == 0x8B and self.code[off + 1] == 0x7D:
            self._fetch(1)
            self._fetch(1)
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            R["rdi"] = self._sread((R["rbp"] + d) & MASK64, 8)
        elif b1 == 0x81 and self.code[off + 1] == 0xC4:
            self._fetch(1)
            self._fetch(1)
            imm = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rsp"] = (R["rsp"] + imm) & MASK64
        else:
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


def _parse_rnyx(raw):
    return bea._parse_rnyx(raw)


ACCEPT_CASES = [
    ("rec-basic-a", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 10, b: 32); return p->a; }\n"),
    ("rec-basic-b", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 10, b: 32); return p->b; }\n"),
    ("rec-one", "record One { x: int }\nfn main(): int { let o: One = One(x: 5); return o->x; }\n"),
    ("rec-three-a", "record T { a: int, b: int, c: int }\nfn main(): int { let t: T = T(a: 1, b: 2, c: 3); return t->a; }\n"),
    ("rec-three-b", "record T { a: int, b: int, c: int }\nfn main(): int { let t: T = T(a: 1, b: 2, c: 3); return t->b; }\n"),
    ("rec-three-c", "record T { a: int, b: int, c: int }\nfn main(): int { let t: T = T(a: 1, b: 2, c: 3); return t->c; }\n"),
    ("rec-bool-t", "record B { t: bool, f: bool }\nfn main(): int { let b: B = B(t: true, f: false); if b->t { return 1; } else { return 0; } }\n"),
    ("rec-bool-f", "record B { t: bool, f: bool }\nfn main(): int { let b: B = B(t: true, f: false); if b->f { return 1; } else { return 0; } }\n"),
    ("rec-bool-mix", "record B { n: int, t: bool }\nfn main(): int { let b: B = B(n: 9, t: false); return b->n; }\n"),
    ("rec-empty", "record E {}\nfn main(): int { let e: E = E(); return 0; }\n"),
    ("rec-seven", "record R { a: int, b: int, c: int, d: int, e: int, f: int, g: int }\nfn main(): int { let r: R = R(a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7); return r->g; }\n"),
    ("rec-reorder", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(b: 32, a: 10); return p->a + p->b; }\n"),
    ("rec-reorder-a", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(b: 32, a: 10); return p->a; }\n"),
    ("rec-expr-init", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1 + 2, b: 3 * 4); return p->a + p->b; }\n"),
    ("rec-copy", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 4, b: 5); let q: Pair = p; return q->a * q->b; }\n"),
    ("rec-copy-both", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 4, b: 5); let q: Pair = p; return p->a + q->b; }\n"),
    ("rec-field-arith", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 6, b: 7); return p->a * p->b - p->a; }\n"),
    ("rec-nest-full", "record Inner { x: int }\nrecord Outer { a: int, inner: Inner, b: int }\nfn main(): int { let o: Outer = Outer(a: 1, inner: Inner(x: 2), b: 3); return o->a + o->inner->x + o->b; }\n"),
    ("rec-nest-first", "record Inner { x: int }\nrecord Outer { a: int, inner: Inner, b: int }\nfn main(): int { let o: Outer = Outer(a: 11, inner: Inner(x: 2), b: 3); return o->a; }\n"),
    ("rec-nest-inner", "record Inner { x: int }\nrecord Outer { a: int, inner: Inner, b: int }\nfn main(): int { let o: Outer = Outer(a: 1, inner: Inner(x: 22), b: 3); return o->inner->x; }\n"),
    ("rec-nest-last", "record Inner { x: int }\nrecord Outer { a: int, inner: Inner, b: int }\nfn main(): int { let o: Outer = Outer(a: 1, inner: Inner(x: 2), b: 33); return o->b; }\n"),
    ("rec-nest-copy", "record Inner { x: int }\nrecord Outer { a: int, inner: Inner, b: int }\nfn main(): int { let o: Outer = Outer(a: 1, inner: Inner(x: 2), b: 3); let i: Inner = o->inner; return i->x; }\n"),
    ("rec-nest-reorder", "record Inner { x: int }\nrecord Outer { a: int, inner: Inner, b: int }\nfn main(): int { let o: Outer = Outer(b: 3, inner: Inner(x: 2), a: 1); return o->a + o->inner->x + o->b; }\n"),
    ("rec-depth-3", "record I1 { x: int }\nrecord I2 { m: I1, y: int }\nrecord I3 { a: int, n: I2 }\nfn main(): int { let o: I3 = I3(a: 4, n: I2(m: I1(x: 5), y: 6)); return o->a + o->n->m->x + o->n->y; }\n"),
    ("rec-param", "record Pair { a: int, b: int }\nfn get_x(p: Pair): int { return p->a; }\nfn main(): int { return get_x(Pair(a: 7, b: 8)); }\n"),
    ("rec-param-mix", "record Pair { a: int, b: int }\nfn f(x: int, p: Pair, y: int): int { return x + p->a + p->b + y; }\nfn main(): int { return f(1, Pair(a: 2, b: 3), 4); }\n"),
    ("rec-param-two", "record A { x: int }\nrecord B { y: int }\nfn fa(a: A): int { return a->x; }\nfn fb(b: B): int { return b->y; }\nfn main(): int { return fa(A(x: 3)) + fb(B(y: 4)); }\n"),
    ("rec-param-six", "record P { a: int, b: int }\nrecord Q { c: int, d: int }\nfn f(p: P, q: Q, x: int, y: int): int { return p->a + p->b + q->c + q->d + x + y; }\nfn main(): int { return f(P(a: 1, b: 2), Q(c: 3, d: 4), 5, 6); }\n"),
    ("rec-call-multi", "record Pair { a: int, b: int }\nfn inc(p: Pair): Pair { return Pair(a: p->a + 1, b: p->b + 1); }\nfn main(): int { let q: Pair = inc(Pair(a: 1, b: 10)); return q->a + q->b; }\n"),
    ("rec-call-discarded", "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn main(): int { mk(1, 2); return 8; }\n"),
    ("rec-zero-arg-ret", "record Pair { a: int, b: int }\nfn const(): Pair { return Pair(a: 9, b: 9); }\nfn main(): int { let p: Pair = const(); return p->a; }\n"),
    ("rec-ret-ctor", "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn main(): int { let p: Pair = mk(3, 4); return p->a + p->b; }\n"),
    ("rec-ret-var", "record Pair { a: int, b: int }\nfn idp(p: Pair): Pair { return p; }\nfn main(): int { let q: Pair = idp(Pair(a: 5, b: 6)); return q->b; }\n"),
    ("rec-ret-nested", "record Inner { x: int }\nrecord Outer { a: int, inner: Inner }\nfn geti(o: Outer): Inner { return o->inner; }\nfn main(): int { let o: Outer = Outer(a: 1, inner: Inner(x: 8)); let i: Inner = geti(o); return i->x; }\n"),
    ("rec-ret-call", "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn wrap(x: int): Pair { return mk(x, x + 1); }\nfn main(): int { let p: Pair = wrap(5); return p->a + p->b; }\n"),
    ("rec-nestcall", "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn sum(p: Pair): int { return p->a + p->b; }\nfn main(): int { return sum(mk(20, 22)); }\n"),
    ("rec-nestcall-deep", "record Pair { a: int, b: int }\nfn mk(a: int): Pair { return Pair(a: a, b: a * 2); }\nfn dup(p: Pair): Pair { return Pair(a: p->a, b: p->b); }\nfn sum(p: Pair): int { return p->a + p->b; }\nfn main(): int { return sum(dup(mk(3))); }\n"),
    ("rec-ret-in-if", "record Pair { a: int, b: int }\nfn pick(c: int): Pair { if c == 1 { return Pair(a: 21, b: 0); } return Pair(a: 0, b: 22); }\nfn main(): int { let p: Pair = pick(1); let q: Pair = pick(2); return p->a + q->b; }\n"),
    ("rec-ret-in-while", "record Pair { a: int, b: int }\nfn f(): Pair { while true { return Pair(a: 3, b: 4); } }\nfn main(): int { let p: Pair = f(); return p->a + p->b; }\n"),
    ("rec-nominal-ok", "record A { x: int }\nrecord B { x: int }\nfn fa(a: A): int { return a->x; }\nfn fb(b: B): int { return b->x; }\nfn main(): int { return fa(A(x: 13)) + fb(B(x: 14)); }\n"),
    ("rec-nominal-off", "record B { x: int }\nrecord A { y: int, x: int }\nfn f(p: A): int { return p->x; }\nfn main(): int { return f(A(y: 5, x: 7)); }\n"),
    ("rec-unused", "record P { a: int }\nfn main(): int { return 0; }\n"),
    ("rec-if-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); if p->a == 1 { return 17; } else { return 93; } }\n"),
    ("rec-while-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 99, b: 0); while p->a == 0 { return 1; } return 2; }\n"),
    ("rec-break-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 0); while true { if p->a == 1 { break; } return 0; } return 7; }\n"),
    ("rec-print-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); if p->a == 1 { print(\"y\"); } return 3; }\n"),
    ("rec-tmp-field", "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn main(): int { return mk(1, 2)->a; }\n"),
    ("rec-ctor-field", "record Pair { a: int, b: int }\nfn main(): int { return Pair(a: 3, b: 4)->b; }\n"),
    ("rec-helper-calls", "record Pair { a: int, b: int }\nfn geta(p: Pair): int { return p->a; }\nfn getb(p: Pair): int { return p->b; }\nfn main(): int { let p: Pair = Pair(a: 11, b: 22); return geta(p) + getb(p); }\n"),
    ("rec-scalar-two", "record Pair { a: int, b: int }\nfn main(): int { let x: int = 5; let y: int = 7; return x + y; }\n"),
    ("rec-temp-clobber", "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn sum(p: Pair): int { return p->a + p->b; }\nfn main(): int { let z: int = 5; let s: int = sum(mk(20, 22)); return s + z; }\n"),
    ("rec-wide-nest", "record Pair { a: int, b: int }\nrecord Outer { p: Pair, b: int }\nfn main(): int { let o: Outer = Outer(p: Pair(a: 7, b: 8), b: 9); return o->b; }\n"),
    ("rec-bool-cmp", "record B { t: bool }\nfn main(): int { let b: B = B(t: (0 - 1) == 5); if b->t { return 1; } else { return 0; } }\n"),
    ("rec-16-ok", "".join("record R%d { x: int }\n" % i for i in range(16)) + "fn main(): int { let a: R0 = R0(x: 1); return a->x; }\n"),
    ("rec-bound-ok", "record Pair { a: int, b: int }\nfn main(): int { " + " ".join("let v%d: Pair = Pair(a: %d, b: %d);" % (i, i, i) for i in range(30)) + " return v29->b; }\n"),
]

REJECT25_CASES = [
    ("rec-eq", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); let q: Pair = Pair(a: 1, b: 2); if p == q { return 1; } else { return 0; } }\n"),
    ("rec-ne", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); if p != p { return 1; } else { return 0; } }\n"),
    ("str-field", "record S { s: str, n: int }\nfn main(): int { let r: S = S(s: \"hi\", n: 1); return r->n; }\n"),
    ("list-field", "record L { l: list<int,2> }\nfn main(): int { let r: L = L(l: [1, 2]); return 0; }\n"),
    ("paren-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); return (p)->a; }\n"),
]

REJECT_CHECK_CASES = [
    ("nominal-mix", "record A { x: int }\nrecord B { x: int }\nfn main(): int { let a: A = B(x: 1); return 0; }\n", "SEM_TYPE_MISMATCH"),
    ("unknown-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); return p->z; }\n", "SEM_UNDECLARED"),
    ("missing-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1); return p->a; }\n", "SEM_ARITY_MISMATCH"),
    ("dup-field", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, a: 2); return p->a; }\n", "SEM_DUPLICATE"),
    ("rec-undeclared", "fn main(): int { let p: Q = Q(x: 1); return 0; }\n", "SEM_UNDECLARED"),
    ("status-field", "record T { s: status<int> }\nfn main(): int { let r: T = T(s: byte_at(\"ab\", 9)); return 0; }\n", "SEM_TYPE_MISMATCH"),
    ("print-record", "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); print(p); return 0; }\n", "SEM_PROFILE_EXCLUDED"),
]

BOUND_CASES_17 = ("records-17", "".join("record R%d { x: int }\n" % i for i in range(17)) + "fn main(): int { return 0; }\n")
BOUND_CASES_130 = ("frames-130", "record Pair { a: int, b: int }\nfn main(): int { " + " ".join("let v%d: Pair = Pair(a: %d, b: %d);" % (i, i, i) for i in range(65)) + " return v64->b; }\n")


class BERAcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 40)

    def test_02_determinism(self):
        again = _run_be(self.combo, ACCEPT_CASES)
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w, _e in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        outs = [(w, e) for _n, _h, w, e in self.images]
        self.assertGreater(len(set(outs)), 10)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_field_order_matters(self):
        by_name = {n: e for n, _h, _w, e in self.images}
        self.assertNotEqual(by_name["rec-three-a"], by_name["rec-three-c"])
        self.assertEqual(by_name["rec-nominal-off"], 7)


class BERRejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, REJECT_CHECK_CASES)
        cls.r17 = _run_be(cls.combo, [BOUND_CASES_17])
        cls.rb130 = _run_be(cls.combo, [BOUND_CASES_130])

    def test_06_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_07_checker_passthrough(self):
        want_code = {"SEM_UNDECLARED": 9, "SEM_ARITY_MISMATCH": 12, "SEM_DUPLICATE": 10,
                     "SEM_TYPE_MISMATCH": 11, "SEM_PROFILE_EXCLUDED": 15}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src = case[0], case[1]
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertFalse(hr.ok)
            self.assertEqual(code, want_code[hr.diagnostic.code], name)
            self.assertEqual(hexstr, "", name)

    def test_08_resource_bounds(self):
        code, _off, hexstr = self.r17[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")
        code, _off, hexstr = self.rb130[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


class BERMutantTests(unittest.TestCase):
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

    def test_be_m1_field_plus8(self):
        combo = _mut(_combo_text(),
                     "  if ch->ts == 1 { } else { if ch->ts == 2 { } else { return BZ(p: t->s, n: 0, c: 25, o: t->s); } }\n  return BZ(p: ch->k, n: sz_mov_rax_home(ch->slot), c: 0, o: 0);",
                     "  if ch->ts == 1 { } else { if ch->ts == 2 { } else { return BZ(p: t->s, n: 0, c: 25, o: t->s); } }\n  return BZ(p: ch->k, n: sz_mov_rax_home(ch->slot + 1), c: 0, o: 0);")
        combo = _mut(combo,
                     "  if ch->ts == 1 { } else { if ch->ts == 2 { } else { return BZ(p: t->s, n: acc, c: 25, o: t->s); } }\n  return BZ(p: ch->k, n: e_mov_rax_home(ch->slot, acc), c: 0, o: 0);",
                     "  if ch->ts == 1 { } else { if ch->ts == 2 { } else { return BZ(p: t->s, n: acc, c: 25, o: t->s); } }\n  return BZ(p: ch->k, n: e_mov_rax_home(ch->slot + 1, acc), c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("rec-basic-a")))

    def test_be_m2_textual_order(self):
        combo = _mut(_combo_text(),
                     "  return be_s_ctor_sep(src, f, fs, fe, rid, dk, ds, fl, e->p, end, nf, got, acc + e->n + be_store_size(dk, ds, fl->off));",
                     "  return be_s_ctor_sep(src, f, fs, fe, rid, dk, ds, fl, e->p, end, nf, got, acc + e->n + be_store_size(dk, ds, got));")
        combo = _mut(combo,
                     "  let a0: int = be_store_emit(dk, ds, fl->off, e->n);",
                     "  let a0: int = be_store_emit(dk, ds, got, e->n);")
        self.assertTrue(self._red_on(combo, self._idx("rec-reorder-a")))

    def test_be_m3_nested_size_omitted(self):
        combo = _mut(_combo_text(),
                     "  let e: BZ = be_s_recx(src, f, fs, fe, dk, ds + fl->off, pos, end);",
                     "  let e: BZ = be_s_recx(src, f, fs, fe, dk, ds, pos, end);")
        combo = _mut(combo,
                     "  let e: BZ = be_e_recx(src, f, fs, fe, dk, ds + fl->off, pos, end, acc);",
                     "  let e: BZ = be_e_recx(src, f, fs, fe, dk, ds, pos, end, acc);")
        self.assertTrue(self._red_on(combo, self._idx("rec-nest-full")))

    def test_be_m4_frame_bytes_omitted(self):
        combo = _mut(_combo_text(),
                     "  let fr: int = nl + be_unit_maxrec(src, f);",
                     "  let fr: int = nl - be_unit_maxrec(src, f);")
        combo = _mut(combo,
                     "  let fr: int = e_frame(nl + be_unit_maxrec(src, f), acc);",
                     "  let fr: int = e_frame(nl - be_unit_maxrec(src, f), acc);")
        self.assertTrue(self._red_on(combo, self._idx("rec-temp-clobber")))
    def test_be_m5_param_wrong_offset(self):
        combo = _mutated_combo(
            "  if v->k == 1 { return be_param_base(src, f, fs, fe, v->slot) + 1; } else { }",
            "  if v->k == 1 { return be_param_base(src, f, fs, fe, v->slot) + 2; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("rec-param")))

    def test_be_m6_arg_copy_removed(self):
        combo = _mut(_combo_text(),
                     "  let e: BZ = be_s_recx(src, f, fs, fe, 0, tb, pos, end);",
                     "  let e: BZ = be_s_recx(src, f, fs, fe, 0, tb + w, pos, end);")
        combo = _mut(combo,
                     "  let e: BZ = be_e_recx(src, f, fs, fe, 0, tb, pos, end, acc);",
                     "  let e: BZ = be_e_recx(src, f, fs, fe, 0, tb + w, pos, end, acc);")
        self.assertTrue(self._red_on(combo, self._idx("rec-param-mix")))

    def test_be_m7_wrong_ret_dst(self):
        combo = _mut(_combo_text(),
                     "  let e: BZ = be_s_recx(src, f, fs, fe, 1, 0, nx->s, end);",
                     "  let e: BZ = be_s_recx(src, f, fs, fe, 0, 0, nx->s, end);")
        combo = _mut(combo,
                     "  let e: BZ = be_e_recx(src, f, fs, fe, 1, 0, nx->s, end, acc);",
                     "  let e: BZ = be_e_recx(src, f, fs, fe, 0, 0, nx->s, end, acc);")
        self.assertTrue(self._red_on(combo, self._idx("rec-ret-ctor")))

    def test_be_m8_sret_reg_wrong(self):
        combo = _mutated_combo(
            "  let a2: int = e_b(125, a1);",
            "  let a2: int = e_b(117, a1);")
        self.assertTrue(self._red_on(combo, self._idx("rec-ret-ctor")))

    def test_be_m9_shared_temp(self):
        combo = _mutated_combo(
            "fn be_tempbase(src: str, f: int, cs: int, ce: int): int {\n  return scope_slot(src, f, cs, ce, ce) + 1;\n}",
            "fn be_tempbase(src: str, f: int, cs: int, ce: int): int {\n  return 1;\n}")
        self.assertTrue(self._red_on(combo, self._idx("rec-temp-clobber")))

    def test_be_m10_ordinal_ignored(self):
        combo = _mutated_combo(
            "  let fl: VS = be_rec_field(src, f, rid, nm->s, nm->l);",
            "  let fl: VS = be_rec_field(src, f, 0, nm->s, nm->l);")
        self.assertTrue(self._red_on(combo, self._idx("rec-nominal-off")))

    def test_be_m11_bool_not_normalized(self):
        combo = _mut(_combo_text(),
                     "  if op == 6 { return 14; } else { }",
                     "  if op == 6 { return 11; } else { }")
        combo = _mut(combo,
                     "  if op == 7 { return 14; } else { }",
                     "  if op == 7 { return 11; } else { }")
        combo = _mut(combo,
                     "  if op == 8 { return 14; } else { }",
                     "  if op == 8 { return 11; } else { }")
        combo = _mut(combo,
                     "  if op == 9 { return 14; } else { }",
                     "  if op == 9 { return 11; } else { }")
        combo = _mut(combo,
                     "  if op == 10 { return 14; } else { }",
                     "  if op == 10 { return 11; } else { }")
        combo = _mut(combo,
                     "  if op == 11 { return 14; } else { }",
                     "  if op == 11 { return 11; } else { }")
        combo = _mut(combo,
                     "fn be_combine_cmp(op: int, acc: int): int {\n  let a0: int = e_cmp_rax_rcx(acc);\n  let a1: int = e_setcc(be_cc(op), a0);\n  let a2: int = e_movzx_eax_al(a1);\n  return a2;\n}",
                     "fn be_combine_cmp(op: int, acc: int): int {\n  let a0: int = e_cmp_rax_rcx(acc);\n  let a1: int = e_setcc(be_cc(op), a0);\n  return a1;\n}")
        self.assertTrue(self._red_on(combo, self._idx("rec-bool-cmp")))

    def test_be_m12_frame_size_wrong(self):
        combo = _mutated_combo(
            "  let fr: int = nl + be_unit_maxrec(src, f);",
            "  let fr: int = nl;")
        self.assertTrue(self._red_on(combo, self._idx("rec-param")))

    def test_be_m13_copy_misses_last(self):
        combo = _mut(_combo_text(),
                     "fn be_copy_size_home(sslot: int, dslot: int, w: int, i: int): int {\n  if i >= w { return 0; } else { }",
                     "fn be_copy_size_home(sslot: int, dslot: int, w: int, i: int): int {\n  if i + 1 >= w { return 0; } else { }")
        combo = _mut(combo,
                     "fn be_copy_emit_home(sslot: int, dslot: int, w: int, i: int, acc: int): int {\n  if i >= w { return acc; } else { }",
                     "fn be_copy_emit_home(sslot: int, dslot: int, w: int, i: int, acc: int): int {\n  if i + 1 >= w { return acc; } else { }")
        combo = _mut(combo,
                     "fn be_copy_size_rdi(sslot: int, w: int, i: int): int {\n  if i >= w { return 0; } else { }",
                     "fn be_copy_size_rdi(sslot: int, w: int, i: int): int {\n  if i + 1 >= w { return 0; } else { }")
        combo = _mut(combo,
                     "fn be_copy_emit_rdi(sslot: int, w: int, i: int, acc: int): int {\n  if i >= w { return acc; } else { }",
                     "fn be_copy_emit_rdi(sslot: int, w: int, i: int, acc: int): int {\n  if i + 1 >= w { return acc; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("rec-ret-var")))

    def test_be_m14_nested_base_omitted(self):
        combo = _mutated_combo(
            "  let r: VS = be_chain(src, f, fl->tl, slot + fl->off, nm->p, end);",
            "  let r: VS = be_chain(src, f, fl->tl, slot, nm->p, end);")
        self.assertTrue(self._red_on(combo, self._idx("rec-nest-inner")))


if __name__ == "__main__":
    unittest.main()
