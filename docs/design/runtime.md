# Stage 10: basic kernel runtime

## Purpose and scope

Adds bounded string/buffer primitives and small, pure, bounded runtime services,
then runs those services from real worker threads through the Stage 7 scheduler.
This is **kernel runtime infrastructure**, not a userspace ABI. There is no
syscall, no Ring 3, no per-process page table, no IPC and no loading of
user programs — those remain Stage 18. Everything runs at ring 0.

Serial remains the primary output; the framebuffer is unchanged.

## Bounded strings

`kernel/runtime/kstring.c` provides freestanding helpers with no libc dependency
(no host libc calls):

- `kstr_nlen` reads at most `max` bytes and never past a declared bound.
- `kstr_copy_n` / `kstr_cat_n` accept separate source extents. No NUL within
  that extent returns `KSTR_TERMINATION` when the source bound is exhausted;
  insufficient destination capacity returns `KSTR_OVERFLOW` and may be
  detected earlier. Both are failures without writes, not truncation. Checks
  precede writes. An unterminated destination
  is rejected before accessing its one-past-end byte. Legacy copy/cat scan
  through NUL or at most cap bytes: callers must provide that readable extent.
  Seven characters plus NUL exactly fit capacity eight; eight plus NUL do not.
  Copy and append have overlap-safe memmove semantics.
- `kstr_cmp` / `kstr_cmpmem` are unsigned byte compares within a bound.
- `kstr_chr` locates a byte within a bound; `kstr_move` is overlap-safe.
- `kstr_utoa` / `kstr_utoa_hex` render into bounded buffers.
- `kstr_format` / `kstr_vformat` are **transactional**: a two-pass walk first
  measures and validates (without writing), and only if it fits into `cap`
  (including the final NUL) does the second pass commit. An overflow or invalid
  specifier leaves the destination unchanged. Format and `%s` storage must be
  readable through NUL or 4096 bytes, and unchanged throughout both passes.
  Destination aliases with either are rejected. Format itself is capped at
  4096 bytes, including NUL. `%s` exact fit and empty output work. Numeric specifiers:
  `%u`/`%x`/`%X` take a `cpu_u64`; `%c` an `int`; `%s` a bounded NUL-terminated
  source (too-long, unterminated sources fail with `KSTR_TERMINATION`);
  `%%` emits a literal `%`.

## Bounded byte buffers

`kernel/runtime/kbuf.c` is a bounded FIFO ring over caller-owned `[data,data+cap)`:

- `kbuf_init` rejects null storage, zero capacity or oversized capacity.
- `kbuf_append`/`kbuf_append_byte` never partially write: a full buffer returns
  `KBUF_FULL` and its contents are unchanged. `kbuf_read`/`kbuf_consume` never
  partially read.
- `kbuf_peek` reads by offset without consuming; `kbuf_clear` resets.
- Ring wrap is exercised so head+count crossings over the end are tested (the
  wrap payload `"cdef"` is host-checked).

Head is a physical index in `[0,cap)`, not an index bounded by count. Count is
in `[0,cap]`; `remaining=cap-count`. Capacity is at most 2^40, so index sums
cannot wrap u64. Each operation validates structural invariants. Storage,
metadata, and external read/write buffers cannot alias. Zero-byte operations
permit NULL; clear resets logical state but does not erase bytes. Metadata is
caller-owned and must not be directly modified. Checks do not establish pointer
provenance or detect a forged capacity that still appears structurally valid.

## Runtime services

`kernel/runtime/krst.c` implements three pure, bounded, reentrant services and a
single `krst_call` dispatcher:

- `KRST_SVC_DIGEST` — FNV-1a 64-bit digest of a bounded region. Pure and
  independently recomputable (`tools/host/runtime_output.py` re-derives it).
  This is a non-cryptographic hash, not an integrity/authentication primitive.
- `KRST_SVC_UPPER` — ASCII uppercase, length-preserving, bounded.
- `KRST_SVC_COUNT_DIGITS` — count of ASCII digits in a bounded region.

Dispatch rejects IRQ context (`KRST_BAD_CONTEXT`), invalid operations
(`KRST_BAD_OP`), null/unaligned `out_len`, null output even with zero capacity,
wrapping/oversized regions and any alias among input, full output extent and
the separate eight-byte length object (`KRST_BAD_ARGS`). Maximum input/output
extent is 65536 bytes, independent of the generic byte-ring limit. A too-small
output returns `KRST_TOO_SMALL` before processing input. UPPER writes exactly
in_len bytes without a NUL; the other services write eight little-endian bytes.
Empty input permits NULL: digest returns FNV's offset basis, count returns zero,
and UPPER writes length zero (but still requires nonnull output). Every failure
leaves output and length unchanged. No service allocates, blocks, yields or
changes IF. `krst_digest` is a trusted computation helper with readable-region
preconditions, not a second validating dispatch interface.

All APIs require live mapped objects with the declared extents. Runtime pointer
arithmetic checks are not VM validation or protection from malicious ring-0
callers. Integer compare APIs have valid-pointer preconditions, not an error
status result. Pure helpers preserve IF; foreground services permit IF=0 or IF=1
and reject ISR calls. Distinct outputs and immutable input make calls reentrant.
Sharing a mutable ring/string requires caller synchronization; no SMP guarantee
or hidden lock is supplied. There is no initialization allocation or service OOM
path: only the test harness's PMM mappings and thread stacks allocate resources.

## Service execution on worker threads

`runtime_self_test()` runs the synthetic string/buffer/service tests first,
then creates `SCHED_THREADS - 1` = seven worker threads, one per free kernel
thread slot, each through `thread_create` from the Stage 7 scheduler. Each
worker folds `ROUNDS = 40` FNV-1a digests of its own input:

    acc = acc * 131 + digest(input)   (mod 2^64)

yields between rounds (`thread_yield`), and increments an observed completed-call
count in its dedicated slot. The bootstrap kernel thread `thread_yield`s until only it is
runnable, then reaps each worker and compares every observed `acc` against the
same fold recomputed by the **guest**; the **host** independently re-derives the
same folds from the emitted `acc`/`total` lines. Fixed folds alone cannot prove
execution: the independent audit demonstrated an accurate canned transcript
passing the original verifier. The repaired harness additionally dumps physical
worker records using the actual linked ELF symbol and validates every result,
unique worker ID/stack, and saved IRQ RIP/RSP inside the service/owned stack.
QEMU's independent CPU interrupt trace must corroborate those RIP/RSP records
with at least two distinct hardware IRQ0 deliveries per stack (i=0, CPL0,
kernel CS/SS). Removing the CPU trace is a negative test even when all guest
serial and physical records remain correct.

Stage 8 leaves IRQ0 masked. Stage 10 now explicitly installs its test handler,
unmasks IRQ0, and runs a non-yielding service-call phase in every worker until
two distinct hardware preemptions have saved RIP inside the linker-delimited
`krst_call` code. Each call hashes an actual 4096-byte stack-local payload; its
result is independently checked on the host. Each worker has a finite 131072
attempt limit and the external boot deadline bounds failure. The IRQ handler
checks rejection of all three services with unchanged output. IRQ0 is masked
again before final reporting. This is an execution probe, not a claim about
wall-clock fairness or physically measured hardware timing. The test owns the
IRQ0 callback for this phase; services themselves do not configure hardware.

Serial plus physical records detect the reviewed no-op/canned/preemption
mutations, not an adversary deliberately forging both sources of evidence or
modifying the verifier. A `[RUNTIME] failure=<name>` halts a failed self-test.

Accounting is balanced: worker stacks are allocated transiently and released on
reap, so the final accounting equals the pre-runtime (post-display) baseline,
which the host checks against the Stage 9 display state.
Three earlier create/run/reap cycles exercise all services on worker stacks.
Real PMM exhaustion with an existing worker verifies failed creation leaves
that worker intact, services still operate without allocation, and restoration
returns all frames. Partial final-group creation drains/reaps the workers it
owns before reporting a fatal self-test failure. Service failure requires no
rollback allocation because services allocate nothing.

## External scope gates

No userspace, no syscalls, no process/page-table isolation, no IPC, no loader,
no filesystem and no physical-hardware certification are claimed in this stage.
The runtime algorithms do not use QEMU devices, TCG timing, host APIs or an
emulator-specific RAM layout. QEMU HMP/ELF evidence is host-only verification;
the self-test's PIC/PIT scheduler is the existing x86-64 bootstrap platform.
The incoming Stage 11 shell sources remain preserved but outside this audit;
normal Stage 10 boots do not run the shell self-test. Its opt-in integration
is not a Stage 11 verification claim.

Known verification boundary: per-worker `rounds`/`preemptions` are
self-attributed counters, and a worker that skipped only its 40-round loop but
kept the probe phase (so its stack still shows real in-service preemptions and
a real `krst_call` trace) could not be distinguished from an honest worker by
any host evidence today. The trace's exact RIP/RSP pair corroboration and the
two-preemption floor close all fully-canned variants; this narrower
rounds-loop-only gap is a documented limitation for the Stage 11 shell work.
