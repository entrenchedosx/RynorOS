# Stage 18a Report — Protected Userspace Foundation

Implemented and verified: CPL0→CPL3→CPL0 protected userspace with
isolated CR3s, TSS/RSP0 exit stacks, an `int $0x80` exit/yield gate,
fault kill paths, and deterministic 2-task timer preemption. Reference
result below is the full `check` gate on the reference host.

## Scope

In: GDT user selectors + TSS/RSP0, per-context address spaces (private
low replica + shared high snapshot), `iretq` entry, `int $0x80` gate
(exit/yield/invalid-call kill), user fault kill path with vector/error/
CR2 record, IRQ0 preemption of CPL3 with faithful GPR resume, SMEP/SMAP
probe (report-only), OOM/admission rollback, host validator plus
repository and integration tests.

Out (later stages): ELF loader (18b), demand paging/processes (18c),
SMEP/SMAP enforcement + PCID/KPTI posture (18d), `syscall` MSRs, IST,
FPU/SSE user state. No self-hosting, network, GUI, or Windows changes.

## Design (see docs/design/userspace.md)

* Fixed layout per context: code `[0x400000,4K)` U-RX, data
  `[0x600000,4K)` U-RW (stop flag, counter, 15-GPR spill), guard
  `[0x7FE000,4K)` unmapped, stack `[0x7FF000,4K)` U-RW.
* GDT: null, kernel code/data (unchanged), user data `0x1B`, user code
  `0x23`, TSS pair `0x28` (available type; CPU writes back Accessed).
  IDT adds vector `0x80` DPL3 (`0xEE`); all else unchanged.
* Address spaces: `vm_clone_low` deep-copies the kernel `PML4[0]` chain
  into private tables (delivery hardware reads IDT/GDT/TSS through the
  active CR3; sharing is impossible since both contexts map identical
  user VAs), plus a by-value snapshot of kernel slots `256..508`
  (509 MMIO / 510..511 window never cloned). Snapshots go stale when
  the kernel high half mutates (`kstack` free on thread join, observed);
  they are re-copied at every entry/resume and for all live spaces at
  destroy. A missed sync fails closed in `user_check`.
* Scheduler integration without invariant changes: a thread in userspace
  is an ordinary thread with a bound link whose `kern_save` resume frame
  is `frame_valid`-compatible by construction (resume label RIP, entry
  RSP in own stack, kernel selectors, masked RFLAGS, vector/error zero).
  `sched_tick` branches on CPL3 origin (record + park + unchanged
  pick/select); gate/fault exits finish with `sched_resume`
  (`user_schedule_next`), leaving the shared assembly tail untouched.
* Stop protocol (timing-independent): from CPL3 tick 25 on, the tick
  path publishes `1` into the current user data page (supervisor store
  on the user CR3, audited to the constant VA); both tasks then exit.
  Exact on every host: 26 ticks, 28 switches, 13/13 preemptions, 14/14
  dispatches. Residual: a tick landing in the few-instruction exit
  window (~3e-7/boot) fails the exact asserts loudly, never silently.
* RFLAGS rule `0x10ED7` (RF allowed: fault delivery preserves it,
  observed on `#UD`); TF/AC/ID/IOPL/NT/VM forbidden; IF required.

## Evidence (host tools/host/user_output.py)

Trailing `[USER]` section after `[FS]`, from `[USER] initialized` to
`[USER] user verified`. Exact: 30 creates (tables all equal, sizes in
range) matched by 30 destroys per-slot; 3 map rows (VA/perm exact, PA
aligned + distinct); exits `{(0,42),(0,9),(0,7),(1,7)}`; yield `(0,1)`;
23 faults (vector/error exact; CR2 exact for fixed landmarks,
supervisor-shape for kernel landmarks, stale-CR2 unchecked, RIP in
code except fetch targets); preempts `(0,13)` + worker `13`;
`cpl3_ticks=26 ticks=26 switches=28`; 2 GPR rows (slots 0/1, counters
positive); 7 accounting lines (1 lifecycle + 3 OOM + 1 gate + 1 faults +
1 preempt); all 5 phase markers.

## Verification

* Guest self-test (kernel/core/user-test.c): lifecycle + admission (3rd
  create rejected), real PMM-exhaustion OOM rollback (0 and 5 frames),
  gate exit/yield/invalid-call kill, 23-case adversarial fault matrix
  (kernel read/write/execute, stack execute, CR3/selector/MSR/fast-
  syscall attacks, far-jump and SS type attacks, `#DE`, stack-pointer
  corruption) with exact error codes and CR2, 2-task preemption with
  GPR-pattern stability across all 26 ticks, balanced PMM/heap/table
  accounting per phase.
* Adversarial review hardening (no new services): terminal-handler
  gates (single `ACTIVE` transition, foreground-only, `RSP0`-matched),
  resume `RSP` alignment/canonicality, attach lifecycle gate,
  fault-path `RSP` containment (kill, not halt), full GDT/IDT/TSS
  runtime allowlist (bases included), per-leaf low-replica and
  high-half `U`-leaf audits, `vm_protect` supervisor-flip guard,
  stop-flag `CR3` check, `EFER.SCE`/`SYSENTER_CS` mediation, null-`LDT`
  enforcement, device-IRQ quiesce for the userspace phase.
* `python tools/build/build.py test` — repository suite green (includes
  22 `test_user_output.py` validator tests: genuine accept plus
  tamper/mutation rejection per row class, extended to the new rows).
* `python tools/build/build.py integration-test` — integration suite
  green (includes 17 `test_userspace.py` tests: shared-good full
  evidence, boot-transcript acceptance, exact rows, 8 new mutants —
  IDT-DPL, GDT-DPL, RSP0-desync, supervisor-code, skipped-transition
  (all fail closed) plus the original 3 — and 3 completion-race
  lock-in tests proving the host cannot declare success before the
  final marker).
* 9-configuration QEMU matrix green; other exception-vector images
  unaffected (CPL3 fault route only triggers on user CS).

## Limitations (carried, not new)

Single CPU; no KPTI/PCID (full-flush `mov %cr3` on every transition;
speculative side channels out of scope — architectural access always
faults); SMEP/SMAP probe-only (`qemu64` reports both absent);
two static contexts with static exit stacks (admission-controlled;
18c lifts with a process table); no FPU/SSE user state. Hostile
userspace containment (as opposed to halt) holds for the fault path;
gate/IRQ-path violations still halt fail-closed by design. Two
Two QEMU-TCG-vs-silicon divergences are pinned to the emulator values,
each with rationale: `SYSENTER` with zero CS (`#UD` vs hardware
`#GP(0)`, hence no pinned row — enforcement is the MSR check) and
noncanonical-push (`#GP(0)` vs hardware `#SS` — property proven is
delivery + kill).
