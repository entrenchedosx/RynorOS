# Kernel execution infrastructure — thread stacks, switching, scheduler

**Historical report from untrusted commit 3187675, superseded by
[the independent Stage 7 audit](stage7-audit.md).** Its preemption/ownership
claims below were not adequate evidence: the clean audit baseline failed an
integration test, ownership and IF probes exposed defects, and the two original
negative tests merely changed assertions. Current code no longer uses backed
guard pages or caller-supplied stack/frame ownership.

Stage 7 adds real per-thread kernel stacks with guard pages, genuine context
switching, and a deterministic round-robin scheduler driven by the PIT IRQ0. It
is a forward milestone on top of the Stage 6 heap baseline
`4c537f037924dc21f7ae7e48aa88c8a4329bb836`.

What was implemented:

- `kernel/mm/kstack.c`: per-thread stacks, each one faulting guard page below four
  RW/NX payload pages, using real PMM frames in a dedicated virtual slot
  (PML4 index 448). The guard frame is held for the stack lifetime but its page is
  non-present so underflow faults. All failure paths roll back frames before
  leaving a mapping.
- `kernel/core/thread.c`: a bounded 8-thread array (index 0 is the bootstrap
  context), full lifecycle (create/yield/exit/join), round-robin run queue, and
  irq-save/spinlock synchronization. `sched_tick` returns the frame to resume so
  IRQ0 truly preempts between threads.
- `kernel/arch/x86_64/switch.asm`: `sched_resume` (one-way) and `thread_switch`
  (symmetrical capture-and-`iretq`).
- `kernel/interrupts/irq.c` + `exceptions.asm`: the IRQ path now resumes from the
  frame `irq_dispatch` returns; `irq_set_handler` lets the scheduler take over
  IRQ0 from the one-shot heartbeat.
- Host validator `tools/host/sched_output.py`, repository fixture test, and
  boot/integration updates.

Verification in QEMU (TCG, 64 MiB, single CPU): the scheduler self-test runs 60
timer preemptions with over a million worker executions across four real threads,
proves at least one genuine supervisor switch, shows each worker resumed on its
own distinct stack, terminates and joins all workers, and restores PMM accounting
exactly. `pmm_check`/`vm_check`/`heap_check`/`scheduler_check` all pass after the
post-IRQ accounting line.

[Scheduler design](../design/scheduler.md) describes the actual API, invariants and
limits. [Codebase audit](codebase-audit.md) records the prior Stage 6 baseline.
[Stage 7 audit](stage7-audit.md) is the current verification record.

Remaining limitations: eight threads, 16 KiB payloads, one CPU, a shared address
space, round-robin only, and preemption that stops at a bounded budget. No user
mode, processes, drivers, filesystem, or RynorLang compiler were added; the
branding/icon and README polish were deliberately deferred to a documented
follow-up.
