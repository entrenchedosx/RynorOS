#!/usr/bin/env python3
"""Stable AST + semantics for Stage 14. Host-side, stdlib only."""

from __future__ import annotations

import argparse
import bisect
from types import GeneratorType
import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.rynorlang.lex import Span, Token, lex
from tools.rynorlang.parse import parse, parse_tokens, ParseNode
from tools.rynorlang import agtypes as _agtypes

MAX_DEPTH = 256

# Frozen codes
CODE_LEX = "PAR_LEX_ERROR"
CODE_FILE = "PAR_FILE_TOO_LARGE"
CODE_INVALID = "PAR_INVALID_INPUT"
CODE_UNEXP_TOKEN = "PAR_UNEXPECTED_TOKEN"
CODE_UNEXP_EOF = "PAR_UNEXPECTED_EOF"
CODE_EXPECTED = "PAR_EXPECTED_TOKEN"
CODE_DEPTH = "PAR_DEPTH_EXCEEDED"

C_UNDECLARED = "SEM_UNDECLARED"
C_DUPLICATE = "SEM_DUPLICATE"
C_TYPE_MISMATCH = "SEM_TYPE_MISMATCH"
C_ARITY_MISMATCH = "SEM_ARITY_MISMATCH"
C_UNKNOWN_FUNCTION = "SEM_UNKNOWN_FUNCTION"
# Statically known capacity overflow (string literals past MAX_STR_LEN).
C_LIMIT_EXCEEDED = "SEM_LIMIT_EXCEEDED"
# Stage 19d profile gate (additive: only fires under --profile=strict,
# which no earlier input requests).
C_PROFILE_EXCLUDED = "SEM_PROFILE_EXCLUDED"
# Backend string cap, mirrored here (rir.py must stay standalone-importable,
# so the constant is duplicated, not imported): statically known overlong
# literals fail here, never in the backend.
MAX_STR_LEN = 4096

# Stage 19a aggregate builtins (reserved names, `print` precedent).
# Single source of truth lives in agtypes (rir.py must agree exactly).
AGG_BUILTINS = _agtypes.AGG_BUILTINS
# Names a record declaration may not claim (type constructors, builtins,
# print, and control words share the top-level namespace with functions;
# a record named `match` would be indistinguishable from a match
# statement at statement position).
RESERVED_TYPE_NAMES = ("list", "map", "status", "result", "print",
                       "match", "break", "continue", "use") + AGG_BUILTINS
# Words no function may claim (reserved builtins plus control words;
# `match` keeps working as a variable via statement backtracking).
# Stage 19c adds `use` (import directive): `fn use` is SEM_DUPLICATE
# while `let use` keeps working through the same fallback.
RESERVED_FN_NAMES = ("print", "match", "break", "continue", "use") + AGG_BUILTINS

# Stage 15b shell-edition codes (additive-only; the six SEM_* above are frozen).
S_UNKNOWN_COMMAND = "SHELL_UNKNOWN_COMMAND"
S_AMBIGUOUS_COMMAND = "SHELL_AMBIGUOUS_COMMAND"
S_PIPELINE_TYPE = "SHELL_PIPELINE_TYPE_MISMATCH"
S_UNIT_STAGE = "SHELL_UNIT_STAGE"
S_REDIRECT = "SHELL_REDIRECT_ERROR"
S_COMMAND_ARITY = "SHELL_COMMAND_ARITY"
S_COMMAND_TYPE = "SHELL_COMMAND_TYPE_MISMATCH"

SHELL_EDITIONS = ("shell", "shell-preview")
# Minimal command-signature model for host-side semantic testing. Each entry
# maps a command name to ([param types], return type or None for unit).
# Types are str/int/bool/flag. This is a test-stub abstraction, NOT a claim
# that these commands exist on RynorOS; real commands arrive with modules
# (19a) and the loader (16). Pass an explicit table in tests.
STUB_COMMAND_TYPES = ("str", "int", "bool", "flag")


def _normalize_edition(edition: str) -> str:
    if edition in SHELL_EDITIONS:
        return "shell"
    return "v1"


VALID_PROFILES = ("default", "strict", "core")


def _normalize_profile(profile: str) -> str:
    if profile in VALID_PROFILES:
        return profile
    return "default"

@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    span: Span
    expected: object | None = None
    got: object | None = None
    name: str | None = None
    callee: str | None = None
    context: str | None = None
    operator: str | None = None
    got_kind: str | None = None
    got_lexeme: str | None = None

@dataclass(frozen=True)
class AnalyzeResult:
    ast: Optional[dict]
    diagnostic: Optional[Diagnostic]
    @property
    def ok(self):
        return self.diagnostic is None

class _AbortAnalysis(Exception):
    pass


class Analyzer:
    def __init__(self, program: ParseNode, source: str | None = None, tokens: tuple[Token, ...] = (),
                 edition: str = "v1", commands: dict | None = None, external: dict | None = None,
                 imports: dict | None = None, self_alias: str | None = None,
                 profile: str = "default"):
        self.program = program
        self.depth = 0
        # Stage 19b loop nesting for break/continue targeting (separate
        # from the depth budget: unbounded nesting is finite source).
        self._loop_depth = 0
        self.edition = _normalize_edition(edition)
        # Stage 19d profile gate: strict excludes shell constructs even
        # when the edition allows them (default is byte-identical: the
        # check below never fires).
        self.profile = _normalize_profile(profile)
        # Host-side stub registry: name -> ([param types], ret or None).
        # None means no command is known (every Cmd is SHELL_UNKNOWN_COMMAND).
        self.commands = commands
        self.global_funcs = {}  # name -> {params: [(name,type)], ret_type, span, symbol, node}
        # Stage 19a records: name -> {fields: [(fname, canonical_type)],
        # span, symbol}. Collected in the first pass like functions.
        self.record_decls = {}
        self.record_sizes = {}  # name -> static byte size (recursion-checked)
        # Stage 19c modules: preloaded foreign globals (mangled names) and
        # this file's alias table. Own top-level names mangle with the
        # file's alias (entry files pass None: identity, v1-identical).
        # `own` maps bare -> stored for same-file references.
        self.imports = dict(imports) if imports else {}
        self.self_alias = self_alias
        self.own: dict[str, str] = {}
        if external:
            for name, info in external.get("funcs", {}).items():
                self.global_funcs[name] = dict(info)
            for name, info in external.get("records", {}).items():
                self.record_decls[name] = dict(info)
        self.sym_counter = 0
        self.diagnostic: Optional[Diagnostic] = None
        self.source = source
        self.line_starts = [0] + [i + 1 for i, char in enumerate(source or "") if char == "\n"]
        self.end_positions: dict[int, tuple[int, int]] = {}
        for token in tokens:
            line, column = token.span.line, token.span.column
            for char in token.lexeme:
                if char == "\n":
                    line, column = line + 1, 1
                else:
                    column += 1
            self.end_positions[token.span.offset + token.span.length] = (line, column)

    def _stored(self, bare: str) -> str:
        # Storage name for an own top-level declaration (identity for the
        # entry file, alias-mangled otherwise). Always populated, so every
        # top-level lookup below behaves identically for both.
        if self.self_alias is None:
            return bare
        return f"{self.self_alias}__{bare}"

    def _top(self, name: str) -> str:
        # Resolve a bare top-level reference to its stored name.
        return self.own.get(name, name)

    def _qualify(self, alias: str, base: str, span: Span):
        # Resolve alias::base to its stored (mangled) name or raise.
        if alias not in self.imports:
            self._error(C_UNDECLARED, f"unknown module '{alias}'", span,
                        expected="imported module", got=alias, name=alias, context="qualified name")
        return f"{alias}__{base}"

    def _split_qualified(self, text: str):
        if "::" in text:
            alias, _, base = text.partition("::")
            if not alias or not base or "::" in base:
                return None
            return alias, base
        return None

    def _span_dict(self, span: Span) -> dict:
        end_offset = span.offset + span.length
        line, column = span.line, span.column
        if self.source is not None:
            line = bisect.bisect_right(self.line_starts, end_offset)
            column = end_offset - self.line_starts[line - 1] + 1
        elif end_offset in self.end_positions:
            line, column = self.end_positions[end_offset]
        return {
            "filename": span.filename,
            "line": span.line,
            "column": span.column,
            "offset": span.offset,
            "length": span.length,
            "start": {"line": span.line, "column": span.column, "offset": span.offset},
            "end": {"line": line, "column": column, "offset": end_offset},
        }

    def _node_span(self, node: ParseNode) -> dict:
        return self._span_dict(node.span)

    def _enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            # use program span
            span = self.program.span
            self.diagnostic = Diagnostic(CODE_DEPTH, f"nesting depth exceeds {MAX_DEPTH}", span)
            raise _AbortAnalysis

    def _leave(self):
        self.depth -= 1

    def _error(self, code: str, message: str, span: Span, **details):
        if self.diagnostic is None:
            self.diagnostic = Diagnostic(code, message, span, **details)
        raise _AbortAnalysis

    def analyze(self) -> AnalyzeResult:
        try:
            if self.commands is not None:
                if not isinstance(self.commands, dict):
                    return AnalyzeResult(None, Diagnostic(CODE_INVALID, "command registry must be a dict", self.program.span))
                for key, value in self.commands.items():
                    if not isinstance(key, str) or not isinstance(value, (tuple, list)) or len(value) != 2:
                        return AnalyzeResult(None, Diagnostic(CODE_INVALID, f"bad registry entry for {key!r}", self.program.span))
                    params, ret = value
                    if (not isinstance(params, list) or any(p not in STUB_COMMAND_TYPES for p in params)
                            or (ret is not None and ret not in ("str", "int", "bool"))):
                        return AnalyzeResult(None, Diagnostic(CODE_INVALID, f"bad signature for command {key!r}", self.program.span))
            # first pass: collect functions and record declarations
            func_nodes = list(self.program.children)  # Program children are FunctionDef/RecordDecl
            function_params = []
            for fn in func_nodes:
                if fn.kind == "RecordDecl":
                    self._collect_record(fn)
                    continue
                if fn.kind == "UseStmt":
                    # Stage 19c import directive (resolved at load; the
                    # import table carries every file). Nothing collects.
                    continue
                # fn is FunctionDef ParseNode
                name = fn.text
                span = fn.span
                stored = self._stored(name)
                if stored in self.global_funcs or stored in self.record_decls:
                    self._error(C_DUPLICATE, f"duplicate function '{name}'", span,
                                expected="unique function", got=name, name=name, context="function")
                if name in RESERVED_FN_NAMES:
                    self._error(C_DUPLICATE, f"'{name}' is reserved", span,
                                expected="non-reserved function name", got=name, name=name, context="function")
                self.own[name] = stored
                # extract params and ret_type (raw type nodes; resolved to
                # canonical strings after record collection, so parametric
                # and record types validate uniformly at every position)
                params = []
                ret_type = None
                block = None
                for child in fn.children:
                    if child.kind == "ParamList":
                        for p in child.children:
                            # p is Param with children (Identifier, Type)
                            pname = p.text
                            tnode = p.children[1] if len(p.children) > 1 else p.children[0]
                            params.append((pname, tnode, p.span))
                    elif child.kind == "Type":
                        ret_type = child
                    elif child.kind == "Block":
                        block = child
                    elif child.kind == "Identifier":
                        continue
                # Parameter/global conflicts are checked only after the complete
                # global table exists, so source order cannot change semantics.
                seen = set()
                for pname, t, span in params:
                    if pname in seen:
                        self._error(C_DUPLICATE, f"duplicate parameter '{pname}'", span,
                                    expected="unique parameter", got=pname, name=pname, context="parameter")
                    if pname in ("break", "continue"):
                        self._error(C_DUPLICATE, f"'{pname}' is a reserved control word", span,
                                    expected="non-reserved name", got=pname, name=pname, context="parameter")
                    seen.add(pname)
                symbol = None  # assigned below: functions take 0..n-1 so the
                # frozen RIR symbol==index rule holds with records present
                self.global_funcs[stored] = {"params": params, "ret_type": ret_type, "span": span, "symbol": symbol, "node": fn, "block": block}
                function_params.append(params)
            for params in function_params:
                for pname, _ptype, pspan in params:
                    if self._top(pname) in self.global_funcs:
                        self._error(C_DUPLICATE, f"duplicate declaration '{pname}' shadows global function", pspan,
                                    expected="name distinct from functions", got=pname, name=pname, context="parameter")
            # Symbol assignment: functions take 0..n-1 in source order (the
            # frozen RIR symbol==index rule), records follow. Deterministic.
            for fn in func_nodes:
                if fn.kind == "FunctionDef":
                    info = self.global_funcs[self._top(fn.text)]
                    if info["symbol"] is None:
                        info["symbol"] = self.sym_counter
                        self.sym_counter += 1
            for fn in func_nodes:
                if fn.kind == "RecordDecl":
                    info = self.record_decls[self._top(fn.text)]
                    if info["symbol"] is None:
                        info["symbol"] = self.sym_counter
                        self.sym_counter += 1
            # Resolve record field types (all names known now) and compute
            # static sizes with occurs-check; bounds enforced here, once.
            self._resolve_record_fields()
            # Resolve every function signature to canonical types (source
            # order, so the first bad signature in the file reports first).
            self._resolve_signatures(func_nodes)
            # second pass: lower each function to stable AST
            stable_funcs = []
            stable_records = []
            for fn in func_nodes:
                if fn.kind == "RecordDecl":
                    stable_records.append(self._lower_record(fn))
                    continue
                if fn.kind == "UseStmt":
                    continue
                stable = self._lower_function(fn)
                stable_funcs.append(stable)
                if self.diagnostic:
                    break
            if self.diagnostic:
                return AnalyzeResult(None, self.diagnostic)
            # build Program stable
            prog_span = self._node_span(self.program)
            prog = {"kind": "Program", "span": prog_span, "functions": stable_funcs}
            if stable_records:
                # The key is absent (not empty) for v1 programs so frozen
                # v1 AST goldens stay byte-identical.
                prog["records"] = stable_records
            return AnalyzeResult(prog, None)
        except _AbortAnalysis:
            return AnalyzeResult(None, self.diagnostic)
        except RecursionError:
            span = self.program.span
            return AnalyzeResult(None, Diagnostic(CODE_DEPTH, "nesting depth exceeded", span))

    def _lower_function(self, fn: ParseNode) -> dict:
        self._enter()
        try:
            name = fn.text
            info = self.global_funcs[self._top(name)]
            ret_type = info["ret_type"]
            block = None
            for c in fn.children:
                if c.kind == "Block":
                    block = c
                    break
            scope_stack = []
            func_scope = {}
            for pname, ptype, pspan in info["params"]:
                sym = self.sym_counter
                self.sym_counter += 1
                func_scope[pname] = (sym, ptype, pspan)
            scope_stack.append(func_scope)
            stable_params = []
            for pname, ptype, pspan in info["params"]:
                sym, _, _ = func_scope[pname]
                stable_params.append({"kind": "Param", "span": self._span_dict(pspan), "name": pname, "type": ptype, "symbol": sym})
            stable_block = self._drive(self._lower_block(block, scope_stack, ret_type))
            prog_span = self._node_span(fn)
            return {"kind": "Function", "span": prog_span, "name": self._top(name), "params": stable_params, "ret_type": ret_type, "body": stable_block, "symbol": info["symbol"]}
        finally:
            self._leave()

    @staticmethod
    def _drive(work):
        """Evaluate lowering generators without using the Python call stack."""
        pending = [work]
        value = None
        try:
            while pending:
                try:
                    child = pending[-1].send(value)
                    value = None
                    if not isinstance(child, GeneratorType):
                        raise TypeError("invalid lowering work item")
                    pending.append(child)
                except StopIteration as completed:
                    pending.pop()
                    value = completed.value
            return value
        finally:
            for generator in reversed(pending):
                generator.close()

    def _lower_block(self, block: ParseNode, scope_stack: list, ret_type, predeclared: list | None = None) -> dict:
        self._enter()
        try:
            # new scope for this block
            scope_stack.append({})
            if predeclared:
                for pname, ptype, psym in predeclared:
                    scope_stack[-1][pname] = (psym, ptype, block.span)
            stmts = []
            for stmt in block.children:
                stable = yield self._lower_stmt(stmt, scope_stack, ret_type)
                if stable is not None:
                    stmts.append(stable)
                if self.diagnostic:
                    break
            # pop scope
            scope_stack.pop()
            return {"kind": "Block", "span": self._node_span(block), "stmts": stmts}
        finally:
            self._leave()

    def _lookup(self, name: str, scope_stack: list):
        for scope in reversed(scope_stack):
            if name in scope:
                return scope[name]
        return None

    def _is_comparable(self, typ: str) -> bool:
        # Stage 19a: equality needs identical value types. Scalars as
        # before; aggregates (records, validated parametric shapes) compare
        # by content. Unit is never comparable.
        if typ in ("int", "bool", "str"):
            return True
        node = _agtypes.parse_type(typ)
        if node is None:
            return False
        if node[0] == "nominal":
            return node[1] in self.record_decls
        return _agtypes.validate_type(node) is None

    def _collect_record(self, node: ParseNode):
        # children: [Identifier name, FieldDecl...]. One top-level
        # namespace shared with functions (redefine either way duplicates).
        name = node.text
        span = node.span
        stored = self._stored(name)
        if stored in self.global_funcs or stored in self.record_decls:
            self._error(C_DUPLICATE, f"duplicate declaration '{name}'", span,
                        expected="unique declaration", got=name, name=name, context="record")
        if name in RESERVED_TYPE_NAMES:
            self._error(C_DUPLICATE, f"'{name}' is a reserved type name", span,
                        expected="non-reserved record name", got=name, name=name, context="record")
        seen: set[str] = set()
        raw: list = []
        for child in node.children[1:]:
            fname = child.text
            if fname in seen:
                self._error(C_DUPLICATE, f"duplicate field '{fname}' in record '{name}'", child.span,
                            expected="unique field", got=fname, name=fname, context="record field")
            seen.add(fname)
            raw.append((fname, child.children[1], child.span))
        symbol = None  # assigned with the functions (records follow them)
        self.own[name] = stored
        self.record_decls[stored] = {"fields_raw": raw, "span": span, "symbol": symbol, "node": node, "bare": name}

    def _type_shape(self, tnode: ParseNode):
        # Parse-tree Type/Cap nodes -> agtypes shape (no validation yet).
        if tnode.kind == "Cap":
            return ("cap", int(tnode.text, 10))
        base = tnode.text
        kids = tnode.children
        if not kids:
            if base in _agtypes.SCALAR_TYPES:
                return ("scalar", base)
            return ("nominal", base)
        args = []
        for kid in kids:
            if kid.kind == "Cap":
                args.append(("cap", int(kid.text, 10)))
            else:
                args.append(self._type_shape(kid))
        return ("generic", base, tuple(args))

    def _resolve_type(self, tnode: ParseNode, check_size: bool = True) -> str:
        # Resolve a parse-tree type to its canonical string, enforcing all
        # shape rules and (unless check_size is False, used while record
        # sizes are still being computed) the static size bound.
        # Qualified `alias::Name` resolves through the import table;
        # bare nominals resolve through own-mangled names.
        raw = tnode.text or ""
        qualified = self._split_qualified(raw)
        if qualified is not None:
            alias, base = qualified
            stored = self._qualify(alias, base, tnode.span)
            if tnode.children:
                self._error(C_TYPE_MISMATCH, f"type '{raw}' takes no arguments", tnode.span,
                            expected="plain record name", got=raw, context="type")
            if stored not in self.record_decls:
                self._error(C_UNDECLARED, f"unknown type '{raw}'", tnode.span,
                            expected="declared record", got=raw, name=raw, context="type")
            return stored
        shape = self._type_shape(tnode)
        if shape is None:
            self._error(C_TYPE_MISMATCH, "malformed type", tnode.span,
                        expected="valid type", context="type")
        if shape[0] == "nominal":
            name = self._top(shape[1])
            if shape[1] in ("list", "map", "status", "result"):
                self._error(C_ARITY_MISMATCH, f"type '{shape[1]}' needs type arguments", tnode.span,
                            expected="type arguments", got=shape[1], context="type")
            if name not in self.record_decls:
                self._error(C_UNDECLARED, f"unknown type '{shape[1]}'", tnode.span,
                            expected="declared record or builtin type", got=shape[1], name=shape[1], context="type")
            return name
        err = _agtypes.validate_type(shape)
        if err == "unknown-base":
            self._error(C_TYPE_MISMATCH, f"type '{shape[1]}' takes no arguments", tnode.span,
                        expected="list, map, or status", got=shape[1], context="type")
        elif err == "arity":
            self._error(C_ARITY_MISMATCH, "wrong number of type arguments", tnode.span,
                        expected="matching arity", context="type")
        elif err == "bad-cap":
            self._error(C_LIMIT_EXCEEDED, "capacity must be at least 1", tnode.span,
                        expected="N >= 1", context="type")
        elif err == "bad-key":
            self._error(C_TYPE_MISMATCH, "map keys must be int, bool, or str", tnode.span,
                        expected="int, bool, or str key", context="type")
        elif err == "nested-status":
            self._error(C_TYPE_MISMATCH, "status cannot nest inside other types", tnode.span,
                        expected="non-status payload", context="type")
        elif err == "too-deep":
            self._error(C_LIMIT_EXCEEDED, "type nesting exceeds 8", tnode.span,
                        expected="nesting depth <= 8", context="type")
        elif err is not None:
            self._error(C_TYPE_MISMATCH, f"invalid type: {err}", tnode.span,
                        expected="valid type", context="type")
        shape = self._mangle_shape(shape, tnode.span)
        canon = _agtypes.canonical(shape)
        if self.profile == "core":
            self._check_core_shape(_agtypes.parse_type(canon), tnode.span)
        if check_size:
            self._check_type_bounded(canon, tnode.span)
        return canon

    def _check_core_shape(self, shape, span: Span) -> None:
        # Stage 19e core dialect: maps and results stay host-only
        # (the guest backend omits map probing and result layouts;
        # corpus expresses them via projection). Iterative over the
        # shape tree; caps are inert tuples.
        stack = [shape] if shape else []
        while stack:
            node = stack.pop()
            if not isinstance(node, tuple) or not node:
                continue
            if node[0] == "generic":
                if node[1] in ("map", "result"):
                    self._error(C_PROFILE_EXCLUDED, f"{node[1]} excluded by --profile=core", span,
                                expected="core type", got=node[1], context="profile gate")
                stack.extend(node[2])

    def _mangle_shape(self, node: tuple, span: Span):
        # Rewrite nominal leaves through the file's alias map (own bare
        # names mangle; qualified alias::Name resolves; already-mangled
        # foreign names pass through). Keeps nested positions (elements,
        # payloads) consistent: canonical strings are always stored-form.
        kind = node[0]
        if kind == "scalar":
            return node
        if kind == "nominal":
            name = node[1]
            qualified = self._split_qualified(name)
            if qualified is not None:
                alias, base = qualified
                return ("nominal", self._qualify(alias, base, span))
            return ("nominal", self._top(name))
        base, args = node[1], node[2]
        out = []
        for arg in args:
            if isinstance(arg, tuple) and arg and arg[0] == "cap":
                out.append(arg)
            else:
                out.append(self._mangle_shape(arg, span))
        return ("generic", base, tuple(out))

    def _check_type_bounded(self, canon: str, span: Span) -> None:
        node = _agtypes.parse_type(canon)
        bounded = _agtypes.check_bounded(node, self.record_sizes if self.record_sizes else None)
        if bounded == "too-big":
            self._error(C_LIMIT_EXCEEDED,
                        f"type {canon} exceeds { _agtypes.MAX_AGG_BYTES} bytes", span,
                        expected=f"at most {_agtypes.MAX_AGG_BYTES} bytes", got=canon, context="type")
        elif bounded == "unknown-record":
            # Only reachable while record sizes are still being computed
            # (first pass resolves names first, so uses below always know).
            self._error(C_UNDECLARED, f"unknown record in type {canon}", span,
                        expected="declared record", got=canon, context="type")

    def _nominal_refs(self, node: tuple, into: set) -> None:
        kind = node[0]
        if kind == "nominal":
            into.add(node[1])
        elif kind == "generic":
            for arg in node[2]:
                if isinstance(arg, tuple) and arg and arg[0] != "cap":
                    self._nominal_refs(arg, into)

    def _resolve_record_fields(self) -> None:
        # Resolve every field type string (all record names known), then
        # compute static sizes with occurs-check. Source order throughout.
        for name, info in self.record_decls.items():
            if "fields" in info:
                # Seeded (imported) records arrive pre-resolved; sizes are
                # still computed below.
                continue
            resolved = []
            for fname, ftnode, fspan in info["fields_raw"]:
                canon = self._resolve_type(ftnode, check_size=False)
                shape = _agtypes.parse_type(canon)
                if shape is not None and shape[0] == "generic" and shape[1] in ("status", "result"):
                    self._error(C_TYPE_MISMATCH, f"record field '{fname}' cannot be a status value", fspan,
                                expected="non-status field", got=canon, name=fname, context="record field")
                resolved.append((fname, canon, fspan))
            info["fields"] = resolved
        busy: set[str] = set()

        def rec_size(name: str, ref_span: Span) -> int:
            if name in self.record_sizes:
                return self.record_sizes[name]
            if name in busy:
                self._error(C_TYPE_MISMATCH, f"recursive record '{name}'", ref_span,
                            expected="non-recursive shape", got=name, name=name, context="record")
            busy.add(name)
            try:
                total = 0
                for _fname, fcanon, fspan in self.record_decls[name]["fields"]:
                    node = _agtypes.parse_type(fcanon)
                    refs: set[str] = set()
                    self._nominal_refs(node, refs)
                    for ref in sorted(refs):
                        if ref not in self.record_sizes:
                            rec_size(ref, fspan)
                    size = _agtypes.size_of(node, self.record_sizes)
                    total += size if size is not None else 0
                if total > _agtypes.MAX_AGG_BYTES:
                    self._error(C_LIMIT_EXCEEDED,
                                f"record '{name}' exceeds {_agtypes.MAX_AGG_BYTES} bytes", self.record_decls[name]["span"],
                                expected=f"at most {_agtypes.MAX_AGG_BYTES} bytes", got=name, name=name, context="record")
                self.record_sizes[name] = total
                return total
            finally:
                busy.discard(name)

        for name in self.record_decls:
            rec_size(name, self.record_decls[name]["span"])

    def _resolve_signatures(self, func_nodes: list) -> None:
        # Distinct names in first-seen order (a disabled duplicate check
        # must resolve each signature once, never re-resolve canonical
        # strings as parse nodes).
        seen: set[str] = set()
        for fn in func_nodes:
            if fn.kind != "FunctionDef" or fn.text in seen:
                continue
            seen.add(fn.text)
            info = self.global_funcs[self._top(fn.text)]
            canon_params = []
            for pname, tnode, pspan in info["params"]:
                ctype = self._resolve_type(tnode)
                canon_params.append((pname, ctype, pspan))
            info["params"] = canon_params
            if info["ret_type"] is not None:
                info["ret_type"] = self._resolve_type(info["ret_type"])

    def _lower_record(self, node: ParseNode) -> dict:
        info = self.record_decls[self._top(node.text)]
        fields = [{"name": fname, "type": fcanon} for fname, fcanon, _fspan in info["fields"]]
        return {"kind": "RecordDecl", "span": self._node_span(node), "name": self._top(node.text),
                "fields": fields, "symbol": info["symbol"]}

    def _declare(self, name: str, typ: str, span: Span, scope_stack: list):
        # check no shadowing anywhere in scope chain + global funcs
        if name in ("break", "continue"):
            self._error(C_DUPLICATE, f"'{name}' is a reserved control word", span,
                        expected="non-reserved name", got=name, name=name, context="let")
        if name in self.global_funcs or self._stored(name) in self.global_funcs:
            self._error(C_DUPLICATE, f"duplicate declaration '{name}' shadows function", span,
                        expected="name distinct from functions", got=name, name=name, context="let")
        for scope in scope_stack:
            if name in scope:
                self._error(C_DUPLICATE, f"duplicate declaration '{name}'", span,
                            expected="unique declaration", got=name, name=name, context="let")
        # also check current scope duplicate (already covered)
        sym = self.sym_counter
        self.sym_counter += 1
        scope_stack[-1][name] = (sym, typ, span)
        return sym

    def _lower_stmt(self, stmt: ParseNode, scope_stack: list, ret_type):
        # Depth mirrors the parser's per-construct accounting exactly: only
        # grammar constructs the parser itself charges (block, if) consume
        # budget here. Statements the parser accepts without charging (let,
        # return, expr-stmt, while) must not consume budget either, or the
        # analyzer would reject programs the frozen parser accepts.
        if stmt.kind == "IfStmt":
            self._enter()
        try:
            if stmt.kind == "LetStmt":
                # children: Identifier, Type, Expr
                name_node = stmt.children[0]
                type_node = stmt.children[1]
                expr_node = stmt.children[2]
                name = name_node.text
                typ = self._resolve_type(type_node)
                # lower init expr first in current scope (before declaration);
                # the declared type elaborates untyped literals in place.
                init, init_type = yield self._lower_expr(expr_node, scope_stack, False, typ)
                if init_type == "unit":
                    self._error(C_TYPE_MISMATCH, f"let initializer for '{name}' is unit", expr_node.span,
                                expected=typ, got="unit", name=name, context="let")
                if init_type != typ:
                    self._error(C_TYPE_MISMATCH, f"let '{name}' expects {typ} got {init_type}", expr_node.span,
                                expected=typ, got=init_type, name=name, context="let")
                sym = self._declare(name, typ, name_node.span, scope_stack)
                return {"kind": "Let", "span": self._node_span(stmt), "name": name, "type": typ, "init": init, "symbol": sym}
            elif stmt.kind == "ReturnStmt":
                # children maybe (Expr,)
                if len(stmt.children) == 0:
                    if ret_type is not None:
                        self._error(C_TYPE_MISMATCH, f"bare return in function returning {ret_type}", stmt.span,
                                    expected=ret_type, got="unit", context="return")
                    return {"kind": "Return", "span": self._node_span(stmt), "value": None}
                else:
                    expr_node = stmt.children[0]
                    init, init_type = yield self._lower_expr(expr_node, scope_stack, False, ret_type)
                    if ret_type is None:
                        self._error(C_TYPE_MISMATCH, f"return with value in unit function", expr_node.span,
                                    expected="unit", got=init_type, context="return")
                    if init_type != ret_type:
                        self._error(C_TYPE_MISMATCH, f"return expects {ret_type} got {init_type}", expr_node.span,
                                    expected=ret_type, got=init_type, context="return")
                    if init_type == "unit":
                        self._error(C_TYPE_MISMATCH, f"return value is unit", expr_node.span,
                                    expected=ret_type, got="unit", context="return")
                    return {"kind": "Return", "span": self._node_span(stmt), "value": init}
            elif stmt.kind == "IfStmt":
                # children: cond, then, else?
                cond_node = stmt.children[0]
                then_node = stmt.children[1]
                else_node = stmt.children[2] if len(stmt.children) > 2 else None
                cond, ctype = yield self._lower_expr(cond_node, scope_stack, False)
                if ctype != "bool":
                    self._error(C_TYPE_MISMATCH, f"if condition expects bool got {ctype}", cond_node.span,
                                expected="bool", got=ctype, context="if condition")
                then_block = yield self._lower_block(then_node, scope_stack, ret_type)
                else_block = None
                if else_node is not None:
                    if else_node.kind == "IfStmt":
                        else_block = yield self._lower_stmt(else_node, scope_stack, ret_type)  # else if
                    else:
                        else_block = yield self._lower_block(else_node, scope_stack, ret_type)
                return {"kind": "If", "span": self._node_span(stmt), "cond": cond, "then": then_block, "else": else_block}
            elif stmt.kind == "WhileStmt":
                cond_node = stmt.children[0]
                body_node = stmt.children[1]
                cond, ctype = yield self._lower_expr(cond_node, scope_stack, False)
                if ctype != "bool":
                    self._error(C_TYPE_MISMATCH, f"while condition expects bool got {ctype}", cond_node.span,
                                expected="bool", got=ctype, context="while condition")
                self._loop_depth += 1
                try:
                    body = yield self._lower_block(body_node, scope_stack, ret_type)
                finally:
                    self._loop_depth -= 1
                return {"kind": "While", "span": self._node_span(stmt), "cond": cond, "body": body}
            elif stmt.kind in ("BreakStmt", "ContinueStmt"):
                word = "break" if stmt.kind == "BreakStmt" else "continue"
                if self._loop_depth <= 0:
                    self._error(C_TYPE_MISMATCH, f"'{word}' outside loop", stmt.span,
                                expected="enclosing loop", got=word, context="loop jump")
                return {"kind": "Break" if word == "break" else "Continue", "span": self._node_span(stmt)}
            elif stmt.kind == "MatchStmt":
                return (yield self._lower_match(stmt, scope_stack, ret_type))
            elif stmt.kind == "ExprStmt":
                expr_node = stmt.children[0]
                # for ExprStmt, allow unit
                expr, etype = yield self._lower_expr(expr_node, scope_stack, True)
                # if expr is Call returning unit, ok; otherwise etype must not be unit (but only Call can be unit)
                return {"kind": "ExprStmt", "span": self._node_span(stmt), "expr": expr}
            elif stmt.kind == "UseStmt":
                # Stage 19c import directive: validated at load (the import
                # table carries every resolved file); lowers to nothing
                # (the caller drops None results).
                return None
            elif stmt.kind == "Block":
                return (yield self._lower_block(stmt, scope_stack, ret_type))
            else:
                self._error(C_TYPE_MISMATCH, f"unknown statement {stmt.kind}", stmt.span,
                            expected="supported statement", got=stmt.kind, context="lowering")
        finally:
            if stmt.kind == "IfStmt":
                self._leave()

    def _lower_expr(self, node: ParseNode, scope_stack: list, allow_unit: bool, expected: str | None = None):
        # Depth mirrors the parser's per-construct accounting exactly: only
        # grammar constructs the parser itself charges (unary, each call
        # argument group, grouping parens) consume budget here. Literals,
        # identifiers and binary operands do not: the parser builds them
        # iteratively without enter().
        # Stage 19a: `expected` carries the annotated type for literal
        # elaboration (let/call-arg/return sites). Non-literal expressions
        # ignore it (no implicit conversions, ever); the context compares
        # the computed type exactly as before.
        if node.kind == "UnaryExpr":
            self._enter()
            try:
                return (yield self._lower_unary(node, scope_stack, allow_unit))
            finally:
                self._leave()
        return (yield self._lower_primary_or_binary(node, scope_stack, allow_unit, expected))

    def _lower_unary(self, node: ParseNode, scope_stack: list, allow_unit: bool):
        if True:
            operand_node = node.children[0]
            op = node.text
            operand, otype = yield self._lower_expr(operand_node, scope_stack, False)
            if otype == "unit":
                self._error(C_TYPE_MISMATCH, f"unit as operand for '{op}'", operand_node.span,
                            expected="non-unit", got="unit", context="unary operand", operator=op)
            if op == "-":
                if otype != "int":
                    self._error(C_TYPE_MISMATCH, f"unary '-' expects int got {otype}", node.span,
                                expected="int", got=otype, context="unary operator", operator=op)
                result_type = "int"
            elif op == "!":
                if otype != "bool":
                    self._error(C_TYPE_MISMATCH, f"unary '!' expects bool got {otype}", node.span,
                                expected="bool", got=otype, context="unary operator", operator=op)
                result_type = "bool"
            elif op == "~":
                # Stage 19a: bitwise complement, two's-complement, total.
                if otype != "int":
                    self._error(C_TYPE_MISMATCH, f"unary '~' expects int got {otype}", node.span,
                                expected="int", got=otype, context="unary operator", operator=op)
                result_type = "int"
            else:
                self._error(C_TYPE_MISMATCH, f"unknown unary '{op}'", node.span,
                            expected=("-", "!", "~"), got=op, context="unary operator", operator=op)
                result_type = otype
            return ({"kind": "UnOp", "span": self._node_span(node), "op": op, "operand": operand, "type": result_type}, result_type)

    def _is_printable(self, typ: str) -> bool:
        # Every value type prints (Stage 19a widens Stage 16); unit never.
        if typ in ("int", "bool", "str"):
            return True
        return self._is_comparable(typ)

    def _const_key(self, node: ParseNode):
        # Constant map keys lower to comparable identities; anything else
        # is dynamic (runtime update-or-insert keeps maps duplicate-free).
        if node.kind == "IntegerLiteral":
            return ("int", node.text)
        if node.kind == "BooleanLiteral":
            return ("bool", node.text)
        if node.kind == "StringLiteral":
            return ("str", node.value if node.value is not None else node.text[1:-1])
        return None

    def _check_dup_keys(self, kids: list) -> None:
        seen: set = set()
        for kid in kids:
            identity = self._const_key(kid.children[0])
            if identity is None:
                continue
            if identity in seen:
                self._error(C_DUPLICATE, "duplicate map key", kid.children[0].span,
                            expected="unique key", got=identity[1], context="map literal")
            seen.add(identity)

    def _lower_list_lit(self, node: ParseNode, scope_stack: list, expected: str | None):
        # Parser charges one level per element group; lowering matches it.
        self._enter()
        try:
            kids = list(node.children)
            if expected is not None:
                shape = _agtypes.parse_type(expected)
                if shape is None or shape[0] != "generic" or shape[1] != "list":
                    self._error(C_TYPE_MISMATCH, "list literal needs a list type", node.span,
                                expected="list<T,N>", got=expected, context="list literal")
                elem_t = _agtypes.canonical(shape[2][0])
                cap = shape[2][1][1]
                if len(kids) > cap:
                    self._error(C_LIMIT_EXCEEDED,
                                f"list literal of {len(kids)} exceeds capacity {cap}", node.span,
                                expected=f"at most {cap} elements", got=f"{len(kids)} elements",
                                context="list literal")
                elems = []
                for kid in kids:
                    stable, etype = yield self._lower_expr(kid, scope_stack, False, elem_t)
                    if etype != elem_t:
                        self._error(C_TYPE_MISMATCH, f"list element expects {elem_t} got {etype}", kid.span,
                                    expected=elem_t, got=etype, context="list element")
                    elems.append(stable)
                return ({"kind": "ListLit", "span": self._node_span(node), "elems": elems, "type": expected}, expected)
            if not kids:
                self._error(C_TYPE_MISMATCH, "empty list needs an annotated type", node.span,
                            expected="annotated list<T,N>", context="list literal")
            first, first_t = yield self._lower_expr(kids[0], scope_stack, False)
            if first_t == "unit":
                self._error(C_TYPE_MISMATCH, "unit as list element", node.children[0].span,
                            expected="non-unit", got="unit", context="list element")
            elems = [first]
            for kid in kids[1:]:
                stable, etype = yield self._lower_expr(kid, scope_stack, False)
                if etype != first_t:
                    self._error(C_TYPE_MISMATCH, f"list element expects {first_t} got {etype}", kid.span,
                                expected=first_t, got=etype, context="list element")
                elems.append(stable)
            computed = f"list<{first_t},{len(elems)}>"
            self._check_type_bounded(computed, node.span)
            return ({"kind": "ListLit", "span": self._node_span(node), "elems": elems, "type": computed}, computed)
        finally:
            self._leave()

    def _lower_map_lit(self, node: ParseNode, scope_stack: list, expected: str | None):
        self._check_dup_keys(list(node.children))
        self._enter()
        try:
            kids = list(node.children)
            if expected is not None:
                shape = _agtypes.parse_type(expected)
                if shape is None or shape[0] != "generic" or shape[1] != "map":
                    self._error(C_TYPE_MISMATCH, "map literal needs a map type", node.span,
                                expected="map<K,V,N>", got=expected, context="map literal")
                key_t = _agtypes.canonical(shape[2][0])
                val_t = _agtypes.canonical(shape[2][1])
                cap = shape[2][2][1]
                if len(kids) > cap:
                    self._error(C_LIMIT_EXCEEDED,
                                f"map literal of {len(kids)} exceeds capacity {cap}", node.span,
                                expected=f"at most {cap} entries", got=f"{len(kids)} entries",
                                context="map literal")
                entries = []
                for kid in kids:
                    kstable, ktype = yield self._lower_expr(kid.children[0], scope_stack, False, key_t)
                    if ktype != key_t:
                        self._error(C_TYPE_MISMATCH, f"map key expects {key_t} got {ktype}", kid.children[0].span,
                                    expected=key_t, got=ktype, context="map key")
                    vstable, vtype = yield self._lower_expr(kid.children[1], scope_stack, False, val_t)
                    if vtype != val_t:
                        self._error(C_TYPE_MISMATCH, f"map value expects {val_t} got {vtype}", kid.children[1].span,
                                    expected=val_t, got=vtype, context="map value")
                    entries.append({"key": kstable, "value": vstable})
                return ({"kind": "MapLit", "span": self._node_span(node), "entries": entries, "type": expected}, expected)
            if not kids:
                self._error(C_TYPE_MISMATCH, "empty map needs an annotated type", node.span,
                            expected="annotated map<K,V,N>", context="map literal")
            kfirst, key_t = yield self._lower_expr(kids[0].children[0], scope_stack, False)
            if key_t not in ("int", "bool", "str"):
                self._error(C_TYPE_MISMATCH, f"map keys must be int, bool, or str got {key_t}", kids[0].children[0].span,
                            expected="int, bool, or str key", got=key_t, context="map key")
            _vfirst, val_t = yield self._lower_expr(kids[0].children[1], scope_stack, False)
            entries = [{"key": kfirst, "value": _vfirst}]
            for kid in kids[1:]:
                kstable, ktype = yield self._lower_expr(kid.children[0], scope_stack, False)
                if ktype != key_t:
                    self._error(C_TYPE_MISMATCH, f"map key expects {key_t} got {ktype}", kid.children[0].span,
                                expected=key_t, got=ktype, context="map key")
                vstable, vtype = yield self._lower_expr(kid.children[1], scope_stack, False)
                if vtype != val_t:
                    self._error(C_TYPE_MISMATCH, f"map value expects {val_t} got {vtype}", kid.children[1].span,
                                expected=val_t, got=vtype, context="map value")
                entries.append({"key": kstable, "value": vstable})
            computed = f"map<{key_t},{val_t},{len(entries)}>"
            self._check_type_bounded(computed, node.span)
            return ({"kind": "MapLit", "span": self._node_span(node), "entries": entries, "type": computed}, computed)
        finally:
            self._leave()

    def _lower_index(self, node: ParseNode, scope_stack: list):
        obj_node, idx_node = node.children[0], node.children[1]
        obj, otype = yield self._lower_expr(obj_node, scope_stack, False)
        shape = _agtypes.parse_type(otype)
        if shape is None or shape[0] != "generic" or shape[1] != "list":
            self._error(C_TYPE_MISMATCH, f"cannot index {otype}", obj_node.span,
                        expected="list<T,N>", got=otype, context="index")
        elem_t = _agtypes.canonical(shape[2][0])
        index, itype = yield self._lower_expr(idx_node, scope_stack, False)
        if itype != "int":
            self._error(C_TYPE_MISMATCH, f"index expects int got {itype}", idx_node.span,
                        expected="int", got=itype, context="index")
        result = f"status<{elem_t}>"
        return ({"kind": "Index", "span": self._node_span(node), "obj": obj, "index": index, "type": result}, result)

    def _lower_field(self, node: ParseNode, scope_stack: list):
        obj_node, name_node = node.children[0], node.children[1]
        obj, otype = yield self._lower_expr(obj_node, scope_stack, False)
        if otype not in self.record_decls:
            self._error(C_TYPE_MISMATCH, f"cannot select field of {otype}", obj_node.span,
                        expected="record value", got=otype, context="field access")
        want = name_node.text
        for fname, ftype, _fspan in self.record_decls[otype]["fields"]:
            if fname == want:
                return ({"kind": "Field", "span": self._node_span(node), "obj": obj, "field": want, "type": ftype}, ftype)
        self._error(C_UNDECLARED, f"record '{otype}' has no field '{want}'", name_node.span,
                    expected="declared field", got=want, name=want, context="field access")

    def _lower_reclit(self, node: ParseNode, scope_stack: list):
        # Parser RecLit: (callee Identifier, FieldInit...). One depth
        # charge for the field group (call-arg-group parity).
        self._enter()
        try:
            callee_name = node.children[0].text
            pairs = [(kid.children[0].text, kid.children[1], kid.children[0].span) for kid in node.children[1:]]
            return (yield self._construct_record(callee_name, pairs, node, scope_stack))
        finally:
            self._leave()

    def _lower_reclit_call(self, callee_name: str, pairs: list, node: ParseNode, scope_stack: list):
        # Empty `Point()` through the call shape (the call group already
        # charged depth). Fieldless record required here.
        return (yield self._construct_record(callee_name, pairs, node, scope_stack))

    def _construct_record(self, callee_name: str, pairs: list, node: ParseNode, scope_stack: list):
        qualified = self._split_qualified(callee_name)
        if qualified is not None:
            alias, base = qualified
            callee_name = self._qualify(alias, base, node.span)
        else:
            callee_name = self._top(callee_name)
        if callee_name in self.global_funcs:
            self._error(C_TYPE_MISMATCH, f"'{callee_name}' is a function, not a record", node.span,
                        expected="record construction", got=callee_name, callee=callee_name, context="record literal")
        if callee_name not in self.record_decls:
            self._error(C_UNDECLARED, f"unknown record '{callee_name}'", node.span,
                        expected="declared record", got=callee_name, name=callee_name, context="record literal")
        decl = self.record_decls[callee_name]
        want = [(fname, ftype) for fname, ftype, _fspan in decl["fields"]]
        seen: dict[str, object] = {}
        for fname, _expr, fspan in pairs:
            if fname in seen:
                self._error(C_DUPLICATE, f"duplicate field '{fname}'", fspan,
                            expected="unique field", got=fname, name=fname, context="record literal")
            if fname not in [f for f, _t in want]:
                self._error(C_UNDECLARED, f"record '{callee_name}' has no field '{fname}'", fspan,
                            expected="declared field", got=fname, name=fname, context="record literal")
            seen[fname] = (_expr, fspan)
        if len(seen) != len(want):
            missing = [f for f, _t in want if f not in seen]
            self._error(C_ARITY_MISMATCH, f"record '{callee_name}' missing fields {missing}", node.span,
                        expected=len(want), got=len(seen), callee=callee_name, context="record literal")
        fields = []
        for fname, ftype in want:
            expr_node, _fspan = seen[fname]
            stable, vtype = yield self._lower_expr(expr_node, scope_stack, False, ftype)
            if vtype != ftype:
                self._error(C_TYPE_MISMATCH, f"field '{fname}' expects {ftype} got {vtype}", expr_node.span,
                            expected=ftype, got=vtype, context="record field")
            fields.append({"name": fname, "value": stable})
        return ({"kind": "RecLit", "span": self._node_span(node), "record": callee_name, "fields": fields, "type": callee_name}, callee_name)

    def _lower_builtin(self, callee_name: str, arg_nodes: list, node: ParseNode, scope_stack: list, expected: str | None = None):
        def operand(index: int, expected: str | None = None):
            if index >= len(arg_nodes):
                self._error(C_ARITY_MISMATCH, f"arity mismatch for '{callee_name}'", node.span,
                            expected="matching arity", got=len(arg_nodes), callee=callee_name, context="call")
            stable, atype = yield self._lower_expr(arg_nodes[index], scope_stack, False, expected)
            if atype == "unit":
                self._error(C_TYPE_MISMATCH, f"unit as argument for '{callee_name}'", arg_nodes[index].span,
                            expected="non-unit", got="unit", callee=callee_name, context="call argument")
            return stable, atype

        def call_node(args: list, result: str):
            return ({"kind": "Call", "span": self._node_span(node), "callee": callee_name,
                     "args": args, "symbol": -1, "type": result}, result)

        def expect_arity(count: int) -> None:
            if len(arg_nodes) != count:
                self._error(C_ARITY_MISMATCH, f"arity mismatch for '{callee_name}' expected {count} got {len(arg_nodes)}", node.span,
                            expected=count, got=len(arg_nodes), callee=callee_name, context="call")

        if callee_name == "len":
            expect_arity(1)
            arg, atype = yield operand(0)
            shape = _agtypes.parse_type(atype)
            ok = atype == "str" or (shape is not None and shape[0] == "generic" and shape[1] in ("list", "map"))
            if not ok:
                self._error(C_TYPE_MISMATCH, f"len expects list, map, or str got {atype}", arg_nodes[0].span,
                            expected="list, map, or str", got=atype, callee=callee_name, context="call argument")
            return call_node([arg], "int")
        if callee_name in ("is_ok", "is_err"):
            expect_arity(1)
            arg, atype = yield operand(0)
            shape = _agtypes.parse_type(atype)
            if shape is None or shape[0] != "generic" or shape[1] != "status":
                self._error(C_TYPE_MISMATCH, f"'{callee_name}' expects status got {atype}", arg_nodes[0].span,
                            expected="status<T>", got=atype, callee=callee_name, context="call argument")
            return call_node([arg], "bool")
        if callee_name == "unwrap_or":
            expect_arity(2)
            arg, atype = yield operand(0)
            shape = _agtypes.parse_type(atype)
            if shape is None or shape[0] != "generic" or shape[1] != "status":
                self._error(C_TYPE_MISMATCH, f"'unwrap_or' expects status got {atype}", arg_nodes[0].span,
                            expected="status<T>", got=atype, callee=callee_name, context="call argument")
            payload_t = _agtypes.canonical(shape[2][0])
            default, dtype = yield operand(1, payload_t)
            if dtype != payload_t:
                self._error(C_TYPE_MISMATCH, f"'unwrap_or' default expects {payload_t} got {dtype}", arg_nodes[1].span,
                            expected=payload_t, got=dtype, callee=callee_name, context="call argument")
            return call_node([arg, default], payload_t)
        if callee_name == "push":
            expect_arity(2)
            arg, atype = yield operand(0)
            shape = _agtypes.parse_type(atype)
            if shape is None or shape[0] != "generic" or shape[1] != "list":
                self._error(C_TYPE_MISMATCH, f"'push' expects list got {atype}", arg_nodes[0].span,
                            expected="list<T,N>", got=atype, callee=callee_name, context="call argument")
            elem_t = _agtypes.canonical(shape[2][0])
            val, vtype = yield operand(1, elem_t)
            if vtype != elem_t:
                self._error(C_TYPE_MISMATCH, f"'push' value expects {elem_t} got {vtype}", arg_nodes[1].span,
                            expected=elem_t, got=vtype, callee=callee_name, context="call argument")
            return call_node([arg, val], f"status<{atype}>")
        if callee_name == "insert":
            if self.profile == "core":
                self._error(C_PROFILE_EXCLUDED, "'insert' excluded by --profile=core", node.span,
                            expected="core builtin", got=callee_name, context="profile gate")
            expect_arity(3)
            arg, atype = yield operand(0)
            shape = _agtypes.parse_type(atype)
            if shape is None or shape[0] != "generic" or shape[1] != "map":
                self._error(C_TYPE_MISMATCH, f"'insert' expects map got {atype}", arg_nodes[0].span,
                            expected="map<K,V,N>", got=atype, callee=callee_name, context="call argument")
            key_t = _agtypes.canonical(shape[2][0])
            val_t = _agtypes.canonical(shape[2][1])
            key, ktype = yield operand(1, key_t)
            if ktype != key_t:
                self._error(C_TYPE_MISMATCH, f"'insert' key expects {key_t} got {ktype}", arg_nodes[1].span,
                            expected=key_t, got=ktype, callee=callee_name, context="call argument")
            val, vtype = yield operand(2, val_t)
            if vtype != val_t:
                self._error(C_TYPE_MISMATCH, f"'insert' value expects {val_t} got {vtype}", arg_nodes[2].span,
                            expected=val_t, got=vtype, callee=callee_name, context="call argument")
            return call_node([arg, key, val], f"status<{atype}>")
        if callee_name == "get":
            if self.profile == "core":
                self._error(C_PROFILE_EXCLUDED, "'get' excluded by --profile=core", node.span,
                            expected="core builtin", got=callee_name, context="profile gate")
            expect_arity(2)
            arg, atype = yield operand(0)
            shape = _agtypes.parse_type(atype)
            if shape is None or shape[0] != "generic" or shape[1] != "map":
                self._error(C_TYPE_MISMATCH, f"'get' expects map got {atype}", arg_nodes[0].span,
                            expected="map<K,V,N>", got=atype, callee=callee_name, context="call argument")
            key_t = _agtypes.canonical(shape[2][0])
            val_t = _agtypes.canonical(shape[2][1])
            key, ktype = yield operand(1, key_t)
            if ktype != key_t:
                self._error(C_TYPE_MISMATCH, f"'get' key expects {key_t} got {ktype}", arg_nodes[1].span,
                            expected=key_t, got=ktype, callee=callee_name, context="call argument")
            return call_node([arg, key], f"status<{val_t}>")
        if callee_name == "byte_at":
            expect_arity(2)
            arg, atype = yield operand(0)
            if atype != "str":
                self._error(C_TYPE_MISMATCH, f"'byte_at' expects str got {atype}", arg_nodes[0].span,
                            expected="str", got=atype, callee=callee_name, context="call argument")
            index, itype = yield operand(1)
            if itype != "int":
                self._error(C_TYPE_MISMATCH, f"'byte_at' index expects int got {itype}", arg_nodes[1].span,
                            expected="int", got=itype, callee=callee_name, context="call argument")
            return call_node([arg, index], "status<int>")
        if callee_name == "fread":
            # Stage 19e file input (self-host compiler source loading).
            expect_arity(3)
            path, ptype = yield operand(0)
            if ptype != "str":
                self._error(C_TYPE_MISMATCH, f"'fread' path expects str got {ptype}", arg_nodes[0].span,
                            expected="str", got=ptype, callee=callee_name, context="call argument")
            offset, otype = yield operand(1)
            if otype != "int":
                self._error(C_TYPE_MISMATCH, f"'fread' offset expects int got {otype}", arg_nodes[1].span,
                            expected="int", got=otype, callee=callee_name, context="call argument")
            length, ltype = yield operand(2)
            if ltype != "int":
                self._error(C_TYPE_MISMATCH, f"'fread' length expects int got {ltype}", arg_nodes[2].span,
                            expected="int", got=ltype, callee=callee_name, context="call argument")
            return call_node([path, offset, length], "status<str>")
        if callee_name == "fjoin":
            # Stage 19e path join (guest module resolution without
            # string synthesis, which the core dialect omits).
            expect_arity(2)
            directory, dtype = yield operand(0)
            if dtype != "str":
                self._error(C_TYPE_MISMATCH, f"'fjoin' dir expects str got {dtype}", arg_nodes[0].span,
                            expected="str", got=dtype, callee=callee_name, context="call argument")
            rel, rtype = yield operand(1)
            if rtype != "str":
                self._error(C_TYPE_MISMATCH, f"'fjoin' rel expects str got {rtype}", arg_nodes[1].span,
                            expected="str", got=rtype, callee=callee_name, context="call argument")
            return call_node([directory, rel], "status<str>")
        if callee_name == "ok" or callee_name == "err":
            if self.profile == "core":
                self._error(C_PROFILE_EXCLUDED, f"'{callee_name}' excluded by --profile=core", node.span,
                            expected="core builtin", got=callee_name, context="profile gate")
            # Stage 19b result constructors: the expected result type
            # elaborates the payload (no inference anywhere in the
            # language, so an unannotated ok()/err() is an error).
            expect_arity(1)
            shape = _agtypes.parse_type(expected) if expected is not None else None
            if shape is None or shape[0] != "generic" or shape[1] != "result":
                self._error(C_TYPE_MISMATCH, f"'{callee_name}' needs an annotated result type", node.span,
                            expected="result<T,E>", got=expected, callee=callee_name, context="call")
            payload_t = _agtypes.canonical(shape[2][0 if callee_name == "ok" else 1])
            val, vtype = yield operand(0, payload_t)
            if vtype != payload_t:
                self._error(C_TYPE_MISMATCH, f"'{callee_name}' payload expects {payload_t} got {vtype}", arg_nodes[0].span,
                            expected=payload_t, got=vtype, callee=callee_name, context="call argument")
            return call_node([val], expected)
        self._error(C_UNKNOWN_FUNCTION, f"unknown builtin '{callee_name}'", node.span,
                    expected="known builtin", got=callee_name, callee=callee_name, context="call")

    def _lower_match(self, stmt: ParseNode, scope_stack: list, ret_type):
        # children: (scrutinee, MatchArm...). Arms are blocks; bindings are
        # narrowed into each arm's scope. Exhaustiveness is enforced by
        # scrutinee type (status/result need ok+err or _; bool needs
        # true+false or _; int/str need _).
        scrut_node = stmt.children[0]
        scrut, stype = yield self._lower_expr(scrut_node, scope_stack, False)
        shape = _agtypes.parse_type(stype)
        scalar = stype in ("int", "bool", "str")
        is_status = shape is not None and shape[0] == "generic" and shape[1] == "status"
        is_result = shape is not None and shape[0] == "generic" and shape[1] == "result"
        if not (scalar or is_status or is_result):
            self._error(C_TYPE_MISMATCH, f"cannot match on {stype}", scrut_node.span,
                        expected="status, result, int, bool, or str", got=stype, context="match")
        if is_result and self.profile == "core":
            self._error(C_PROFILE_EXCLUDED, "match on result excluded by --profile=core", scrut_node.span,
                        expected="core scrutinee", got=stype, context="profile gate")
        if is_status:
            payload_t = _agtypes.canonical(shape[2][0])
            ok_t, err_t = payload_t, "int"
        elif is_result:
            ok_t = _agtypes.canonical(shape[2][0])
            err_t = _agtypes.canonical(shape[2][1])
        else:
            ok_t = err_t = stype
        arms = []
        covered_ok = covered_err = covered_wild = False
        covered_lits: set = set()
        for arm_node in stmt.children[1:]:
            pat_node, body_node = arm_node.children[0], arm_node.children[1]
            if covered_wild:
                self._error(C_TYPE_MISMATCH, "unreachable match arm", arm_node.span,
                            expected="reachable pattern", context="match arm")
            bindings, kind = self._lower_pattern(pat_node, stype, ok_t, err_t, scope_stack)
            if kind == "wild" or kind == "bind":
                covered_wild = True
            elif kind in ("ok", "err"):
                if (kind == "ok" and covered_ok) or (kind == "err" and covered_err):
                    self._error(C_DUPLICATE, f"duplicate '{kind}' arm", arm_node.span,
                                expected="unique variant arm", got=kind, context="match arm")
                if kind == "ok":
                    covered_ok = True
                else:
                    covered_err = True
            elif kind == "lit":
                key = self._pattern_key(pat_node, stype)
                if key in covered_lits:
                    self._error(C_DUPLICATE, "duplicate match literal", arm_node.span,
                                expected="unique literal", context="match arm")
                covered_lits.add(key)
            for bname, _btype, _bsym in bindings:
                if self._lookup(bname, scope_stack) is not None or bname in self.global_funcs or bname in RESERVED_FN_NAMES:
                    self._error(C_DUPLICATE, f"duplicate declaration '{bname}'", pat_node.span,
                                expected="unique binding", got=bname, name=bname, context="match binding")
            bound = []
            sym_of = {}
            for bname, btype, _ignore in bindings:
                sym = self.sym_counter
                self.sym_counter += 1
                bound.append((bname, btype, sym))
                sym_of[bname] = sym
            body = yield self._lower_block(body_node, scope_stack, ret_type, bound)
            arms.append({"pattern": self._pattern_json(pat_node, kind, sym_of), "body": body})
        if is_status or is_result:
            covered = (covered_ok and covered_err) or covered_wild
        elif stype == "bool":
            covered = covered_wild or (("lit", "true") in covered_lits and ("lit", "false") in covered_lits)
        else:
            covered = covered_wild
        if not covered:
            self._error(C_TYPE_MISMATCH, "non-exhaustive match", stmt.span,
                        expected="covering arms", got=stype, context="match")
        return {"kind": "Match", "span": self._node_span(stmt), "scrut": scrut, "arms": arms}

    def _pattern_key(self, pat_node: ParseNode, stype: str):
        if pat_node.kind == "LitPat":
            return ("lit", pat_node.text)
        return ("other", pat_node.kind, pat_node.text)

    def _pattern_json(self, pat_node: ParseNode, kind: str, sym_of: dict) -> dict:
        base = {"kind": {"wild": "WildPat", "bind": "BindPat", "lit": "LitPat",
                         "ok": "OkPat", "err": "ErrPat"}[kind],
                "span": self._node_span(pat_node)}
        if kind == "bind":
            base["name"] = pat_node.text
            base["symbol"] = sym_of[pat_node.text]
        elif kind == "lit":
            # Decoded value for strings (quotes + escapes resolved like
            # StringLiteral); raw lexemes otherwise.
            text = pat_node.text or ""
            if len(text) >= 2 and text[0] == '"' and isinstance(pat_node.value, str):
                base["value"] = pat_node.value
            else:
                base["value"] = text
        elif kind in ("ok", "err"):
            sub = pat_node.children[0]
            if sub.kind == "BindPat":
                base["binding"] = sub.text
                base["symbol"] = sym_of[sub.text]
            elif sub.kind == "LitPat":
                base["literal"] = sub.text
        return base

    def _lit_pat_type(self, node: ParseNode) -> str | None:
        # LitPat carries the raw lexeme; the lexer invariants make the
        # type decidable: true/false, quoted strings, digit runs.
        text = node.text or ""
        if text == "true" or text == "false":
            return "bool"
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
            return "str"
        if text and all(ch.isdigit() for ch in text):
            return "int"
        return None

    def _lower_pattern(self, pat_node: ParseNode, stype: str, ok_t: str, err_t: str, scope_stack: list):
        # Returns (bindings, kind) where kind in wild/bind/lit/ok/err.
        # Bindings are (name, type, span-or-None) validated for shadowing
        # by the caller, which mints symbols.
        kind = pat_node.kind
        if kind == "WildPat":
            return ([], "wild")
        if kind == "Identifier":
            # Bare-word pattern from hand-built trees (parser emits BindPat).
            return ([(pat_node.text, stype, None)], "bind")
        if kind == "BindPat":
            return ([(pat_node.text, stype, None)], "bind")
        if kind == "LitPat":
            want = self._lit_pat_type(pat_node)
            if want is None or want != stype:
                self._error(C_TYPE_MISMATCH, "match literal mistyped", pat_node.span,
                            expected=stype, got=want, context="match pattern")
            return ([], "lit")
        if kind == "CtorPat":
            which = pat_node.text
            sub = pat_node.children[0]
            shape = _agtypes.parse_type(stype)
            if shape is None or shape[0] != "generic" or shape[1] not in ("status", "result"):
                self._error(C_TYPE_MISMATCH, f"'{which}' pattern needs a status or result", pat_node.span,
                            expected="status or result", got=stype, context="match pattern")
            payload_t = ok_t if which == "ok" else err_t
            if sub.kind == "WildPat":
                return ([], which)
            if sub.kind == "BindPat":
                return ([(sub.text, payload_t, None)], which)
            # Literal payload: must match the payload type exactly.
            want = self._lit_pat_type(sub)
            if want is None or want != payload_t:
                self._error(C_TYPE_MISMATCH, f"'{which}' payload mistyped", sub.span,
                            expected=payload_t, got=want, context="match pattern")
            return ([], which)
        self._error(C_TYPE_MISMATCH, f"unknown pattern {kind}", pat_node.span,
                    expected="supported pattern", got=kind, context="match pattern")

    def _lower_pipeline(self, node: ParseNode, scope_stack: list, allow_unit: bool):
        # MVP: every non-final stage is str; a unit final stage makes a unit
        # pipeline, legal only as an ExprStmt (checked by the Stmt context via
        # the returned unit type, mirroring the frozen Call rule). Like the
        # parser's iterative loop, lowering charges no depth per stage.
        stages = []
        stage_types = []
        for index, stage_node in enumerate(node.children):
            stage, stype = yield self._lower_stage(stage_node, scope_stack, piped=index > 0)
            stages.append(stage)
            stage_types.append(stype)
        for index, stype in enumerate(stage_types[:-1]):
            if stype == "unit":
                self._error(S_UNIT_STAGE, f"pipeline stage {index} is unit; only the final stage may be unit", node.children[index].span,
                            expected="non-unit str stage", got="unit", context=f"pipeline stage {index}")
            if stype != "str":
                self._error(S_PIPELINE_TYPE, f"pipeline stage {index} expects str got {stype}", node.children[index].span,
                            expected="str", got=stype, context=f"pipeline stage {index}")
        final_type = stage_types[-1]
        if final_type == "unit":
            ptype = "unit"
        elif final_type != "str":
            self._error(S_PIPELINE_TYPE, f"pipeline result expects str got {final_type}", node.children[-1].span,
                        expected="str", got=final_type, context="pipeline result")
            ptype = final_type
        else:
            ptype = "str"
        void = allow_unit  # unit-ness is reported via the type, as for Call.
        return ({"kind": "Pipeline", "span": self._node_span(node), "stages": stages, "type": ptype}, ptype)

    def _lower_stage(self, node: ParseNode, scope_stack: list, piped: bool = False):
        # One pipeline stage: a command (which observes the piped input) or
        # any ordinary expression (evaluated independently; implicit flow
        # applies to commands only). Stages observe actual types
        # (allow_unit) so the pipeline rule reports the precise stage span
        # instead of a generic context error.
        if node.kind == "CmdExpr":
            return (yield self._lower_cmd(node, scope_stack, True, piped))
        return (yield self._lower_expr(node, scope_stack, True))

    def _lower_cmd(self, node: ParseNode, scope_stack: list, allow_unit: bool, piped: bool = False):
        # The parser charges one enter() per CmdExpr; lowering matches it so
        # depth-boundary parity holds in both editions.
        self._enter()
        try:
            name = node.text
            argv_nodes = []
            redirect_nodes = []
            for child in node.children[1:]:
                if child.kind == "CmdArgs":
                    argv_nodes.extend(child.children)
                elif child.kind == "Redirect":
                    redirect_nodes.append(child)
                else:
                    self._error(S_REDIRECT, f"unexpected command child {child.kind}", child.span,
                                expected="argument or redirect", got=child.kind, context="command")
            bare = not argv_nodes and not redirect_nodes
            entry = self.commands.get(name) if isinstance(self.commands, dict) else None
            if bare:
                # Lone word: lexical variables keep their v1 meaning first,
                # so `let ls: str = ...; ls |> count` uses the variable even
                # when a stub command shares the name. (No-shadowing keeps
                # this disjoint from the function/command ambiguity below.)
                found = self._lookup(name, scope_stack)
                if found is not None:
                    sym, typ, _ = found
                    return ({"kind": "Var", "span": self._node_span(node), "name": name, "symbol": sym, "type": typ}, typ)
            if name in self.global_funcs and entry is not None:
                self._error(S_AMBIGUOUS_COMMAND, f"'{name}' names both a function and a command", node.span,
                            expected="unambiguous command", got=name, name=name, context="command")
            if entry is None:
                if name in self.global_funcs:
                    self._error(S_UNKNOWN_COMMAND, f"'{name}' is a function, not a command (use {name}(...))", node.span,
                                expected="known command", got=name, name=name, context="command")
                self._error(S_UNKNOWN_COMMAND, f"unknown command '{name}'", node.span,
                            expected="known command", got=name, name=name, context="command")
            want_params, want_ret = entry
            # Piped input fills the command's first parameter when this Cmd
            # is a non-head stage (MVP implicit flow, commands only). The
            # head stage satisfies its signature from explicit args alone.
            # Piping into a zero-parameter command is an arity error: the
            # input would have nowhere typed to go.
            if piped:
                got_types: list = ["str"]
                span_offset = 1
            else:
                got_types = []
                span_offset = 0
            lowered_argv = []
            for arg in argv_nodes:
                if arg.kind == "FlagArg":
                    lowered_argv.append({"kind": "Flag", "span": self._node_span(arg), "name": arg.text, "type": "flag"})
                    got_types.append("flag")
                elif arg.kind == "UnaryExpr":
                    stable, atype = yield self._lower_expr(arg, scope_stack, False)
                    lowered_argv.append(stable)
                    got_types.append(atype)
                else:
                    stable, atype = yield self._lower_expr(arg, scope_stack, False)
                    if atype == "unit":
                        self._error(C_TYPE_MISMATCH, f"unit as command argument", arg.span,
                                    expected="non-unit", got="unit", name=name, context="command argument")
                    lowered_argv.append(stable)
                    got_types.append(atype)
            lowered_redirects = []
            for redir in redirect_nodes:
                target_node = redir.children[0]
                target, ttype = yield self._lower_expr(target_node, scope_stack, False)
                if ttype != "str":
                    self._error(S_REDIRECT, f"redirect target expects str got {ttype}", target_node.span,
                                expected="str", got=ttype, context="redirect")
                lowered_redirects.append({"kind": "Redirect", "span": self._node_span(redir),
                                          "op": redir.text, "target": target, "type": "str"})
            if len(got_types) != len(want_params):
                self._error(S_COMMAND_ARITY, f"arity mismatch for command '{name}' expected {len(want_params)} got {len(got_types)}", node.span,
                            expected=len(want_params), got=len(got_types), name=name, context="command")
            for index, (got, want) in enumerate(zip(got_types, want_params)):
                explicit = index - span_offset
                span = argv_nodes[explicit].span if 0 <= explicit < len(argv_nodes) else node.span
                if got != want:
                    self._error(S_COMMAND_TYPE, f"arg {index} for command '{name}' expects {want} got {got}", span,
                                expected=want, got=got, name=name, context=f"argument {index}")
            result_type = want_ret if want_ret is not None else "unit"
            void = allow_unit
            sym = self.sym_counter
            self.sym_counter += 1
            return ({"kind": "Cmd", "span": self._node_span(node), "name": name,
                     "argv": lowered_argv, "redirects": lowered_redirects,
                     "type": result_type, "symbol": sym}, result_type)
        finally:
            self._leave()

    def _lower_primary_or_binary(self, node: ParseNode, scope_stack: list, allow_unit: bool, expected: str | None = None):
        if True:
            if node.kind == "IntegerLiteral":
                return ({"kind": "IntLit", "span": self._node_span(node), "value": node.text, "type": "int"}, "int")
            elif node.kind == "StringLiteral":
                # node.value is unescaped, node.text is lexeme
                str_value = node.value if node.value is not None else node.text[1:-1]
                if len(str_value) > MAX_STR_LEN:
                    self._error(C_LIMIT_EXCEEDED,
                                f"string literal of {len(str_value)} bytes exceeds {MAX_STR_LEN}",
                                self._node_span(node),
                                expected=f"at most {MAX_STR_LEN} bytes",
                                got=f"{len(str_value)} bytes")
                return ({"kind": "StrLit", "span": self._node_span(node), "value": str_value, "lexeme": node.text, "type": "str"}, "str")
            elif node.kind == "BooleanLiteral":
                val = node.text == "true"
                return ({"kind": "BoolLit", "span": self._node_span(node), "value": val, "type": "bool"}, "bool")
            elif node.kind == "Identifier":
                name = node.text
                entry = self._lookup(name, scope_stack)
                if entry is None:
                    self._error(C_UNDECLARED, f"undeclared variable '{name}'", node.span,
                                expected="declared variable", got=name, name=name, context="variable reference")
                sym, typ, _ = entry
                return ({"kind": "Var", "span": self._node_span(node), "name": name, "symbol": sym, "type": typ}, typ)
            elif node.kind == "GroupExpr":
                # The parser charges one level per grouping paren (enter in
                # parse_primary), and so does lowering it. Transparent to
                # literal elaboration.
                self._enter()
                try:
                    inner = node.children[0]
                    return (yield self._lower_expr(inner, scope_stack, allow_unit, expected))
                finally:
                    self._leave()
            elif node.kind in ("OrExpr", "AndExpr", "EqualityExpr", "RelationalExpr", "AdditiveExpr", "MultiplicativeExpr",
                                 "BitOrExpr", "BitXorExpr", "BitAndExpr", "ShiftExpr"):
                left_node = node.children[0]
                right_node = node.children[1]
                op = node.text
                left, ltype = yield self._lower_expr(left_node, scope_stack, False)
                if ltype == "unit":
                    self._error(C_TYPE_MISMATCH, f"unit as operand for '{op}'", left_node.span,
                                expected="non-unit", got="unit", context="binary operand", operator=op)
                right, rtype = yield self._lower_expr(right_node, scope_stack, False)
                if rtype == "unit":
                    self._error(C_TYPE_MISMATCH, f"unit as operand for '{op}'", right_node.span,
                                expected="non-unit", got="unit", context="binary operand", operator=op)
                # type rules
                result_type = None
                if node.kind in ("AdditiveExpr", "MultiplicativeExpr"):
                    # op in + - * / %
                    if ltype != "int" or rtype != "int":
                        self._error(C_TYPE_MISMATCH, f"operator '{op}' expects int,int got {ltype},{rtype}", node.span,
                                    expected=("int", "int"), got=(ltype, rtype), context="binary operator", operator=op)
                    result_type = "int"
                elif node.kind in ("BitOrExpr", "BitXorExpr", "BitAndExpr", "ShiftExpr"):
                    # Stage 19a: op in | ^ & << >>, two's-complement ints,
                    # total (shifts mask). Same frozen shape as arithmetic.
                    if ltype != "int" or rtype != "int":
                        self._error(C_TYPE_MISMATCH, f"operator '{op}' expects int,int got {ltype},{rtype}", node.span,
                                    expected=("int", "int"), got=(ltype, rtype), context="binary operator", operator=op)
                    result_type = "int"
                elif node.kind == "EqualityExpr":
                    if ltype != rtype or not self._is_comparable(ltype):
                        self._error(C_TYPE_MISMATCH, f"equality '{op}' expects matching value types got {ltype},{rtype}", node.span,
                                    expected="matching value types", got=(ltype, rtype), context="equality", operator=op)
                    result_type = "bool"
                elif node.kind == "RelationalExpr":
                    if ltype != "int" or rtype != "int":
                        self._error(C_TYPE_MISMATCH, f"relational '{op}' expects int,int got {ltype},{rtype}", node.span,
                                    expected=("int", "int"), got=(ltype, rtype), context="relational operator", operator=op)
                    result_type = "bool"
                elif node.kind == "AndExpr" or node.kind == "OrExpr":
                    if ltype != "bool" or rtype != "bool":
                        self._error(C_TYPE_MISMATCH, f"logical '{op}' expects bool,bool got {ltype},{rtype}", node.span,
                                    expected=("bool", "bool"), got=(ltype, rtype), context="logical operator", operator=op)
                    result_type = "bool"
                else:
                    result_type = "int"
                return ({"kind": "BinOp", "span": self._node_span(node), "op": op, "left": left, "right": right, "type": result_type}, result_type)
            elif node.kind in ("PipeExpr", "CmdExpr", "FlagArg", "Redirect", "CmdArgs"):
                if self.edition != "shell":
                    self._error(CODE_UNEXP_TOKEN, f"shell syntax requires the shell edition (got {node.kind})", node.span,
                                expected="v1 expression", got=node.kind, context="edition gate")
                if self.profile == "strict":
                    self._error(C_PROFILE_EXCLUDED, f"shell construct {node.kind} excluded by --profile=strict", node.span,
                                expected="v1 expression", got=node.kind, context="profile gate")
                if node.kind == "PipeExpr":
                    return (yield self._lower_pipeline(node, scope_stack, allow_unit))
                elif node.kind == "CmdExpr":
                    return (yield self._lower_cmd(node, scope_stack, allow_unit))
                else:
                    self._error(C_TYPE_MISMATCH, f"unexpected shell fragment {node.kind}", node.span,
                                expected="pipeline or command context", got=node.kind, context="lowering")
            elif node.kind == "ListLit":
                return (yield self._lower_list_lit(node, scope_stack, expected))
            elif node.kind == "MapLit":
                return (yield self._lower_map_lit(node, scope_stack, expected))
            elif node.kind == "IndexExpr":
                return (yield self._lower_index(node, scope_stack))
            elif node.kind == "FieldExpr":
                return (yield self._lower_field(node, scope_stack))
            elif node.kind == "RecLit":
                return (yield self._lower_reclit(node, scope_stack))
            elif node.kind == "CallExpr":
                # The parser charges one level per call argument group (enter
                # in parse_postfix around the arg list); lowering matches it.
                self._enter()
                try:
                    # children: callee primary, ArgList
                    callee_node = node.children[0]
                    arglist_node = node.children[1] if len(node.children) > 1 else None
                    # The frozen grammar allows any primary as the callee
                    # (PostfixExpr), but only an Identifier names a function.
                    # Every other callee form is semantically non-callable and
                    # must be rejected at the callee itself -- never resolved
                    # through str(text) which would look up a function literally
                    # named "None"/"1" and could even find one.
                    if callee_node.kind != "Identifier":
                        self._error(C_UNKNOWN_FUNCTION, f"called expression is not a function name", callee_node.span,
                                    expected="identifier callee", got=callee_node.kind, context="call")
                    callee_name = callee_node.text
                    qualified = self._split_qualified(callee_name)
                    if qualified is not None:
                        alias, base = qualified
                        callee_name = self._qualify(alias, base, callee_node.span)
                    else:
                        # Own top-level names resolve through the file's
                        # alias map (identity for entry files: v1-identical).
                        callee_name = self._top(callee_name)
                    # Stage 16 builtin: print(x). Stage 19a widens the
                    # argument to every value type (aggregates render in the
                    # frozen canonical format; the RIR builder desugars).
                    # A user function named print is rejected as reserved (see
                    # analyze()), so reaching here with that name means the
                    # builtin. It behaves like a unit call everywhere,
                    # hence ExprStmt-natural via the shared unit rules.
                    if callee_name == "print":
                        arg_nodes = list(arglist_node.children) if arglist_node is not None else []
                        if len(arg_nodes) != 1:
                            self._error(C_ARITY_MISMATCH, f"arity mismatch for 'print' expected 1 got {len(arg_nodes)}", node.span,
                                        expected=1, got=len(arg_nodes), callee=callee_name, context="call")
                        arg_stable, atype = yield self._lower_expr(arg_nodes[0], scope_stack, False)
                        if atype == "unit":
                            self._error(C_TYPE_MISMATCH, "unit as argument for 'print'", arg_nodes[0].span,
                                        expected="non-unit", got="unit", callee=callee_name, context="call argument")
                        if not self._is_printable(atype):
                            self._error(C_TYPE_MISMATCH, f"print expects a value type got {atype}", arg_nodes[0].span,
                                        expected="value type", got=atype, callee=callee_name, context="call argument")
                        if self.profile == "core":
                            self._check_core_shape(_agtypes.parse_type(atype), arg_nodes[0].span)
                        stable = {"kind": "Call", "span": self._node_span(node), "callee": "print", "args": [arg_stable], "symbol": -1, "type": "unit"}
                        return (stable, "unit")
                    # Stage 19a aggregate builtins (reserved names; dedicated
                    # RIR ops; first-error order = source order of checks).
                    if callee_name in AGG_BUILTINS:
                        arg_nodes = list(arglist_node.children) if arglist_node is not None else []
                        return (yield self._lower_builtin(callee_name, arg_nodes, node, scope_stack, expected))
                    # Stage 19a record construction through the empty call
                    # shape `Point()` (named fields route via RecLit in the
                    # parser; positional args are never record construction).
                    if callee_name in self.record_decls:
                        arg_nodes = list(arglist_node.children) if arglist_node is not None else []
                        if arg_nodes:
                            self._error(C_TYPE_MISMATCH, f"record '{callee_name}' construction needs named fields", node.span,
                                        expected="named fields", got=callee_name, callee=callee_name, context="call")
                        return (yield self._lower_reclit_call(callee_name, [], node, scope_stack))
                    # lookup function
                    if callee_name not in self.global_funcs:
                        self._error(C_UNKNOWN_FUNCTION, f"unknown function '{callee_name}'", callee_node.span,
                                    expected="known function", got=callee_name, callee=callee_name, context="call")
                    finfo = self.global_funcs[callee_name]
                    expected_arity = len(finfo["params"])
                    # get args (parameter types elaborate untyped literals)
                    args = []
                    arg_types = []
                    want_params = [pt for (_pn, pt, _ps) in finfo["params"]]
                    if arglist_node is not None:
                        for index, arg_expr_node in enumerate(arglist_node.children):
                            # each arg is Expr
                            want = want_params[index] if index < len(want_params) else None
                            arg_stable, atype = yield self._lower_expr(arg_expr_node, scope_stack, False, want)
                            if atype == "unit":
                                self._error(C_TYPE_MISMATCH, f"unit as argument for '{callee_name}'", arg_expr_node.span,
                                            expected="non-unit", got="unit", callee=callee_name, context="call argument")
                            args.append(arg_stable)
                            arg_types.append(atype)
                    if len(args) != expected_arity:
                        self._error(C_ARITY_MISMATCH, f"arity mismatch for '{callee_name}' expected {expected_arity} got {len(args)}", node.span,
                                    expected=expected_arity, got=len(args), callee=callee_name, context="call")
                    # check each arg type
                    for i, (atype, (pname, ptype, _)) in enumerate(zip(arg_types, finfo["params"])):
                        if atype != ptype:
                            self._error(C_TYPE_MISMATCH, f"arg {i} for '{callee_name}' expects {ptype} got {atype}", arglist_node.children[i].span if arglist_node and i < len(arglist_node.children) else node.span,
                                        expected=ptype, got=atype, callee=callee_name, context=f"argument {i}")
                    # result type
                    result_type = finfo["ret_type"] if finfo["ret_type"] is not None else "unit"
                    # No generic unit rejection here by design: every value
                    # context above carries its own unit check (let init,
                    # return, condition, operand, argument), so a unit Call
                    # always surfaces with the specific span and message of
                    # the context that misuses it. ExprStmt alone allows unit.
                    stable = {"kind": "Call", "span": self._node_span(node), "callee": callee_name, "args": args, "symbol": finfo["symbol"], "type": result_type}
                    return (stable, result_type)
                finally:
                    self._leave()
            else:
                self._error(C_TYPE_MISMATCH, f"unknown expr {node.kind}", node.span,
                            expected="supported expression", got=node.kind, context="lowering")

def analyze(source: str, filename: str = "<input>", edition: str = "v1", commands: dict | None = None, profile: str = "default") -> AnalyzeResult:
    if not isinstance(source, str) or not isinstance(filename, str):
        return AnalyzeResult(None, Diagnostic(CODE_INVALID, "source and filename must be strings", Span(filename if isinstance(filename,str) else "<input>",1,1,0,0)))
    # lex+parse
    pres = parse(source, filename, edition)
    if not pres.ok:
        # pass through PAR_*/LEX_*
        d = pres.diagnostic
        return AnalyzeResult(None, Diagnostic(
            d.code, d.message, d.span,
            expected=getattr(d, "expected", None) or None,
            got_kind=getattr(d, "got_kind", None),
            got_lexeme=getattr(d, "got_lexeme", None),
        ))
    # lowering + semantics
    analyzer = Analyzer(pres.root, source=source, edition=edition, commands=commands, profile=profile)
    return analyzer.analyze()

def analyze_bytes(data: bytes, filename: str = "<input>", edition: str = "v1", commands: dict | None = None, profile: str = "default") -> AnalyzeResult:
    if not isinstance(data, bytes) or not isinstance(filename, str):
        return AnalyzeResult(None, Diagnostic(CODE_INVALID, "data must be bytes", Span(filename if isinstance(filename,str) else "<input>",1,1,0,0)))
    from tools.rynorlang.lex import lex_bytes
    res = lex_bytes(data, filename, edition)
    if res.diagnostic:
        code = CODE_FILE if res.diagnostic.code=="LEX_FILE_TOO_LARGE" else CODE_LEX
        return AnalyzeResult(None, Diagnostic(code, res.diagnostic.message, res.diagnostic.span, got_kind=res.diagnostic.code))
    return analyze_tokens(res.tokens, filename, source=data.decode("ascii"), edition=edition, commands=commands, profile=profile)

def analyze_file(path, edition: str = "v1", commands: dict | None = None, profile: str = "default") -> AnalyzeResult:
    try:
        p = Path(path)
        # use lex_file for bounds
        from tools.rynorlang.lex import lex_file
        lres = lex_file(p, edition)
        if lres.diagnostic:
            code = CODE_FILE if lres.diagnostic.code=="LEX_FILE_TOO_LARGE" else CODE_LEX
            return AnalyzeResult(None, Diagnostic(code, lres.diagnostic.message, lres.diagnostic.span, got_kind=lres.diagnostic.code))
        return analyze_tokens(lres.tokens, str(p), edition=edition, commands=commands, profile=profile)
    except (OSError, TypeError, ValueError) as e:
        return AnalyzeResult(None, Diagnostic(CODE_INVALID, str(e), Span(str(path),1,1,0,0)))

def analyze_tokens(tokens, filename: str = "<input>", source: str | None = None, edition: str = "v1", commands: dict | None = None, profile: str = "default") -> AnalyzeResult:
    if not isinstance(filename, str) or (source is not None and not isinstance(source, str)):
        return AnalyzeResult(None, Diagnostic(CODE_INVALID, "invalid filename or source", Span("<input>",1,1,0,0)))
    if not isinstance(tokens, tuple) or not tokens:
        return AnalyzeResult(None, Diagnostic(CODE_INVALID, "tokens must be non-empty tuple", Span(filename,1,1,0,0)))
    # validate tokens via parse_tokens
    pres = parse_tokens(tokens, edition)
    if not pres.ok:
        d = pres.diagnostic
        return AnalyzeResult(None, Diagnostic(
            d.code, d.message, d.span,
            expected=getattr(d, "expected", None) or None,
            got_kind=getattr(d, "got_kind", None),
            got_lexeme=getattr(d, "got_lexeme", None),
        ))
    if source is not None:
        source_tokens = lex(source, tokens[0].span.filename, edition)
        if source_tokens.diagnostic is not None:
            # The supplied source is itself lexically at fault: report its
            # real diagnostic (mapped like the sibling entry points) instead
            # of masking it as a token mismatch.
            lex_diag = source_tokens.diagnostic
            lex_code = CODE_FILE if lex_diag.code == "LEX_FILE_TOO_LARGE" else CODE_LEX
            return AnalyzeResult(None, Diagnostic(lex_code, lex_diag.message, lex_diag.span,
                                                  got_kind=lex_diag.code))
        if source_tokens.tokens != tokens:
            return AnalyzeResult(None, Diagnostic(CODE_INVALID, "source does not match tokens", Span(filename,1,1,0,0)))
    analyzer = Analyzer(pres.root, source=source, tokens=tokens, edition=edition, commands=commands, profile=profile)
    return analyzer.analyze()

def iter_ast_json(ast):
    """Serialize the caller-owned AST without using the Python call stack.

    The AST contract uses string keys exclusively; output is byte-identical
    to json.dumps(ast, sort_keys=True, separators=(",", ":")) on that domain.
    """
    pending = [(False, ast)]
    while pending:
        raw, value = pending.pop()
        if raw:
            yield value
        elif isinstance(value, dict):
            yield "{"
            pending.append((True, "}"))
            keys = sorted(value)
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                pending.append((False, value[key]))
                pending.append((True, ":"))
                pending.append((False, key))
                if index:
                    pending.append((True, ","))
        elif isinstance(value, list):
            yield "["
            pending.append((True, "]"))
            for index in range(len(value) - 1, -1, -1):
                pending.append((False, value[index]))
                if index:
                    pending.append((True, ","))
        else:
            yield json.dumps(value, sort_keys=True, separators=(",", ":"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Analyze Stage 14 RynorLang source")
    ap.add_argument("source", type=Path, nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--edition", default="v1",
                    help="language edition: v1 (default) or shell/shell-preview")
    ap.add_argument("--profile", default="default", choices=list(VALID_PROFILES),
                    help="build profile: default (current behavior) or strict (19d reproducible lock)")
    args = ap.parse_args(argv)
    if args.source is None:
        ap.print_usage(sys.stderr)
        return 2
    if args.edition not in ("v1", "shell", "shell-preview"):
        print(f"unknown edition {args.edition!r} (expected v1 or shell)", file=sys.stderr)
        return 2
    from tools.rynorlang.shell import DEMO_COMMANDS
    commands = DEMO_COMMANDS if _normalize_edition(args.edition) == "shell" else None
    res = analyze_file(args.source, args.edition, commands, args.profile)
    if not res.ok:
        d = res.diagnostic
        # diagnostic line
        msg = f"{d.span.filename}:{d.span.line}:{d.span.column}:{d.span.offset}: {d.code}: {d.message}"
        detail = {
            "code": d.code,
            "message": d.message,
            "span": {"filename": d.span.filename, "line": d.span.line, "column": d.span.column, "offset": d.span.offset, "length": d.span.length},
        }
        for key in ("expected", "got", "name", "callee", "context", "operator", "got_kind", "got_lexeme"):
            value = getattr(d, key)
            if value is not None:
                detail[key] = value
        print(json.dumps({"diagnostic": detail}, sort_keys=True, separators=(",",":")), file=sys.stderr)
        print(msg, file=sys.stderr)
        return 2 if d.code == CODE_INVALID else 1
    # success: stable AST dump + SEM_OK
    # need deterministic dump
    sys.stdout.writelines(iter_ast_json(res.ast))
    print()
    print("SEM_OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
