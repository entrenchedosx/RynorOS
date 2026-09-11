# Stage 19d close-out — conformance, determinism, self-host checklist

Status: **implemented and verified (host-side).** Frozen spec:
`docs/design/rynorlang-conformance.md`. Closes the ROADMAP 19d row
within the RFC scope (full-language guest execution and the core
subset stay 19e-owned; mechanism + handoff here).

## Baseline / scope

Baseline `7c492e4` (19d RFC frozen). Scope: canonical-encoding
pins, the bounded 83-cell pairing matrix, host 3× evidence
(ungated text over the full corpus, gated linked incl. exe bytes),
guest boundary evidence, `--profile=strict` (R1/R2), the seed plan
(document), the kLOC counting rule + counter (reference only).
Deferred per RFC: full-language guest execution, the core-dialect
subset definition, the baby compiler, RNG-generated cases.

## Implementation

Slices: RFC freeze (`7c492e4`); profile plumbing + `kloc.py` +
33-test suite + 9 fixtures + `qual_construct` project + guest
repair (`6025ddb`). The guest work caught real 19a drift: the
`len` builtin closed the intentional host/guest divergence the
`rlen` G7 rows pinned, and QEMU-gated suites never ran since —
re-derived against current host behavior with host-value
agreement, not just code agreement.

## Compatibility

Additive-only: `--profile` defaults to current behavior
(byte-identical; the strict check never fires); new code
`SEM_PROFILE_EXCLUDED` fires only under explicit `strict`;
R2 reuses `MOD_PIN_MISMATCH` only under explicit `strict`;
no frozen semantic changed (456 repo green, incl. all v1/19a–19c
suites and the 19d RFC's own references).

## Tests

33-test conformance suite (pairing inventory over 83 cells, 9 new
fixtures + project with eyeballed goldens, cap boundaries ±1 at
exact bytes, ungated text 3× over 35 corpus programs, banned-
source pin, R1/R2, kLOC pins, 8-mutant gate: canonical-string,
print-order, key-bytes, profile-bypass, manifest-order,
merge-order, step-budget, module-cap). Gated linked 3× covers
exit+stdout+exe-bytes for all 35.
Guest (override firmware `b7dea28b…39946e`, pinned firmware
absent — environmental): `test_rlen` 28/28, `test_rleval` 56/56
green; session transcripts 3× green each (`g2_len_session`,
`f8_differential_session_a`).
Evidence: corpus aggregate sha256
`713c919b14255b5f65889510a0889636195c482ec4361d85f0b9e8562166117d`
over (name, exit, stdout-hash, exe-hash) × 35. kLOC reference:
host `tools/rynorlang/*.py` = 12 files / 8581 effective lines;
`std/` = 3 files / 65 lines (informational, not a gate).
Full repo 456 green.

## Notable semantics (audited)

- Strict is a lock, not a hint: R1/R2 violations are errors
  with codes, in their existing phases (analysis load order
  preserved).
- Text determinism is ungated and total (lex→asm over the full
  corpus); linked bytes are toolchain-determined (gated,
  recorded); guest parity needs 19e's execution environment —
  three independent layers, each with its own evidence.
- Manifest comparison stays order-insensitive (proven by the
  order-sensitivity mutant flipping to red); hashes cover file
  bytes only.
- The `len(nosuch)` shell row documents a real parsing fact:
  bare-identifier call arguments are `CmdExpr` in shell
  edition (host rejects unknown-command, guest undeclared) —
  divergence kept DOCUMENTED, not papered over.

## Git state

RFC `7c492e4`, implementation `6025ddb`. This close-out:
report + ROADMAP. Tree clean; nothing pushed.
