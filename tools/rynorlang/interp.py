#!/usr/bin/env python3
"""TEST-ONLY differential oracle for the Stage 15a native backend.

This module is NOT code generation, NOT a runtime, and MUST NEVER ship in
any RynorOS image or be presented as execution of RynorLang. It exists for
exactly one purpose: independently evaluating verified RIR so that
``oracle(module) == native(module)`` can be asserted per fixture. Any
divergence is a BACKEND bug by definition.

Honesty rules (binding):
  1. The oracle consumes only verified RIR (it refuses invalid modules) built
     from the frozen Stage-14 analyzer output. It shares the frontend
     (lex/parse/analyze) and NOTHING of the backend (no import of compile.py,
     no ABI constants, no register model, no instruction selection).
  2. Integer semantics are re-derived from the C/x86-64 contract, not copied
     from the emitter: two's-complement wrap on +,-,*, trunc-toward-zero
     division, dividend-signed remainder, trap on zero divisor and on
     INT_MIN/-1, signed comparisons, eager canonical-bool logic.
  3. Evaluation is iterative over blocks with an explicit heap call stack: no
     Python recursion limit is reachable from legal input. A step budget and
     a call-depth budget turn non-termination into a reported trap instead of
     a hang, so differential tests always terminate.
  4. A passing differential suite proves nothing beyond the covered fixtures;
     the oracle alone never closes a stage.
"""

from __future__ import annotations

from tools.rynorlang import rir as _rir

MASK64 = (1 << 64) - 1
INT_MIN = -(1 << 63)

STEP_LIMIT = 10_000_000
CALL_LIMIT = 100_000


def _signed(value: int) -> int:
    value &= MASK64
    return value - (1 << 64) if value >= (1 << 63) else value


def _div_trunc(a: int, b: int) -> int:
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


class OracleRefused(Exception):
    """Raised when the oracle is handed invalid RIR (harness bug, not a trap)."""


def run_rir(module: dict, func: str = "main", step_limit: int = STEP_LIMIT,
            call_limit: int = CALL_LIMIT, out: list | None = None) -> dict:
    """Evaluate a verified RIR module's entry function.

    Returns {"exit": int|None, "trapped": str|None, "steps": int} where exit
    is the full signed i64 for int returns (0 for unit), and trapped is one
    of "div0" (zero divisor or INT_MIN/-1), "falloff" (unreachable reached),
    "depth" (call budget), "steps" (step budget). Exactly one of exit/trapped
    is set. Raises OracleRefused on invalid RIR or a missing/bad entry.

    Stage 16: runtime-helper calls (rt_print_*) render into `out` when it is
    a list (int as signed decimal, bool as true/false, str as raw text);
    when None their output is discarded. The return shape never changes.
    """
    problems = _rir.verify_module(module)
    if problems:
        raise OracleRefused("oracle refuses invalid RIR: " + problems[0])
    entry = next((f for f in module["funcs"] if f.get("name") == func), None)
    if entry is None or entry.get("params"):
        raise OracleRefused(f"oracle entry must be fn {func}()")
    if entry.get("ret") not in ("int", None):
        raise OracleRefused(f"oracle entry must return int or unit")
    funcs = {f["name"]: f for f in module["funcs"]}
    strtab = {e["id"]: e["bytes"] for e in module.get("strtab", [])}
    emitted: list = out if out is not None else []
    # frames: [func, block_id, ip, env] plus pending dest slot appended on call.
    frames = [[entry, "bb0", 0, {}]]
    steps = 0
    while frames:
        if len(frames) > call_limit:
            return {"exit": None, "trapped": "depth", "steps": steps}
        cur, blk_id, ip, env = frames[-1][0], frames[-1][1], frames[-1][2], frames[-1][3]
        blk = next(b for b in cur["blocks"] if b["id"] == blk_id)
        if ip < len(blk["instrs"]):
            steps += 1
            if steps > step_limit:
                return {"exit": None, "trapped": "steps", "steps": steps}
            outcome = _exec_instr(blk["instrs"][ip], env, funcs, strtab, emitted)
            frames[-1][2] += 1
            if outcome is None:
                continue
            action, payload = outcome
            if action == "trap":
                return {"exit": None, "trapped": payload, "steps": steps}
            target, argvals, dest = payload
            frames.append([target, "bb0", 0,
                           {f"%{i}": v for i, v in enumerate(argvals)}, dest])
            continue
        term = blk["term"]
        op = term["op"]
        if op == "jmp":
            frames[-1][1] = term["tgt"]
            frames[-1][2] = 0
        elif op == "br":
            cond = env[term["cond"]]
            frames[-1][1] = term["then"] if cond else term["else"]
            frames[-1][2] = 0
        elif op == "ret":
            value = env[term["v"]] if term.get("v") is not None else 0
            child = frames.pop()
            if not frames:
                return {"exit": _signed(value), "trapped": None, "steps": steps}
            dest = child[4] if len(child) > 4 else None
            if dest is not None:
                frames[-1][3][dest] = value
        elif op == "unreachable":
            return {"exit": None, "trapped": "falloff", "steps": steps}
        else:  # pragma: no cover - verifier excludes this
            raise OracleRefused(f"unknown terminator {op!r}")
    return {"exit": 0, "trapped": None, "steps": steps}  # unreachable; defensive


def _exec_instr(instr: dict, env: dict, funcs: dict, strtab: dict, emitted: list | None = None):
    """Execute one instruction; returns None, ("call", ...), or ("trap", kind)."""
    op = instr["op"]
    if op == "const":
        typ = instr["type"]
        value = instr["value"]
        if typ == "int":
            env[instr["dst"]] = int(value, 10) & MASK64
        elif typ == "bool":
            env[instr["dst"]] = 1 if value else 0
        elif typ == "str":
            env[instr["dst"]] = strtab[value]
        return None
    if op == "copy":
        env[instr["dst"]] = env[instr["src"]]
        return None
    if op == "binop":
        return _exec_binop(instr, env)
    if op == "unop":
        operand = env[instr["v"]]
        if instr["operator"] == "-":
            env[instr["dst"]] = (-_signed(operand)) & MASK64
        else:
            env[instr["dst"]] = 1 if not operand else 0
        return None
    if op == "call":
        name = instr["name"]
        if name in _rir.RT_HELPERS:
            # Stage 16 host-runtime helper: render exactly what the native
            # runtime writes (no newline, no truncation). Re-derived here,
            # not imported from the emitter.
            value = env[instr["args"][0]]
            if name == "rt_print_int":
                rendered = str(_signed(value))
            elif name == "rt_print_bool":
                rendered = "true" if value else "false"
            else:
                rendered = strtab[value] if isinstance(value, int) else value
            if emitted is not None:
                emitted.append(rendered)
            return None
        target = funcs.get(name)
        if target is None:  # pragma: no cover - verifier excludes this
            raise OracleRefused(f"call to unknown function {name!r}")
        argvals = [env[a] for a in instr["args"]]
        return ("call", (target, argvals, instr.get("dst")))
    raise OracleRefused(f"unknown opcode {op!r}")  # pragma: no cover


def _exec_binop(instr: dict, env: dict):
    op = instr["operator"]
    left = env[instr["l"]]
    right = env[instr["r"]]
    if op in ("+", "-", "*", "/", "%"):
        a, b = _signed(left), _signed(right)
        if op == "+":
            env[instr["dst"]] = (a + b) & MASK64
        elif op == "-":
            env[instr["dst"]] = (a - b) & MASK64
        elif op == "*":
            env[instr["dst"]] = (a * b) & MASK64
        elif op == "/":
            if b == 0 or (a == INT_MIN and b == -1):
                return ("trap", "div0")
            env[instr["dst"]] = _div_trunc(a, b) & MASK64
        elif op == "%":
            if b == 0 or (a == INT_MIN and b == -1):
                return ("trap", "div0")
            env[instr["dst"]] = (a - _div_trunc(a, b) * b) & MASK64
        return None
    if op in ("==", "!="):
        if isinstance(left, str):
            equal = left == right
        else:
            equal = _signed(left) == _signed(right)
        env[instr["dst"]] = (1 if equal else 0) if op == "==" else (0 if equal else 1)
        return None
    if op in ("<", ">", "<=", ">="):
        a, b = _signed(left), _signed(right)
        result = {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        env[instr["dst"]] = 1 if result else 0
        return None
    if op in ("&&", "||"):
        left_b = 1 if left else 0
        right_b = 1 if right else 0
        env[instr["dst"]] = (left_b and right_b) if op == "&&" else (left_b or right_b)
        return None
    raise OracleRefused(f"unknown binop {op!r}")  # pragma: no cover
