# RynorLang Stage 19c — modules, manifests, std, edition policy (frozen specification)

Status: **frozen.** Executable spec for Stage 19c: `use` imports, file
modules, hash-pinned manifests, the `std` utility modules, and the
edition/version policy. Nothing here executes by itself.

Precedence: below all earlier frozen contracts; above the implementation.

## 1. Scope

IN: `use "path";` imports (file = module, mandatory alias =
filename stem, qualified `alias::name` calls and types); cycle =
error; duplicate-alias and pin/edition errors; `rlmod.json` manifests
(sha256 pins + per-module edition, enforced when present);
`tools/rynorlang/std/` utility modules (`std/` prefix mapping);
pure-RynorLang `std` helpers with suites; FROZEN `std/io` + `std/mem`
signatures with 19e-owned bindings (no host builtins in 19c);
edition policy (v1 only + additive-only rules).
OUT: package manager/network fetching (never — local files only);
selective imports (whole module); `pub` visibility (all top-level
public); relative `..`/absolute paths (rejected); default (bare)
imports (always qualified); guest/host syscall bindings for std/io
and std/mem (19e owns them — signatures frozen here so nothing
drifts); methods; version ranges (exact pins only).

## 2. Syntax (no new keywords; one new token)

- `::` → COLON_COLON (DOUBLE_TOKENS; was `LEX_INVALID_CHAR`).
- Import statement (statement position only):
  `use "lib/math.rl";` — `use` is IDENTIFIER text; after it a STRING
  means import (deterministic single-token lookahead — no
  backtracking needed: `use` + non-STRING falls back to ordinary
  expression-statement, which then fails or resolves as today since
  no function may be named `use`). Trailing `;` required (frozen).
- Qualified callee: `math::sin(x)` — postfix `::` IDENT after an
  Identifier (parser folds to callee text `math::sin`; analyzer
  splits, validates, mangles). Qualified record type:
  `p: math::Point` (parse_type accepts IDENT `::` IDENT; analyzer
  resolves). Qualified construction `math::Point(x: 1)` flows through
  the same callee path.
- `use` as a variable keeps working (`test_08`-style identifier
  property preserved for the word); no function/record/variable may
  be NAMED `use`... variables named `use`: `let use = 1;` — hmm,
  `use "x";` vs `use;`: lookahead distinguishes (STRING follows only
  for imports). A variable named `use` REFERENCED as `use;` parses as
  expression-statement ✓ works. Only fn/record named `use` are
  reserved (`SEM_DUPLICATE`, like `match`).

## 3. Resolution and identity

- Paths are project-relative (`/`-separated, no leading `/`, no `..`
  segments — violations are `MOD_NOT_FOUND` with explanatory text),
  resolved against the importing file's directory. `std/` prefix maps
  to the toolchain directory `tools/rynorlang/std/` (explicit,
  documented, deterministic — the only magic prefix).
- Alias = filename stem; must match `[A-Za-z_][A-Za-z0-9_]*` else
  `MOD_NOT_FOUND` ("module alias must be an identifier"). Same stem
  twice (even via different spellings) → `MOD_DUPLICATE`. Self-import
  (`use` naming its own file) → `MOD_CYCLE`.
- file = module: every top-level function and record is importable;
  no selective lists, no visibility modifiers.
- Calls and record uses are ALWAYS qualified (`m::f(...)`,
  `m::R{...}`, `p: m::R`); bare names never resolve into imports
  (no ambiguity, no shadowing analysis across files).
- Name mangling for the merged namespace: `{alias}__{name}`. Any
  collision with a user-declared name (in any file) is
  `MOD_DUPLICATE` at load (deterministic, source order). Calls
  rewrite to mangled names during per-file analysis; `main` stays
  bare and must live in the entry file (a dep may also define
  `main` — it merges as `alias__main`, unreachable but harmless).
- Diamond imports load once (visited set); cycles (incl. self) are
  `MOD_CYCLE` naming the chain `a -> b -> a`. Analysis order:
  entry first, then depth-first `use` order (first-error follows
  reading order). Load errors (not-found/cycle/pin/edition) precede
  all semantic errors deterministically.
- `use` statements validate (alias known) then drop out of the stable
  AST (directives, like comments — nothing downstream changes shape).

## 4. Manifests (`rlmod.json`)

- Location: the entry file's directory only (no parent walk).
  Absent = unpinned mode (resolve + analyze, no hash checks).
- Format: `{"modules": {"lib/math.rl": {"sha256": "<hex>",
  "edition": "v1"}}}` — keys are project-relative paths with `/`
  separators. Unknown top-level/file keys → `MOD_BAD_MANIFEST`;
  malformed JSON → `MOD_BAD_MANIFEST`; wrong hash → 
  `MOD_PIN_MISMATCH`; edition other than `"v1"` →
  `MOD_EDITION_MISMATCH`. Imported files missing from a present
  manifest are... REQUIRED to be pinned (strict: unpinned import
  with a manifest present → `MOD_PIN_MISMATCH` naming the file).
  `std/` files are pinned by the toolchain, never the manifest.
- Rationale for strictness: a manifest that silently skips files is
  not a pin. Strictness is testable (each rule has a fixture).

## 5. Edition policy (frozen)

- Exactly one edition exists: `v1` (the whole language through 19b,
  default everywhere, no declaration needed).
- Growth rules (binding on all later stages): additive-only (new
  syntax = previously-error inputs; new names reserved with
  collision checks); v1 inputs behave byte-identically forever
  (the full v1 suite is the gate — it must pass unchanged); a future
  breaking feature needs a new edition name + its own RFC + an
  edition-gated test matrix (15b precedent); editions never rename
  or retype frozen constructs (`status` stays).
- Mixed-edition programs: rejected (`MOD_EDITION_MISMATCH`) until a
  later RFC defines cross-edition rules.

## 6. `std` modules

- Location `tools/rynorlang/std/`: `list.rl` (contains, count_where
  via explicit loops... no — first-order only: `contains(l, v)`,
  `first_or(l, d)`), `result.rl` (`ok_or`, `err_code`,
  `is_ok_or`?... keep tiny and real), `str.rl` (`starts_with`?... no
  slicing available — `contains_str`? hmm, without concat/substr the
  str helpers are thin: `is_empty`, `len_eq`?...). Exact contents are
  implementation detail within this bound: first-order, pure,
  tested; each with a conformance program exercising every function.
  (Slice H `starts_with` speculation stays out unless a std function
  genuinely needs it — it does not.)
- `std/io` + `std/mem` SIGNATURES (frozen, unimplemented-host):
  `io: print_line(str), read_file(path)->status<str>,
  write_file(path, str)->status<int>`... hmm, write returns what?
  status<int> (bytes)? and `mem: alloc(size)->status<Addr?>`...
  there is no pointer type! Memory API without pointers is
  meaningless — DEFER THE SHAPES TOO: freeze only the NAMESPACES
  (`std/io`, `std/mem` reserved) + the rule that their functions
  arrive with 19e's execution environment (guest bindings over the
  18b/18c boundary; host bindings for testing). NO function
  signatures invented (inventing unimplementable signatures is the
  guessing this process forbids — the namespaces + ownership rule is
  the honest freezable unit).
  Hmm — wait, but the ROADMAP says "memory and file APIs". Freezing
  only namespaces feels thin... but it is exactly what's freezable
  without an execution environment. The 19e RFC will define the
  functions against real guest/host capabilities. Documented as such.

## 7. Diagnostics (additive MOD_* family; frozen SEM_* untouched)

`MOD_NOT_FOUND` (missing/unreadable/illegal path/bad alias),
`MOD_CYCLE` (chain named), `MOD_DUPLICATE` (alias clash or mangle
collision), `MOD_PIN_MISMATCH` (hash or unpinned-with-manifest),
`MOD_EDITION_MISMATCH`, `MOD_BAD_MANIFEST`. First-error preserved
(load phase before semantics; reading order within phases).

## 8. Alternatives considered

- A1 bare imports (`use "m"; sin(x)`): rejected — cross-file
  shadowing analysis for zero benefit; qualification is explicit.
- A2 `pub` modifiers: rejected — everything public is simpler and
  sufficient at this scale.
- A3 search paths (`$RLPATH`): rejected — relative resolution is
  deterministic and hermetic; no environment dependence.
- A4 manifest-per-module sidecars: rejected — one project manifest
  is fewer moving parts; strictness covers integrity.
- A5 `as` aliases: rejected — stem aliases are deterministic;
  renaming adds surface without a 19c consumer.
- A6 `m.sin()` / `m->sin()`: rejected — DOT is frozen-invalid,
  ARROW means field access; `::` is free and unambiguous.
- A7 implementing std/io+std/mem host builtins now: rejected — no
  execution environment needs them yet; signatures without bindings
  would be stubs (forbidden); 19e owns bindings with real tests.

## 9. Critics (strongest objection each, all resolved)

1. Kernel correctness: N/A (host-side only).
2. Language semantics: "mandatory qualification is verbose for 19e's
   1.5 kLOC compiler." → Resolved: verbosity is explicitness; the
   alternative (flat + whole-project unique names) is worse at that
   scale, and `::` is two characters. Locked.
3. ABI/compat: "mangling changes downstream names." → Resolved: only
   for imported names (new programs); v1/stable names byte-identical;
   collisions are deterministic errors, never silent. Locked.
4. Security: "path traversal (`..`, absolute) escapes the project."
   → Resolved: rejected at load (`MOD_NOT_FOUND`); manifest keys are
   normalized project-relative; hashing is sha256 over file bytes.
   Locked.
5. Minimalism: "manifest strictness (unpinned-with-manifest errors)
   annoys." → Resolved: a pin that skips files is theater; strictness
   is the documented, tested contract. Locked.
6. Testability: "host-only modules prove nothing about 19e's guest
   use." → Resolved: 19c proves the language mechanism (resolve,
   mangle, merge, differentials through imports); guest bindings are
   19e's exit with 19e's tests. Locked.
7. Cold reviewer: "two namespaces (files + aliases) confuse."
   → Resolved: one rule — alias is always the stem, always required,
   always qualified. Documented with examples. Locked.

## 10. Bounds inventory (additive)

`use` statements ≤ 64 per file, import depth ≤ 16, total modules ≤
64, manifest ≤ 64 KiB, module file ≤ 1 MiB (frozen lexer bound),
alias length ≤ 64. Each bound has accept/reject evidence.

## 11. Test plan (binding)

New dirs `tests/fixtures/rynorlang/modules/{good,bad}/` + std
conformance programs; new `tests/repository/test_rynorlang_modules.py`
(inventory, resolve/alias/mangle unit behavior, accept/reject with
exact MOD_*/SEM_* codes, cycle/pin/edition fixtures incl. tampered
hashes, determinism incl. multi-file, differentials THROUGH imports
with exit+stdout, std conformance, ≥10-mutant matrix: cycle-undetected,
double-load-inconsistent, alias-normalization, pin-skip, unqualified
resolution, mangle-collision-missed, edition-ignored). v1 suite green
unchanged. Guest suites untouched.
