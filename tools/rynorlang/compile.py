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
from tools.rynorlang import agtypes as _agtypes

COMP_NO_ENTRY = "COMP_NO_ENTRY"

_ARG_REGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")

# Stage 19e file builtins: dedicated RIR ops lowered through runtime
# helpers (not `call` instrs). The helpers own a 1 MiB bump arena for
# stable result strings (addresses never leak into output: only bytes
# are observed, so ASLR/PIE cannot perturb determinism).
_RT_HELPER_BY_OP = {"str_fread": "rt_fread", "str_fjoin": "rt_fjoin",
                     "str_argv": "rt_argv"}

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
        self._seq = 0
        self._rectypes: dict[str, list] = {}
        self._rec_sizes: dict[str, int] = {}
        self._rec_busy: set[str] = set()
        for entry in module.get("rectypes", []):
            self._rectypes[entry["name"]] = [(f["name"], f["type"]) for f in entry["fields"]]

    def _record_size(self, name: str) -> int:
        if name in self._rec_sizes:
            return self._rec_sizes[name]
        if name in self._rec_busy:
            raise ValueError(f"emitter: recursive record '{name}'")
        self._rec_busy.add(name)
        try:
            total = 0
            for _fname, ftype in self._rectypes[name]:
                total += self._type_size(ftype)
            self._rec_sizes[name] = total
            return total
        finally:
            self._rec_busy.discard(name)

    def _type_size(self, typ: str) -> int:
        if typ == "str":
            return 16
        if typ in ("int", "bool"):
            return 8
        node = _agtypes.parse_type(typ)
        if node is not None and node[0] == "nominal" and node[1] in self._rectypes:
            return self._record_size(node[1])
        if node is not None:
            # Pre-size nominal dependencies (order-independent): size_of
            # only sees completed entries.
            stack = [node]
            while stack:
                item = stack.pop()
                if not isinstance(item, tuple) or not item:
                    continue
                if item[0] == "nominal":
                    if item[1] in self._rectypes:
                        self._record_size(item[1])
                elif item[0] == "generic":
                    stack.extend(a for a in item[2] if isinstance(a, tuple))
        size = _agtypes.size_of(node, self._rec_sizes) if node is not None else None
        if size is None:
            raise ValueError(f"emitter: unresolvable type {typ!r}")
        return size

    def _type_width(self, typ: str) -> int:
        return (self._type_size(typ) + 7) // 8

    def _field_layout(self, rec: str) -> list:
        """[(name, type, slot_offset)...] in declaration order."""
        out = []
        offset = 0
        for fname, ftype in self._rectypes[rec]:
            out.append((fname, ftype, offset))
            offset += self._type_width(ftype)
        return out

    def _fresh(self, tag: str) -> str:
        self._seq += 1
        return f"{_mangle(self.func['name'])}_{tag}_{self._seq}"

    def home_at(self, vreg: str, slot_offset: int = 0) -> str:
        return f"[rbp - {8 * (self.slot_of[vreg] + 1 + slot_offset)}]"

    def _emit_copy_range(self, dst_base: str, src_base: str, nslots: int) -> None:
        # Bounded block copy (caller-saved regs only; string instructions
        # stay forbidden). Home-relative addresses DESCEND per slot (higher
        # slots live at lower addresses), so the loops step down. Unrolled
        # for one slot, looped otherwise.
        if nslots <= 0:
            return
        if nslots == 1:
            self.out("    mov rax, " + src_base)
            self.out("    mov " + dst_base + ", rax")
            return
        loop = self._fresh("cpy")
        self.out(f"    mov rcx, {nslots}")
        self.out(f"    lea rsi, {src_base}")
        self.out(f"    lea rdi, {dst_base}")
        self.out(f"{loop}:")
        self.out("    mov rax, [rsi]")
        self.out("    mov [rdi], rax")
        self.out("    sub rsi, 8")
        self.out("    sub rdi, 8")
        self.out("    dec rcx")
        self.out(f"    jnz {loop}")

    def _emit_zero_range(self, dst_base: str, nslots: int) -> None:
        if nslots <= 0:
            return
        if nslots == 1:
            self.out("    xor eax, eax")
            self.out("    mov " + dst_base + ", rax")
            return
        loop = self._fresh("zero")
        self.out(f"    mov rcx, {nslots}")
        self.out(f"    lea rdi, {dst_base}")
        self.out("    xor eax, eax")
        self.out(f"{loop}:")
        self.out("    mov [rdi], rax")
        self.out("    sub rdi, 8")
        self.out("    dec rcx")
        self.out(f"    jnz {loop}")

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
        # Runtime helpers (rt_*) live in the linked program runtime object,
        # not in this translation unit: declare exactly the referenced ones.
        # Modules without print emit no extern lines (Stage 15a goldens
        # stay byte-identical). print_agg sites need all three scalar
        # printers (operand kinds vary per site; the set is deterministic).
        needed = sorted({instr["name"] for func in self.module["funcs"]
                         for block in func["blocks"]
                         for instr in block["instrs"]
                         if isinstance(instr, dict) and instr.get("op") == "call"
                         and instr.get("name") in _rir.RT_HELPERS})
        has_print_agg = any(isinstance(instr, dict) and instr.get("op") == "print_agg"
                            for func in self.module["funcs"]
                            for block in func["blocks"]
                            for instr in block["instrs"])
        if has_print_agg:
            for helper in ("rt_print_int", "rt_print_bool", "rt_print_str"):
                if helper not in needed:
                    needed.append(helper)
            needed.sort()
        # Stage 19e file builtins: dedicated RIR ops (not `call`), so
        # their runtime helpers are declared from op presence.
        for func in self.module["funcs"]:
            for block in func["blocks"]:
                for instr in block["instrs"]:
                    if isinstance(instr, dict):
                        helper = _RT_HELPER_BY_OP.get(instr.get("op"))
                        if helper is not None and helper not in needed:
                            needed.append(helper)
        needed.sort()
        for name in needed:
            self.out(f"extern {_mangle(name)}")
        if needed:
            self.out("")
        self._emit_rodata()
        if has_print_agg:
            self._collect_print_strings()
            self._emit_punct()
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

    # Fixed punctuation for print_agg (shared rodata, deterministic order).
    _PUNCT = ("[", "]", ", ", "{", "}", ": ", "ok(", "err(", ")")

    def _collect_print_strings(self) -> None:
        # Field-name `name: ` punctuations for every record type reachable
        # from a print_agg site (transitively). Deterministic site order.
        self._field_punct: list[tuple] = []
        seen: set[str] = set()

        def visit(typ: str) -> None:
            node = _agtypes.parse_type(typ)
            if node is None:
                return
            if node[0] == "nominal":
                if node[1] in seen or node[1] not in self._rectypes:
                    return
                seen.add(node[1])
                for fname, ftype in self._rectypes[node[1]]:
                    self._field_punct.append((f"_rlpf_{len(self._field_punct)}", f"{fname}: "))
                    visit(ftype)
            elif node[0] == "generic":
                for arg in node[2]:
                    if isinstance(arg, tuple) and arg and arg[0] != "cap":
                        visit(_agtypes.canonical(arg))

        for func in self.module["funcs"]:
            for block in func["blocks"]:
                for instr in block["instrs"]:
                    if isinstance(instr, dict) and instr.get("op") == "print_agg":
                        visit(self._print_agg_type(func, instr))

    def _print_agg_type(self, func: dict, instr: dict) -> str:
        # Resolve a print_agg operand's type by scanning the function once
        # (print_agg sites are few; clarity over cleverness).
        want = instr.get("agg")
        for block in func["blocks"]:
            for ins in block["instrs"]:
                if isinstance(ins, dict) and ins.get("dst") == want:
                    return ins.get("type")
        for index, param in enumerate(func["params"]):
            if f"%{index}" == want:
                return param["type"]
        raise ValueError(f"emitter: print_agg of unknown vreg {want!r}")

    def _emit_punct(self) -> None:
        self.out("section .rodata align=8")
        for index, text in enumerate(self._PUNCT):
            blob = ",".join(f"0x{b:02x}" for b in text.encode("ascii"))
            self.out(f"_rlpunc_{index}: db {blob}")
        for label, text in self._field_punct:
            blob = ",".join(f"0x{b:02x}" for b in text.encode("ascii"))
            safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in label)
            self.out(f"{safe}: db {blob}")
        self.out("")

    def _field_label(self, fname: str) -> str:
        for label, text in self._field_punct:
            if text == f"{fname}: ":
                return "".join(c if (c.isalnum() or c == "_") else "_" for c in label)
        raise ValueError(f"emitter: no punct label for field '{fname}'")

    def _print_field_name(self, fname: str) -> None:
        label = self._field_label(fname)
        text = f"{fname}: "
        self.out(f"    lea rdi, [rel {label}]")
        self.out(f"    mov rsi, {len(text)}")
        self.out(f"    call {_mangle('rt_print_str')}")

    def _emit_print_agg(self, instr: dict) -> None:
        atype = self._print_agg_type(self.func, instr)
        self._print_value_at(atype, instr["agg"], 0)

    def _print_value_at(self, typ: str, vreg: str, off: int) -> None:
        # Print the value at a vreg home offset in the frozen canonical
        # format. Straight-line static traversal (no live state crosses
        # helper calls except the map first-flag, which lives in 16
        # stack bytes claimed symmetrically around the traversal).
        if typ in ("int", "bool", "str"):
            self._print_scalar_at(typ, vreg, off)
            return
        node = _agtypes.parse_type(typ)
        if node[0] == "nominal":
            self._print_punct(3)  # {
            first = True
            for fname, ftype, foff in self._field_layout(node[1]):
                if not first:
                    self._print_punct(2)  # ,
                first = False
                self._print_field_name(fname)
                self._print_value_at(ftype, vreg, off + foff)
            self._print_punct(4)  # }
            return
        base = node[1]
        if base == "status":
            payload_t = _agtypes.canonical(node[2][0])
            is_ok = self._fresh("pok")
            done = self._fresh("pdone")
            self.out(f"    mov rax, {self.home_at(vreg, off)}")
            self.out("    test rax, rax")
            self.out(f"    jz {is_ok}")
            self._print_punct(7)  # err(
            self._print_scalar_at("int", vreg, off + 1)
            self._print_punct(8)  # )
            self.out(f"    jmp {done}")
            self.out(f"{is_ok}:")
            self._print_punct(6)   # ok(
            self._print_value_at(payload_t, vreg, off + 2)
            self._print_punct(8)  # )
            self.out(f"{done}:")
            return
        if base == "result":
            _err_t, pay_t, t_off = self._result_layout(typ)
            is_ok = self._fresh("pok")
            done = self._fresh("pdone")
            self.out(f"    mov rax, {self.home_at(vreg, off)}")
            self.out("    test rax, rax")
            self.out(f"    jz {is_ok}")
            self._print_punct(7)  # err(
            self._print_value_at(_err_t, vreg, off + 1)
            self._print_punct(8)  # )
            self.out(f"    jmp {done}")
            self.out(f"{is_ok}:")
            self._print_punct(6)   # ok(
            self._print_value_at(pay_t, vreg, off + t_off)
            self._print_punct(8)  # )
            self.out(f"{done}:")
            return
        if base == "list":
            elem_t = _agtypes.canonical(node[2][0])
            cap = node[2][1][1]
            elem_w = self._type_width(elem_t)
            self._print_punct(0)  # [
            for i in range(cap):
                skip = self._fresh("pelskip")
                self.out(f"    mov rax, {self.home_at(vreg, off)}")
                self.out(f"    cmp rax, {i + 1}")
                self.out(f"    jb {skip}")
                if i > 0:
                    self._print_punct(2)  # ,
                self._print_value_at(elem_t, vreg, off + 1 + i * elem_w)
                self.out(f"{skip}:")
            self._print_punct(1)  # ]
            return
        if base == "map":
            key_t = _agtypes.canonical(node[2][0])
            val_t = _agtypes.canonical(node[2][1])
            cap = node[2][2][1]
            key_w = self._type_width(key_t)
            val_w = self._type_width(val_t)
            slot_w = 1 + key_w + val_w
            self._print_punct(3)  # {
            # First-printed flag in claimed stack bytes (calls preserve
            # rsp; nested maps claim their own symmetric window).
            self.out("    sub rsp, 16")
            self.out("    mov qword [rsp], 0")
            for j in range(cap):
                skip = self._fresh("pmskip")
                nosep = self._fresh("pmnosep")
                self.out(f"    mov rax, {self.home_at(vreg, off + 1 + j * slot_w)}")
                self.out("    test rax, rax")
                self.out(f"    jz {skip}")
                self.out("    cmp qword [rsp], 0")
                self.out(f"    je {nosep}")
                self._print_punct(2)  # ,
                self.out(f"{nosep}:")
                self.out("    mov qword [rsp], 1")
                self._print_value_at(key_t, vreg, off + 1 + j * slot_w + 1)
                self._print_punct(5)  # :
                self._print_value_at(val_t, vreg, off + 1 + j * slot_w + 1 + key_w)
                self.out(f"{skip}:")
            self.out("    add rsp, 16")
            self._print_punct(4)  # }
            return
        raise ValueError(f"emitter: cannot print {typ!r}")

    def _print_punct(self, index: int) -> None:
        text = self._PUNCT[index]
        self.out(f"    lea rdi, [rel _rlpunc_{index}]")
        self.out(f"    mov rsi, {len(text)}")
        self.out(f"    call {_mangle('rt_print_str')}")

    def _print_scalar_at(self, typ: str, vreg: str, off: int) -> None:
        # Print one scalar value at a vreg home offset (slots, subtractive
        # like all home addressing -- never byte arithmetic on strings).
        helper = {"int": "rt_print_int", "bool": "rt_print_bool",
                  "str": "rt_print_str"}[typ]
        self.out(f"    mov rdi, {self.home_at(vreg, off)}")
        if typ == "str":
            self.out(f"    mov rsi, {self.home_at(vreg, off + 1)}")
        self.out(f"    call {_mangle(helper)}")

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
        # Pre-warm record sizes for every vreg type so the shared
        # allocator sees complete widths (order-independent).
        for typ in self.vreg_type.values():
            if isinstance(typ, str) and typ not in ("int", "bool", "str"):
                self._type_size(typ)
        self.slot_of, self.frameslots = _rir.assign_slots(
            func["blocks"], self.vreg_type, len(func["params"]), self._rec_sizes)

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
            instrs = block["instrs"]
            term = block["term"]
            if self._is_tail_call(func, block, instrs, term):
                for instr in instrs[:-1]:
                    self._emit_instr(func, instr)
                self._emit_tail_call(func, instrs[-1])
                continue
            for instr in instrs:
                self._emit_instr(func, instr)
            self._emit_term(func, block, term)
        self.out("")

    def _is_tail_call(self, func: dict, block: dict, instrs: list, term: dict) -> bool:
        # Backend peephole (Stage 19e): a direct user-function call
        # whose value flows straight into `ret` needs no frame of its
        # own — teardown and jump instead of call and return, so
        # tail-recursive programs run in constant stack. Sound by
        # construction: the call is the block's last instruction (no
        # pending work), the term returns exactly its value (or both
        # are valueless), runtime helpers are excluded (foreign frame
        # protocol), the value is used nowhere else, and — critically
        # — the call needs no stack slots: with slots the callee entry
        # state cannot be reproduced without fresh space below the
        # rewound frame (per-iteration drift), so those stay normal
        # calls (correct, linear stack). Only all-register calls
        # (scalars, no sret) take the jump.
        if not instrs or not isinstance(term, dict) or term.get("op") != "ret":
            return False
        instr = instrs[-1]
        if not isinstance(instr, dict) or instr.get("op") != "call":
            return False
        name = instr.get("name")
        if not isinstance(name, str) or name in _rir.RT_HELPERS:
            return False
        if "dst" in instr:
            if term.get("v") != instr["dst"]:
                return False
            if self.vreg_type[instr["dst"]] not in ("int", "bool", "str"):
                return False
            if self._vreg_used_elsewhere(func, block, instr["dst"]):
                return False
        elif term.get("v") is not None:
            return False
        _ret_agg, _regs, stack, sbytes = self._call_plan(instr)
        if sbytes:
            return False
        return True

    def _vreg_used_elsewhere(self, func: dict, block: dict, vreg: str) -> bool:
        for other in func["blocks"]:
            if other is block:
                continue
            for instr in other["instrs"]:
                if not isinstance(instr, dict):
                    continue
                for value in instr.values():
                    if value == vreg or (isinstance(value, list) and vreg in value):
                        return True
            term = other.get("term", {})
            if not isinstance(term, dict):
                continue
            for value in term.values():
                if value == vreg or (isinstance(value, list) and vreg in value):
                    return True
        return False

    def _emit_tail_call(self, func: dict, instr: dict) -> None:
        # All-register tail call only (the gate guarantees sbytes == 0):
        # marshal into registers, drop our own frame, and jump. No
        # stack slots are written and no dummy is pushed, so repeated
        # tail calls reuse the identical stack shape every iteration
        # (constant space, proven by the constricted-stack tests). The
        # return-address slot above the rewound frame still holds our
        # caller's address (nothing writes there), so the callee's
        # normal `leave; ret` lands directly in our caller.
        _ret_agg, regs, _stack, _sbytes = self._call_plan(instr)
        for arg, reg in regs:
            self._emit_arg_to_reg(arg, reg)
        self.out("    mov rsp, rbp")
        self.out("    pop rbp")
        self.out(f'    jmp {_mangle(instr["name"])}')

    def _spill_params(self, func: dict) -> None:
        # Aggregate returns arrive with the hidden slot at stack slot 0
        # (sret); user params shift right by one stack slot while registers
        # are undisturbed. Scalar functions behave byte-identically.
        sret = func["ret"] is not None and func["ret"] not in ("int", "bool", "str")
        slot = 0
        stack_slot = 1 if sret else 0
        for index, param in enumerate(func["params"]):
            home = f"[rbp - {8 * (self.slot_of[f'%{index}'] + 1)}]"
            width = self._param_width(param["type"])
            if slot < len(_ARG_REGS):
                if width > 1 and slot + width > len(_ARG_REGS):
                    # Whole value goes to the stack; never split across
                    # the register/stack boundary.
                    self._spill_param_stack(home, index, stack_slot, width)
                    stack_slot += width
                    slot += width
                    continue
                if param["type"] == "str":
                    reg = _ARG_REGS[slot]
                    self.out(f"    mov {home}, {reg}")
                    nxt = f"[rbp - {8 * (self.slot_of[f'%{index}'] + 2)}]"
                    self.out(f"    mov {nxt}, {_ARG_REGS[slot + 1]}")
                    slot += 2
                    continue
                if width == 1:
                    self.out(f"    mov {home}, {_ARG_REGS[slot]}")
                    slot += 1
                    continue
                for part in range(width):
                    self.out(f"    mov rax, {_ARG_REGS[slot + part]}")
                    self.out(f"    mov {self.home_at(f'%{index}', part)}, rax")
                slot += width
            else:
                self._spill_param_stack(home, index, stack_slot, width)
                stack_slot += width
                slot += width

    def _param_width(self, typ: str) -> int:
        if typ == "str":
            return 2
        if typ in ("int", "bool"):
            return 1
        return self._type_width(typ)

    def _spill_param_stack(self, home: str, index: int, stack_slot: int, width: int = 0) -> None:
        # Incoming stack slot k lives at [rbp+16+8k]. Keep a separate stack
        # index because an atomic str that does not fit in the last register
        # begins at stack slot zero even though its logical ABI slot is five.
        if width == 0:
            width = 2 if self.vreg_type[f"%{index}"] == "str" else 1
        for part in range(width):
            k = stack_slot + part
            self.out("    mov rax, [rbp + %d]" % (16 + 8 * k))
            if part == 0:
                self.out(f"    mov {home}, rax")
            else:
                self.out(f"    mov {self.home_at(f'%{index}', part)}, rax")

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
        elif op in ("make_record", "get_field", "make_list", "list_len",
                    "list_idx", "list_push", "make_map", "map_get",
                    "map_insert", "map_len", "str_len", "str_byte_at",
                    "str_fread", "str_fjoin", "str_argv",
                    "status_is_ok", "status_is_err", "status_unwrap_or",
                    "result_ok", "result_err", "unwrap_ok", "unwrap_err",
                    "print_agg"):
            self._emit_agg(func, instr)
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
        typ = instr["type"]
        width = self._type_width(typ) if typ not in ("int", "bool") else 1
        if width == 1 and typ != "str":
            self.load_reg("rax", instr["src"])
            self.store_reg("rax", instr["dst"])
            return
        if typ == "str":
            self.load_reg("rax", instr["src"])
            self.store_reg("rax", instr["dst"])
            src2 = f"[rbp - {8 * (self.slot_of[instr['src']] + 2)}]"
            dst2 = f"[rbp - {8 * (self.slot_of[instr['dst']] + 2)}]"
            self.out(f"    mov rax, {src2}")
            self.out(f"    mov {dst2}, rax")
            return
        self._emit_copy_range(self.home(instr["dst"]), self.home(instr["src"]), width)

    def _emit_binop(self, func: dict, instr: dict) -> None:
        op = instr["operator"]
        dst = instr["dst"]
        if op in ("+", "-", "*", "/", "%"):
            self._emit_arith(func, instr)
        elif op in ("&", "|", "^", "<<", ">>"):
            self._emit_bitop(instr)
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

    def _emit_bitop(self, instr: dict) -> None:
        # Two's-complement wrap; shifts mask the count (x86 masks 64-bit
        # counts implicitly, but the mask is explicit for auditability).
        op = instr["operator"]
        self.load_reg("rax", instr["l"])
        self.load_reg("rcx", instr["r"])
        if op in ("<<", ">>"):
            self.out("    and rcx, 63")
            self.out(f"    {'shl' if op == '<<' else 'sar'} rax, cl")
        else:
            self.out(f"    {'and' if op == '&' else 'or' if op == '|' else 'xor'} rax, rcx")
        self.store_reg("rax", instr["dst"])

    def _emit_compare(self, func: dict, instr: dict) -> None:
        op = instr["operator"]
        dst = instr["dst"]
        typ = self.vreg_type[instr["l"]]
        if typ == "str":
            if op not in ("==", "!="):
                raise ValueError("emitter: str ordering is not supported")
            self._emit_str_compare(func, instr)
            return
        if typ not in ("int", "bool"):
            if op not in ("==", "!="):
                raise ValueError("emitter: aggregate ordering is not supported")
            self._emit_agg_eq(func, instr)
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
        # Direction flag is ambient process state: pin it explicitly so a
        # caller-set DF can never turn this bounded scan into a backwards
        # OOB read with a wrong equality result.
        self.out("    cld")
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
        elif op == "~":
            self.out("    not rax")
        else:
            raise ValueError(f"emitter: unknown unop {op!r}")
        self.store_reg("rax", instr["dst"])

    # -- aggregates ---------------------------------------------------
    def _emit_agg(self, func: dict, instr: dict) -> None:
        op = instr["op"]
        if op == "make_record":
            self._emit_make_record(instr)
        elif op == "get_field":
            self._emit_get_field(instr)
        elif op == "make_list":
            self._emit_make_list(instr)
        elif op == "list_len":
            self.out(f"    mov rax, {self.home(instr['seq'])}")
            self.store_reg("rax", instr["dst"])
        elif op == "map_len":
            self.out(f"    mov rax, {self.home(instr['map'])}")
            self.store_reg("rax", instr["dst"])
        elif op == "str_len":
            self.out(f"    mov rax, {self.home_at(instr['v'], 1)}")
            self.store_reg("rax", instr["dst"])
        elif op == "list_idx":
            self._emit_list_idx(instr)
        elif op == "list_push":
            self._emit_list_push(instr)
        elif op == "make_map":
            self._emit_make_map(instr)
        elif op == "map_get":
            self._emit_map_get(instr)
        elif op == "map_insert":
            self._emit_map_insert(instr)
        elif op == "str_byte_at":
            self._emit_str_byte_at(instr)
        elif op == "str_fread":
            self._emit_rt_str_status(instr, "rt_fread",
                                     ["path", "offset", "length"])
        elif op == "str_fjoin":
            self._emit_rt_str_status(instr, "rt_fjoin",
                                     ["directory", "rel"])
        elif op == "str_argv":
            self._emit_rt_str_status(instr, "rt_argv", ["index"])
        elif op == "status_is_ok":
            self.out(f"    mov rax, {self.home(instr['v'])}")
            self.out("    xor rax, 1")
            self.store_reg("rax", instr["dst"])
        elif op == "status_is_err":
            self.out(f"    mov rax, {self.home(instr['v'])}")
            self.store_reg("rax", instr["dst"])
        elif op == "status_unwrap_or":
            self._emit_unwrap_or(instr)
        elif op == "result_ok" or op == "result_err":
            self._emit_result_make(instr)
        elif op == "unwrap_ok" or op == "unwrap_err":
            self._emit_unwrap_path(instr)
        elif op == "print_agg":
            self._emit_print_agg(instr)
        else:
            raise ValueError(f"emitter: unknown aggregate opcode {op!r}")

    def _emit_result_make(self, instr: dict) -> None:
        dst = instr["dst"]
        typ = instr["type"]
        err_t, pay_t, t_off = self._result_layout(typ)
        width = self._type_width(typ)
        self._emit_zero_range(self.home(dst), width)
        if instr["op"] == "result_ok":
            self._emit_copy_range(self.home_at(dst, t_off), self.home(instr["val"]), self._type_width(pay_t))
        else:
            self.out("    mov rax, 1")
            self.out(f"    mov {self.home(dst)}, rax")
            self._emit_copy_range(self.home_at(dst, 1), self.home(instr["val"]), self._type_width(err_t))

    def _emit_unwrap_path(self, instr: dict) -> None:
        # Path-validated extraction (the builder emits these only on the
        # taken arm, dominated by the tag test): straight payload copy,
        # no tag check, no trap.
        dst = instr["dst"]
        vtype = self.vreg_type[instr["v"]]
        node = _agtypes.parse_type(vtype)
        if node is not None and node[0] == "generic" and node[1] == "result":
            err_t = _agtypes.canonical(node[2][1])
            pay_t = _agtypes.canonical(node[2][0])
            if instr["op"] == "unwrap_ok":
                src_off, width = 1 + self._type_width(err_t), self._type_width(pay_t)
            else:
                src_off, width = 1, self._type_width(err_t)
        elif instr["op"] == "unwrap_ok":
            src_off = 2
            width = self._type_width(_agtypes.canonical(node[2][0]))
        else:
            src_off, width = 1, 1
        self._emit_copy_range(self.home(dst), self.home_at(instr["v"], src_off), width)

    def _status_ok(self, dst: str) -> None:
        # tag=0, code=0 at the status home (payload stored separately).
        self.out("    xor eax, eax")
        self.out(f"    mov {self.home(dst)}, rax")
        self.out(f"    mov {self.home_at(dst, 1)}, rax")

    def _status_err(self, dst: str, code: int, payload_slots: int) -> None:
        # tag=1, code=<code>, payload zeroed (err payloads are always
        # zero so status equality stays deterministic).
        self.out("    mov rax, 1")
        self.out(f"    mov {self.home(dst)}, rax")
        self.out(f"    mov rax, {code}")
        self.out(f"    mov {self.home_at(dst, 1)}, rax")
        self._emit_zero_range(self.home_at(dst, 2), payload_slots)

    def _emit_make_record(self, instr: dict) -> None:
        dst = instr["dst"]
        rectype = instr["type"]
        width = self._type_width(rectype)
        self._emit_zero_range(self.home(dst), width)
        layout = self._field_layout(rectype)
        for temp, (_fname, ftype, off) in zip(instr["args"], layout):
            w = self._type_width(ftype)
            self._emit_copy_range(self.home_at(dst, off), self.home(temp), w)

    def _emit_get_field(self, instr: dict) -> None:
        rtype = self.vreg_type[instr["rec"]]
        layout = self._field_layout(rtype)
        off = next(o for n, _t, o in layout if n == instr["field"])
        w = self._type_width(instr["type"])
        self._emit_copy_range(self.home(instr["dst"]), self.home_at(instr["rec"], off), w)

    def _emit_make_list(self, instr: dict) -> None:
        dst = instr["dst"]
        typ = instr["type"]
        node = _agtypes.parse_type(typ)
        elem_t = _agtypes.canonical(node[2][0])
        width = self._type_width(typ)
        elem_w = self._type_width(elem_t)
        self._emit_zero_range(self.home(dst), width)
        self.out(f"    mov rax, {len(instr['args'])}")
        self.out(f"    mov {self.home(dst)}, rax")
        for index, temp in enumerate(instr["args"]):
            self._emit_copy_range(self.home_at(dst, 1 + index * elem_w), self.home(temp), elem_w)

    def _result_layout(self, typ: str):
        # (E, T, T-offset-slots) for result<T,E>: tag @0, E @1, T after E.
        node = _agtypes.parse_type(typ)
        err_t = _agtypes.canonical(node[2][1])
        pay_t = _agtypes.canonical(node[2][0])
        return err_t, pay_t, 1 + self._type_width(err_t)

    def _emit_unwrap_or(self, instr: dict) -> None:
        dst = instr["dst"]
        typ = instr["type"]
        width = self._type_width(typ)
        is_ok = self._fresh("unwok")
        done = self._fresh("unwdone")
        self.out(f"    mov rax, {self.home(instr['v'])}")
        self.out("    test rax, rax")
        self.out(f"    jz {is_ok}")
        self._emit_copy_range(self.home(dst), self.home(instr["default"]), width)
        self.out(f"    jmp {done}")
        self.out(f"{is_ok}:")
        vtype = self.vreg_type[instr["v"]]
        node = _agtypes.parse_type(vtype)
        src_off = 2
        if node is not None and node[0] == "generic" and node[1] == "result":
            # Result ok-payload sits after the err payload.
            src_off = 1 + self._type_width(_agtypes.canonical(node[2][1]))
        self._emit_copy_range(self.home(dst), self.home_at(instr["v"], src_off), width)
        self.out(f"{done}:")

    def _emit_list_idx(self, instr: dict) -> None:
        dst = instr["dst"]
        seq_type = self.vreg_type[instr["seq"]]
        node = _agtypes.parse_type(seq_type)
        elem_t = _agtypes.canonical(node[2][0])
        elem_w = self._type_width(elem_t)
        oor = self._fresh("idxoor")
        done = self._fresh("idxdone")
        self.out(f"    mov rax, {self.home(instr['index'])}")
        self.out("    test rax, rax")
        self.out(f"    js {oor}")
        self.out(f"    cmp rax, {self.home(instr['seq'])}")
        self.out(f"    jae {oor}")
        # ok: tag=0, code=0, payload = elem[idx] (elem size is a multiple
        # of 8 by the padding-free layouts; home-relative addresses
        # descend, so the dynamic scale subtracts).
        self._status_ok(dst)
        self.out(f"    mov rax, {self.home(instr['index'])}")
        self.out(f"    imul rax, rax, {elem_w * 8}")
        self.out(f"    lea rsi, {self.home(instr['seq'])}")
        self.out("    sub rsi, rax")
        self.out("    sub rsi, 8")
        self._emit_copy_range(self.home_at(dst, 2), "[rsi]", elem_w)
        self.out(f"    jmp {done}")
        self.out(f"{oor}:")
        self._status_err(dst, _agtypes.ERR_OORANGE, elem_w)
        self.out(f"{done}:")

    def _emit_list_push(self, instr: dict) -> None:
        dst = instr["dst"]
        seq_type = self.vreg_type[instr["seq"]]
        node = _agtypes.parse_type(seq_type)
        elem_t = _agtypes.canonical(node[2][0])
        cap = node[2][1][1]
        elem_w = self._type_width(elem_t)
        list_w = self._type_width(seq_type)
        full = self._fresh("pushfull")
        done = self._fresh("pushdone")
        self.out(f"    mov rax, {self.home(instr['seq'])}")
        self.out(f"    cmp rax, {cap}")
        self.out(f"    jae {full}")
        self._status_ok(dst)
        # Copy the whole list home, then store elem at len and bump len
        # (home-relative: element len lives len slots below the base).
        self._emit_copy_range(self.home_at(dst, 2), self.home(instr["seq"]), list_w)
        self.out(f"    mov rax, {self.home_at(dst, 2)}")
        self.out(f"    imul rax, rax, {elem_w * 8}")
        self.out(f"    lea rsi, {self.home_at(dst, 2)}")
        self.out("    sub rsi, rax")
        self.out("    sub rsi, 8")
        self._emit_copy_range("[rsi]", self.home(instr["val"]), elem_w)
        self.out(f"    mov rax, {self.home_at(dst, 2)}")
        self.out("    inc rax")
        self.out(f"    mov {self.home_at(dst, 2)}, rax")
        self.out(f"    jmp {done}")
        self.out(f"{full}:")
        self._status_err(dst, _agtypes.ERR_FULL, list_w)
        self.out(f"{done}:")

    def _emit_make_map(self, instr: dict) -> None:
        dst = instr["dst"]
        width = self._type_width(instr["type"])
        # Empty map: count 0 with all slots zeroed (tags empty).
        self._emit_zero_range(self.home(dst), width)
        if instr["args"]:
            self._emit_map_fill(instr, dst)

    def _emit_str_byte_at(self, instr: dict) -> None:
        dst = instr["dst"]
        oor = self._fresh("byoor")
        done = self._fresh("bydone")
        self.out(f"    mov rax, {self.home(instr['index'])}")
        self.out("    test rax, rax")
        self.out(f"    js {oor}")
        self.out(f"    cmp rax, {self.home_at(instr['v'], 1)}")
        self.out(f"    jae {oor}")
        self._status_ok(dst)
        self.out(f"    mov rsi, {self.home(instr['v'])}")
        self.out(f"    mov rax, {self.home(instr['index'])}")
        self.out("    movzx eax, byte [rsi + rax]")
        self.out(f"    mov {self.home_at(dst, 2)}, rax")
        self.out(f"    jmp {done}")
        self.out(f"{oor}:")
        self._status_err(dst, _agtypes.ERR_OORANGE, 1)
        self.out(f"{done}:")

    def _emit_rt_str_status(self, instr: dict, helper: str, fields: list) -> None:
        # Marshal str/int homes into SysV regs (str takes two), call the
        # runtime helper (rax = byte count or negative err code, rdx =
        # result buffer), and fill the status<str> home. Only
        # caller-saved registers are touched; homes live under rbp.
        regs = iter(("rdi", "rsi", "rdx", "rcx"))
        for field in fields:
            home = self.home(instr[field])
            vtype = self.vreg_type[instr[field]]
            if vtype == "str":
                self.out(f"    mov {next(regs)}, {home}")
                self.out(f"    mov {next(regs)}, {self.home_at(instr[field], 1)}")
            else:
                self.out(f"    mov {next(regs)}, {home}")
        self.out(f"    call {_mangle(helper)}")
        dst = instr["dst"]
        err = self._fresh("rter")
        done = self._fresh("rtdone")
        self.out("    test rax, rax")
        self.out(f"    js {err}")
        self.out("    xor ecx, ecx")
        self.out(f"    mov {self.home(dst)}, rcx")
        self.out(f"    mov {self.home_at(dst, 1)}, rcx")
        self.out(f"    mov {self.home_at(dst, 2)}, rdx")
        self.out(f"    mov {self.home_at(dst, 3)}, rax")
        self.out(f"    jmp {done}")
        self.out(f"{err}:")
        self.out("    neg rax")
        self.out("    mov rcx, 1")
        self.out(f"    mov {self.home(dst)}, rcx")
        self.out(f"    mov {self.home_at(dst, 1)}, rax")
        self.out("    xor ecx, ecx")
        self.out(f"    mov {self.home_at(dst, 2)}, rcx")
        self.out(f"    mov {self.home_at(dst, 3)}, rcx")
        self.out(f"{done}:")
    # Key bytes: int = 8-byte LE of the u64 bits; bool = low byte (homes
    # hold canonical 0/1); str = raw bytes. Same algorithm in the oracle
    # (re-derived) and the analyzer-visible freeze; differentials prove it.
    def _emit_hash(self, key_type: str, key_home: str) -> None:
        # rcx = FNV-1a-64(key bytes). Clobbers rax, rcx, rdx, rsi, r8, r10.
        self.out("    mov rcx, 14695981039346656037")
        self.out("    mov r10, 1099511628211")
        if key_type == "bool":
            self.out(f"    movzx edx, byte {key_home}")
            self.out("    xor rcx, rdx")
            self.out("    imul rcx, r10")
        elif key_type == "int":
            self.out(f"    mov rdx, {key_home}")
            self.out("    mov r8, 8")
            loop = self._fresh("fnvb")
            self.out(f"{loop}:")
            self.out("    movzx eax, dl")
            self.out("    xor rcx, rax")
            self.out("    imul rcx, r10")
            self.out("    shr rdx, 8")
            self.out("    dec r8")
            self.out(f"    jnz {loop}")
        else:  # str: (ptr,len) home pair
            self.out(f"    mov rsi, {key_home}")
            self.out(f"    mov r8, {self._plus8(key_home)}")
            loop = self._fresh("fnvs")
            empty = self._fresh("fnvse")
            self.out("    test r8, r8")
            self.out(f"    jz {empty}")
            self.out(f"{loop}:")
            self.out("    movzx edx, byte [rsi]")
            self.out("    xor rcx, rdx")
            self.out("    imul rcx, r10")
            self.out("    inc rsi")
            self.out("    dec r8")
            self.out(f"    jnz {loop}")
            self.out(f"{empty}:")

    def _plus8(self, home: str) -> str:
        # Second slot of a two-slot home: one slot DOWN (higher slot
        # numbers live at lower addresses).
        base = int(re.search(r"rbp - (\d+)", home).group(1))
        return f"[rbp - {base + 8}]"

    def _emit_key_eq(self, key_type: str, slot_reg: str, key_off: int, want_home: str, equal_label: str) -> None:
        # Compare the key `key_off` slots below the slot address in
        # slot_reg with the wanted key home (home-relative addresses
        # descend); jump to equal_label on equality, fall through on
        # mismatch. Clobbers rax, rcx, rdx, rsi, rdi, r8.
        miss = self._fresh("keyne")
        if key_type in ("int", "bool"):
            self.out(f"    mov rax, [{slot_reg} - {8 * key_off}]")
            self.out(f"    cmp rax, {want_home}")
            self.out(f"    je {equal_label}")
            return
        self.out(f"    mov rax, {want_home}")
        self.out(f"    mov rdx, {self._plus8(want_home)}")
        self.out(f"    cmp rdx, [{slot_reg} - {8 * key_off + 8}]")
        self.out(f"    jne {miss}")
        self.out(f"    mov rsi, [{slot_reg} - {8 * key_off}]")
        self.out(f"    mov rdi, {want_home}")
        self.out("    mov rcx, rdx")
        self.out("    cld")
        self.out("    repe cmpsb")
        self.out(f"    je {equal_label}")
        self.out(f"{miss}:")

    def _emit_probe(self, map_home: str, key_home: str, key_type: str,
                    slot_bytes: int, cap: int, empty_cb, hit_cb, miss_cb) -> None:
        # Open-addressing probe loop over a map home. Python callables run
        # at decision points (slot address in r11, index in rdx on entry).
        # Callbacks must preserve r10 (map base) unless they jump out;
        # r11/rax/rcx/rdx/rsi/rdi/r8/r9 are scratch at callback entry
        # (key_eq clobbers are fenced by balanced push/pop on every path).
        self.out(f"    lea r10, {map_home}")
        loop = self._fresh("probe")
        nxt = self._fresh("pnxt")
        nowrap = self._fresh("pwrap")
        hteq = self._fresh("phit")
        self.out(f"{loop}:")
        self.out(f"    imul r11, rdx, {slot_bytes}")
        self.out("    mov rax, r10")
        self.out("    sub rax, r11")
        self.out("    lea r11, [rax - 8]")
        self.out("    mov rax, [r11]")
        self.out("    test rax, rax")
        empty = self._fresh("pempty")
        self.out(f"    jz {empty}")
        self.out("    push rcx")
        self.out("    push rdx")
        self.out("    push r8")
        self._emit_key_eq(key_type, "r11", 1, key_home, hteq)
        self.out("    pop r8")
        self.out("    pop rdx")
        self.out("    pop rcx")
        self.out(f"    jmp {nxt}")
        self.out(f"{hteq}:")
        self.out("    pop r8")
        self.out("    pop rdx")
        self.out("    pop rcx")
        hit_cb()
        self.out(f"{empty}:")
        empty_cb()
        self.out(f"{nxt}:")
        self.out("    inc rdx")
        self.out(f"    cmp rdx, {cap}")
        self.out(f"    jb {nowrap}")
        self.out("    xor edx, edx")
        self.out(f"{nowrap}:")
        self.out("    inc r8")
        self.out(f"    cmp r8, {cap}")
        self.out(f"    jb {loop}")
        miss_cb()

    def _map_layout(self, map_type: str):
        node = _agtypes.parse_type(map_type)
        key_t = _agtypes.canonical(node[2][0])
        val_t = _agtypes.canonical(node[2][1])
        cap = node[2][2][1]
        key_w = self._type_width(key_t)
        val_w = self._type_width(val_t)
        slot_bytes = 8 + 8 * key_w + 8 * val_w
        return cap, key_t, val_t, key_w, val_w, slot_bytes

    def _emit_map_get(self, instr: dict) -> None:
        dst = instr["dst"]
        map_type = self.vreg_type[instr["map"]]
        cap, key_t, val_t, key_w, val_w, slot_bytes = self._map_layout(map_type)
        miss = self._fresh("getmiss")
        done = self._fresh("getdone")
        self._emit_hash(key_t, self.home(instr["key"]))
        self.out("    mov rax, rcx")
        self.out("    xor edx, edx")
        self.out(f"    mov r10, {cap}")
        self.out("    div r10")
        self.out("    xor r8d, r8d")

        def on_hit() -> None:
            self._status_ok(dst)
            self._emit_copy_range(self.home_at(dst, 2), f"[r11 - {8 + 8 * key_w}]", val_w)
            self.out(f"    jmp {done}")

        def on_empty() -> None:
            self.out(f"    jmp {miss}")

        def on_miss() -> None:
            self.out(f"{miss}:")
            self._status_err(dst, _agtypes.ERR_NOTFOUND, val_w)

        self._emit_probe(self.home(instr["map"]), self.home(instr["key"]), key_t,
                         slot_bytes, cap, on_empty, on_hit, on_miss)
        self.out(f"{done}:")

    def _emit_map_insert(self, instr: dict) -> None:
        dst = instr["dst"]
        map_type = self.vreg_type[instr["map"]]
        cap, key_t, val_t, key_w, val_w, slot_bytes = self._map_layout(map_type)
        map_w = self._type_width(map_type)
        full = self._fresh("insfull")
        done = self._fresh("insdone")
        self._emit_hash(key_t, self.home(instr["key"]))
        self.out("    mov rax, rcx")
        self.out("    xor edx, edx")
        self.out(f"    mov r10, {cap}")
        self.out("    div r10")
        self.out("    xor r8d, r8d")
        self.out("    mov r9, -1")

        def on_hit() -> None:
            # Key present: copy the map, overwrite the value, bump nothing.
            self._emit_copy_range(self.home_at(dst, 2), self.home(instr["map"]), map_w)
            self._status_ok(dst)
            self.out(f"    lea r11, {self.home_at(dst, 2)}")
            self.out(f"    imul rax, rdx, {slot_bytes}")
            self.out("    sub r11, rax")
            self.out("    sub r11, 8")
            self._emit_copy_range(f"[r11 - {8 + 8 * key_w}]", self.home(instr["val"]), val_w)
            self.out(f"    jmp {done}")

        def on_empty() -> None:
            self.out("    cmp r9, -1")
            none_yet = self._fresh("noneyet")
            self.out(f"    jne {none_yet}")
            self.out("    mov r9, rdx")
            self.out(f"{none_yet}:")

        def on_miss() -> None:
            self.out("    cmp r9, -1")
            self.out(f"    je {full}")
            self._emit_copy_range(self.home_at(dst, 2), self.home(instr["map"]), map_w)
            self._status_ok(dst)
            self.out(f"    lea r11, {self.home_at(dst, 2)}")
            self.out(f"    imul rax, r9, {slot_bytes}")
            self.out("    sub r11, rax")
            self.out("    sub r11, 8")
            self.out("    mov rax, 1")
            self.out("    mov [r11], rax")
            self._emit_copy_range("[r11 - 8]", self.home(instr["key"]), key_w)
            self._emit_copy_range(f"[r11 - {8 + 8 * key_w}]", self.home(instr["val"]), val_w)
            self.out(f"    mov rax, {self.home_at(dst, 2)}")
            self.out("    inc rax")
            self.out(f"    mov {self.home_at(dst, 2)}, rax")
            self.out(f"    jmp {done}")
            self.out(f"{full}:")
            self._status_err(dst, _agtypes.ERR_FULL, map_w)

        self._emit_probe(self.home(instr["map"]), self.home(instr["key"]), key_t,
                         slot_bytes, cap, on_empty, on_hit, on_miss)
        self.out(f"{done}:")

    def _emit_map_fill(self, instr: dict, dst: str) -> None:
        # Literal entries into a fresh zeroed map (count starts 0). The
        # analyzer guarantees at most N static entries, so a free slot
        # always exists; dynamic duplicate keys update in place.
        map_type = instr["type"]
        cap, key_t, val_t, key_w, val_w, slot_bytes = self._map_layout(map_type)
        args = instr["args"]
        for index in range(0, len(args), 2):
            key_tmp, val_tmp = args[index], args[index + 1]
            entry_done = self._fresh("filldone")
            self._emit_hash(key_t, self.home(key_tmp))
            self.out("    mov rax, rcx")
            self.out("    xor edx, edx")
            self.out(f"    mov r10, {cap}")
            self.out("    div r10")
            self.out("    xor r8d, r8d")

            def on_hit(entry_done=entry_done) -> None:
                self._emit_copy_range(f"[r11 - {8 + 8 * key_w}]", self.home(val_tmp), val_w)
                self.out(f"    jmp {entry_done}")

            def on_empty(entry_done=entry_done, key_tmp=key_tmp, val_tmp=val_tmp) -> None:
                self.out("    mov rax, 1")
                self.out("    mov [r11], rax")
                self._emit_copy_range("[r11 - 8]", self.home(key_tmp), key_w)
                self._emit_copy_range(f"[r11 - {8 + 8 * key_w}]", self.home(val_tmp), val_w)
                self.out(f"    mov rax, {self.home(dst)}")
                self.out("    inc rax")
                self.out(f"    mov {self.home(dst)}, rax")
                self.out(f"    jmp {entry_done}")

            def on_miss(entry_done=entry_done) -> None:
                # Unreachable by construction (static entry count <= N);
                # deterministic fallthrough keeps the op total.
                self.out(f"    jmp {entry_done}")

            self._emit_probe(self.home(dst), self.home(key_tmp), key_t,
                             slot_bytes, cap, on_empty, on_hit, on_miss)
            self.out(f"{entry_done}:")

    def _emit_agg_eq(self, func: dict, instr: dict) -> None:
        # Content equality for records/lists (padding-free layouts make
        # bounded byte-compare sound; every construction path zeroes or
        # fully defines its home), logical equality for maps, tag+code+
        # payload for status. Result in al (0/1), stored like setcc.
        op = instr["operator"]
        dst = instr["dst"]
        typ = self.vreg_type[instr["l"]]
        base = self._fresh("eq")
        self._emit_eq_body(typ, instr["l"], 0, instr["r"], 0, f"{base}_ne")
        self.out("    mov al, 1")
        done = self._fresh("eqdone")
        self.out(f"    jmp {done}")
        self.out(f"{base}_ne:")
        self.out("    xor eax, eax")
        self.out(f"{done}:")
        self.out("    movzx rax, al")
        if op == "!=":
            self.out("    xor rax, 1")
        self.store_reg("rax", dst)

    def _emit_eq_body(self, typ: str, va: str, oa: int, vb: str, ob: int, ne_label: str) -> None:
        # Content equality by recursive static traversal (no string ops,
        # no direction subtleties: every comparison names its home slots
        # explicitly). Clobbers rax, rcx, rdx, rsi, rdi, r8-r11.
        if typ in ("int", "bool"):
            self.out(f"    mov rax, {self.home_at(va, oa)}")
            self.out(f"    cmp rax, {self.home_at(vb, ob)}")
            self.out(f"    jne {ne_label}")
            return
        if typ == "str":
            self.out(f"    mov rcx, {self.home_at(va, oa + 1)}")
            self.out(f"    cmp rcx, {self.home_at(vb, ob + 1)}")
            self.out(f"    jne {ne_label}")
            self.out(f"    mov rsi, {self.home_at(va, oa)}")
            self.out(f"    mov rdi, {self.home_at(vb, ob)}")
            self.out("    cld")
            self.out("    repe cmpsb")
            self.out(f"    jne {ne_label}")
            return
        node = _agtypes.parse_type(typ)
        if node[0] == "nominal":
            for _fname, ftype, off in self._field_layout(node[1]):
                self._emit_eq_body(ftype, va, oa + off, vb, ob + off, ne_label)
            return
        base = node[1]
        if base == "status":
            payload_t = _agtypes.canonical(node[2][0])
            self.out(f"    mov rax, {self.home_at(va, oa)}")
            self.out(f"    cmp rax, {self.home_at(vb, ob)}")
            self.out(f"    jne {ne_label}")
            self.out(f"    mov rax, {self.home_at(va, oa + 1)}")
            self.out(f"    cmp rax, {self.home_at(vb, ob + 1)}")
            self.out(f"    jne {ne_label}")
            self._emit_eq_body(payload_t, va, oa + 2, vb, ob + 2, ne_label)
            return
        if base == "result":
            err_t, pay_t, t_off = self._result_layout(typ)
            self.out(f"    mov rax, {self.home_at(va, oa)}")
            self.out(f"    cmp rax, {self.home_at(vb, ob)}")
            self.out(f"    jne {ne_label}")
            self._emit_eq_body(err_t, va, oa + 1, vb, ob + 1, ne_label)
            self._emit_eq_body(pay_t, va, oa + t_off, vb, ob + t_off, ne_label)
            return
        if base == "list":
            elem_t = _agtypes.canonical(node[2][0])
            cap = node[2][1][1]
            elem_w = self._type_width(elem_t)
            self.out(f"    mov rax, {self.home_at(va, oa)}")
            self.out(f"    cmp rax, {self.home_at(vb, ob)}")
            self.out(f"    jne {ne_label}")
            for i in range(1, cap + 1):
                skip = self._fresh("eqskip")
                self.out(f"    mov rax, {self.home_at(va, oa)}")
                self.out(f"    cmp rax, {i}")
                self.out(f"    jb {skip}")
                self._emit_eq_body(elem_t, va, oa + 1 + (i - 1) * elem_w,
                                   vb, ob + 1 + (i - 1) * elem_w, ne_label)
                self.out(f"{skip}:")
            return
        if base == "map":
            self._emit_map_eq(node, va, oa, vb, ob, ne_label)
            return
        raise ValueError(f"emitter: cannot compare {typ!r}")

    def _emit_map_eq(self, node: tuple, va: str, oa: int, vb: str, ob: int, ne_label: str) -> None:
        # Logical map equality: equal counts, and every occupied A slot
        # has a key-equal slot in B with an equal value. O(N^2) static
        # unrolled scans (N static, bounded). Uses the stack-flag pattern
        # for nothing (all structure is static; per-slot found-flags live
        # in unrolled straight-line code with early exits).
        key_t = _agtypes.canonical(node[2][0])
        val_t = _agtypes.canonical(node[2][1])
        cap = node[2][2][1]
        key_w = self._type_width(key_t)
        val_w = self._type_width(val_t)
        slot_w = 1 + key_w + val_w
        self.out(f"    mov rax, {self.home_at(va, oa)}")
        self.out(f"    cmp rax, {self.home_at(vb, ob)}")
        self.out(f"    jne {ne_label}")
        for j in range(cap):
            skip = self._fresh("meqskip")
            self.out(f"    mov rax, {self.home_at(va, oa + 1 + j * slot_w)}")
            self.out("    test rax, rax")
            self.out(f"    jz {skip}")
            found = self._fresh("meqfound")
            for k in range(cap):
                next_k = self._fresh("meqnext")
                self.out(f"    mov rax, {self.home_at(vb, ob + 1 + k * slot_w)}")
                self.out("    test rax, rax")
                self.out(f"    jz {next_k}")
                self._emit_cmp_value(key_t, va, oa + 1 + j * slot_w + 1, vb, ob + 1 + k * slot_w + 1, next_k)
                self._emit_eq_body(val_t, va, oa + 1 + j * slot_w + 1 + key_w,
                                   vb, ob + 1 + k * slot_w + 1 + key_w, next_k)
                self.out(f"    jmp {found}")
                self.out(f"{next_k}:")
            self.out(f"    jmp {ne_label}")
            self.out(f"{found}:")
            self.out(f"{skip}:")

    def _emit_cmp_value(self, typ: str, va: str, oa: int, vb: str, ob: int, ne_label: str) -> None:
        # Scalar-only key comparison (map keys are int/bool/str).
        if typ in ("int", "bool"):
            self.out(f"    mov rax, {self.home_at(va, oa)}")
            self.out(f"    cmp rax, {self.home_at(vb, ob)}")
            self.out(f"    jne {ne_label}")
            return
        self.out(f"    mov rcx, {self.home_at(va, oa + 1)}")
        self.out(f"    cmp rcx, {self.home_at(vb, ob + 1)}")
        self.out(f"    jne {ne_label}")
        self.out(f"    mov rsi, {self.home_at(va, oa)}")
        self.out(f"    mov rdi, {self.home_at(vb, ob)}")
        self.out("    cld")
        self.out("    repe cmpsb")
        self.out(f"    jne {ne_label}")

    def _arg_width(self, arg: str) -> int:
        typ = self.vreg_type[arg]
        if typ == "str":
            return 2
        if typ in ("int", "bool"):
            return 1
        return self._type_width(typ)

    def _emit_arg_to_reg(self, arg: str, reg: str) -> None:
        width = self._arg_width(arg)
        if width == 1:
            self.load_reg("rax", arg)
            self.out(f"    mov {reg}, rax")
            return
        if self.vreg_type[arg] == "str":
            self.load_reg("rax", arg)
            self.out(f"    mov {reg}, rax")
            nxt = f"[rbp - {8 * (self.slot_of[arg] + 2)}]"
            self.out(f"    mov rax, {nxt}")
            # Second half follows the first in the register order.
            order = list(_ARG_REGS)
            self.out(f"    mov {order[order.index(reg) + 1]}, rax")
            return
        order = list(_ARG_REGS)
        base = order.index(reg)
        for index in range(width):
            self.out(f"    mov rax, {self.home_at(arg, index)}")
            self.out(f"    mov {order[base + index]}, rax")

    def _emit_arg_to_stack(self, arg: str, stack_index: int) -> None:
        width = self._arg_width(arg)
        k = stack_index * 8
        if width == 1:
            self.load_reg("rax", arg)
            self.out(f"    mov [rsp + {k}], rax")
            return
        if self.vreg_type[arg] == "str":
            self.load_reg("rax", arg)
            self.out(f"    mov [rsp + {k}], rax")
            nxt = f"[rbp - {8 * (self.slot_of[arg] + 2)}]"
            self.out(f"    mov rax, {nxt}")
            self.out(f"    mov [rsp + {k + 8}], rax")
            return
        for index in range(width):
            self.out(f"    mov rax, {self.home_at(arg, index)}")
            self.out(f"    mov [rsp + {k + 8 * index}], rax")

    def _call_plan(self, instr: dict):
        # Shared marshal plan for normal and tail calls: aggregate
        # returns take a caller-provided hidden slot at stack slot 0
        # (sret) with user params shifted right; registers undisturbed.
        # Returns (ret_agg, regs, stack, sbytes).
        args = instr["args"]
        ret_agg = "dst" in instr and self.vreg_type[instr["dst"]] not in ("int", "bool", "str")
        regs: list[tuple] = []
        stack: list[tuple] = []
        slot = 0
        nstack = 1 if ret_agg else 0
        for arg in args:
            width = self._arg_width(arg)
            if width > 1 and slot < len(_ARG_REGS) and slot + width > len(_ARG_REGS):
                stack.append((arg, nstack))
                nstack += width
                slot += width
            elif slot < len(_ARG_REGS):
                regs.append((arg, _ARG_REGS[slot]))
                slot += width
            else:
                stack.append((arg, nstack))
                nstack += width
                slot += width
        # Round stack bytes up to 16 so rsp%16==0 holds at the call.
        sbytes = (nstack * 8 + 15) // 16 * 16
        return ret_agg, regs, stack, sbytes

    def _emit_call(self, func: dict, instr: dict) -> None:
        # Aggregate returns use a caller-provided hidden slot passed as
        # stack slot 0 (sret); user params shift right by one stack slot
        # while registers are undisturbed. Scalars behave byte-identically
        # to before (no sret slot, same marshal).
        # Marshal left-to-right into SysV slots; a value crossing the
        # register/stack boundary moves wholly to the stack, never split.
        ret_agg, regs, stack, sbytes = self._call_plan(instr)
        if sbytes:
            self.out(f"    sub rsp, {sbytes}")
        if ret_agg:
            self.out(f"    lea rax, {self.home(instr['dst'])}")
            self.out("    mov [rsp + 0], rax")
        for arg, reg in regs:
            self._emit_arg_to_reg(arg, reg)
        for arg, stack_index in stack:
            self._emit_arg_to_stack(arg, stack_index)
        self.out(f'    call {_mangle(instr["name"])}')
        if sbytes:
            self.out(f"    add rsp, {sbytes}")
        if "dst" in instr and not ret_agg:
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
                vtype = self.vreg_type[value]
                if vtype == "str":
                    self.load_reg("rax", value)
                    nxt = f"[rbp - {8 * (self.slot_of[value] + 2)}]"
                    self.out(f"    mov rdx, {nxt}")
                elif vtype in ("int", "bool"):
                    self.load_reg("rax", value)
                else:
                    # Aggregate return via the caller's hidden slot at
                    # stack slot 0 (sret); rax is intentionally stale.
                    width = self._type_width(vtype)
                    self.out("    mov rdi, [rbp + 16]")
                    self._emit_copy_range("[rdi]", self.home(value), width)
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


def compile_source(source: str, filename: str = "<input>", profile: str = "default"):
    """Full pipeline: lex/parse/analyze -> RIR -> asm.

    Returns (asm_text, None) or (None, {"code","message"}) where code is a
    PAR_*/SEM_*/COMP_* diagnostic code. Never raises on bad input.
    """
    result = _analyze.analyze(source, filename, profile=profile)
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
    group.add_argument("--build", metavar="DIR",
                       help="build a host-native program into DIR (prog.asm/prog.o/rt_linux.o/prog)")
    group.add_argument("--run", action="store_true",
                       help="build to a temp dir, run it, forward its stdout; "
                            "exit code is the program's (diagnostics stay on stderr)")
    parser.add_argument("--profile", default="default", choices=("default", "strict", "core"),
                        help="build profile: default (current behavior), strict (19d reproducible lock), or core (19e self-host subset)")
    args = parser.parse_args(argv)
    if args.source is None:
        parser.print_usage(sys.stderr)
        return 2
    if args.build is not None or args.run:
        from tools.rynorlang import program as _program
        return _program.main_build(args)
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
    result = _analyze.analyze(source, str(args.source), profile=args.profile)
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
