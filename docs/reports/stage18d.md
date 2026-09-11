# Stage 18d verification and close-out — native shell, REPL, and evaluator in CPL3

Status: **implemented and verified** (slices A–G). This report closes the
Stage 18d roadmap row. Slices A–G were implemented in commits
`c182078`…`1a5a390`; this report records the independent re-verification,
two repairs found during it, and the exact exit evidence. Nothing in the
frozen ABI (`docs/design/stage18d-abi.md`) was changed by this close-out.

## 1. Baseline commit

`1a5a390` (Stage 18d Slice G) plus verification repairs in `126344e`
(harness only; no product change). Working tree was clean at `1a5a390`
(no staged/untracked deltas); HEAD is a linear descendant of the
trustworthy `68501e8` (Stage 18c) through the six documented slice
commits. No post-`1a5a390` product change existed to audit.

## 2. Initial test totals

At audit start: repository suite RED under `build.py test`
(`loader_errors`: `test_cplshell.py: No module named 'tools'`) and 6
Windows-side toolchain-missing failures (no clang/nasm/QEMU on PATH).
Cause established before any patch: harness import-order regression
(Slice C/D/E modules) plus absent canonical toolchain on this machine —
not product regressions.

## 3. Scope (REQUIRED / DEFERRED / OUT OF SCOPE)

REQUIRED (frozen ABI §§A–F): syscalls 3–8 (`read`, `spawn`, `wait`,
`terminate`, `fread`, `spawn_pipe`) with exact register contracts;
`sys_err`/`proc_state`/selector enums; 96-byte `spawn_spec`; 64-bit
generation handles; C argv handoff; RYNX v2 bounded windows; CPL3 shell
with filesystem scripts; true streaming pipelines with backpressure;
Ctrl-C aborts; `$?` status; bounded resident evaluator with
transactional commit, dual arenas, and `len()`.

DEFERRED by the ABI itself: blocking `read` (bit 0 reserved, identical
signature later); `STDIN_FILE`/`STDOUT_FILE` selectors (must-be-0);
`fread` never gains a flags word; heap/arena windows RESERVED.

OUT OF SCOPE (unchanged): kernel evaluation (placement guard holds —
no `kernel/shell/repl*`, no `rl_*` references under `kernel/`);
compiler/codegen inside the evaluator; RIR/VM/JIT; filesystem
create/enumerate/growth (scripts are read-only files baked by the host
`fs_image` builder); networking/graphics/Windows (Stages 20–21).

## 4. Architecture decisions (inherited, verified — not re-decided)

- Nonblocking-only syscalls with `AGAIN` + untouched outputs; counts
  exclusively via out-params so counts never collide with statuses.
- Atomic dual admission (`spawn_pipe` admits both children or neither;
  both handle outputs published last with rollback).
- `wait` validates `status_out` before reap (terminal results never lost
  to a bad pointer); shell `$?` cached in userspace.
- Whole-buffer re-analysis item-at-a-time with scratch-symtab
  swap-on-success; session/≤8192B, symbols ≤128, values arena ≤5120B,
  per-submission arena 4096B (ABI §F budget: 30720 of 32768).
- Ownership tripwire: `RL_PARTNAME|RL_SPTR` strings can never commit;
  `rl_own_check_tab` runs pre-swap; `sess_used` rolls back on failure.

## 5. Files/subsystems changed (slices A–G, verified present)

`kernel/core/read-test.c`, `proc.c`, `pipe.c`, `shd.c`, `load.c`,
`user.c`, `thread.c`, `drivers/keyboard.c`, `interrupts/irq.c`,
`include/uapi.h|proc.h|pipe.h|shd.h|syscall.h`,
`user/shell/sh.c|sh_key.*|sh_parse.*|rl_lex.*|rl_parse.*|rl_sem.*|rl_eval.*|rl_mem.h`,
`user/lib/rt/rt.c|rt.h|rt_gate.asm|rt_pipe.h`, `user/proc-tests/`
(shell helpers, pipe/file probes, `rltest.c` scale driver),
`tools/host/` validators, `tools/rynorlang/program.py` (+`link_script`
parameter, default byte-identical) and `rynoros_v2.ld`.

## 6. ABI changes

Additive only, per the frozen document: syscalls 3–8, `sys_err`
append-only, new selectors reserved-zero, RYNX v2 version-gated (v1
1pp/1pp loads byte-identically through the unchanged default script).
Syscalls 0–2, register file, fd0/fd1 meaning, pipe short-0/`-1`
discipline, and all prior error classes unchanged. No renumbering.

## 7. Implementation summary

- Slice A/B: nonblocking `read` over keyboard stdin (LOST-marker loss
  epochs), six-arg gate (`rt_gate6`), output-publication-last
  syscall substrate.
- Slice C: process table (slot|gen<<32), `spawn`/`wait`/`terminate`,
  argv construction under the §E stack layout, RYNX v2 loader.
- Slice D: stateless `fread` (≤16K over 4K staging), `/bin` bare-name
  discovery, kernel 4096B pipe rings with generation endpoints,
  atomic `spawn_pipe`, 8192B wrap/streaming proof.
- Slice E: CPL3 shell (`sh.c`), filesystem scripts
  (`/test/session.sh` → `[SH] script done status=0`), Ctrl-C paths.
- Slice F: resident bounded evaluator (lexer/parser/checker/tree-walk
  over the frozen grammar; statuses syntax/semantic/NOTIMPL→2, trap→129).
- Slice G: `len(expr)` as `RLN_LEN` (exact-word callee only; bare
  `len`/`length` keep frozen meanings; same depth charge as CALL;
  one-argument type rule; pure byte length at eval).

## 8. Error semantics

Language syntax/semantic/NOTIMPL → status 2 with `[SH] error syntax` /
`[RL] reject <class>` / `[RL] notimpl <kw>`; eval trap (div0) → 129
`[RL] trap div0`; resource exhaustion → honest NOMEM (`[RL] error`,
never corruption); commands/pipelines/Ctrl-C follow Slice E exactly;
`[RL] stats` only after evaluator submissions (Slice E transcripts
byte-identical). Parser/semantic/runtime classes never collapse
(verified by `*_rejected_twins`, `*_rejections`, differential suites).

## 9. Memory/resource model

CPL3 shell+evaluator live in the frozen data window (§F budget above);
max-state NOMEM test pins exhaustion-is-honest. Kernel side: PMM/heap/
table balance per program with live-count return to 0 against a pinned
monotonic watermark; process/pipe admission fully rolled back on
failure (no live child/pipe/zombie/leaked context on failed dual
admission); zombies reaped via wait race rules (A-first/B-first both
waited, `ALREADY_GONE` on the race).

## 10. Bounds (tested max-1/max/max+1 where applicable)

Session 8191/8192/8193, 128-symbol cap, string lifetimes, near-8K
perf, 50× leak walks (via in-guest `/bin/rltest` — keyboard sessions
cannot type scale inputs inside a boot deadline); `read`/`write`
≤4096; `fread` ≤16384; path ≤32; argv ≤8, total ≤256 incl. NULs;
`spawn_spec` reserved words must-be-0; pipe ring 4096 with wrap;
expression depth bounded with bypass mutant.

## 11. Behavior tests (all green, guest transcripts only)

`test_input` 7, `test_proc` 10, `test_pipe` 15, `test_cplshell` 26,
`test_rleval` 56, `test_rlen` 28 — 142 guest tests. Script execution
from the filesystem (`test_script_execution`), wrap transfer
(8192B/4 turns/`overlap=1`), bidirectional backpressure
(`full≥1`/`empty≥1`), 10 rotating-seed reuse iterations byte-exact,
differential evaluator conformance (pure/session/commands sessions),
scale suite (session/symbol/string/perf/depth/leak).

## 12. Adversarial tests

Hostile per-syscall matrices (bad pointers, wrap, stale generations,
RX targets, reserved enums), malformed RYNX/paths, truncated scripts,
`length` vs `len` prefix attacks, NUL-counting, history-bias and
cstring-scan attacks on `len`, Ctrl-C at every pump stage. No
canned-output branch survives the host-variable scripts (alternate
39-key and token-parametrized sessions).

## 13. Mutant evidence (95 guest mutants, all RED when broken)

input 6, proc 8, pipe 12, cplshell 16, rleval 20, rlen 12, load 7,
userspace 9, rt 5 — each boots a mutated tree, predicts RED, observes
RED with the exact reason (sequential-pipe deadlock, dropped rollback,
stale ownership, skipped scratch reset, precedence swap, depth off,
kernel-eval link, host-answer oracle, `len` type/arity/spelling
mutants, …), then restores. The `len`-via-command and
`len`-history-bias mutants pin the exact-word/no-state-leak rules.

## 14. Compatibility regressions (full matrix, §15–16)

Adjacent stages re-run after every slice: `test_load` 15,
`test_userspace` 18, `test_rt` 12, `test_filesystem` 19 green; then
the untouched-stage sweep: scheduler 23, keyboard 26, shell 9, heap 5,
pmm 7, vm 8, runtime 35, display 31, storage 10, audit 4 green.
`test_boot` 13/14 — the single RED is the SeaBIOS SHA-256 pin
(canonical QEMU 10.0 ROM `ae6f6aa9…` vs this matrix's QEMU 11.1.1 ROM
`b7dea28b…`); transcript, RIP, reap, and version assertions all pass.
The pin is provenance, not product behavior: left unchanged.

## 15. Full test totals

Repository: 602/602 green (`build.py test`, incl. participation
inventory). Integration: 377/378 green plus the 1 environment-pinned
firmware hash above; inventory counts unchanged (no test added or
removed by the repairs). Default image: 1048576 bytes, 545 payload
sectors. Verification toolchain (this matrix only): archlinux clang
22.1.8 / lld 22.1.8 / nasm 3.02 / QEMU 11.1.1 / SeaBIOS `b7dea28b…`;
byte identity is claimed only for identical inputs/tool versions per
`docs/design/bootstrap-dependencies.md`.

## 16. Resource balance

Every guest suite asserts delta-zero (PMM/heap/table counters,
`balanced` rows, live-count watermarks, leak walks). The 50× evaluator
leak walk and per-program alloc watermarks return to baseline; failed
submissions roll back session and scratch arenas (failed-`let`
prints-nothing, failure stats reset, Ctrl-C session reset).

## 17. Design/freeze audit — MATCH except two documented items

- MATCH: frozen ABI §§A–F, output-publication rule, contradiction-attack
  answers (15/15 hold in the implementation), placement (no kernel
  eval, no compiler in evaluator, no RIR/VM/JIT), v1 byte-identical
  default path, append-only errors, reserved-zero enforcement.
- AUTHORIZED CHANGE: `rt_gate.asm` `_start` now forwards argc/argv
  (ABI §E C-consumption convention; old binaries carry their own stub,
  unaffected — verified by 18a–18c suites staying green).
- REPAIRED (this close-out, harness only): Slice C/D/E test-module
  import order (missing ROOT `sys.path` insert broke `build.py test`
  discovery order-dependently); `test_rt` mutant-compile fragility
  (mutant layout noise vs the frozen v1 window — mixed-image fallback,
  sound: no false greens; leg re-proven with a 5000-byte kernel write
  on the wire and `[RT] failure=status`).
- No FREEZE BREAK. No MISMATCH.

## 18. Scope audit

`starts_with` (Slice H speculation) was NOT implemented: no roadmap
entry authorizes it. No speculative builtins, no shell printhooks, no
`file:` selectors, no blocking reads. The evaluator accepts exactly
the frozen grammar plus `len()`.

## 19. Known limitations / deferred work

- RYNORFS v1 has no create/enumerate/growth: scripts are baked into
  images host-side; interactive sessions are keyboard-driven.
- Evaluator is a bounded tree-walk over the frozen subset (ints, bools,
  strings, `let`, `if` as expression, calls to `len` only); loops,
  aggregates, match, modules, and status values wait for Stage 19.
- Single-CPU, TCG, QEMU-only evidence; no bare-metal claim.
- This matrix's toolchain differs from the canonical pins (see §15);
  one firmware-hash pin and tighter v1 headroom (t_alloc 4085/4096
  under clang-22 vs the pinned toolchain) follow from that. Product
  invariants hold under both; no limit was raised.

## 20. Git state

Slices: `c182078`, `3d1316d` (ABI clarifications), `14680cb`,
`b8b3209`, `2682802`, `91d7728`, `1a5a390`. Repairs: `126344e`
(harness). This close-out: docs + ROADMAP only. Tree clean; nothing
pushed (push requires explicit authorization).
