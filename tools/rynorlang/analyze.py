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

# Stage 15b shell-edition codes (additive-only; the five SEM_* above are frozen).
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
                 edition: str = "v1", commands: dict | None = None):
        self.program = program
        self.depth = 0
        self.edition = _normalize_edition(edition)
        # Host-side stub registry: name -> ([param types], ret or None).
        # None means no command is known (every Cmd is SHELL_UNKNOWN_COMMAND).
        self.commands = commands
        self.global_funcs = {}  # name -> {params: [(name,type)], ret_type, span, symbol, node}
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
            # first pass: collect functions
            func_nodes = list(self.program.children)  # Program children are FunctionDef
            function_params = []
            for fn in func_nodes:
                # fn is FunctionDef ParseNode
                name = fn.text
                span = fn.span
                if name in self.global_funcs:
                    self._error(C_DUPLICATE, f"duplicate function '{name}'", span,
                                expected="unique function", got=name, name=name, context="function")
                if name == "print":
                    self._error(C_DUPLICATE, "'print' is a reserved builtin", span,
                                expected="non-reserved function name", got=name, name=name, context="function")
                # extract params and ret_type
                # children: [Identifier name, ParamList?, Type?, Block]
                params = []
                ret_type = None
                block = None
                for child in fn.children:
                    if child.kind == "ParamList":
                        for p in child.children:
                            # p is Param with children (Identifier, Type)
                            pname = p.text
                            tnode = p.children[1] if len(p.children) > 1 else p.children[0]
                            # tnode is Type with text
                            t = tnode.text
                            params.append((pname, t, p.span))
                    elif child.kind == "Type":
                        ret_type = child.text
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
                    seen.add(pname)
                symbol = self.sym_counter
                self.sym_counter += 1
                self.global_funcs[name] = {"params": params, "ret_type": ret_type, "span": span, "symbol": symbol, "node": fn, "block": block}
                function_params.append(params)
            for params in function_params:
                for pname, _ptype, pspan in params:
                    if pname in self.global_funcs:
                        self._error(C_DUPLICATE, f"duplicate declaration '{pname}' shadows global function", pspan,
                                    expected="name distinct from functions", got=pname, name=pname, context="parameter")
            # second pass: lower each function to stable AST
            stable_funcs = []
            for fn in func_nodes:
                stable = self._lower_function(fn)
                stable_funcs.append(stable)
                if self.diagnostic:
                    break
            if self.diagnostic:
                return AnalyzeResult(None, self.diagnostic)
            # build Program stable
            prog_span = self._node_span(self.program)
            prog = {"kind": "Program", "span": prog_span, "functions": stable_funcs}
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
            info = self.global_funcs[name]
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
            return {"kind": "Function", "span": prog_span, "name": name, "params": stable_params, "ret_type": ret_type, "body": stable_block, "symbol": info["symbol"]}
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

    def _lower_block(self, block: ParseNode, scope_stack: list, ret_type) -> dict:
        self._enter()
        try:
            # new scope for this block
            scope_stack.append({})
            stmts = []
            for stmt in block.children:
                stable = yield self._lower_stmt(stmt, scope_stack, ret_type)
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

    def _declare(self, name: str, typ: str, span: Span, scope_stack: list):
        # check no shadowing anywhere in scope chain + global funcs
        if name in self.global_funcs:
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
                typ = type_node.text
                # lower init expr first in current scope (before declaration)
                init, init_type = yield self._lower_expr(expr_node, scope_stack, False)
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
                    init, init_type = yield self._lower_expr(expr_node, scope_stack, False)
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
                body = yield self._lower_block(body_node, scope_stack, ret_type)
                return {"kind": "While", "span": self._node_span(stmt), "cond": cond, "body": body}
            elif stmt.kind == "ExprStmt":
                expr_node = stmt.children[0]
                # for ExprStmt, allow unit
                expr, etype = yield self._lower_expr(expr_node, scope_stack, True)
                # if expr is Call returning unit, ok; otherwise etype must not be unit (but only Call can be unit)
                return {"kind": "ExprStmt", "span": self._node_span(stmt), "expr": expr}
            elif stmt.kind == "Block":
                return (yield self._lower_block(stmt, scope_stack, ret_type))
            else:
                self._error(C_TYPE_MISMATCH, f"unknown statement {stmt.kind}", stmt.span,
                            expected="supported statement", got=stmt.kind, context="lowering")
        finally:
            if stmt.kind == "IfStmt":
                self._leave()

    def _lower_expr(self, node: ParseNode, scope_stack: list, allow_unit: bool):
        # Depth mirrors the parser's per-construct accounting exactly: only
        # grammar constructs the parser itself charges (unary, each call
        # argument group, grouping parens) consume budget here. Literals,
        # identifiers and binary operands do not: the parser builds them
        # iteratively without enter().
        if node.kind == "UnaryExpr":
            self._enter()
            try:
                return (yield self._lower_unary(node, scope_stack, allow_unit))
            finally:
                self._leave()
        return (yield self._lower_primary_or_binary(node, scope_stack, allow_unit))

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
            else:
                self._error(C_TYPE_MISMATCH, f"unknown unary '{op}'", node.span,
                            expected=("-", "!"), got=op, context="unary operator", operator=op)
                result_type = otype
            return ({"kind": "UnOp", "span": self._node_span(node), "op": op, "operand": operand, "type": result_type}, result_type)

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

    def _lower_primary_or_binary(self, node: ParseNode, scope_stack: list, allow_unit: bool):
        if True:
            if node.kind == "IntegerLiteral":
                return ({"kind": "IntLit", "span": self._node_span(node), "value": node.text, "type": "int"}, "int")
            elif node.kind == "StringLiteral":
                # node.value is unescaped, node.text is lexeme
                return ({"kind": "StrLit", "span": self._node_span(node), "value": node.value if node.value is not None else node.text[1:-1], "lexeme": node.text, "type": "str"}, "str")
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
                # parse_primary), and so does lowering it.
                self._enter()
                try:
                    inner = node.children[0]
                    return (yield self._lower_expr(inner, scope_stack, allow_unit))
                finally:
                    self._leave()
            elif node.kind in ("OrExpr", "AndExpr", "EqualityExpr", "RelationalExpr", "AdditiveExpr", "MultiplicativeExpr"):
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
                elif node.kind == "EqualityExpr":
                    if ltype != rtype or ltype not in ("int","bool","str"):
                        self._error(C_TYPE_MISMATCH, f"equality '{op}' expects same int/bool/str got {ltype},{rtype}", node.span,
                                    expected="matching int, bool, or str", got=(ltype, rtype), context="equality", operator=op)
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
                if node.kind == "PipeExpr":
                    return (yield self._lower_pipeline(node, scope_stack, allow_unit))
                elif node.kind == "CmdExpr":
                    return (yield self._lower_cmd(node, scope_stack, allow_unit))
                else:
                    self._error(C_TYPE_MISMATCH, f"unexpected shell fragment {node.kind}", node.span,
                                expected="pipeline or command context", got=node.kind, context="lowering")
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
                    # Stage 16 builtin: print(x: int|bool|str): unit. A user
                    # function named print is rejected as reserved (see
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
                        if atype not in ("int", "bool", "str"):
                            self._error(C_TYPE_MISMATCH, f"print expects int, bool, or str got {atype}", arg_nodes[0].span,
                                        expected=("int", "bool", "str"), got=atype, callee=callee_name, context="call argument")
                        stable = {"kind": "Call", "span": self._node_span(node), "callee": "print", "args": [arg_stable], "symbol": -1, "type": "unit"}
                        return (stable, "unit")
                    # lookup function
                    if callee_name not in self.global_funcs:
                        self._error(C_UNKNOWN_FUNCTION, f"unknown function '{callee_name}'", callee_node.span,
                                    expected="known function", got=callee_name, callee=callee_name, context="call")
                    finfo = self.global_funcs[callee_name]
                    expected_arity = len(finfo["params"])
                    # get args
                    args = []
                    arg_types = []
                    if arglist_node is not None:
                        for arg_expr_node in arglist_node.children:
                            # each arg is Expr
                            arg_stable, atype = yield self._lower_expr(arg_expr_node, scope_stack, False)
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

def analyze(source: str, filename: str = "<input>", edition: str = "v1", commands: dict | None = None) -> AnalyzeResult:
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
    analyzer = Analyzer(pres.root, source=source, edition=edition, commands=commands)
    return analyzer.analyze()

def analyze_bytes(data: bytes, filename: str = "<input>", edition: str = "v1", commands: dict | None = None) -> AnalyzeResult:
    if not isinstance(data, bytes) or not isinstance(filename, str):
        return AnalyzeResult(None, Diagnostic(CODE_INVALID, "data must be bytes", Span(filename if isinstance(filename,str) else "<input>",1,1,0,0)))
    from tools.rynorlang.lex import lex_bytes
    res = lex_bytes(data, filename, edition)
    if res.diagnostic:
        code = CODE_FILE if res.diagnostic.code=="LEX_FILE_TOO_LARGE" else CODE_LEX
        return AnalyzeResult(None, Diagnostic(code, res.diagnostic.message, res.diagnostic.span, got_kind=res.diagnostic.code))
    return analyze_tokens(res.tokens, filename, source=data.decode("ascii"), edition=edition, commands=commands)

def analyze_file(path, edition: str = "v1", commands: dict | None = None) -> AnalyzeResult:
    try:
        p = Path(path)
        # use lex_file for bounds
        from tools.rynorlang.lex import lex_file
        lres = lex_file(p, edition)
        if lres.diagnostic:
            code = CODE_FILE if lres.diagnostic.code=="LEX_FILE_TOO_LARGE" else CODE_LEX
            return AnalyzeResult(None, Diagnostic(code, lres.diagnostic.message, lres.diagnostic.span, got_kind=lres.diagnostic.code))
        return analyze_tokens(lres.tokens, str(p), edition=edition, commands=commands)
    except (OSError, TypeError, ValueError) as e:
        return AnalyzeResult(None, Diagnostic(CODE_INVALID, str(e), Span(str(path),1,1,0,0)))

def analyze_tokens(tokens, filename: str = "<input>", source: str | None = None, edition: str = "v1", commands: dict | None = None) -> AnalyzeResult:
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
    analyzer = Analyzer(pres.root, source=source, tokens=tokens, edition=edition, commands=commands)
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
    args = ap.parse_args(argv)
    if args.source is None:
        ap.print_usage(sys.stderr)
        return 2
    if args.edition not in ("v1", "shell", "shell-preview"):
        print(f"unknown edition {args.edition!r} (expected v1 or shell)", file=sys.stderr)
        return 2
    from tools.rynorlang.shell import DEMO_COMMANDS
    commands = DEMO_COMMANDS if _normalize_edition(args.edition) == "shell" else None
    res = analyze_file(args.source, args.edition, commands)
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
