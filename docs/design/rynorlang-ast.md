# RynorLang stable AST and semantics design

Status: **implemented for Stage 14**. This document freezes the stable AST shape, scope and type rules, and semantic diagnostic codes that become the input contract for Stage 15 (compiler). Changing a node kind, field name, type rule, or diagnostic code requires an explicit later-stage design change.

## Purpose

State the responsibility, non-goals, and dependencies. This section satisfies `docs/design/subsystem-template.md#Purpose`.

**Responsibility.** Lower the Stage 13 temporary syntax tree (`tools/rynorlang/parse.py` `ParseNode`) into a stable, JSON-compatible AST and perform name resolution and type checking for the syntactic subset. The result is host-side and deterministic, with exact source spans, resolved symbols, inferred types, and a single first-error diagnostic. The AST schema is frozen, but its Python dictionary/list representation is caller-owned and mutable. The implementation is the Python 3.10+ standard-library module `tools/rynorlang/analyze.py`; `rynorlang/ast/` remains reserved for the future self-hosted implementation.

**Non-goals.** No interpretation, code generation, constant folding, optimization, module/import resolution, inference, all-paths return checking, or execution. No `print` builtin (calls to `print` are `SEM_UNKNOWN_FUNCTION` until Stage 16). No implicit conversions, no composite types, no generics, no shadowing.

**Dependencies.** The frozen lexer (`tools/rynorlang/lex.py`, `docs/design/rynorlang-lexer.md`) and frozen parser (`tools/rynorlang/parse.py`, `docs/design/rynorlang-parser.md`) are the sole front ends. The analyzer consumes `ParseResult.root` (`Program` temporary node) and `Span` values defined there. Source rules (ASCII, 1 MiB, `//` comments) and bounds (depth 256, 1 MiB) are inherited. Parser diagnostics pass through unchanged; lexer failures retain their message/span and the parser's documented `PAR_LEX_ERROR` or `PAR_FILE_TOO_LARGE` normalization.

## Frozen type system

Source types are exactly `int` (signed 64-bit `i64`), `bool`, `str`. No implicit conversions, no subtyping, no composite or generic types. Every expression has a single static type in `{int,bool,str}` or the special `unit` for functions without a return type (see call/return rules).

| Construct | Operand types | Result type | Notes |
|---|---|---|---|
| `+ - * / %` | `int,int` | `int` | `%` is remainder, division by zero is runtime, not semantic |
| `== !=` | `T,T` where `T ∈ {int,bool,str}` | `bool` | both sides same `T`; `str == str` ok, `int == bool` mismatch |
| `< > <= >=` | `int,int` | `bool` | `str < str` and `bool < bool` are mismatches |
| `&& \|\|` | `bool,bool` | `bool` | |
| unary `-` | `int` | `int` | |
| unary `!` | `bool` | `bool` | lexer emits `BANG` for single `!`; `!=` is not `!` |
| `let x: T = e` | `type(e) == T` | `T` (symbol `x`) | annotation mandatory |
| `if`/`while` condition | `bool` | — | |
| `return e?` | `type(e) == ret_type` or `e is None && ret_type is None` | — | bare `return;` only in `ret_type is None` |
| `call f(args)` | callee is an `Identifier` naming a known function, `len(args)==len(params)`, each `type(arg_i)==type(param_i)` | `ret_type` of `f` or `unit` | `unit` callable only as `ExprStmt`, never as value; a non-identifier callee (grouped, chained, literal) is `SEM_UNKNOWN_FUNCTION` with the honest "called expression is not a function name" message at the callee span — it is never resolved through its text |

A function without a return type yields `unit` and must not be used as a value (e.g., `let x: int = noRet();` is a type mismatch, `let x: int = f()` where `f` returns `int` is ok).

## Frozen scopes and name resolution

* **Functions:** global, unique, forward-reference allowed. Duplicate function names in the same program are `SEM_DUPLICATE`. Calls resolve to the global function table regardless of textual order.
* **Locals (params and `let`):** block-scoped, visible from the point of declaration to the end of the enclosing `Block`. A `Block` introduces a new scope; nested blocks nest scopes.
* **No shadowing:** redeclaring a name that already exists in *any* enclosing scope (including outer blocks and the global function table) is `SEM_DUPLICATE`. This applies to `let` vs `let`, `let` vs `param`, `let` vs function, and `param` vs `param`/`function`.
* **Use before declaration:** a `Var` that is not found in the current scope chain at the point of use is `SEM_UNDECLARED`. Because locals are visible only after declaration, `let x: int = x;` is undeclared for the initializer, and forward local references are errors. Function forward references *are* allowed for `Call` (`SEM_UNKNOWN_FUNCTION` only if no global function of that name exists).

Scopes are implemented as a stack of dictionaries `name -> (symbol_index, type, span)`. The global function map is built in a first pass; the second pass walks blocks depth-first, pushing/popping scopes, allocating deterministic declaration-order indices.

## Stable AST contract

Lowering is a deterministic pass from the Stage 13 temporary tree to frozen stable node kinds. The temporary tree remains parser-internal and is never emitted. The stable AST is the Stage 15 contract; its kinds/fields are frozen.

**Node kinds (exactly these 16, no aliases):** `Program`, `Function`, `Param`, `Block`, `Let`, `If`, `While`, `Return`, `ExprStmt`, `BinOp`, `UnOp`, `IntLit`, `BoolLit`, `StrLit`, `Var`, `Call`. Field names are exact and lower-camel, no hedging.

| Kind | Fields | Span | Notes |
|---|---|---|---|
| `Program` | `functions: Function[]` | cover `Program` span | `functions` in source order |
| `Function` | `name: string`, `params: Param[]`, `ret_type: "int"|"bool"|"str"|null`, `body: Block`, `symbol: int` | `FunctionDef` span | `symbol` is global function index (0..n-1) |
| `Param` | `name: string`, `type: "int"|"bool"|"str"`, `symbol: int` | `Param` span | `symbol` is local declaration index (global deterministic) |
| `Block` | `stmts: (Let|If|While|Return|ExprStmt|Block)[]` | `Block` span | |
| `Let` | `name: string`, `type: "int"|"bool"|"str"`, `init: Expr`, `symbol: int` | `LetStmt` span | `init` type must equal `type` |
| `If` | `cond: Expr`, `then: Block`, `else: Block|If|null` | `IfStmt` span | `cond` must be `bool`; `else` binds nearest |
| `While` | `cond: Expr`, `body: Block` | `WhileStmt` span | `cond` must be `bool` |
| `Return` | `value: Expr|null` | `ReturnStmt` span | `value` null means bare `return;` |
| `ExprStmt` | `expr: Expr` | `ExprStmt` span | `expr` may be `Call` returning `unit` (allowed) or any non-unit expr; a `unit` Call used as value elsewhere is an error, but as `ExprStmt` it is the only allowed `unit` context |
| `BinOp` | `op: string`, `left: Expr`, `right: Expr`, `type: "int"|"bool"` | cover `left..right` | `op` is lexeme (`+`, `&&`, `==`, `<`, etc.), `type` is result type |
| `UnOp` | `op: string`, `operand: Expr`, `type: "int"|"bool"` | cover `op..operand` | `op` is `"-"` or `"!"` |
| `IntLit` | `value: string` (exact decimal text from source), `type: "int"` | `IntegerLiteral` span | leading zeroes are retained; the value is not converted to a host int |
| `BoolLit` | `value: bool`, `type: "bool"` | `BooleanLiteral` span | |
| `StrLit` | `value: string` (unescaped, `Token.value`), `lexeme: string` (raw `"..."`), `type: "str"` | `StringLiteral` span | `value` is `Token.value` (e.g., `\n` → newline) |
| `Var` | `name: string`, `symbol: int`, `type: "int"|"bool"|"str"` | `Identifier` span | `symbol` is the resolved local's declaration index |
| `Call` | `callee: string`, `args: Expr[]`, `symbol: int`, `type: "int"|"bool"|"str"|"unit"` | `CallExpr` span | `symbol` is callee function's global index; `type` is callee's `ret_type` or `unit` |

Every node carries `kind` and `span` (`{filename,line,column,offset,length}` plus deterministic `start`/`end` objects). Source APIs calculate end line/column from the source bytes; `analyze_tokens` derives it from the final covered token. Multiline spans therefore do not treat byte length as a column count. `Var`/`Call` carry resolved `symbol` and inferred `type`; `Function` carries `symbol`. Lowering uses an explicit depth counter (limit 256) whose per-construct accounting mirrors the parser's exactly (one level per function, block, `if`, unary, call-argument group, and grouping paren; `let`/`return`/`expr`-statements and binary operators consume none, matching `parse.py`'s `enter()` sites). The analyzer therefore accepts exactly the programs the parser accepts within the frozen limit — verified at the 254/255 boundary for every construct family — with host recursion failures converted to a bounded diagnostic as a defensive fallback.

Deterministic JSON dump: `json.dumps(ast, sort_keys=True, separators=(",",":"))` with `span` objects expanded as `{filename,line,column,offset,length,start:{line,column,offset},end:{line,column,offset}}` for byte-identical repeatability. The stable AST plus final line `SEM_OK` is the CLI success payload.

## Diagnostic codes

Parser diagnostics are exact strings, no aliases, mirroring `LexResult` discipline. The analyzer stops at the **first** semantic diagnostic; no tree is returned on error, and no second semantic error is reported.

| Code | Meaning | Span | Detail in `message` |
|---|---|---|---|
| `PAR_LEX_ERROR` | forwarded lexer fault | lexer's span | `got_kind` is original `LEX_*` |
| `PAR_FILE_TOO_LARGE` | forwarded `LEX_FILE_TOO_LARGE` | at `MAX_SOURCE_BYTES` | |
| `PAR_INVALID_INPUT` | malformed API input | `(1,1,0,0)` | |
| `PAR_UNEXPECTED_TOKEN` / `PAR_UNEXPECTED_EOF` / `PAR_EXPECTED_TOKEN` / `PAR_DEPTH_EXCEEDED` | parser failures | parser's span | pass through unchanged |
| `SEM_UNDECLARED` | use of undeclared var | `Var` span | `name` not in scope chain |
| `SEM_DUPLICATE` | duplicate declaration (no shadowing) | second declaration span | `name` already in enclosing scope |
| `SEM_TYPE_MISMATCH` | type rule violation | offending expr span | `expected` type, `got` type, `op` or `context` (e.g., `let`, `if cond`, `return`, `binop +`, `unop !`) |
| `SEM_ARITY_MISMATCH` | call arity ≠ param count | `CallExpr` span | `callee`, `expected` arity, `got` arity |
| `SEM_UNKNOWN_FUNCTION` | call to unknown function, or non-identifier callee (grouped/chained/literal) | callee span | `callee` not in global function table (builtins like `print` are unknown until Stage 16); a non-identifier callee reports "called expression is not a function name" and is never resolved through its text |

All diagnostics carry `code`, `message`, and `span` (`{filename,line,column,offset,length}`). Every `SEM_*` diagnostic also carries structured `expected` and `got`; `name`, `callee`, `context`, and `operator` are populated when applicable. The first diagnostic ends analysis; there is no recovery, secondary diagnostic, or partial tree. Lexer/parser diagnostics are never wrapped as `SEM_*`.

## Public interfaces

**Status: implemented for Stage 14.**

```python
# tools/rynorlang/analyze.py
analyze(source: str, filename: str = "<input>") -> AnalyzeResult
analyze_bytes(data: bytes, filename: str = "<input>") -> AnalyzeResult
analyze_file(path: str | Path) -> AnalyzeResult
analyze_tokens(tokens: tuple[Token,...], filename: str = "<input>") -> AnalyzeResult  # for testing

@dataclass(frozen=True)
class AnalyzeResult:
    ast: dict | None        # caller-owned JSON-compatible AST on success
    diagnostic: Diagnostic | None  # first LEX_*/PAR_*/SEM_* on failure
    ok: bool
```

* `analyze` lexes with `tools.rynorlang.lex.lex` and parses with `tools/rynorlang/parse.parse`; lex/parse failures are returned as `PAR_*` without semantic analysis. On parse success, it lowers to the stable AST and runs name/type checks.
* `analyze_tokens` requires a valid `parse_tokens` tuple (ordered, non-overlapping, single final `EOF`); malformed input yields `PAR_INVALID_INPUT`.
* Ownership: caller retains `source`/`data`; each call returns a new AST. `AnalyzeResult` and diagnostics are frozen records, while the JSON-compatible AST dictionaries/lists are mutable by their caller.
* Determinism: same `source` and `filename` produce equal `AnalyzeResult` and byte-identical CLI JSON.

**CLI (frozen):**

```text
python tools/rynorlang/analyze.py <file.rl> [--json]
# success: stable AST JSON on stdout + final line "SEM_OK", exit 0
# failure: diagnostic JSON or "file:line:col:offset: CODE: message" on stderr + diagnostic line, exit 1
# usage/OSError: exit 2
```

Exit 0 iff `ok` and `diagnostic is None`; exit 1 for any `LEX_*/PAR_*/SEM_*`; exit 2 for missing file/usage. On success the JSON is deterministic (`sort_keys=True, separators=(",",":")`) and `SEM_OK` is the last line, enabling `diff` repeatability (`test 3x`).

**Stability.** Node `kind` strings, field names, diagnostic codes, `PARSE_MAX_DEPTH` (256) and `MAX_SOURCE_BYTES` (1 MiB) are frozen. Adding a type, operator, or diagnostic code is a breaking change.

## Invariants

1. No input beyond 1 MiB is analyzed; `PAR_FILE_TOO_LARGE` before lexing.
2. If the lexer reported a diagnostic, the analyzer reports `PAR_LEX_ERROR`/`PAR_FILE_TOO_LARGE` and never returns a tree.
3. If the parser reported a diagnostic, the analyzer reports that `PAR_*` and never returns a tree or semantic diagnostic.
4. On semantic success the token stream is consumed through exactly `Program` + `EOF`; `SEM_*` never hides a parse error.
5. Every stable node span covers exactly its dominated source bytes; sibling spans remain in source order and are contained in their parent; `Var`/`Call` spans equal the identifier/call span from the temporary tree.
6. Scopes are block-nested, no shadowing, deterministic indices; forward function references resolve.
7. Type rules are exactly the frozen table; `unit` appears only as a `Call` type for functions without `ret_type` and only as `ExprStmt` is allowed to contain a `unit` Call.
8. The first diagnostic ends analysis; at most one `Diagnostic` exists.
9. Depth never exceeds 256; violation yields `PAR_DEPTH_EXCEEDED` (parser) or `SEM_*` never due to host recursion.
10. Same `source`+`filename` produce equal `AnalyzeResult` and byte-identical CLI output (determinism, testable by 3x invocation).
11. No analyzer output represents executable code; only a stable AST or a diagnostic exists.

Violations are detected by unit tests that compare kind/span/code/type/symbol, by live temporary-copy mutations that remove semantic checks, and by depth/span tests.

## Implementation status

**Implemented — Stage 14.** Host analyzer `tools/rynorlang/analyze.py` implements the frozen lowering, scopes, and type rules. The repository's `unittest` suite has 63 semantic test methods covering exact fixture inventories, deterministic symbols, multiline spans, forward references, unit-as-value, string equality versus ordering, bounded malformed input, parser/analyzer depth-boundary parity, `analyze_bytes` round-trips, else-if lowering, honest non-identifier-callee diagnostics, exact span values, and live mutations, plus an 8-test public-API gauntlet covering cross-entrypoint diagnostic equivalence, source/token consistency, the exact 1 MiB boundary, full-schema positions, and iterative serialization. `tools/rynorlang/analyze.py` is the single implementation; `rynorlang/ast/` contains only `.gitkeep`.

* Lowering pass inside `Analyzer` from `ParseNode` to stable `Program` with an explicit depth counter.
* Scope stack with global function map + block scopes, no shadowing, use-before-declare, forward fn allowed.
* Type checker for the frozen operator table, `let`/`if`/`while`/`return`/`call`/unit rules.
* CLI `tools/rynorlang/analyze.py` deterministic JSON + `SEM_OK`.

**Planned (Stage 15):** compiler ABI/format, native codegen for arithmetic/control-flow/functions, linking and execution harness.

**Experimental / explicitly not planned in Stage 14:**
* Type inference, all-paths return checking, modules/imports, arrays/structs, generics, builtins like `print`, constant folding, optimization, error recovery, self-hosted analyzer.

## Tests

**Implemented — Stage 14.** Mirrors Stage 13 discipline (52 parser tests). Commands:

```text
python -B -m unittest discover -s tests/repository -p 'test_rynorlang_semantics.py' -v
python -B -m unittest discover -s tests/repository -p 'test_rynorlang_lexer.py' -v
python -B -m unittest discover -s tests/repository -p 'test_rynorlang_parser.py' -v
python tools/build/build.py validate
python tools/build/build.py test   # repository (includes semantics) + build-failure checks
```

*Fixtures (exact):*
* `tests/fixtures/rynorlang/semantics/good/` has exactly 12 files covering empty/unit functions, forward references, all type-rule groups, string equality, unary operators, nested blocks, and deterministic symbols.
* `tests/fixtures/rynorlang/semantics/bad/` has exactly 20 files mapped by filename to one required `SEM_*` code, span, and structured expected/got detail.

*Test families:*
* **Layout:** `analyze.py` exists under `tools/rynorlang/`, no duplicate under `rynorlang/ast/`, fixture inventory exact, stdlib-only, no hedging strings.
* **Semantics:** each `good` analyzes `ok==True` with correct `type`/`symbol`; each `bad` yields exactly one `SEM_*` with correct code and span (symbol/expected/got fields); forward function reference `Call` before declaration is `ok`; `unit` Call as `ExprStmt` is `ok` but as `let` init is `SEM_TYPE_MISMATCH`; `str == str` is `ok` but `str < str` is `SEM_TYPE_MISMATCH`; duplicate across nested scopes is `SEM_DUPLICATE` (no shadowing); use-before-declare is `SEM_UNDECLARED`.
* **Span/depth/bound:** error span equals offending identifier/operator span, pinned to exact line/column/offset/length values; the analyzer accepts exactly what the parser accepts at the 254/255 nesting boundary for every construct family (while, block, call, unary, group); 256 deep still `PAR_DEPTH_EXCEEDED` from parser, `SEM_*` spans are ordered.
* **API/CLI:** `AnalyzeResult` types, `PAR_*` pass-through, `analyze_bytes` round-trip equals `analyze`, deterministic 3x CLI `SEM_OK` and byte-identical JSON, exit codes 0/1/2, missing file 2.
* **Mutation:** twenty-one temporary-copy mutations remove scope lookup, duplicate-function, duplicate-parameter, parameter-shadows-function, let-shadows-function, arithmetic/equality/relational/logical/unary typing, if/while condition, return typing, let typing, argument typing, arity, unknown-function, no-shadowing, unit-as-value enforcement, and non-identifier-callee honesty. Each mutant must exhibit the invalid behavior its normal rule test pins (accepting its probe or flipping the pinned diagnostic), proving the tests observe behavior rather than renamed constants. Temporary directories are automatically removed. A separate 8-test API gauntlet pins cross-entrypoint diagnostic equivalence, source/token consistency, the exact 1 MiB boundary, full-schema positions, and iterative serialization.

Coverage limits: semantics tests are host-only, no kernel/QEMU, no execution.

## Known limitations

* No type inference; every `let` and `Param` must annotate its type.
* No all-paths return checking; a function declared `: int` may fall off the end without `return` and still be `SEM_OK`.
* No shadowing; valid shadowing in other languages is an error here by design.
* No builtins; `print`, `use`, modules/imports are unknown until Stage 16.
* No composite types, arrays, structs, generics, or `for`/`match`/`break`.
* Host-only, not self-hosted, not part of boot image; QEMU irrelevant to semantics correctness, but kernel suite remains a regression gate.
* Single-error discipline; no recovery or secondary diagnostics.
* Bootstrap dependency on `tools/rynorlang/lex.py` and `parse.py`; lexer/parser code changes implicitly affect semantics via `PAR_*` pass-through.
