# Stage 19b close-out — match, control flow, status/Result

Status: **implemented and verified (host-side).** Frozen spec:
`docs/design/rynorlang-control.md`. Closes the ROADMAP 19b row.

## Baseline / scope

Baseline `4e7f04e` (19a closed). Scope: `match` statements with
narrowing, `break`/`continue`, `result<T,E>` with `ok`/`err`
constructors, explicit threading idiom, `Error` record convention.
Deferred per RFC: `?` operator (rejected), expression-`match` (no
phi nodes), structural aggregate patterns (wildcard + accessors
suffice), `status` renaming (frozen), exceptions (none exist).

## Implementation

Slices: RFC freeze; engines (parser contextual match with
commit-on-brace backtracking + `=>` arms + patterns, analyzer with
reserved words/loop depth/narrowing/exhaustiveness/ok-err
elaboration, RIR match-to-br lowering with loop-context jumps and
4 result ops + 3 generalizations, backend result layouts/ctors/
unwraps/==/print, oracle mirrors); 23 fixtures + 18-test suite.

## Compatibility

No new lexer tokens (`=>` is EQUAL+GREATER; words stay identifiers).
Previously-valid inputs take identical branches (match/break/continue
shapes were parse errors before, except the reserved-word migration:
no fixture defined `match`/`break`/`continue`/`ok`/`err` as
functions — verified zero collisions). v1/19a suites pass unchanged
(646 repo green); two 19a rules tightened uniformly with RFC
amendments (no status/result nesting anywhere; record fields checked).

## Tests

5 good / 18 bad control fixtures; suite (inventory, accept/reject
with exact codes, control-flow goldens, exhaustiveness boundary,
3× determinism, 5 native differentials with exit+stdout equality,
golden stdouts, 8-mutant matrix: exhaustiveness/unreachable/
outside-loop/ctor-guard removals, break-target swap (structural),
unwrap confusion (type-visible), backend arm-swap (native
divergence)). Full repo 646 green.

## Notable semantics (audited)

- `continue` is unreachable in terminating programs (no assignment
  means loop iterations are identical; only break/return on a first
  iteration can exit). Total and correct; documented, not worked
  around. Revisit if assignment ever lands.
- Match dispatch is disjoint by construction (literals unique,
  ok/err disjoint, bindings/wildcards terminal and checked last);
  first-match order is unobservable. No phi nodes needed (arms are
  blocks; values leave via return/unwrap_or).
- Error values thread untouched across calls (field-equality proven
  by error_threading: code 10 + message intact through two calls).

## Git state

RFC `fe6b745`, engines `5dc9b20`, fixtures/suite `9e7670f`. This
close-out: report + ROADMAP. Tree clean; nothing pushed.
