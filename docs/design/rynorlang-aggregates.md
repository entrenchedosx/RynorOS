# RynorLang Stage 19a — aggregate values (frozen specification)

Status: **frozen.** This document is the executable specification for
Stage 19a. It resolves the ROADMAP intent (`record`, `list<T,N>`,
`map<K,V,N>`, capacities in types, exhaustion behavior) plus the
grab-bag tail (string/byte operations, fixed-width integers, bitops)
into a minimal implementable surface. Nothing here executes by itself;
implementation must match it exactly.

Precedence: below the frozen Stage 12–16 contracts (lexer, grammar,
stable AST schema discipline, RIR/CFG/ABI, program model) and the P3
growth policy where they overlap; above the implementation. Where this
document extends a frozen surface it does so additively (see §10).

## 1. Scope

IN: nominal records, fixed-capacity lists, fixed-capacity maps with
value semantics and static sizes; a minimal `status` error value for
fallible collection operations; `len`, `==`, `print` over aggregates;
bitwise ops on `int`; `len(str)` and `byte_at`; exact-cap static
rejection; host engines (lex/parse/analyze/RIR/compile/oracle).

OUT (deferred with reason): multi-width integers (19a pins `int`=i64
two's-complement, trap-on-overflow per the 15a ABI; `u8` etc. need no
19a feature — `byte_at` returns `int` by the C-`getchar` precedent);
string concat (needs dynamic allocation policy — 19c API era);
assignment/mutation statements (functional update ops suffice);
iteration syntax (`while`+`len`+index covers it); map remove;
`status` `==` stays (trivial, included) but `match` is 19b;
guest (CPL3) evaluator parity (18d evaluator stays frozen; no 19a
exit needs it — owned by the stage that first executes aggregates
in-OS); methods, defaults, generics beyond the builtins below.

## 2. Lexer (additive only, no new keywords)

- New punctuation, all previously `LEX_INVALID_CHAR` (no valid v1
  program contains them; every frozen lexer golden passes unchanged):
  `[` → LEFT_BRACKET, `]` → RIGHT_BRACKET, `&` → AMP, `|` → PIPE,
  `^` → CARET, `~` → TILDE, `<<` → SHIFT_LEFT, `>>` → SHIFT_RIGHT
  (maximal munch, `|>`-precedent).
- Deliberately NOT added: `.` (DOT). The frozen bad-fixture
  `invalid_char_dot.rl` (`let x = 3.14;`) plus
  `test_49_all_fixtures_have_the_expected_outcome` require `.` to stay
  lexically invalid (floats deferred). Field access therefore uses the
  existing `->` token in postfix position (see §3), which is free
  real estate: `->` occurs in valid v1 only in function return-type
  position, never in expressions.
- Shell `>>` append-redirect keeps working: `parse_redirect` accepts
  either two adjacent `>` tokens or one `SHIFT_RIGHT` token (parser
  accommodation; shell fixtures byte-stable).

## 3. Syntax

```
program    ::= { function | record_decl }
record_decl::= "record" IDENT "{" [ field { "," field } ] "}"
field      ::= IDENT ":" type
type       ::= "int" | "bool" | "str" | IDENTREC
             | "list" "<" type "," INTEGER ">"
             | "map" "<" keytype "," type "," INTEGER ">"
             | "status" "<" type ">"
keytype    ::= "int" | "bool" | "str"
```

- Trailing commas forbidden everywhere (frozen rule extended).
- Record construction: postfix `IDENT "{" [ name ":" expr { "," ... } ] "}"`
  (all fields required, any order, no duplicates, no unknowns).
- Field access: postfix `expr "->" IDENT` (existing ARROW token).
- Index: postfix `expr "[" expr "]"`.
- List literal: `"[" [ expr { "," expr } ] "]"` (empty `[]` allowed;
  element count ≤ N else static rejection).
- Map literal: `"{" [ expr ":" expr { "," ... } ] "}"` in expression
  position only (blocks remain statement-position; `Point {` with a
  leading identifier is a record literal, never a map).
- `>>` closes two type-arg levels (C++11 rule); `>` adjacent to `=`
  must be space-separated (`> =`), else maximal munch yields `>=`.
- Bitops fit the Pratt table C-style without disturbing frozen relative
  order: `||`=1, `&&`=2, `|`=3, `^`=4, `&`=5, `==`/`!=`=6, rel=7,
  `<<`/`>>`=8, `+`-`-`=9, `*`-`/`-`%`=10. Unary `~` joins `-`/`!`.
- New builtins are ordinary call shapes (reserved names, `print`
  precedent): `len`, `push`, `insert`, `get`, `is_ok`, `is_err`,
  `unwrap_or`, `byte_at`. Redefining them is `SEM_DUPLICATE`
  (zero fixture collisions — verified).

## 4. Type system

- Types are canonical strings: `int`, `bool`, `str`, `Point`,
  `list<int,4>`, `map<str,int,8>`, `status<list<int,4>>` (no inner
  whitespace). Equality is string equality after canonicalization.
- Same-type rule extended: `==` needs identical canonical types over
  all value types (unit excluded as today). Assignment/`let`/arg/return
  matching is exact-type (no conversions, as today).
- Records are nominal (name matters). Fields: any storable type.
  Recursive records (direct or mutual) are `SEM_TYPE_MISMATCH`.
  Record/fn top-level names share one namespace (`SEM_DUPLICATE`).
- Storable types (list elements, map values, record fields, status
  payloads): any value type except `status` (single-level tags only;
  19b may revisit). Map keys: exactly `int|bool|str`.
- `N` (capacity): integer literal, `N ≥ 1` (`SEM_LIMIT_EXCEEDED`
  for 0). No other `N` bound is needed: the size cap subsumes it.
- Static size: int/bool 8, str 16, record Σ fields, list 8+N·size(T),
  map 8+N·(8+size(K)+size(V)), status 16+size(T). All sizes ≡ 0
  (mod 8): layouts are padding-free by construction.
- `MAX_AGG_BYTES = 8192` per value (static size incl. metadata).
  Exceeding → `SEM_LIMIT_EXCEEDED`. Exact pairs: `list<int,1023>`
  (8192 ✓) vs `list<int,1024>` (8200 ✗). Type nesting depth ≤ 8
  (same code). Frames keep the independent `MAX_FRAMESLOTS = 1024`
  cap (`COMP_FRAME_TOO_BIG` on combined pressure, as today).
- No new diagnostic codes: capacity/depth → `SEM_LIMIT_EXCEEDED`;
  shape errors → the fitting frozen `SEM_*` code with a tuned message
  (unknown record → `SEM_UNDECLARED`; bad field → `SEM_UNDECLARED`/
  `SEM_DUPLICATE`; recursion → `SEM_TYPE_MISMATCH`; missing/extra
  construction fields → `SEM_ARITY_MISMATCH`).

## 5. Semantics of operations (all total, none trap)

- `len(x)`: `list`→current length, `map`→count, `str`→bytes; else
  `SEM_TYPE_MISMATCH`.
- `push(l: list<T,N>, v: T): status<list<T,N>>` — full (len==N) yields
  `err(1)`, else `ok(new list, len+1)`. Functional (input unchanged).
- `insert(m, k, v): status<map<K,V,N>>` — existing key updates
  (count unchanged); new key at full yields `err(1)`.
- `get(m, k): status<V>` — missing yields `err(2)`.
- `l[i]`: `0 ≤ i < len` else `err(3)` (`status<T>`). Bound is the
  runtime length, not N. Negative indices are out-of-range (no wraparound).
- `byte_at(s, i): status<int>` — byte value 0..255, OOB yields `err(3)`.
- `is_ok/is_err(s: status<T>): bool`; `unwrap_or(s, d: T): T`.
- Frozen err codes: 1 FULL, 2 NOTFOUND, 3 OUT_OF_RANGE (4 NOMEM
  reserved; unused in 19a — frames are static, so runtime exhaustion
  is impossible by construction).
- `==`/`!=`: records/lists compare contents (padding-free ⇒
  byte-compare is sound); maps compare logically (count + every
  key/value pair — insertion-order independent); status compares
  tag+code+payload.
- `print(x)` accepts every value type; aggregates render in the frozen
  canonical format: records `{x: 1, y: 2}` (declaration order),
  lists `[1, 2]`, maps `{"a": 1}` (entries sorted by key: ints
  numeric, bools false<true, strs lexicographic), status `ok(...)` /
  `err(1)`. The RIR builder desugars aggregate prints into `rt_print_*`
  scalar calls + punctuation (no runtime-asm change); the oracle
  renders the same format directly.
- Bitops on `int`: `& | ^ ~` two's-complement wrap; `<<` wraps (low 64
  bits kept); `>>` arithmetic (sign-extending); dynamic shift amounts
  masked `& 63` (x86 rule, no trap); static amounts are always legal
  (masking is total — no analyzer check needed).
- Map hashing: FNV-1a-64 over canonical key bytes (int: 8-byte LE;
  bool: 1 byte; str: raw bytes), slot `h % N`, linear probe, first
  empty slot ends lookup. Deterministic across engines (oracle
  re-derives the same algorithm — honesty preserved as with div/mod).

## 6. Memory representation (frozen little-endian layouts, align 8)

- record: fields concatenated in declaration order at running offsets.
- list: u64 len @0, elements @8+i·size(T).
- map: u64 count @0, N slots @8+j·(8+size(K)+size(V)); slot = u64 tag
  (0 empty / 1 occupied) + key + value.
- status: u64 tag (0 ok / 1 err) @0, i64 code @8, payload T @16.
- Value semantics everywhere: copy-on-assign, copy-on-arg,
  copy-on-return. No aliasing, no cycles, no null, no GC.
- Function ABI: aggregates pass by value in SysV slots (width =
  ceil(size/8); a value that does not fit the remaining registers
  moves wholly to the stack — never split, `str` precedent);
  aggregate returns use a caller-provided hidden slot passed as the
  first SysV slot (sret), shifting declared params right; scalar
  returns unchanged (`rax`, `rax+rdx` for str).

## 7. Engines (all five host engines carry aggregates)

- lex/parse: §2–§3. Depth parity: one charge per literal element-group
  (mirroring call arg-groups); none for field/index postfix.
- analyze: canonicalize + validate + size (shared `types.py`);
  `status`-typed flows; builtin signatures; print widening.
- RIR: `rectypes` envelope table (validated; dumps extended);
  activate reserved `make_record/get_field/make_list/list_idx/
  list_len/list_push`; new `make_map/map_get/map_insert/map_len/
  str_len/str_byte_at/status_is_ok/status_is_err/status_unwrap_or`;
  `binop ==`/`!=` generalized to identical value types; `copy`/`call`
  carry aggregate types; slot width generalized (`str` subsumed);
  builder re-checks every rule independently (never trusts type strings).
- compile: multi-slot homes, bounded copy/zero loops (caller-saved
  regs only; string-ops stay forbidden), static-offset field/element
  access, sret, per-kind `==`, print desugar needs no new helpers.
  `check_asm` unchanged.
- interp (oracle): Python structural values, same first-error order,
  same traps (div0/falloff only — aggregate ops never trap),
  per-op step cost 1, print renders §5 formats directly.
- program pipeline (16) inherits; entry still `main()->int|unit`.

## 8. Alternatives considered (strongest rejected option each)

- A1 references/borrowing for aggregates (vs value semantics):
  rejected — needs lifetimes/alias analysis the frozen analyzer lacks;
  copies are bounded and deterministic.
- A2 runtime tags/boxing (vs static inline): rejected — violates the
  static fast path and the reserved-`value`-type rejection.
- A3 arena allocation with runtime err (vs inline frames): rejected —
  inline covers the whole 19a surface; arenas add exhaustion paths
  with no 19a consumer. The exit's "arena-exhaustion yields err" is
  satisfied by static `SEM_LIMIT_EXCEEDED` rejection (exact-cap
  pairs) plus total runtime ops (never trap, proven by construction
  and max-cap execution tests).
- A4 methods/UFCS (`l.push(v)`, needs DOT overloading): rejected —
  builtins-as-functions need no new call semantics.
- A5 `match`-early for status (vs builtins): rejected — 19b owns
  match; `is_ok/unwrap_or` bridge without preempting it.
- A6 new keywords (vs contextual words): rejected — zero lexer churn,
  frozen goldens untouched, collisions impossible.
- A7 edition gate (vs always-on): rejected — new syntax was lex-errors
  before, so v1 programs parse byte-identically with no gate; the v1
  suite is the gate (must pass unchanged).

## 9. Critics (strongest objection each, all resolved)

1. Kernel correctness: "RIR slot-width generalization could miscompile
   `str` (2-slot special cases scattered in 3 files)." → Resolution:
   single `slot_width()` in shared `types.py`; `str` becomes
   `width==2` through the same path; v1 RIR goldens must pass
   byte-identical (gate).
2. Language semantics: "`l[i]` returning `status` infects all indexing;
   users will demand trapping `[]`." → Resolution: total-by-default is
   the project philosophy (runtime doc §40: caps exceeded ⇒ `err`,
   never trap); 19b `match` will make it ergonomic. Locked.
3. ABI/compat: "sret changes call codegen for ALL functions."
   → Resolution: sret applies only when the return type is an
   aggregate; scalar paths emit byte-identical code (v1 ELF hash check
   in tests). Locked.
4. Security: "map hash flooding / adversarial collisions → O(N)
   probes." → Resolution: N ≤ ~256 static, probes bounded, worst case
   deterministic and tiny; no hash-DoS of interest. Locked.
5. Minimalism: "print-desugar bloats RIR for big literals."
   → Resolution: desugar is linear and bounded; canonical output is
   required for conformance ($19d). Locked.
6. Testability: "static-only exhaustion is untestable at runtime."
   → Resolution: exact-cap accept/reject pairs + max-cap execution +
   mutants removing each check; runtime totality proven by
   construction + differential runs. Locked.
7. Cold reviewer: "status<T> vs 19b Result<T,E> collision."
   → Resolution: `status<T>` frozen with int payload; 19b adds
   `result<T,E>` + `match` WITHOUT renaming or retyping `status`
   (additive; documented forward rule in §1).

## 10. Compatibility

- Lexer: byte-identical on all previously-valid inputs (new tokens
  only for previously-invalid chars). All 49 lexer tests + 16/19
  fixtures pass unchanged.
- Parser: v1 token streams take identical branches (new branches key
  on DOT/BRACKET/SHIFT/`record`-word/`{`-in-expression — all absent
  from valid v1). All 55 parser tests unchanged.
- Analyzer/RIR/compiler: v1 constructs lower identically (new code
  paths key on new AST kinds / non-scalar type strings). All 63+8
  semantics, 48 RIR, 39 compiler, 47 shell, 46 program tests
  unchanged; v1 ELF hash spot-check required in the test patch.
- `print` of int/bool/str emits identical RIR/asm (desugar only for
  aggregate args).

## 11. Bounds inventory

Source ≤1MiB; int magnitude ≤i64max; str ≤4096B; expr/type nesting
≤256/≤8; aggregate value ≤8192B; N ≥ 1; frames ≤1024 slots;
map probes ≤N; shift masked &63; interp steps ≤10M, calls ≤100k.
Every bound has accept/reject or max/max+1 evidence.

## 12. Supersessions of the 15b shell-language notes (explicit)

`docs/design/rynorlang-shell-language.md` is NOT edited; the
following 15b forward-looking notes yield to higher-precedence
sources, recorded here per the freeze hierarchy (executable tests
and ROADMAP intent outrank older design prose):

- 15b §4 planned a `DOT` token plus `Member`/`MethodCall` kinds for
  19a field access. SUPERSEDED: the frozen executable
  `test_49_all_fixtures_have_the_expected_outcome` +
  `invalid_char_dot.rl` (`let x = 3.14;`) require `.` to stay
  `LEX_INVALID_CHAR`, so no `DOT` token and no `Member` kind exist;
  field access is postfix `->` lowering to `Field` nodes. The 15b
  "`->` stays reserved" sentence (written to justify `|>` over `->`
  for pipelines) is spent by this decision: no future construct
  needs it, and reserving syntax forever for nothing is rejected.
  No 15b test or fixture uses `->` in expression position, so all
  47 shell tests pass unchanged.
- 15b §5 prose "`|` still `LEX_INVALID_CHAR`" described 15b-era
  behavior, not an executable pin (no test or fixture asserts it).
  The ROADMAP 19a bitops requirement needs the universal `|`/`&`/`^`
  spellings (word-operators would break the frozen identifier
  property, e.g. `or` in `test_08`), so `|`/`&`/`^`/`~`/`<<`/`>>`
  become tokens. The §5 byte-identical promise itself holds: every
  previously-valid input lexes/parses byte-identically (gated by the
  full v1 suite, which must pass unchanged).

## 13. Test plan (binding)

New fixtures `tests/fixtures/rynorlang/aggregates/{good,bad}/`
(≥12 good incl. nesting/map-order/print-format/max-cap, ≥18 bad incl.
every cap boundary ±1, recursion, key-type, status-nesting, missing/
dup/unknown fields); new `tests/repository/test_rynorlang_aggregates.py`
(semantics accept/reject with exact codes, RIR JSON goldens,
determinism 3×, native differentials exit+stdout, v1-ELF-hash gate,
≥12-mutant matrix one-per-check); inventory + fixture-dir pins
updated. Guest suites untouched (no guest surface).
