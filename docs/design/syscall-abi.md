# RynorOS syscall ABI (Stages 18b–18d, P1; `int $0x80` only)

## Mechanism choice

Stage 18b extends the verified 18a `int $0x80` DPL3 gate. `syscall` /
`sysret` are deliberately NOT introduced: they need `EFER.SCE`, `STAR`,
`LSTAR`, `SFMASK` programming plus a manual stack switch (no `RSP0`
auto-switch), `RCX`/`R11` clobber handling, and a new entry path — pure
new attack surface with no 18b need. The `int` gate hardware-loads
`RSP0`, preserves all GPRs via push/pop, and runs handlers with `IF=0`
(interrupt gate), so IRQ0 cannot interleave a syscall body; preemption
happens only at CPL3 boundaries via the verified tick path. `EFER.SCE`
and `SYSENTER_CS` stay enforced zero; CPL3 `SYSCALL` still faults.

## Registers

* `EAX`: number (low 32 bits; nonzero high 32 fails closed for every
  reason, including the pre-18b exit/yield paths).
* `EBX`, `ECX`, `EDX`: arguments (full 64 bits for pointers/lengths).
* Return in full `RAX` (`0..len` or `(u64)-1`, so all 64 bits are
  significant); every other GPR is preserved (the resume frame
  restores recorded state; only the recorded `RAX` is overwritten).
* The gate instruction is exactly `CD 80`; the hardware frame already
  points past it, so no kernel `RIP` adjustment exists anywhere.
* The conventional user window lives below 4 GiB, but the kernel
  accepts full 64-bit pointers and rejects anything outside `U`-mapped
  user pages (no truncation — high bits fail closed via the `USER`-bit
  check in the two-pass copyin); lengths are full 64-bit with
  overflow-checked arithmetic.

## Numbers (frozen, extend upward only)

| Number | Name | Arguments | Returns |
|---|---|---|---|
| 0 | exit | `EBX` = status | never returns (terminal) |
| 1 | yield | — | resumes (no evidence row) |
| 2 | write | `EBX` = fd, `ECX` = buf, `EDX` = len | bytes written, or `(u64)-1` |
| 3 | read | `EBX` = fd (0), `ECX` = buf, `EDX` = len, `ESI` = nread_out, `EDI` = flags (0), `EBP` = 0 | `sys_err` in `RAX`, count via `*nread_out` |
| 4 | spawn | `EBX` = spec_ptr, `ECX` = handle_out | `sys_err` in `RAX` |
| 5 | wait | `EBX` = handle, `ECX` = status_out | `sys_err` in `RAX` |
| 6 | terminate | `EBX` = handle | `sys_err` in `RAX` |
| 7 | fread | `EBX` = path_ptr, `ECX` = path_len, `EDX` = offset, `ESI` = buf, `EDI` = len, `EBP` = nread_out | `sys_err` in `RAX` |
| 8 | spawn_pipe | `EBX` = spec_a, `ECX` = spec_b, `EDX` = handle_a_out, `ESI` = handle_b_out | `sys_err` in `RAX` |
| 9 | fcreate | see `p1a2-cpl3-abi.md` G2 | `sys_err` in `RAX` |
| 10 | fwrite | see `p1a2-cpl3-abi.md` G2 | `sys_err` in `RAX` |
| 11 | fstat | see `p1a3-lifecycle-abi.md` | `sys_err` in `RAX` |
| 12 | readdir | see `p1a3-lifecycle-abi.md` | `sys_err` in `RAX` |
| 13 | unlink | see `p1a3-lifecycle-abi.md` | `sys_err` in `RAX` |

Stage 18d Slice A adds `read` (nonblocking stdin, `sys_err` return with
out-param count — see `docs/design/stage18d-abi.md` §A); numbers 4–8 are
frozen by that document with handlers in later slices. P1-A2/A3 add 9–13
(`fcreate`/`fwrite`/`fstat`/`readdir`/`unlink` — see `p1a2-cpl3-abi.md`
and `p1a3-lifecycle-abi.md`). Unknown
and reserved numbers (`14..2^32-1`) die as `invalid_call` (existing kill
path). `exit`/`yield` keep their 18a numbers and
semantics; `EBX` still carries the exit code.

## `exit(status)`

Stops the process: state `EXITED`, status recorded, thread detached by
the driver, slot destroyed (frames + tables reclaimed, verified by
balance accounting). Never returns.

## `write(fd, buf, len)` (temporary serial sink)

`fd` must be `1` (stdout). Output goes to the kernel serial transcript
as a `[LOAD] write` evidence row with exact hex bytes — explicitly the
temporary 18b ABI contract, not a Unix descriptor model and not a
claim about consoles or files. No blocking:
the serial poll is bounded and the call never sleeps. (`read` on fd 0
arrived in 18d Slice A; pipes and P1 files add further fds — see
`stage18d-abi.md`, `p1a2-cpl3-abi.md`, `p1a3-lifecycle-abi.md`.)

Validation order (all before any memory touch): `fd`, length cap
(`4096`, else error), zero-length short-circuit (`0`), address wrap,
then a two-pass copy — every page must query `OK` with the `USER` bit
(supervisor leaves, holes, and noncanonical addresses fail here with
nothing written), then bytes move page by page with per-chunk
`vm_frame_access` (window pointers never survive a VM call). Returns
bytes written, or `(u64)-1` with nothing written.

## `yield()`

Unchanged 18a semantics (voluntary reschedule, no evidence row for
loaded programs). Never masks the timer drive. Not callable from IRQ
context through any user-controlled path (handlers check foreground).

## Fault and error model

Bad pointers, overflowed ranges, unmapped/non-user pages, bad fds, and
oversize lengths all return `(u64)-1` (or kill via `invalid_call` for
bad numbers) without touching kernel state. A faulting loaded program
dies `FAULTED` like any user context; the driver reports it. Return to
user reuses the 18a resume validation (`CS/SS/RIP/RSP/RFLAGS/CR3`
principles); `sysret`/`iretq` never consume user-controlled values
(frames are kernel-built).

## Future (not implemented)

`sbrk`, blocking waits, `argv`/`env`, richer errors beyond `sys_err`.
(`read`, more fds, and bounded arenas arrived in 18d/P1.) The namespace
and the copyin primitives stay designed to
extend without renumbering. (Full 64-bit pointers are already accepted
and validated per §Registers above, so they are not future work.)
