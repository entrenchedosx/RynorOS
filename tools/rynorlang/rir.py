#!/usr/bin/env python3
"""RynorLang IR (RIR) v1: typed CFG over the frozen Stage-14 stable AST.

Host-side, Python 3.10+ standard library only. This module builds RIR from an
analyzed stable-AST dict (see docs/design/rynorlang-ast.md: the 16 frozen node
kinds with direct fields, e.g. Let{name,type,init,symbol}, Call{callee,args}),
verifies structural and type invariants, and serializes a canonical,
deterministic, human-readable form used for golden tests. No code generation
(see compile.py), no execution (see interp.py, test oracle only).

RIR is intentionally small: static types {int,bool,str} plus erased unit,
direct calls only, no phi nodes (sound: the language has no assignment and no
shadowing, so every name resolves to one dominating definition), no GC, no
exceptions, no dynamic dispatch.

Lowering uses an explicit work stack for expressions, so arbitrarily deep
left-associated operator chains (which the analyzer accepts) never touch the
Python recursion limit. Control-structure nesting is bounded by the analyzer's
own depth limit and lowered with plain recursion.
"""

from __future__ import annotations


RIR_VERSION = 1
MAX_FRAMESLOTS = 1024
MAX_STR_LEN = 4096

VALUE_TYPES = ("int", "bool", "str")
ALL_TYPES = ("int", "bool", "str", "unit")

# Reserved for future dynamic-free shell values (Stage 19a). The v1 verifier
# hard-rejects this type; the static fast path never boxes or checks tags.
RESERVED_TYPES = ("value",)

# Opcodes the v1 verifier rejects outright (reserved for later editions).
RESERVED_OPS = (
    "make_record", "get_field", "set_field",
    "make_list", "list_idx", "list_len", "list_push",
    "make_status", "match_br",
    "spawn_pipe", "exec_cmd", "open_handle",
)
# AST kinds beyond the frozen 16 that must be rejected, never miscompiled.
RESERVED_AST_KINDS = (
    "Pipeline", "Cmd", "Member", "Record", "List", "Match",
    "Break", "Continue", "Use", "Module",
)
# The 16 frozen stable-AST kinds (docs/design/rynorlang-ast.md).
STABLE_KINDS = (
    "Program", "Function", "Param", "Block", "Let", "If", "While",
    "Return", "ExprStmt", "BinOp", "UnOp", "IntLit", "BoolLit",
    "StrLit", "Var", "Call",
)

# Diagnostic codes emitted by build_rir. Verifier failures are plain
# human-readable strings (see verify_module); only the builder mints codes.
# Stage 16 host-runtime helpers: name -> ([param types], ret or None).
# These are the only RIR-level callees that need no RIR function definition;
# the program linker resolves them against the host runtime object
# (tools/rynorlang/runtime/rt_linux.asm). The rt_ prefix is reserved: user
# functions may not claim it (builder and verifier both refuse).
RT_HELPERS = {
    "rt_print_int": (["int"], None),
    "rt_print_bool": (["bool"], None),
    "rt_print_str": (["str"], None),
}
RT_PREFIX = "rt_"
_RT_PRINT_BY_TYPE = {"int": "rt_print_int", "bool": "rt_print_bool", "str": "rt_print_str"}
COMP_V2_UNSUPPORTED = "COMP_V2_UNSUPPORTED"
COMP_STR_TOO_LONG = "COMP_STR_TOO_LONG"
COMP_BAD_AST = "COMP_BAD_AST"
COMP_FRAME_TOO_BIG = "COMP_FRAME_TOO_BIG"
COMP_RIR_VERSION = "COMP_RIR_VERSION"
COMP_DEPTH_EXCEEDED = "COMP_DEPTH_EXCEEDED"

_I64_MAX = 2 ** 63 - 1


def _identifier(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and value.isascii()
            and (value[0].isalpha() or value[0] == "_")
            and all(c.isalnum() or c == "_" for c in value[1:]))


def _vreg(value: object) -> bool:
    return (isinstance(value, str) and len(value) > 1 and value[0] == "%"
            and value[1:].isascii() and value[1:].isdigit())


# ---------------------------------------------------------------------------
# Builder: stable AST dict -> RIR dict.
# ---------------------------------------------------------------------------

class _RirError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> None:
    raise _RirError(code, message)


def build_rir(ast: dict, source_name: str = "<input>"):
    """Lower an analyzed stable-AST dict to an RIR module dict.

    Returns (module, None) on success or (None, {"code":..., "message":...})
    on failure. Never raises on malformed input; never accepts a program the
    analyzer rejected (callers must only feed analyzer-accepted trees, and the
    builder re-checks every type rule independently).
    """
    try:
        return _build(ast, source_name), None
    except _RirError as error:
        return None, {"code": error.code, "message": error.message}
    except RecursionError:
        # Control-structure nesting that slips past every counter still
        # fails closed instead of escaping as an interpreter traceback.
        return None, {"code": COMP_DEPTH_EXCEEDED,
                      "message": "lowering exceeds the nesting budget"}


def _build(ast: dict, source_name: str) -> dict:
    if not isinstance(ast, dict) or ast.get("kind") != "Program":
        _fail(COMP_BAD_AST, "RIR input must be a Program AST dict")
    functions = ast.get("functions")
    if not isinstance(functions, list):
        _fail(COMP_BAD_AST, "Program.functions must be a list")
    # First pass: collect callee signatures so calls validate independently.
    seen: set[str] = set()
    sigs: dict[str, tuple[list, object]] = {}
    for fn in functions:
        if not isinstance(fn, dict) or fn.get("kind") != "Function":
            _fail(COMP_V2_UNSUPPORTED, "Program members must be Function nodes")
        name = fn.get("name")
        if not _identifier(name):
            _fail(COMP_BAD_AST, "Function name must be an ASCII identifier")
        if name.startswith(RT_PREFIX):
            _fail(COMP_BAD_AST, f"function '{name}' uses the reserved '{RT_PREFIX}' runtime namespace")
        if name in seen:
            _fail(COMP_BAD_AST, f"duplicate function '{name}'")
        seen.add(name)
        params = fn.get("params")
        if not isinstance(params, list):
            _fail(COMP_BAD_AST, f"function '{name}' params must be a list")
        ptypes = []
        for param in params:
            if not isinstance(param, dict) or param.get("kind") != "Param":
                _fail(COMP_BAD_AST, f"function '{name}' params must be Param nodes")
            ptype = param.get("type")
            if not _identifier(param.get("name")):
                _fail(COMP_BAD_AST, "Param name must be an ASCII identifier")
            if ptype not in VALUE_TYPES:
                if ptype in RESERVED_TYPES:
                    _fail(COMP_V2_UNSUPPORTED,
                          f"param '{param.get('name')}' uses reserved type '{ptype}'")
                _fail(COMP_BAD_AST, f"param '{param.get('name')}' has invalid type {ptype!r}")
            ptypes.append(ptype)
        param_slots = sum(2 if ptype == "str" else 1 for ptype in ptypes)
        if param_slots > MAX_FRAMESLOTS:
            _fail(COMP_FRAME_TOO_BIG,
                  f"function '{name}' parameters need {param_slots} slots "
                  f"(max {MAX_FRAMESLOTS})")
        ret = fn.get("ret_type")
        if ret is not None and ret not in VALUE_TYPES:
            if ret in RESERVED_TYPES:
                _fail(COMP_V2_UNSUPPORTED, f"function '{name}' uses reserved type '{ret}'")
            _fail(COMP_BAD_AST, f"function '{name}' has invalid return type {ret!r}")
        sigs[name] = (ptypes, ret)
    for helper, hsig in RT_HELPERS.items():
        sigs[helper] = (list(hsig[0]), hsig[1])
    strtab: list[dict] = []
    str_ids: dict[str, int] = {}
    rir_funcs = [_lower_function(fn, sigs, strtab, str_ids) for fn in functions]
    return {"rir_version": RIR_VERSION, "source": source_name,
            "strtab": strtab, "funcs": rir_funcs}


def _intern_str(value: object, strtab: list, str_ids: dict) -> int:
    if not isinstance(value, str):
        _fail(COMP_BAD_AST, "StrLit value must be a string")
    for char in value:
        if ord(char) > 0x7F:
            _fail(COMP_BAD_AST, "StrLit value must be ASCII")
    if len(value) > MAX_STR_LEN:
        _fail(COMP_STR_TOO_LONG,
              f"string literal of {len(value)} bytes exceeds {MAX_STR_LEN}")
    if value not in str_ids:
        str_ids[value] = len(strtab)
        strtab.append({"id": len(strtab), "len": len(value), "bytes": value})
    return str_ids[value]


class _FunctionLowering:
    """Per-function lowering state: vreg/type allocation and block buffer.

    Home-slot assignment is deliberately NOT done here: slots are assigned
    after lowering by assign_slots(), which reuses dead vregs' slots
    (liveness-based, deterministic). Bump allocation would need one slot per
    temporary, so a legal 5000-term chain alone would exceed the frame bound.

    Scopes are tracked explicitly: `scopes` is a stack of declared-symbol
    lists, one per open lexical block. Params live in the base scope, which
    is never popped during the function. A use that cannot see its symbol in
    any open scope fails closed instead of resolving through a stale binding
    left behind by an exited block.
    """

    def __init__(self, strtab: list, str_ids: dict, sigs: dict):
        self.strtab = strtab
        self.str_ids = str_ids
        self.sigs = sigs
        self.vreg_types: dict[str, str] = {}
        self.next_vreg = 0
        self.blocks: list[dict] = []
        self.symbols: dict[int, str] = {}
        self.scopes: list[list] = []

    def new_vreg(self, typ: str) -> str:
        name = f"%{self.next_vreg}"
        self.next_vreg += 1
        self.vreg_types[name] = typ
        return name

    def new_block(self) -> int:
        index = len(self.blocks)
        self.blocks.append({"id": f"bb{index}", "instrs": [], "term": None})
        return index

    def emit(self, block: int, instr: dict) -> None:
        self.blocks[block]["instrs"].append(instr)

    def terminate(self, block: int, term: dict) -> None:
        if self.blocks[block]["term"] is not None:
            _fail(COMP_BAD_AST, "internal error: block terminated twice")
        self.blocks[block]["term"] = term

    def is_open(self, block: int) -> bool:
        return self.blocks[block]["term"] is None


def _uses_of_instr(instr: dict) -> list:
    """Operand vregs read by one instruction, in evaluation order."""
    op = instr.get("op")
    if op == "copy":
        return [instr["src"]] if isinstance(instr.get("src"), str) else []
    if op == "binop":
        return [v for v in (instr.get("l"), instr.get("r")) if isinstance(v, str)]
    if op == "unop":
        return [instr["v"]] if isinstance(instr.get("v"), str) else []
    if op == "call":
        args = instr.get("args")
        return [a for a in args] if isinstance(args, list) else []
    return []


def _uses_of_term(term: dict) -> list:
    op = term.get("op") if isinstance(term, dict) else None
    if op == "br":
        return [term["cond"]] if isinstance(term.get("cond"), str) else []
    if op == "ret":
        return [term["v"]] if isinstance(term.get("v"), str) else []
    return []


def assign_slots(blocks: list, vreg_types: dict, nparams: int) -> tuple:
    """Greedy CFG-liveness home-slot assignment (deterministic).

    Returns (slot_of, frameslots). A backwards fixed point computes live-in
    and live-out sets over the actual CFG, then a deterministic interference
    coloring reuses only non-overlapping homes. This remains sound for valid
    hand-built RIR whose edges do not follow layout order. `str` occupies two
    consecutive slots. Shared by builder, verifier, and emitter.
    """
    def order_key(vreg: str) -> int:
        try:
            return int(vreg[1:])
        except (ValueError, IndexError, TypeError, AttributeError):
            return 1 << 60

    count = len(blocks)
    ids = [b.get("id") if isinstance(b, dict) else None for b in blocks]
    index_of = {bid: i for i, bid in enumerate(ids) if isinstance(bid, str)}
    succ: list[set[int]] = [set() for _ in blocks]
    block_use: list[set[str]] = [set() for _ in blocks]
    block_def: list[set[str]] = [set() for _ in blocks]
    for bi, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        defined: set[str] = set()
        for instr in block.get("instrs", []):
            if not isinstance(instr, dict):
                continue
            for operand in _uses_of_instr(instr):
                if isinstance(operand, str) and operand not in defined:
                    block_use[bi].add(operand)
            dst = instr.get("dst")
            if isinstance(dst, str):
                defined.add(dst)
                block_def[bi].add(dst)
        term = block.get("term")
        if isinstance(term, dict):
            for operand in _uses_of_term(term):
                if isinstance(operand, str) and operand not in defined:
                    block_use[bi].add(operand)
            for key in ("tgt", "then", "else"):
                target = term.get(key)
                if isinstance(target, str) and target in index_of:
                    succ[bi].add(index_of[target])

    live_in: list[set[str]] = [set() for _ in blocks]
    live_out: list[set[str]] = [set() for _ in blocks]
    changed = True
    while changed:
        changed = False
        for bi in range(count - 1, -1, -1):
            out = set().union(*(live_in[s] for s in succ[bi])) if succ[bi] else set()
            incoming = block_use[bi] | (out - block_def[bi])
            if out != live_out[bi] or incoming != live_in[bi]:
                live_out[bi] = out
                live_in[bi] = incoming
                changed = True

    interference: dict[str, set[str]] = {v: set() for v in vreg_types}

    def add_clique(values) -> None:
        ordered = sorted((v for v in values if v in interference), key=order_key)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                interference[left].add(right)
                interference[right].add(left)

    # Preserve the former layout-interval conservatism as an additional
    # interference source. It avoids fragile same-instruction coalescing and
    # keeps established frame sizes, while CFG liveness below adds the edges
    # that layout order alone cannot see.
    last_position: dict[str, tuple[int, int]] = {}
    layout_defs: list[tuple[str, tuple[int, int]]] = [
        (f"%{i}", (-1, -1)) for i in range(nparams)]
    for bi, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        instrs = block.get("instrs", [])
        for pos, instr in enumerate(instrs):
            if not isinstance(instr, dict):
                continue
            for operand in _uses_of_instr(instr):
                if isinstance(operand, str):
                    last_position[operand] = (bi, pos)
            dst = instr.get("dst")
            if isinstance(dst, str):
                layout_defs.append((dst, (bi, pos)))
                last_position[dst] = (bi, pos)
        for operand in _uses_of_term(block.get("term", {})):
            if isinstance(operand, str):
                last_position[operand] = (bi, len(instrs))
    active: set[str] = set()
    for vreg, at in sorted(layout_defs,
                           key=lambda item: (item[1], order_key(item[0]))):
        active = {other for other in active
                  if last_position.get(other, (-1, -1)) >= at}
        for other in active:
            if vreg != other and vreg in interference and other in interference:
                interference[vreg].add(other)
                interference[other].add(vreg)
        active.add(vreg)

    add_clique(f"%{i}" for i in range(nparams))
    for bi, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        live = set(live_out[bi])
        live.update(v for v in _uses_of_term(block.get("term", {}))
                    if isinstance(v, str))
        for instr in reversed(block.get("instrs", [])):
            if not isinstance(instr, dict):
                continue
            dst = instr.get("dst")
            if isinstance(dst, str) and dst in interference:
                for other in live:
                    if other != dst and other in interference:
                        interference[dst].add(other)
                        interference[other].add(dst)
                live.discard(dst)
            live.update(v for v in _uses_of_instr(instr) if isinstance(v, str))
    defs: list[tuple] = []
    for index in range(nparams):
        defs.append((f"%{index}", (-1, -1)))
    for bi, block in enumerate(blocks):
        instrs = block.get("instrs", []) if isinstance(block, dict) else []
        for pos, instr in enumerate(instrs):
            if isinstance(instr, dict) and isinstance(instr.get("dst"), str):
                defs.append((instr["dst"], (bi, pos)))
    slot_of: dict[str, int] = {}
    top = 0
    for vreg, at in sorted(defs, key=lambda item: (item[1], order_key(item[0]))):
        if vreg in slot_of:
            continue
        need = 2 if vreg_types.get(vreg) == "str" else 1
        used = set()
        for other in interference.get(vreg, set()):
            if other not in slot_of:
                continue
            width = 2 if vreg_types.get(other) == "str" else 1
            used.update(range(slot_of[other], slot_of[other] + width))
        start = 0
        while any(s in used for s in range(start, start + need)):
            start += 1
        slot_of[vreg] = start
        top = max(top, start + need)
    return slot_of, top


def _lower_function(fn: dict, sigs: dict, strtab: list, str_ids: dict) -> dict:
    if not isinstance(fn, dict):
        _fail(COMP_BAD_AST, "function must be a dict")
    name = fn.get("name")
    params = fn.get("params")
    ret = fn.get("ret_type", None)
    if not isinstance(name, str) or not name:
        _fail(COMP_BAD_AST, "Function name must be a non-empty string")
    if not isinstance(params, list):
        _fail(COMP_BAD_AST, f"function '{name}' params must be a list")
    body = fn.get("body")
    if not isinstance(body, dict) or body.get("kind") != "Block":
        _fail(COMP_BAD_AST, f"function '{name}' body must be a Block")
    low = _FunctionLowering(strtab, str_ids, sigs)
    rir_params = []
    # Incoming values occupy %0..%k-1 in order; named copies follow, so the
    # verifier's params-predefined rule and the emitter's ABI mapping agree.
    pregs = []
    for param in params:
        if not isinstance(param, dict):
            _fail(COMP_BAD_AST, f"function '{name}' params must be Param nodes")
        ptype = param.get("type")
        if ptype not in VALUE_TYPES:
            if ptype in RESERVED_TYPES:
                _fail(COMP_V2_UNSUPPORTED,
                      f"param '{param.get('name')}' uses reserved type '{ptype}'")
            _fail(COMP_BAD_AST, f"param '{param.get('name')}' has invalid type {ptype!r}")
        pregs.append(low.new_vreg(ptype))
    base_scope = []
    for index, param in enumerate(params):
        pname = param.get("name")
        ptype = param.get("type")
        psym = param.get("symbol")
        if type(psym) is not int:
            _fail(COMP_BAD_AST, f"param '{pname}' symbol must be an int")
        if psym in low.symbols:
            _fail(COMP_BAD_AST, f"param '{pname}' redefines an existing symbol")
        if pregs[index] != f"%{index}":
            _fail(COMP_BAD_AST, "internal error: param vreg numbering drifted")
        vreg = low.new_vreg(ptype)
        low.symbols[psym] = vreg
        base_scope.append(psym)
        rir_params.append({"name": pname, "symbol": psym, "type": ptype})
    low.scopes.append(base_scope)
    entry = low.new_block()
    for index, param in enumerate(params):
        low.emit(entry, {"op": "copy", "dst": low.symbols[param["symbol"]],
                         "type": param["type"], "src": f"%{index}"})
    _lower_block_contents(low, body.get("stmts"), entry, name, ret)
    # Function epilogue: an open trailing block ends by rule -- bare `ret`
    # for unit functions, `unreachable` (fall-off trap) otherwise.
    for index in range(len(low.blocks)):
        if low.is_open(index):
            if ret is None:
                low.terminate(index, {"op": "ret"})
            else:
                low.terminate(index, {"op": "unreachable"})
    slot_of, frameslots = assign_slots(low.blocks, low.vreg_types, len(params))
    if frameslots > MAX_FRAMESLOTS:
        _fail(COMP_FRAME_TOO_BIG,
              f"function '{name}' needs {frameslots} slots (max {MAX_FRAMESLOTS})")
    low.slot_of = slot_of
    blocks = [{"id": b["id"], "instrs": b["instrs"], "term": b["term"]} for b in low.blocks]
    return {"name": name, "symbol": fn.get("symbol"), "params": rir_params,
            "ret": ret, "blocks": blocks, "frameslots": frameslots}


def _lower_block_contents(low: _FunctionLowering, stmts: object, cur: int,
                          fname: str, ret: object) -> int:
    """Lower a statement list into blocks starting at cur; return tail block.

    Each block pushes one lexical scope; declarations die with it, so a use
    after the block ends fails closed instead of resolving a stale binding.
    """
    if not isinstance(stmts, list):
        _fail(COMP_BAD_AST, f"function '{fname}' block stmts must be a list")
    low.scopes.append([])
    try:
        for stmt in stmts:
            if not low.is_open(cur):
                cur = low.new_block()
            cur = _lower_stmt(low, stmt, cur, fname, ret)
        return cur
    finally:
        for sym in low.scopes.pop():
            low.symbols.pop(sym, None)


def _lower_stmt(low: _FunctionLowering, stmt: object, cur: int,
                fname: str, ret: object) -> int:
    if not isinstance(stmt, dict):
        _fail(COMP_BAD_AST, f"function '{fname}' statement must be a dict")
    kind = stmt.get("kind")
    if kind == "Let":
        temp = _lower_expr(low, stmt.get("init"), cur, fname)
        sym = stmt.get("symbol")
        want = stmt.get("type")
        if not isinstance(sym, int):
            _fail(COMP_BAD_AST, "Let symbol must be an int")
        if sym in low.symbols:
            _fail(COMP_BAD_AST, "Let redefines an existing symbol")
        if low.vreg_types[temp] != want:
            _fail(COMP_BAD_AST,
                    f"Let initializer type {low.vreg_types[temp]} != declared {want!r}")
        low.symbols[sym] = temp
        low.scopes[-1].append(sym)
        return cur
    if kind == "Return":
        value = stmt.get("value")
        if value is None:
            if ret is not None:
                _fail(COMP_BAD_AST, "bare return in non-unit function")
            low.terminate(cur, {"op": "ret"})
        else:
            temp = _lower_expr(low, value, cur, fname)
            if low.vreg_types[temp] != ret:
                _fail(COMP_BAD_AST,
                        f"return type {low.vreg_types[temp]} != signature {ret!r}")
            low.terminate(cur, {"op": "ret", "v": temp})
        return cur
    if kind == "If":
        return _lower_if(low, stmt, cur, fname, ret)
    if kind == "While":
        header_b = low.new_block()
        body_b = low.new_block()
        exit_b = low.new_block()
        # The condition lives only in the header so it recomputes per
        # iteration; evaluating it in the pre-header too would emit a dead
        # computation (and a spurious call, once calls can have effects).
        low.terminate(cur, {"op": "jmp", "tgt": f"bb{header_b}"})
        hcond = _lower_expr(low, stmt.get("cond"), header_b, fname)
        if low.vreg_types[hcond] != "bool":
            _fail(COMP_BAD_AST, "while condition must be bool")
        low.terminate(header_b, {"op": "br", "cond": hcond,
                                 "then": f"bb{body_b}", "else": f"bb{exit_b}"})
        body_tail = _lower_block_contents(
            low, _need_block_stmts(low, stmt.get("body"), fname),
            body_b, fname, ret)
        if low.is_open(body_tail):
            low.terminate(body_tail, {"op": "jmp", "tgt": f"bb{header_b}"})
        return exit_b
    if kind == "ExprStmt":
        _lower_expr(low, stmt.get("expr"), cur, fname)
        return cur
    if kind == "Block":
        return _lower_block_contents(low, stmt.get("stmts"), cur, fname, ret)
    if kind in RESERVED_AST_KINDS:
        _fail(COMP_V2_UNSUPPORTED, f"statement kind '{kind}' is reserved for a later edition")
    _fail(COMP_V2_UNSUPPORTED, f"unknown statement kind {kind!r}")
    raise AssertionError("unreachable")  # _fail always raises


def _need_block_stmts(low: _FunctionLowering, node: object, fname: str) -> object:
    if not isinstance(node, dict) or node.get("kind") != "Block":
        _fail(COMP_BAD_AST, f"function '{fname}' branch must be a Block")
    return node.get("stmts")


def _lower_if(low: _FunctionLowering, stmt: dict, cur: int, fname: str, ret: object) -> int:
    cond = _lower_expr(low, stmt.get("cond"), cur, fname)
    if low.vreg_types[cond] != "bool":
        _fail(COMP_BAD_AST, "if condition must be bool")
    then_b = low.new_block()
    else_b = low.new_block()
    join_b = low.new_block()
    low.terminate(cur, {"op": "br", "cond": cond, "then": f"bb{then_b}", "else": f"bb{else_b}"})
    then_tail = _lower_block_contents(
        low, _need_block_stmts(low, stmt.get("then"), fname),
        then_b, fname, ret)
    if low.is_open(then_tail):
        low.terminate(then_tail, {"op": "jmp", "tgt": f"bb{join_b}"})
    else_node = stmt.get("else")
    if else_node is None:
        # No else: the false edge falls straight through to the join.
        low.terminate(else_b, {"op": "jmp", "tgt": f"bb{join_b}"})
    elif isinstance(else_node, dict) and else_node.get("kind") == "If":
        # else-if chain lowers in the else block without an extra join.
        bridged = _lower_if(low, else_node, else_b, fname, ret)
        if low.is_open(bridged):
            low.terminate(bridged, {"op": "jmp", "tgt": f"bb{join_b}"})
    elif isinstance(else_node, dict) and else_node.get("kind") == "Block":
        else_tail = _lower_block_contents(
            low, else_node.get("stmts"), else_b, fname, ret)
        if low.is_open(else_tail):
            low.terminate(else_tail, {"op": "jmp", "tgt": f"bb{join_b}"})
    else:
        _fail(COMP_BAD_AST, "If else must be a Block, If, or null")
    return join_b


def _lower_expr(low: _FunctionLowering, node: object, cur: int, fname: str) -> str:
    """Iterative post-order lowering over STABLE AST expression nodes.

    Stable nodes carry direct fields (left/right/operand/args), not children
    lists. The explicit work stack makes arbitrarily deep left-associated
    operator chains safe: the analyzer already accepts them, so lowering
    must too. Operands push right-first so the left operand lowers first,
    preserving left-to-right evaluation order.

    Every pushed occurrence carries a FRESH result key, so an aliased DAG
    (the same dict object twice, only possible in hand-built input) lowers
    each occurrence independently instead of colliding in the result map.
    Stack items are (node, built, key, child_keys).
    """
    results: dict[int, str] = {}
    active_nodes: set[int] = set()
    counter = [0]

    def fresh() -> int:
        counter[0] += 1
        return counter[0]

    stack: list[tuple] = [(node, False, 0, ())]
    while stack:
        item, built, key, kids = stack.pop()
        if not isinstance(item, dict):
            _fail(COMP_BAD_AST, f"function '{fname}' expression must be a dict")
        kind = item.get("kind")
        if kind in ("IntLit", "BoolLit", "StrLit", "Var"):
            results[key] = _lower_leaf(low, item, cur, fname)
        elif kind == "BinOp":
            left_node = item.get("left")
            right_node = item.get("right")
            if not isinstance(left_node, dict) or not isinstance(right_node, dict):
                _fail(COMP_BAD_AST, "BinOp needs left/right expression nodes")
            if not built:
                if id(item) in active_nodes:
                    _fail(COMP_BAD_AST, "expression AST contains a cycle")
                active_nodes.add(id(item))
                left_key, right_key = fresh(), fresh()
                stack.append((item, True, key, (left_key, right_key)))
                stack.append((right_node, False, right_key, ()))
                stack.append((left_node, False, left_key, ()))
            else:
                active_nodes.remove(id(item))
                left = results.pop(kids[0])
                right = results.pop(kids[1])
                results[key] = _lower_binop(low, item, left, right, cur, fname)
        elif kind == "UnOp":
            operand_node = item.get("operand")
            if not isinstance(operand_node, dict):
                _fail(COMP_BAD_AST, "UnOp needs an operand node")
            if not built:
                if id(item) in active_nodes:
                    _fail(COMP_BAD_AST, "expression AST contains a cycle")
                active_nodes.add(id(item))
                sub = fresh()
                stack.append((item, True, key, (sub,)))
                stack.append((operand_node, False, sub, ()))
            else:
                active_nodes.remove(id(item))
                operand = results.pop(kids[0])
                results[key] = _lower_unop(low, item, operand, cur, fname)
        elif kind == "Call":
            callee = item.get("callee")
            args = item.get("args")
            if not isinstance(callee, str) or not callee:
                _fail(COMP_V2_UNSUPPORTED, "call callee must be an Identifier name")
            if not isinstance(args, list):
                _fail(COMP_BAD_AST, "Call args must be a list")
            for arg in args:
                if not isinstance(arg, dict):
                    _fail(COMP_BAD_AST, "Call args must be expression nodes")
            if not built:
                if id(item) in active_nodes:
                    _fail(COMP_BAD_AST, "expression AST contains a cycle")
                active_nodes.add(id(item))
                keys = tuple(fresh() for _ in args)
                stack.append((item, True, key, keys))
                for arg, sub in zip(reversed(args), reversed(keys)):
                    stack.append((arg, False, sub, ()))
            else:
                active_nodes.remove(id(item))
                arg_temps = [results.pop(sub) for sub in kids]
                if callee == "print":
                    # Stage 16 builtin: exactly one int/bool/str argument
                    # (the analyzer guarantees the shape; re-checked here so
                    # the backend never trusts the frontend blindly).
                    if len(arg_temps) != 1:
                        _fail(COMP_BAD_AST, "print call must carry exactly one lowered argument")
                    atype = low.vreg_types.get(arg_temps[0])
                    helper = _RT_PRINT_BY_TYPE.get(atype)
                    if helper is None:
                        _fail(COMP_BAD_AST, f"print of {atype!r} needs int, bool, or str")
                    callee = helper
                results[key] = _lower_call(low, item, callee, arg_temps, cur, fname)
        elif kind in RESERVED_AST_KINDS:
            _fail(COMP_V2_UNSUPPORTED, f"expression kind '{kind}' is reserved for a later edition")
        else:
            _fail(COMP_V2_UNSUPPORTED, f"unknown expression kind {kind!r}")
    return results[0]


def _lower_leaf(low: _FunctionLowering, node: dict, cur: int, fname: str) -> str:
    kind = node.get("kind")
    if kind == "IntLit":
        text = node.get("value")
        if not isinstance(text, str):
            _fail(COMP_BAD_AST, "IntLit value must be a decimal string")
        try:
            value = int(text, 10)
        except ValueError:
            _fail(COMP_BAD_AST, f"IntLit has non-decimal value {text!r}")
        if value < 0 or value > _I64_MAX:
            _fail(COMP_BAD_AST, f"IntLit {text!r} out of i64 magnitude range")
        dst = low.new_vreg("int")
        low.emit(cur, {"op": "const", "dst": dst, "type": "int", "value": str(value)})
        return dst
    if kind == "BoolLit":
        value = node.get("value")
        if value is not True and value is not False:
            _fail(COMP_BAD_AST, f"BoolLit has invalid value {value!r}")
        dst = low.new_vreg("bool")
        low.emit(cur, {"op": "const", "dst": dst, "type": "bool", "value": value})
        return dst
    if kind == "StrLit":
        sid = _intern_str(node.get("value"), low.strtab, low.str_ids)
        dst = low.new_vreg("str")
        low.emit(cur, {"op": "const", "dst": dst, "type": "str", "value": sid})
        return dst
    if kind == "Var":
        sym = node.get("symbol")
        if not isinstance(sym, int) or sym not in low.symbols:
            _fail(COMP_BAD_AST, "Var references an unknown symbol")
        return low.symbols[sym]
    _fail(COMP_BAD_AST, f"unknown leaf kind {kind!r}")
    raise AssertionError("unreachable")  # _fail always raises


_BINOP_RULES = {
    "+": ("int", "int", "int"), "-": ("int", "int", "int"),
    "*": ("int", "int", "int"), "/": ("int", "int", "int"),
    "%": ("int", "int", "int"),
    "==": ("any-eq", "any-eq", "bool"), "!=": ("any-eq", "any-eq", "bool"),
    "<": ("int", "int", "bool"), ">": ("int", "int", "bool"),
    "<=": ("int", "int", "bool"), ">=": ("int", "int", "bool"),
    "&&": ("bool", "bool", "bool"), "||": ("bool", "bool", "bool"),
}


def _lower_binop(low: _FunctionLowering, node: dict, left: str, right: str,
                 cur: int, fname: str) -> str:
    op = node.get("op")
    want = node.get("type")
    rule = _BINOP_RULES.get(op) if isinstance(op, str) else None
    if rule is None:
        _fail(COMP_V2_UNSUPPORTED, f"unknown binary operator {op!r}")
    ltype = low.vreg_types[left]
    rtype = low.vreg_types[right]
    req_l, req_r, result = rule
    if req_l == "any-eq":
        if ltype != rtype or ltype not in VALUE_TYPES:
            _fail(COMP_BAD_AST, f"equality '{op}' needs matching int/bool/str, got {ltype}/{rtype}")
    elif ltype != req_l or rtype != req_r:
        _fail(COMP_BAD_AST, f"operator '{op}' needs {req_l}/{req_r}, got {ltype}/{rtype}")
    if want != result:
        _fail(COMP_BAD_AST, f"operator '{op}' result must be {result}, node says {want!r}")
    dst = low.new_vreg(result)
    low.emit(cur, {"op": "binop", "operator": op, "dst": dst, "type": result,
                   "l": left, "r": right})
    return dst


def _lower_unop(low: _FunctionLowering, node: dict, operand: str, cur: int, fname: str) -> str:
    op = node.get("op")
    want = node.get("type")
    otype = low.vreg_types[operand]
    if op == "-":
        if otype != "int" or want != "int":
            _fail(COMP_BAD_AST, "unary '-' needs int operand and int result")
        result = "int"
    elif op == "!":
        if otype != "bool" or want != "bool":
            _fail(COMP_BAD_AST, "unary '!' needs bool operand and bool result")
        result = "bool"
    else:
        _fail(COMP_V2_UNSUPPORTED, f"unknown unary operator {op!r}")
    dst = low.new_vreg(result)
    low.emit(cur, {"op": "unop", "operator": op, "dst": dst, "type": result, "v": operand})
    return dst


def _lower_call(low: _FunctionLowering, node: dict, callee: object, arg_temps: list,
                cur: int, fname: str) -> str:
    if not isinstance(callee, str) or not callee:
        _fail(COMP_BAD_AST, "call callee must be a non-empty name")
    want = node.get("type")
    if want not in ALL_TYPES:
        if want in RESERVED_TYPES:
            _fail(COMP_V2_UNSUPPORTED, f"call uses reserved type '{want}'")
        _fail(COMP_BAD_AST, f"call result type must be int/bool/str/unit, got {want!r}")
    for temp in arg_temps:
        if low.vreg_types[temp] not in VALUE_TYPES:
            _fail(COMP_BAD_AST, "call arguments must be int/bool/str values")
    sig = low.sigs.get(callee)
    if sig is None:
        _fail(COMP_BAD_AST, f"call references unknown function '{callee}'")
    param_types, ret_type = sig
    arg_types = [low.vreg_types[temp] for temp in arg_temps]
    if arg_types != param_types:
        _fail(COMP_BAD_AST,
              f"call to '{callee}' arguments {arg_types} != {param_types}")
    expected = "unit" if ret_type is None else ret_type
    if want != expected:
        _fail(COMP_BAD_AST,
              f"call to '{callee}' result must be {expected}, got {want!r}")
    if want == "unit":
        low.emit(cur, {"op": "call", "name": callee, "args": list(arg_temps)})
        return ""
    dst = low.new_vreg(want)
    low.emit(cur, {"op": "call", "dst": dst, "type": want, "name": callee, "args": list(arg_temps)})
    return dst


# ---------------------------------------------------------------------------
# Verifier: RIR dict -> list of error strings (empty means valid).
# Every rule below is independently testable; rule order is fixed so output
# is deterministic.
# ---------------------------------------------------------------------------

def verify_module(module: object) -> list:
    """Check every structural and type invariant; return error strings."""
    errors: list[str] = []
    if not isinstance(module, dict):
        return ["envelope: RIR module must be a dict"]
    if module.get("rir_version") != RIR_VERSION:
        return [f"envelope: rir_version must be {RIR_VERSION} "
                f"(got {module.get('rir_version')!r})"]
    if set(module) - {"rir_version", "source", "strtab", "funcs"}:
        errors.append("envelope: module carries unknown fields")
    source = module.get("source")
    if not isinstance(source, str):
        errors.append("envelope: source must be a string")
    strtab = module.get("strtab")
    if not isinstance(strtab, list):
        errors.append("envelope: strtab must be a list")
        strtab = []
    else:
        seen_bytes: set[str] = set()
        for index, entry in enumerate(strtab):
            if not isinstance(entry, dict):
                errors.append(f"strtab: entry {index} must be a dict")
                continue
            if set(entry) - {"id", "len", "bytes"}:
                errors.append(f"strtab: entry {index} carries unknown fields")
            if type(entry.get("id")) is not int or entry.get("id") != index:
                errors.append(f"strtab: entry {index} id must be {index}")
            length = entry.get("len")
            data = entry.get("bytes")
            if type(length) is not int or not isinstance(data, str):
                errors.append(f"strtab: entry {index} needs int len and str bytes")
                continue
            if any(ord(c) > 0x7F for c in data):
                errors.append(f"strtab: entry {index} must be ASCII")
            if length != len(data):
                errors.append(f"strtab: entry {index} len must equal byte length")
            if length < 0 or length > MAX_STR_LEN:
                errors.append(f"strtab: entry {index} length out of range")
            if data in seen_bytes:
                errors.append(f"strtab: entry {index} duplicates an earlier string")
            seen_bytes.add(data)
    funcs = module.get("funcs")
    if not isinstance(funcs, list):
        errors.append("envelope: funcs must be a list")
        return errors
    sigs: dict[str, tuple[list, object]] = {}
    names: set[str] = set()
    for func in funcs:
        _collect_sig(func, names, sigs, errors)
    for helper, hsig in RT_HELPERS.items():
        sigs[helper] = (list(hsig[0]), hsig[1])
    for index, func in enumerate(funcs):
        if isinstance(func, dict) and (type(func.get("symbol")) is not int
                                      or func.get("symbol") != index):
            errors.append(f"func {index}: symbol must equal source index {index}")
        _verify_function(func, names, sigs, len(strtab), errors)
    return errors


def _collect_sig(func: object, names: set, sigs: dict, errors: list) -> None:
    if not isinstance(func, dict):
        return
    name = func.get("name")
    if not _identifier(name):
        return
    if isinstance(name, str) and name.startswith(RT_PREFIX):
        errors.append(f"func: '{name}' uses the reserved '{RT_PREFIX}' runtime namespace")
        return
    if name in names:
        errors.append(f"func: duplicate function name '{name}'")
        return
    names.add(name)
    params = func.get("params")
    if not isinstance(params, list):
        return
    sigs[name] = ([p.get("type") for p in params if isinstance(p, dict)],
                  func.get("ret"))


def _verify_function(func: object, names: set, sigs: dict, strtab_len: int,
                     errors: list) -> None:
    if not isinstance(func, dict):
        errors.append("func: each function must be a dict")
        return
    if set(func) - {"name", "symbol", "params", "ret", "blocks", "frameslots"}:
        errors.append("func: function carries unknown fields")
    name = func.get("name")
    if not _identifier(name):
        errors.append("func: name must be an ASCII identifier")
        name = "<bad-func>"
    params = func.get("params")
    if not isinstance(params, list):
        errors.append(f"func '{name}': params must be a list")
        params = []
    ret = func.get("ret")
    if ret is not None and ret not in VALUE_TYPES:
        errors.append(f"func '{name}': ret must be int/bool/str/null")
        ret = None
    vregs: dict[str, str] = {}
    seen_psyms: set[int] = set()
    for index, param in enumerate(params):
        if not isinstance(param, dict):
            errors.append(f"func '{name}': param {index} must be a dict")
            continue
        if set(param) - {"name", "symbol", "type"}:
            errors.append(f"func '{name}': param {index} carries unknown fields")
        if not _identifier(param.get("name")):
            errors.append(f"func '{name}': param {index} name must be an ASCII identifier")
        ptype = param.get("type")
        if ptype not in VALUE_TYPES:
            errors.append(f"func '{name}': param {index} has invalid type {ptype!r}")
        psym = param.get("symbol")
        if type(psym) is not int or psym in seen_psyms:
            errors.append(f"func '{name}': param {index} has a duplicate or non-int symbol")
        else:
            seen_psyms.add(psym)
        vregs[f"%{index}"] = ptype if ptype in VALUE_TYPES else "int"
    blocks = func.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append(f"func '{name}': blocks must be a non-empty list")
        return
    param_slots = sum(2 if p.get("type") == "str" else 1
                      for p in params if isinstance(p, dict))
    if param_slots > MAX_FRAMESLOTS:
        errors.append(f"func '{name}': parameter slots {param_slots} exceeds "
                      f"{MAX_FRAMESLOTS}")
        return
    ids = [b.get("id") if isinstance(b, dict) else None for b in blocks]
    if ids != [f"bb{i}" for i in range(len(blocks))]:
        errors.append(f"func '{name}': block ids must be dense bb0..bb{len(blocks) - 1} in order")
    idset = {bid for bid in ids if isinstance(bid, str)}
    dom, reachable = _dominance(blocks, ids)
    # Two-pass def/use: pre-scan records every definition's first occurrence
    # so use checks see future defs. This makes same-block forward uses
    # report "before its definition" (not "undefined") and keeps the
    # same-block order check load-bearing instead of dead. The main loop
    # still owns redefinition errors via incremental _fresh.
    g_vregs: dict[str, str] = dict(vregs)
    g_defpos: dict[str, tuple] = {f"%{index}": (-1, -1) for index in range(len(params))}
    for _bi, _block in enumerate(blocks):
        if not isinstance(_block, dict):
            continue
        for _pos, _instr in enumerate(_block.get("instrs", [])):
            if not isinstance(_instr, dict):
                continue
            _dst = _instr.get("dst")
            _typ = _instr.get("type")
            if _instr.get("op") == "call" and _dst is None and _typ is None:
                continue
            if isinstance(_dst, str) and _dst not in g_vregs and _typ in VALUE_TYPES and _vreg(_dst):
                g_vregs[_dst] = _typ
                g_defpos[_dst] = (_bi, _pos)
    # defpos maps each vreg to its definition point; params dominate every
    # reachable block. Uses are checked against real dominance, not layout
    # order, so a definition on one branch can never authorize a use after
    # the join.
    defpos: dict[str, tuple] = {f"%{index}": (-1, -1) for index in range(len(params))}
    for bindex, block in enumerate(blocks):
        _verify_block(func, name, bindex, block, vregs, idset, strtab_len, ret,
                      sigs, errors, defpos, dom, reachable, g_vregs, g_defpos)
    # frameslots must equal the shared allocator's recomputation exactly, so
    # builder, verifier, and emitter can never disagree on homes.
    try:
        _slot_of, want_slots = assign_slots(blocks, vregs, len(params))
    except Exception:
        errors.append(f"func '{name}': slot assignment failed on malformed blocks")
        return
    if type(func.get("frameslots")) is not int or func.get("frameslots") != want_slots:
        errors.append(f"func '{name}': frameslots must equal the recomputed slot sum {want_slots}")
    if want_slots > MAX_FRAMESLOTS:
        errors.append(f"func '{name}': frameslots {want_slots} exceeds {MAX_FRAMESLOTS}")


def _dominance(blocks: list, ids: list) -> tuple:
    """Real dominance over the block CFG (iterative dataflow, deterministic).

    Returns (dom, reachable) where dom maps each reachable block index to
    the set of block indices dominating it. Malformed terminators simply
    contribute no edges; unreachable blocks are absent from reachable.
    """
    count = len(blocks)
    index_of: dict = {}
    for pos, bid in enumerate(ids):
        if isinstance(bid, str) and bid not in index_of:
            index_of[bid] = pos
    succ: dict = {i: [] for i in range(count)}
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        term = block.get("term")
        if not isinstance(term, dict):
            continue
        for key in ("tgt", "then", "else"):
            tgt = term.get(key)
            if isinstance(tgt, str) and tgt in index_of:
                dest = index_of[tgt]
                if dest not in succ[i]:
                    succ[i].append(dest)
    reachable: set = set()
    work = [0] if count else []
    while work:
        node = work.pop()
        if node in reachable:
            continue
        reachable.add(node)
        work.extend(succ[node])
    dom: dict = {i: set(reachable) for i in reachable}
    if 0 in reachable:
        dom[0] = {0}
    changed = True
    while changed:
        changed = False
        for i in sorted(reachable):
            if i == 0:
                continue
            preds = [p for p in range(count) if i in succ[p] and p in reachable]
            if not preds:
                continue
            new = set.intersection(*[dom[p] for p in preds]) | {i}
            if new != dom[i]:
                dom[i] = new
                changed = True
    return dom, reachable


def _use(errors: list, func: str, where: str, vreg: object, vregs: dict,
         defpos: dict, dom: dict, reachable: set, bindex: int,
         pos: int, g_vregs: dict | None = None,
         g_defpos: dict | None = None) -> object:
    universe = g_vregs if g_vregs is not None else vregs
    spots = g_defpos if g_defpos is not None else defpos
    if not isinstance(vreg, str) or vreg not in universe:
        errors.append(f"func '{func}': {where} uses undefined vreg {vreg!r}")
        return "int"
    if bindex in reachable:
        spot = spots.get(vreg)
        if spot is None:
            errors.append(f"func '{func}': {where} uses vreg {vreg} with no recorded definition")
            return universe[vreg]
        dbi, dpos = spot
        if dbi == bindex:
            if not dpos < pos:
                errors.append(f"func '{func}': {where} uses vreg {vreg} before its definition")
        elif dbi != -1 and dbi not in dom.get(bindex, set()):
            errors.append(f"func '{func}': {where} uses vreg {vreg} not dominated by its definition")
    else:
        # Unreachable blocks have no dominators; still enforce existence
        # (checked above) and same-block ordering so dead tails verify
        # fully instead of silently accepting forward uses.
        spot = spots.get(vreg)
        if spot is not None and spot[0] == bindex and not spot[1] < pos:
            errors.append(f"func '{func}': {where} uses vreg {vreg} before its definition")
    return universe[vreg]


def _verify_block(func: dict, name: str, bindex: int, block: object, vregs: dict,
                   idset: set, strtab_len: int, ret: object, sigs: dict,
                   errors: list, defpos: dict, dom: dict, reachable: set,
                   g_vregs: dict | None = None,
                   g_defpos: dict | None = None) -> None:
    if not isinstance(block, dict):
        errors.append(f"func '{name}': block {bindex} must be a dict")
        return
    if set(block) - {"id", "instrs", "term"}:
        errors.append(f"func '{name}': block {bindex} carries unknown fields")
    instrs = block.get("instrs")
    if not isinstance(instrs, list):
        errors.append(f"func '{name}': block {block.get('id')} instrs must be a list")
        return
    term = block.get("term")
    if not isinstance(term, dict):
        errors.append(f"func '{name}': block {block.get('id')} needs exactly one terminator")
        return
    for pos, instr in enumerate(instrs):
        _verify_instr(func, name, block.get("id"), pos, instr, vregs, defpos,
                      dom, reachable, bindex, strtab_len, sigs, errors,
                      g_vregs, g_defpos)
    _verify_term(func, name, block.get("id"), term, vregs, defpos, dom,
                 reachable, bindex, len(instrs), idset, ret, errors,
                 g_vregs, g_defpos)


def _fresh(errors: list, func: str, where: str, dst: object, typ: object,
           vregs: dict, defpos: dict, bindex: int, pos: int) -> None:
    if not _vreg(dst):
        errors.append(f"func '{func}': {where} dst must be a numeric vreg")
        return
    if dst in vregs:
        errors.append(f"func '{func}': {where} redefines vreg {dst}")
        return
    if typ not in VALUE_TYPES:
        errors.append(f"func '{func}': {where} has invalid type {typ!r}")
        return
    vregs[dst] = typ
    defpos[dst] = (bindex, pos)


def _verify_instr(func: dict, name: str, bid: object, pos: int, instr: object,
                   vregs: dict, defpos: dict, dom: dict, reachable: set,
                   bindex: int, strtab_len: int,
                   sigs: dict, errors: list,
                   g_vregs: dict | None = None,
                   g_defpos: dict | None = None) -> None:
    where = f"block {bid} instr {pos}"
    if not isinstance(instr, dict):
        errors.append(f"func '{name}': {where} must be a dict")
        return
    op = instr.get("op")
    if op in ("jmp", "br", "ret", "unreachable"):
        errors.append(f"func '{name}': {where} terminator must not appear as instruction")
        return
    if op in RESERVED_OPS:
        errors.append(f"func '{name}': {where} uses reserved opcode '{op}'")
        return
    if op == "const":
        if set(instr) - {"op", "dst", "type", "value"}:
            errors.append(f"func '{name}': {where} const carries unknown fields")
        typ = instr.get("type")
        value = instr.get("value")
        if typ == "int":
            if (not isinstance(value, str) or not value or not value.isascii()
                    or not value.isdigit()):
                errors.append(f"func '{name}': {where} int const value must be a decimal string")
            else:
                try:
                    number = int(value, 10)
                except ValueError:
                    errors.append(f"func '{name}': {where} int const is not decimal")
                else:
                    if number < 0 or number > _I64_MAX:
                        errors.append(f"func '{name}': {where} int const out of range")
        elif typ == "bool":
            if value is not True and value is not False:
                errors.append(f"func '{name}': {where} bool const must be true/false")
        elif typ == "str":
            if type(value) is not int or value < 0 or value >= strtab_len:
                errors.append(f"func '{name}': {where} str const must reference strtab [0,{strtab_len})")
        else:
            errors.append(f"func '{name}': {where} const has invalid type {typ!r}")
            return
        _fresh(errors, name, where, instr.get("dst"), typ, vregs, defpos, bindex, pos)
    elif op == "copy":
        if set(instr) - {"op", "dst", "type", "src"}:
            errors.append(f"func '{name}': {where} copy carries unknown fields")
        typ = instr.get("type")
        src = instr.get("src")
        stype = _use(errors, name, where, src, vregs, defpos, dom, reachable, bindex, pos, g_vregs, g_defpos)
        if typ != stype:
            errors.append(f"func '{name}': {where} copy type {typ!r} != src type {stype!r}")
        _fresh(errors, name, where, instr.get("dst"), typ, vregs, defpos, bindex, pos)
    elif op == "binop":
        if set(instr) - {"op", "operator", "dst", "type", "l", "r"}:
            errors.append(f"func '{name}': {where} binop carries unknown fields")
        _verify_binop(func, name, where, instr, vregs, defpos, dom, reachable, bindex, pos, errors, g_vregs, g_defpos)
    elif op == "unop":
        if set(instr) - {"op", "operator", "dst", "type", "v"}:
            errors.append(f"func '{name}': {where} unop carries unknown fields")
        _verify_unop(func, name, where, instr, vregs, defpos, dom, reachable, bindex, pos, errors, g_vregs, g_defpos)
    elif op == "call":
        allowed = {"op", "name", "args", "dst", "type"}
        if set(instr) - allowed:
            errors.append(f"func '{name}': {where} call carries unknown fields")
        _verify_call(func, name, where, instr, vregs, defpos, dom, reachable, bindex, pos, sigs, errors, g_vregs, g_defpos)
    else:
        errors.append(f"func '{name}': {where} has unknown opcode {op!r}")


def _verify_binop(func, name, where, instr, vregs, defpos, dom, reachable, bindex, pos, errors, g_vregs=None, g_defpos=None) -> None:
    operator = instr.get("operator")
    typ = instr.get("type")
    ltype = _use(errors, name, where, instr.get("l"), vregs, defpos, dom, reachable, bindex, pos, g_vregs, g_defpos)
    rtype = _use(errors, name, where, instr.get("r"), vregs, defpos, dom, reachable, bindex, pos, g_vregs, g_defpos)
    good = False
    if operator in ("+", "-", "*", "/", "%"):
        good = ltype == "int" and rtype == "int" and typ == "int"
    elif operator in ("==", "!="):
        good = ltype == rtype and ltype in VALUE_TYPES and typ == "bool"
    elif operator in ("<", ">", "<=", ">="):
        good = ltype == "int" and rtype == "int" and typ == "bool"
    elif operator in ("&&", "||"):
        good = ltype == "bool" and rtype == "bool" and typ == "bool"
    if not good:
        errors.append(f"func '{name}': {where} binop '{operator}' mistyped ({ltype},{rtype})->{typ!r}")
    _fresh(errors, name, where, instr.get("dst"), typ, vregs, defpos, bindex, pos)


def _verify_unop(func, name, where, instr, vregs, defpos, dom, reachable, bindex, pos, errors, g_vregs=None, g_defpos=None) -> None:
    operator = instr.get("operator")
    typ = instr.get("type")
    vtype = _use(errors, name, where, instr.get("v"), vregs, defpos, dom, reachable, bindex, pos, g_vregs, g_defpos)
    if operator == "-":
        good = vtype == "int" and typ == "int"
    elif operator == "!":
        good = vtype == "bool" and typ == "bool"
    else:
        good = False
    if not good:
        errors.append(f"func '{name}': {where} unop '{operator}' mistyped ({vtype})->{typ!r}")
    _fresh(errors, name, where, instr.get("dst"), typ, vregs, defpos, bindex, pos)


def _verify_call(func, name, where, instr, vregs, defpos, dom, reachable, bindex, pos, sigs, errors, g_vregs=None, g_defpos=None) -> None:
    callee = instr.get("name")
    args = instr.get("args")
    if not isinstance(callee, str) or not callee:
        errors.append(f"func '{name}': {where} call needs a callee name")
        return
    if not isinstance(args, list):
        errors.append(f"func '{name}': {where} call args must be a list")
        return
    arg_types = [_use(errors, name, where, arg, vregs, defpos, dom, reachable, bindex, pos, g_vregs, g_defpos) for arg in args]
    dst = instr.get("dst", None)
    typ = instr.get("type", None)
    if (dst is None) != (typ is None):
        errors.append(f"func '{name}': {where} call dst/type must both be present or absent")
        return
    sig = sigs.get(callee)
    if sig is None:
        errors.append(f"func '{name}': {where} calls unknown function '{callee}'")
    else:
        want_args, want_ret = sig
        if len(args) != len(want_args):
            errors.append(f"func '{name}': {where} call arity {len(args)} != {len(want_args)} "
                          f"for '{callee}'")
        else:
            for index, (got, want) in enumerate(zip(arg_types, want_args)):
                if got != want:
                    errors.append(f"func '{name}': {where} call arg {index} is {got}, "
                                  f"'{callee}' wants {want}")
        if want_ret is None:
            if dst is not None:
                errors.append(f"func '{name}': {where} call to unit function must discard")
        elif dst is None or typ != want_ret:
            errors.append(f"func '{name}': {where} call result must be {want_ret}")
    if dst is not None:
        _fresh(errors, name, where, dst, typ, vregs, defpos, bindex, pos)


def _verify_term(func, name, bid, term, vregs, defpos, dom, reachable, bindex, npos, idset, ret, errors, g_vregs=None, g_defpos=None) -> None:
    where = f"block {bid} terminator"
    if not isinstance(term, dict):
        errors.append(f"func '{name}': {where} must be a dict")
        return
    op = term.get("op")
    if op == "jmp":
        if not isinstance(term.get("tgt"), str) or term.get("tgt") not in idset:
            errors.append(f"func '{name}': {where} jumps to unknown block")
        if set(term) - {"op", "tgt"}:
            errors.append(f"func '{name}': {where} jmp must only carry tgt")
    elif op == "br":
        ctype = _use(errors, name, where, term.get("cond"), vregs, defpos, dom, reachable, bindex, npos, g_vregs, g_defpos)
        if ctype != "bool":
            errors.append(f"func '{name}': {where} br condition must be bool")
        for key in ("then", "else"):
            if not isinstance(term.get(key), str) or term.get(key) not in idset:
                errors.append(f"func '{name}': {where} {key} targets unknown block")
        if set(term) - {"op", "cond", "then", "else"}:
            errors.append(f"func '{name}': {where} br must only carry cond/then/else")
    elif op == "ret":
        value = term.get("v", None)
        if ret is None:
            if value is not None:
                errors.append(f"func '{name}': {where} unit function must use bare ret")
        else:
            if value is None:
                errors.append(f"func '{name}': {where} non-unit function must return a value")
            else:
                vtype = _use(errors, name, where, value, vregs, defpos, dom, reachable, bindex, npos, g_vregs, g_defpos)
                if vtype != ret:
                    errors.append(f"func '{name}': {where} returns {vtype!r}, signature says {ret!r}")
        if set(term) - {"op", "v"}:
            errors.append(f"func '{name}': {where} ret must only carry v")
    elif op == "unreachable":
        if set(term) - {"op"}:
            errors.append(f"func '{name}': {where} unreachable carries no fields")
    else:
        errors.append(f"func '{name}': {where} has unknown terminator {op!r}")


# ---------------------------------------------------------------------------
# Canonical serialization: deterministic, human-readable, golden-testable.
# ---------------------------------------------------------------------------

def _escape_str_bytes(data: str) -> str:
    out = []
    for char in data:
        code = ord(char)
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif 0x20 <= code <= 0x7E:
            out.append(char)
        else:
            out.append(f"\\x{code:02x}")
    return "".join(out)


def dumps(module: dict) -> str:
    """Serialize an RIR module to canonical text (byte-deterministic)."""
    lines = [f'; rir_version={module.get("rir_version", RIR_VERSION)} '
             f'source="{module.get("source", "")}" funcs={len(module.get("funcs", []))}']
    for entry in module.get("strtab", []):
        lines.append(f'.strtab #{entry["id"]} len={entry["len"]} '
                     f'bytes="{_escape_str_bytes(entry["bytes"])}"')
    for func in module.get("funcs", []):
        params = ", ".join(f'{p["name"]}: {p["type"]}' for p in func.get("params", []))
        ret = func.get("ret")
        lines.append(f'.function {func.get("name")} : ({params}) -> '
                     f'{ret if ret is not None else "unit"} '
                     f'; symbol={func.get("symbol")} frameslots={func.get("frameslots")}')
        for block in func.get("blocks", []):
            lines.append(f'.block {block.get("id")}')
            for instr in block.get("instrs", []):
                lines.append(f'  {_dump_instr(instr)}')
            lines.append(f'  {_dump_term(block.get("term", {}))}')
    return "\n".join(lines) + "\n"


def _dump_instr(instr: dict) -> str:
    op = instr.get("op")
    if op == "const":
        typ = instr.get("type")
        value = instr.get("value")
        if typ == "bool":
            value = "true" if value else "false"
        elif typ == "str":
            value = f"#{value}"
        return f'{instr.get("dst")} = const {typ} {value}'
    if op == "copy":
        return f'{instr.get("dst")} = copy {instr.get("type")} {instr.get("src")}'
    if op == "binop":
        return (f'{instr.get("dst")} = binop {instr.get("type")} {instr.get("operator")} '
                f'{instr.get("l")} {instr.get("r")}')
    if op == "unop":
        return f'{instr.get("dst")} = unop {instr.get("type")} {instr.get("operator")} {instr.get("v")}'
    if op == "call":
        args = ", ".join(instr.get("args", []))
        if "dst" in instr:
            return f'{instr.get("dst")} = call {instr.get("type")} {instr.get("name")}({args})'
        return f'call {instr.get("name")}({args})'
    return f"<bad-op {op!r}>"


def _dump_term(term: dict) -> str:
    op = term.get("op")
    if op == "jmp":
        return f'jmp {term.get("tgt")}'
    if op == "br":
        return f'br {term.get("cond")} {term.get("then")} {term.get("else")}'
    if op == "ret":
        return "ret" if term.get("v") is None else f'ret {term.get("v")}'
    if op == "unreachable":
        return "unreachable"
    return f"<bad-term {op!r}>"
