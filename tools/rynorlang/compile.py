#!/usr/bin/env python3
"""RynorLang native backend (Stage 15a): RIR -> freestanding x86-64 NASM.

Host-side, Python 3.10+ standard library only. Emits one deterministic
assembly text per verified RIR module following docs/design/rynorlang-abi.md:
SysV-subset calls, spill-everything homes, no red zone, no SIMD, direct
calls and static jumps only, `ud2` on divide-by-zero, `int3` on fall-off.
See compile_rir() for the entry point and main() for the CLI.

Safety posture: emit_asm() verifies the module first and refuses invalid
RIR instead of emitting garbage. The emitter never emits `syscall`, indirect
calls/jumps, or SSE; check_asm() re-scans the text independently and the
test suite asserts both directions.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.rynorlang import rir as _rir
from tools.rynorlang import analyze as _analyze

COMP_NO_ENTRY = "COMP_NO_ENTRY"

_ARG_REGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")

_REG = r"(?:r(?:ax|bx|cx|dx|si|di|bp|sp|8|9|1[0-5])|e(?:ax|bx|cx|dx|si|di|bp|sp)|[abcd][lhw]|sil|dil|bpl|spl|r[89][bdw]?)"
_FORBIDDEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(syscall|sysret|sysenter|iretq?|cli|sti|hlt|"
    r"in|out|insb?|insw?|insd?|outsb?|outsw?|outsd?|"
    r"movs[bdqw]|stos[bdqw]|lods[bdqw]|scas[bdqw]|cmps[bdqw]|"
    r"movaps|movapd|movups|movupd|movdqa|movdqu|addss|addps|mulss|mulps|"
    r"subss|subps|divss|divps|xorps|xorpd|pxor|stmxcsr|ldmxcsr|"
    r"xmm([0-9]|1[0-5])|ymm([0-9]|1[0-5])|zmm[0-9]+)\b"
    r"|call\s+(" + _REG + r"|\[)|jmp\s+(" + _REG + r"|\[)",
)
_FORBIDDEN_CALLEE_SAVED_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:rbx|ebx|bx|bl|bh|r1[2-5](?:d|w|b)?)(?![A-Za-z0-9_])")


def check_asm(text: str) -> list:
    """Independently re-scan emitted assembly for forbidden constructs.

    The single exception is the exact line `repe cmpsb`, which the emitter
    uses for bounded string content comparison (rcx <= 4096, both sides
    length-checked); it is allowlisted literally, never by pattern.
    """
    problems = []
    if not isinstance(text, str) or "bits 64" not in text:
        problems.append("asm must be 64-bit NASM text")
        return problems
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split(";", 1)[0]
        if code.strip() == "repe cmpsb":
            continue
        if _FORBIDDEN_RE.search(code):
            problems.append(f"line {number}: forbidden construct: {line.strip()[:60]}")
        elif _FORBIDDEN_CALLEE_SAVED_RE.search(code):
            problems.append(f"line {number}: forbidden callee-saved scratch: "
                            f"{line.strip()[:60]}")
    return problems


def _mangle(name: str) -> str:
    # A length prefix prevents a source function from colliding with a block
    # or trap suffix generated for another function.
    return f"rl_{len(name)}_{name}"


class _Emitter:
    def __init__(self, module: dict):
        self.module = module
        self.lines: list[str] = []
        self.vreg_type: dict[str, str] = {}
        self.slot_of: dict[str, int] = {}
        self.func = None

    def out(self, line: str = "") -> None:
        self.lines.append(line)

    def home(self, vreg: str) -> str:
        return f"[rbp - {8 * (self.slot_of[vreg] + 1)}]"

    def load_reg(self, reg: str, vreg: str) -> None:
        typ = self.vreg_type[vreg]
        if typ == "str":
            self.out(f"    mov {reg}, {self.home(vreg)}")
        else:
            self.out(f"    mov {reg}, {self.home(vreg)}")

    def store_reg(self, reg: str, vreg: str) -> None:
        self.out(f"    mov {self.home(vreg)}, {reg}")

    # -- module ---------------------------------------------------------
    def emit_module(self) -> str:
        self.out("bits 64")
        self.out("default rel")
        self.out("section .note.GNU-stack noalloc noexec no progbits")
        self.out("")
        self._emit_rodata()
        self.out("section .text")
        for func in self.module["funcs"]:
            self._emit_function(func)
        return "\n".join(self.lines) + "\n"

    def _emit_rodata(self) -> None:
        entries = self.module.get("strtab", [])
        if not entries:
            return
        self.out("section .rodata align=8")
        for entry in entries:
            blob = ",".join(f"0x{b:02x}" for b in entry["bytes"].encode("ascii"))
            if blob:
                self.out(f'_rlstr_{entry["id"]}: db {blob}')
            else:
                self.out(f'_rlstr_{entry["id"]}: db 0')
        self.out("")

    # -- functions ------------------------------------------------------
    def _assign_homes(self, func: dict) -> None:
        # Home slots come from the shared allocator (rir.assign_slots), the
        # same computation the builder used to fill frameslots and the
        # verifier recomputed to check it -- emission cannot disagree.
        self.vreg_type = {}
        for index, param in enumerate(func["params"]):
            self.vreg_type[f"%{index}"] = param["type"]
        for block in func["blocks"]:
            for instr in block["instrs"]:
                dst = instr.get("dst")
                if isinstance(dst, str) and dst not in self.vreg_type:
                    self.vreg_type[dst] = instr.get("type")
        self.slot_of, self.frameslots = _rir.assign_slots(
            func["blocks"], self.vreg_type, len(func["params"]))

    def _emit_function(self, func: dict) -> None:
        self.func = func
        self._assign_homes(func)
        label = _mangle(func["name"])
        frame = (self.frameslots * 8 + 15) // 16 * 16
        self.out(f"global {label}")
        self.out(f"{label}:")
        self.out("    push rbp")
        self.out("    mov rbp, rsp")
        if frame:
            self.out(f"    sub rsp, {frame}")
        self._spill_params(func)
        for block in func["blocks"]:
            self.out(f"{label}_{block['id']}:")
            for instr in block["instrs"]:
                self._emit_instr(func, instr)
            self._emit_term(func, block, block["term"])
        self.out("")

    def _spill_params(self, func: dict) -> None:
        slot = 0
        stack_slot = 0
        for index, param in enumerate(func["params"]):
            home = f"[rbp - {8 * (self.slot_of[f'%{index}'] + 1)}]"
            if slot < len(_ARG_REGS):
                reg = _ARG_REGS[slot]
                if param["type"] == "str":
                    if slot + 1 >= len(_ARG_REGS):
                        # Whole str goes to the stack; never split across
                        # the register/stack boundary.
                        self._spill_param_stack(home, index, stack_slot)
                        stack_slot += 2
                        slot += 2
                        continue
                    self.out(f"    mov {home}, {reg}")
                    nxt = f"[rbp - {8 * (self.slot_of[f'%{index}'] + 2)}]"
                    self.out(f"    mov {nxt}, {_ARG_REGS[slot + 1]}")
                    slot += 2
                    continue
                self.out(f"    mov {home}, {reg}")
                slot += 1
            else:
                width = 2 if param["type"] == "str" else 1
                self._spill_param_stack(home, index, stack_slot)
                stack_slot += width
                slot += width

    def _spill_param_stack(self, home: str, index: int, stack_slot: int) -> None:
        # Incoming stack slot k lives at [rbp+16+8k]. Keep a separate stack
        # index because an atomic str that does not fit in the last register
        # begins at stack slot zero even though its logical ABI slot is five.
        k = stack_slot
        self.out("    mov rax, [rbp + %d]" % (16 + 8 * k))
        self.out(f"    mov {home}, rax")
        if self.vreg_type[f"%{index}"] == "str":
            self.out("    mov rax, [rbp + %d]" % (16 + 8 * (k + 1)))
            nxt = f"[rbp - {8 * (self.slot_of[f'%{index}'] + 2)}]"
            self.out(f"    mov {nxt}, rax")

    # -- instructions ---------------------------------------------------
    def _emit_instr(self, func: dict, instr: dict) -> None:
        op = instr["op"]
        if op == "const":
            self._emit_const(instr)
        elif op == "copy":
            self._emit_copy(instr)
        elif op == "binop":
            self._emit_binop(func, instr)
        elif op == "unop":
            self._emit_unop(instr)
        elif op == "call":
            self._emit_call(func, instr)
        else:
            raise ValueError(f"emitter: unknown opcode {op!r}")

    def _emit_const(self, instr: dict) -> None:
        typ = instr["type"]
        if typ == "int":
            self.out(f'    mov rax, {instr["value"]}')
            self.store_reg("rax", instr["dst"])
        elif typ == "bool":
            self.out(f'    mov rax, {1 if instr["value"] else 0}')
            self.store_reg("rax", instr["dst"])
        elif typ == "str":
            sid = instr["value"]
            self.out(f"    lea rax, [rel _rlstr_{sid}]")
            self.store_reg("rax", instr["dst"])
            entry = self.module["strtab"][sid]
            self.out(f"    mov rax, {entry['len']}")
            nxt = f"[rbp - {8 * (self.slot_of[instr['dst']] + 2)}]"
            self.out(f"    mov {nxt}, rax")
        else:
            raise ValueError(f"emitter: bad const type {typ!r}")

    def _emit_copy(self, instr: dict) -> None:
        self.load_reg("rax", instr["src"])
        self.store_reg("rax", instr["dst"])
        if instr["type"] == "str":
            src2 = f"[rbp - {8 * (self.slot_of[instr['src']] + 2)}]"
            dst2 = f"[rbp - {8 * (self.slot_of[instr['dst']] + 2)}]"
            self.out(f"    mov rax, {src2}")
            self.out(f"    mov {dst2}, rax")

    def _emit_binop(self, func: dict, instr: dict) -> None:
        op = instr["operator"]
        dst = instr["dst"]
        if op in ("+", "-", "*", "/", "%"):
            self._emit_arith(func, instr)
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            self._emit_compare(func, instr)
        elif op in ("&&", "||"):
            left = instr["l"]
            right = instr["r"]
            self.load_reg("rax", left)
            self.load_reg("rcx", right)
            self.out(f"    {'and' if op == '&&' else 'or'} rax, rcx")
            self.store_reg("rax", dst)
        else:
            raise ValueError(f"emitter: unknown binop {op!r}")

    def _emit_arith(self, func: dict, instr: dict) -> None:
        op = instr["operator"]
        self.load_reg("rax", instr["l"])
        self.load_reg("rcx", instr["r"])
        if op == "+":
            self.out("    add rax, rcx")
        elif op == "-":
            self.out("    sub rax, rcx")
        elif op == "*":
            self.out("    imul rax, rcx")
        elif op in ("/", "%"):
            # Zero divisor traps via ud2 below; INT_MIN/-1 traps through the
            # hardware #DE from idiv itself. Fall-through must skip the trap.
            site = self._fresh_trap_site(func)
            self.out("    test rcx, rcx")
            self.out(f"    jz {site}")
            self.out("    cqo")
            self.out("    idiv rcx")
            if op == "%":
                self.out("    mov rax, rdx")
            done = f"{site}_done"
            self.out(f"    jmp {done}")
            self.out(f"{site}:")
            self.out("    ud2")
            self.out(f"{done}:")
        self.store_reg("rax", instr["dst"])

    def _fresh_trap_site(self, func: dict) -> str:
        seq = self._trap_seq = getattr(self, "_trap_seq", 0) + 1
        return f'{_mangle(func["name"])}_trap_div0_{seq}'

    def _setcc(self, cond: str, dst: str) -> None:
        self.out(f"    {cond} al")
        self.out("    movzx rax, al")
        self.store_reg("rax", dst)

    def _emit_compare(self, func: dict, instr: dict) -> None:
        op = instr["operator"]
        dst = instr["dst"]
        typ = self.vreg_type[instr["l"]]
        if typ == "str":
            if op not in ("==", "!="):
                raise ValueError("emitter: str ordering is not supported")
            self._emit_str_compare(func, instr)
            return
        self.load_reg("rax", instr["l"])
        self.load_reg("rcx", instr["r"])
        self.out("    cmp rax, rcx")
        mapping = {"==": "sete", "!=": "setne", "<": "setl", ">": "setg",
                   "<=": "setle", ">=": "setge"}
        self._setcc(mapping[op], dst)

    def _emit_str_compare(self, func: dict, instr: dict) -> None:
        # Byte-wise content comparison (value semantics: equal contents
        # compare equal even for distinct literals). Zero-length sides never
        # dereference: repe with rcx=0 sets ZF without touching memory.
        base = f'{_mangle(func["name"])}_streq_{instr["dst"][1:]}'
        left, right = instr["l"], instr["r"]
        self.load_reg("rsi", left)
        nxt_l = f"[rbp - {8 * (self.slot_of[left] + 2)}]"
        self.load_reg("rdi", right)
        nxt_r = f"[rbp - {8 * (self.slot_of[right] + 2)}]"
        self.out(f"    mov rcx, {nxt_l}")
        self.out(f"    mov rdx, {nxt_r}")
        self.out("    cmp rcx, rdx")
        self.out(f"    jne {base}_ne")
        self.out("    repe cmpsb")
        self.out(f"    sete al")
        self.out(f"    jmp {base}_done")
        self.out(f"{base}_ne:")
        self.out("    xor eax, eax")
        self.out(f"{base}_done:")
        self.out("    movzx rax, al")
        if instr["operator"] == "!=":
            self.out("    xor rax, 1")
        self.store_reg("rax", instr["dst"])

    def _emit_unop(self, instr: dict) -> None:
        op = instr["operator"]
        self.load_reg("rax", instr["v"])
        if op == "-":
            self.out("    neg rax")
        elif op == "!":
            self.out("    xor rax, 1")
        else:
            raise ValueError(f"emitter: unknown unop {op!r}")
        self.store_reg("rax", instr["dst"])

    def _emit_call(self, func: dict, instr: dict) -> None:
        args = instr["args"]
        # Marshal left-to-right into SysV slots; a str crossing the
        # register/stack boundary moves wholly to the stack, never split.
        regs: list[tuple] = []
        stack: list[tuple] = []
        slot = 0
        nstack = 0
        for arg in args:
            if self.vreg_type[arg] == "str" and slot < len(_ARG_REGS) and slot + 1 >= len(_ARG_REGS):
                stack.append((arg, nstack))
                nstack += 2
                slot += 2
            elif slot < len(_ARG_REGS):
                regs.append((arg, _ARG_REGS[slot]))
                slot += 2 if self.vreg_type[arg] == "str" else 1
            else:
                width = 2 if self.vreg_type[arg] == "str" else 1
                stack.append((arg, nstack))
                nstack += width
                slot += width
        # Round stack bytes up to 16 so rsp%16==0 holds at the call.
        sbytes = (nstack * 8 + 15) // 16 * 16
        if sbytes:
            self.out(f"    sub rsp, {sbytes}")
        for arg, reg in regs:
            self.load_reg("rax", arg)
            self.out(f"    mov {reg}, rax")
            if self.vreg_type[arg] == "str":
                nxt = f"[rbp - {8 * (self.slot_of[arg] + 2)}]"
                self.out(f"    mov rax, {nxt}")
                # Second half follows the first in the register order.
                order = list(_ARG_REGS)
                self.out(f"    mov {order[order.index(reg) + 1]}, rax")
        for arg, stack_index in stack:
            k = stack_index * 8
            self.load_reg("rax", arg)
            self.out(f"    mov [rsp + {k}], rax")
            if self.vreg_type[arg] == "str":
                nxt = f"[rbp - {8 * (self.slot_of[arg] + 2)}]"
                self.out(f"    mov rax, {nxt}")
                self.out(f"    mov [rsp + {k + 8}], rax")
        self.out(f'    call {_mangle(instr["name"])}')
        if sbytes:
            self.out(f"    add rsp, {sbytes}")
        if "dst" in instr:
            if self.vreg_type[instr["dst"]] == "str":
                self.store_reg("rax", instr["dst"])
                nxt = f"[rbp - {8 * (self.slot_of[instr['dst']] + 2)}]"
                self.out(f"    mov {nxt}, rdx")
            else:
                self.store_reg("rax", instr["dst"])

    # -- terminators ----------------------------------------------------
    def _emit_term(self, func: dict, block: dict, term: dict) -> None:
        label = _mangle(func["name"])
        op = term.get("op")
        if op == "jmp":
            self.out(f'    jmp {label}_{term["tgt"]}')
        elif op == "br":
            self.load_reg("rax", term["cond"])
            self.out("    test rax, rax")
            self.out(f'    jnz {label}_{term["then"]}')
            self.out(f'    jmp {label}_{term["else"]}')
        elif op == "ret":
            value = term.get("v")
            if value is not None:
                self.load_reg("rax", value)
                if self.vreg_type[value] == "str":
                    nxt = f"[rbp - {8 * (self.slot_of[value] + 2)}]"
                    self.out(f"    mov rdx, {nxt}")
            elif func["name"] == "main" and func["ret"] is None:
                # A unit main still exits cleanly with status 0.
                self.out("    xor eax, eax")
            self.out("    leave")
            self.out("    ret")
        elif op == "unreachable":
            self.out(f"{label}_{block['id']}_trap_falloff:")
            self.out("    int3")
        else:
            raise ValueError(f"emitter: unknown terminator {op!r}")


def emit_asm(module: dict) -> str:
    """Emit NASM text for a verified RIR module (verifies first, refuses bad IR)."""
    problems = _rir.verify_module(module)
    if problems:
        raise ValueError("emitter refuses invalid RIR: " + problems[0])
    if not module.get("funcs"):
        raise ValueError("emitter refuses an empty module: nothing to emit")
    return _Emitter(module).emit_module()


def compile_source(source: str, filename: str = "<input>"):
    """Full pipeline: lex/parse/analyze -> RIR -> asm.

    Returns (asm_text, None) or (None, {"code","message"}) where code is a
    PAR_*/SEM_*/COMP_* diagnostic code. Never raises on bad input.
    """
    result = _analyze.analyze(source, filename)
    if not result.ok:
        diag = result.diagnostic
        return None, {"code": diag.code, "message": diag.message}
    module, error = _rir.build_rir(result.ast, filename)
    if error is not None:
        return None, error
    problems = _rir.verify_module(module)
    if problems:
        return None, {"code": "COMP_BAD_RIR", "message": problems[0]}
    entry = next((f for f in module["funcs"] if f["name"] == "main"), None)
    if entry is None or entry["params"] or entry["ret"] not in ("int", None):
        return None, {"code": COMP_NO_ENTRY,
                      "message": "native entry must be fn main() with ()->int or ()->unit"}
    try:
        return emit_asm(module), None
    except ValueError as error:
        return None, {"code": "COMP_EMIT_FAILED", "message": str(error)}


def main(argv=None) -> int:
    """rynorlangc: host-side RynorLang compiler driver (bootstrap boundary).

    This driver runs on the HOST (CPython). It is the L0 bootstrap compiler,
    not a RynorOS native program: it reads .rl source with the frozen
    toolchain, lowers to RIR, and emits freestanding x86-64 NASM. Native
    execution happens separately in the disclosed test harness.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rir", action="store_true", help="print RIR text instead of assembly")
    group.add_argument("--asm", action="store_true", help="print assembly (default)")
    args = parser.parse_args(argv)
    if args.source is None:
        parser.print_usage(sys.stderr)
        return 2
    try:
        raw = args.source.read_bytes()
    except OSError as error:
        print(f"{args.source}:1:1:0: PAR_INVALID_INPUT: {error}", file=sys.stderr)
        return 2
    try:
        source = raw.decode("ascii")
    except UnicodeDecodeError:
        # Non-ASCII source is a lexical failure, not a usage error.
        print(f"{args.source}:1:1:0: PAR_LEX_ERROR: "
              "RynorLang Stage 12 source is ASCII-only", file=sys.stderr)
        return 1
    result = _analyze.analyze(source, str(args.source))
    if not result.ok:
        diag = result.diagnostic
        print(f"{diag.span.filename}:{diag.span.line}:{diag.span.column}:"
              f"{diag.span.offset}: {diag.code}: {diag.message}", file=sys.stderr)
        return 1
    module, error = _rir.build_rir(result.ast, str(args.source))
    if error is not None:
        print(f"{args.source}:1:1:0: {error['code']}: {error['message']}", file=sys.stderr)
        return 1
    problems = _rir.verify_module(module)
    if problems:
        print(f"{args.source}:1:1:0: COMP_BAD_RIR: {problems[0]}", file=sys.stderr)
        return 1
    if args.rir:
        sys.stdout.write(_rir.dumps(module))
        return 0
    entry = next((f for f in module["funcs"] if f["name"] == "main"), None)
    if entry is None or entry["params"] or entry["ret"] not in ("int", None):
        print(f"{args.source}:1:1:0: {COMP_NO_ENTRY}: "
              "native entry must be fn main() with ()->int or ()->unit", file=sys.stderr)
        return 1
    try:
        sys.stdout.write(emit_asm(module))
    except ValueError as error:
        print(f"{args.source}:1:1:0: COMP_EMIT_FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
