# RynorLang

RynorLang is the planned native language of RynorOS. Source files use the
`.rl` extension. Stage 12 implements only a deterministic host-side lexer; it
does not parse, compile, execute, or validate the semantics of a program.

## Stage 12 lexical contract

The Stage 12 source format is deliberately small and frozen:

- Input is ASCII and is limited to 1 MiB (1,048,576 bytes).
- Spaces, tabs, carriage returns, and line feeds are whitespace.
- `//` begins a line comment. Block comments are not recognized.
- Identifiers match `[A-Za-z_][A-Za-z0-9_]*` and are case-sensitive.
- Keywords are `fn`, `let`, `if`, `else`, `while`, `return`, `true`, `false`,
  `int`, `bool`, and `str`.
- Integer tokens contain decimal digits only. Their magnitude must not exceed
  `9223372036854775807`; a leading sign is a separate token.
- Strings use double quotes and support only `\\`, `\"`, `\n`, and `\t`.
- Two-character operators use maximal munch: `==`, `!=`, `<=`, `>=`, `&&`,
  `||`, and `->`.
- Single-character tokens are `+ - * / % = < > ( ) { } ; , :`.

Every token has a source filename, one-based line and column, zero-based byte
offset, and byte length. Because Stage 12 is ASCII-only, character and byte
advancement are identical. The lexer stops at the first diagnostic. Invalid
characters, integer overflow, unterminated strings, invalid escapes, invalid
input types, and oversized files have distinct diagnostic codes.

The implementation is [tools/rynorlang/lex.py](../tools/rynorlang/lex.py).
`rynorlang/lexer/` remains a reserved language-project directory and does not
contain a second implementation.

## Small planned syntax

The following is a syntax sample, not an executable program:

```text
fn add(a: int, b: int) -> int {
    return a + b;
}

fn main() {
    let x: int = 10;
    let y: int = 20;
    if x < y {
        print("Hello, RynorOS!");
    } else {
        while false {}
    }
}
```

Stage 13 will define parsing and a temporary syntax tree. Name resolution,
types, control-flow validity, modules, compiler output, runtime behavior, OS
bindings, and self-hosting remain future work. In particular, Stage 12 does
not establish that `print`, function calls, declarations, or expressions are
semantically valid; it only produces tokens.

## Interfaces and ownership

The host module exposes immutable `Span`, `Token`, `Diagnostic`, and
`LexResult` records plus:

- `lex(source: str, filename="<input>")`
- `lex_bytes(data: bytes, filename="<input>")`
- `lex_file(path)`

The caller owns the input. Results own ordinary immutable Python strings and
tuples. Lexical failures are returned in `LexResult.diagnostic`; filesystem
I/O failures from `lex_file` remain `OSError`. The CLI exits 0 on successful
tokenization, 1 on a lexical diagnostic, and 2 on an I/O/usage failure.

## Tests and limitations

Repository tests use 16 valid and 19 invalid fixtures plus direct boundary and
API checks. They cover exact keywords, identifier boundaries, maximal munch,
spans, all diagnostics, exact-size acceptance, over-size rejection, and
deterministic CLI output.

The lexer is a Python 3.10+ bootstrap dependency using only the standard
library. It is not part of Rynorkernel or the boot image. Unicode, block
comments, integer prefixes, digit separators, floating point, character
literals, interpolation, parsing, AST generation, compilation, and execution
are not implemented.
