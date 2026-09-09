#ifndef RYNOR_SYSCALL_H
#define RYNOR_SYSCALL_H
#include "cpu.h"

/* Stage 18b syscall ABI (int $0x80 ONLY; syscall/sysret unprogrammed).
 *
 * Registers: EAX = number (low 32 bits; high 32 must be zero), EBX/ECX/EDX
 * = arguments (full 64 bits for pointers/lengths). Return in full RAX
 * (0..len or (cpu_u64)-1); every other GPR is preserved (the resume
 * frame restores recorded state).
 * The gate instruction is exactly CD 80; the hardware frame already
 * points past it, so no kernel RIP adjustment exists. Handlers run with
 * IF=0 (interrupt gate), so IRQ0 cannot interleave a syscall body;
 * preemption happens only at CPL3 boundaries via the verified tick path.
 * The conventional user window lives below 4 GiB, but the kernel takes
 * full 64-bit pointers and rejects anything outside U-mapped user pages
 * (no truncation); lengths are full 64-bit with overflow-checked
 * arithmetic.
 */
#define SYS_EXIT 0u
#define SYS_YIELD 1u
#define SYS_WRITE 2u
/* Stage 18d numbers (frozen in docs/design/stage18d-abi.md). Only
 * SYS_READ has a handler; 4..8 remain reserved kills until their slice
 * (unknown numbers die as invalid_call; the namespace only ever
 * extends upward, never renumbers). */
#define SYS_READ 3u
#define SYS_SPAWN 4u
#define SYS_WAIT 5u
#define SYS_TERMINATE 6u
#define SYS_FREAD 7u
#define SYS_SPAWN_PIPE 8u
/* 9..2^32-1 reserved: unknown numbers die as invalid_call; the namespace
   only ever extends upward, never renumbers. */

#define SYS_STDOUT 1u
#define SYSCALL_WRITE_MAX 4096u
/* Stage 18d read cap (frozen ABI §A: SYSCALL_WRITE_MAX mirror). */
#define SYSCALL_READ_MAX 4096u
/* Stage 18d fd domain: only stdin 0 exists (keyboard staging in Slice A;
 * endpoint multiplexing arrives with spawn selectors in Slice C). */
#define SYS_STDIN 0u

/* write() return: bytes written (0..len, short allowed like serial and
   fs_write prefix semantics), or (cpu_u64)-1 on invalid arguments with
   nothing written (two-pass validation before any copy). */

#endif
