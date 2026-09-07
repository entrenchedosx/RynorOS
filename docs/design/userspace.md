# RynorOS Userspace Foundation (Stage 18a)

Protected userspace entry, isolation, and return. One static model, no
loader, no syscall dispatcher beyond a two-reason gate, no per-process
kernel objects beyond two self-test slots.

## Non-goals (18b and later)

No ELF parsing in the kernel (18b loads RYNX envelopes instead — see
`executable-format.md`), no file-backed or demand paging (18c), no process
table, signals, or virtual memory areas (18c), no SMEP/SMAP enforcement
(18d), no PCID/KPTI/meltdown posture change (18d/`§30`), no `syscall`
MSRs, no IST stacks, no FPU/SSE user state, no `RFLAGS.AC` support.

## Address layout (fixed, documented, asserted both sides)

User virtual addresses, per context, low canonical half:

| Region | Range | Mapping | Purpose |
|---|---|---|---|
| Code | `[0x400000, 0x401000)` | `U RX` (`VM_USER\|VM_EXECUTE`) | one blob, copied before map |
| Data | `[0x600000, 0x601000)` | `U RW` (`VM_USER\|VM_WRITE`) | stop flag, counter, GPR spill |
| Guard | `[0x7FE000, 0x7FF000)` | unmapped | stack-overflow trap page |
| Stack | `[0x7FF000, 0x800000)` | `U RW` (`VM_USER\|VM_WRITE`) | 4 KiB user stack, top `0x800000` |

Data-page offsets (guest blob and kernel alias agree):

| Offset | Field |
|---|---|
| `0x00` | stop flag (`0` run, `1` exit) |
| `0x08` | iteration counter |
| `0x10..0x88` | GPR spill `rax rbx rcx rdx rsi rdi rbp r8..r15` |

Kernel-half sharing: each user root clones kernel PML4 slots
`256..508` by value (shared intermediate tables, supervisor leaves, so
user access still faults at the leaf `U/S` bit). Slots `509` (MMIO),
`510..511` (frame window, future) are never cloned. Snapshots go stale:
kernel high tables mutate during normal operation (`kstack` alloc/free
on thread create/join, heap growth). Staleness is repaired, never
ignored: the snapshot is re-copied at every user entry/resume
(active-correctness) and for all live spaces at destroy time (so the
closing check compares current values). A missed sync fails closed in
`user_check`, never silently corrupt. This rests on a hard rule:
CPL3-entry handlers (tick, gate, fault) never allocate or map
kernel-high tables — audited, since single-CPU means nothing else runs
while a user space is on-CPU. Consequences:

* Every user activation starts with `mov %cr3` (full TLB flush, no
  PCID/global pages by boot contract), so stale kernel-half entries
  cannot survive across activations.
* `vm_check`/`vm_destroy` must never run on a user space: shared tables
  would fail ownership accounting and `destroy_tree` would free kernel
  tables. Teardown unmaps only slots `0..255` (shared tables never drain
  so `prune` never releases them), drops the private low chain with
  `vm_release_low`, verifies low slots are zero, then releases the root
  only when exactly one table (the root) remains.

Low-half replication: RynorKernel is a low-half kernel (linked at
`0x8000`), so interrupt delivery on a user CR3 needs the low image
mapped too: the CPU reads IDT/GDT/TSS and handler code through the
active CR3 before any software switch can run. Each user space gets a
private deep copy of the kernel `PML4[0]` chain (`vm_clone_low`:
private PDPT/PD/PTs, leaf values verbatim, `PML4[0]` linked last).
Sharing is impossible (both contexts map identical user VAs, which
would collide in shared tables); copying is sound because kernel low
mappings are immutable after `vm_initialize` (`range_valid` forbids
kernel low maps), and `vm_clone_low` refuses any kernel low chain
outside PDPT entry 0 (`VM_UNSUPPORTED`) or any copied leaf with `U/S`
set (`VM_CORRUPT`). Presence of the supervisor kernel text is
spot-checked per context (`vm_query` of `0x8000` must read supervisor
execute).
* Kernel code running with `IF=0` in foreground always runs on the
  kernel CR3. Interrupt, fault, and gate entry switch to the kernel CR3
  as the first C action, before any `vm_frame_access` window use: the
  window (slot 511) does not exist in user spaces.

Invariant: `CPL0` implies kernel CR3, except the few stub instructions
between hardware entry and the first C switch, which touch only the
kernel exit stack and shared-half statics.

## Segments, TSS, gates

GDT (7 entries, built at boot, verified with `SGDT`/`STR`):

| Index | Selector | Descriptor |
|---|---|---|
| 0 | `0x00` | null |
| 1 | `0x08` | kernel code (unchanged) |
| 2 | `0x10` | kernel data (unchanged) |
| 3 | `0x1B` | user data, DPL3 |
| 4 | `0x23` | user code, 64-bit, DPL3 |
| 5..6 | `0x28` | 64-bit TSS descriptor |

TSS: single-CPU, `RSP0` only, all IST entries zero (no IST by design,
documented), I/O bitmap base = size (no bitmap, so `cli`/`in`/`out` from
CPL3 fault with `#GP`). The descriptor is an available 64-bit TSS (type
`0x9`; `LTR` faults on busy); the CPU sets Accessed on load, so
post-`LTR` verifiers expect type `0xB`. `RSP0` always points at a
valid exit stack top or is zero before the first entry; each user entry
reloads it for the entering context. `RSP0` selects the exit stack,
never kernel stacks.

No LDT exists: `cpu_load_gdt` executes `LLDT`-null, and `user_check`
requires `SLDT == 0`, so TI-bit selectors fault canonically as
`#GP(index/TI)` before any memory access instead of depending on
reset LDTR details.

No fast-syscall backdoor: the kernel never programs `EFER.SCE`,
`STAR/LSTAR/SFMASK`, or `SYSENTER_CS/ESP/EIP`; `probe()` fails closed
unless `EFER.SCE == 0` (and `SYSENTER_CS == 0` when `SEP` exists).
With `SCE` clear, CPL3 `SYSCALL` raises `#UD` (exercised as an attack
blob); `SYSENTER` behavior differs between silicon (`#GP(0)`) and
QEMU TCG (`#UD`), so no `SYSENTER` fault value is pinned — the
enforcement is the MSR check, not a transcript row.

Runtime descriptor allowlist (`user_check`, every create/destroy):
`GDTR` base equals the code immediate; `gdt[0..2]` exact (null,
kernel code/data — a kernel-DPL flip would hand CPL3 kernel
selectors); `gdt[3..4]` exact user descriptors; TSS limit `103`,
type `0x8B`, granularity byte zero, and full 64-bit base equal to
`&cpu_tss`; `TR == 0x28`; `LDTR == 0`; `IDTR` limit `4095` with
`0..47 == 0x8E` (kernel selector, `IST` 0), `128 == 0xEE` at
`user_exit_stub`, and everything else non-present (a flipped DPL
would let user code invoke a privileged handler without `#GP`).

IDT: vector `0x80` is a present DPL3 interrupt gate at the kernel code
selector (`0xEE`). All other user-reachable state is unchanged:
vectors `0..47` stay DPL0 `0x8E`, the rest non-present. The `int $0x80`
stub pushes a zero error slot plus vector `128` and joins the shared
common entry, which routes `vector == 128` to `user_handle_exit`
(`noreturn`). `INT n` injection of other vectors from CPL3 still faults,
as before.

Gate reasons (`EAX`, low 32 bits; `EBX` carries the exit code):

| `EAX` | Meaning |
|---|---|
| 0 | exit (`EBX` = code) |
| 1 | yield (voluntary reschedule) |
| other | killed as `invalid_call` (fail closed) |

Return codes from `user_enter`/`user_resume` (`RAX` via the kernel
resume frame): `1` exited, `2` yielded, `3` preempted, `4` faulted.
Details (exit code, fault vector/error/CR2) live in the context record.

## User frame validation (before any use)

Hardware builds `SS/RSP/RFLAGS/CS/RIP`; the stub adds vector/error.
Every entry path validates, fail closed (`halt` with evidence; hostile
users are an 18d concern, but malformed frames never run):

* `CS == 0x23`, `SS == 0x1B`.
* Gate and IRQ paths: `RIP` inside the code page, `RSP` inside the
  stack page, 8-aligned. The fault path checks `CS/SS/RIP/RFLAGS`
  only and deliberately does NOT check `RSP` at all: a faulted
  context is never resumed (`user_resume` refuses non-`ACTIVE`), its
  recorded `RSP` is never trusted for any stack switch (exit stacks
  are static) and never printed, so a corrupted user stack records a
  kill instead of halting the kernel.
* `(RFLAGS & 0x202) == 0x202` and `!(RFLAGS & ~0x10ED7)`: `CF PF AF ZF
  SF IF DF OF` plus `RF` only. `RF` is allowed because fault delivery
  preserves it (observed set in `#UD`-saved images under QEMU); it is
  harmless with no debug active. No `TF AC ID IOPL NT VM VIF VIP`. `IF`
  is required: user code always runs interruptable so the timer keeps
  preempting. Note: the shared mask technically admits `TF`; a hostile
  task that sets `TF` via `POPF` deterministically halts the kernel on
  the next `#DB` (unmanaged vector by design) — availability-only DoS
  with no escape, owned by 18d hostile-user containment, not 18a.
  `AC`/`ID` are inert here (no `CR0.AM` trap, no `CPUID`-gating use).
* Vector/error match the path (`128/0` for the gate; hardware values
  for faults, checked against the whitelist below).
* The frame pointer lies inside the current context's exit stack.
* Terminal handlers (`user_handle_exit`, `user_handle_fault`) additionally
  require: context still `ACTIVE` (no double exit / exit-after-fault /
  record clobber), foreground (never IRQ-nested), and
  `RSP0 == exit_top` (the frame must sit on the stack the TSS
  selected, closing stale-`RSP0` frame confusion).
* `user_resume` re-validates the recorded `RIP/RSP/RFLAGS` including
  `RSP` alignment and canonicality (gap found by adversarial review).
* `thread_attach_user` only binds pristine `ACTIVE` records (same
  shape as `enter_valid`), so terminal records cannot be re-pinned
  to leak slots.

User-reachable fault whitelist for the kill path (record vector, error,
CR2, then schedule next): `0 #DE 4 #OF 5 #BR 6 #UD 7 #NM 10 #TS 11 #NP
12 #SS 13 #GP 14 #PF 16 #MF 17 #AC 19 #VE`. NMI, double fault, machine
check, and reserved vectors keep the existing diagnose-and-halt path.
Page faults from CPL3 are never resolved (no demand paging in 18a).

## Adversarial fault matrix (23 rows, real hardware)

Beyond exits/yields, the self-test runs one fault blob per context
(30 creates / 30 destroys total) and pins every vector/error/CR2.
Kernel text/data landmarks come from data-page parameters
(`USER_DATA_KTEXT/_KDATA`, prefilled by `user_create`); the guest
asserts exact landmark `CR2`s while the host asserts the
supervisor-violation shape (canonical, outside the user range), so
link-address shifts never desync the two.

| Class | Blobs (all must fault, never complete) |
|---|---|
| Kernel read | `[0x8000]` supervisor text (`#PF` 5); high-half supervisor (`#PF` 4, unmapped slot 511); kernel-text landmark (`#PF` 5) |
| Kernel write | user code page (`#PF` 7); kernel-data landmark (`#PF` 7); kernel stack-pointer push (`#PF` 7 at landmark−8) |
| Kernel execute | user data page (`#PF` 15, NX); user stack page (`#PF` 15, NX); kernel-text landmark (`#PF` 15, `RIP == CR2`) |
| Privileged insn | `cli`, `mov rax,cr3` (`#GP` 0); `syscall` with `SCE==0` (`#UD`) |
| Segment attacks | kernel data as `DS` (`#GP` 10); out-of-range `ES` (`#GP` 40); TI-bit `DS` with invalid LDT (`#GP` 1C); far jump to kernel `CS` (`#GP` 08); far jump to data-as-`CS` (`#GP` 18); code-as-`SS` (`#GP` 20); `#DE` via `div`; null dereference (`#PF` 4) |
| Stack corruption | noncanonical `RSP` + push (contained kill; `#GP(0)` on QEMU TCG, `#SS` on silicon — pinned to the emulator value, property is delivery+kill); `int $0x80` with bad reason (invalid-call kill) |
| CPU self-test | invalid selector `0x38` (`#GP` 38) stays CPL0-only coverage |

Selector immediates use `RPL=0` throughout: the enforced property is
privilege/type rejection, and `RPL`-zero keeps the expected `#GP`
error identical whether the CPU reports the full selector or masks
`RPL` (observed both ways across QEMU runs: `0x1F` reported as
`0x1C`). Non-`RPL`-portable fault values are never pinned
(`SYSENTER`, noncanonical-push vector).

## Scheduler integration (no invariant changes)

A thread in userspace is an ordinary kernel thread with a bound
`struct user_link`. Its kernel resume frame (`kern_save`, filled by the
entry asm on every entry) is deliberately `frame_valid`-compatible
(`RIP` = resume label in kernel text, `RSP` = entry stack pointer in
its own stack bounds, `CS/SS` kernel, `RFLAGS = pushfq & 0x10ED7`,
`vector = error = 0`), so `scheduler_check`, `sched_handoff`, and the
assembly tail validate preempted-user threads with zero changes.

* `sched_tick` branches first on `(frame->cs & 3) == 3`: record full
  user state (GPRs included), switch to kernel CR3, stash
  `saved = kern_save` with `RAX = preempted`, then the unchanged
  pick/select/statistics path. Resume later pops through the resume
  label (`ret` into the entry caller), which re-enters via
  `user_resume` from recorded state.
* `user_schedule_next` (gate/fault paths, never IRQ context) does the
  same without tick accounting and the caller finishes with
  `sched_resume` (`noreturn`): the shared assembly tail is untouched.
* `RSP0` is reloaded on every user entry for the entering context,
  and re-checked against `exit_top` on all three CPL3 paths (tick in
  `user_save_state`, gate and fault in the terminal handlers).
* IRQ0 handler discipline is unchanged, with one addition: handlers
  that can run on a user CR3 (i.e. the 18a drive) may touch only
  kernel-half statics, ports, and the PIC. No window, heap, VM, or
  serial use in IRQ context (existing rule, now load-bearing).
* Device IRQs are quiesced for the userspace phase (`user_initialize`
  masks lines 1..15; only IRQ0 has a CPL3 path, since a device IRQ
  landing on a CPL3 frame has no resume path). Earlier phases already
  consumed their input; boot halts after the self-test.

## Preemption stop protocol (timing-independent)

The two spin tasks must still be running at a tick-defined stop point on
any host speed, so the stop is tick-driven, not iteration-driven. From
CPL3 tick 25 on, the tick path publishes `1` into the current user data
page's stop flag (a supervisor store to the current user page while
still on the user CR3, before the CR3 switch; legal with SMAP off and
audited to touch only that constant VA). Each task exits on its next
flag observation. Exits are flag-gated, so no task can exit early and
the counts are exact on every host: 26 ticks, 28 switches, 13/13
preemptions (13 tick preemptions each), 14/14 dispatches (13 tick
dispatches each plus one exit-path dispatch each).

Flag timing detail: tick 25 flags the bootstrap and selects the worker,
which spins unflagged until tick 26 flags it and selects the bootstrap;
both then exit. Strict two-thread alternation (no yields, no early
exits) makes this deterministic. The first exit masks IRQ0 (terminal
transitions quiesce the drive; yields never do), so at most one extra
tick could land in the few-instruction exit window (~3e-7 per boot);
such an event fails the exact asserts loudly, never silently.

A 1e9-iteration backstop in the spin loop guards against a flag bug
turning into a hang: tripping it fails the exact counts loudly.

## Address-space auditing (every create/destroy)

* `replica_ok`: every present leaf of the private low replica must
  equal the kernel's leaf at the same VA (same frame, same `W/X`,
  supervisor-only, `A/D` ignored), except the three user pages, which
  must carry exactly their user permissions. Missing replica leaves
  fail safe (unmapped faults); extra or altered leaves fail here.
* `high_ok`: no user-accessible leaf may exist anywhere under kernel
  `PML4` 256..508 (every such leaf is copied verbatim into all user
  spaces). Intermediates legitimately carry `U`; only leaves grant
  access, so only leaves are checked. Slots 509..511 are never copied.
* `clone_high`/`sync_high` copy shared entries verbatim (intermediate
  `U` bits are normal table-walk state); `vm_clone_low` already
  rejects `U` leaves fail-closed at copy time.
* `vm_protect` on non-kernel spaces may only reprotect existing user
  leaves (supervisor-to-user flips fail closed). User-to-supervisor and
  `W/X` flips on user leaves are left to 18b API design (no CPL3 caller
  exists today; `W+X` is rejected globally in any case).
* `DS/ES` are intentionally not sanitized across CPL3 transitions:
  benign in long mode (segment bases ignored except `FS/GS`, which are
  never used with bases here; paging still enforces). 18d may reload
  them for hygiene.
* `user_publish_stop` re-checks `CR3 == space.root` before its
  fixed-VA store, so call-order changes cannot redirect the stop flag
  into identity-mapped kernel memory.
* TLB discipline: `PGE`/`PCIDE`/`LA57` are forbidden at `VM` init, so
  every `CR3` reload fully flushes; user-space tables are only ever
  mutated on the kernel `CR3` while dead, never live. No `invlpg`
  path exists for user VAs (documented 18b debt alongside SMP shootdown).

## Static limits (admission control, tested)

* `USER_MAX_CONTEXTS = 2`: two static 4 KiB exit stacks in `.bss`.
  Creation beyond that fails cleanly with evidence; 18c lifts this with
  a process table.
* One code, one data, one stack frame per context (PMM-owned, released
  on destroy). Guard page stays unmapped.
* No FPU/SSE in userspace (blobs use GPRs only); no user `AC`.

## CPU feature posture (`§30`)

SMEP/SMAP are probed via CPUID and reported (`[USER] smep=0/1
smap=0/1`), with guest-side self-consistency (report equals CPUID bits;
`CR4` bits observed clear since 18a never enables them). Enforcement is
deferred to 18d. The probe asserts nothing about values host-side
beyond syntax; QEMU `qemu64` is expected to report both present.

Side channels are out of scope for 18a with honest documentation: the
shared high-half snapshot keeps kernel pages mapped supervisor-only in
user address spaces (no KPTI/PCID by explicit stage scope), so a
speculative-execution CPU could transiently touch kernel TLB entries
even though every architectural access faults. Meltdown-class
resistance arrives with the 18d isolation work, not here.

## Evidence contract (host `tools/host/user_output.py`)

Trailing `[USER]` section after `[FS]`: starts with `[USER]
initialized`, ends with `[USER] user verified`. Deterministic lines
(exit codes, fault vectors/errors/CR2, tick/switch/preemption counts,
mapping VAs/perms, admission rejection, balance) are checked exactly;
timing-derived counters are presence-checked with kernel-side lower
bounds. See `docs/reports/stage18a.md` for the line grammar.
