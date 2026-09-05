# RynorLang shell language

Status: **design for Stages 16–18; not implemented.** This document defines the
shell surface of RynorLang: pipelines, commands, property access, the REPL
contract, and the edition policy that keeps the frozen v1 grammar byte-identical.
It is deliberately separate from `docs/design/shell.md`, which specifies the
frozen ring-0 kernel monitor (no evaluation there, ever). It creates no
implementation. See `rynorlang-runtime.md` (values/runtime), `rynorlang-ast.md`
(frozen core), and `ROADMAP.md` (staging).

## 1. Pipeline operator: `|>`

`a |> b |> c` — left-associative, precedence 0 (below `||`).

*Why `|>` and not `->`:* `->` is already lexed-but-rejected and reads as a
returns-chain to every C/Rust-family reader; spending that reservation to save
one lexer line misreads the construct users will read most. `|>` is
self-documenting, has no collision with any frozen token (`|` alone stays
`LEX_INVALID_CHAR`; maximal munch dispatches `|`→`||` vs `>`→`|>` on disjoint
second characters), is whitespace-safe (`a | > b` stays a two-token error),
and burns no future syntax (`|` alone remains free; `->` stays reserved).
Lexer delta: exactly one new double-token entry, **edition-gated** like
everything in this document (Section 5): default v1 rejects `|>` exactly
as today.

## 2. Commands vs functions

* **Function:** `f(a, b)` — the frozen `Call` (callee `Identifier`, parens,
  commas, statically typed args, forward references, no shadowing). Unchanged.
* **Command:** `upper hello`, `count "a1b2"`, `ls -la /tmp` — a bare word plus
  space-separated arguments, **no parens, no commas**. A command is parsed as
  `Cmd{name, argv}`. Arguments need **zero new lexer tokens**:
  bare words and plain variables lex as `IDENTIFIER` today (`upper hello` is
  already `IDENTIFIER IDENTIFIER`); flags are span-adjacent `MINUS`+`IDENTIFIER`
  (`-n`: the parser requires `MINUS.offset + 1 == IDENT.offset`, so `- n`
  with a space stays two tokens and fails — whitespace info survives in spans
  even though the lexer discards it); redirects reuse frozen tokens
  (`>`/`>>` as one/two `GREATER`, `2>` as `INTEGER`+`GREATER`); quoted strings,
  integers, and booleans reuse their literals. `$` variables, globs, and
  subshells do **not** exist: shell variables are plain RynorLang identifiers
  (`let x = ...; upper x` passes a `Var`), and there is no interpolation.
  External programs (later): `run "prog" args...` — same node, dispatched via
  the loader instead of the service table.

Desugaring a command into the frozen `Call` is **rejected as dishonest**:
`Call{callee,symbol,type}` requires a global-function index and typed params
that external commands do not have; failures would misreport
`SEM_UNKNOWN_FUNCTION`/`SEM_ARITY_MISMATCH` with wrong spans and pollute the
deterministic symbol space the Stage 15 backend pins. The honest core is new
`Pipeline{stages}` and `Cmd{name,argv}` kinds from day one (edition-gated).

## 3. Pipeline semantics (MVP = batch, then streaming)

* **Values:** MVP stages exchange bounded `str` (existing `Token.value`
  semantics). No objects/tuples/streams yet — the frozen types have no
  composites. Typed rows arrive with `record`/`status` (Stage 19a); until then
  the stage type rule is explicit, not dynamic.
* **Grammar position:** a pipeline is a full precedence-0 expression level
  (`PipeExpr ::= OrExpr ("|>" OrExpr)*`), so `let x: str = ls |> count;` and
  `(a |> b)` compose like any expression — including the composition that
  makes shell pipelines worth having. This is not a grammar hole: every stage
  is statically typed (MVP: each stage must have type `str`; the pipeline's
  type is its last stage's type), `1 + upper x` still fails (an `int` stage
  where `str` is required), and `if a |> b { }` fails (`str` ≠ `bool`).
* **The `unit` rule extends, not breaks:** a non-final stage must be non-`unit`
  (a `unit` call produces no value to flow); a `unit`-typed pipeline is allowed
  only as an `ExprStmt` — the exact mirror of the frozen `unit`-Call rule.
  No redefinition, no leak.
* **Execution:** MVP runs each stage left-to-right to completion into a bounded
  buffer (caller-owned `kbuf`, cap-checked), then the next stage; overflow
  aborts with outputs unchanged (`KBUF_FULL`/`TOO_SMALL` discipline, never a
  partial write). Later: pull-iterator with a 1–8-slot ring; a full buffer
  yields upstream (backpressure via scheduler yield); no unbounded collect.
* **Errors abort, never flow as values.** The first failing stage stops the
  pipeline; downstream never runs; the pipeline result is the last value or
  the first `SHELL_*`/`KRST_*` code. Rationale: fail-closed kernel heritage,
  matches transactional runtime services, prevents `"error: unknown"` being
  consumed as data. Error-as-value arrives only with `status`/`Result` (Stage
  19a); userspace adds a `$?`-style status query, never sooner.
* **Cap:** one pipe buffer is bounded (exact cap fixed in the shell RFC; 64 B
  line discipline vs 4 KiB page are the candidates). Full buffer = abort in
  MVP, backpressure later — never silent truncation.

## 4. Property access: `.` (deferred to records, Stage 19a)

`.` is `LEX_INVALID_CHAR` today (no floats, no methods), so the slot is free
when needed — but property access without `record` types would be untypable
(the analyzer would have to reject every use until records land), so the `DOT`
token and any `Member`/`MethodCall` kinds are **deferred to Stage 19a with the
`record` type itself**, not part of 15b. When they land: one lexer entry
(`".": "DOT"`; `..`/`...` lex as two/three `DOT`s and fail at parse), grammar
as tightest postfix (`Postfix ::= Primary ("." IDENT)* ("(" Args ")")*`, so
`a.b(c)` is field-then-call), and — to keep the additive-only promise without
retyping frozen `Call.callee: string` — a distinct `MethodCall{obj, method,
args}` kind rather than a widened `Call`. No silent change to `int`/`str`
ordering or `:` type syntax. Until then, `a.b` stays `LEX_INVALID_CHAR`
exactly as today.

## 5. Edition policy (how v1 stays frozen)

* Default `analyze()`/`parse()`/`lex()` behavior is **byte-identical v1**:
  `|>`/`|`/`.` still `LEX_INVALID_CHAR`; `Pipeline`/`Cmd`/`Member` never
  emitted; the 16 kinds, 5 `SEM_*` codes, depth-256 accounting, and 1 MiB bound
  unchanged. All existing fixtures pass byte-identical.
* Shell surface lives behind an explicit edition flag
  (`analyze(..., edition="shell-preview")`, CLI `--edition`; **planned API —
  no `edition` parameter exists yet, and adding it defaulting to `v1` is
  backward-compatible**); the flag defaults to `v1`. New kinds/codes/fields are
  **additive-only, never renamed/removed/retyped**; no old rule is widened
  (`str < bool` stays `SEM_TYPE_MISMATCH`).
* Stage 15 pins the v1 contract by exact-kind whitelist plus the enforced test
  inventory (today: 16 kinds, 5 `SEM_*` codes — there is no `ast_version` key
  in emitted JSON yet, so the pin is the whitelist + gate, not a version
  field; a version envelope arrives with the edition work without disturbing
  v1 goldens). Non-v1 nodes are rejected explicitly instead of miscompiled.

## 6. REPL contract (userspace only, Stage 18b)

The REPL never runs in the kernel and never evaluates untrusted text in ring 0.
It runs in CPL3 with the session model below:

* **Per-block, not per-line:** accumulate input until braces balance and the
  buffer parses; otherwise show a continuation prompt. A single `if`/`while`/`fn`
  needs its block.
* **Session = one implicit program:** `fn` definitions append to the global
  table (no shadowing, per the frozen rule — redefinition is an honest
  `SEM_DUPLICATE`, "use a new name"); top-level `let` persists in a session
  store; bare expressions evaluate, print, and are discarded.
* **Determinism via whole-buffer re-analysis:** each submission re-analyzes the
  entire session text (bounded by 1 MiB), so identical session text always
  yields identical symbols — history can never silently shift indices.
* **Memory:** per-submission arena (reset after each block) plus the session
  arena for persisted values only; a balance walk asserts zero leak per prompt.
* **Interrupt:** `Ctrl-C` sets an abort flag via the IRQ1 path, discards the
  current partial block, preserves the session; it never kills the REPL thread.
* **No history/cursor** until the 18b shell milestone itself lands.

## 7. Scripts (before any filesystem)

Before Stage 17 block/filesystem work, scripts reach the runtime **only** via
the read-only boot bundle (magic+version+count manifest, per-entry
`{name[32],len,fnv1a}`, whole bundle `< 64 KiB`, validated before any byte
executes; corrupt input halts with a diagnostic). No serial-paste execution —
paste stays a QEMU test-harness facility. Exit status is the `return` value of
`main()->int`, emitted as an `[RL] exit=N` serial marker. `argv`/pipes/files
arrive with 18b.

## 8. Testing requirements (binding)

* Fixtures live under `tests/fixtures/rynorlang/shell-edition/good|bad/`
  (never move v1 files); v1 `GOOD/BAD_NAMES` sets stay pinned; new
  `EXPECTED_SHELL_GOOD/BAD` inventories beside them.
* Per feature, all four dimensions: positive (shape/span/type), negative
  (exact code+span), boundary (empty segment, trailing operator, buffer-full,
  max depth), mutation (remove dispatch/full-check/arena-free → must flip).
* Edition gate tests: v1 rejects `|>`/`.` with the OLD codes; shell edition
  accepts; Stage-15 rejects v2 kinds explicitly.
* REPL tests run on the host first (accumulator, session persistence,
  arena-flatness, interrupt-abort) plus a placement guard asserting no
  `kernel/shell/repl*` ever exists.
* Inventory: new keys (`test_rynorlang_edition`, `test_rynorlang_repl`, …),
  never folded into old counts; counts are evidence, not goals.

## 9. Open questions (for the shell RFC, not this doc)

Exact pipe-buffer cap; `$?`-style status in MVP vs 18b; `run` as ring-0 stub
vs 18b-only; `status`-propagation syntax (`?` vs `match`); `map` key types.
