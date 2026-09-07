# Stage 18b Report — Program Loader and Syscall Boundary

Implemented and verified: fixed-VA executable loading (RYNX v1) from
RYNORFS through a validated loader into isolated Stage 18a address
spaces, plus a minimal `int $0x80` syscall boundary (exit, write,
yield) with validated copyin. Reference result below is the full
`check` gate on the reference host.

## Scope

In: RYNX v1 envelope + host ELF converter, backend RynorOS target
(link at fixed VAs, `int $0x80` runtime), kernel envelope validation,
loader (create/fill/enter/destroy), syscall dispatch + `sys_write` +
copyin, three real programs + hostile probe + 13-file malformed
matrix, two-slot isolation proof, OOM/rollback, host validators,
repository and integration tests.

Out (later stages): ELF parsing in ring 0 (never), variable entry
(needs enter-at-offset), address growth/`sbrk` (18c), `read`/more fds
(18c), blocking waits (none needed: all syscalls non-blocking),
argv/env (documented absent), runtime library (18c), shell (18d),
`syscall`/`sysret` (deferred with rationale), Windows (21x).

## Design (see docs/design/executable-format.md, syscall-abi.md)

* Prerequisite decisions (audit-resolved, not assumed): no blocking
  (exit/write/yield never sleep; no WAITING state); `int $0x80`
  extended (RSP0 auto-switch is the verified safety property; no new
  privileged state); RYNX envelope with kernel-side ELF exclusion
  (complex parsing stays in testable host Python); fixed windows, no
  growth (18a layout reused verbatim); entry pinned to offset zero
  (existing enter path needs no change).
* Format: 28-byte header (magic/version/arch/hdr-len/reserved/entry/
  code/data-filesz/data-memsz) + code + data bytes, exact total.
  Fixed model: code U-RX at `0x400000`, data U-RW at `0x600000` (BSS
  zero tail), fixed stack, guard. W^X by construction (never mapped
  otherwise); envelope carries no VAs or permissions.
* Loader: validate (checked arithmetic, per-class error codes) →
  `user_create_loaded` (shared 18a setup, no prefill of kernel
  addresses) → enter → run → detach → destroy. No half-loaded
  runnable process (failures leave FREE slots, proven by balance).
* Processes ARE 18a contexts (1 thread each, max 2), documented
  mapping: FREE=new slot, LOADING=transient in loader, ACTIVE=bound
  running, EXITED/FAULTED=terminal, destroy=reaped. No new struct.
* Syscalls: numbers 0/1/2 frozen (extend upward); EAX return, others
  preserved; high-32 RAX rejected for all reasons; write is fd-1-only,
  ≤4096, two-pass validated copyin, hex evidence rows (temporary
  serial-sink ABI, explicitly not POSIX/Unix).

## Evidence (host tools/host/load_output.py)

Trailing `[LOAD]` section after `[USER]` (banner optional), ending
with `[LOAD] load verified`, or the single line `[LOAD] no image,
skipped` (every boot terminates the transcript; the boot loop
requires the terminator). Exact: 7 creates (tables all 6) paired
with 7 destroys; program rows validated against envelope bytes from
the image file; exits `42/0/196418/42+42 isolation/42/0`
(sysprobe last); writes = hello plus the probe's good write (first 8
code bytes) plus 5 rejected empties; 13 reject rows with exact
reasons; maps per slot plus cross-slot frame disjointness for the
simultaneously-live isolation pair; tickspin delta/preemptions ≥ 1;
8 accounting lines.

## Verification

* Real programs, compiled at test time (never canned): exit42 →
  exit 42; writehello → `hello` bytes + exit 0; fib27 → 196418 after
  a preemption-guaranteed spin window (delta + preemptions asserted).
* Hostile probe (hand-assembled, absolute immediates): good write
  plus bad-fd/kernel-pointer/overflow/unmapped/noncanonical writes,
  all correctly rejected, exit 0.
* Malformed matrix (13 files): magic/version/arch/header/entry/
  code-size/data-size/shape classes, each rejected with its reason,
  no context created.
* Isolation: same image live in both slots (identical VAs, disjoint
  frames asserted guest- and host-side), independent exit-42s.
* OOM: drained PMM fails loads cleanly; restore balances.
* Mutations: envelope-magic skip, copyin USER-check removal, syscall
  number swap, faked return status, ignored exit, RWX mapping,
  hardcoded status — all fail closed (timeout or guest failure, no
  verified marker). Fake-loader-success class covered by design
  (load-then-verify ordering) and the skipped-transition 18a mutant.
* `python tools/build/build.py test` — repository suite green
  (includes 13 `test_rnyx.py` converter tests: hand-built ELFs plus
  a real backend round-trip).
* `python tools/build/build.py integration-test` — integration suite
  green (includes 15 `test_load.py` tests).
* Stage 0–18a regression green (repo grew only by stabilization and
  18b additions — 550 total; integration 224 total incl. prior suites).

## Limitations (carried, not new)

Single CPU; no KPTI/PCID/SMEP/SMAP (inherited 18a posture); no
growth/`sbrk`; no argv/env (empty stack reads as argc=0); write is
serial-hex evidence, not a console; fd 1 only; no read; no blocking;
no FS create/enumerate (images built on host); QEMU-only verification;
two static slots. TCG-vs-silicon notes from 18a apply unchanged.

## Repair loop (live-tree gauntlet, uncommitted)

* Gate-frame kill: hostile `origin`/RAX-high from LOADED programs records
  `FAULTED/class 2` instead of halting; 18a blobs still panic fail-closed
  (`kernel/core/user.c`).
* Stop-flag gating: `sched_tick` counts ticks for loaded delta but never
  stores into loaded data pages; `user_publish_stop` refuses `loaded`
  fail-closed (`kernel/core/thread.c`, `kernel/core/user.c`).
* Create unwind: `create_with_image` tears down on closing `user_check`
  failure; `load_program` leaves `*out` untouched on bad/OOM paths.
* Validator hardening (`tools/host/load_output.py`): pins 7 programs,
  21 maps, 7 exits, 7 destroys in addition to prior 7 creates/7 writes/
  13 rejects/1 tickspin/8 balanced; truncated-map fake now fails
  (`want 21 maps, got 6`).
* Fresh-review residuals (fail-closed, documented not repaired): whole-page
  U-RW data window regardless of `data_memsz` (fixed-window design);
  max-size trailing-gate `RIP==BASE+PAGE` faults instead of exiting;
  hostile tick-time RSP halts single CPU via `frame_failure` (DoS, no
  escape; gate/fault paths already kill for loaded).

## 18c handoff

Runtime library work gets: frozen syscall numbers (extend upward),
copyin primitives (`vm_query`+frame-access chunk pattern), fixed user
windows (growth is 18c's first design decision), evidence-row
conventions (`[LOAD]` grammar + terminator discipline), and the
converter/backend split (ELF parsing stays in host Python).
