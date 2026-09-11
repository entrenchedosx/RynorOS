#!/usr/bin/env python3
"""Canonical aggregate value types for Stage 19a (shared, engine-neutral).

Host-side, Python 3.10+ standard library only. This module owns the
frozen 19a type algebra so lex/parse/analyze/RIR/compile/oracle can
never disagree: canonical strings, static sizes, slot widths, capacity
bounds, err codes, and the FNV-1a-64 map hash. It imports nothing from
the package (engines import it, never the reverse), keeping rir.py
standalone-importable per the Stage 14 precedent.

See docs/design/rynorlang-aggregates.md for the frozen specification.
"""

from __future__ import annotations


# Frozen scalar types (Stage 14). Home widths: int/bool 1 slot, str 2.
SCALAR_TYPES = ("int", "bool", "str")
SCALAR_SIZES = {"int": 8, "bool": 8, "str": 16}

# Parametric builtins: name -> (n_type_args, last_arg_is_capacity).
PARAMETRIC = {"list": (2, True), "map": (3, True), "status": (1, False)}

# Storable positions (list elements, map values, record fields, status
# payloads) accept any value type except status itself (single-level
# tags; 19b may revisit). Map keys are exactly int|bool|str.
KEY_TYPES = ("int", "bool", "str")

# Frozen aggregate bounds (see the 19a RFC §4/§11).
MAX_AGG_BYTES = 8192
MAX_TYPE_NESTING = 8

# Stage 19a aggregate builtins (reserved names, `print` precedent: user
# functions may not claim them; analyzer lowers calls to dedicated RIR
# ops). Single source: analyze.py and rir.py both import this tuple.
AGG_BUILTINS = ("len", "push", "insert", "get", "is_ok", "is_err",
                "unwrap_or", "byte_at")

# Frozen 19a err codes (int payload of err statuses).
ERR_FULL = 1
ERR_NOTFOUND = 2
ERR_OORANGE = 3
# 4 NOMEM is reserved and unused in 19a (frames are static).


def _is_name(text: str) -> bool:
    return (isinstance(text, str) and bool(text) and text.isascii()
            and (text[0].isalpha() or text[0] == "_")
            and all(c.isalnum() or c == "_" for c in text[1:]))


def parse_type(text: object) -> tuple | None:
    """Parse a canonical type string into a shape tree.

    Returns ("scalar", name), ("nominal", name), or ("generic", base,
    (args,)) where capacity args are ("cap", int). Returns None when
    the string is not a well-formed type shape. Shape validity (known
    bases, arg counts, key/storable rules, bounds) is validate_type's
    job; this function only checks shape.
    """
    if not isinstance(text, str) or not text:
        return None
    node, pos = _parse_shape(text, 0)
    if node is None or pos != len(text):
        return None
    return node


def _parse_shape(text: str, pos: int) -> tuple | None:
    start = pos
    while pos < len(text) and (text[pos].isalnum() or text[pos] == "_"):
        pos += 1
    name = text[start:pos]
    if not _is_name(name):
        return None, start
    if pos < len(text) and text[pos] == "<":
        args: list = []
        pos += 1
        while True:
            if pos < len(text) and text[pos] == ">":
                pos += 1
                break
            if args:
                if pos >= len(text) or text[pos] != ",":
                    return None, start
                pos += 1
            if pos < len(text) and text[pos].isdigit():
                digits = pos
                while pos < len(text) and text[pos].isdigit():
                    pos += 1
                args.append(("cap", int(text[digits:pos])))
            else:
                sub, pos = _parse_shape(text, pos)
                if sub is None:
                    return None, start
                args.append(sub)
            if pos >= len(text):
                return None, start
        return ("generic", name, tuple(args)), pos
    if name in SCALAR_TYPES:
        return ("scalar", name), pos
    return ("nominal", name), pos


def canonical(node: tuple) -> str:
    """Render a shape tree in canonical form (no inner whitespace)."""
    kind = node[0]
    if kind in ("scalar", "nominal"):
        return node[1]
    _, base, args = node
    parts = []
    for arg in args:
        if isinstance(arg, tuple) and arg and arg[0] == "cap":
            parts.append(str(arg[1]))
        else:
            parts.append(canonical(arg))
    return f"{base}<{','.join(parts)}>"


def validate_type(node: tuple, depth: int = 0) -> str | None:
    """Check shape rules; return an error tag or None when valid.

    Tags: "unknown-base", "arity", "bad-cap", "bad-key", "not-storable",
    "nested-status", "too-deep". Callers map tags to their own
    diagnostics (SEM_* in the analyzer, COMP_BAD_AST in RIR).
    """
    if depth > MAX_TYPE_NESTING:
        return "too-deep"
    kind = node[0]
    if kind == "scalar":
        return None
    if kind == "nominal":
        return None
    if depth >= MAX_TYPE_NESTING:
        return "too-deep"
    _, base, args = node
    spec = PARAMETRIC.get(base)
    if spec is None:
        return "unknown-base"
    want, has_cap = spec
    if len(args) != want:
        return "arity"
    body = args[:-1] if has_cap else args
    for arg in body:
        if not isinstance(arg, tuple) or (arg[0] not in ("scalar", "nominal", "generic")):
            return "arity"
        # No status inside collections or status payloads (single-level
        # tags everywhere: indexing a list yields status<T> directly, so
        # T itself is never a status; 19b may revisit alongside Result).
        if arg[0] == "generic" and arg[1] == "status":
            return "nested-status"
        err = validate_type(arg, depth + 1)
        if err is not None:
            return err
    if has_cap:
        cap = args[-1]
        if not (isinstance(cap, tuple) and cap and cap[0] == "cap"):
            return "arity"
        if cap[1] < 1:
            return "bad-cap"
    if base == "map":
        key = body[0]
        if not (key[0] == "scalar" and key[1] in KEY_TYPES):
            return "bad-key"
    return None


def size_of(node: tuple, record_sizes: dict | None = None) -> int | None:
    """Static byte size, or None for an undeclared nominal record."""
    kind = node[0]
    if kind == "scalar":
        return SCALAR_SIZES[node[1]]
    if kind == "nominal":
        if not record_sizes:
            return None
        return record_sizes.get(node[1])
    _, base, args = node
    if base == "list":
        elem = size_of(args[0], record_sizes)
        if elem is None:
            return None
        return 8 + args[1][1] * elem
    if base == "map":
        key = size_of(args[0], record_sizes)
        val = size_of(args[1], record_sizes)
        if key is None or val is None:
            return None
        return 8 + args[2][1] * (8 + key + val)
    if base == "status":
        pay = size_of(args[0], record_sizes)
        if pay is None:
            return None
        return 16 + pay
    return None


def check_bounded(node: tuple, record_sizes: dict | None = None) -> str | None:
    """Enforce MAX_AGG_BYTES; return an error tag or None.

    Tags: "unknown-record" (nominal size unresolvable here),
    "too-big". Scalar-only trees are trivially bounded (None).
    """
    if node[0] == "scalar":
        return None
    size = size_of(node, record_sizes)
    if size is None:
        return "unknown-record"
    if size > MAX_AGG_BYTES:
        return "too-big"
    return None


def slot_width_for_size(size: int) -> int:
    """Home slots (8 bytes each) for a static byte size."""
    return (size + 7) // 8


def slot_width(node: tuple, record_sizes: dict | None = None) -> int | None:
    """Home slots for a validated type node (None if unresolvable)."""
    if node[0] == "scalar":
        return 1 if node[1] in ("int", "bool") else 2
    size = size_of(node, record_sizes)
    if size is None:
        return None
    return slot_width_for_size(size)


def is_value_type_text(text: object, record_names: set | None = None) -> bool:
    """True for scalar types, valid parametric shapes, or declared records.

    Used by RIR/compile gates that only have the canonical string (plus
    the module's record table). Full rule errors come from validate_type;
    this is the membership fast path.
    """
    if text in SCALAR_TYPES:
        return True
    node = parse_type(text)
    if node is None:
        return False
    if node[0] == "nominal":
        return record_names is not None and node[1] in record_names
    if node[0] == "generic":
        if validate_type(node) is not None:
            return False
        return True
    return False


def fnv1a64(data: bytes) -> int:
    """FNV-1a 64-bit (map hashing; oracle/differentials share this exact
    function — the native emitter re-implements it and differentials
    prove equality)."""
    h = 14695981039346656037
    for byte in data:
        h ^= byte
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def key_bytes(kind: str, value: object) -> bytes:
    """Canonical key bytes for hashing (int: 8-byte LE of the u64 bits;
    bool: one byte; str: raw ASCII bytes)."""
    if kind == "int":
        return (int(value) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
    if kind == "bool":
        return b"\x01" if value else b"\x00"
    return str(value).encode("ascii")
