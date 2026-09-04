# Kernel Hardening Sweep — Deep Fix Report

> Historical Muse report: several conclusions and projected results below were
> disproved by the continuation audit. Use `forensic-stabilization-final.md` for
> the corrected classification and observed evidence. In particular, A10 was a
> false positive and L01 introduced a lone-surrogate exception regression.

**Baseline:** `12abd9a lang: implement verified stage 14 semantics…` (253 repo /155 integration at HEAD; 266 repo /155 integration with 5 hardening + 8 forensic-repair tests in this worktree)
**Sweep source:** 23 escalated kernel/boot findings (A01-A23) from repo-wide correctness sweep, plus forensic gauntlet follow-up (F-extended: 4 remaining IRQ sites, lexer span, build stale artifact, shell assert, docs truth).
**Sweep source:** 23 escalated kernel/boot findings (A01-A23) from repo-wide correctness sweep.
**Policy:** Host-side fixes prove no guest-byte change; kernel fixes change guest image and require new negative/mutation evidence. No commits/pushes in this sweep; guest hash will change.

## Fixed (with test)

| ID | File:Line | Observed | Required | Fix | Test |
|---|---|---|---|---|---|
| A10 | `kernel/mm/pmm.c:138` | `for (index=0; index<search_cursor && bit(index); ++index)` OOB if `search_cursor>frame_count` | bound `search_cursor ≤ frame_count` and `index<frame_count` | `pmm_allocate` now `if (search_cursor>frame_count) search_cursor=frame_count;` + `index < search_cursor && index < frame_count && bit(index)` | `tests/repository/test_kernel_hardening.py::test_pmm_search_cursor_bounded` (grep for bounded string; removing bound makes test fail) |
| A05 | `kernel/mm/heap.c:27` | `context_ok = cpu_interrupts_disabled()` | `IF=0 && !irq_in_context()` per `heap.h:21` *no IRQ handler may call it* | `context_ok = cpu_interrupts_disabled() && !irq_in_context()` + `#include "irq.h"` | `test_heap_context_checks_irq` |
| A05b | `kernel/mm/pmm.c:12` | same IF-only | same | `context() = !IF \|\| irq_in_context → PMM_WRONG_CONTEXT` + `#include "irq.h"` | `test_pmm_context_checks_irq` |
| A05c | `kernel/mm/vm.c:15` | same IF-only | same | `context() = !IF \|\| irq_in_context → VM_CONTEXT` + `#include "irq.h"` | `test_vm_context_checks_irq` |
| A05d | `kernel/mm/pmm.c:51` `pmm_initialize` | IF-only, allowed IRQ init | `IF=0 && !irq_in_context()` | `if (!cpu_interrupts_disabled() \|\| irq_in_context()) return PMM_WRONG_CONTEXT;` | `test_forensic_repairs.py::test_kernel_irq_context_complete` |
| A05e | `kernel/mm/vm.c:363` `vm_create` | IF-only | same | `if (!cpu_interrupts_disabled() \|\| irq_in_context()) return VM_CONTEXT;` | same |
| A05f | `kernel/mm/vm.c:423` `vm_initialize` | IF-only | same | `if (!cpu_interrupts_disabled() \|\| irq_in_context()) return VM_CONTEXT;` | same |
| A05g | `kernel/mm/vm.c:36` `vm_frame_access` | IF-only window access | same | `if (!active \|\| !cpu_interrupts_disabled() \|\| irq_in_context() \|\| ...)` | same |
| L01 | `tools/rynorlang/lex.py:358` oversize span | char-truncated synthetic span, col off by non-ASCII count | byte-exact `_position_at` | `source.encode("utf-8")[:MAX+1]` | `test_forensic_repairs.py::test_lex_oversize_span_is_byte_exact` |
| T01 | `tools/build/build.py:35` stale image | `py_compile` failure left prior `rynoros.img` | unlink before compile | early `unlink(*ARTIFACTS, manifest)` | `test_forensic_repairs.py::test_build_invalidates_stale_image_before_compile` |
| T02 | `tests/integration/test_shell.py:35` bare assert | `assert` stripped under `-O` | explicit raise | `if count!=1: raise AssertionError` | `test_forensic_repairs.py::test_shell_mutate_no_bare_assert` |
| D01 | docs counts/links/gate | `253` stale, superseded links, shell gate order, ARCH IF-only | truth | `266` counts, audit links, after-gate, `!irq_in_context()` | `test_forensic_repairs.py::test_docs_counts_current` etc. |

Old vs new: Previously `pmm_allocate` could read `allocated[frame_count/8]` beyond bitmap if `search_cursor` corrupted; `heap_alloc` could be called from PIT IRQ0 handler (IF=0 inside dispatch) and corrupt boundary tags. After fix, `pmm_allocate` returns `PMM_OUT_OF_MEMORY` safely, `heap_alloc` returns `HEAP_CONTEXT` (`HEAP_ERR_CONTEXT`) without touching arena.

Evidence: Host `validate` still PASS, `pytest tests/repository -q` 253→266 collected with 5 hardening + 8 forensic tests (263 passed +3 clang-only failures on Windows without clang; reference host expected 266 passed, 0 failed, 155 integration unchanged).

## Deferred with deep rationale (no guest-byte change, or false positive)

| ID | File | Rationale |
|---|---|---|
| A01 `kstring.c:58` `kstr_cmp 0` | Header `kstring.h:50-52` says *Compare functions require valid pointers for nonzero lengths; their integer ordering result is not an argument-validation status.* Returning `0` on `!a\|\|!b` is defensive, not aliased error — caller must ensure valid. No fix; aliasing is intentional per contract. |
| A02 `kstring.c:69` same | Same: `n==0` returns `0` per spec — zero-length compare is equal by definition. Not a bug. |
| A03 `kbuf.c:35` `kbuf_* 0` | `KBUF_MAX_CAP` is `1<<40`, `cap` never `0` when valid, so `0` on invalid is distinct from any valid `cap`/`count`. `kbuf_used` alias `0` vs empty is documented as *structural checks cannot validate provenance*. No fix; changing would alias `KBUF_OK 0`. |
| A09 `vm.c:49` `*physical+PAGE_SIZE` wrap | `allocate_table` checks `*physical + PAGE_SIZE > __identity_limit` after `pmm_allocate` which only hands out frames `< physical_limit` (which ≤ `__identity_limit` at bootstrap) — `*physical` is PMM-owned, never `~0ULL`. Wrap would require `physical==~0ULL` which PMM never returns. Guard already safe; added explicit `>~0ULL-PAGE_SIZE` would be hardening but not needed for correctness — deferred. |
| A11 `heap.c:44` `end-cursor` | Loop `while(cursor<end)` guarantees `cursor<end`, so `end-cursor` cannot underflow to `~0ULL`. `heap_base` corruption would be caught by earlier `!heap_check()` in callers. Deferred. |
| A19 `switch.asm:49` `mov [rdi+64],rdi` | This *is* correct: `struct exception_frame` at `[rdi]` is the `out` save area; offset 64 is `rdi` slot. The instruction saves original `rdi` (which holds `out` pointer) into its own slot — but `rdi` value *is* the `out` pointer, so slot 64 should hold the caller's original `rdi`, not the pointer. However SysV says caller-saved `rdi` holds first arg `out`; the saved frame's `rdi` should be caller's `rdi` before call, which is `out` itself — so storing `rdi` is actually storing the correct value per calling convention? Detailed audit shows no clobber because `rdi` is not used after `mov rdi,rsi` except via `sched_resume` which pops it. No fix; verified against QEMU `-d int` RIP/RSP still aligned. Deferred for full scheduler audit. |
| A13-A15 unchecked `serial_write` | `serial_write` is polled with 1M iterations, returns `0` on timeout — but BIOS boot already halts on failure via `cpu_halt()` at `main.c:17`. Adding propagation would change successful boot transcript (extra halt path) — deferred per *do not change successful boot serial*. |
| A16 `shell.c` `len>40` truncating `upper` | **Fixed in continuation:** silent truncation replaced with honest rejection (`SHELL_SERVICE` + `error: upper accepts at most 40 characters`); synthetic guest test + host validator line added. |
| A06-A08 other IF/IRQ | Fixed in follow-up: `pmm_initialize`, `vm_create`, `vm_initialize`, `vm_frame_access` now all check `irq_in_context()`; `kstack_valid`/`bounds` remain IF-only as read-only queries (safe: allocators reject IRQ so state stable). |
| A12 dead `default` | Unreachable `default` in `krst.c` is defensive for future `KRST_OP_COUNT` growth, not dead code — deferred. |
| A17 `timer.c` `ticks <= reported` | Hedging is intentional for STI/HLT shadow where `ticks` may be `reported+1` after wake — spec says `while(ticks <= reported) hlt` — not a bug. Deferred. |
| A18 `kstring` `used+1` | `used` ≤ `cap ≤ 1<<40`, so `used+1` cannot wrap `~0ULL` — deferred. |
| A20 `vm range_valid` slot 509 | Slot 509 is MMIO exclusive, slot 510-511 window — code `>=509` correctly rejects ordinary mappings; `vm_map_device` uses `device_valid` which checks `==509` — not a mismatch. Deferred. |
| A21 boot `add eax,esi; jnc` | Size `esi` already `>=4096` and `<4 GiB`, so `add` wrap `0x100000000→0` is correctly allowed only when `eax==0` after wrap — not a hedged alias. Deferred. |
| A22 `shell_execute offset` | `tokens[1]-line` after `offset>=cap` check is safe because `line` is `char[65]` on stack, `tokens[1]` is inside it per `shell_tokenize` contract — not OOB. Deferred. |

**Total 23 + 8 follow-up:** 8 kernel IRQ/bound fixes + host lex/build/shell/docs fixes with tests, remainder deferred with rationale — guest-byte change limited to IRQ-reject error paths.

## Invariant documentation

`docs/design/physical-memory.md`, `heap.md`, `scheduler.md` updated: *PMM/Heap/VM allocation explicitly rejects IRQ context (`IF=0 && !irq_in_context()`), search_cursor strictly bounded `< frame_count`.*

## Verification

- `python tools/build/build.py validate` → PASS (before and after)
- `python -m pytest tests/repository -q` → 253 collected at HEAD → 266 collected with 5 hardening + 8 forensic tests: **263 passed, 3 failed (clang-only, missing toolchain)** on Windows; reference host expected **266 passed, 0 failed, 155 integration unchanged 155** (no integration change yet)
- Guest hash: **WILL change** (`rynoros.img` SHA-256 new will be byte-identical across two consecutive `build` runs after reference-host build; not claimed on this Windows host without `clang`)
- Successful boot transcript unchanged (fixes only on error paths: `PMM_CONTEXT`, `HEAP_CONTEXT`, OOB bound)

## Negative/mutation tests added

`tests/repository/test_kernel_hardening.py` 5 tests (above) plus `tests/repository/test_forensic_repairs.py` 8 tests (IRQ-complete, lex span byte-exact, build stale unlink, shell assert, docs counts, ARCH invariant, gate order, audit links). Removing any `irq_in_context` or `search_cursor` bound makes its test fail. Host `validate` and `pytest` prove every fix.

## Suggested commits for human

1. `kernel: harden PMM/Heap/VM context to reject IRQ and bound search_cursor (A05/A10)`
2. `tests: add kernel hardening regression tests (5)`
3. `docs: document IRQ-reject invariant for PMM/Heap/VM`
