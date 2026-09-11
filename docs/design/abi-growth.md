# ABI / syscall growth policy (P3, frozen)

Status: **frozen.** This document governs every UAPI addition after
Stage 18d (syscalls 0–8, `sys_err` 0–10, RYNX v1/v2, `spawn_spec` v1).
It allocates no features and invents no syscalls: it is a policy
framework. Any future RFC that adds a number, error, struct, flag, or
namespace must cite the rule it follows here.

Precedence: below `docs/design/stage18d-abi.md` §§A–F (which stay
normative for syscalls 0–8) and below the frozen per-stage UAPI
headers (`kernel/include/uapi.h`, `syscall.h`); above future
stage RFCs. Conflicts resolve in favor of the older freeze.

## G1. Numbers are append-only, allocated one RFC at a time

- Syscall numbers extend upward from 9 (`SYSCALL_NEXT_FREE = 9`).
  No RFC may renumber, reuse, or retire a number.
- No range reservations: ranges rot into false promises. Each RFC
  takes the next free number(s) and records the new free value.
- Number 0–8 semantics (including `write` pipe-full short-0 vs
  `(u64)-1`, `read` AGAIN-vs-EOF) are immutable.

## G2. Register discipline is inherited, never re-decided

Every new syscall uses the frozen §D file: number in EAX (nonzero
high-32 ⇒ `invalid_call` kill); args in order RBX, RCX, RDX, RSI,
RDI, RBP (full 64-bit, validated, never truncated); return in full
RAX; all other GPRs preserved; unused argument registers must be 0
else INVAL (kill reserved for the EAX rule only).

## G3. Memory discipline: copy-once staging, publish last

Pointer/length pairs follow the `sys_write` precedent: scalar checks
(fd/cap/zero/wrap) → two-pass USER-bit copy into kernel staging →
semantic validation of STAGED bytes → capacity/admission checks →
state commit → output publication last. Never trust mutable user
memory twice. Never publish handles/results before admission.
Failed admission leaves all outputs untouched and rolls back.

## G4. Error namespace grows append-only with justification

`sys_err` keeps values 0–10 forever. A new code needs: (a) a semantic
gap no existing code covers (parser vs semantic vs trap vs API-error
vs child-status vs fault vs abort stay distinct); (b) frozen numeric
value; (c) per-syscall mapping tests; (d) a mutant removing the new
check. `rt_err`/`KRST_*` mappings are documented per RFC, never
remapped silently.

## G5. Structs carry their own future

Every new UAPI struct: `sizeof`/`offsetof` asserted in implementation;
all padding/reserved words must-be-0 (checked, not ignored); at
least 16 trailing reserved bytes unless the RFC justifies less;
enums append-only with explicit integers; variable-length data uses
explicit (ptr,len) pairs with `argv total incl. NULs`-style caps —
never bare NUL-terminated strings across the boundary (kernel appends
exactly one NUL per `user_arg` where the frozen contract says so).

## G6. Blocking is opt-in per call, never a default change

Where a syscall gains a blocking form it follows the `read` precedent:
a flags word with bit 0 = BLOCKING, default 0 = nonblocking forever;
identical signature/numbers for both forms; reserved bits must-be-0.
Polling callers (pipe yield-retry, KBD `AGAIN` loops) keep working
unchanged. `fread` never gains blocking (bounded batch by design).

## G7. Handle/fd namespaces stay disjoint and generation-checked

Small-integer fds (0, 1, …) and 64-bit `slot|(gen<<32)` capability
handles never alias across namespaces (proc/file/pipe/device/…).
Every new capability namespace uses per-slot generations (++ on free,
skip 0); stale/consumed/cross-type handles ⇒ BADHANDLE with outputs
untouched. No namespace may recycle a live identity.

## G8. No silent behavior changes; version, don't mutate

If a later stage needs different semantics for an existing call, it
gets a new number or a versioned struct — never a flag-day change to
the frozen call. RYNX v1 loads byte-identically forever; v2 caps only
tighten with linker-enforced CI pins.

## G9. Feature discovery is deferred, not ad-hoc

No `version`/`feature` syscall is created by this policy. If a stage
needs discovery, its RFC must propose exactly one mechanism here
first (options: versioned structs, new numbers, reserved-word
negotiation) — never a private ioctl-style escape hatch.

## G10. Testability is part of the ABI

A new number/error/struct/flag is not frozen until: hostile
per-syscall matrix (bad pointer/wrap/stale/reserved-enum), rollback
proof (failed admission ⇒ zero delta), bounds triplets
(max-1/max/max+1), and at least one mutant per new check — all green
in-guest. Untestable ABI is not ABI.
