# RynorLang Stage 19b — match, control flow, status/Result (frozen specification)

Status: **frozen.** Executable spec for Stage 19b: `match` with
narrowing, `break`/`continue`, `result<T,E>` with explicit threading,
and the `Error` convention. Resolves the open `?`-vs-`match` question
(explicit `match` wins; no `?` operator). Nothing here executes by
itself; implementation must match it exactly.

Precedence: below the 19a freeze (which it extends additively) and
all earlier frozen contracts; above the implementation.

## 1. Scope

IN: `match` statements with narrowing patterns; `break`/`continue`;
`result<T,E>` type with `ok`/`err` constructors; explicit error
threading idiom; `Error` record convention; control-flow RIR goldens.
OUT (deferred with reason): `?` propagation operator (rejected, §7);
expression-valued `match` (no phi nodes in RIR — arms are blocks,
values leave via `return`/`unwrap_or`; §7); structural patterns for
records/lists/maps (wildcard + field access cover 19b; record
patterns owned by 19e iff its core dialect needs them); `status`
renaming (frozen; coexists with `result`); methods; exceptions
(there are none — propagation is explicit and total).

## 2. Lexer (no new tokens)

`=>` arrives as EQUAL + GREATER (adjacent; enforced by span check,
redirect precedent). `match`/`break`/`continue` stay IDENTIFIERs.
Reservations: no function may be named `match`/`break`/`continue`
(`SEM_DUPLICATE`, `print` precedent); no VARIABLE may be named
`break`/`continue` either (loop keywords are universally reserved —
rejected at first `let`/param binding); variables named `match` keep
working via statement-level backtracking (§3).

## 3. Syntax

```
match_stmt ::= "match" expr "{" match_arm { "," match_arm } "}"
match_arm  ::= pattern "=>" block
pattern    ::= "_" | literal | binding | ctor_pat
literal    ::= INTEGER | STRING | "true" | "false"
binding    ::= IDENT               (binds the whole scrutinee)
ctor_pat   ::= ("ok" | "err") "(" ( "_" | literal | binding ) ")"
```

- Trailing commas forbidden (frozen rule). Arms comma-separated.
- `match` opens a statement ONLY when an expression-statement parse
  would also succeed ambiguously: parse `match`, scrutinee
  (`parse_pipeline`), `{`; on any failure before the first arm,
  restore and parse as an ordinary expression-statement
  (`parse_cmd_or_expr` precedent). A bare `match;` stays a Var.
- `break;` / `continue;` statements (exactly; trailing content after
  the word is a parse error, keeping them unmistakable).
- `ok(v)` / `err(e)` are builtin call shapes (reserved names, `print`
  precedent) elaborated against the expected `result<T,E>` (or
  computed from operands where unambiguous — see §4).
- Statement dispatch tries `match` before expression-statements;
  `break`/`continue` are dispatched by word.

## 4. Type system and narrowing

- `result<T,E>`: parametric builtin (`T`, `E` any value types except
  direct `result`-in-`result`/`status` nesting — the single-level-tags
  rule extends uniformly: payload positions accept any value type
  except `status`/`result` themselves; map-key rule unchanged).
  Canonical strings `result<int,str>`; static size = 8 + size(E) +
  size(T) (tag u64 @0, err payload E @8, ok payload T @8+size(E));
  same 8192-byte bound, nesting ≤ 8, N/A capacities.
- `ok(v)`/`err(e)`: with expected `result<T,E>`, elaborate `v` vs `T`
  / `e` vs `E`; without expected (non-annotated positions), the type
  is underdetermined → `SEM_TYPE_MISMATCH` ("needs an annotated
  result type"). No inference anywhere else either (frozen rule).
- `match` scrutinee types: `status<T>`, `result<T,E>`, `int`,
  `bool`, `str`. Records/lists/maps as scrutinee → `SEM_TYPE_MISMATCH`
  ("cannot match" — narrow via field access/index first).
- Narrowing: `ok(x)`/`err(e)` bind `x: T` / `e: E` (or the status
  payload for `status` scrutinees); literal patterns require the
  literal's type to equal the scrutinee's scalar type; `_` and bare
  bindings bind nothing/new-whole. Bindings obey no-shadowing and
  are scoped to their arm block.
- Exhaustiveness (all `SEM_TYPE_MISMATCH`, message-tuned):
  `status`/`result` need `ok`+`err` or `_`; `bool` needs
  `true`+`false` or `_`; `int`/`str` need `_`. Duplicate patterns
  (`SEM_DUPLICATE`): same literal twice, `ok` twice, or a binding/
  wildcard followed by anything (unreachable arm).
- `break`/`continue` outside any loop body → `SEM_TYPE_MISMATCH`
  ("outside loop"). Loops nest; both target the innermost loop.
  `match` is transparent (not a loop boundary).
- `Error` convention (documentation, not a builtin): users declare
  `record Error { code: int, message: str, span: int, trace: list<int,8> }`
  with `span = -1` meaning absent. Propagation is explicit:
  `match fallible() { ok(v) => { ... }, err(e) => { return e.code; } }`
  — codes/messages flow untouched (no squash codes), proven by
  multi-call field-equality tests.

## 5. Semantics

- Arm selection is first-match in source order (literals before
  bindings by construction — unreachable arms are rejected, so order
  beyond that is unobservable... except literal-vs-literal overlap is
  impossible (duplicates rejected) and `ok`-vs-`err` are disjoint:
  selection is deterministic AND order-independent. Documented as
  first-match, implemented as disjoint dispatch).
- `break` exits the innermost loop immediately (post-iteration code
  skipped); `continue` starts the next iteration (condition
  re-evaluated). Both are total (no traps, no unwinding — RIR `jmp`).
- Arm blocks execute at most once; exactly one arm executes per
  `match` (exhaustiveness guarantees totality).

## 6. Memory representation and RIR

- `result<T,E>` layout: u64 tag (0 ok / 1 err) @0, E payload @8, T
  payload @8+size(E). `err` payloads of the UNUSED variant are
  zeroed at construction (deterministic equality, 19a rule).
- New RIR ops (frozen): `result_ok` (val → result), `result_err`
  (val → result), `unwrap_ok` (result|status → ok-payload, valid by
  construction on the taken arm), `unwrap_err` (→ err-payload).
  The 19a `status_is_ok`/`status_is_err`/`status_unwrap_or` generalize
  to accept `result<T,E>` operands (same tag@0 rule; emitter dispatches
  layouts by operand type). `==` generalizes to identical `result`
  types (tag+code... tag+E+T compare). `print` generalizes
  (`ok(...)`/`err(...)` rendering via the payload printers).
- `match` lowers in the builder to tag reads + `br` + payload
  unwraps + arm blocks + join (existing terminators only; no phi —
  arms are blocks). `break`/`continue` lower to `jmp` to the
  enclosing loop's exit/header via a builder loop-context stack.
- Slot widths generalize through the shared allocator untouched
  (width = f(static size) already).

## 7. Alternatives considered

- A1 `?` postfix propagation: rejected — implicit control flow and a
  new punctuation token for what explicit `match`+`return` already
  expresses; the open question from the runtime doc is answered here.
- A2 expression-`match`: rejected — RIR has no phi nodes and gains
  none for 19b; statement arms + early return + `unwrap_or` cover
  every use (proven by the propagation fixtures).
- A3 exceptions/hidden traps: rejected — total-by-construction is the
  project philosophy; every path is explicit.
- A4 structural record/list patterns: deferred — wildcard + accessors
  suffice for 19b exits; 19e may add them with its own RFC if the
  self-hosted compiler needs them.
- A5 `Error` as builtin type: rejected — a user-declared record
  convention needs zero machinery and stays flexible.
- A6 `break` with levels/labels: rejected — innermost-only suffices;
  labeled control is unneeded complexity.
- A7 `match` as keyword: rejected — contextual word + backtracking
  preserves every v1 program (verified by the suite running unchanged).

## 8. Critics (strongest objection each, all resolved)

1. Kernel correctness: N/A (host-side only; no guest surface).
2. Language semantics: "statement-only match can't produce values for
   nested positions." → Resolved: `unwrap_or` covers expressions;
   fixtures prove both idioms; 19d conformance pins them.
3. ABI/compat: "generalizing 19a status ops changes frozen RIR."
   → Resolved: verifier rules widen additively (status inputs verify
   byte-identically); v1/19a RIR goldens must pass unchanged (gate).
4. Security: "match on user-controlled ints with many literal arms is
   O(n) scan." → Resolved: arms bounded by source size; linear scan
   is deterministic and total; no jump tables needed.
5. Minimalism: "four new RIR ops plus three generalizations is a lot."
   → Resolved: each is forced (construct ×2, extract ×2); extraction
   cannot reuse `unwrap_or` (no dummy defaults); generalization
   avoids a parallel result-only family (which would be MORE ops).
6. Testability: "path-validated unwraps are trusted, not proven."
   → Resolved: builder emits them only on dominated taken-arms;
   verifier checks types; differentials execute both arms; mutants
   swap arm order and drop the tag check (must go RED).
7. Cold reviewer: "`status` vs `result` duplication confuses."
   → Resolved: `status<T>` frozen with int payload (19a programs keep
   working byte-identically); `result<T,E>` is the general form;
   the RFC documents both with examples; 19d conformance covers both.

## 9. Bounds inventory (additive)

Match arms ≤ 256 per match (parse depth budget covers nesting;
arm COUNT bounded by source length implicitly — explicit cap: 64
arms, `SEM_LIMIT_EXCEEDED` beyond); pattern nesting ≤ 3
(`ok(ok(...))` is rejected anyway by no-nesting); loop-nesting for
break/continue is unbounded structurally but each loop is finite
source (termination is the programmer's, as in v1 `while`).
All 19a bounds unchanged.

## 10. Test plan (binding)

New dirs `tests/fixtures/rynorlang/control/{good,bad}/` (≥10 good:
status/result match incl. nesting, literal/wildcard arms,
exhaustiveness edges, break/continue incl. nesting, Error threading
across calls, control-flow goldens; ≥14 bad: nonexhaustive ×3,
unreachable ×2, outside-loop ×2, scrutinee-type ×2, pattern-type ×2,
dup-ok, arity, annotation-needed, `?`-rejection (parse error)).
New `tests/repository/test_rynorlang_control.py` (accept/reject with
exact codes, RIR control-flow goldens, determinism, differentials
with exit+stdout, ≥10-mutant matrix: arm-swap, tag-check removal,
unwrap confusion, exhaustiveness removal, break-target swap,
ok/err-ctor swap, `?` absent). Inventory + dir pins updated. Guest
suites untouched.
