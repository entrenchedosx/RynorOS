# RynorLang

RynorLang is the planned native language of RynorOS and uses the `.rl` source
extension. Stages 12 and 13 provide deterministic host-side bootstrap tooling:
a lexer and parser. They do not compile or execute programs.

## Lexical subset

- ASCII source, at most 1 MiB; `//` line comments only.
- Keywords: `fn let if else while return true false int bool str`.
- Identifiers: `[A-Za-z_][A-Za-z0-9_]*`.
- Unsuffixed decimal integer magnitudes through `9223372036854775807`.
- Double-quoted strings with only `\\`, `\"`, `\n`, and `\t` escapes.
- Maximal-munch `== != <= >= && || ->`, followed by single-character
  `+ - * / % ! = < > ( ) { } ; , :` tokens.
- Every token and diagnostic has a filename, one-based line/column, zero-based
  byte offset, and byte length. Scanning stops at the first error.

The sole implementation is `tools/rynorlang/lex.py`. The `rynorlang/lexer/`
directory is reserved for a future self-hosted implementation.

## Stage 13 syntax

```rl
fn add(a: int, b: int): int {
    return a + b;
}

fn main() {
    let total: int = add(10, 20);
    if !false && total > 0 {
        print("Hello, RynorOS!");
    }
}
```

The parser recognizes functions, typed parameters and `let` declarations,
blocks, `return`, `if`/`else`, `while`, expression statements, calls, literals,
and the documented unary/binary expression grammar. Optional function return
types use `:`. Parameter and call argument lists do not allow trailing commas.
Binary precedence is `||`, `&&`, equality, relational, additive, then
multiplicative; unary `-` and `!` bind most tightly.

The parser produces a frozen temporary syntax tree with lexer spans and stops at
the first `PAR_*` diagnostic. Nesting is bounded at 256. The implementation is
`tools/rynorlang/parse.py`; `rynorlang/parser/` remains reserved.

## Host APIs

```text
lex / lex_bytes / lex_file -> LexResult
parse / parse_bytes / parse_file / parse_tokens -> ParseResult
```

Both tools require Python 3.10+ and only the standard library. Their CLIs emit
deterministic JSON on success and a diagnostic on stderr on failure. The lexer
has 49 repository tests with 16 valid and 19 invalid fixtures. The parser has 52
repository tests with 14 valid and 21 invalid fixtures, including five live
mutation checks.

## Status and limitations

Implemented: lexical recognition, source spans, syntax recognition, temporary
tree construction, deterministic host CLI output, and invalid-input diagnostics.

Not implemented: a stable semantic AST, name resolution, type checking,
modules/imports, compiler, object format, linker, runtime, OS bindings, native
applications, self-hosting, or execution. A parsed call such as `print(...)`
does not establish that `print` exists.
