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
from tools.rynorlang.lex import Span, Token, lex, lex_bytes, lex_file


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
    def __init__(self, tokens: tuple[Token, ...]) -> None:
        self.tokens = tokens
        self.index = 0
        self.depth = 0

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
        expression = self.parse_expression()
        semicolon = self.expect("SEMICOLON", "after expression")
        return ParseNode("ExprStmt", _cover(expression.span, semicolon.span), (expression,))

    def parse_let(self) -> ParseNode:
        start = self.take()
        name = self.identifier()
        self.expect("COLON", "between variable name and type")
        type_node = self.parse_type()
        self.expect("EQUAL", "before initializer")
        expression = self.parse_expression()
        end = self.expect("SEMICOLON", "after let statement")
        return ParseNode("LetStmt", _cover(start.span, end.span), (name, type_node, expression), text=name.text)

    def parse_return(self) -> ParseNode:
        start = self.take()
        children: tuple[ParseNode, ...] = ()
        if not self.at("SEMICOLON"):
            children = (self.parse_expression(),)
        end = self.expect("SEMICOLON", "after return statement")
        return ParseNode("ReturnStmt", _cover(start.span, end.span), children)

    def parse_if(self) -> ParseNode:
        self.enter()
        try:
            start = self.take()
            condition = self.parse_expression()
            then_block = self.parse_block()
            children: list[ParseNode] = [condition, then_block]
            if self.match("ELSE"):
                children.append(self.parse_if() if self.at("IF") else self.parse_block())
            return ParseNode("IfStmt", _cover(start.span, children[-1].span), tuple(children))
        finally:
            self.leave()

    def parse_while(self) -> ParseNode:
        start = self.take()
        condition = self.parse_expression()
        body = self.parse_block()
        return ParseNode("WhileStmt", _cover(start.span, body.span), (condition, body))

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
                    arguments.append(self.parse_expression())
                    while self.match("COMMA"):
                        # MUTATION_POINT_CALL_TRAILING_COMMA
                        if self.at("RIGHT_PAREN"):
                            self.fail("PAR_EXPECTED_TOKEN", "trailing comma is not allowed in argument list")
                        arguments.append(self.parse_expression())
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
                expression = self.parse_expression()
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
        filename = tokens[0].span.filename
    return ParseResult(None, ParseDiagnostic("PAR_INVALID_INPUT", message, Span(filename, 1, 1, 0, 0)))


def parse_tokens(tokens: tuple[Token, ...]) -> ParseResult:
    if not isinstance(tokens, tuple) or not tokens:
        return _input_error(tokens, "tokens must be a non-empty tuple")
    if any(not isinstance(token, Token) for token in tokens):
        return _input_error(tokens, "every item must be a lexer Token")
    eof_positions = [index for index, token in enumerate(tokens) if token.kind == "EOF"]
    if eof_positions != [len(tokens) - 1]:
        return _input_error(tokens, "tokens must contain exactly one final EOF")
    previous_end = 0
    for token in tokens:
        span = token.span
        if not isinstance(span, Span):
            return _input_error(tokens, "every token must contain a lexer Span")
        if span.line < 1 or span.column < 1 or span.offset < previous_end or span.length < 0:
            return _input_error(tokens, "token spans must be ordered and non-overlapping")
        previous_end = span.offset + span.length
    parser = _Parser(tokens)
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


def _from_lex(result: object) -> ParseResult:
    diagnostic = getattr(result, "diagnostic", None)
    if diagnostic is not None:
        if not isinstance(diagnostic, LexDiagnostic):
            return _input_error((), "lexer returned an invalid diagnostic")
        code = "PAR_FILE_TOO_LARGE" if diagnostic.code == "LEX_FILE_TOO_LARGE" else "PAR_LEX_ERROR"
        return ParseResult(None, ParseDiagnostic(code, diagnostic.message, diagnostic.span, got_kind=diagnostic.code))
    return parse_tokens(result.tokens)


def parse(source: str, filename: str = "<input>") -> ParseResult:
    if not isinstance(source, str) or not isinstance(filename, str):
        return _input_error((), "source and filename must be strings")
    return _from_lex(lex(source, filename))


def parse_bytes(data: bytes, filename: str = "<input>") -> ParseResult:
    if not isinstance(data, bytes) or not isinstance(filename, str):
        return _input_error((), "data must be bytes and filename must be a string")
    return _from_lex(lex_bytes(data, filename))


def parse_file(path: str | Path) -> ParseResult:
    try:
        return _from_lex(lex_file(path))
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
    args = command.parse_args(argv)
    result = parse_file(args.source)
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
