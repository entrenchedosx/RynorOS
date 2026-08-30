# Kernel execution: ownership and context contract

## Purpose and status

Audited Stage 7: single-CPU, ring-0 kernel threads in the one active kernel
address space. The fixed table has eight slots: bootstrap plus seven workers.
There is no process, alternate CR3, user mode, SMP, priority queue or blocking
wait facility. The scheduler has no test tick limit; the boot self-test masks
its own PIT source after each measured phase.

## Public interfaces and lifetime

`ksched.h` defines the interfaces. `scheduler_initialize` is a once-only
foreground IF=0 operation after VM/IRQ setup. It registers the current
linker-stack context as bootstrap. Initialization is separate from the tests.

`thread_create(&id, entry, arg)` requires foreground IF=0. It validates an entry
in linked kernel text, finds a FREE slot, allocates its stack internally and
publishes READY only after complete success. Failure leaves the ID and resource
state unchanged. No caller-provided stack or fictitious address-space pointer
is attached. `thread_id` is a monotonically increasing nonzero 64-bit value;
IDs are never reused, and creation fails rather than wrapping at 2^63.
The ID is not a pointer to a recycled slot.

`thread_yield` saves/disables IF before touching scheduler state. It either
retains the lone current thread or selects the next READY slot. On resumption,
it checks that the selecting path already set current correctly, and restores
the original IF. It never overwrites current to hide bookkeeping errors.
Yield rejects IRQ context and held locks.

`thread_exit` disables IF, forbids bootstrap/IRQ/locked exit, marks the current
worker EXITED and resumes the next READY context. It **never frees its live
stack**. Returning from an entry function enters this same exit path.
Bootstrap remains runnable; there is no implicit idle thread or halt fallback.

`thread_join(id)` is a nonblocking reap, not a sleep/wait operation. It requires
foreground IF=0 and an EXITED non-current worker. It unmaps/releases that stack
before clearing the slot. Self, bootstrap, live, nonexistent and reaped IDs fail.
There is no detached-thread or cancellation API. A second exit cannot normally
execute: exit is noreturn and EXITED slots are never selected.

`thread_state`, `thread_statistics`, `thread_current`, ready counts and scheduler
statistics provide bounded inspection. Output pointers are trusted valid kernel
objects, not userspace buffers. No API protects against arbitrary malicious
writes by other ring-0 code.

## Stack ownership and layout

`kstack.c` owns eight independent virtual slots beginning at
`0xffffe00000000000` (PML4 index 448). Slot stride is five 4096-byte pages:

- Lowest page: absent guard, **no physical backing allocated**.
- Next four pages: supervisor RW/NX payload, 16 KiB, zeroed through real mappings.
- Stack top: exclusive end of the payload; stack grows downward.

Every payload/table frame comes from the existing PMM/VM. The first stack
requires four data frames plus three VM table frames; subsequent stacks share
that branch. There is no static physical stack pool and no redundant guard frame.

A zero-initialized `kstack` handle is immovable. A private registry owns its
physical-frame list and binds it to that exact handle address plus generation.
Copying the handle does not transfer ownership. Slot/generation mismatch and
stale generations fail; metadata supplied in a handle cannot nominate a frame
to release. Stack creation preflights guard and payload VAs before mutation.
Partial map failures unmap/invalidate before releasing data frames and restore
PMM/table counts; internal cleanup errors halt.

Destruction checks the complete guard/payload/permission/PMM ownership set
before altering any mapping, rejects the currently executing stack, unmaps each
payload before releasing its frame, clears the private record and invalidates
the handle. Corrupted mapping ownership fails without partially freeing a stack.
The thread table owns its internal handles for their entire lifetime.

A guard access from a healthy stack produces a real fatal #PF, and execution
from payload produces NX #PF. **This does not make actual stack overflow
diagnosable:** an exception delivered on an already exhausted stack can still
double/triple-fault because no TSS/IST emergency stack exists. Large jumps can
also skip a single guard page. Do not claim general overflow recovery.

## Scheduler state invariants

FREE has no live ID/stack. READY has a valid saved context. Exactly one live
thread is RUNNING and equals current. EXITED retains its stack until reap and
is never selected. IDs are distinct and bootstrap cannot become FREE/EXITED.
`scheduler_check` checks live invariants, not merely self-test counters.
Unknown current pointers are compared with table addresses before dereference.

Round-robin scans slots immediately after the current slot, wrapping once.
It never derives an array index from an external ID. This gives cyclic selection
among continuously READY threads when interrupts are serviced; it is **not**
a wall-clock fairness guarantee. A thread that keeps IF=0 can starve others.

## Exact assembly/C context contract

The shared 176-byte `exception_frame` retains Stage 2 layout:
r15..r8 at 0..56; rdi/rsi/rbp/rdx/rcx/rbx/rax at 64..112;
vector 120, error 128; RIP 136, CS 144, RFLAGS 152, RSP 160, SS 168.
C static assertions check the assembly-relevant offsets.

IRQ entry saves all fifteen non-RSP GPRs and the hardware frame, clears DF for C,
aligns RSP to 16 bytes before calls, and keeps the original frame pointer in
callee-saved RBX. IRQ0 callback runs with IF=0; PIC EOI is issued before
`sched_tick` copies the interrupted frame into the current record, chooses the
next READY context and returns its saved frame. No callback allocates or calls VM.
An explicit IRQ-context flag rejects scheduler lifecycle calls from a handler.

Before assembly consumes the returned pointer, `sched_handoff(original, selected)`
checks pointer provenance, current/state, stack range, text RIP, selectors,
normalized vector/error and supported flags. An arbitrary pointer is never
dereferenced first. Unchanged IRQ frames must be on the current stack; switched
frames must be the selected thread's private saved frame. Only then does assembly
move RSP, restore GPRs, skip vector/error and execute IRETQ.

`thread_switch(out, next)` is a **SysV function-call boundary**, not an
instruction-level checkpoint. It captures registers as they stand on entry,
the actual IF=0 flags, RSP pointing at its return address, and a local resume RIP.
A later IRETQ lands at that resume label, then RET returns to C, which restores
the caller's saved IF. Caller-saved registers/arithmetic flags have ordinary
SysV call semantics; callee-saved registers are preserved. The old unaligned
error-slot store and hardcoded IF=1 were removed.

`sched_resume(next)` is the common one-way restore tail; exit uses it only
after validation with IF=0. Fresh frames enter a C trampoline at payload_top-8,
with a dummy return word, satisfying SysV RSP mod 16 = 8 on function entry.
IRQ-time saves preserve the actual interrupted GPRs and flags.

64-bit mode IRETQ restores SS:RSP even for CPL0-to-CPL0 returns. CS=0x08 and
SS=0x10 are enforced. DS/ES and FS/GS state remain the shared kernel setup:
threads may not install private segments/TLS. No FPU/SIMD/debug-register context
is supported; compilation remains general-registers-only with no red zone.
Arithmetic flags, DF, IF and RF are preserved; TF, IOPL, NT and VM are rejected.
RF must be accepted in actual hardware frames, not confused with an invalid bit.
NMI remains masked and no nested scheduling is supported.

Frame validation accepts the entire PIC vector range 32..47, not only IRQ0.
Only IRQ0 drives selection; other IRQs retain their interrupted context. The
self-test executes INT 39/47 with the PIC masked and ISR clear to exercise real
CPU frame creation and IRETQ through the spurious IRQ7/15 paths. This is not a
claim of real external IRQ7/15 delivery or device-driver support. A timer-only
validator mutation must fail at the final handoff gate.

References: [Intel SDM, 64-bit interrupts/IRET](https://cdrdv2-public.intel.com/868137/325462-089-sdm-vol-1-2abcd-3abcd-4.pdf)
and [AMD APM Volume 2, RF and interrupt state](https://docs.amd.com/api/khub/documents/sD1_QL~h4Afq2_tvzxqqSQ/content).
They specify hardware behavior; no other kernel implementation was copied.

## Synchronization

`irq_save` returns only prior IF and executes CLI with a compiler memory barrier.
`irq_restore` explicitly restores both IF=0 and IF=1 cases; it does not promise
arithmetic-flag restoration. Nested pairs preserve the outer state.
Enabling interrupts while a lock is held fails closed.

The UP `spin_lock` interface returns success/failure rather than spinning
forever on contention: with IF=0 on one CPU, no other context can release a
contended lock. Acquisition requires IF=0, binds owner/thread-or-IRQ context and
handle identity, and increments held-lock accounting. Recursive/copied/wrong
unlock fails. Yield and exit with held locks are forbidden. This is deliberately
not an SMP spinlock or a sleeping mutex; no atomic multi-CPU guarantee is implied.

Compiler-emitted struct copies/zeroes use small original freestanding
`memcpy`/`memset` byte routines, not type-punning loops or a host libc.

## Tests and limitations

`scheduler-test.c` and `scheduler-test.asm` are separate from subsystem code.
Tests cover eight stack slots, seven workers, copied/stale/foreign ownership,
guard/payload map conflicts, actual OOM with 0..7 frames left, partial table/data
rollback, zeroed reuse, repeated create/yield/exit/reap, live/self/stale joins,
IF nesting, and foreground/IRQ lock restrictions.

Three busy assembly workers make **no calls, yields or HLT** during the measured
loop. Each checks seeded GPRs, DF/CF and its real RSP. After 24 hardware ticks,
saved IRQ RIPs must be inside that loop and all workers must have repeated
preemptions/dispatches; host tests compare against actual ELF symbols.
Another 24-tick phase has exactly two runnable contexts; a final 24-tick phase
has only bootstrap and must cause no additional switches. The test, not the
scheduler implementation, owns these budgets.

Hardware guard/NX and corrupt-current/RSP/selector/handoff variants fail closed.
Real omitted switching, omitted frame release, stale-ID lookup, ownership-check,
register-restore and IF-restore mutations must fail. Exact verification and
remaining limitations are in [the Stage 7 audit](../reports/stage7-audit.md).
