"""Stage 19e M3 scalar print lowering (int/bool/str-var).

M3 lowers `print` of scalar int/bool/str arguments. The frozen
oracle (interp.py run_rir: rt_print_* emission, no newline) renders
exactly one argument: int as signed decimal, bool as true/false,
str as raw (ptr,len) bytes (empty prints nothing). The baby has no
runtime object and no linker (selfhost.md C1/C3 rejected), so the
guest backend INLINES the rt_rynor write sequence (EAX=2 write,
EBX=1 stdout; syscall-abi.md: no other fd): the exact existing
e_print_lit form. Int/bool build decimal/word bytes in a 48-byte
caller-stack scratch (net-zero sub/add, 16-aligned); str-var prints
(ptr,len) straight from the 2-word home, skipping the syscall when
empty (rt_rynor .done guard). Only caller-saved regs (rax/rcx/rdx);
RBX via imm32 like every existing print; rbp homes only read.

Scope (stay backend-25): print of aggregates (records/lists/status/
results print via projections per the selfhost corpus rule;
print_agg machinery stays out), print of calls/unknown shapes.

QEMU/native proof lives in probe_m3native.py (MATCH at M3 commit);
here execution is proven by the test-only emulator chain extended
below with exactly the new machine forms (it EXECUTES baby bytes;
it never generates code). Reference semantics come from the trusted
host oracle (exit low 32 bits + stdout bytes). RYNX extent
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
STACK_SIZE = bea.STACK_SIZE
STACK_BASE = STACK_TOP - STACK_SIZE
DATA_BASE = 0x600000


class Emu(bes.Emu):
    def __init__(self, code, data=b""):
        super().__init__(code)
        self.data = bytes(data)
        self.stdout = bytearray()

    def step(self):
        off = self.rip - CODE_BASE
        # M3 int80: CD 80 with EAX==2 (write: EBX=fd 1, ECX=buf,
        # EDX=len) appends guest bytes to test stdout; EAX==0 exits
        # with EBX. (The data-suite layer below only serves writes
        # over the data segment, so this layer claims the int80
        # first; the stackargs layer between passes CD through
        # untouched.) EAX carries the FULL 64-bit rax (the backend
        # loads it with mov eax,imm = B8, which zeroes the high half;
        # masking with 0xFFFFFFFF would ALSO work, but =0/=2 reads
        # the register the kernel reads).
        if self.code[off:off + 2] == bytes([0xCD, 0x80]):
            self._fetch(2)
            R = self.reg
            if R["rax"] == 0:
                self.exited = R["rbx"] & 0xFFFFFFFF
                return
            if R["rax"] == 2:
                if R["rbx"] != 1:
                    raise RuntimeError(f"write to fd {R['rbx']}")
                # NOTE: the write-number check MUST come first (rax==2
                # selects the write path); the buffer is ECX (low half
                # of the guest rcx) and the length is EDX (low half of
                # the guest rdx). The baby backend builds buf in rsi
                # and moves it with mov ecx,esi (89 F1) and len in rdx
                # (sub rdx,rsi / mov edx,imm), so ecx/rdx low halves
                # carry buf/len exactly like e_print_lit's imm32 forms.
                # Two windows serve writes: the guest stack window
                # [STACK_BASE, STACK_TOP) for int/bool scratch bytes,
                # and the data segment [DATA_BASE, DATA_BASE+len) for
                # str-lit bytes (the str-var xchg path can carry a
                # data-segment pointer when the arg came from a lit).
                # The write CLOBBERS rax (kernel returns count in
                # RAX): model it (count = len), because later code
                # (template epilogues) can test/mov rax and the flags
                # must reflect the real machine.
                buf, ln = R["rcx"] & 0xFFFFFFFF, R["rdx"] & 0xFFFFFFFF
                if STACK_BASE <= buf and buf + ln <= STACK_TOP:
                    for i in range(ln):
                        self.stdout.append(self._sread(buf + i, 1))
                elif DATA_BASE <= buf and buf + ln <= DATA_BASE + len(self.data):
                    self.stdout += self.data[buf - DATA_BASE:buf - DATA_BASE + ln]
                else:
                    raise RuntimeError(f"write out of range {buf:#x}+{ln}")
                R["rax"] = ln
                return
            raise RuntimeError(f"unsupported syscall {R['rax']}")
        # M3 bare opcodes dispatched here (no REX prefix): push rsi
        # (56, caller-saved save alongside push rcx 51), pop rdx
        # (5A, claimed before lower layers read it as an arg-pop),
        # pop rsi (5E, claimed before the calls-suite layer, which
        # reads 5E as an arg-pop), mov eax/ebx/ecx/edx,imm32
        # (B8/BB/B9/BA: bare, no REX, zero the high half), test
        # eax,eax (85 C0), jmp rel32 (E9), jz/jne/jns rel32 (0F 8x),
        # mov ecx,[rsp+ib8] (8B, legacy v1), sub/add rsp,48, xor
        # edx,edx (31 D2), pop rcx (59), mov ecx,esi (89 F1), and
        # the C6/C7 stores.
        # push rsi: 56 (save caller-saved rsi around the template).
        if self.code[off:off + 1] == bytes([0x56]):
            self._fetch(1)
            self._push(self.reg["rsi"])
            return
        # pop rdx: 5A (str len off the machine stack; same one-byte
        # pop class as pop rcx/rsi/rax, claimed here before lower
        # layers read it as an arg-pop).
        if self.code[off:off + 1] == bytes([0x5A]):
            self._fetch(1)
            self.reg["rdx"] = self._pop()
            return
        # pop rsi: 5E (restore caller-saved rsi; claimed here before
        # the calls-suite layer, which reads 5E as an arg-pop).
        if self.code[off:off + 1] == bytes([0x5E]):
            self._fetch(1)
            self.reg["rsi"] = self._pop()
            return
        # jmp rel32: E9 cd (MUST precede the 80 C2 digit-add arm: a
        # jmp disp can contain bytes that look like 80 C2 at a LATER
        # offset, and more importantly the zero image's jmp at 0x9b
        # (E9 26 00 00 00) must dispatch as a branch, not fall to the
        # base "bad opcode". Single-byte E9 match first).
        if self.code[off:off + 1] == bytes([0xE9]):
            self._fetch(1)
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            self.rip = (self.rip + disp) & MASK64
            return
        # xor edx,edx: 31 D2 (no REX: 32-bit form zeroes rdx).
        if self.code[off:off + 2] == bytes([0x31, 0xD2]):
            self._fetch(2)
            self.reg["rdx"] = self._flags_logic(0)
            return
        # add dl,imm8: 80 C2 ib8 (digit ASCII; bare, no REX).
        # ORDER: this arm precedes the generic 80-dispatch below (the
        # base Emu serves 80 via 83 F0 xor... precisely the base
        # handles B0==0x83 only; bare 0x80 would fall to "bad opcode".
        # The digit add is the ONLY bare-80 form the template emits).
        if self.code[off:off + 2] == bytes([0x80, 0xC2]):
            self._fetch(2)
            v = self._fetch(1)[0]
            lo = (self.reg["rdx"] & 0xFF) + v
            self.cf = 1 if lo > 0xFF else 0
            lo &= 0xFF
            self.zf = 1 if lo == 0 else 0
            self.sf = 1 if lo >= 0x80 else 0
            self.of = 0
            self.reg["rdx"] = (self.reg["rdx"] & ~0xFF) | lo
            return
        # mov [rsi],dl: 88 16 (digit store; bare, no REX).
        if self.code[off:off + 2] == bytes([0x88, 0x16]):
            self._fetch(2)
            self._swrite(self.reg["rsi"] & MASK64, 1, self.reg["rdx"] & 0xFF)
            return
        # mov [rax],dl: 88 10 (legacy v1 digit store; kept so old
        # images still decode if any linger in scratch).
        if self.code[off:off + 2] == bytes([0x88, 0x10]):
            self._fetch(2)
            self._swrite(self.reg["rax"] & MASK64, 1, self.reg["rdx"] & 0xFF)
            return
        # mov BYTE [rsi],'-': C6 06 2D (negative sign store).
        if self.code[off:off + 3] == bytes([0xC6, 0x06, 0x2D]):
            self._fetch(3)
            self._swrite(self.reg["rsi"] & MASK64, 1, 0x2D)
            return
        if self.code[off:off + 1] == bytes([0xC6]):
            b1 = self.code[off + 1]
            if b1 == 0x44 and self.code[off + 2] == 0x24:
                self._fetch(3)
                d = self._fetch(1)[0]
                v = self._fetch(1)[0]
                self._swrite(self.reg["rsp"] + d, 1, v)
                return
            if b1 == 0x00:
                self._fetch(2)
                v = self._fetch(1)[0]
                self._swrite(self.reg["rax"] & MASK64, 1, v)
                return
        if self.code[off:off + 1] == bytes([0xC7]):
            if self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
                self._fetch(3)
                d = self._fetch(1)[0]
                v = int.from_bytes(self._fetch(4), "little")
                self._swrite(self.reg["rsp"] + d, 4, v)
                return
        # mov ecx,[rsp+disp8]: 8B 4C 24 ib8 (LEGACY v1 write form;
        # v2 uses LEA+imm; kept so old images still decode).
        if self.code[off:off + 3] == bytes([0x8B, 0x4C, 0x24]):
            self._fetch(3)
            disp = self._fetch(1)[0]
            self.reg["rcx"] = self._sread(self.reg["rsp"] + disp, 4)
            return
        # mov edx,imm32: BA ib32 (zero/int len + bool lens; bare,
        # no REX: zeroes rdx high).
        if self.code[off:off + 1] == bytes([0xBA]):
            self._fetch(1)
            self.reg["rdx"] = int.from_bytes(self._fetch(4), "little")
            return
        # mov ebx,imm32: BB ib32 (fd 1; bare, no REX: zeroes rbx high).
        if self.code[off:off + 1] == bytes([0xBB]):
            self._fetch(1)
            self.reg["rbx"] = int.from_bytes(self._fetch(4), "little") & 0xFFFFFFFF
            return
        # mov edx,[rsp+disp8]: 8B 54 24 ib8 (LEGACY v1 write form).
        # NOTE: bare 8B (no REX) ONLY; the REX form 48 8B 54 24 is
        # claimed by _rex above (8-byte). This step() arm sees b0 ==
        # 0x8B with NO preceding 48 (the dispatcher split on the
        # prefix byte), so no overlap. This arm stays for old images
        # only. 4-byte read: the v1 stash held a 32-bit len.
        if self.code[off:off + 3] == bytes([0x8B, 0x54, 0x24]):
            self._fetch(3)
            disp = self._fetch(1)[0]
            self.reg["rdx"] = self._sread(self.reg["rsp"] + disp, 4)
            return
        # sub rsp,48: 48 83 EC 30
        if self.code[off:off + 4] == bytes([0x48, 0x83, 0xEC, 0x30]):
            self._fetch(4)
            self.reg["rsp"] = (self.reg["rsp"] - 48) & MASK64
            return
        # add rsp,48: 48 83 C4 30
        if self.code[off:off + 4] == bytes([0x48, 0x83, 0xC4, 0x30]):
            self._fetch(4)
            self.reg["rsp"] = (self.reg["rsp"] + 48) & MASK64
            return
        # M3 jne rel32: 0F 85 cd (int digit-loop back-edge).
        # ORDER: this arm precedes the bare-0F fallback below (which
        # serves the branch suite's 0F 84 jz by delegating downward):
        # exact two-byte match first, fallback last. NOTE the layer
        # quirk: this step() consumed b0 via _fetch, so `off` no longer
        # addresses the opcode... precisely this layer peeks with
        # self.code[off] WITHOUT fetching first (off = rip - BASE at
        # entry), unlike the branch layer which fetches b0 first. Both
        # work; this layer's peek style keeps multi-byte matches atomic
        # (no rip rewind on miss).
        if self.code[off:off + 2] == bytes([0x0F, 0x85]):
            self._fetch(2)
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            if not self.zf:
                self.rip = (self.rip + disp) & MASK64
            return
        # M3 pop rcx: 59 (template restores; the base Emu serves 59
        # too, but this layer claims it first so the M3 comment
        # audit stays local to this file).
        if self.code[off:off + 1] == bytes([0x59]):
            self._fetch(1)
            self.reg["rcx"] = self._pop()
            return
        # M3 jns rel32: 0F 89 cd (int sign skip).
        if self.code[off:off + 2] == bytes([0x0F, 0x89]):
            self._fetch(2)
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            if not self.sf:
                self.rip = (self.rip + disp) & MASK64
            return
        # M3 jz rel32: 0F 84 cd (int zero skip / bool false / str
        # empty-skip; the branch-suite layer below serves 0F 84 too,
        # but this layer claims it first so the M3 comment audit
        # stays local to this file).
        # LAYER-ORDER NOTE: this step() peeks multi-byte forms BEFORE
        # delegating to super().step() at the end. The branch layer
        # below fetches b0 first and rewinds on miss; this layer never
        # rewinds (peek-then-fetch is atomic). A 0F byte reaching the
        # branch layer means NO M3 0F-arm matched: the branch layer
        # serves 0F 84 and bounces anything else downward. Since this
        # layer serves 0F 84/85/89 itself, the branch layer's 0F path
        # is unreachable from M3 images (kept for non-M3 images).
        if self.code[off:off + 2] == bytes([0x0F, 0x84]):
            self._fetch(2)
            disp = int.from_bytes(self._fetch(4), "little", signed=True)
            if self.zf:
                self.rip = (self.rip + disp) & MASK64
            return
        # jmp rel32: E9 cd (nz-write skips the zero block; the branch
        # layer below serves E9 too; claimed here for the same reason).
        # NOTE: the M3 template's jne back-edge (0F 85) shares its
        # 85 second byte with the 0F 84 jz above: order matters not
        # (full two-byte match on distinct second bytes 84/85/89).
        # (The E9 arm itself lives ABOVE, before the 80 C2 arm: a jmp
        # disp can alias digit-add prefixes. This comment stays with
        # the branch group for audit locality.)
        # NOTE: the M3 template's jne back-edge (0F 85) shares its
        # 85 second byte with the 0F 84 jz above: order matters not
        # (full two-byte match on distinct second bytes 84/85/89).
        # test eax,eax: 85 C0 (template entry tests; the branch layer
        # below serves it too; claimed here for the same reason).
        # FLAGS: eax-low-32 zero => ZF=1 (a full 64-bit rax with
        # nonzero high still tests EQUAL when low is zero: the
        # template's jz then takes the zero path and prints "0".
        # Narrowing in the ENTRY test is INTENTIONAL (bool/int-zero
        # share it); the digit-LOOP test uses rax-wide e_test_rax.
        if self.code[off:off + 2] == bytes([0x85, 0xC0]):
            self._fetch(2)
            r = self.reg["rax"] & 0xFFFFFFFF
            self.cf = 0
            self.of = 0
            self.zf = 1 if r == 0 else 0
            self.sf = 1 if r >= (1 << 31) else 0
            return
        # mov ecx,esi: 89 F1 (write buf = rsi low half; the kernel
        # takes the full 64-bit pointer and validates, so the
        # truncation is a no-op for stack addresses below 4 GiB).
        if self.code[off:off + 2] == bytes([0x89, 0xF1]):
            self._fetch(2)
            self.reg["rcx"] = self.reg["rsi"] & 0xFFFFFFFF
            return
        super().step()

    def _rex(self):
        off = self.rip - CODE_BASE
        b1 = self.code[off]
        # NOTE: _rex is entered with the 0x48 prefix ALREADY CONSUMED
        # (the base dispatcher fetched it): off points PAST the prefix
        # and b1 is the SECOND byte. All comparisons below are on
        # post-prefix bytes. (The calls-suite layer below reads the
        # same way: off = rip - BASE then b1 = code[off].)
        # This layer runs BEFORE the calls-suite layer, which serves
        # 48 89 home spills (7D/75/55/4D) and 48 8B home loads (45/85).
        # The M3 template's rsp-relative forms (44/54/74/7C + SIB 24)
        # are disjoint from those rbp-relative forms, so this layer
        # claims exactly its own ModRM set and delegates everything
        # else downward.
        # mov rax,[rsp+disp8]: b1=8B b2=44 SIB=24 (int sign reload).
        if b1 == 0x8B and self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            self.reg["rax"] = self._sread(self.reg["rsp"] + d, 8)
            return
        # mov rdx,[rsp+disp8]: 48 8B 54 24 ib8 (int magnitude/end reload:
        # FULL 8-byte read; the stash holds a 64-bit end pointer).
        # LAYER NOTE: this arm MUST precede the legacy 4-byte 8B 54
        # arm below (same prefix): the v2 template's 5-byte form
        # (with SIB 24) matches here; the legacy 4-byte form
        # (8B 54 24 ib8 WITHOUT the REX... precisely the legacy form
        # IS 8B 54 24 ib8 bare, 4 bytes total: same first 3 bytes!)
        # ...both forms share prefix 8B 54 24; the REX 48 prefix byte
        # distinguishes them: THIS arm runs under _rex (b1 follows
        # 48), the legacy arm under step() (b0 == 0x8B bare). No
        # overlap: _rex only sees post-48 bytes.
        if b1 == 0x8B and self.code[off + 1] == 0x54 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            self.reg["rdx"] = self._sread(self.reg["rsp"] + d, 8)
            return
        # lea rax,[rsp+disp8]: 48 8D 44 24 ib8 (digit end / word buf).
        if b1 == 0x8D and self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            self.reg["rax"] = (self.reg["rsp"] + d) & MASK64
            return
        # mov [rsp+disp8],rax: 48 89 44 24 ib8 (value/end spill).
        if b1 == 0x89 and self.code[off + 1] == 0x44 and self.code[off + 2] == 0x24:
            self._fetch(3)
            d = self._fetch(1)[0]
            self._swrite(self.reg["rsp"] + d, 8, self.reg["rax"])
            return
        # mov rsi,rax: 48 89 C6 (digit pointer / word buf from rax).
        if b1 == 0x89 and self.code[off + 1] == 0xC6:
            self._fetch(2)
            self.reg["rsi"] = self.reg["rax"]
            return
        # mov rax,rdx: 48 89 D0 (magnitude into rax for div).
        if b1 == 0x89 and self.code[off + 1] == 0xD0:
            self._fetch(2)
            self.reg["rax"] = self.reg["rdx"]
            return
        # sub rdx,rsi: 48 29 F2 (write length = end - ptr).
        if b1 == 0x29 and self.code[off + 1] == 0xF2:
            self._fetch(2)
            self.reg["rdx"] = self._flags_sub(self.reg["rdx"], self.reg["rsi"])
            return
        # xchg rax,[rsp]: 48 87 04 24 (str ptr/len swap).
        if b1 == 0x87 and self.code[off + 1] == 0x04 and self.code[off + 2] == 0x24:
            self._fetch(3)
            top = self._sread(self.reg["rsp"], 8)
            self._swrite(self.reg["rsp"], 8, self.reg["rax"])
            self.reg["rax"] = top
            return
        # div rcx: 48 F7 F1 (rax=quot, rdx=rem; magnitude < 2^63 so
        # no overflow; flags model like the host emulator: undef).
        if b1 == 0xF7 and self.code[off + 1] == 0xF1:
            self._fetch(2)
            assert self.reg["rcx"] != 0, "div by zero"
            dvd = (self.reg["rdx"] << 64) | (self.reg["rax"] & MASK64)
            div = self.reg["rcx"] & MASK64
            self.reg["rax"] = (dvd // div) & MASK64
            self.reg["rdx"] = (dvd % div) & MASK64
            return
        # dec rax: 48 FF C8.
        if b1 == 0xFF and self.code[off + 1] == 0xC8:
            self._fetch(2)
            self.reg["rax"] = (self.reg["rax"] - 1) & MASK64
            return
        # mov [rax],dl: 88 10 (digit store; bare, handled in step()).
        # add dl,imm8: 80 C2 ib8 (digit ASCII shift).
        # NOTE: bare form (no REX) is handled in step(); the REX
        # router never sees it. This arm is dead but kept to
        # document the encoding next to its siblings.
        if b1 == 0x80 and self.code[off + 1] == 0xC2:
            raise RuntimeError("bare 80 C2 must route via step(), not _rex")
        # cmp rax,imm8: 48 83 F8 ib8 (sign test).
        if b1 == 0x83 and self.code[off + 1] == 0xF8:
            self._fetch(2)
            v = self._fetch(1)[0]
            v = v - 256 if v >= 128 else v
            self._flags_sub(self.reg["rax"], v & MASK64)
            return
        # (add dl,imm8 lives in step(): bare 80 C2, no REX.)
        # neg rax: 48 F7 D8 (magnitude prep).
        if b1 == 0xF7 and self.code[off + 1] == 0xD8:
            self._fetch(2)
            a = self.reg["rax"]
            r = (0 - a) & MASK64
            self.cf = 1 if a != 0 else 0
            self.zf = 1 if r == 0 else 0
            self.sf = 1 if r >= (1 << 63) else 0
            self.of = 1 if a == (1 << 63) else 0
            self.reg["rax"] = r
            return
        # xor edx,edx: 31 D2 (dividend high clear; bare form, no REX).
        if b1 == 0x31 and self.code[off + 1] == 0xD2:
            self._fetch(2)
            self.reg["rdx"] = self._flags_logic(0)
            return
        # mov rcx,imm32: 48 C7 C1 ib32 (divisor staging, push-free).
        if b1 == 0xC7 and self.code[off + 1] == 0xC1:
            self._fetch(2)
            self.reg["rcx"] = int.from_bytes(self._fetch(4), "little")
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
    ("m3-int-lit", 'fn main(): int { print(42); return 0; }\n', b"42", 0),
    ("m3-int-neg", 'fn main(): int { print(0 - 7); return 0; }\n', b"-7", 0),
    ("m3-int-zero", 'fn main(): int { print(0); return 0; }\n', b"0", 0),
    ("m3-bool-t", 'fn main(): int { print(true); return 0; }\n', b"true", 0),
    ("m3-bool-f", 'fn main(): int { print(false); return 0; }\n', b"false", 0),
    ("m3-int-var", 'fn main(): int { let x: int = 42; print(x); return 0; }\n', b"42", 0),
    ("m3-bool-var", 'fn main(): int { let b: bool = true; print(b); return 1; }\n', b"true", 1),
    ("m3-str-var", 'fn f(s: str): int { print(s); return 0; }\nfn main(): int { return f("ab"); }\n', b"ab", 0),
    ("m3-int-expr", 'fn main(): int { print(2 + 3); return 0; }\n', b"5", 0),
    ("m3-seq", 'fn main(): int { print(1); print(true); print("s"); return 0; }\n', b"1trues", 0),
    ("m3-str-empty", 'fn f(s: str): int { print(s); return 0; }\nfn main(): int { return f(""); }\n', b"", 0),
    ("m3-int-param", 'fn f(x: int): int { print(x); return 0; }\nfn main(): int { return f(0 - 123); }\n', b"-123", 0),
]

REJECT25_CASES = [
    ("print-status", 'fn w(x: int): status<int> { let l: list<int,1> = [x]; return l[0]; }\nfn main(): int { let s: status<int> = w(5); print(s); return 0; }\n'),
]


class M3AcceptTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(self.images), 10)

    def test_02_determinism(self):
        again = _run_be(self.combo, [(n, s) for n, s, _w, _e in ACCEPT_CASES])
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
        by_name = {n: (w, e) for n, _h, w, e in self.images}
        self.assertEqual(by_name["m3-int-lit"], (b"42", 0))
        self.assertEqual(by_name["m3-int-neg"], (b"-7", 0))
        self.assertEqual(by_name["m3-int-zero"], (b"0", 0))
        self.assertEqual(by_name["m3-bool-t"], (b"true", 0))
        self.assertEqual(by_name["m3-bool-f"], (b"false", 0))
        self.assertEqual(by_name["m3-int-var"], (b"42", 0))
        self.assertEqual(by_name["m3-bool-var"], (b"true", 1))
        self.assertEqual(by_name["m3-str-var"], (b"ab", 0))
        self.assertEqual(by_name["m3-int-expr"], (b"5", 0))
        self.assertEqual(by_name["m3-seq"], (b"1trues", 0))
        self.assertEqual(by_name["m3-str-empty"], (b"", 0))
        self.assertEqual(by_name["m3-int-param"], (b"-123", 0))

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


class M3RejectTests(unittest.TestCase):
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


class M3MutantTests(unittest.TestCase):
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

    def test_m3_m1_zero_path_broken(self):
        # Witness MUST be nonzero (m3-int-lit), not m3-int-zero: on
        # print(0) the jz flips to jnz and STILL takes the jump to the
        # zero block (ZF=1 either branch), so the mutant is
        # unobservable on the zero input. On print(42) the flip skips
        # the zero block and runs magnitude-0 digits -> "0".
        combo = _mut(_combo_text(),
                     "  return e_print_int_nz(e_jcc_z(be_rel32(s8j0targ(s4 + sz_jcc()), s4, sz_jcc()), s4));",
                     "  return e_print_int_nz(e_jcc_nz(be_rel32(s8j0targ(s4 + sz_jcc()), s4, sz_jcc()), s4));")
        self.assertTrue(self._red_on(combo, self._idx("m3-int-lit")))

    def test_m3_m2_sign_inverted(self):
        combo = _mut(_combo_text(),
                     "  let d35: int = e_jns(be_rel32(snwrtarg(d33 + sz_jns()), d33, sz_jns()), d33);",
                     "  let d35: int = e_js(be_rel32(snwrtarg(d33 + sz_jns()), d33, sz_jns()), d33);")
        self.assertTrue(self._red_on(combo, self._idx("m3-int-neg")))

    def test_m3_m3_bool_sense_flipped(self):
        combo = _mut(_combo_text(),
                     "  return e_print_bool_true(e_jcc_z(be_rel32(s3fpos(s2 + sz_jcc()), s2, sz_jcc()), s2));",
                     "  return e_print_bool_true(e_jcc_nz(be_rel32(s3fpos(s2 + sz_jcc()), s2, sz_jcc()), s2));")
        self.assertTrue(self._red_on(combo, self._idx("m3-bool-t")))

    def test_m3_m4_empty_guard_dropped(self):
        # Live anchor is e_print_str_home (the jz lives there, not in
        # e_print_str_go). Witness MUST be nonempty (m3-str-var), not
        # m3-str-empty: on "" the jz flips to jnz and still skips the
        # go block (JZ taken, JNZ not taken: both land past), so the
        # mutant is unobservable on the empty input. On "ab" the flip
        # skips the write and prints nothing.
        combo = _mut(_combo_text(),
                     "  return e_print_str_go(base, e_jcc_z(be_rel32(s3sdone(base, s2 + sz_jcc()), s2, sz_jcc()), s2));",
                     "  return e_print_str_go(base, e_jcc_nz(be_rel32(s3sdone(base, s2 + sz_jcc()), s2, sz_jcc()), s2));")
        self.assertTrue(self._red_on(combo, self._idx("m3-str-var")))

    def test_m3_m5_template_size_short(self):
        combo = _mut(_combo_text(),
                     "fn be_print_int_size(): int {\n  return 176;\n}",
                     "fn be_print_int_size(): int {\n  return 100;\n}")
        self.assertTrue(self._red_on(combo, self._idx("m3-int-lit")))


if __name__ == "__main__":
    unittest.main()
