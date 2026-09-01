# Stage 10: basic kernel runtime

Implements bounded strings/byte buffers and small runtime services, driven from
seven worker threads through the verified Stage 7 scheduler. All execution is
ring 0; there is no userspace, syscall or protected user task (Stage 18). This
report documents the QEMU-lab evidence only — no physical-hardware certification
is claimed.

The incoming completion claim required repair. The independent
[audit report](stage10-audit.md) supersedes its verification claims: it found
disabled timer preemption, an accurate canned-transcript bypass, bounds and
aliasing defects, and incomplete tests. The [design contract](../design/runtime.md)
describes the repaired implementation and its trusted-caller limitations.

## What was built

- **Bounded strings** (`kernel/runtime/kstring.c`, `kernel/include/kstring.h`):
  `kstr_nlen/copy/cat/cmp/cmpmem/chr/move/utoa/utoa_hex/format/vformat`. The
  formatter is two-pass and transactional — an overflowing or invalid format
  leaves the destination unchanged.
- **Bounded byte rings** (`kernel/runtime/kbuf.c`, `kernel/include/kbuf.h`):
  wrap-around FIFO, no partial writes/reads, counts never exceed capacity.
- **Runtime services** (`kernel/runtime/krst.c`, `kernel/include/krst.h`):
  `KRST_SVC_DIGEST` (FNV-1a 64), `KRST_SVC_UPPER`, `KRST_SVC_COUNT_DIGITS`,
  dispatched through `krst_call` with overlap/undersize/bad-op/bad-arg
  rejection.
- **Worker-thread integration** (`kernel/runtime/runtime-test.c`): the services
  are run from seven `thread_create`d workers; each folds 40 digests with
  `acc = acc*131 + digest` (mod 2^64), yields between rounds, and reports its
  `acc`/`rounds`; the host re-derives every fold independently.

## Host-recomputed evidence

`tools/host/runtime_output.py` independently computes the FNV-1a 64 digest and
the `*131` fold from the same `W_INPUT` literals and constants, so it never
trusts the guest's own digest claims. Reference values it recomputes:

- `fnv1a("w0:data0123") = 0x31be90fd0b4db280`
- worker 0 fold `acc0 = 0x96b2b2353f662800`
- all-workers `total = 0x7c209a0c59d000a0`

The guest transcript also emits fixed format outputs (`fmt0="rynor 42 2a"`,
`fmt1="334"`, `fmt2="FF"`) and the buffer wrap payload (`wrap="cdef"`), all of
which the host checks exactly. These fixed values alone can be printed by a
canned implementation; the audit reproduced that bypass. The repaired harness
additionally inspects physical worker records and saved hardware IRQ state
against the actual ELF code and stack extents.

## Evidence and gates

- Synthetic/repository parser tests: every Stage 10 line is required; tampering
  any worker fold, the total, the format outputs, the wrap payload, or the
  accounting against the display baseline is rejected (`tests/repository/test_runtime_output.py`).
- Real QEMU boot: full `validate_boot_output` includes the Stage 10 block between
  display-end and `POST_IRQ`; worker `acc`/`total` exactly match host folds and
  final accounting equals the display baseline (`tests/integration/test_runtime.py`).
- Negative/mutation gauntlet: breaking the FNV constant (caught only by the
  host, since the guest stays self-consistent), breaking the worker fold
  (caught by the guest), breaking upper/count services, the bounded-copy
  overflow guard, the buffer no-partial-write rule, bypassing worker rounds, and
  a canned forged transcript — each fails. See the integration test list.
- Final independent commands/results and any intermediate failures are recorded
  in `stage10-audit.md`; earlier reported totals are not substituted for reruns.

## Limits

No GUI, no userspace, no syscalls, no user page tables, no IPC, no loader and no
physical-hardware verification are claimed. The Stage 10 work is uncommitted as
of this report; the independently observed values above come from QEMU under the
bounded test harness, not from physical hardware.
