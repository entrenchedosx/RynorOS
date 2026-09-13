"""Stage 19e BE-D static strings and print/data lowering.

The baby backend (`rynorlang/selfhost/emit.rl`, core dialect) packs
decoded string literals (no dedup: every occurrence gets its own
entry, consecutive order) into the normal RYNX v2 data section and
lowers `print("literal")` to an inline write syscall (rax=2, rbx=1,
rcx=DATA_BASE+offset, rdx=len, int 0x80). No NUL terminators are
stored or scanned; lengths are explicit. `print(int/bool)` stays
backend-25; string locals/params/returns stay backend-25.

QEMU/native stdout proof lives in tests/integration/
test_native_backend.py; here execution is proven by the test-only
emulator below (BE-C emulator plus exactly the print forms the
backend emits: mov ebx/ecx/edx imm32 and the rax==2 write path over
the data segment). Reference stdout/exit come from the trusted host
oracle (slot-1 child writes for stdout, exit low 32 bits).
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.repository import test_rynorlang_selfhost_emit_branch as bec  # noqa: E402
from tests.repository import test_rynorlang_selfhost_emit as bea  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402


MASK64 = bea.MASK64
DATA_BASE = 0x600000


class Emu(bec.Emu):
    def __init__(self, code, data=b""):
        super().__init__(code)
        self.data = bytes(data)
        self.stdout = bytearray()

    def step(self):
        b0 = self._fetch(1)[0]
        R = self.reg
        if b0 == 0xB9:
            R["rcx"] = int.from_bytes(self._fetch(4), "little")
        elif b0 == 0xBA:
            R["rdx"] = int.from_bytes(self._fetch(4), "little")
        elif b0 == 0xBB:
            R["rbx"] = int.from_bytes(self._fetch(4), "little") & 0xFFFFFFFF
        elif b0 == 0xCD:
            b1 = self._fetch(1)[0]
            assert b1 == 0x80, f"bad int {b1:#x}"
            if R["rax"] == 0:
                self.exited = R["rbx"] & 0xFFFFFFFF
            elif R["rax"] == 2:
                buf, ln = R["rcx"], R["rdx"]
                if R["rbx"] != 1:
                    raise RuntimeError(f"write to fd {R['rbx']}")
                if not (DATA_BASE <= buf and buf + ln <= DATA_BASE + len(self.data)):
                    raise RuntimeError(f"write out of data {buf:#x}+{ln}")
                self.stdout += self.data[buf - DATA_BASE:buf - DATA_BASE + ln]
                R["rax"] = ln
            else:
                raise RuntimeError(f"unsupported syscall {R['rax']}")
        else:
            self.rip -= 1
            super().step()


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


ACCEPT_CASES = [
    ("d-print-x", 'fn main(): int { print("x"); return 0; }\n', b"x", 0),
    ("d-print-hello", 'fn main(): int { print("hello"); return 0; }\n', b"hello", 0),
    ("d-print-empty", 'fn main(): int { print(""); return 3; }\n', b"", 3),
    ("d-print-two", 'fn main(): int { print("ab"); print("cde"); return 0; }\n', b"abcde", 0),
    ("d-print-dup", 'fn main(): int { print("xy"); print("xy"); return 0; }\n', b"xyxy", 0),
    ("d-print-nl", 'fn main(): int { print("a\\nb"); return 0; }\n', b"a\nb", 0),
    ("d-print-tab", 'fn main(): int { print("a\\tb"); return 0; }\n', b"a\tb", 0),
    ("d-print-quote", 'fn main(): int { print("say \\"hi\\""); return 0; }\n', b'say "hi"', 0),
    ("d-print-backslash", 'fn main(): int { print("a\\\\b"); return 0; }\n', b"a\\b", 0),
    ("d-print-mixed", 'fn main(): int { print("a\\nb\\\\c\\"d\\te"); return 0; }\n', b'a\nb\\c"d\te', 0),
    ("d-print-nonzero", 'fn main(): int { print("q"); return 7; }\n', b"q", 7),
    ("d-print-arith", 'fn main(): int { let v: int = 2 + 3; print("v"); return v; }\n', b"v", 5),
    ("d-print-if", 'fn main(): int { if true { print("T"); } else { print("F"); } return 1; }\n', b"T", 1),
    ("d-print-else", 'fn main(): int { if false { print("T"); } else { print("F"); } return 2; }\n', b"F", 2),
    ("d-print-while", 'fn main(): int { while true { print("W"); return 2; } return 3; }\n', b"W", 2),
    ("d-print-helper", 'fn h(): int { print("h"); return 0; }\nfn main(): int { h(); return 4; }\n', b"h", 4),
    ("d-print-helper-arg", 'fn h(x: int): int { print("p"); return x; }\nfn main(): int { return h(6); }\n', b"p", 6),
    ("d-print-three", 'fn main(): int { print("one"); print("two"); print("six"); return 0; }\n', b"onetwosix", 0),
    ("d-print-long", 'fn main(): int { print("' + "A" * 200 + '"); return 0; }\n', b"A" * 200, 0),
    ("d-print-4000", 'fn main(): int { print("' + "B" * 4000 + '"); return 0; }\n', b"B" * 4000, 0),
    ("d-print-spaces", 'fn main(): int { print("a b c"); return 0; }\n', b"a b c", 0),
    ("d-print-digits", 'fn main(): int { print("0123456789"); return 9; }\n', b"0123456789", 9),
]

REJECT25_CASES = [
    ("print-int", 'fn main(): int { print(1); return 0; }\n'),
    ("print-bool", 'fn main(): int { print(true); return 0; }\n'),
    ("print-var", 'fn main(): int { let x: int = 1; print(x); return 0; }\n'),
    ("str-ret", 'fn f(): str { return "ab"; }\nfn main(): int { return 0; }\n'),
]

REJECT_CHECK_CASES = [
    ("print-arity-0", 'fn main(): int { print(); return 0; }\n', "SEM_ARITY_MISMATCH"),
    ("print-arity-2", 'fn main(): int { print("a", "b"); return 0; }\n', "SEM_ARITY_MISMATCH"),
]


class BEDAcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, [(n, s) for n, s, _w, _e in ACCEPT_CASES])
        cls.images = []
        for (code, off, hexstr), (name, src, _w, _e) in zip(cls.results, ACCEPT_CASES):
            assert code == 0, (name, code, off)
            raw = bytes.fromhex(hexstr)
            want_exit, want_out = _oracle_run(src)
            got_exit, got_out = run_image_data(raw)
            assert got_exit == want_exit, (name, got_exit, want_exit)
            assert want_out == _w, (name, want_out)
            assert got_out == want_out, (name, got_out, want_out)
            cls.images.append((name, hexstr, want_out, want_exit))

    def test_01_exit_and_stdout(self):
        self.assertGreaterEqual(len(self.images), 20)

    def test_02_determinism(self):
        again = _run_be(self.combo, [(n, s) for n, s, _w, _e in ACCEPT_CASES])
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _w, _e in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        outs = set(w for _n, _h, w, _e in self.images)
        self.assertGreater(len(outs), 10)

    def test_04_data_exactness(self):
        for name, hexstr, want_out, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
            self.assertEqual(fsz, msz, name)
            self.assertEqual(len(raw), 28 + csz + fsz, name)


class BEDRejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, REJECT_CHECK_CASES)

    def test_05_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_06_checker_passthrough(self):
        want_code = {"SEM_ARITY_MISMATCH": 12}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src = case[0], case[1]
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertFalse(hr.ok)
            self.assertEqual(code, want_code[hr.diagnostic.code], name)
            self.assertEqual(hexstr, "", name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


class BEDMutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()

    def _red_on(self, mutant_combo, idx):
        name, src, _w, _e = ACCEPT_CASES[idx]
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
        for i, (n, _s, _w, _e) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_bd_m1_wrong_base(self):
        combo = _mutated_combo(
            "fn be_data_base(): int {\n  return 6291456;\n}",
            "fn be_data_base(): int {\n  return 6291457;\n}")
        self.assertTrue(self._red_on(combo, self._idx("d-print-x")))

    def test_bd_m2_ptr_plus_one(self):
        combo = _mutated_combo(
            "  let q0: int = e_print_lit(be_data_base() + off, len, acc);",
            "  let q0: int = e_print_lit(be_data_base() + off + 1, len, acc);")
        self.assertTrue(self._red_on(combo, self._idx("d-print-hello")))

    def test_bd_m3_len_plus_one(self):
        combo = _mutated_combo(
            "fn be_str_len(src: str, pos: int, end: int): int {\n  if pos >= end { return 0; } else { }",
            "fn be_str_len(src: str, pos: int, end: int): int {\n  if pos >= end { return 1; } else { }")
        self.assertTrue(self._red_on(combo, self._idx("d-print-two")))

    def test_bd_m4_len_minus_one(self):
        combo = _mutated_combo(
            "  if b == 92 { return 1 + be_str_len(src, pos + 2, end); } else { }",
            "  if b == 92 { return be_str_len(src, pos + 2, end); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("d-print-mixed")))

    def test_bd_m5_no_escape_decode(self):
        combo = _mutated_combo(
            "  if b == 92 { return be_emit_str(src, pos + 2, end, e_b(be_esc_byte(unwrap_or(byte_at(src, pos + 1), 0)), acc)); } else { }",
            "  if b == 92 { return be_emit_str(src, pos + 2, end, e_b(unwrap_or(byte_at(src, pos + 1), 0), acc)); } else { }")
        self.assertTrue(self._red_on(combo, self._idx("d-print-mixed")))

    def test_bd_m6_data_size_mismatch(self):
        combo = _mutated_combo(
            "  return BZ(p: m->p, n: m->n + d, c: 0, o: 0);",
            "  return BZ(p: m->p, n: m->n, c: 0, o: 0);")
        self.assertTrue(self._red_on(combo, self._idx("d-print-x")))

    def test_bd_m7_wrong_fd(self):
        combo = _mutated_combo(
            "  let a1: int = e_mov_ebx_imm(1, a0);",
            "  let a1: int = e_mov_ebx_imm(2, a0);")
        self.assertTrue(self._red_on(combo, self._idx("d-print-x")))

    def test_bd_m8_wrong_syscall(self):
        combo = _mutated_combo(
            "  let a0: int = e_mov_eax_imm(2, acc);",
            "  let a0: int = e_mov_eax_imm(3, acc);")
        self.assertTrue(self._red_on(combo, self._idx("d-print-x")))

    def test_bd_m9_overflow_check_removed(self):
        # The 32768 data bound is unreachable through the string-literal
        # driver transport (cases embed as driver literals capped at 4096),
        # so this mutant tightens the bound to 10 instead of removing it:
        # the 200-byte case must flip from 0 to 26, proving the gate fires.
        combo = _mutated_combo(
            "  let data: int = be_data_len(src, f);\n  if data <= 32768 { } else { return derr(26, f, 0); }",
            "  let data: int = be_data_len(src, f);\n  if data <= 10 { } else { return derr(26, f, 0); }")
        (code, _off, _hex) = _run_be(combo, [("d-print-long", 'fn main(): int { print("' + "A" * 200 + '"); return 0; }\n')])[0]
        self.assertEqual(code, 26)

    def test_bd_m10_canned_literal(self):
        combo = _mutated_combo(
            "fn be_emit_data_fn(src: str, f: int, it: TI, end: int, acc: int): BZ {",
            "fn be_emit_data_fn(src: str, f: int, it: TI, end: int, acc: int): BZ {\n"
            '  print("78");\n'
            "  return BZ(p: 0, n: acc, c: 0, o: 0);",
            count=1)
        reds = 0
        for idxname in ("d-print-x", "d-print-hello"):
            if self._red_on(combo, self._idx(idxname)):
                reds += 1
        self.assertGreater(reds, 0)

    def test_bd_m11_shared_offset(self):
        combo = _mutated_combo(
            "  let q0: int = e_print_lit(be_data_base() + off, len, acc);",
            "  let q0: int = e_print_lit(be_data_base(), len, acc);")
        self.assertTrue(self._red_on(combo, self._idx("d-print-two")))

    def test_bd_m12_nul_scan(self):
        combo = _mutated_combo(
            "  let a3: int = e_mov_edx_imm(len, a2);",
            "  let a3: int = e_mov_edx_imm(64, a2);")
        self.assertTrue(self._red_on(combo, self._idx("d-print-x")))


if __name__ == "__main__":
    unittest.main()
