# Stage 15b report -- RynorLang shell surface (host-side, edition-gated)

## Outcome

Stage 15b is a host-side Python 3.10+ language-surface bootstrap. It adds an
explicit edition boundary (`v1` default, `"shell"`/`"shell-preview"` opt-in),
a `|>` pipeline operator with honest `Pipeline`/`Cmd` AST kinds, statically
checked `str`-only stages with the extended `unit` rule, zero-new-token
commands resolved against an explicit stub registry, and a TEST-ONLY bounded
host evaluator plus a REPL block accumulator. It changes no boot or kernel
source, adds no shell codegen, runs nothing in userspace or ring 0, and
leaves the default v1 grammar byte-identical (every valid v1 fixture keeps
identical AST JSON in both editions).

## Implementation

- Lexer (`tools/rynorlang/lex.py`): exactly one additive double token,
  `"|>" -> PIPE_GT`, recognized only in shell editions. v1 still reports
  `LEX_INVALID_CHAR` for `|`; `| >` (spaced) stays a two-token error; `||`,
  `->`, `-`, `>`, `>>`, `!` lex identically in both editions. `--edition`
  on the CLI.
- Parser (`tools/rynorlang/parse.py`): precedence-0 left-associative
  `PipeExpr ::= Stage ("|>" Stage)*` (iterative, no depth per `|>`), honest
  `CmdExpr{name, CmdArgs, Redirect*}` (never a desugared `Call`), `FlagArg`
  for span-adjacent `-flag`, `Redirect{op, target}` for `>`/`>>` with
  quoted-string targets only. A lone bare word in stage position is a
  zero-arg command candidate; `f(a)` stays a `Call`; `a - b` (spaced) stays
  subtraction while adjacent `a -b` is a command (explicit, pinned edition
  difference); `a > b` stays a comparison. `--edition` on the CLI.
- Analyzer (`tools/rynorlang/analyze.py`): `edition=` + `commands=` registry
  on every entry point (`analyze`, `analyze_bytes`, `analyze_file`,
  `analyze_tokens`; `--edition` on the CLI). New stable kinds `Pipeline`,
  `Cmd`, `Flag`, `Redirect` and 7 additive codes (`SHELL_UNKNOWN_COMMAND`,
  `SHELL_AMBIGUOUS_COMMAND`, `SHELL_PIPELINE_TYPE_MISMATCH`,
  `SHELL_UNIT_STAGE`, `SHELL_REDIRECT_ERROR`, `SHELL_COMMAND_ARITY`,
  `SHELL_COMMAND_TYPE_MISMATCH`); the 5 `SEM_*` codes are untouched.
  Non-final stages must be `str` (unit gets `SHELL_UNIT_STAGE` first, then
  the str gate); unit pipelines only as `ExprStmt` (via the returned unit
  type, mirroring `Call`); piped input fills a non-head command's first
  parameter (piping into a zero-parameter command is an arity error);
  implicit flow is commands-only. Bare words resolve lexical variables
  first, then the registry; function names get an honest
  use-`f(...)` hint; no implicit conversions anywhere. Depth charges mirror
  the parser exactly (one per `CmdExpr`, none per `|>`) so the
  parser/analyzer depth parity holds in both editions.
- Host shell (`tools/rynorlang/shell.py`, TEST-ONLY, never shipped):
  documented `DEMO_COMMANDS` stub signatures + `DEMO_IMPLS`, `run_pipeline`
  (left-to-right into bounded `PIPE_BUF_CAP=4096` buffers, overflow raises
  `ShellOverflow` instead of truncating, first failure aborts downstream and
  raises instead of flowing as a value, unit final returns `None`),
  `BlockAccumulator` (brace-balance + parse grouping for the future
  userspace REPL; failed submissions touch only the text buffer).
- RIR/backend (`tools/rynorlang/rir.py`, `compile.py`): unchanged by design;
  `Pipeline`/`Cmd` (already in `RESERVED_AST_KINDS`) fail the build with
  `COMP_V2_UNSUPPORTED` instead of miscompiling. No shell codegen yet.

## Fixtures and tests

Fixtures: 12 good + 11 bad under
`tests/fixtures/rynorlang/shell-edition/good|bad/` (pipes, chains, nesting,
precedence, args, flags, redirects + chains, zero-arg commands, unit
pipelines, let composition, call mixing; type/arity/unknown/ambiguous/
redirect/empty/trailing negatives with exact codes).

`tests/repository/test_rynorlang_shell.py` contains 46 test methods: shell
lexer gates + maximal munch; parser associativity/precedence/shapes/spans/
flag-vs-minus/redirect forms; exact good/bad inventories; v1-old-code
rejection (`PAR_LEX_ERROR`); whole-v1-corpus AST identity across editions;
alias equivalence; typing/unit/resolution/arity/redirect rules; RIR+backend
v2 rejection; bad-registry handling; 3x byte-identical determinism; no shell
kinds in any v1-analyzed corpus file; fixture-value differential evaluation;
abort/overflow/unit/call-boundary evaluator behavior; block accumulator;
kernel `repl*`/eval placement guard; 7 mutation checks (lexer gate, pipeline
check, unit check, unknown-command check, stage-order swap, no-desugar proof,
edition-gate removal).

## Verification

```text
python -m pytest tests/repository/test_rynorlang_shell.py -q
python tools/build/build.py test
python tools/build/build.py check
```

Reference-host result: 47 shell tests pass (250 subtests); full `test`
gate passes 433/433 repository tests; `check` passes build, repository, and
integration gates. Inventory sum `REPOSITORY_TEST_INVENTORY` equals collected
count (433). Docs counts in `README.md`, `ARCHITECTURE.md`, and
`docs/design/shell.md` match; `test_docs_counts_current` derives the total.

## Forensic notes (found and repaired during implementation)

- `a > b` initially parsed as command+redirect in shell edition, stealing
  comparison syntax. Repaired: redirect targets are quoted strings only
  (`cmd > "out"`); `a > b` is a comparison in both editions. Pinned by
  corpus-identity test_19 plus the `a > b` pin in test_20b.
- Same-block `-flag` adjacency vs subtraction: spaced `a - b` stays
  subtraction in both editions; only adjacent `a -b` is a command -- an
  explicit, pinned edition difference (test_20b). No valid v1 program that
  avoids adjacent-minus changes meaning.
- Verifier-style single-pass masking did not recur: shell use checks run on
  fully lowered stages left-to-right with per-stage spans.

## Limitations

- Static `str`-only stages; no records/lists/status/match/modules; function
  calls keep explicit args (no implicit pipeline application); `Call` stages
  are typed but not host-evaluated (`SHELL_UNSUPPORTED_STAGE`).
- Redirects are validated AST only (no filesystem); the host evaluator does
  not model files. Streaming/backpressure, `$?`/status, session persistence,
  and real commands belong to later stages (16/18b/19a).
- Native exits/ABL/RIR behavior unchanged from 15a; no shell codegen.
- Host-only: kernel monitor frozen, no `kernel/shell/repl*`, no ring-0 eval.
