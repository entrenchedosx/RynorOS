"""Stage 19e BE-A scalar backend: machine-code emission, RYNX validity,
execution behavior via a test x86-64 emulator, anti-canned variation,
bounds, and backend mutants.

The baby backend (`rynorlang/selfhost/emit.rl`, core dialect) compiles a
restricted validated subset (single `fn main(): int`, scalar int/bool lets,
`return`, arithmetic/logic/shift/compare, no calls/division/aggregates)
directly to x86-64 machine code in a normal RYNX v2 image. (Multi-function
programs and direct calls are covered by BE-B in
`test_rynorlang_selfhost_emit_calls.py`; the `call`/`second-fn` shapes
graduated there.) QEMU and a
native toolchain are unavailable here, so execution is proven by the
test-only emulator below (it EXECUTES baby bytes; it never generates
code and cannot mask a broken backend). Reference semantics come from
the trusted host oracle (exit low 32 bits, the EBX width).
"""

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402



MASK64 = (1 << 64) - 1
CODE_BASE = 0x400000
STACK_TOP = 0x800000
STACK_SIZE = 4096


def _s64(v):
    v &= MASK64
    return v - (1 << 64) if v >= (1 << 63) else v


class Emu:
    def __init__(self, code: bytes):
        self.code = bytes(code)
        self.mem = bytearray(STACK_SIZE)
        self.reg = {"rax": 0, "rcx": 0, "rdx": 0, "rbx": 0,
                    "rsp": STACK_TOP, "rbp": 0, "rsi": 0, "rdi": 0,
                    "r8": 0, "r9": 0, "r10": 0, "r11": 0}
        self.cf = self.zf = self.sf = self.of = 0
        self.rip = CODE_BASE
        self.exited = None

    def _fetch(self, n):
        off = self.rip - CODE_BASE
        if off < 0 or off + n > len(self.code):
            raise RuntimeError(f"rip out of code: {self.rip:#x}")
        b = self.code[off:off + n]
        self.rip += n
        return b

    def _sread(self, addr, n):
        if not (STACK_TOP - STACK_SIZE <= addr and addr + n <= STACK_TOP):
            raise RuntimeError(f"bad stack read {addr:#x}")
        return int.from_bytes(self.mem[addr - (STACK_TOP - STACK_SIZE):addr - (STACK_TOP - STACK_SIZE) + n], "little")

    def _swrite(self, addr, n, val):
        if not (STACK_TOP - STACK_SIZE <= addr and addr + n <= STACK_TOP):
            raise RuntimeError(f"bad stack write {addr:#x}")
        val &= MASK64
        self.mem[addr - (STACK_TOP - STACK_SIZE):addr - (STACK_TOP - STACK_SIZE) + n] = val.to_bytes(8, "little")[:n]

    def _push(self, val):
        self.reg["rsp"] = (self.reg["rsp"] - 8) & MASK64
        self._swrite(self.reg["rsp"], 8, val)

    def _pop(self):
        v = self._sread(self.reg["rsp"], 8)
        self.reg["rsp"] = (self.reg["rsp"] + 8) & MASK64
        return v

    def _flags_add(self, a, b):
        r = (a + b) & MASK64
        self.cf = 1 if a + b > MASK64 else 0
        self.zf = 1 if r == 0 else 0
        self.sf = 1 if r >= (1 << 63) else 0
        self.of = 1 if ((~(a ^ b)) & (a ^ r) & (1 << 63)) != 0 else 0
        return r

    def _flags_sub(self, a, b):
        r = (a - b) & MASK64
        self.cf = 1 if a < b else 0
        self.zf = 1 if r == 0 else 0
        self.sf = 1 if r >= (1 << 63) else 0
        self.of = 1 if (((a ^ b) & (a ^ r)) & (1 << 63)) != 0 else 0
        return r

    def _flags_logic(self, r):
        r &= MASK64
        self.cf = 0
        self.of = 0
        self.zf = 1 if r == 0 else 0
        self.sf = 1 if r >= (1 << 63) else 0
        return r

    def _flags_shl(self, a, n):
        n &= 63
        if n == 0:
            return a & MASK64
        r = (a << n) & MASK64
        self.cf = (a >> (64 - n)) & 1
        self.zf = 1 if r == 0 else 0
        self.sf = 1 if r >= (1 << 63) else 0
        # OF defined only for 1-bit counts; deterministic 0 otherwise.
        self.of = (((a ^ r) >> 63) & 1) if n == 1 else 0
        return r

    def _flags_sar(self, a, n):
        n &= 63
        if n == 0:
            return a & MASK64
        s = _s64(a)
        r = (s >> n) & MASK64
        self.cf = (a >> (n - 1)) & 1 if n >= 1 else 0
        self.zf = 1 if r == 0 else 0
        self.sf = 1 if r >= (1 << 63) else 0
        self.of = 0
        return r

    def _cond(self, cc):
        # cc = low byte of setcc opcode: 94 sete, 95 setne, 9c setl,
        # 9e setle, 9f setg, 9d setge
        if cc == 0x94:
            return self.zf
        if cc == 0x95:
            return 1 - self.zf
        if cc == 0x9C:
            return 1 if self.sf != self.of else 0
        if cc == 0x9E:
            return 1 if (self.zf or self.sf != self.of) else 0
        if cc == 0x9F:
            return 1 if (not self.zf and self.sf == self.of) else 0
        if cc == 0x9D:
            return 1 if self.sf == self.of else 0
        raise RuntimeError(f"bad setcc {cc:#x}")

    def step(self):
        b0 = self._fetch(1)[0]
        R = self.reg
        if b0 == 0x50:
            self._push(R["rax"])
        elif b0 == 0x51:
            self._push(R["rcx"])
        elif b0 == 0x55:
            self._push(R["rbp"])
        elif b0 == 0x58:
            R["rax"] = self._pop()
        elif b0 == 0x59:
            R["rcx"] = self._pop()
        elif b0 == 0x5D:
            R["rbp"] = self._pop()
        elif b0 == 0xC3:
            self.rip = self._pop()
        elif b0 == 0xC9:
            R["rsp"] = R["rbp"]
            R["rbp"] = self._pop()
        elif b0 == 0xCC:
            raise RuntimeError("int3")
        elif b0 == 0xCD:
            b1 = self._fetch(1)[0]
            assert b1 == 0x80, f"bad int {b1:#x}"
            if R["rax"] == 0:
                self.exited = R["rbx"] & 0xFFFFFFFF
            else:
                raise RuntimeError(f"unsupported syscall {R['rax']}")
        elif b0 == 0xB8:
            R["rax"] = int.from_bytes(self._fetch(4), "little")
        elif b0 == 0x83:
            b1 = self._fetch(1)[0]
            assert b1 == 0xF0, f"bad 83 {b1:#x}"
            imm = self._fetch(1)[0]
            assert imm == 1
            R["rax"] = self._flags_logic(R["rax"] ^ 1) & 0xFFFFFFFF
        elif b0 == 0x89:
            b1 = self._fetch(1)[0]
            if b1 == 0xC3:
                R["rbx"] = R["rax"] & 0xFFFFFFFF
            elif b1 == 0xC1:
                R["rcx"] = R["rax"]
            elif b1 == 0x45:
                d = self._fetch(1)[0]
                d = d - 256 if d >= 128 else d
                self._swrite((R["rbp"] + d) & MASK64, 8, R["rax"])
            elif b1 == 0x85:
                d = int.from_bytes(self._fetch(4), "little", signed=True)
                self._swrite((R["rbp"] + d) & MASK64, 8, R["rax"])
            elif b1 == 0xE5:
                R["rbp"] = R["rsp"]
            else:
                raise RuntimeError(f"bad 89 {b1:#x}")
        elif b0 == 0x8B:
            b1 = self._fetch(1)[0]
            if b1 == 0x45:
                d = self._fetch(1)[0]
                d = d - 256 if d >= 128 else d
                R["rax"] = self._sread((R["rbp"] + d) & MASK64, 8)
            elif b1 == 0x85:
                d = int.from_bytes(self._fetch(4), "little", signed=True)
                R["rax"] = self._sread((R["rbp"] + d) & MASK64, 8)
            else:
                raise RuntimeError(f"bad 8b {b1:#x}")
        elif b0 == 0xE8:
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            self._push(self.rip)
            self.rip = (self.rip + disp) & MASK64
        elif b0 == 0x48:
            self._rex()
        elif b0 == 0x0F:
            b1 = self._fetch(1)[0]
            if b1 in (0x94, 0x95, 0x9C, 0x9E, 0x9F, 0x9D):
                b2 = self._fetch(1)[0]
                assert b2 == 0xC0, f"bad setcc modrm {b2:#x}"
                R["rax"] = (R["rax"] & ~0xFF) | self._cond(b1)
            elif b1 == 0xB6:
                b2 = self._fetch(1)[0]
                assert b2 == 0xC0
                R["rax"] = R["rax"] & 0xFF
            elif b1 == 0xAF:
                b2 = self._fetch(1)[0]
                assert b2 == 0xC1, f"bad imul {b2:#x}"
                R["rax"] = _s64(R["rax"]) * _s64(R["rcx"]) & MASK64
                # flags unspecified for imul; corpus never reads them
                self.cf = self.of = 0
            else:
                raise RuntimeError(f"bad 0f {b1:#x}")
        else:
            raise RuntimeError(f"bad opcode {b0:#x} at {(self.rip - 1):#x}")

    def _rex(self):
        R = self.reg
        b1 = self._fetch(1)[0]
        if b1 == 0xB8:
            R["rax"] = int.from_bytes(self._fetch(8), "little")
        elif b1 == 0x89:
            b2 = self._fetch(1)[0]
            if b2 == 0xE5:
                R["rbp"] = R["rsp"]
            elif b2 == 0xC1:
                R["rcx"] = R["rax"]
            elif b2 == 0x45:
                d = self._fetch(1)[0]
                d = d - 256 if d >= 128 else d
                self._swrite((R["rbp"] + d) & MASK64, 8, R["rax"])
            elif b2 == 0x85:
                d = int.from_bytes(self._fetch(4), "little", signed=True)
                self._swrite((R["rbp"] + d) & MASK64, 8, R["rax"])
            else:
                raise RuntimeError(f"bad rex89 {b2:#x}")
        elif b1 == 0x8B:
            b2 = self._fetch(1)[0]
            if b2 == 0x45:
                d = self._fetch(1)[0]
                d = d - 256 if d >= 128 else d
                R["rax"] = self._sread((R["rbp"] + d) & MASK64, 8)
            elif b2 == 0x85:
                d = int.from_bytes(self._fetch(4), "little", signed=True)
                R["rax"] = self._sread((R["rbp"] + d) & MASK64, 8)
            else:
                raise RuntimeError(f"bad rex8b {b2:#x}")
        elif b1 == 0x01:
            b2 = self._fetch(1)[0]
            assert b2 == 0xC8, f"bad add {b2:#x}"
            R["rax"] = self._flags_add(R["rax"], R["rcx"])
        elif b1 == 0x29:
            b2 = self._fetch(1)[0]
            assert b2 == 0xC8, f"bad sub {b2:#x}"
            R["rax"] = self._flags_sub(R["rax"], R["rcx"])
        elif b1 == 0x21:
            b2 = self._fetch(1)[0]
            assert b2 == 0xC8, f"bad and {b2:#x}"
            R["rax"] = self._flags_logic(R["rax"] & R["rcx"])
        elif b1 == 0x09:
            b2 = self._fetch(1)[0]
            assert b2 == 0xC8, f"bad or {b2:#x}"
            R["rax"] = self._flags_logic(R["rax"] | R["rcx"])
        elif b1 == 0x31:
            b2 = self._fetch(1)[0]
            assert b2 == 0xC8, f"bad xor {b2:#x}"
            R["rax"] = self._flags_logic(R["rax"] ^ R["rcx"])
        elif b1 == 0x39:
            b2 = self._fetch(1)[0]
            assert b2 == 0xC8, f"bad cmp {b2:#x}"
            self._flags_sub(R["rax"], R["rcx"])
        elif b1 == 0xF7:
            b2 = self._fetch(1)[0]
            if b2 == 0xD8:
                a = R["rax"]
                r = (0 - a) & MASK64
                self.cf = 1 if a != 0 else 0
                self.zf = 1 if r == 0 else 0
                self.sf = 1 if r >= (1 << 63) else 0
                self.of = 1 if a == (1 << 63) else 0
                R["rax"] = r
            elif b2 == 0xD0:
                R["rax"] = self._flags_logic(~R["rax"])
            else:
                raise RuntimeError(f"bad f7 {b2:#x}")
        elif b1 == 0xD3:
            b2 = self._fetch(1)[0]
            n = R["rcx"] & 0xFF
            if b2 == 0xE0:
                R["rax"] = self._flags_shl(R["rax"], n)
            elif b2 == 0xF8:
                R["rax"] = self._flags_sar(R["rax"], n)
            else:
                raise RuntimeError(f"bad d3 {b2:#x}")
        elif b1 == 0xC7:
            b2 = self._fetch(1)[0]
            assert b2 == 0xC0, f"bad c7 {b2:#x}"
            imm = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rax"] = imm & MASK64
        elif b1 == 0x81:
            b2 = self._fetch(1)[0]
            assert b2 == 0xEC, f"bad 81 {b2:#x}"
            imm = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rsp"] = (R["rsp"] - imm) & MASK64
        elif b1 == 0x0F:
            b2 = self._fetch(1)[0]
            if b2 == 0xAF:
                b3 = self._fetch(1)[0]
                assert b3 == 0xC1, f"bad imul {b3:#x}"
                R["rax"] = _s64(R["rax"]) * _s64(R["rcx"]) & MASK64
                self.cf = self.of = 0
            elif b2 in (0x94, 0x95, 0x9C, 0x9E, 0x9F, 0x9D):
                b3 = self._fetch(1)[0]
                assert b3 == 0xC0, f"bad setcc modrm {b3:#x}"
                R["rax"] = (R["rax"] & ~0xFF) | self._cond(b2)
            elif b2 == 0xB6:
                b3 = self._fetch(1)[0]
                assert b3 == 0xC0
                R["rax"] = R["rax"] & 0xFF
            else:
                raise RuntimeError(f"bad rex0f {b2:#x}")
        else:
            raise RuntimeError(f"bad rex48 {b1:#x}")

    def run(self, cap=100000):
        for _ in range(cap):
            if self.exited is not None:
                return self.exited
            self.step()
        raise RuntimeError("step cap exceeded")


def run_image(code: bytes):
    return Emu(code).run()


MASK64 = (1 << 64) - 1


def _combo_text(extra=""):
    parts = []
    for name in ("util.rl", "lex.rl", "check.rl", "prog.rl", "emit.rl"):
        parts.append((ROOT / "rynorlang" / "selfhost" / name).read_text(encoding="utf-8"))
    return parts[0] + chr(10) + parts[1] + chr(10) + parts[2] + chr(10) + parts[3] + chr(10) + parts[4] + chr(10) + extra + chr(10)


def _esc(s):
    out = []
    for ch in s:
        o = ord(ch)
        if o == 92:
            out.append(chr(92) + chr(92))
        elif o == 34:
            out.append(chr(92) + chr(34))
        elif o == 10:
            out.append(chr(92) + "n")
        elif o == 9:
            out.append(chr(92) + "t")
        elif o == 13:
            out.append(chr(92) + "r")
        else:
            out.append(ch)
    return "".join(out)


def _oracle_exit(src):
    result = analyzer.analyze(src, "t.rl", profile="core")
    assert result.ok, result.diagnostic
    module, error = rir.build_rir(result.ast, "t.rl")
    assert error is None, error
    emitted = []
    outcome = oracle.run_rir(module, out=emitted)
    assert outcome["trapped"] is None, outcome
    assert outcome["exit"] is not None
    return outcome["exit"] & 0xFFFFFFFF


def _run_be(combo_src, cases):
    drv_lines = [
        'fn be_case(src: str, f: int): int {',
        '  let d: D = be_main(src, f);',
        '  print("|");',
        '  print(d->c);',
        '  print(",");',
        '  print(d->o);',
        '  print(";");',
        '  return 0;',
        '}',
        'fn main(): int {',
    ]
    for i, case in enumerate(cases):
        src = case[1]
        drv_lines.append('  let s%d: str = %s%s%s;' % (i, chr(34), _esc(src), chr(34)))
    for i, _case in enumerate(cases):
        drv_lines.append("  be_case(s%d, 0);" % i)
    drv_lines.append("  return 0;")
    drv_lines.append("}")
    drv = chr(10).join(drv_lines) + chr(10)
    full = combo_src + drv
    for profile in ("core", None):
        kw = {} if profile is None else {"profile": profile}
        result = analyzer.analyze(full, "bedrv.rl", **kw)
        if not result.ok:
            raise AssertionError("driver rejected under %s: %s" % (profile, result.diagnostic))
    result = analyzer.analyze(full, "bedrv.rl", profile="core")
    module, error = rir.build_rir(result.ast, "bedrv.rl")
    if error is not None:
        raise AssertionError("driver RIR build failed: %s" % error)
    if rir.verify_module(module):
        raise AssertionError("driver RIR verify failed")
    emitted = []
    outcome = oracle.run_rir(module, out=emitted, step_limit=500000000)
    if outcome["trapped"] is not None or outcome["exit"] != 0:
        raise AssertionError("driver trapped: %s" % (outcome,))
    chunks = "".join(emitted).split(";")
    if len(chunks) != len(cases) + 1:
        raise AssertionError("driver emitted %d chunks for %d cases" % (len(chunks), len(cases)))
    out = []
    for chunk in chunks[:len(cases)]:
        hexstr, _, tail = chunk.rpartition("|")
        code, _, off = tail.partition(",")
        for ch in hexstr:
            assert ch in "0123456789abcdef", chunk
        out.append((int(code), int(off), hexstr))
    return out


def _parse_rnyx(raw):
    assert raw[:4] == b"RYNX", raw[:16]
    ver, arch, hlen, res, entry, code_sz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
    assert (ver, arch, hlen, res, entry) == (2, 1, 28, 0, 0), (ver, arch, hlen, res, entry)
    assert (fsz, msz) == (0, 0), (fsz, msz)
    assert 1 <= code_sz <= 65536, code_sz
    code = raw[28:28 + code_sz]
    assert len(code) == code_sz and len(raw) == 28 + code_sz
    return code, code_sz


ACCEPT_CASES = [
    ("ret-0", "fn main(): int { return 0; }\n"),
    ("ret-1", "fn main(): int { return 1; }\n"),
    ("ret-neg1", "fn main(): int { return 0 - 1; }\n"),
    ("ret-42", "fn main(): int { return 42; }\n"),
    ("ret-255", "fn main(): int { return 255; }\n"),
    ("ret-256", "fn main(): int { return 256; }\n"),
    ("ret-65535", "fn main(): int { return 65535; }\n"),
    ("ret-i32max", "fn main(): int { return 2147483647; }\n"),
    ("ret-u32max-1", "fn main(): int { return 2147483648; }\n"),
    ("ret-u32max", "fn main(): int { return 4294967295; }\n"),
    ("ret-u32max+1", "fn main(): int { return 4294967296; }\n"),
    ("ret-i32min", "fn main(): int { return 0 - 2147483648; }\n"),
    ("ret-i32min-1", "fn main(): int { return 0 - 2147483648 - 1; }\n"),
    ("ret-i64max", "fn main(): int { return 9223372036854775807; }\n"),
    ("ret-i64min", "fn main(): int { return 0 - 9223372036854775807 - 1; }\n"),
    ("add", "fn main(): int { return 1 + 2; }\n"),
    ("add-rev", "fn main(): int { return 2 + 1; }\n"),
    ("sub", "fn main(): int { return 7 - 3; }\n"),
    ("sub-rev", "fn main(): int { return 3 - 7; }\n"),
    ("mul", "fn main(): int { return 4 * 5; }\n"),
    ("prec1", "fn main(): int { return (2 + 3) * 4; }\n"),
    ("prec2", "fn main(): int { return 2 + (3 * 4); }\n"),
    ("prec3", "fn main(): int { return 10 - 2 * 3; }\n"),
    ("prec4", "fn main(): int { return (10 - 2) * 3; }\n"),
    ("neg", "fn main(): int { return 0 - 5; }\n"),
    ("neg-lit", "fn main(): int { return 0 - 3; }\n"),
    ("neg-neg", "fn main(): int { return 0 - (0 - 7); }\n"),
    ("unot-0", "fn main(): int { let b: bool = !false; return 11; }\n"),
    ("unot-7", "fn main(): int { let b: bool = !true; return 12; }\n"),
    ("unot-not", "fn main(): int { let b: bool = !!true; return 13; }\n"),
    ("bitnot-0", "fn main(): int { return ~0; }\n"),
    ("bitand", "fn main(): int { return 6 & 5; }\n"),
    ("bitor", "fn main(): int { return 6 | 5; }\n"),
    ("bitxor", "fn main(): int { return 6 ^ 5; }\n"),
    ("andand-t", "fn main(): int { let b: bool = true && true; return 20; }\n"),
    ("andand-f", "fn main(): int { let b: bool = true && false; return 21; }\n"),
    ("oror-t", "fn main(): int { let b: bool = false || true; return 22; }\n"),
    ("oror-f", "fn main(): int { let b: bool = false || false; return 23; }\n"),
    ("shl", "fn main(): int { return 1 << 3; }\n"),
    ("shr", "fn main(): int { return 256 >> 4; }\n"),
    ("sar-neg", "fn main(): int { return (0 - 8) >> 2; }\n"),
    ("shl-65", "fn main(): int { return 1 << 65; }\n"),
    ("shr-big", "fn main(): int { return 255 >> 100; }\n"),
    ("shl-63", "fn main(): int { return 1 << 63; }\n"),
    ("sar-63", "fn main(): int { return (0 - 1) >> 63; }\n"),
    ("shl-0", "fn main(): int { return 5 << 0; }\n"),
    ("eq-t", "fn main(): int { let b: bool = 1 == 1; return 30; }\n"),
    ("eq-f", "fn main(): int { let b: bool = 1 == 2; return 31; }\n"),
    ("ne-t", "fn main(): int { let b: bool = 1 != 2; return 32; }\n"),
    ("ne-f", "fn main(): int { let b: bool = 1 != 1; return 33; }\n"),
    ("lt-t", "fn main(): int { let b: bool = 1 < 2; return 34; }\n"),
    ("lt-f", "fn main(): int { let b: bool = 2 < 1; return 35; }\n"),
    ("le-t", "fn main(): int { let b: bool = 2 <= 2; return 36; }\n"),
    ("gt-t", "fn main(): int { let b: bool = 3 > 2; return 37; }\n"),
    ("ge-f", "fn main(): int { let b: bool = 2 >= 3; return 38; }\n"),
    ("lt-neg", "fn main(): int { let b: bool = (0 - 1) < 1; return 39; }\n"),
    ("gt-neg", "fn main(): int { let b: bool = (0 - 1) > 1; return 40; }\n"),
    ("overflow-cmp", "fn main(): int { let b: bool = 2147483647 + 1 > 0; return 41; }\n"),
    ("wrap-eq", "fn main(): int { let b: bool = (0 - 1) == 4294967295; return 42; }\n"),
    ("let-one", "fn main(): int { let x: int = 5; return x; }\n"),
    ("let-two", "fn main(): int { let x: int = 5; let y: int = 7; return x + y; }\n"),
    ("let-sqdiff", "fn main(): int { let x: int = 2; let y: int = x * x; return y - x; }\n"),
    ("let-bool-t", "fn main(): int { let b: bool = true; let c: bool = b || false; return 50; }\n"),
    ("let-bool-eq", "fn main(): int { let b: bool = 1 == 2; let c: bool = b && true; return 51; }\n"),
    ("let-use-twice", "fn main(): int { let x: int = 3; return x * x + x; }\n"),
    ("let-16", "fn main(): int { let a0: int = 0; let a1: int = 1; let a2: int = 2; let a3: int = 3; let a4: int = 4; let a5: int = 5; let a6: int = 6; let a7: int = 7; let a8: int = 8; let a9: int = 9; let b0: int = 10; let b1: int = 11; let b2: int = 12; let b3: int = 13; let b4: int = 14; let b5: int = 15; return b5; }\n"),
    ("let-17", "fn main(): int { let a0: int = 0; let a1: int = 1; let a2: int = 2; let a3: int = 3; let a4: int = 4; let a5: int = 5; let a6: int = 6; let a7: int = 7; let a8: int = 8; let a9: int = 9; let b0: int = 10; let b1: int = 11; let b2: int = 12; let b3: int = 13; let b4: int = 14; let b5: int = 15; let b6: int = 16; return b6; }\n"),
    ("nested-paren", "fn main(): int { return ((((43)))); }\n"),
    ("nested-add", "fn main(): int { return 1 + (2 + (3 + (4 + 5))); }\n"),
    ("nested-muladd", "fn main(): int { return ((1 + 2) * (3 + 4)) - 5; }\n"),
    ("block-single", "fn main(): int { { return 7; } }\n"),
    ("exprstmt", "fn main(): int { 1 + 2; return 5; }\n"),
    ("exprstmt-true", "fn main(): int { true; return 3; }\n"),
    ("dead-after-ret", "fn main(): int { return 1; return 2; }\n"),
    ("bool-expr-ret", "fn main(): int { let b: bool = !true || false && true; return 52; }\n"),
    ("mixed-prec", "fn main(): int { return 1 << 3 | 6 & 5 ^ 3; }\n"),
]

REJECT25_CASES = [
    ("div", "fn main(): int { return 1 / 2; }\n"),
    ("mod", "fn main(): int { return 1 % 2; }\n"),
    ("if-stmt", "fn main(): int { if true { return 0; } return 1; }\n"),
    ("while-stmt", "fn main(): int { while true { break; } return 0; }\n"),
    ('match-stmt', 'fn main(): int { let r: status<int> = byte_at("ab", 0); match r { ok(v) => { return v; }, err(e) => { return 1; } } }\n'),
    ('str-let', 'fn main(): int { let s: str = "ab"; return 0; }\n'),
    ("list-let", "fn main(): int { let l: list<int,2> = [1, 2]; return 0; }\n"),
    ("record", "record P { a: int }\nfn main(): int { return 0; }\n"),
    ("params", "fn main(a: int): int { return a; }\n"),
    ("bool-main", "fn main(): bool { return true; }\n"),
    ("unit-ret", "fn main() { return; }\n"),
    ("print-call", "fn main(): int { print(1); return 0; }\n"),
    ("no-main", "fn f(): int { return 0; }\n"),
    ("continue-stmt", "fn main(): int { while true { continue; } return 0; }\n"),
]

REJECT_CHECK_CASES = [
    ("undeclared", "fn main(): int { return x; }\n", "SEM_UNDECLARED"),
    ("dup-let", "fn main(): int { let x: int = 1; let x: int = 2; return 0; }\n", "SEM_DUPLICATE"),
    ("unknown-fn", "fn main(): int { return nosuch(); }\n", "SEM_UNKNOWN_FUNCTION"),
]

TRAP_CASES = [
    ("empty-body", "fn main(): int {}\n"),
]

BOUND_CASES_129 = "fn main(): int { " + " ".join("let v%d: int = %d;" % (i, i) for i in range(129)) + " return v128; }\n"
BOUND_CASES_128 = "fn main(): int { " + " ".join("let v%d: int = %d;" % (i, i) for i in range(128)) + " return v127; }\n"

SIZE_PIN = {}
class BEAcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, ACCEPT_CASES)
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
        self.assertGreaterEqual(len(self.images), 20)

    def test_02_determinism(self):
        again = _run_be(self.combo, ACCEPT_CASES)
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        hexes = [h for _n, h, _s, _w in self.images]
        self.assertEqual(len(set(hexes)), len(hexes))
        exits = set(w for _n, _h, _s, w in self.images)
        self.assertGreater(len(exits), 10)

    def test_04_code_sizes_bounded(self):
        for name, _h, sz, _w in self.images:
            self.assertGreaterEqual(sz, 1)
            self.assertLessEqual(sz, 65536)


class BERejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)
        cls.rchk = _run_be(cls.combo, REJECT_CHECK_CASES)
        cls.rtrap = _run_be(cls.combo, TRAP_CASES)
        cls.rb129 = _run_be(cls.combo, [("frames-129", BOUND_CASES_129)])
        cls.rb128 = _run_be(cls.combo, [("frames-128", BOUND_CASES_128)])

    def test_05_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)

    def test_06_checker_passthrough(self):
        want_code = {"SEM_UNDECLARED": 9, "SEM_DUPLICATE": 10, "SEM_UNKNOWN_FUNCTION": 13}
        for (code, _off, hexstr), case in zip(self.rchk, REJECT_CHECK_CASES):
            name, src = case[0], case[1]
            hr = analyzer.analyze(src, "t.rl", profile="core")
            self.assertFalse(hr.ok)
            self.assertEqual(code, want_code[hr.diagnostic.code])
            self.assertEqual(hexstr, "")

    def test_07_empty_body_traps(self):
        (code, _off, hexstr), (_name, _src) = self.rtrap[0], TRAP_CASES[0]
        self.assertEqual(code, 0)
        raw = bytes.fromhex(hexstr)
        code_bytes, _sz = _parse_rnyx(raw)
        with self.assertRaises(RuntimeError):
            run_image(code_bytes)

    def test_08_frame_bounds(self):
        code, _off, hexstr = self.rb129[0]
        self.assertEqual(code, 26)
        self.assertEqual(hexstr, "")
        code, _off, hexstr = self.rb128[0]
        self.assertEqual(code, 0)
        raw = bytes.fromhex(hexstr)
        code_bytes, _sz = _parse_rnyx(raw)
        self.assertEqual(run_image(code_bytes), 127)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


def _mutated_combo(old, new, count=1):
    return _mut(_combo_text(), old, new, count)


FIXED_RETURN0 = "52594e58020001001c000000000000001a0000000000000000000000e80900000089c3b800000000cd80554889e5b800000000c9c3cc"


class BEMutantTests(unittest.TestCase):
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

    def test_be_m1_const_ignored(self):
        combo = _mutated_combo(
            "  let a1: int = e_le32(v, a0);",
            "  let a1: int = e_le32(0, a0);")
        self.assertTrue(self._red_on(combo, 3))

    def test_be_m2_add_is_sub(self):
        combo = _mutated_combo(
            "fn e_add_rax_rcx(acc: int): int {",
            "fn e_add_rax_rcx(acc: int): int {\n  return e_sub_rax_rcx(acc);",
            count=1)
        self.assertTrue(self._red_on(combo, 15))

    def test_be_m3_size_wrong(self):
        combo = _mutated_combo(
            "fn sz_start(): int {\n  return 14;\n}",
            "fn sz_start(): int {\n  return 15;\n}")
        (code, _off, _hex) = _run_be(combo, [ACCEPT_CASES[3]])[0]
        self.assertEqual(code, 28)

    def test_be_m4_entry_wrong(self):
        combo = _mutated_combo(
            "  let a12: int = e_le32(0, a11);",
            "  let a12: int = e_le32(1, a11);")
        (code, _off, hexstr) = _run_be(combo, [ACCEPT_CASES[3]])[0]
        self.assertEqual(code, 0)
        raw = bytes.fromhex(hexstr)
        ver, arch, hlen, res, entry = struct.unpack("<HHHHI", raw[4:16])
        self.assertNotEqual(entry, 0)

    def test_be_m5_bound_disabled(self):
        combo = _mutated_combo(
            "  if nl <= 128 { } else { return derr(26, f, cs); }",
            "  if nl <= 999999 { } else { return derr(26, f, cs); }")
        (code, _off, _hex) = _run_be(combo, [("frames-129", BOUND_CASES_129)])[0]
        self.assertNotEqual(code, 26)

    def test_be_m6_call_disp_off_by_one(self):
        combo = _mutated_combo(
            "  let disp: int = mainoff - 5;",
            "  let disp: int = mainoff - 4;")
        self.assertTrue(self._red_on(combo, 3))

    def test_be_m7_native_only_pending(self):
        self.skipTest("QEMU/native unavailable: wrong-exit-register mutant needs execution proof")

    def test_be_m8_canned_image(self):
        combo = _mutated_combo(
            "fn be_emit_prog(src: str, f: int): BZ {",
            "fn be_emit_prog(src: str, f: int): BZ {\n"
            "  let s: BZ = be_size_prog(src, f);\n"
            '  print("52594e58020001001c000000000000001a0000000000000000000000e80900000089c3b800000000cd80554889e5b800000000c9c3cc");\n'
            "  return BZ(p: 0, n: s->n, c: 0, o: 0);",
            count=1)
        reds = 0
        for idx in (3, 15, 16):
            if self._red_on(combo, idx):
                reds += 1
        self.assertGreater(reds, 0)


if __name__ == "__main__":
    unittest.main()
