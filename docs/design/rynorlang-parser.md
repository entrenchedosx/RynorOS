# RynorLang parser design

Status: **implemented for Stage 13 as host-side bootstrap tooling**.

The parser consumes the Stage 12 token stream and produces an immutable,
temporary syntax tree. It performs syntax recognition only: it does not resolve
names, check types, build the eventual stable AST, generate code, or execute
RynorLang. The implementation is the Python 3.10+ standard-library module
`tools/rynorlang/parse.py`; `rynorlang/parser/` remains reserved for the future
self-hosted implementation.

## Frozen grammar

```text
Program       ::= FunctionDef* EOF
FunctionDef   ::= "fn" IDENT "(" ParamList? ")" (":" Type)? Block
ParamList     ::= Param ("," Param)*
Param         ::= IDENT ":" Type
Type          ::= "int" | "bool" | "str"
Block         ::= "{" Statement* "}"
Statement     ::= LetStmt | ReturnStmt | IfStmt | WhileStmt | ExprStmt | Block
LetStmt       ::= "let" IDENT ":" Type "=" Expr ";"
ReturnStmt    ::= "return" Expr? ";"
IfStmt        ::= "if" Expr Block ("else" (Block | IfStmt))?
WhileStmt     ::= "while" Expr Block
ExprStmt      ::= Expr ";"
Expr          ::= OrExpr
OrExpr        ::= AndExpr ("||" AndExpr)*
AndExpr       ::= EqualityExpr ("&&" EqualityExpr)*
EqualityExpr  ::= RelationalExpr (("==" | "!=") RelationalExpr)*
RelationalExpr ::= AdditiveExpr (("<" | ">" | "<=" | ">=") AdditiveExpr)*
AdditiveExpr  ::= MultiplicativeExpr (("+" | "-") MultiplicativeExpr)*
MultiplicativeExpr ::= UnaryExpr (("*" | "/" | "%") UnaryExpr)*
UnaryExpr     ::= ("-" | "!") UnaryExpr | PostfixExpr
PostfixExpr   ::= PrimaryExpr ("(" ArgList? ")")*
ArgList       ::= Expr ("," Expr)*
PrimaryExpr   ::= IDENT | INTEGER | STRING | "true" | "false" | "(" Expr ")"
```

Return types use `:`. The lexer still recognizes `->`, but Stage 13 rejects it
in function signatures. Neither parameter lists nor call argument lists permit
a trailing comma. Binary operators are left-associative. Precedence increases
from `||` through `&&`, equality, relational, additive, multiplicative, and
finally right-associative unary `-` and `!`. An `else` binds to the nearest
unmatched `if`.

## Tree and spans

`ParseNode` is a frozen dataclass with `kind`, lexer `Span`, immutable `children`,
and optional `text`/`value`. Node kinds are `Program`, `FunctionDef`,
`ParamList`, `Param`, `Type`, `Block`, the five statement kinds, the six binary
precedence kinds, `UnaryExpr`, `CallExpr`, `ArgList`, `GroupExpr`, and the four
leaf kinds. Spans retain filename, one-based line and column, zero-based ASCII
byte offset, and covered byte length. EOF is never a tree child.

The tree is deliberately temporary. Stage 14 may introduce a semantic AST with
different node organization after the syntax contract has proved stable.

## API and diagnostics

```text
parse(source, filename="<input>") -> ParseResult
parse_bytes(data, filename="<input>") -> ParseResult
parse_file(path) -> ParseResult
parse_tokens(tokens) -> ParseResult
```

`ParseResult` is frozen and contains exactly one of a root or diagnostic. The
parser stops at the first error. Diagnostic codes are:

- `PAR_LEX_ERROR` and `PAR_FILE_TOO_LARGE` for lexer failures;
- `PAR_INVALID_INPUT` for malformed API input/token streams;
- `PAR_UNEXPECTED_TOKEN` and `PAR_UNEXPECTED_EOF`;
- `PAR_EXPECTED_TOKEN` for a required grammar token;
- `PAR_DEPTH_EXCEEDED` when nesting would exceed 256.

Every diagnostic has a real lexer span. The implementation validates that a
supplied token tuple is ordered, non-overlapping, and ends in exactly one EOF.
It temporarily supplies CPython with enough recursion headroom while holding a
module lock, restores the previous process limit before returning, and converts
an unexpected interpreter recursion failure into `PAR_DEPTH_EXCEEDED`.

## Invariants and tests

- Lexer failure prevents parsing and no partial tree is returned.
- No successful parse leaves a non-EOF token unconsumed.
- Return-type colon and comma rules are enforced in the parser, not fixtures.
- Tree and result objects cannot be mutated after construction.
- Nesting counters are balanced through `finally` blocks.
- CLI output is deterministic sorted JSON; a diagnostic exits nonzero.

The Stage 13 repository module contains 52 strict tests with 14 valid and 21
invalid fixtures. It covers the grammar, all precedence levels, associativity,
spans, API validation, depth, `!`, deterministic CLI output, and five live
mutations: parameter trailing comma, return-type token, depth guard, precedence,
and trailing top-level input.

## Limitations

This is ASCII-only bootstrap tooling inherited from Stage 12. There are no
modules/imports, Unicode identifiers, block comments, floating point values,
character literals, arrays, structs, generics, semantic validation, compiler,
runtime binding, kernel integration, or language execution. QEMU and physical
hardware do not execute this parser; the kernel suite is only a regression gate.
