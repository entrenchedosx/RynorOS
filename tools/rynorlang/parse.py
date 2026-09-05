#!/usr/bin/env python3
"""Deterministic host-side parser for the frozen Stage 13 RynorLang grammar."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.rynorlang.lex import Diagnostic as LexDiagnostic
from tools.rynorlang.lex import MAX_SOURCE_BYTES, Span, Token, lex, lex_bytes, lex_file


PARSE_MAX_DEPTH = 256
_RECURSION_LOCK = threading.Lock()
_RECURSION_HEADROOM = 4096


@dataclass(frozen=True)
class ParseNode:
    kind: str
    span: Span
    children: tuple["ParseNode", ...] = ()
    text: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class ParseDiagnostic:
    code: str
    message: str
    span: Span
    expected: tuple[str, ...] = ()
    got_kind: str | None = None
    got_lexeme: str | None = None


@dataclass(frozen=True)
class ParseResult:
    root: ParseNode | None
    diagnostic: ParseDiagnostic | None

    @property
    def ok(self) -> bool:
        return self.diagnostic is None

    @property
    def tree(self) -> ParseNode | None:
        return self.root


class _Abort(Exception):
    def __init__(self, diagnostic: ParseDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


_TYPE_TOKENS = {"INT_TYPE", "BOOL_TYPE", "STR_TYPE"}
_SHELL_EDITIONS = ("shell", "shell-preview")


def _normalize_edition(edition: str) -> str:
    if edition in _SHELL_EDITIONS:
        return "shell"
    return "v1"
_BINARY = {
    "OR_OR": (1, "OrExpr"),
    "AND_AND": (2, "AndExpr"),
    "EQ_EQ": (3, "EqualityExpr"),
    "BANG_EQ": (3, "EqualityExpr"),
    "LESS": (4, "RelationalExpr"),
    "GREATER": (4, "RelationalExpr"),
    "LESS_EQ": (4, "RelationalExpr"),
    "GREATER_EQ": (4, "RelationalExpr"),
    "PLUS": (5, "AdditiveExpr"),
    "MINUS": (5, "AdditiveExpr"),
    "STAR": (6, "MultiplicativeExpr"),
    "SLASH": (6, "MultiplicativeExpr"),
    "PERCENT": (6, "MultiplicativeExpr"),
}


def _cover(start: Span, end: Span) -> Span:
    finish = end.offset + end.length
    return Span(start.filename, start.line, start.column, start.offset, finish - start.offset)


class _Parser:
    def __init__(self, tokens: tuple[Token, ...], edition: str = "v1") -> None:
        self.tokens = tokens
        self.index = 0
        self.depth = 0
        self.edition = _normalize_edition(edition)

    def current(self) -> Token:
        return self.tokens[self.index]

    def at(self, kind: str) -> bool:
        return self.current().kind == kind

    def take(self) -> Token:
        token = self.current()
        if token.kind != "EOF":
            self.index += 1
        return token

    def match(self, *kinds: str) -> Token | None:
        if self.current().kind in kinds:
            return self.take()
        return None

    def fail(self, code: str, message: str, expected: Sequence[str] = ()) -> None:
        token = self.current()
        raise _Abort(
            ParseDiagnostic(
                code,
                message,
                token.span,
                tuple(expected),
                token.kind,
                token.lexeme,
            )
        )

    def expect(self, kind: str, context: str) -> Token:
        if not self.at(kind):
            code = "PAR_UNEXPECTED_EOF" if self.at("EOF") else "PAR_EXPECTED_TOKEN"
            self.fail(code, f"expected {kind} {context}", (kind,))
        return self.take()

    def enter(self) -> None:
        self.depth += 1
        # MUTATION_POINT_DEPTH_GUARD
        if self.depth > PARSE_MAX_DEPTH:
            self.depth -= 1
            self.fail("PAR_DEPTH_EXCEEDED", f"parse nesting exceeds {PARSE_MAX_DEPTH}")

    def leave(self) -> None:
        self.depth -= 1

    def parse_program(self) -> ParseNode:
        start = self.current().span
        functions: list[ParseNode] = []
        while self.at("FN"):
            functions.append(self.parse_function())
        # MUTATION_POINT_PROGRAM_TRAILING
        if not self.at("EOF"):
            self.fail("PAR_UNEXPECTED_TOKEN", "only function definitions are allowed at top level", ("FN", "EOF"))
        eof = self.current()
        end = functions[-1].span if functions else eof.span
        return ParseNode("Program", _cover(start, end), tuple(functions))

    def parse_function(self) -> ParseNode:
        self.enter()
        try:
            start = self.expect("FN", "to begin a function")
            name = self.identifier()
            self.expect("LEFT_PAREN", "after function name")
            params: list[ParseNode] = []
            if not self.at("RIGHT_PAREN"):
                params.append(self.parse_param())
                while self.match("COMMA"):
                    # MUTATION_POINT_PARAM_TRAILING_COMMA
                    if self.at("RIGHT_PAREN"):
                        self.fail("PAR_EXPECTED_TOKEN", "trailing comma is not allowed in parameter list", ("IDENTIFIER",))
                    params.append(self.parse_param())
            self.expect("RIGHT_PAREN", "after parameters")
            children: list[ParseNode] = [name]
            if params:
                children.append(ParseNode("ParamList", _cover(params[0].span, params[-1].span), tuple(params)))
            # MUTATION_POINT_RETURN_COLON
            if self.match("COLON"):
                children.append(self.parse_type())
            body = self.parse_block()
            children.append(body)
            return ParseNode("FunctionDef", _cover(start.span, body.span), tuple(children), text=name.text)
        finally:
            self.leave()

    def parse_param(self) -> ParseNode:
        name = self.identifier()
        self.expect("COLON", "between parameter name and type")
        type_node = self.parse_type()
        return ParseNode("Param", _cover(name.span, type_node.span), (name, type_node), text=name.text)

    def parse_type(self) -> ParseNode:
        if self.current().kind not in _TYPE_TOKENS:
            self.fail("PAR_EXPECTED_TOKEN", "expected type int, bool, or str", tuple(sorted(_TYPE_TOKENS)))
        token = self.take()
        return ParseNode("Type", token.span, text=token.lexeme)

    def parse_block(self) -> ParseNode:
        self.enter()
        try:
            left = self.expect("LEFT_BRACE", "to begin block")
            statements: list[ParseNode] = []
            while not self.at("RIGHT_BRACE"):
                if self.at("EOF"):
                    self.fail("PAR_UNEXPECTED_EOF", "unterminated block", ("RIGHT_BRACE",))
                statements.append(self.parse_statement())
            right = self.take()
            return ParseNode("Block", _cover(left.span, right.span), tuple(statements))
        finally:
            self.leave()

    def parse_statement(self) -> ParseNode:
        if self.at("LET"):
            return self.parse_let()
        if self.at("RETURN"):
            return self.parse_return()
        if self.at("IF"):
            return self.parse_if()
        if self.at("WHILE"):
            return self.parse_while()
        if self.at("LEFT_BRACE"):
            return self.parse_block()
        expression = self.parse_pipeline()
        semicolon = self.expect("SEMICOLON", "after expression")
        return ParseNode("ExprStmt", _cover(expression.span, semicolon.span), (expression,))

    def parse_let(self) -> ParseNode:
        start = self.take()
        name = self.identifier()
        self.expect("COLON", "between variable name and type")
        type_node = self.parse_type()
        self.expect("EQUAL", "before initializer")
        expression = self.parse_pipeline()
        end = self.expect("SEMICOLON", "after let statement")
        return ParseNode("LetStmt", _cover(start.span, end.span), (name, type_node, expression), text=name.text)

    def parse_return(self) -> ParseNode:
        start = self.take()
        children: tuple[ParseNode, ...] = ()
        if not self.at("SEMICOLON"):
            children = (self.parse_pipeline(),)
        end = self.expect("SEMICOLON", "after return statement")
        return ParseNode("ReturnStmt", _cover(start.span, end.span), children)

    def parse_if(self) -> ParseNode:
        self.enter()
        try:
            start = self.take()
            condition = self.parse_pipeline()
            then_block = self.parse_block()
            children: list[ParseNode] = [condition, then_block]
            if self.match("ELSE"):
                children.append(self.parse_if() if self.at("IF") else self.parse_block())
            return ParseNode("IfStmt", _cover(start.span, children[-1].span), tuple(children))
        finally:
            self.leave()

    def parse_while(self) -> ParseNode:
        start = self.take()
        condition = self.parse_pipeline()
        body = self.parse_block()
        return ParseNode("WhileStmt", _cover(start.span, body.span), (condition, body))

    def parse_pipeline(self) -> ParseNode:
        # Stage 15b shell surface: precedence-0 left-associative pipeline.
        # Iterative like the binary loop, so long chains cost no depth.
        # In v1 PIPE_GT tokens cannot occur via the lexer; a hand-built
        # token stream carrying one is rejected with an old code so the
        # v1 contract never silently grows.
        left = self.parse_stage()
        if not self.at("PIPE_GT"):
            return left
        if self.edition != "shell":
            self.fail("PAR_UNEXPECTED_TOKEN", "pipeline operator requires the shell edition", ("SEMICOLON",))
        stages: list[ParseNode] = [left]
        while self.match("PIPE_GT"):
            if self.at("EOF"):
                self.fail("PAR_UNEXPECTED_EOF", "pipeline stage missing after '|>'", ("IDENTIFIER", "STRING", "INTEGER"))
            stages.append(self.parse_stage())
        return ParseNode("PipeExpr", _cover(stages[0].span, stages[-1].span), tuple(stages))

    def parse_stage(self) -> ParseNode:
        # One pipeline-stage position. A lone bare word here is a zero-arg
        # command candidate (resolved in semantics: lexical variable first,
        # then the stub registry), so `ls |> count` reads as shell while a
        # lone word anywhere else keeps its v1 meaning. Anything else falls
        # through to juxtaposition-command or ordinary expression parsing.
        if self.edition == "shell" and self.at("IDENTIFIER"):
            nxt = self.tokens[self.index + 1] if self.index + 1 < len(self.tokens) else None
            if nxt is not None and nxt.kind in ("PIPE_GT", "SEMICOLON", "EOF", "RIGHT_BRACE",
                                                "RIGHT_PAREN", "COMMA"):
                tok = self.take()
                name_node = ParseNode("Identifier", tok.span, text=tok.lexeme)
                self.enter()
                try:
                    return ParseNode("CmdExpr", tok.span, (name_node,), text=tok.lexeme)
                finally:
                    self.leave()
        return self.parse_cmd_or_expr()

    def parse_cmd_or_expr(self) -> ParseNode:
        if self.edition == "shell" and self.at("IDENTIFIER"):
            saved = self.index
            try:
                node = self.parse_cmd()
            except _Abort:
                self.index = saved
            else:
                if node is not None:
                    return node
                self.index = saved
        return self.parse_expression()

    def parse_cmd(self) -> ParseNode | None:
        # Honest command node, never a desugared Call: bare word plus
        # space-separated args/redirects using zero new lexer tokens.
        # Returns None for a lone word (which stays a Var) so v1 meanings
        # are preserved; raises for malformed command text.
        name_tok = self.take()
        nxt = self.current()
        if nxt.kind not in ("IDENTIFIER", "MINUS", "INTEGER", "STRING", "TRUE", "FALSE", "GREATER"):
            return None
        if nxt.kind == "MINUS":
            after = self.tokens[self.index + 1] if self.index + 1 < len(self.tokens) else None
            if (after is None or after.kind not in ("IDENTIFIER", "INTEGER")
                    or nxt.span.offset + 1 != after.span.offset):
                # `a - b` (spaced) stays a binary subtraction, never a command.
                return None
        if nxt.kind == "GREATER":
            # A lone word followed by `>` is a comparison (`a > b`), never a
            # command -- unless the redirect target is a quoted string, which
            # no comparison operand can be (`a > "o"` is a type error in v1).
            # `>>` needs the same two-token lookahead.
            after = self.tokens[self.index + 1] if self.index + 1 < len(self.tokens) else None
            if after is None:
                return None
            if after.kind == "STRING":
                pass
            elif (after.kind == "GREATER" and after.span.offset == nxt.span.offset + 1
                    and self.index + 2 < len(self.tokens)
                    and self.tokens[self.index + 2].kind == "STRING"):
                pass
            else:
                return None
        name_node = ParseNode("Identifier", name_tok.span, text=name_tok.lexeme)
        args: list[ParseNode] = []
        redirects: list[ParseNode] = []
        while True:
            tok = self.current()
            if tok.kind == "IDENTIFIER":
                args.append(ParseNode("Identifier", tok.span, text=tok.lexeme))
                self.take()
            elif tok.kind in ("INTEGER", "STRING", "TRUE", "FALSE"):
                args.append(self.parse_primary())
            elif tok.kind == "MINUS":
                after = self.tokens[self.index + 1] if self.index + 1 < len(self.tokens) else None
                if (after is not None and after.kind == "IDENTIFIER"
                        and tok.span.offset + 1 == after.span.offset):
                    self.take()
                    flag_tok = self.take()
                    args.append(ParseNode("FlagArg", _cover(tok.span, flag_tok.span), text=flag_tok.lexeme))
                elif (after is not None and after.kind == "INTEGER"
                        and tok.span.offset + 1 == after.span.offset):
                    self.take()
                    int_tok = self.take()
                    args.append(ParseNode("UnaryExpr", _cover(tok.span, int_tok.span),
                                          (ParseNode("IntegerLiteral", int_tok.span, text=int_tok.lexeme, value=int_tok.value),),
                                          text="-"))
                else:
                    self.fail("PAR_EXPECTED_TOKEN", "command arguments use bare words, literals, or adjacent -flags", ("IDENTIFIER", "STRING", "INTEGER"))
            elif tok.kind == "GREATER":
                redirects.append(self.parse_redirect())
            else:
                break
        if not args and not redirects:
            return None
        self.enter()
        try:
            children: list[ParseNode] = [name_node]
            if args:
                children.append(ParseNode("CmdArgs", _cover(args[0].span, args[-1].span), tuple(args)))
            children.extend(redirects)
            return ParseNode("CmdExpr", _cover(name_tok.span, children[-1].span), tuple(children), text=name_tok.lexeme)
        finally:
            self.leave()

    def parse_redirect(self) -> ParseNode:
        # MVP bound: redirect targets are quoted strings only. A bare word
        # after `>` stays a comparison operand (`a > b` keeps its v1 meaning);
        # only `cmd > "file"` / `cmd >> "file"` form redirects.
        first = self.expect("GREATER", "to begin a redirect")
        op = ">"
        nxt = self.current()
        if nxt.kind == "GREATER" and nxt.span.offset == first.span.offset + 1:
            self.take()
            op = ">>"
        target = self.current()
        if target.kind != "STRING":
            self.fail("PAR_EXPECTED_TOKEN", 'redirect target must be a quoted string (e.g. > "out")', ("STRING",))
            raise AssertionError("unreachable")
        self.take()
        target_node: ParseNode = ParseNode("StringLiteral", target.span, text=target.lexeme, value=target.value)
        return ParseNode("Redirect", _cover(first.span, target_node.span), (target_node,), text=op)

    def parse_expression(self, minimum: int = 1) -> ParseNode:
        left = self.parse_unary()
        while self.current().kind in _BINARY:
            precedence, kind = _BINARY[self.current().kind]
            if precedence < minimum:
                break
            operator = self.take()
            right = self.parse_expression(precedence + 1)
            left = ParseNode(kind, _cover(left.span, right.span), (left, right), text=operator.lexeme)
        return left

    def parse_unary(self) -> ParseNode:
        operator = self.match("MINUS", "BANG")
        if operator is not None:
            self.enter()
            try:
                operand = self.parse_unary()
                return ParseNode("UnaryExpr", _cover(operator.span, operand.span), (operand,), text=operator.lexeme)
            finally:
                self.leave()
        return self.parse_postfix()

    def parse_postfix(self) -> ParseNode:
        expression = self.parse_primary()
        while self.match("LEFT_PAREN"):
            self.enter()
            try:
                arguments: list[ParseNode] = []
                if not self.at("RIGHT_PAREN"):
                    arguments.append(self.parse_pipeline())
                    while self.match("COMMA"):
                        # MUTATION_POINT_CALL_TRAILING_COMMA
                        if self.at("RIGHT_PAREN"):
                            self.fail("PAR_EXPECTED_TOKEN", "trailing comma is not allowed in argument list")
                        arguments.append(self.parse_pipeline())
                right = self.expect("RIGHT_PAREN", "after arguments")
                arg_node = ParseNode("ArgList", _cover(arguments[0].span, arguments[-1].span), tuple(arguments)) if arguments else ParseNode("ArgList", right.span)
                expression = ParseNode("CallExpr", _cover(expression.span, right.span), (expression, arg_node))
            finally:
                self.leave()
        return expression

    def parse_primary(self) -> ParseNode:
        token = self.current()
        if token.kind == "IDENTIFIER":
            return self.identifier()
        if token.kind == "INTEGER":
            self.take()
            return ParseNode("IntegerLiteral", token.span, text=token.lexeme, value=token.value)
        if token.kind == "STRING":
            self.take()
            return ParseNode("StringLiteral", token.span, text=token.lexeme, value=token.value)
        if token.kind in {"TRUE", "FALSE"}:
            self.take()
            return ParseNode("BooleanLiteral", token.span, text=token.lexeme, value=token.lexeme)
        if token.kind == "LEFT_PAREN":
            self.enter()
            try:
                left = self.take()
                expression = self.parse_pipeline()
                right = self.expect("RIGHT_PAREN", "after expression")
                return ParseNode("GroupExpr", _cover(left.span, right.span), (expression,))
            finally:
                self.leave()
        self.fail("PAR_UNEXPECTED_TOKEN", "expected expression", ("IDENTIFIER", "INTEGER", "STRING", "TRUE", "FALSE", "LEFT_PAREN"))

    def identifier(self) -> ParseNode:
        token = self.expect("IDENTIFIER", "for name")
        return ParseNode("Identifier", token.span, text=token.lexeme)


def _input_error(tokens: object, message: str) -> ParseResult:
    filename = "<tokens>"
    if isinstance(tokens, tuple) and tokens and isinstance(tokens[0], Token):
        span = tokens[0].span
        if isinstance(span, Span) and isinstance(span.filename, str):
            filename = span.filename
    return ParseResult(None, ParseDiagnostic("PAR_INVALID_INPUT", message, Span(filename, 1, 1, 0, 0)))


def parse_tokens(tokens: tuple[Token, ...], edition: str = "v1") -> ParseResult:
    if not isinstance(tokens, tuple) or not tokens:
        return _input_error(tokens, "tokens must be a non-empty tuple")
    if any(not isinstance(token, Token) for token in tokens):
        return _input_error(tokens, "every item must be a lexer Token")
    eof_positions = [index for index, token in enumerate(tokens) if token.kind == "EOF"]
    if eof_positions != [len(tokens) - 1]:
        return _input_error(tokens, "tokens must contain exactly one final EOF")
    previous_end = 0
    previous_line = 1
    previous_column = 1
    filename = None
    for token in tokens:
        span = token.span
        if not isinstance(span, Span):
            return _input_error(tokens, "every token must contain a lexer Span")
        if not isinstance(span.filename, str) or any(
            type(value) is not int for value in (span.line, span.column, span.offset, span.length)
        ):
            return _input_error(tokens, "span filename must be a string and coordinates must be integers")
        if span.line < 1 or span.column < 1 or span.offset < previous_end or span.length < 0:
            return _input_error(tokens, "token spans must be ordered and non-overlapping")
        if filename is None:
            filename = span.filename
        if span.filename != filename:
            return _input_error(tokens, "token spans must belong to one source file")
        if span.offset + span.length > MAX_SOURCE_BYTES:
            return _input_error(tokens, "token spans must fit within the source byte limit")
        # Source bytes are unavailable here, but every token occupies one line.
        # A gap must have enough bytes for its newlines and final column; on the
        # same line every ASCII byte (including a tab) advances one column.
        gap = span.offset - previous_end
        lines = span.line - previous_line
        if (lines < 0
                or (lines == 0 and span.column != previous_column + gap)
                or (lines > 0 and lines + span.column - 1 > gap)):
            return _input_error(tokens, "token line and column must agree with byte offsets")
        if (not isinstance(token.kind, str) or not isinstance(token.lexeme, str)
                or (token.value is not None and not isinstance(token.value, str))):
            return _input_error(tokens, "token kind, lexeme and optional value must be strings")
        if span.length != len(token.lexeme):
            return _input_error(tokens, "token span length must match its lexeme")
        # Reuse the frozen lexer so caller-created tokens cannot bypass literal
        # range/escape rules or supply a decoded value inconsistent with the text.
        # The check runs in the same edition so shell tokens validate as shell.
        checked = lex(token.lexeme, edition=edition)
        expected_count = 1 if token.kind == "EOF" else 2
        if (not checked.ok or len(checked.tokens) != expected_count
                or checked.tokens[0].kind != token.kind
                or checked.tokens[0].lexeme != token.lexeme
                or checked.tokens[0].value != token.value):
            return _input_error(tokens, "token kind, lexeme and value must agree with the lexer")
        previous_end = span.offset + span.length
        previous_line = span.line
        previous_column = span.column + span.length
    parser = _Parser(tokens, edition)
    # CPython consumes several interpreter frames for one grammar nesting level.
    # Serialize the temporary recursion-limit adjustment and restore it before
    # returning so importing this module has no persistent process-wide effect.
    with _RECURSION_LOCK:
        old_limit = sys.getrecursionlimit()
        if old_limit < _RECURSION_HEADROOM:
            sys.setrecursionlimit(_RECURSION_HEADROOM)
        try:
            return ParseResult(parser.parse_program(), None)
        except _Abort as failure:
            return ParseResult(None, failure.diagnostic)
        except RecursionError:
            token = parser.current()
            return ParseResult(None, ParseDiagnostic("PAR_DEPTH_EXCEEDED", "parser recursion limit reached", token.span))
        finally:
            if old_limit < _RECURSION_HEADROOM:
                sys.setrecursionlimit(old_limit)


def _from_lex(result: object, edition: str = "v1") -> ParseResult:
    diagnostic = getattr(result, "diagnostic", None)
    if diagnostic is not None:
        if not isinstance(diagnostic, LexDiagnostic):
            return _input_error((), "lexer returned an invalid diagnostic")
        code = "PAR_FILE_TOO_LARGE" if diagnostic.code == "LEX_FILE_TOO_LARGE" else "PAR_LEX_ERROR"
        return ParseResult(None, ParseDiagnostic(code, diagnostic.message, diagnostic.span, got_kind=diagnostic.code))
    return parse_tokens(result.tokens, edition)


def parse(source: str, filename: str = "<input>", edition: str = "v1") -> ParseResult:
    if not isinstance(source, str) or not isinstance(filename, str):
        return _input_error((), "source and filename must be strings")
    return _from_lex(lex(source, filename, edition), edition)


def parse_bytes(data: bytes, filename: str = "<input>", edition: str = "v1") -> ParseResult:
    if not isinstance(data, bytes) or not isinstance(filename, str):
        return _input_error((), "data must be bytes and filename must be a string")
    return _from_lex(lex_bytes(data, filename, edition), edition)


def parse_file(path: str | Path, edition: str = "v1") -> ParseResult:
    try:
        return _from_lex(lex_file(path, edition), edition)
    except (OSError, TypeError, ValueError) as error:
        return ParseResult(None, ParseDiagnostic("PAR_INVALID_INPUT", str(error), Span(str(path), 1, 1, 0, 0)))


def _json_value(value: object) -> object:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    return value


def main(argv: Sequence[str] | None = None) -> int:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("source", type=Path)
    command.add_argument("--json", action="store_true", help="emit deterministic JSON syntax tree")
    command.add_argument("--edition", default="v1",
                         help="language edition: v1 (default) or shell/shell-preview")
    args = command.parse_args(argv)
    if args.edition not in ("v1", "shell", "shell-preview"):
        print(f"unknown edition {args.edition!r} (expected v1 or shell)", file=sys.stderr)
        return 2
    result = parse_file(args.source, args.edition)
    if not result.ok:
        if result.diagnostic is None:
            print("PAR_INVALID_INPUT: parser returned neither tree nor diagnostic", file=sys.stderr)
            return 1
        diagnostic = result.diagnostic
        print(f"{diagnostic.span.filename}:{diagnostic.span.line}:{diagnostic.span.column}:{diagnostic.span.offset}: {diagnostic.code}: {diagnostic.message}", file=sys.stderr)
        return 1
    if result.root is None:
        print("PAR_INVALID_INPUT: parser returned no tree", file=sys.stderr)
        return 1
    try:
        # Left-iterative productions (expression chains, chained calls) build
        # unbounded tree width from bounded grammar nesting. Serialize with a
        # transiently raised limit, matching the parse-time headroom, so a
        # legal-but-wide tree can never escape as a traceback.
        with _RECURSION_LOCK:
            sys.setrecursionlimit(_RECURSION_HEADROOM)
            payload = json.dumps(_json_value(result.root), sort_keys=True, separators=(",", ":"))
    except RecursionError:
        span = result.root.span
        print(f"{span.filename}:{span.line}:{span.column}:{span.offset}: PAR_DEPTH_EXCEEDED: "
              "tree exceeds serialization limits", file=sys.stderr)
        return 1
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
