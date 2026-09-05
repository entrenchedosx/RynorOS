# RynorLang

RynorLang is the planned native language of RynorOS and uses the `.rl` source
extension. Stages 12-14 provide deterministic host-side bootstrap tooling:
a lexer, parser, and semantic analyzer. Stage 15a adds a typed IR, verifier,
and native backend plus a test-only oracle. Kernel execution remains a later
stage.

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

## Stage 14 semantics

```rl
fn add(a: int, b: int): int {
    return a + b;
}
fn main(): int {
    let x: int = add(1, 2);
    if x > 0 {
        return x;
    }
    return 0;
}
```

The analyzer lowers the temporary tree to a stable JSON-compatible AST schema (`Program, Function, Param, Block, Let, If, While, Return, ExprStmt, BinOp, UnOp, IntLit, BoolLit, StrLit, Var, Call`) with exact spans, deterministic symbol indices, and type checking: `int`/`bool`/`str` only, no implicit conversions, `unit` for missing return types (callable only as `ExprStmt`), `==`/`!=` on same `int`/`bool`/`str`, `<` etc. on `int` only, `&&`/`||` on `bool`, unary `-` on `int` and `!` on `bool`, `let` annotation must match, `if`/`while` cond `bool`, `return` must match, call arity and per-arg types must match, forward function references allowed, locals block-scoped with no shadowing, use-before-declare is an error. Returned dictionaries/lists are mutable and caller-owned; the schema is frozen. Diagnostics are exactly `SEM_UNDECLARED`, `SEM_DUPLICATE`, `SEM_TYPE_MISMATCH`, `SEM_ARITY_MISMATCH`, `SEM_UNKNOWN_FUNCTION`, plus parser-normalized lexical and syntax diagnostics, first-error, with spans. The implementation is `tools/rynorlang/analyze.py`; `rynorlang/ast/` remains reserved.

## Host APIs

```text
lex / lex_bytes / lex_file -> LexResult                    # + edition="shell" for |>
parse / parse_bytes / parse_file / parse_tokens -> ParseResult
analyze / analyze_bytes / analyze_file -> AnalyzeResult  # stable AST + SEM_* or SEM_OK
build_rir / verify_module / dumps / assign_slots  # RIR v1 + shared home slots
emit_asm / compile_source / check_asm  # rynorlangc NASM backend (verified RIR only)
run_rir  # TEST-ONLY differential oracle, never shipped
run_pipeline  # TEST-ONLY shell evaluator (tools/rynorlang/shell.py)
```

All tools require Python 3.10+ and only the standard library. Their CLIs emit
deterministic JSON on success and a diagnostic on stderr on failure
(`--edition v1|shell` on lex/parse/analyze; analyze in shell edition resolves
commands against an explicit stub registry — `DEMO_COMMANDS` for the CLI —
never a fake OS database). The lexer
has 49 repository tests with 16 valid and 19 invalid fixtures. The parser has 54
repository tests with 14 valid and 21 invalid fixtures, including five live
mutation checks. The semantics has 63 repository tests with 12 valid and 20 invalid fixtures, including twenty-one live behavioral mutation checks.
The RIR layer has 48 repository tests with golden text, verifier units,
slot-soundness probes, a branch-dominance test, and builder mutations. The
compiler layer has 39 repository tests with golden ASM, determinism, negative
pre-emit checks, `check_asm` abuse flagging, native differential runs (17 good
+ 3 trap fixtures), and 21 mutation-focused RIR/backend tests. The shell
surface has 47 repository tests with 12 good + 11 bad `shell-edition/`
fixtures covering edition gates, precedence, commands, redirects, typing,
determinism, differential evaluation, and mutation checks.

## Stage 15b shell surface (host-side, edition-gated)

```rl
fn main(): str {
    let x: str = ls |> count;
    let y: str = upper "hi" > "out";
    return y;
}
```

`a |> b |> c` is left-associative at precedence 0 (below `||`). Commands are
honest `Cmd` nodes (never desugared `Call`s): bare words, literals, adjacent
`-flags`, and quoted `>`/`>>` redirect targets using zero new lexer tokens.
Bare words in stage position resolve lexical variables first, then the stub
registry. Every non-final stage is `str`; `unit` pipelines only as `ExprStmt`;
piped input fills a non-head command's first parameter. `a > b` stays a
comparison; `a - b` stays subtraction (only adjacent `a -b` is a command —
an explicit, documented edition difference). The 15a backend rejects the new
kinds (`COMP_V2_UNSUPPORTED`): no shell codegen yet, no userspace execution,
no kernel evaluation.

## Status and limitations

Implemented: lexical recognition, source spans, syntax recognition, temporary
tree construction, stable AST lowering, name resolution, type checking, deterministic symbol indices, invalid-input diagnostics, typed IR (RIR v1)
with dominance verifier and shared slot allocator, NASM backend for the
SysV-subset ABI (`int`/`bool`/`str`/`unit`, spill-everything homes, `ud2`/`int3`
traps), disclosed host harness, and a test-only oracle.

Not implemented: modules/imports, runtime services beyond exact-bytes
`print`, OS bindings, native RynorOS applications, argv, heap allocation,
self-hosting, or in-kernel execution. No type inference, no all-paths
return checking, no shadowing, no builtins beyond `print`.

## Stage 16 host-native programs

```rl
fn main(): int {
    print("hello");
    print(6 * 7);
    return 0;
}
```

`rynorlangc prog.rl --build DIR` writes `DIR/prog.{asm,o,exe}` (plus
`rt_linux.o`); `--run` builds in a temp dir and forwards stdout verbatim.
Entry is `fn main(): int|unit` with no args (argc/argv deferred); exit is
low-8-bit with documented truncation; `print(int/bool/str)` writes exact
bytes (no newline) through the labeled Linux host runtime
(`tools/rynorlang/runtime/rt_linux.asm`, static-only, no heap). Test
executables only: no RynorOS syscalls, no userspace, no self-hosting. See
`docs/design/rynorlang-program-model.md`.
