# Kernel runtime (Stage 10)

Ring-0 runtime services and bounded primitives, deliberately **not** userspace
(no syscalls, no Ring 3, no user page tables; protected tasks remain Stage 18).

- `kstring.h` / `kstring.c` — bounded string/copy/cat/cmp/chr/move and a
  transactional bounded `kstr_format` (two-pass; failure leaves the buffer
  unchanged). `%u/%x/%X` take a `cpu_u64`; `%s` is bounded; `%c` an int.
- `kbuf.h` / `kbuf.c` — bounded FIFO byte ring over caller-owned storage with
  wrap-around, no partial writes, and counts that never exceed capacity.
- `krst.h` / `krst.c` — runtime services (FNV-1a 64 digest, uppercase, digit
  count) dispatched through `krst_call`, with overlap/undersize/bad-op/bad-arg
  rejection.
- `runtime-test.c` — `runtime_self_test()`: synthetic bounds tests, then the
  services are driven from seven worker threads through the Stage 7 scheduler.
  Worker digest folds and formatted/buffer outputs are re-derived independently
  by `tools/host/runtime_output.py`. The host also inspects physical worker
  records and requires hardware IRQ RIP/RSP inside service code/owned stacks;
  fixed strings alone cannot establish execution.

The self-test enters at ring 0 with IF=0 and explicitly enables IRQ0 for its
preemption phase, then masks it again. Services accept foreground IF=0 or IF=1,
preserve IF, never allocate/block/yield, and reject IRQ context. Mutable objects
require caller synchronization; trusted pointers are not a protection boundary.
`boundary-test.c` covers guard pages, ring wraps, invalid requests, repeated
service workers and real PMM exhaustion. See `docs/design/runtime.md` for the
complete ownership, source-bound, overlap and error contracts.
