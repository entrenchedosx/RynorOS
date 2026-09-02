# RynorLang lexer design

Status: **implemented for Stage 12**. This document freezes the lexical subset.
Changing a token spelling or lexical rule requires an explicit later-stage
design change.

## Purpose and scope

The lexer converts bounded `.rl` source into tokens with exact source spans. It
is a Python 3.10+ bootstrap tool at `tools/rynorlang/lex.py`, uses only the
standard library, and has no kernel, QEMU, or host-OS-runtime dependency beyond
reading a requested file. There is intentionally no duplicate implementation
under `rynorlang/lexer/`.

Stage 12 performs no parsing, AST construction, semantic analysis, code
generation, linking, loading, or execution.

## Frozen source rules

- Encoding: ASCII only.
- Maximum input: 1,048,576 bytes; file input reads at most one byte beyond the
  limit and larger input is rejected before scanning.
- Whitespace: space, tab, carriage return, and line feed.
- Comments: `//` through the next line feed or end of input. No block comments.
- Identifiers: `[A-Za-z_][A-Za-z0-9_]*`, case-sensitive.
- Keywords: `fn let if else while return true false int bool str`.
- Integers: maximal decimal digit runs, from `0` through
  `9223372036854775807`. A sign is a separate token.
- Strings: double-quoted; only `\\`, `\"`, `\n`, and `\t` are escapes.
  Literal carriage returns and line feeds are not allowed inside strings.
- Double operators: `== != <= >= && || ->`, selected before single characters.
- Single tokens: `+ - * / % = < > ( ) { } ; , :`.

Words not in the keyword list—including `give`, `when`, `otherwise`, `and`,
`or`, `not`, `i64`, `unit`, `use`, and `print`—are ordinary identifiers.

## Token and diagnostic model

`Span` contains the source filename, one-based line and column, zero-based byte
offset, and byte length. ASCII makes byte and character advancement identical.
Only line feed advances the line and resets the column; carriage return is
whitespace and advances the column.

`Token` contains an exact kind, exact source lexeme, span, and an optional
decoded string value. Successful results end with one zero-length `EOF` token.

`LexResult` contains an immutable token tuple and either no diagnostic or one
`Diagnostic`. Token kinds and diagnostic codes use ordinary exact strings—no
aliases or compatibility equality.

The lexer stops at the first diagnostic. Tokens before the fault may be
present; the invalid input is represented only by the diagnostic and no later
input is scanned.

Implemented diagnostic codes:

| Code | Meaning |
|---|---|
| `LEX_INVALID_CHAR` | Non-ASCII input or a character outside the frozen tokens |
| `LEX_INT_OVERFLOW` | Decimal magnitude exceeds signed 64-bit maximum |
| `LEX_UNTERMINATED_STRING` | EOF or an unescaped newline occurs before `"` |
| `LEX_INVALID_ESCAPE` | A backslash is followed by a non-frozen escape |
| `LEX_FILE_TOO_LARGE` | Input exceeds 1 MiB |
| `LEX_INVALID_INPUT` | Library caller supplies the wrong input type |

## Interfaces and failure semantics

```python
lex(source: str, filename: str = "<input>") -> LexResult
lex_bytes(data: bytes, filename: str = "<input>") -> LexResult
lex_file(path: str | Path) -> LexResult
```

The library does not raise for lexical errors or invalid argument types.
`lex_file` deliberately allows filesystem `OSError` to remain distinct from a
lexical diagnostic. The caller retains its input; returned records contain
immutable strings and tuples.

The provisional CLI is:

```text
python tools/rynorlang/lex.py [--json] source.rl
```

Exit status is 0 on success, 1 on a lexical diagnostic, and 2 on usage or file
I/O failure. Successful output is written to stdout. Diagnostics are written to
stderr. JSON keys and token ordering are deterministic.

## Invariants

1. No input beyond 1 MiB is scanned.
2. Every successful token span covers exactly its original ASCII bytes.
3. Token spans are ordered and non-overlapping.
4. Only the frozen keyword set produces keyword kinds.
5. A valid string is one token; invalid strings cannot produce a `STRING`.
6. The first error ends scanning and has an exact code and span.
7. The same input and filename produce equal results and byte-identical CLI
   output.
8. No output artifact representing parsed or executable code is produced.

## Tests

`tests/repository/test_rynorlang_lexer.py` contains 49 strict test methods.
Fixtures comprise 16 valid and 19 invalid `.rl` files. Direct tests additionally
cover exact-size acceptance, pre-scan oversized rejection, ASCII rejection
inside and outside strings, public API types, exact diagnostic identity,
first-error termination, and CLI stream/exit behavior.

Mutation checks replace each of the four required diagnostic paths
(`LEX_INVALID_CHAR`, `LEX_INT_OVERFLOW`, `LEX_UNTERMINATED_STRING`, and
`LEX_INVALID_ESCAPE`) in a temporary copy; the corresponding targeted tests
must fail.

## Known limitations

Unicode, block comments, numeric bases, separators, floating point, character
literals, interpolation, and additional punctuation are unsupported. The
lexer is bootstrap tooling, not self-hosted RynorLang. Physical hardware and
QEMU are irrelevant to lexer correctness, although the existing kernel suite
continues to run as a regression gate.
