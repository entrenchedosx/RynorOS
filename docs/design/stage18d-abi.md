# Stage 18d Frozen ABI (UAPI)

Status: **frozen for Slice A–G implementation.** This document resolves
Blockers A–F of the final ABI freeze pass against repository source at
commit `68501e8`. The Stage 18d ADR (architecture) is accepted; this file
supplies the exact binary contract so no ABI semantics are invented
during coding. Nothing here executes: no kernel, userspace, test, build,
or ROADMAP change accompanies it.

Source evidence index (all paths relative to repo root):

- `kernel/include/cpu.h:16-21` — `exception_frame` carries full u64
  `rdi rsi rbp rdx rcx rbx rax`; offsets pinned by `_Static_assert`.
- `kernel/core/user.c:221-229` (`record_state`) — all GPRs recorded;
  `943-945` — gate reason `(u32)f->rax` plus `!(f->rax >> 32)` kill rule.
- `kernel/core/user.c:915-993` (`user_handle_exit`) — terminal results
  via `c->gprs[0]` into resume RAX; exit/yield/write dispatch shape.
- `user/lib/rt/rt.c:15-24` (`rt_gate`) — wrappers use only
  `EAX/EBX/ECX/EDX`; `ESI/EDI/EBP` are free for extension.
- `user/lib/rt/rt_gate.asm:17-24`, `tools/rynorlang/runtime/rt_rynor.asm:30-34`
  — both `_start`s are `call`-only; neither reads `[RSP]`.
- `tools/rynorlang/runtime/rt_rynor.asm:9-16` — legacy helpers pass
  ECX/EDX low halves only (32-bit truncation is a property of those
  helpers, not of the kernel, which validates full 64-bit).
- `kernel/storage/fs.c:415-437` (`fs_open`), `460-503` (`fs_read`),
  `505-512` (`fs_close`), `208-217` (`decode_handle`) — handle shape
  `slot | gen<<3`, BUSY/NOTFILE/RANGE/INVALID discipline,
  `*nread` written pre-loop (line 473), completed-prefix on IOERR,
  untouched on other errors, 2-byte kernel-buf alignment.
- `kernel/core/load.c:78-116` (`copy_from_user`, `sys_write`) —
  validation order fd → cap → zero → wrap → two-pass USER-bit copy.
- `kernel/core/thread.c:147-187` — `thread_create` returns 0 when no
  FREE slot; `thread_join` reaps EXITED non-current non-bootstrap only.
- `kernel/interrupts/irq.c:40-65` — non-0 IRQ returns raw frame
  (hence the Slice A park path; no handoff bypass exists).
- `kernel/include/load.h:21-43` — RYNX envelope + `rnyx_error` 0-based
  enum precedent for `sys_err`.
- `kernel/include/syscall.h:20-28` — numbers 0–2, append-upward rule,
  `SYSCALL_WRITE_MAX 4096`.

## A. `read()` — one return convention (Blocker A)

```c
sys_err read(u32 fd, void *buf, u64 len, u64 *nread_out, u32 flags);
```

- `OK + *nread>0`: bytes staged and copied.
- `AGAIN`: source empty (keyboard or pipe). `buf` and `*nread_out`
  **untouched**. Distinguished from EOF only by RAX — byte counts can
  never collide with status codes because counts travel exclusively
  through `*nread_out`.
- `OK + *nread==0`: EOF only — CLOSED stdin, or PIPE_R with all
  writers gone and empty. Keyboard never produces EOF.
- `INVAL/BADARG/...`: all outputs untouched.
- Zero length: validate fd/flags/`nread_out`, write `*nread_out=0`,
  return OK, never touch `buf`.
- Max length 4096 (`SYSCALL_WRITE_MAX` mirror).
- `flags` must be 0 (bit 0 = BLOCKING, reserved; bits 1..63 reserved).
  Future blocking sleeps under bit 0 with identical signature/numbers.
- User `buf` needs **no** alignment (`copy_to_user` is byte-wise; the
  2-byte rule in `fs.c` constrains kernel staging only).

## B. `spawn_pipe()` outputs + atomicity (Blocker B)

```c
sys_err spawn_pipe(const spawn_spec *a, const spawn_spec *b,
                   u64 *handle_a_out, u64 *handle_b_out);
```

Commit order: validate A → validate B → stage both images → reserve
2 slots + 2 threads + the single pipe → construct pipe → admit A (stdout
=PIPE_W) and B (stdin=PIPE_R); B stdout=SERIAL, A stdin=CLOSED unless a
spec says otherwise → write `handle_a_out`, then `handle_b_out` LAST.
Any failure before commit: no live child, no pipe, no zombie, no leaked
context/thread/frame/table, **both outputs untouched** (a failing second
pointer write rolls back the first). Owner of both = spawner slot.
Ctrl-C order: producer (A) first, then consumer (B); natural-exit race
yields `ALREADY_GONE`, both still waited. A-first death → B drains to
EOF; B-first death → A gets short-0 writes (FULL-equivalent backpressure
signal, never silent loss).

## C. `wait()` — one return domain (Blocker C)

```c
sys_err wait(u64 handle, proc_status *status_out);

struct proc_status {
    u32 state;    /* proc_state */
    u32 code;     /* EXITED: low 32 of exit code; FAULTED: vector */
    u32 detail;   /* FAULTED: low 32 of fault error; else 0 */
    u32 reserved; /* 0 */
};
```

- RUNNING: returns OK, publishes `{PROC_RUNNING,0,0,0}`, consumes nothing.
- Terminal: validate `status_out` (USER+WRITE) FIRST; copy status to
  staging; publish; THEN reap. A failed `status_out` write returns INVAL
  with **no consumption** — terminal results are never lost to a bad
  pointer.
- Stale/consumed/non-owner: BADHANDLE, output untouched.
- Shell `$?` = cached first-wait result in userspace, never re-query.

## D. Frozen binary UAPI (Blocker D)

Syscalls: `0 exit, 1 yield, 2 write` (unchanged); `3 read, 4 spawn,
5 wait, 6 terminate, 7 fread, 8 spawn_pipe`.

Register file (all new numbers): number in EAX (high-32 zero, else
`invalid_call` kill — `user.c:943-945` rule extended); args in order
RBX, RCX, RDX, RSI, RDI, RBP (full 64-bit; `cpu.h:16-21` frame carries
them); return in full RAX; all other GPRs preserved (existing guarantee).
Unused argument registers must be 0, else INVAL (kill reserved for the
EAX rule only).

| # | EBX | ECX | EDX | ESI | EDI | EBP | RAX |
|---|---|---|---|---|---|---|---|
| 3 read | fd==0 | buf | len≤4096 | nread_out | flags==0 | 0 | sys_err |
| 4 spawn | spec_ptr | handle_out | 0 | 0 | 0 | 0 | sys_err |
| 5 wait | handle | status_out | 0 | 0 | 0 | 0 | sys_err |
| 6 terminate | handle | 0 | 0 | 0 | 0 | 0 | sys_err |
| 7 fread | path_ptr | path_len≤32 | offset | buf | len≤16384 | nread_out | sys_err |
| 8 spawn_pipe | spec_a | spec_b | handle_a_out | handle_b_out | 0 | 0 | sys_err |

`fread` has no flags word by design (bounded batch ≤16K; no blocking
form will ever be added).

```c
enum sys_err : u32 {
    SYS_OK = 0, SYS_AGAIN = 1, SYS_INVAL = 2, SYS_NOTFOUND = 3,
    SYS_MALFORMED = 4, SYS_BADHANDLE = 5, SYS_BUSY = 6, SYS_NOMEM = 7,
    SYS_BADARG = 8, SYS_ALREADY_GONE = 9, SYS_IOERR = 10
}; /* append-only; never renumber, never overlap 0-2 semantics */

enum proc_state : u32 {
    PROC_RUNNING = 0, PROC_EXITED = 1, PROC_FAULTED = 2, PROC_ABORTED = 3
}; /* separate domain; never compare against sys_err */

enum stdin_sel : u32 { STDIN_CLOSED = 0, STDIN_KBD = 1, STDIN_PIPE = 2,
                       STDIN_FILE = 3 /* reserved, must-be-0 in base */ };
enum stdout_sel : u32 { STDOUT_SERIAL = 0, STDOUT_PIPE = 1,
                        STDOUT_FILE = 2 /* reserved, must-be-0 in base */ };
```

```c
struct user_arg { u64 ptr; u64 len; }; /* 16 B, align 8; strings carry
  no NUL (kernel appends exactly one each); interior NUL -> BADARG */

struct spawn_spec {
    u64 path_ptr;    /* 0 */
    u64 path_len;    /* 8, 1..32 */
    u64 args_ptr;    /* 16, user_arg[nargs] */
    u32 nargs;       /* 24, <=8 */
    u32 stdin_sel;   /* 28 */
    u32 stdout_sel;  /* 32 */
    u32 stderr_sel;  /* 36, must be 0 (serial) in base */
    u64 file_in;     /* 40, must be 0 in base */
    u64 file_out;    /* 48, must be 0 in base */
    u64 file_err;    /* 56, must be 0 in base */
    u64 reserved[4]; /* 64..96, must be 0 */
};
/* Normative (implementation asserts): sizeof == 96; offsetof == comments;
   align == 8. Selector enums append-only. argv total incl. appended NULs <= 256. */
```

Process handles: `slot | (gen<<32)`, u64, per-slot gen ++ on free skip 0.
Handle namespaces (proc/file/pipe) are disjoint by decree.

## E. Startup stack (Blocker E)

```text
RSP % 16 == 0
[RSP + 0]           = argc : u64
[RSP + 8 + 8*i]     = argv[i] : u64   (0 <= i < argc)
[RSP + 8 + 8*argc]  = NULL : u64
strings in [RSP + 16 + 8*argc, 0x800000), each NUL-terminated
argc == 0  =>  [RSP] = 0, [RSP + 8] = NULL   (RSP itself never 0)
```

Backward compatibility (verified, not asserted): `rt_gate.asm:17-24`
and `rt_rynor.asm:30-34` `_start`s are `call`-only; compiled `rl_4_main`
uses SysV callee-built frames; 18a blobs use fixed data offsets (the
`SS_RSP`/`KERN_RSP` blobs set their own faulting values deliberately).
Shifted RSP (`TOP - block`, block ≤ 512) satisfies `origin_valid` and
`user_resume` range/alignment checks (`user.c:183-190, 891-894`).

## F. Memory sufficiency (Blocker F)

RYNX v2: code ≤ 64 KiB (16 pp), data ≤ 32 KiB (8 pp); v1 1pp/1pp loads
byte-identically (version-gated checks). Code 64 KiB is conservative
(~2–3 kLOC C shell+evaluator at ~60 lines/KB); the cap only ever
tightens later (linker-enforced, CI-pinned). VA map frozen:
code `[0x400000,0x410000)`, data `[0x600000,0x608000)`,
heap RESERVED `[0x610000,0x700000)`, arena RESERVED `[0x700000,0x7F0000)`,
guard/stack unchanged.

Data-window worst case (32 KiB):

| Category | Bytes |
|---|---:|
| shell/REPL statics + line/argv scratch | 2048 |
| session source text (cap) | 8192 |
| symbol table 128 x 32 B | 4096 |
| scratch symtab (swap-on-success) | 4096 |
| session values arena (cap; exhaustion = honest NOMEM) | 5120 |
| per-submission arena (item nodes + eval + pipe chunks) | 4096 |
| input/script/argv scratch | 1024 |
| safety margin | 2096 |
| TOTAL <= | 30720 of 32768 |

Whole-buffer re-analysis is item-at-a-time (scratch symtab rebuilt in
order, swapped only on success); peak scratch stays O(1) in session
size. Slice F test: maximum legal session + near-maximum submission +
failed-submission rollback + arena reset + leak walk + deterministic
NOMEM (never corruption).

## Output-publication rule (all output-bearing syscalls)

> Validate/stage first, publish outputs last, and never consume
> irreversible kernel state before successful output publication.

`read`/`spawn`/`fread`/`spawn_pipe`: outputs written after admission or
transfer staging; failures leave them untouched (`AGAIN` writes nothing,
unlike `fs_read`'s OK-path `*nread`). `wait`: failed `status_out`
write ⇒ no reap (§C). `write` pipe-full ⇒ short-0 (existing short-write
convention, `syscall.h:29-31`), never `(u64)-1` (reserved for invalid);
rt pipe helper yield-retries on short-0; serial path unchanged
(len or -1). Broken pipe (readers gone) ⇒ `(u64)-1`, diagnosed via
`wait` states.

## Contradiction-attack results (15 questions)

1. Count vs AGAIN: PASS (counts only via `*nread_out`; AGAIN writes nothing).
2. Abort both children: PASS (`spawn_pipe` publishes both handles; order A-then-B).
3. API failure vs process state: PASS (`sys_err` in RAX, `proc_state` in struct).
4. Terminal status vs bad pointer: PASS (validate → publish → reap).
5. One handle visible on partial failure: PASS (both outputs last; rollback).
6. Survivor of failed admission: PASS (admit-both-or-neither + verified rollback).
7. argc=0 stack: PASS (`[RSP]=0, [RSP+8]=NULL`; RSP nonzero, aligned).
8. Descriptor interpretation: PASS (`user_arg` frozen 16 B + asserts).
9. Enum ordering: PASS (all values explicit integers).
10. Data-window overflow: PASS (§F budget + max-state NOMEM test).
11. Empty vs EOF: PASS (empty→AGAIN untouched; EOF→OK+0; keyboard never EOF).
12. Stale alias: PASS (64-bit gen, skip-0, BADHANDLE).
13. Ctrl-C vs natural exit: PASS (`ALREADY_GONE` + wait-both, no zombie leak).
14. RX output target: PASS (USER+WRITE `copy_to_user` both passes).
15. 18a–18c validity: PASS (§E verification; v1 loader path identical).

## Slice readiness

Slices A–G can now be implemented without inventing ABI semantics.
Remaining Slice F/G obligations created here (not new decisions):
max-state NOMEM test, link-size pins with tighten-later rule, 10-mutant
gate, hostile per-syscall matrix, marker-driven session grammar.
