# Kernel execution infrastructure: threads, stacks, scheduler

## Purpose and status

Implemented: bounded, single-CPU kernel threads with real per-thread kernel
stacks (payload plus a faulting guard page), genuine context switching, and a
deterministic round-robin scheduler driven by the PIT IRQ0 that preempts between
threads. This is the internal kernel scheduling facility, **not** a user-process,
syscall, or SMP facility. No user mode is implied.

## Threads and shared address space

There is exactly one CPU and one active kernel address space
(`vm_kernel_space()`). Every thread, including the bootstrap context, executes in
ring 0 in that shared space. `thread_create` attaches a caller-provided,
already-allocated `kstack`; the bootstrap context becomes first-class `thread[0]`
with `has_stack=0` (it keeps the linker-owned fixed stack) so the supervisor can
preempt it exactly like a worker and later resume it.

A small fixed `threads[8]` array (index 0 is bootstrap) holds every thread. There
is no dynamic heap queue: the run queue is the set of `THREAD_READY` threads.
`pick_next` scans deterministically forward from just after `current`, giving a
strict round-robin order. States are FREE, READY, RUNNING, EXITED (zombie awaiting
`thread_join`). Both worker and bootstrap threads are valid runnable entities.

## Kernel stacks and guard pages

Thread stacks live in a dedicated high virtual region (PML4 index 448,
`0xffffe00000000000`), free from the heap slot 384, the VM window 510, and the
reserved 509/510/511. Each of up to 8 slots is one faulting guard page below
`KSTACK_PAGES` (4) payload pages. All slot frames come from real PMM.

`kstack_alloc` preflights the whole slot for conflicts, allocates
`KSTACK_SLOT_PAGES` frames, maps every slot page RW/NX, then unmaps the guard
page. The guard frame stays allocated for the stack lifetime but its virtual page
is non-present, so any underflow into the guard faults instead of silently
corrupting an adjacent stack. `kstack_free` (via `thread_join`) unmaps payload
pages, verifies the guard is still hidden, and releases the guard frame first then
each payload frame. Every alloc/free path releases all frames before failing
without leaving a mapping; rollback failures halt rather than claim success.

A fresh thread's initial `exception_frame` points RIP at a C trampoline that calls
`entry(arg)` and then `thread_exit()`, with RSP at the top of the payload beneath
a dummy return address (16-byte ABI alignment preserved).

## Context switching

Two assembly primitives in `kernel/arch/x86_64/switch.asm`:

- `sched_resume(next)`: one-way; set RSP to a saved frame, pop all GPRs, skip the
  software vector/error slot, `iretq`. Used by `thread_exit`.
- `thread_switch(out, next)`: capture the full live context of the caller into
  `out` (all GPRs, current RSP, a resume RIP that returns to the caller) and
  `iretq` to `next`; it returns to its caller only after this thread is later
  resumed. Used by cooperative `thread_yield`.

The timer preemption path reuses the proven exception entry: `irq_dispatch`
returns an `exception_frame *`; for IRQ0 it calls `sched_tick` and `exception_common`
IRETQs from whatever frame it returns. When a switch occurs, that is a genuinely
different thread's saved frame. When it does not, the same frame resumes. IRQ
handlers never allocate, serial, block, or touch the scheduler lifecycle.

## Scheduler and preemption

`sched_tick` runs only for IRQ0 while `sched_active`. It saves the interrupted
frame into `current`, marks it READY, picks the next READY thread, and returns its
frame. A `preemptions` counter increments only on an actual switch, proving the
supervisor redirected execution to another thread rather than merely servicing an
ISR.

The bootstrap test swaps the one-shot heartbeat handler to a scheduler drive ISR,
arms IRQ0, and enables interrupts. The supervisor yields the bootstrap thread
into the READY ring; the timer then preempts between all threads until a bounded
tick budget is spent, at which point the ISR masks IRQ0 and workers observe the
stop and exit cooperatively. All workers exit into joinable zombies; `thread_join`
reaps them and frees their stacks, restoring PMM exactly.

## Synchronization

Single CPU. `irq_save`/`irq_restore` (CLI and saved-IF restore) form the correct
critical section under timer preemption and inside IRQ handlers. A `spinlock_t`
is provided but is only safe when the holder cannot be preempted or yield; it is
not a substitute for disabling preemption. The scheduler's own counters are
guarded by irq-save critical sections in the worker loops.

## API errors and context

`ksched.h` declares stacks, threads, scheduler tick, yield, exit, join, ready
counts, and the sync primitives. Lifecycle calls require IF=0 and one CPU.
`thread_join` returns 0 unless the thread is EXITED and then reaps it (frees its
stack). No call in the IRQ path allocates or blocks.

## Tests

`scheduler_self_test` (in `thread.c`) creates three workers each writing a marker
on its own stack, runs them under full timer preemption, and asserts: the ISR ran
for the full budget, at least one tick actually switched threads
(`preemptions > 0`), every worker resumed repeatedly on its own distinct stack
(`runs` high, markers distinct and still mapped), all workers terminated and were
joined, and PMM allocated/free bytes are byte-for-byte restored. `scheduler_check`
asserts the idle invariant after the run. Host `sched_output.py` parses the
statistics; repository and integration tests cover the section and broken-image
variants.

## Limitations

Eight threads, 16 KiB payload each, single CPU, shared address space, no
priorities or time slicing beyond round-robin, no sleep/wake, and no user
processes. Preemption stops at a bounded tick budget rather than running forever.
Spinlocks are not SMP-safe and must not be held while yielding.
