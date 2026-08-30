# RynorLang: initial language design

## Purpose and implementation status

**Experimental design only.** RynorLang is the intended native language of
RynorOS; its source extension is **`.rl`**. No lexer, parser, compiler, runtime,
or language conformance tests are implemented. This document proposes a small
first subset, not a working language or stable specification.

Prefer readable local code, explicit effects, predictable types, and helpful
errors. Familiar punctuation is useful, but compatibility with C, Rust, Python,
or any other language is not a goal. Avoid generics, inheritance, macros,
exceptions, implicit numeric coercions, and package ecosystems in the first subset.

## Small proposed syntax

```text
fn main() {
    let x = 10;
    let y: i64 = 20;
    print("Hello, RynorOS!");
    print(x + y);
}

fn add(a: i64, b: i64): i64 {
    give a + b;
}
```

`examples/hello.rl` is a syntax sample, not a runnable test. The proposed program
entry is one `fn main()` with no parameters and no returned value. Top-level
executable statements are excluded; the initial compilation unit contains only
function declarations. Function declarations may refer to later declarations.

## Lexical rules

- Source is valid UTF-8. Whitespace separates tokens and is otherwise ignored.
  Source locations count lines and Unicode code-point columns starting at 1.
- Comments start with `//` and continue to the end of the line. Block comments
  are excluded initially. Comment markers inside strings are ordinary text.
- Identifiers match `[A-Za-z_][A-Za-z0-9_]*` and are case-sensitive. Unicode
  identifiers are deferred. Keywords are `fn`, `let`, `give`, `when`, `otherwise`,
  `while`, `true`, `false`, `and`, `or`, and `not`; `i64`, `bool`, `str`, `unit`,
  and `use` are reserved. `print` is a reserved runtime name, not a lexer keyword.
- Statements use `;`; blocks use `{ }`. No automatic semicolon insertion.
- Decimal integer literals only; a leading sign is a unary operator. Prefixes,
  digit separators, floating-point literals, and character literals are deferred.

## Types and values

- `i64`: signed 64-bit integer, from -9223372036854775808 to 9223372036854775807.
  Unsuffixed integer literals have this type. The magnitude 9223372036854775808
  is accepted only immediately under unary minus to express the minimum value.
- `bool`: `true` or `false`, not interchangeable with integers.
- `str`: immutable UTF-8 string value; initially only literals and passing/copying
  references to literals are supported. No mutation, indexing, concatenation,
  or allocation API yet. The future ABI must specify pointer/length layout.
- `unit`: no returned value; omitted function result annotations mean `unit`.
  It is not a variable/parameter type in the initial subset.

Other integer widths, raw addresses, aggregates, and manual memory facilities
are deferred. These will be necessary for systems programming and self-hosting,
so the first subset alone cannot implement the entire OS.

## Strings

Double quotes delimit strings. Supported escapes are `\"`, `\\`, `\n`, `\r`,
and `\t`. Unknown escapes, invalid UTF-8, and unescaped newlines are errors.
No interpolation or embedded source evaluation. Literal storage lives for the
program lifetime; a full string ownership model is a later design decision.

## Variables and arithmetic

`let name = expression;` declares an initialized, mutable local; an optional
annotation as in `let count: i64 = 0;` must match the initializer. No globals or
uninitialized variables. `count = count + 1;` assigns an existing local.
Assignment is a statement, not an expression. Lexical block scope applies;
duplicate or shadowing names within overlapping scopes are rejected initially.

Integer operators are `+`, `-`, `*`, `/`, `%`, and unary `-`. Division truncates
toward zero; remainder has the dividend's sign. Overflow, division/remainder
by zero, and minimum-integer divided/remainder by -1 are errors, never silent
wraparound. Statically determined violations produce compile errors; dynamic
ones require a defined runtime trap once that runtime exists.

From highest to lowest precedence: calls/parentheses; unary `-` and `not`;
`* / %`; `+ -`; one comparison (`< <= > >= == !=`); `and`; `or`.
Arithmetic operators associate left; unary operators nest right. Comparisons
cannot be chained. Ordered comparisons accept `i64`; equality accepts matching
`i64` or `bool`. String equality is deferred. Operands/arguments evaluate left
to right; boolean `and`/`or` short-circuit and require booleans.

## Functions

Use `fn name(parameter: type, ...): result { ... }`. Parameters are immutable
locals; function names are unique and functions cannot be nested. No overloading,
default arguments, variadics, or function values. Argument types must match.
`give expression;` returns a value; `give;` returns from a `unit` function.
Non-unit functions require a return on every reachable path; unit functions can
fall through. Calls are expressions; a call can also be a standalone statement.
Other expression statements are excluded. Recursion is permitted in the design,
but stack limits and failure handling need a real runtime contract.

`print(value);` is the sole proposed initial output intrinsic, accepting one
`str`, `i64`, or `bool` and returning `unit`. Intended output is the string's
UTF-8 bytes, decimal integer text, or `true`/`false`, followed by a newline.
This is a future runtime service: no host `print` wrapper or compiler exists here.

## Conditionals and loops

```text
fn main() {
    let count = 0;
    while count < 3 {
        when count == 0 {
            print("start");
        } otherwise {
            print(count);
        }
        count = count + 1;
    }
}
```

`when condition { ... }` optionally takes `otherwise { ... }`; conditions must
be `bool`. `while condition { ... }` checks its boolean condition before each
iteration. Blocks are mandatory. These are statements, not expressions.
No `for`, `break`, `continue`, pattern matching, or implicit truthiness initially.

## Imports/modules

**Deferred experimental extension, excluded from the initial subset.** One `.rl`
file is one compilation unit. Reserve `use` for a possible future module form
such as `use console;`, but reject it until module resolution, exports, cycles,
and build inputs are specified. Do not silently map names onto host packages.

## Errors and public interfaces

Planned diagnostics include source path, line, column, stage, and explanation.
Reject invalid tokens, unterminated strings, syntax errors, unknown names,
wrong argument counts/types, missing returns, and unsupported constructs.
Failed compilation must return a nonzero exit status and must not publish a
usable output artifact. Runtime failure must be distinct from compile failure.
Error codes, compiler CLI, AST schema, binary format, and runtime ABI are not yet
public interfaces; they must be specified at their implementation milestones.

## Invariants, tests, and known limitations

Invariants: `.rl` identifies source; no implicit coercions or unsupported-syntax
fallbacks; only actual compilation/execution may be reported as success.
`lexer/`, `parser/`, `ast/`, `compiler/`, and `runtime/` reserve implementation
areas. `tests/` reserves local language fixtures/unit tests; root
`tests/rynorlang/` reserves cross-pass conformance tests. Both are empty today.
Repository tests validate extension metadata and this sample's presence, not
the sample's semantics. Grammar formalization, runtime ABI, OS bindings, memory
facilities, module rules, and security properties remain unimplemented.
