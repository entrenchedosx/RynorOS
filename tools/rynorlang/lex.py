#!/usr/bin/env python3
"""Deterministic host-side lexer for the Stage 12 RynorLang subset."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


MAX_SOURCE_BYTES = 1024 * 1024
MAX_I64_TEXT = "9223372036854775807"

KEYWORDS = {
    "fn": "FN",
    "let": "LET",
    "if": "IF",
    "else": "ELSE",
    "while": "WHILE",
    "return": "RETURN",
    "true": "TRUE",
    "false": "FALSE",
    "int": "INT_TYPE",
    "bool": "BOOL_TYPE",
    "str": "STR_TYPE",
}

DOUBLE_TOKENS = {
    "==": "EQ_EQ",
    "!=": "BANG_EQ",
    "<=": "LESS_EQ",
    ">=": "GREATER_EQ",
    "&&": "AND_AND",
    "||": "OR_OR",
    "->": "ARROW",
}

SINGLE_TOKENS = {
    "+": "PLUS",
    "-": "MINUS",
    "*": "STAR",
    "/": "SLASH",
    "%": "PERCENT",
    "!": "BANG",
    "=": "EQUAL",
    "<": "LESS",
    ">": "GREATER",
    "(": "LEFT_PAREN",
    ")": "RIGHT_PAREN",
    "{": "LEFT_BRACE",
    "}": "RIGHT_BRACE",
    ";": "SEMICOLON",
    ",": "COMMA",
    ":": "COLON",
}

ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}


@dataclass(frozen=True)
class Span:
    filename: str
    line: int
    column: int
    offset: int
    length: int


@dataclass(frozen=True)
class Token:
    kind: str
    lexeme: str
    span: Span
    value: str | None = None


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    span: Span


@dataclass(frozen=True)
class LexResult:
    tokens: tuple[Token, ...]
    diagnostic: Diagnostic | None

    @property
    def ok(self) -> bool:
        return self.diagnostic is None


class _Scanner:
    def __init__(self, source: str, filename: str) -> None:
        self.source = source
        self.filename = filename
        self.index = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []

    def span(self, line: int, column: int, offset: int, length: int) -> Span:
        return Span(self.filename, line, column, offset, length)

    def advance(self) -> str:
        char = self.source[self.index]
        self.index += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def fail(
        self,
        code: str,
        message: str,
        line: int,
        column: int,
        offset: int,
        length: int,
    ) -> LexResult:
        return LexResult(
            tuple(self.tokens),
            Diagnostic(code, message, self.span(line, column, offset, length)),
        )

    def scan(self) -> LexResult:
        while self.index < len(self.source):
            char = self.source[self.index]
            if ord(char) > 0x7F:
                return self.fail(
                    "LEX_INVALID_CHAR",
                    "RynorLang Stage 12 source is ASCII-only",
                    self.line,
                    self.column,
                    self.index,
                    1,
                )
            if char in " \t\r\n":
                self.advance()
                continue

            if self.source.startswith("//", self.index):
                self.advance()
                self.advance()
                while self.index < len(self.source) and self.source[self.index] != "\n":
                    if ord(self.source[self.index]) > 0x7F:
                        return self.fail(
                            "LEX_INVALID_CHAR",
                            "RynorLang Stage 12 source is ASCII-only",
                            self.line,
                            self.column,
                            self.index,
                            1,
                        )
                    self.advance()
                continue

            line = self.line
            column = self.column
            offset = self.index

            if _is_identifier_start(char):
                self.advance()
                while self.index < len(self.source) and _is_identifier_continue(
                    self.source[self.index]
                ):
                    self.advance()
                lexeme = self.source[offset:self.index]
                self.tokens.append(
                    Token(
                        KEYWORDS.get(lexeme, "IDENTIFIER"),
                        lexeme,
                        self.span(line, column, offset, self.index - offset),
                    )
                )
                continue

            if "0" <= char <= "9":
                self.advance()
                while self.index < len(self.source) and "0" <= self.source[self.index] <= "9":
                    self.advance()
                lexeme = self.source[offset:self.index]
                normalized = lexeme.lstrip("0") or "0"
                if len(normalized) > len(MAX_I64_TEXT) or (
                    len(normalized) == len(MAX_I64_TEXT) and normalized > MAX_I64_TEXT
                ):
                    return self.fail(
                        "LEX_INT_OVERFLOW",
                        "decimal integer exceeds signed 64-bit maximum",
                        line,
                        column,
                        offset,
                        self.index - offset,
                    )
                self.tokens.append(
                    Token(
                        "INTEGER",
                        lexeme,
                        self.span(line, column, offset, self.index - offset),
                    )
                )
                continue

            if char == '"':
                error = self.scan_string(line, column, offset)
                if error is not None:
                    return error
                continue

            pair = self.source[self.index:self.index + 2]
            if pair in DOUBLE_TOKENS:
                self.advance()
                self.advance()
                self.tokens.append(
                    Token(DOUBLE_TOKENS[pair], pair, self.span(line, column, offset, 2))
                )
                continue

            if char in SINGLE_TOKENS:
                self.advance()
                self.tokens.append(
                    Token(SINGLE_TOKENS[char], char, self.span(line, column, offset, 1))
                )
                continue

            return self.fail(
                "LEX_INVALID_CHAR",
                f"invalid character 0x{ord(char):02x}",
                line,
                column,
                offset,
                1,
            )

        self.tokens.append(
            Token("EOF", "", self.span(self.line, self.column, self.index, 0))
        )
        return LexResult(tuple(self.tokens), None)

    def scan_string(self, line: int, column: int, offset: int) -> LexResult | None:
        self.advance()
        decoded: list[str] = []
        while self.index < len(self.source):
            char = self.source[self.index]
            if char == '"':
                self.advance()
                lexeme = self.source[offset:self.index]
                self.tokens.append(
                    Token(
                        "STRING",
                        lexeme,
                        self.span(line, column, offset, self.index - offset),
                        "".join(decoded),
                    )
                )
                return None
            if ord(char) > 0x7F:
                return self.fail(
                    "LEX_INVALID_CHAR",
                    "RynorLang Stage 12 source is ASCII-only",
                    self.line,
                    self.column,
                    self.index,
                    1,
                )
            if char in "\r\n":
                return self.fail(
                    "LEX_UNTERMINATED_STRING",
                    "unescaped newline in string",
                    line,
                    column,
                    offset,
                    self.index - offset,
                )
            if char == "\\":
                escape_line = self.line
                escape_column = self.column
                escape_offset = self.index
                self.advance()
                if self.index >= len(self.source):
                    return self.fail(
                        "LEX_UNTERMINATED_STRING",
                        "unterminated string",
                        line,
                        column,
                        offset,
                        self.index - offset,
                    )
                escape = self.source[self.index]
                if escape not in ESCAPES:
                    return self.fail(
                        "LEX_INVALID_ESCAPE",
                        f"unsupported escape \\{escape}",
                        escape_line,
                        escape_column,
                        escape_offset,
                        2,
                    )
                self.advance()
                decoded.append(ESCAPES[escape])
                continue
            decoded.append(self.advance())

        return self.fail(
            "LEX_UNTERMINATED_STRING",
            "unterminated string",
            line,
            column,
            offset,
            self.index - offset,
        )


def _is_identifier_start(char: str) -> bool:
    return char == "_" or "A" <= char <= "Z" or "a" <= char <= "z"


def _is_identifier_continue(char: str) -> bool:
    return _is_identifier_start(char) or "0" <= char <= "9"


def _input_error(filename: str, code: str, message: str, offset: int = 0) -> LexResult:
    return LexResult((), Diagnostic(code, message, Span(filename, 1, 1, offset, 1)))


def _position_at(data: bytes, offset: int) -> tuple[int, int]:
    prefix = data[:offset]
    line = prefix.count(b"\n") + 1
    last_newline = prefix.rfind(b"\n")
    column = len(prefix) + 1 if last_newline < 0 else len(prefix) - last_newline
    return line, column


def _too_large(filename: str, data: bytes) -> LexResult:
    line, column = _position_at(data, MAX_SOURCE_BYTES)
    return LexResult(
        (),
        Diagnostic(
            "LEX_FILE_TOO_LARGE",
            "source exceeds the 1 MiB Stage 12 limit",
            Span(filename, line, column, MAX_SOURCE_BYTES, 1),
        ),
    )


def lex(source: str, filename: str = "<input>") -> LexResult:
    if not isinstance(source, str):
        return _input_error(filename, "LEX_INVALID_INPUT", "source must be text")
    # The size bound is bytes. For the only accepted alphabet, ASCII, a
    # character count is already the byte count, so the bound below is exact
    # without encoding. Non-ASCII library input is lexically invalid, so it
    # must reach the scanner's own LEX_INVALID_CHAR (lone surrogates included,
    # never a UnicodeEncodeError from an eager encode). lex_bytes() owns
    # byte-exact handling for callers that already hold encoded bytes.
    if source.isascii() and len(source) > MAX_SOURCE_BYTES:
        return _too_large(filename, source.encode("ascii")[:MAX_SOURCE_BYTES + 1])
    return _Scanner(source, filename).scan()


def lex_bytes(data: bytes, filename: str = "<input>") -> LexResult:
    if not isinstance(data, bytes):
        return _input_error(filename, "LEX_INVALID_INPUT", "source must be bytes")
    if len(data) > MAX_SOURCE_BYTES:
        return _too_large(filename, data)
    # Latin-1 preserves each input byte as one character, so the scanner sees
    # non-ASCII bytes in their true source order and byte offsets. They fall
    # through to LEX_INVALID_CHAR like every other unsupported character.
    return _Scanner(data.decode("latin-1"), filename).scan()


def lex_file(path: str | Path) -> LexResult:
    source_path = Path(path)
    with source_path.open("rb") as source_file:
        data = source_file.read(MAX_SOURCE_BYTES + 1)
    return lex_bytes(data, str(source_path))


def _span_json(span: Span) -> dict[str, int | str]:
    return {
        "column": span.column,
        "filename": span.filename,
        "length": span.length,
        "line": span.line,
        "offset": span.offset,
    }


def _token_json(token: Token) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": token.kind,
        "lexeme": token.lexeme,
        "span": _span_json(token.span),
    }
    if token.value is not None:
        record["value"] = token.value
    return record


def _diagnostic_json(diagnostic: Diagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "span": _span_json(diagnostic.span),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tokenize Stage 12 RynorLang source")
    parser.add_argument("source", type=Path)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = lex_file(arguments.source)
    except OSError as error:
        print(f"{arguments.source}: {error}", file=sys.stderr)
        return 2

    if result.diagnostic is not None:
        diagnostic = result.diagnostic
        if arguments.json:
            print(
                json.dumps(
                    {"diagnostic": _diagnostic_json(diagnostic)},
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                file=sys.stderr,
            )
        else:
            span = diagnostic.span
            print(
                f"{span.filename}:{span.line}:{span.column}:{span.offset}: "
                f"{diagnostic.code}: {diagnostic.message}",
                file=sys.stderr,
            )
        return 1

    if arguments.json:
        print(
            json.dumps(
                {"tokens": [_token_json(token) for token in result.tokens]},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        for token in result.tokens:
            span = token.span
            print(
                f"{span.line}:{span.column}:{span.offset}:{span.length} "
                f"{token.kind} {json.dumps(token.lexeme, ensure_ascii=True)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
