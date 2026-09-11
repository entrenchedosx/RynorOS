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

# Stage 19a err codes (frozen; shared spec with agtypes, re-derived here
# per the honesty rules — the oracle shares the frontend and RIR shape,
# never backend code).
ERR_FULL = 1
ERR_NOTFOUND = 2
ERR_OORANGE = 3
# Stage 19e: arena overflow (NOMEM) and misc file failures (IO),
# re-derived like the rest. The oracle is test-only and unbounded,
# so NOMEM is unreachable here by construction (lengths cap at
# 16384 per call); backends implement the real bound.
ERR_NOMEM = 4
ERR_IO = 5

FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211


def _fnv1a64(data: bytes) -> int:
    digest = FNV_OFFSET
    for byte in data:
        digest ^= byte
        digest = (digest * FNV_PRIME) & MASK64
    return digest


def _key_bytes(kind: str, value: object) -> bytes:
    if kind == "int":
        return (int(value) & MASK64).to_bytes(8, "little")
    if kind == "bool":
        return b"\x01" if value else b"\x00"
    return str(value).encode("ascii")


def _signed(value: int) -> int:
    value &= MASK64
    return value - (1 << 64) if value >= (1 << 63) else value


def _zero_value(shape, rectypes: dict):
    """Zero value for a parsed type shape (err payloads, empty slots)."""
    if shape is None:
        raise OracleRefused("cannot zero an unknown type")
    kind = shape[0]
    if kind == "scalar":
        return 0 if shape[1] in ("int", "bool") else ""
    if kind == "nominal":
        fields = rectypes.get(shape[1])
        if fields is None:
            raise OracleRefused(f"unknown record '{shape[1]}'")
        return {fname: _zero_value(_parse_ctype(ft), rectypes) for fname, ft in fields}
    base = shape[1]
    if base == "list":
        return (0, ())
    if base == "map":
        cap = shape[2][2][1]
        return (0, tuple((0, None, None) for _ in range(cap)))
    if base == "status":
        return (1, 0, _zero_value(shape[2][0], rectypes))
    if base == "result":
        return (1, _zero_value(shape[2][1], rectypes), _zero_value(shape[2][0], rectypes))
    raise OracleRefused(f"cannot zero type {shape!r}")


def _render(value: object, shape, rectypes: dict) -> str:
    """Frozen canonical print format (mirrors the native emitter).

    Maps print in slot order (insertion/hash/probe order), not sorted:
    slot order is a pure function of the insert sequence under the frozen
    hash+probe rule, so differentials agree exactly.
    """
    if shape is None:
        raise OracleRefused("cannot render an unknown type")
    kind = shape[0]
    if kind == "scalar":
        if shape[1] == "int":
            return str(_signed(value))
        if shape[1] == "bool":
            return "true" if value else "false"
        return str(value)
    if kind == "nominal":
        fields = rectypes.get(shape[1])
        if fields is None:
            raise OracleRefused(f"unknown record '{shape[1]}'")
        if not isinstance(value, dict):
            raise OracleRefused("record value must be a dict")
        parts = [f"{fname}: {_render(value[fname], _parse_ctype(ft), rectypes)}"
                 for fname, ft in fields]
        return "{" + ", ".join(parts) + "}"
    base = shape[1]
    if base == "list":
        elem = shape[2][0]
        length, elems = value
        return "[" + ", ".join(_render(v, elem, rectypes) for v in elems[:length]) + "]"
    if base == "map":
        key_s, val_s = shape[2][0], shape[2][1]
        _count, slots = value
        parts = [f"{_render(k, key_s, rectypes)}: {_render(v, val_s, rectypes)}"
                 for tag, k, v in slots if tag]
        return "{" + ", ".join(parts) + "}"
    if base == "status":
        tag, code, payload = value
        if tag == 0:
            return f"ok({_render(payload, shape[2][0], rectypes)})"
        return f"err({code})"
    if base == "result":
        tag, err_v, ok_v = value
        if tag == 0:
            return f"ok({_render(ok_v, shape[2][0], rectypes)})"
        return f"err({_render(err_v, shape[2][1], rectypes)})"
    raise OracleRefused(f"cannot render type {shape!r}")


def _values_equal(left: object, right: object, shape, rectypes: dict) -> bool:
    """Structural content equality (maps logical, like the emitter)."""
    if shape is None:
        raise OracleRefused("cannot compare an unknown type")
    kind = shape[0]
    if kind == "scalar":
        if shape[1] == "str":
            return left == right
        return _signed(left) == _signed(right)
    if kind == "nominal":
        fields = rectypes.get(shape[1])
        if fields is None:
            raise OracleRefused(f"unknown record '{shape[1]}'")
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        return all(fname in left and fname in right
                   and _values_equal(left[fname], right[fname], _parse_ctype(ft), rectypes)
                   for fname, ft in fields)
    base = shape[1]
    if base == "list":
        elem = shape[2][0]
        llen, lelems = left
        rlen, relems = right
        return (llen == rlen and len(lelems) >= llen and len(relems) >= rlen
                and all(_values_equal(lv, rv, elem, rectypes)
                        for lv, rv in zip(lelems[:llen], relems[:rlen])))
    if base == "map":
        key_s, val_s = shape[2][0], shape[2][1]
        lcount, lslots = left
        rcount, rslots = right
        if lcount != rcount:
            return False
        ritems = [(k, v) for tag, k, v in rslots if tag]
        for tag, key, val in lslots:
            if not tag:
                continue
            match = next(((k, v) for k, v in ritems
                          if _values_equal(key, k, key_s, rectypes)
                          and _values_equal(val, v, val_s, rectypes)), None)
            if match is None:
                return False
        return True
    if base == "status":
        ltag, lcode, lpay = left
        rtag, rcode, rpay = right
        return (ltag == rtag and lcode == rcode
                and _values_equal(lpay, rpay, shape[2][0], rectypes))
    if base == "result":
        ltag, lerr, lok = left
        rtag, rerr, rok = right
        return (ltag == rtag
                and _values_equal(lerr, rerr, shape[2][1], rectypes)
                and _values_equal(lok, rok, shape[2][0], rectypes))
    raise OracleRefused(f"cannot compare type {shape!r}")


def _div_trunc(a: int, b: int) -> int:
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


class OracleRefused(Exception):
    """Raised when the oracle is handed invalid RIR (harness bug, not a trap)."""


def _split_top(text: str) -> list:
    """Split a type-argument list at depth-0 commas."""
    parts = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _parse_ctype(text: object):
    """Parse a canonical RIR type string into a shape tree (oracle-local,
    re-derived from the frozen spec; never imported from the backend)."""
    if text in ("int", "bool", "str"):
        return ("scalar", text)
    if not isinstance(text, str) or not text:
        return None
    if "<" not in text:
        return ("nominal", text) if text.replace("_", "").isalnum() and text[0].isalpha() else None
    base, rest = text.split("<", 1)
    if not rest.endswith(">"):
        return None
    inner = rest[:-1]
    args = []
    for part in _split_top(inner):
        if part.isdigit():
            args.append(("cap", int(part)))
        else:
            sub = _parse_ctype(part)
            if sub is None:
                return None
            args.append(sub)
    return ("generic", base, tuple(args))


def run_rir(module: dict, func: str = "main", step_limit: int = STEP_LIMIT,
            call_limit: int = CALL_LIMIT, out: list | None = None) -> dict:
    """Evaluate a verified RIR module's entry function.

    Returns {"exit": int|None, "trapped": str|None, "steps": int} where exit
    is the full signed i64 for int returns (0 for unit), and trapped is one
    of "div0" (zero divisor or INT_MIN/-1), "falloff" (unreachable reached),
    "depth" (call budget), "steps" (step budget, shared with consecutive
    instruction-free edges so empty-block cycles trap instead of hanging).
    Exactly one of exit/trapped is set. Raises OracleRefused on invalid
    RIR or a missing/bad entry. Native execution has no equivalent
    budgets: a natively hanging program hangs (SIGSEGV on stack
    exhaustion, harness timeout otherwise) where the oracle reports a
    trap — the documented differential limit.

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
    rectypes = {e["name"]: [(f["name"], f["type"]) for f in e.get("fields", [])]
                for e in module.get("rectypes", []) if isinstance(e, dict)}
    vtype_maps: dict[str, dict] = {}
    for func in module["funcs"]:
        table: dict[str, str] = {}
        for index, param in enumerate(func.get("params", [])):
            table[f"%{index}"] = param.get("type")
        for block in func.get("blocks", []):
            for ins in block.get("instrs", []):
                dst = ins.get("dst") if isinstance(ins, dict) else None
                if isinstance(dst, str) and dst not in table:
                    table[dst] = ins.get("type")
        vtype_maps[func["name"]] = table
    emitted: list = out if out is not None else []
    # frames: [func, block_id, ip, env] plus pending dest slot appended on call.
    frames = [[entry, "bb0", 0, {}]]
    steps = 0
    # Terminator-only transitions execute no instruction: an empty-block
    # cycle would spin here forever without tripping the step budget, so
    # consecutive instruction-free edges share the same budget. Normal
    # programs never approach it (finite empty chains are short), and
    # reported step counts are unchanged (only instructions increment).
    idle = 0
    while frames:
        if len(frames) > call_limit:
            return {"exit": None, "trapped": "depth", "steps": steps}
        cur, blk_id, ip, env = frames[-1][0], frames[-1][1], frames[-1][2], frames[-1][3]
        blk = next(b for b in cur["blocks"] if b["id"] == blk_id)
        if ip < len(blk["instrs"]):
            idle = 0
            steps += 1
            if steps > step_limit:
                return {"exit": None, "trapped": "steps", "steps": steps}
            outcome = _exec_instr(blk["instrs"][ip], env, funcs, strtab, emitted, rectypes,
                                  vtype_maps.get(cur["name"], {}))
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
        idle += 1
        if idle > step_limit:
            return {"exit": None, "trapped": "steps", "steps": steps}
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


def _exec_instr(instr: dict, env: dict, funcs: dict, strtab: dict, emitted: list | None = None, rectypes: dict | None = None, vtypes: dict | None = None):
    """Execute one instruction; returns None, ("call", ...), or ("trap", kind)."""
    op = instr["op"]
    rectypes = rectypes or {}
    vtypes = vtypes or {}
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
        return _exec_binop(instr, env, rectypes, vtypes)
    if op == "unop":
        operand = env[instr["v"]]
        if instr["operator"] == "-":
            env[instr["dst"]] = (-_signed(operand)) & MASK64
        elif instr["operator"] == "~":
            env[instr["dst"]] = (~operand) & MASK64
        else:
            env[instr["dst"]] = 1 if not operand else 0
        return None
    if op in ("make_record", "get_field", "make_list", "list_len", "list_idx", "list_push",
              "make_map", "map_get", "map_insert", "map_len",
              "str_len", "str_byte_at", "str_fread", "str_fjoin", "status_is_ok", "status_is_err",
              "status_unwrap_or", "result_ok", "result_err", "unwrap_ok", "unwrap_err"):
        return _exec_agg(instr, env, rectypes, emitted, vtypes)
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
    if op == "print_agg":
        shape = _parse_ctype(vtypes.get(instr["agg"]))
        if emitted is not None:
            emitted.append(_render(env[instr["agg"]], shape, rectypes))
        return None
    raise OracleRefused(f"unknown opcode {op!r}")  # pragma: no cover


def _rectype_fields(rectypes: dict, name: str):
    fields = rectypes.get(name)
    if fields is None:
        raise OracleRefused(f"unknown record '{name}'")
    return fields


def _key_kind(key_shape) -> str:
    if key_shape is not None and key_shape[0] == "scalar" and key_shape[1] in ("int", "bool", "str"):
        return key_shape[1]
    raise OracleRefused(f"bad map key type {key_shape!r}")


def _map_probe(slots: list, key: object, kind: str, rectypes: dict):
    """Linear probe over oracle map slots; returns slot index or None."""
    cap = len(slots)
    if cap == 0:
        return None
    start = _fnv1a64(_key_bytes(kind, key)) % cap
    for step in range(cap):
        index = (start + step) % cap
        tag, slot_key, _slot_val = slots[index]
        if not tag:
            return None
        if _values_equal(key, slot_key, ("scalar", kind), rectypes):
            return index
    return None


def _map_locate(slots: list, key: object, kind: str, rectypes: dict):
    """Probe for update-or-insert; ("hit"|"empty", index) or ("full", -1)."""
    cap = len(slots)
    start = _fnv1a64(_key_bytes(kind, key)) % cap
    first_empty = None
    for step in range(cap):
        index = (start + step) % cap
        tag, slot_key, _slot_val = slots[index]
        if not tag:
            if first_empty is None:
                first_empty = index
            continue
        if _values_equal(key, slot_key, ("scalar", kind), rectypes):
            return ("hit", index)
    if first_empty is not None:
        return ("empty", first_empty)
    return ("full", -1)


def _exec_agg(instr: dict, env: dict, rectypes: dict, emitted: list | None = None, vtypes: dict | None = None):
    """Execute one Stage 19a aggregate instruction (frozen semantics).

    Records are name->value dicts (field order comes from rectypes, so
    get_field needs no type threading); lists are (length, elems-tuple);
    maps are (count, slots-tuple) under the frozen hash+probe rule;
    statuses are (tag, code, payload) with zeroed err payloads.
    Operand types resolve through vtypes (verified RIR guarantees them).
    """
    op = instr["op"]
    vtypes = vtypes or {}

    def optype(temp: str) -> str:
        typ = vtypes.get(temp)
        if not isinstance(typ, str):
            raise OracleRefused(f"unknown vreg {temp!r}")
        return typ
    if op == "make_record":
        typ = instr["type"]
        fields = _rectype_fields(rectypes, typ)
        args = instr["args"]
        if len(args) != len(fields):
            raise OracleRefused("make_record arity mismatch")
        env[instr["dst"]] = {fname: env[a] for a, (fname, _ft) in zip(args, fields)}
        return None
    if op == "get_field":
        record = env[instr["rec"]]
        field = instr["field"]
        if not isinstance(record, dict) or field not in record:
            raise OracleRefused("get_field of missing field")
        env[instr["dst"]] = record[field]
        return None
    if op == "make_list":
        env[instr["dst"]] = (len(instr["args"]), tuple(env[a] for a in instr["args"]))
        return None
    if op == "list_len":
        env[instr["dst"]] = env[instr["seq"]][0]
        return None
    if op == "list_idx":
        length, elems = env[instr["seq"]]
        index = _signed(env[instr["index"]])
        if index < 0 or index >= length:
            elem_t = _inner_shape(optype(instr["seq"]), "list")[2][0]
            env[instr["dst"]] = (1, ERR_OORANGE, _zero_value(elem_t, rectypes))
        else:
            env[instr["dst"]] = (0, 0, elems[index])
        return None
    if op == "list_push":
        length, elems = env[instr["seq"]]
        cap = _list_cap(optype(instr["seq"]))
        if length >= cap:
            env[instr["dst"]] = (1, ERR_FULL, (0, ()))
        else:
            env[instr["dst"]] = (0, 0, (length + 1, elems + (env[instr["val"]],)))
        return None
    if op == "make_map":
        args = instr["args"]
        cap = _map_cap(instr["type"])
        kind = _map_key_kind(instr["type"])
        slots = [[0, None, None] for _ in range(cap)]
        count = 0
        for pos in range(0, len(args), 2):
            key, val = env[args[pos]], env[args[pos + 1]]
            where, index = _map_locate(slots, key, kind, rectypes)
            if where == "hit":
                slots[index][2] = val
            elif where == "empty":
                slots[index] = [1, key, val]
                count += 1
        env[instr["dst"]] = (count, tuple((t, k, v) for t, k, v in slots))
        return None
    if op == "map_get":
        _count, slots = env[instr["map"]]
        kind = _map_key_kind(optype(instr["map"]))
        index = _map_probe(list(slots), env[instr["key"]], kind, rectypes)
        if index is None:
            env[instr["dst"]] = (1, ERR_NOTFOUND, _zero_status_payload(instr["type"], rectypes))
        else:
            env[instr["dst"]] = (0, 0, slots[index][2])
        return None
    if op == "map_insert":
        count, slots = env[instr["map"]]
        map_t = optype(instr["map"])
        cap = _map_cap(map_t)
        kind = _map_key_kind(map_t)
        mutable = [list(s) for s in slots]
        where, index = _map_locate(mutable, env[instr["key"]], kind, rectypes)
        if where == "hit":
            mutable[index][2] = env[instr["val"]]
            env[instr["dst"]] = (0, 0, (count, tuple((t, k, v) for t, k, v in mutable)))
        elif where == "empty":
            mutable[index] = [1, env[instr["key"]], env[instr["val"]]]
            env[instr["dst"]] = (0, 0, (count + 1, tuple((t, k, v) for t, k, v in mutable)))
        else:
            env[instr["dst"]] = (1, ERR_FULL, (0, tuple((0, None, None) for _ in range(cap))))
        return None
    if op == "map_len":
        env[instr["dst"]] = env[instr["map"]][0]
        return None
    if op == "str_len":
        env[instr["dst"]] = len(env[instr["v"]])
        return None
    if op == "str_byte_at":
        text = env[instr["v"]]
        index = _signed(env[instr["index"]])
        if index < 0 or index >= len(text):
            env[instr["dst"]] = (1, ERR_OORANGE, 0)
        else:
            env[instr["dst"]] = (0, 0, ord(text[index]))
        return None
    if op == "str_fread":
        # Test-only host-filesystem read (mirrors the native helper's
        # contract, not its implementation): exact bytes, surrogate
        # escapes round-trip non-ASCII deterministically.
        import os as _os
        raw_path = env[instr["path"]].encode("ascii", "surrogateescape")
        offset = _signed(env[instr["offset"]])
        length = _signed(env[instr["length"]])
        if offset < 0 or length < 0 or length > 16384:
            env[instr["dst"]] = (1, ERR_OORANGE, "")
            return None
        try:
            with open(raw_path, "rb") as handle:
                handle.seek(0, _os.SEEK_END)
                size = handle.tell()
                if offset > size:
                    env[instr["dst"]] = (1, ERR_OORANGE, "")
                    return None
                handle.seek(offset)
                data = handle.read(length)
        except FileNotFoundError:
            env[instr["dst"]] = (1, ERR_NOTFOUND, "")
            return None
        except OSError:
            env[instr["dst"]] = (1, ERR_IO, "")
            return None
        env[instr["dst"]] = (0, 0, data.decode("ascii", "surrogateescape"))
        return None
    if op == "str_fjoin":
        directory = env[instr["directory"]]
        rel = env[instr["rel"]]
        if not rel or rel.startswith("/"):
            env[instr["dst"]] = (1, ERR_OORANGE, "")
            return None
        env[instr["dst"]] = (0, 0, rel if not directory else directory + "/" + rel)
        return None
    if op == "status_is_ok":
        env[instr["dst"]] = 1 if env[instr["v"]][0] == 0 else 0
        return None
    if op == "status_is_err":
        env[instr["dst"]] = 1 if env[instr["v"]][0] != 0 else 0
        return None
    if op == "status_unwrap_or":
        tag, _code, payload = env[instr["v"]]
        env[instr["dst"]] = payload if tag == 0 else env[instr["default"]]
        return None
    if op == "result_ok" or op == "result_err":
        # Layout mirrors status structurally: (tag, E, T) with the
        # unused variant zeroed (deterministic equality).
        if op == "result_ok":
            env[instr["dst"]] = (0, _zero_result_err(instr["type"], rectypes), env[instr["val"]])
        else:
            env[instr["dst"]] = (1, env[instr["val"]], _zero_result_ok(instr["type"], rectypes))
        return None
    if op == "unwrap_ok" or op == "unwrap_err":
        # Path-validated extraction (builder emits on the taken arm only).
        tag, err_v, ok_v = env[instr["v"]]
        env[instr["dst"]] = ok_v if op == "unwrap_ok" else err_v
        return None
    if op == "result_ok" or op == "result_err":
        # Layout mirrors status structurally: (tag, E, T) with the
        # unused variant zeroed (deterministic equality).
        if op == "result_ok":
            env[instr["dst"]] = (0, _zero_result_err(instr["type"], rectypes), env[instr["val"]])
        else:
            env[instr["dst"]] = (1, env[instr["val"]], _zero_result_ok(instr["type"], rectypes))
        return None
    if op == "unwrap_ok" or op == "unwrap_err":
        # Path-validated extraction (builder emits on the taken arm only).
        tag, err_v, ok_v = env[instr["v"]]
        env[instr["dst"]] = ok_v if op == "unwrap_ok" else err_v
        return None
    if op == "print_agg":
        raise OracleRefused("print_agg renders in run_rir (see _render)")
    raise OracleRefused(f"unknown opcode {op!r}")  # pragma: no cover


def _inner_shape(typ: str, base: str):
    """Unwrap status<T> to T's shape for capacity queries (oracle-local)."""
    node = _parse_ctype(typ)
    if node is not None and node[0] == "generic" and node[1] == "status":
        node = node[2][0]
    if node is None or node[0] != "generic" or node[1] != base:
        raise OracleRefused(f"expected {base} type, got {typ!r}")
    return node


def _zero_result_err(typ: str, rectypes: dict):
    node = _parse_ctype(typ)
    return _zero_value(node[2][1], rectypes)


def _zero_result_ok(typ: str, rectypes: dict):
    node = _parse_ctype(typ)
    return _zero_value(node[2][0], rectypes)


def _list_cap(typ: str) -> int:
    return _inner_shape(typ, "list")[2][1][1]


def _map_cap(typ: str) -> int:
    return _inner_shape(typ, "map")[2][2][1]


def _map_key_kind(typ: str) -> str:
    return _key_kind(_inner_shape(typ, "map")[2][0])


def _zero_status_payload(typ: str, rectypes: dict):
    node = _parse_ctype(typ)
    return _zero_value(node[2][0], rectypes)


def _exec_binop(instr: dict, env: dict, rectypes: dict, vtypes: dict | None = None):
    op = instr["operator"]
    vtypes = vtypes or {}
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
    if op in ("&", "|", "^", "<<", ">>"):
        # Stage 19a bitops: two's-complement wrap; shifts mask the count.
        a, b = _signed(left) & MASK64, _signed(right) & MASK64
        if op == "&":
            env[instr["dst"]] = a & b
        elif op == "|":
            env[instr["dst"]] = a | b
        elif op == "^":
            env[instr["dst"]] = a ^ b
        elif op == "<<":
            env[instr["dst"]] = (a << (b & 63)) & MASK64
        else:
            env[instr["dst"]] = (_signed(a) >> (b & 63)) & MASK64
        return None
    if op in ("==", "!="):
        # Stage 19a: structural content equality over value types (the
        # verifier guarantees both sides share the left operand's type).
        shape = _parse_ctype(vtypes.get(instr["l"]))
        equal = _values_equal(left, right, shape, rectypes)
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
