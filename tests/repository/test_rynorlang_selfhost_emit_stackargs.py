"""Stage 19e G2 extended direct-call ABI beyond six word slots.

Frozen ABI (derived from emitted frames, not assumed from System V):
- slots 0..5 -> RDI, RSI, RDX, RCX, R8, R9 (unchanged, byte-identical).
- slots 6+  -> caller stack, pushed in ascending slot order during the
  existing left-to-right argument evaluation (no new temps, no reorder).
- callee entry: [RSP]=retaddr; prologue push RBP; incoming stack slot
  6+k lives at [RBP+16+8*(N-7-k)] without sret, [RBP+24+...] with sret
  (16 = retaddr + saved RBP; +8 skips the sret address word which the
  caller pushes last). Callee spills stack words into the SAME home
  slots the 1-based frontend model already uses.
- caller cleanup: none for <=6 (pops already balanced); add RSP,8N for
  N>=7. RSP after cleanup == RSP before setup, exactly; no padding is
  added anywhere (alignment invariant = historical status quo).
- bound: <=12 params and <=12 slots, else backend 25. Twelve keeps every
  new rsp-relative displacement in imm8 and covers the 7..12 compiler
  histogram buckets (the beq-shaped 7-slot family included).

New machine forms (caller-saved only): mov reg,[RSP+ib] x6, add RSP,ib.
Callee stack reads reuse mov rax,[RBP+disp32] + mov home,rax.
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
    def step(self):
        b0 = self._fetch(1)[0]
        if b0 == 0x4C:
            off = self.rip - CODE_BASE
            if self.code[off] == 0x8B and self.code[off + 1] in (0x44, 0x4C):
                self._fetch(1)
                b2 = self._fetch(1)[0]
                sib = self._fetch(1)[0]
                assert sib == 0x24, f"bad sib {sib:#x}"
                d = self._fetch(1)[0]
                d = d - 256 if d >= 128 else d
                v = self._sread((self.reg["rsp"] + d) & MASK64, 8)
                if b2 == 0x44:
                    self.reg["r8"] = v
                else:
                    self.reg["r9"] = v
                return
            self.rip -= 1
            super().step()
            return
        self.rip -= 1
        super().step()

    def _rex(self):
        R = self.reg
        off = self.rip - CODE_BASE
        b1 = self.code[off]
        if b1 == 0x8B and self.code[off + 1] in (0x7C, 0x74, 0x54, 0x4C) and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            d = d - 256 if d >= 128 else d
            v = self._sread((R["rsp"] + d) & MASK64, 8)
            b2 = self.code[off + 1]
            if b2 == 0x7C:
                R["rdi"] = v
            elif b2 == 0x74:
                R["rsi"] = v
            elif b2 == 0x54:
                R["rdx"] = v
            else:
                R["rcx"] = v
            return
        if b1 == 0x83 and self.code[off + 1] == 0xC4:
            self._fetch(2)
            imm = self._fetch(1)[0]
            R["rsp"] = self._flags_add(R["rsp"], imm & MASK64)
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
    ("args-0", "fn f(): int { return 3; }\nfn main(): int { return f(); }\n"),
    ("args-1", "fn id(x: int): int { return x; }\nfn main(): int { return id(9); }\n"),
    ("args-2", "fn sub(a: int, b: int): int { return a - b; }\nfn main(): int { return sub(30, 4); }\n"),
    ("args-5", "fn f(a: int, b: int, c: int, d: int, e: int): int { return e * 100 + a; }\nfn main(): int { return f(7, 1, 1, 1, 3); }\n"),
    ("args-6", "fn f(a: int, b: int, c: int, d: int, e: int, g: int): int { return g * 10 + a; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6); }\n"),
    ("args-7", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int): int { return a + b * 2 + c * 3 + d * 4 + e * 5 + g * 6 + h * 7; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6, 7); }\n"),
    ("args-7-rev", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int): int { return h * 100 + g * 10 + a; }\nfn main(): int { return f(9, 8, 1, 1, 1, 2, 3); }\n"),
    ("args-8", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int): int { return i * 100 + h * 10 + a; }\nfn main(): int { return f(5, 1, 1, 1, 1, 1, 2, 3); }\n"),
    ("args-9", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int, j: int): int { return j * 100 + a; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6, 7, 8, 9); }\n"),
    ("args-10", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int, j: int, k: int): int { return k * 1000 + j * 100 + a; }\nfn main(): int { return f(2, 0, 0, 0, 0, 0, 0, 0, 3, 4); }\n"),
    ("args-11", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int, j: int, k: int, l: int): int { return l * 100 + k * 10 + a; }\nfn main(): int { return f(1, 0, 0, 0, 0, 0, 0, 0, 0, 2, 3); }\n"),
    ("args-12str", "fn f(a: str, b: str, c: str, d: str, e: str, g: str): int { return 12; }\nfn main(): int { return f(\"a\", \"bb\", \"ccc\", \"dddd\", \"eeeee\", \"ffffff\"); }\n"),
    ("mix-str5", "fn g(s: str, a: int, b: int, c: int, d: int, e: int): int { return e; }\nfn main(): int { return g(\"pq\", 1, 2, 3, 4, 5); }\n"),
    ("mix-5str", "fn g(a: int, b: int, c: int, d: int, e: int, s: str): int { return e; }\nfn main(): int { return g(1, 2, 3, 4, 5, \"pq\"); }\n"),
    ("mix-4str1", "fn g(a: int, b: int, c: int, d: int, s: str, x: int): int { return x * 10 + d; }\nfn main(): int { return g(1, 2, 3, 4, \"pq\", 6); }\n"),
    ("mix-3str1", "fn g(a: str, b: str, c: str, x: int): int { return x; }\nfn main(): int { return g(\"a\", \"bb\", \"ccc\", 77); }\n"),
    ("mix-4str", "fn g(a: str, b: str, c: str, d: str): int { return 13; }\nfn main(): int { return g(\"a\", \"bb\", \"ccc\", \"dddd\"); }\n"),
    ("locals-7", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int): int { let x: int = a + g; let y: int = x + h; return y; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6, 7); }\n"),
    ("multi-mix", "fn s6(a: int, b: int, c: int, d: int, e: int, f: int): int { return a + b + c + d + e + f; }\nfn s7(a: int, b: int, c: int, d: int, e: int, f: int, g: int): int { return g * 10 + a; }\nfn s9(a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int, i: int): int { return i; }\nfn main(): int { let x: int = s6(1, 2, 3, 4, 5, 6); let y: int = s7(1, 2, 3, 4, 5, 6, 7); let z: int = s9(0, 0, 0, 0, 0, 0, 0, 0, 42); let w: int = s6(2, 2, 2, 2, 2, 2); return x + y + z + w; }\n"),
    ("nest-7", "fn g(a: int, b: int, c: int, d: int, e: int, f: int, w: int): int { return w; }\nfn h(x: int, y: int): int { return x + y; }\nfn main(): int { return h(g(1, 2, 3, 4, 5, 6, 7), 100); }\n"),
    ("nest-7in7", "fn g(a: int, b: int, c: int, d: int, e: int, f: int, w: int): int { return w + a; }\nfn h(x: int, p: int, q: int, r: int, s: int, t: int, u: int): int { return x + u; }\nfn main(): int { return h(g(1, 1, 1, 1, 1, 1, 10), 0, 0, 0, 0, 0, 100); }\n"),
    ("sret-7", "record Pair { a: int, b: int }\nfn mk(a: int, b: int, c: int, d: int, e: int, f: int, g: int): Pair { return Pair(a: a, b: g); }\nfn main(): int { let p: Pair = mk(1, 2, 3, 4, 5, 6, 7); return p->a + p->b; }\n"),
    ("rec-stack-params", "record P { a: int, b: int }\nfn f(p: P, q: P, r: P, s: P): int { return p->a + q->a + r->a + s->a + p->b + q->b + r->b + s->b; }\nfn main(): int { return f(P(a: 1, b: 2), P(a: 3, b: 4), P(a: 5, b: 6), P(a: 7, b: 8)); }\n"),
    ("status-7", "fn at(l: list<int,3>, i: int, a: int, b: int, c: int, d: int, e: int): status<int> { return l[i]; }\nfn main(): int { let l: list<int,3> = [5, 6, 7]; let s: status<int> = at(l, 2, 1, 1, 1, 1, 1); return unwrap_or(s, 0); }\n"),
    ("str-sret-7", "fn put(l: list<int,2>, v: int, a: int, b: int, c: int, d: int, e: int): status<list<int,2>> { let s: status<list<int,2>> = push(l, v); return s; }\nfn main(): int { let l: list<int,2> = [1]; let s: status<list<int,2>> = put(l, 2, 0, 0, 0, 0, 0); let m: list<int,2> = unwrap_or(s, l); return len(m); }\n"),
    ("branch-7", "fn pick(a: int, b: int, c: int, d: int, e: int, f: int, g: int): int { if g == 7 { return a; } return e; }\nfn main(): int { return pick(11, 0, 0, 0, 22, 0, 7) + pick(0, 0, 0, 0, 33, 0, 0); }\n"),
]

REJECT25_CASES = [
    ("slots-13", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int, j: int, k: int, l: int, m: int, n: int): int { return n; }\nfn main(): int { return 0; }\n"),
    ("params-13", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int, j: int, k: int, l: int, m: int, n: int): int { return 0; }\nfn main(): int { return f(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13); }\n"),
    ("slots-14str", "fn f(a: str, b: str, c: str, d: str, e: str, g: str, h: str): int { return 0; }\nfn main(): int { return 0; }\n"),
    ("str-ret-still", "fn h(x: int, a: int, b: int, c: int, d: int, e: int, g: int): str { return \"q\"; }\nfn main(): int { return 0; }\n"),
]

BOUND_CASES = [
    ("slots-11", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int, j: int, k: int, l: int): int { return l; }\nfn main(): int { return f(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 41); }\n"),
    ("slots-12", "fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int, i: int, j: int, k: int, l: int, m: int): int { return m; }\nfn main(): int { return f(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 43); }\n"),
]


class G2AcceptTests(unittest.TestCase):
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
        self.assertGreater(len(set(outs)), 8)

    def test_04_code_sizes_bounded(self):
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertGreaterEqual(csz, 1)
            self.assertLessEqual(csz, 65536)
            self.assertLessEqual(fsz, 32768)

    def test_05_spot_values(self):
        by_name = {n: e for n, _h, _w, e in self.images}
        self.assertEqual(by_name["args-7"], 140)
        self.assertEqual(by_name["args-9"], 901)
        self.assertEqual(by_name["sret-7"], 8)
        self.assertEqual(by_name["rec-stack-params"], 36)
        self.assertEqual(by_name["status-7"], 7)
        self.assertEqual(by_name["mix-5str"], 5)

    def test_06_no_new_forms_under_7(self):
        import re
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            if name in ("args-0", "args-1", "args-2", "args-5", "args-6"):
                self.assertNotRegex(code, b"\x48\x83\xc4", name)
                for i in range(len(code) - 4):
                    if code[i] == 0x48 and code[i + 1] == 0x8B and code[i + 3] == 0x24:
                        self.fail(f"rsp-relative load in {name}")


class G2RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rb = _run_be(cls.combo, BOUND_CASES)

    def test_07_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_08_boundary_11_12_ok(self):
        for (code, off, hexstr), (name, src) in zip(self.rb, BOUND_CASES):
            self.assertEqual(code, 0, (name, code, off))
            raw = bytes.fromhex(hexstr)
            want_exit, want_out = _oracle_run(src)
            got_exit, got_out = run_image_data(raw)
            self.assertEqual(got_exit, want_exit, name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


class G2MutantTests(unittest.TestCase):
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

    def test_g2_m1_push_stride(self):
        combo = _mut(_combo_text(),
                     "  return be_push_home_at(tb, w, i + 1, a1);",
                     "  return be_push_home_at(tb, w, i + 2, a1);")
        self.assertTrue(self._red_on(combo, self._idx("status-7")))

    def test_g2_m2_callee_base_plus8(self):
        combo = _mut(_combo_text(),
                     "  let disp: int = 16 + 8 * (ns - 7 - k) + ragg * 24;",
                     "  let disp: int = 24 + 8 * (ns - 7 - k) + ragg * 24;")
        self.assertTrue(self._red_on(combo, self._idx("args-7")))

    def test_g2_m3_no_retaddr_term(self):
        combo = _mut(_combo_text(),
                     "  let disp: int = 16 + 8 * (ns - 7 - k) + ragg * 24;",
                     "  let disp: int = 8 + 8 * (ns - 7 - k) + ragg * 24;")
        self.assertTrue(self._red_on(combo, self._idx("args-7")))

    def test_g2_m4_spill_store_dropped(self):
        combo = _mut(_combo_text(),
                     "  let a0: int = e_ld_rbp_off(disp, acc);\n  return e_mov_home_rax(hs, a0);",
                     "  let a0: int = e_ld_rbp_off(disp, acc);\n  return a0;")
        self.assertTrue(self._red_on(combo, self._idx("args-7")))

    def test_g2_m5_no_cleanup(self):
        combo = _mut(_combo_text(),
                     "  let q3: int = e_add_rsp_ib(ns * 8, q2);",
                     "  let q3: int = q2;")
        combo = _mut(combo,
                     "  return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + 6 * sz_load_rsp() + sz_add_rsp_ib() + sz_call_op(), c: 0, o: 0);",
                     "  return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + 6 * sz_load_rsp() + sz_call_op(), c: 0, o: 0);")
        body = " ".join("s12(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12);" for _ in range(45))
        src = "fn s12(a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int, i: int, j: int, k: int, l: int): int { return l; }\nfn main(): int { " + body + " return 0; }\n"
        (code, _off, hexstr) = _run_be(self.combo, [("m5base", src)])[0]
        self.assertEqual(code, 0)
        want_exit, _ = _oracle_run(src)
        got_exit, _ = run_image_data(bytes.fromhex(hexstr))
        self.assertEqual(got_exit, want_exit)
        (mcode, _moff, mhex) = _run_be(combo, [("m5mut", src)])[0]
        self.assertEqual(mcode, 0)
        with self.assertRaises(RuntimeError):
            run_image_data(bytes.fromhex(mhex))

    def test_g2_m6_cleanup_plus8(self):
        combo = _mut(_combo_text(),
                     "  let q3: int = e_add_rsp_ib(ns * 8, q2);",
                     "  let q3: int = e_add_rsp_ib(ns * 8 + 8, q2);")
        self.assertTrue(self._red_on(combo, self._idx("multi-mix")))

    def test_g2_m7_ptr_len_swapped(self):
        combo = _mut(_combo_text(),
                     "  if reg == 4 { return e_load_rsp_r(68, disp, acc); } else { }",
                     "  if reg == 4 { return e_load_rsp_r(76, disp, acc); } else { }")
        combo = _mut(combo,
                     "  return e_load_rsp_r(76, disp, acc);",
                     "  return e_load_rsp_r(68, disp, acc);")
        self.assertTrue(self._red_on(combo, self._idx("mix-5str")))

    def test_g2_m8_one_reg_per_arg(self):
        combo = _mut(_combo_text(),
                     "  let a0: int = be_e_spillw(acc, regs, base, w, 0, ns, ragg);\n  return be_e_spills(src, f, cs, ce, a0, i + 1, regs + w);",
                     "  let a0: int = be_e_spillw(acc, regs, base, w, 0, ns, ragg);\n  return be_e_spills(src, f, cs, ce, a0, i + 1, regs + 1);")
        self.assertTrue(self._red_on(combo, self._idx("mix-4str1")))

    def test_g2_m9_cleanup_size_extra(self):
        combo = _mut(_combo_text(),
                     "  return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + 6 * sz_load_rsp() + sz_add_rsp_ib() + sz_call_op(), c: 0, o: 0);",
                     "  return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + 6 * sz_load_rsp() + sz_add_rsp_ib() + 8 + sz_call_op(), c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("args-7")))

    def test_g2_m10_size_omit_cleanup(self):
        combo = _mut(_combo_text(),
                     "  return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + 6 * sz_load_rsp() + sz_add_rsp_ib() + sz_call_op(), c: 0, o: 0);",
                     "  return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + 6 * sz_load_rsp() + sz_call_op(), c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("args-7")))

    def test_g2_m11_const_offset(self):
        combo = _mut(_combo_text(),
                     "  let d: int = 8 * (ns - 1 - j) + ragg * 24;",
                     "  let d: int = 8 * (ns - 1 - 6) + ragg * 24;")
        self.assertTrue(self._red_on(combo, self._idx("args-8")))

    def test_g2_m12_reg_only_fallback(self):
        combo = _mut(_combo_text(),
                     "  if ns <= 6 { return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + be_pop_size(ns, 0) + sz_call_op(), c: 0, o: 0); } else { }",
                     "  if ns <= 12 { return BZ(p: a->p, n: a->n + be_sret_size(dk, ragg) + be_pop_size(ns, 0) + sz_call_op(), c: 0, o: 0); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("args-7")))


if __name__ == "__main__":
    unittest.main()
