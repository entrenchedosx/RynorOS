# Stage 19c close-out — modules, manifests, std, edition policy

Status: **implemented and verified (host-side).** Frozen spec:
`docs/design/rynorlang-modules.md`. Closes the ROADMAP 19c row
within the RFC scope (memory/file API *bindings* stay 19e-owned;
their signatures are frozen here).

## Baseline / scope

Baseline `97db00d` (19c RFC frozen). Scope: `use "path";` imports
(file = module, mandatory stem alias, qualified `alias::name` calls
and types), cycle/duplicate/pin/edition errors, strict `rlmod.json`
manifests (sha256 + per-module edition), the `std/` toolchain prefix
with pure-RynorLang `math`/`str`/`test` helpers, frozen `std/io` +
`std/mem` signatures, v1-only additive edition policy. Deferred per
RFC: package manager/network (never), selective imports, `pub`
visibility, `..`/absolute paths, bare imports, guest/host syscall
bindings for `std/io`/`std/mem` (19e owns them), methods, version
ranges.

## Implementation

Slices: RFC freeze (`97db00d`); engines (`e04297b`: lexer `::`
token, parser `use` statements + `alias::name` folding, analyzer
self-alias mangling with own-maps/external seeding/qualified
resolution, `module.py` loader with `compile_entry`, `program.py`
`build_module_program`, `std/{math,str,test}.rl`); fixtures + suite
(`938c4e2`); spec-compliance follow-up (`7d40bb2`: load-time
mangle-collision scan, reserved `use`, dep-main/use-as-variable
pins). Engine debugging after the RFC added nested-nominal mangling
through alias maps, type-word qualification heads (`str::f`,
`str::T`), and hermetic relative cycle chains.

## Compatibility

One new lexer token (`::` was `LEX_INVALID_CHAR`); qualification
folding only triggers on `::` adjacency (previously-error inputs).
`use` joins the reserved function/record names (`match` precedent:
`fn use` is `SEM_DUPLICATE`, `let use` keeps working) — verified
zero collisions across all prior fixtures and std sources.
Same-file duplicates stay the analyzer's `SEM_DUPLICATE`
(v1-identical; the load scan only fires on dunder exports). v1/19a/
19b suites pass unchanged (423 repo green, incl. 3 semantics mutant
anchors moved with the mangling-aware duplicate checks).

## Tests

7 good / 18 bad module projects; 18-test suite (inventory, std
conformance covering every std function, accept, cross-boundary
mangling pin, reject with exact codes, `no_main` → `COMP_NO_ENTRY`
at compile, manifest honesty (pinned-good hash matches, edition
fixture differs only in edition, hash fixture only in hash),
exact relative cycle chains, 3× determinism, 7 native differentials
with exit+stdout equality, golden pins, 6-mutant gate: pin-skip,
edition-ignored, alias-weakening, shared-dep memo removal
(→ `MOD_DUPLICATE`), load-collision removal (falls through to the
analyzer `SEM_DUPLICATE` backstop), stored-identity (breaks
qualified types)). Full repo 423 green.

## Notable semantics (audited)

- Entry files mangle by identity, so single-file programs behave
  byte-identically (the v1 gate, not a migration).
- Diamond imports analyze once (visited memo); removing the memo
  surfaces as `MOD_DUPLICATE`, which is why sharing needs its pin.
- Load errors precede semantic errors: the mangle-collision scan
  runs before analysis, with the analyzer's seed reservation as
  backstop — order proven by the fallthrough mutant.
- Diagnostics are hermetic: cycle chains use project-relative
  paths (absolute paths would leak machine layout).
- Manifests are strict: a present manifest pins every project
  import (`std/` is toolchain-pinned, never manifest-pinned).
- A dep may define `main` (merges as `alias__main`, unreachable,
  harmless); the entry must define it (`COMP_NO_ENTRY` otherwise).

## Git state

RFC `97db00d`, engines `e04297b`, fixtures/suite `938c4e2`,
compliance `7d40bb2`. This close-out: report + ROADMAP. Tree
clean; nothing pushed.
