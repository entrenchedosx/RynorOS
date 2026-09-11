# Stage 19a close-out — aggregate language values

Status: **implemented and verified (host-side).** Frozen spec:
`docs/design/rynorlang-aggregates.md` (+2 addenda). This report closes
the ROADMAP 19a row within its frozen scope.

## Baseline / scope

Baseline `74e55b7` (18d closed) via P3 (`abi-growth.md`) and the 19a
RFC (`f9c92d8` + `no-DOT`/`slot-order` addenda). Scope: nominal
records, `list<T,N>`, `map<K,V,N>`, minimal `status` error values,
aggregate builtins, `==`/`print`/`len`, bitops, `byte_at` — across the
five host engines (lex/parse/analyze/RIR/compile/oracle). Deferred per
RFC: multi-width integers, concat, assignment, iteration syntax, map
remove, `match` (19b), modules (19c), guest-evaluator parity (owned by
the stage that first executes aggregates in-OS).

## Implementation

Slices: shared `agtypes.py` + additive lexer tokens; parser
(contextual records/types, call-shaped construction, `->`/`[]`/`{}`
postfix + literals, C-order bitops, depth-tagged `>>` closing, shell
`>>` accommodation); analyzer (record table, canonical types,
occurs-check, bidirectional literals, builtins, generalized `==`);
RIR (`rectypes` table, 16 aggregate ops, width-generalized shared
allocator, aggregate param aliasing); backend (multi-slot homes,
bounded copy/zero loops, FNV open-addressing maps, total fallible
ops with zeroed err payloads, recursive `==`/print, stack-slot-0
sret); oracle (structural values, identical probing, re-derived FNV).
Home-relative addresses descend (one slot = 8 bytes down); the three
ascending-address bugs found by bisection are fixed and documented in
the slice commits.

## Compatibility (no freeze break)

Lexer/parser/analyzer/RIR/compiler: every previously-valid input
takes identical branches (new syntax was lex/parse errors before).
All v1 suites pass unchanged except pins that moved WITH the features
they guard (lexer 18/19 sources, parser precedence-mutant numbers +
program message, shell pipe-layer pins incl. a sharpened gate mutant,
programs-05 code, RIR reserved-opcode word, 3 compiler mutant
anchors) — rejections preserved in every case. v1 ASM hashes
(hello/fib) pinned. Mismatches: none. Authorized changes: two 15b
supersessions recorded in the RFC with hierarchy rationale (DOT plan
yields to frozen `test_49`; `|`-invalid prose yields to ROADMAP
bitops; `->` reservation spent on field access).

## Tests

New: 50 fixtures (16 good / 34 bad incl. every cap boundary ±1,
recursion pair, key/type/arity classes) + `test_rynorlang_aggregates`
(26 tests: inventory, algebra units, accept/reject with exact codes,
RIR goldens, 3× determinism, 16 native differentials with exit+stdout
equality, print/bitop stdout goldens, v1-ASM-hash gate, exact
8192-boundary accept vs `COMP_FRAME_TOO_BIG` interaction). Full repo:
628 green (was 602; +26, inventories + doc counts updated).

## Mutants (13, all RED-as-designed)

Analyzer (8): overcap/dup-field/recursion/cap-zero/nesting/key/
reserved-name/field-existence removals flip fixtures to accepted.
RIR (3): list-cap removal (hand-built overcap verifies clean vs real
flag), frameslots tamper, get_field-result removal (width-matched
tamper isolates the single check). Backend (2, native): sret-slot
move and FNV-prime change diverge from oracle goldens (the prime
mutant uses keys whose slot orders provably differ).

## Resources / performance

Static sizes only (no runtime allocation anywhere): max value 8192 B,
frames ≤1024 slots, probes ≤N, shifts masked. Native runs used in
differentials complete within harness timeouts; maxcap (8 KiB values)
executes cleanly. No leaks possible by construction (no heap).

## Git state

Slices `81cbf34` (types+lexer), `4da2707` (parser), `5d0ffab`+`ae5bc5d`
(analyzer), `602cb39` (RIR/backend/oracle), `7ffbffb` (fixtures/suite).
This close-out: report + ROADMAP. Tree clean; nothing pushed.
