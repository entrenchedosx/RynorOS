"""Stage 19e M6 fjoin/argv status<str> lowering + use elision.

M6 accepts `let s: status<str> = fjoin(dir, rel)` and (in main only)
`let s: status<str> = argv(i)` in the baby backend
(`rynorlang/selfhost/emit.rl`, core dialect), plus zero-byte elision
of `use "path";` statements (the host RIR already drops fn-body
`use`, so elision is oracle-exact). Consumption paths: `let u: str =
unwrap_or(s, default)` (new strx-unwrap route + str-payload gate on
both size/emit sides), `match s` (frozen M2 status path, str payload
needs no M2 change), `is_ok`/`is_err`.

fjoin guards (all forward to err): empty rel, rel[0] == '/',
either operand longer than 32 bytes (FS path cap, selfhost.md
sections 6/22; the oracle joins arbitrary lengths, so the 32/33
bound is a documented guest bound pinned with +-1 evidence, the
same class as the documented stack-exhaustion differential limit).
Empty dir stores rel directly; otherwise dir + '/' + rel are copied
into this call site's private 9-slot frame scratch (72 bytes; at
most 65 land, so the copy never overflows) and (ptr, len) is
stored. Each fjoin site owns its own scratch: two sites in one
function never clobber each other (frame budgeted by be_fn_maxfj).

argv reads argc/argv off the startup stack ([rbp+16]/[rbp+24+8*i]
in main, stage18d-abi E); helpers stay 25. The argv strings live
above the startup RSP, which no frame touches, so the stored
(ptr, len) stays valid. Out-of-range/negative index => (1, 3, "").

New machine forms (all caller-saved, no RBX): movzx eax,[rsi]
(0F B6 04 26), mov [rdi],al (88 07), inc rcx/rsi/rdi + dec rcx
(48 FF C1/C6/C7/C9), mov rax,[rsp+disp8] (48 8B 44 24),
mov rax,[rbp+rcx*8+disp32] (48 8B 84 CD), lea rdi,[rbp+disp32]
(48 8D BD), cmp rcx,rax (48 39 C1), cmp rax,imm32 (48 3D),
mov rsi,rax (48 89 C6), xor ecx,ecx (31 C9), jne rel32 (0F 85).

Out of scope (stay backend-25): fread everywhere; direct
`unwrap_or(fjoin(...))` / `unwrap_or(argv(...))` without a status
let; argv in helpers; fjoin/argv in conditions, call args, or
aggregate positions other than let-status<str>.

QEMU/native proof lives in probe_m6native.py (MATCH at M6 commit),
including argv with real shell-passed args; here execution is proven
by the test-only emulator below extended with exactly the new forms
(it EXECUTES baby bytes; it never generates code). Reference
semantics come from the trusted host oracle (exit low 32 bits;
argv passed through). RYNX extent (`len(raw) == 28 + csz + fsz`)
is asserted per case.
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
STACK_SIZE = bea.STACK_SIZE


class Emu(bes.Emu):
    def __init__(self, code, data=b"", argv=None):
        super().__init__(code, data)
        if argv is not None:
            self._preload_argv(list(argv))

    def _preload_argv(self, argv):
        # Mirror stage18d-abi E: RSP0 = TOP - block; [RSP0] = argc;
        # [RSP0+8+8*i] = argv[i]; NULL terminator; NUL-terminated
        # strings above the array, all below STACK_TOP.
        enc = [a.encode("utf-8") for a in argv]
        n = len(enc)
        rsp0 = STACK_TOP - 512
        base = STACK_TOP - STACK_SIZE
        self._swrite(rsp0, 8, n)
        stop = rsp0 + 8 + 8 * n
        self._swrite(stop, 8, 0)
        p = stop + 8
        for i, b in enumerate(enc):
            self._swrite(rsp0 + 8 + 8 * i, 8, p)
            assert p + len(b) + 1 <= STACK_TOP, "argv overflow"
            self.mem[p - base:p - base + len(b)] = b
            self.mem[p - base + len(b)] = 0
            p += len(b) + 1
        self.reg["rsp"] = rsp0
        self._rsp0 = rsp0

    def _mread_byte(self, addr):
        if DATA_BASE <= addr < DATA_BASE + len(self.data):
            return self.data[addr - DATA_BASE]
        if STACK_TOP - STACK_SIZE <= addr < STACK_TOP:
            return self.mem[addr - (STACK_TOP - STACK_SIZE)]
        raise RuntimeError(f"bad byte read {addr:#x}")

    def _flags_inc(self, a):
        r = (a + 1) & MASK64
        self.zf = 1 if r == 0 else 0
        self.sf = 1 if r >= (1 << 63) else 0
        self.of = 1 if a == (1 << 63) - 1 else 0
        return r

    def _flags_dec(self, a):
        r = (a - 1) & MASK64
        self.zf = 1 if r == 0 else 0
        self.sf = 1 if r >= (1 << 63) else 0
        self.of = 1 if a == (1 << 63) else 0
        return r

    def step(self):
        off = self.rip - CODE_BASE
        b0 = self.code[off]
        R = self.reg
        if b0 == 0x56:
            self._fetch(1)
            self._push(R["rsi"])
            return
        if b0 == 0x52:
            self._fetch(1)
            self._push(R["rdx"])
            return
        if b0 == 0x31:
            self._fetch(1)
            b1 = self._fetch(1)[0]
            assert b1 == 0xC9, f"bad xor {b1:#x}"
            R["rcx"] = 0
            self.cf = 0
            self.of = 0
            self.zf = 1
            self.sf = 0
            return
        if b0 == 0x88:
            self._fetch(1)
            b1 = self._fetch(1)[0]
            assert b1 == 0x07, f"bad mov-r8 {b1:#x}"
            self._swrite(R["rdi"] & MASK64, 1, R["rax"] & 0xFF)
            return
        if b0 == 0x0F:
            b1 = self.code[off + 1]
            if b1 == 0xB6 and self.code[off + 2] == 0x04 and self.code[off + 3] == 0x26:
                self._fetch(4)
                R["rax"] = self._mread_byte(R["rsi"] & MASK64)
                return
            if b1 == 0xB6 and self.code[off + 2] == 0x04 and self.code[off + 3] == 0x06:
                self._fetch(4)
                R["rax"] = self._mread_byte((R["rsi"] + R["rax"]) & MASK64)
                return
            if b1 == 0x85:
                self._fetch(2)
                disp = int.from_bytes(self._fetch(4), "little", signed=True)
                if not self.zf:
                    self.rip = (self.rip + disp) & MASK64
                return
        super().step()

    def _rex(self):
        off = self.rip - CODE_BASE
        b1 = self.code[off]
        R = self.reg
        if b1 == 0x3D:
            self._fetch(1)
            imm = int.from_bytes(self._fetch(4), "little", signed=True)
            self._flags_sub(R["rax"], imm & MASK64)
            return
        if b1 == 0x39 and self.code[off + 1] == 0xC1:
            self._fetch(2)
            self._flags_sub(R["rcx"], R["rax"])
            return
        if b1 == 0x89 and self.code[off + 1] == 0xC6:
            self._fetch(2)
            R["rsi"] = R["rax"]
            return
        if b1 == 0x8B and self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            R["rax"] = self._sread((R["rsp"] + d) & MASK64, 8)
            return
        if b1 == 0x8B and self.code[off + 1] == 0x84 and self.code[off + 2] == 0xCD:
            self._fetch(3)
            d = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rax"] = self._sread((R["rbp"] + R["rcx"] * 8 + d) & MASK64, 8)
            return
        if b1 == 0x8D and self.code[off + 1] == 0xBD:
            self._fetch(2)
            d = int.from_bytes(self._fetch(4), "little", signed=True)
            R["rdi"] = (R["rbp"] + d) & MASK64
            return
        if b1 == 0xFF:
            self._fetch(1)
            b2 = self._fetch(1)[0]
            if b2 == 0xC1:
                R["rcx"] = self._flags_inc(R["rcx"])
                return
            if b2 == 0xC6:
                R["rsi"] = self._flags_inc(R["rsi"])
                return
            if b2 == 0xC7:
                R["rdi"] = self._flags_inc(R["rdi"])
                return
            if b2 == 0xC9:
                R["rcx"] = self._flags_dec(R["rcx"])
                return
            raise RuntimeError(f"bad rexFF {b2:#x}")
        super()._rex()


def _oracle_run(src, argv=None):
    result = analyzer.analyze(src, "t.rl", profile="core")
    assert result.ok, result.diagnostic
    module, error = rir.build_rir(result.ast, "t.rl")
    assert error is None, error
    emitted = []
    outcome = oracle.run_rir(module, out=emitted, argv=argv)
    assert outcome["trapped"] is None, outcome
    assert outcome["exit"] is not None
    writes = []
    for e in emitted:
        writes.append(e if isinstance(e, bytes) else str(e).encode("utf-8"))
    return outcome["exit"] & 0xFFFFFFFF, b"".join(writes)


def run_image_data(raw: bytes, argv=None):
    ver, arch, hlen, res, entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
    assert (ver, arch, hlen, res, entry) == (2, 1, 28, 0, 0)
    assert fsz == msz, (fsz, msz)
    assert 1 <= csz <= 65536 and 0 <= fsz <= 32768, (csz, fsz)
    code, data = raw[28:28 + csz], raw[28 + csz:28 + csz + fsz]
    assert len(code) == csz and len(data) == fsz and len(raw) == 28 + csz + fsz
    em = Emu(code, data, argv=argv)
    out = em.run()
    want_rsp = getattr(em, "_rsp0", STACK_TOP)
    assert em.reg["rsp"] == want_rsp, f"RSP imbalance {em.reg['rsp']:#x}"
    return out, bytes(em.stdout)


def _combo_text(extra=""):
    return bea._combo_text(extra)


def _run_be(combo_src, cases):
    return bea._run_be(combo_src, cases)


_L32 = "a" * 32
_L33 = "b" * 33

ACCEPT_CASES = [
    ("m6-fjoin-basic", 'fn main(): int { let s: status<str> = fjoin("a", "b"); let u: str = unwrap_or(s, "d"); return len(u); }\n'),
    ("m6-fjoin-empty-dir", 'fn main(): int { let s: status<str> = fjoin("", "xy"); let u: str = unwrap_or(s, "d"); return len(u); }\n'),
    ("m6-fjoin-empty-rel", 'fn main(): int { let s: status<str> = fjoin("a", ""); let u: str = unwrap_or(s, "dflt"); return len(u); }\n'),
    ("m6-fjoin-abs-rel", 'fn main(): int { let s: status<str> = fjoin("a", "/x"); let u: str = unwrap_or(s, "d"); return len(u); }\n'),
    ("m6-fjoin-vars", 'fn main(): int { let d: str = "p"; let r: str = "q"; let s: status<str> = fjoin(d, r); let u: str = unwrap_or(s, "d"); return len(u); }\n'),
    ("m6-fjoin-content", 'fn main(): int { let s: status<str> = fjoin("ab", "cd"); let u: str = unwrap_or(s, "d"); let b0: int = unwrap_or(byte_at(u, 0), 0); let b2: int = unwrap_or(byte_at(u, 2), 0); let b4: int = unwrap_or(byte_at(u, 4), 0); return len(u) + b0 + b2 + b4; }\n'),
    ("m6-fjoin-two-sites", 'fn main(): int { let a: status<str> = fjoin("a", "b"); let b: status<str> = fjoin("cc", "d"); let ua: str = unwrap_or(a, "x"); let ub: str = unwrap_or(b, "y"); return len(ua) + len(ub) * 10; }\n'),
    ("m6-fjoin-32-ok", 'fn main(): int { let s: status<str> = fjoin("' + _L32 + '", "b"); let u: str = unwrap_or(s, "d"); return len(u); }\n'),
    ("m6-fjoin-33-dir-err", 'fn main(): int { let s: status<str> = fjoin("' + _L33 + '", "b"); let u: str = unwrap_or(s, "dflt"); return len(u); }\n'),
    ("m6-fjoin-33-rel-err", 'fn main(): int { let s: status<str> = fjoin("a", "' + _L33 + '"); let u: str = unwrap_or(s, "dflt"); return len(u); }\n'),
    ("m6-fjoin-match-ok", 'fn main(): int { let s: status<str> = fjoin("a", "b"); match s { ok(v) => { return len(v); }, err(e) => { return e; } } }\n'),
    ("m6-fjoin-match-err", 'fn main(): int { let s: status<str> = fjoin("a", ""); match s { ok(v) => { return len(v); }, err(e) => { return e; } } }\n'),
    ("m6-fjoin-isok", 'fn main(): int { let s: status<str> = fjoin("a", "b"); if is_ok(s) { return 1; } return 0; }\n'),
    ("m6-fjoin-iserr", 'fn main(): int { let s: status<str> = fjoin("a", ""); if is_err(s) { return 1; } return 0; }\n'),
    ("m6-fjoin-helper", 'fn h(d: str, r: str): int { let s: status<str> = fjoin(d, r); let u: str = unwrap_or(s, "e"); return len(u); }\nfn main(): int { return h("a", "b"); }\n'),
    ("m6-use-basic", 'fn main(): int { use "m"; return 7; }\n'),
    ("m6-use-fjoin", 'fn main(): int { use "m"; let s: status<str> = fjoin("a", "b"); let u: str = unwrap_or(s, "d"); return len(u); }\n'),
    ("m6-usevar-expr", 'fn main(): int { let use: int = 3; return use + 4; }\n'),
    ("m6-argv-1", 'fn main(): int { let s: status<str> = argv(1); let u: str = unwrap_or(s, "dd"); return len(u); }\n'),
    ("m6-argv-0", 'fn main(): int { let s: status<str> = argv(0); let u: str = unwrap_or(s, "dd"); return len(u); }\n'),
    ("m6-argv-oob", 'fn main(): int { let s: status<str> = argv(1); let u: str = unwrap_or(s, "dd"); return len(u); }\n'),
    ("m6-argv-neg", 'fn main(): int { let s: status<str> = argv(0 - 1); let u: str = unwrap_or(s, "dd"); return len(u); }\n'),
    ("m6-argv-empty", 'fn main(): int { let s: status<str> = argv(1); let u: str = unwrap_or(s, "dd"); return len(u); }\n'),
    ("m6-argv-expr", 'fn main(): int { let i: int = 1; let s: status<str> = argv(i); let u: str = unwrap_or(s, "dd"); return len(u); }\n'),
    ("m6-argv-content", 'fn main(): int { let s: status<str> = argv(1); let u: str = unwrap_or(s, "dd"); let b0: int = unwrap_or(byte_at(u, 0), 0); let b1: int = unwrap_or(byte_at(u, 1), 0); return len(u) + b0 + b1; }\n'),
    ("m6-argv-match", 'fn main(): int { let s: status<str> = argv(1); match s { ok(v) => { return len(v); }, err(e) => { return e; } } }\n'),
    ("m6-argv-argc0", 'fn main(): int { let s: status<str> = argv(0); let u: str = unwrap_or(s, "dd"); return len(u); }\n'),
]

ARGV = {
    "m6-argv-1": ["prog", "hello"],
    "m6-argv-0": ["prog", "hello"],
    "m6-argv-oob": ["prog"],
    "m6-argv-neg": ["prog"],
    "m6-argv-empty": ["prog", ""],
    "m6-argv-expr": ["prog", "xyz"],
    "m6-argv-content": ["prog", "hi"],
    "m6-argv-match": ["prog", "q"],
    "m6-argv-argc0": [],
}

REJECT25_CASES = [
    ("fread-let", 'fn main(): int { let s: status<str> = fread("f", 0, 1); return 0; }\n'),
    ("fread-unwrap-direct", 'fn main(): int { let u: str = unwrap_or(fread("f", 0, 1), "d"); return len(u); }\n'),
    ("fread-cond", 'fn main(): int { if is_ok(fread("f", 0, 1)) { return 1; } return 0; }\n'),
    ("direct-unwrap-fjoin", 'fn main(): int { let u: str = unwrap_or(fjoin("a", "b"), "d"); return len(u); }\n'),
    ("direct-unwrap-argv", 'fn main(): int { let u: str = unwrap_or(argv(1), "d"); return len(u); }\n'),
    ("helper-argv", 'fn h(x: int): int { let s: status<str> = argv(x); let u: str = unwrap_or(s, "d"); return len(u); }\nfn main(): int { return h(1); }\n'),
    ("fjoin-callarg", 'fn h(x: str): int { return len(x); }\nfn main(): int { return h(unwrap_or(fjoin("a", "b"), "d")); }\n'),
    ("fjoin-cond", 'fn main(): int { if is_ok(fjoin("a", "b")) { return 1; } return 0; }\n'),
    ("use-bad-empty", 'fn main(): int { use ""; return 7; }\n'),
    ("use-bad-abs", 'fn main(): int { use "/a"; return 7; }\n'),
    ("use-bad-long", 'fn main(): int { use "' + _L33 + '"; return 7; }\n'),
]

# Documented guest bound: operands longer than 32 bytes take the err
# arm (exit 4 via the "dflt" default) while the oracle joins them
# (exit 35). Both sides are pinned: the guest bound AND the oracle
# divergence (if the host ever caps fjoin, this pin fails loudly and
# the contract gets revisited instead of silently changing).
BOUND_CASES = {
    "m6-fjoin-33-dir-err": (4, 35),
    "m6-fjoin-33-rel-err": (4, 35),
}


def _insn_len(code: bytes, i: int):
    n = len(code)

    def need(k):
        if i + k > n:
            raise AssertionError(f"truncated decode at {i:#x}")

    b0 = code[i]
    if b0 in (0x50, 0x51, 0x52, 0x56, 0x58, 0x59, 0x5A, 0x5E, 0x5F, 0x55, 0x5D, 0xC3, 0xC9, 0xCC):
        return i + 1
    if b0 == 0xCD:
        need(2)
        return i + 2
    if 0xB8 <= b0 <= 0xBF:
        need(5)
        return i + 5
    if b0 == 0xE8 or b0 == 0xE9:
        need(5)
        return i + 5
    if b0 == 0x85:
        need(2)
        assert code[i + 1] == 0xC0, f"bad test at {i:#x}"
        return i + 2
    if b0 == 0x31:
        need(2)
        assert code[i + 1] == 0xC9, f"bad xor at {i:#x}"
        return i + 2
    if b0 == 0x88:
        need(2)
        assert code[i + 1] == 0x07, f"bad mov-r8 at {i:#x}"
        return i + 2
    if b0 == 0x89:
        need(2)
        b1 = code[i + 1]
        if b1 in (0xC3, 0xC1, 0xE5, 0xF1):
            return i + 2
        if b1 == 0x45:
            need(3)
            return i + 3
        if b1 == 0x85:
            need(6)
            return i + 6
        raise AssertionError(f"bad 89 {b1:#x} at {i:#x}")
    if b0 == 0x8B:
        need(2)
        b1 = code[i + 1]
        if b1 == 0x45:
            need(3)
            return i + 3
        if b1 == 0x85:
            need(6)
            return i + 6
        raise AssertionError(f"bad 8b {b1:#x} at {i:#x}")
    if b0 == 0x0F:
        need(2)
        b1 = code[i + 1]
        if b1 in (0x84, 0x85, 0x83, 0x88):
            need(6)
            return i + 6
        if b1 in (0x94, 0x95, 0x9C, 0x9E, 0x9F, 0x9D):
            need(3)
            assert code[i + 2] == 0xC0, f"bad setcc at {i:#x}"
            return i + 3
        if b1 == 0xB6:
            need(3)
            b2 = code[i + 2]
            if b2 == 0xC0:
                return i + 3
            if b2 == 0x04:
                need(4)
                assert code[i + 3] in (0x06, 0x26), f"bad movzx sib at {i:#x}"
                return i + 4
            raise AssertionError(f"bad 0fb6 {b2:#x} at {i:#x}")
        if b1 == 0xAF:
            need(3)
            assert code[i + 2] == 0xC1, f"bad imul at {i:#x}"
            return i + 3
        raise AssertionError(f"bad 0f {b1:#x} at {i:#x}")
    if b0 == 0x48:
        need(2)
        b1 = code[i + 1]
        if b1 == 0xB8:
            need(10)
            return i + 10
        if b1 == 0x3D:
            need(6)
            return i + 6
        if b1 == 0x89:
            need(3)
            b2 = code[i + 2]
            if b2 in (0xE5, 0xC1, 0xC6):
                return i + 3
            if b2 in (0x45, 0x4D, 0x55, 0x75, 0x7D, 0x47):
                need(4)
                return i + 4
            if b2 in (0x85, 0x87):
                need(7)
                return i + 7
            if b2 == 0x06:
                return i + 3
            raise AssertionError(f"bad rex89 {b2:#x} at {i:#x}")
        if b1 == 0x8B:
            need(3)
            b2 = code[i + 2]
            if b2 in (0x45, 0x7D):
                need(4)
                return i + 4
            if b2 in (0x85, 0x8D):
                need(7)
                return i + 7
            if b2 == 0x06:
                return i + 3
            if b2 in (0x44, 0x7C, 0x74, 0x54, 0x4C):
                need(4)
                assert code[i + 3] == 0x24, f"bad sib at {i:#x}"
                need(5)
                return i + 5
            if b2 == 0x84:
                need(4)
                assert code[i + 3] == 0xCD, f"bad sib at {i:#x}"
                need(8)
                return i + 8
            raise AssertionError(f"bad rex8b {b2:#x} at {i:#x}")
        if b1 == 0x8D:
            need(3)
            b2 = code[i + 2]
            if b2 in (0x85, 0xBD, 0xB5):
                need(7)
                return i + 7
            raise AssertionError(f"bad lea {b2:#x} at {i:#x}")
        if b1 in (0x01, 0x29):
            need(3)
            assert code[i + 2] in (0xC8, 0xC6), f"bad alu at {i:#x}"
            return i + 3
        if b1 == 0x87:
            need(4)
            assert code[i + 2] == 0x04, f"bad xchg at {i:#x}"
            assert code[i + 3] == 0x24, f"bad xchg sib at {i:#x}"
            return i + 4
        if b1 == 0x39:
            need(3)
            assert code[i + 2] in (0xC8, 0xC1), f"bad cmp at {i:#x}"
            return i + 3
        if b1 in (0x21, 0x09, 0x31):
            need(3)
            assert code[i + 2] == 0xC8, f"bad logic at {i:#x}"
            return i + 3
        if b1 == 0x85:
            need(3)
            assert code[i + 2] == 0xC0, f"bad test at {i:#x}"
            return i + 3
        if b1 == 0x83:
            need(3)
            b2 = code[i + 2]
            if b2 in (0xC0, 0xC4, 0xEC, 0xEE):
                need(4)
                return i + 4
            raise AssertionError(f"bad rex83 {b2:#x} at {i:#x}")
        if b1 == 0xC7:
            need(3)
            assert code[i + 2] == 0xC0, f"bad c7 at {i:#x}"
            need(7)
            return i + 7
        if b1 == 0x81:
            need(3)
            b2 = code[i + 2]
            if b2 in (0xEC, 0xC4):
                need(7)
                return i + 7
            raise AssertionError(f"bad rex81 {b2:#x} at {i:#x}")
        if b1 == 0x69:
            need(3)
            assert code[i + 2] == 0xC0, f"bad imul-imm at {i:#x}"
            need(7)
            return i + 7
        if b1 == 0xF7:
            need(3)
            assert code[i + 2] in (0xD8, 0xD0), f"bad f7 at {i:#x}"
            return i + 3
        if b1 == 0xFF:
            need(3)
            assert code[i + 2] in (0xC1, 0xC6, 0xC7, 0xC9), f"bad rexFF at {i:#x}"
            return i + 3
        if b1 == 0x0F:
            need(3)
            b2 = code[i + 2]
            if b2 == 0xAF:
                need(4)
                assert code[i + 3] == 0xC1, f"bad imul at {i:#x}"
                return i + 4
            if b2 in (0x94, 0x95, 0x9C, 0x9E, 0x9F, 0x9D, 0xB6):
                need(4)
                assert code[i + 3] == 0xC0, f"bad setcc/movzx at {i:#x}"
                return i + 4
            raise AssertionError(f"bad rex0f {b2:#x} at {i:#x}")
        raise AssertionError(f"bad rex48 {b1:#x} at {i:#x}")
    if b0 == 0x4C:
        need(3)
        if code[i + 1] == 0x8B:
            assert code[i + 2] in (0x44, 0x4C), f"bad rex4c modrm {code[i + 2]:#x} at {i:#x}"
            assert code[i + 3] == 0x24, f"bad rex4c sib at {i:#x}"
            need(5)
            return i + 5
        if code[i + 1] == 0x89:
            assert code[i + 2] in (0x45, 0x4D), f"bad rex4c spill {code[i + 2]:#x} at {i:#x}"
            need(4)
            return i + 4
        raise AssertionError(f"bad rex4c {code[i + 1]:#x} at {i:#x}")
    raise AssertionError(f"unknown opcode {b0:#x} at {i:#x}")


def _sweep_starts(code: bytes):
    starts = set()
    i, n = 0, len(code)
    while i < n:
        starts.add(i)
        i = _insn_len(code, i)
    return starts


class M6AcceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.results = _run_be(cls.combo, ACCEPT_CASES)
        cls.images = []
        for (code, off, hexstr), (name, src) in zip(cls.results, ACCEPT_CASES):
            assert code == 0, (name, code, off)
            raw = bytes.fromhex(hexstr)
            argv = ARGV.get(name)
            want_exit, want_out = _oracle_run(src, argv)
            got_exit, got_out = run_image_data(raw, argv)
            if name in BOUND_CASES:
                want_guest, want_oracle = BOUND_CASES[name]
                assert got_exit == want_guest, (name, got_exit, want_guest)
                assert want_exit == want_oracle, (name, want_exit, want_oracle)
            else:
                assert got_exit == want_exit, (name, got_exit, want_exit)
            assert got_out == want_out, (name, got_out, want_out)
            cls.images.append((name, hexstr, want_out, got_exit))

    def test_01_exit_behavior(self):
        self.assertGreaterEqual(len(self.images), 20)

    def test_02_determinism(self):
        again = _run_be(self.combo, ACCEPT_CASES)
        first = [(c, o, h) for c, o, h in self.results]
        self.assertEqual([(c, o, h) for c, o, h in again], first)

    def test_03_anti_canned(self):
        # (program, argv-input) pairs are all distinct: three argv(1)
        # cases share one image by construction (same source, three
        # argv vectors), and the argc0/argv-0 pair shares another;
        # their exits still vary with the input vector.
        keys = [(h, tuple(ARGV.get(n, ()))) for n, h, _w, _e in self.images]
        self.assertEqual(len(set(keys)), len(keys))
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
        self.assertEqual(by_name["m6-fjoin-basic"], 3)
        self.assertEqual(by_name["m6-fjoin-empty-dir"], 2)
        self.assertEqual(by_name["m6-fjoin-empty-rel"], 4)
        self.assertEqual(by_name["m6-fjoin-abs-rel"], 1)
        self.assertEqual(by_name["m6-fjoin-vars"], 3)
        self.assertEqual(by_name["m6-fjoin-content"], 5 + 97 + 47 + 100)
        self.assertEqual(by_name["m6-fjoin-two-sites"], 3 + 4 * 10)
        self.assertEqual(by_name["m6-fjoin-32-ok"], 34)
        self.assertEqual(by_name["m6-fjoin-33-dir-err"], 4)
        self.assertEqual(by_name["m6-fjoin-33-rel-err"], 4)
        self.assertEqual(by_name["m6-fjoin-match-ok"], 3)
        self.assertEqual(by_name["m6-fjoin-match-err"], 3)
        self.assertEqual(by_name["m6-fjoin-isok"], 1)
        self.assertEqual(by_name["m6-fjoin-iserr"], 1)
        self.assertEqual(by_name["m6-fjoin-helper"], 3)
        self.assertEqual(by_name["m6-use-basic"], 7)
        self.assertEqual(by_name["m6-use-fjoin"], 3)
        self.assertEqual(by_name["m6-usevar-expr"], 7)
        self.assertEqual(by_name["m6-argv-1"], 5)
        self.assertEqual(by_name["m6-argv-0"], 4)
        self.assertEqual(by_name["m6-argv-oob"], 2)
        self.assertEqual(by_name["m6-argv-neg"], 2)
        self.assertEqual(by_name["m6-argv-empty"], 0)
        self.assertEqual(by_name["m6-argv-expr"], 3)
        self.assertEqual(by_name["m6-argv-content"], 2 + 104 + 105)
        self.assertEqual(by_name["m6-argv-match"], 1)
        self.assertEqual(by_name["m6-argv-argc0"], 2)

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

    def test_08_branch_targets_on_boundaries(self):
        # G4 lesson, enforced: every static branch/call displacement
        # lands on a swept instruction boundary inside the code
        # segment; every call lands on a 0x55 prologue. The sweep is
        # fail-closed (unknown opcode raises, it never skips).
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            starts = _sweep_starts(code)
            for s in sorted(starts):
                b0 = code[s]
                if b0 == 0xE8 or b0 == 0xE9:
                    disp = int.from_bytes(code[s + 1:s + 5], "little", signed=True)
                    target = s + 5 + disp
                    self.assertIn(target, starts, f"{name} branch@{s:#x}->{target:#x}")
                    if b0 == 0xE8:
                        self.assertEqual(code[target], 0x55, f"{name} call@{s:#x}")
                elif b0 == 0x0F and code[s + 1] in (0x84, 0x85, 0x83, 0x88):
                    disp = int.from_bytes(code[s + 2:s + 6], "little", signed=True)
                    target = s + 6 + disp
                    self.assertIn(target, starts, f"{name} jcc@{s:#x}->{target:#x}")

    def test_09_start_shape_pinned(self):
        # argv(i) reads [rbp+16]/argv on the strength of _start being
        # a single call: pin the call-only shape (E8 ... 89 C3 B8 ..
        # CD 80, 14 bytes) so any _start change re-audits argv.
        for name, hexstr, _w, _e in self.images:
            raw = bytes.fromhex(hexstr)
            _ver, _arch, _hlen, _res, _entry, csz, fsz, _msz = struct.unpack("<HHHHIIII", raw[4:28])
            code = raw[28:28 + csz]
            self.assertEqual(code[0], 0xE8, name)
            self.assertEqual(bytes(code[5:7]), bytes([0x89, 0xC3]), name)
            self.assertEqual(code[7], 0xB8, name)
            self.assertEqual(bytes(code[12:14]), bytes([0xCD, 0x80]), name)


class M6RejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()
        cls.r25 = _run_be(cls.combo, REJECT25_CASES)

    def test_10_unsupported_subset(self):
        for (code, _off, hexstr), (name, _src) in zip(self.r25, REJECT25_CASES):
            self.assertEqual(code, 25, name)
            self.assertEqual(hexstr, "", name)


def _mut(base, old, new, count=1):
    assert base.count(old) == count, (old[:80], base.count(old))
    return base.replace(old, new)


class M6MutantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.combo = _combo_text()

    def _red_on(self, mutant_combo, idx):
        name, src = ACCEPT_CASES[idx]
        argv = ARGV.get(name)
        if name in BOUND_CASES:
            want_exit = BOUND_CASES[name][0]
        else:
            want_exit, _w = _oracle_run(src, argv)
        try:
            (code, _off, hexstr) = _run_be(mutant_combo, [(name, src)])[0]
        except (AssertionError, RuntimeError):
            return True
        if code != 0:
            return True
        raw = bytes.fromhex(hexstr)
        try:
            got_exit, _g = run_image_data(raw, argv)
        except (RuntimeError, AssertionError):
            return True
        return got_exit != want_exit

    def _idx(self, name):
        for i, (n, _s) in enumerate(ACCEPT_CASES):
            if n == name:
                return i
        raise AssertionError(name)

    def test_m6_m1_empty_rel_check_inverted(self):
        combo = _mut(_combo_text(),
                     "  let a2: int = e_jcc_z(d1, a1);",
                     "  let a2: int = e_jcc_nz(be_rel32(0, 0, sz_jcc_nz()), a1);")
        self.assertTrue(self._red_on(combo, self._idx("m6-fjoin-empty-rel")))

    def test_m6_m2_err_tag_zeroed(self):
        combo = _mut(_combo_text(),
                     "  let a4: int = e_mov_rax_imm(1, a3);",
                     "  let a4: int = e_mov_rax_imm(0, a3);")
        self.assertTrue(self._red_on(combo, self._idx("m6-fjoin-empty-rel")))

    def test_m6_m3_copy_loop_exits_early(self):
        combo = _mut(_combo_text(),
                     "  let a5: int = e_jnz(be_rel32(head, a4, sz_jnz()), a4);",
                     "  let a5: int = e_jcc_z(be_rel32(head, a4, sz_jcc()), a4);")
        self.assertTrue(self._red_on(combo, self._idx("m6-fjoin-content")))

    def test_m6_m4_argv_bound_compare_dropped(self):
        combo = _mut(_combo_text(),
                     "  let a4: int = e_cmp_rcx_rax(a3);",
                     "  let a4: int = a3;")
        self.assertTrue(self._red_on(combo, self._idx("m6-argv-1")))

    def test_m6_m5_strlen_count_dropped(self):
        combo = _mut(_combo_text(),
                     "  let a8: int = e_inc_rcx(a7);",
                     "  let a8: int = a7;")
        self.assertTrue(self._red_on(combo, self._idx("m6-argv-content")))

    def test_m6_m6_loop_size_short(self):
        combo = _mut(_combo_text(),
                     "  return sz_movzx_eax_rsi() + sz_store_al_rdi() + sz_inc_rsi() + sz_inc_rdi() + sz_dec_rcx() + sz_jnz();",
                     "  return sz_movzx_eax_rsi() + sz_store_al_rdi() + sz_inc_rsi() + sz_inc_rdi() + sz_jnz();")
        self.assertTrue(self._red_on(combo, self._idx("m6-fjoin-basic")))


if __name__ == "__main__":
    unittest.main()

