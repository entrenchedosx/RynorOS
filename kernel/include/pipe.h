#ifndef RYNOR_PIPE_H
#define RYNOR_PIPE_H
#include "cpu.h"
#include "uapi.h"

/* Stage 18d Slice D: kernel-owned bounded pipe for two-stage streaming
 * pipelines. Exactly one pipe can exist (UAPI_PIPE_MAX); it carries no
 * userspace handle (association is performed by spawn_pipe admission and
 * verified by slot+generation on every access). Storage is a heap-
 * allocated 4096-byte ring attached at admission and freed exactly once
 * when both endpoints close; endpoint lifetime follows execution
 * (terminal transition), not zombie reaping. All entry points require
 * IF=0 foreground and never run in IRQ context (single CPU, no locks).
 * See docs/design/stage18d-abi.md §§A-B and the output-publication rule.
 */

#define PIPE_CAP UAPI_PIPE_BUF
#define PIPE_MAX UAPI_PIPE_MAX

/* Diagnostic snapshot for driver evidence (plain copy, no locks). */
struct pipe_snapshot {
    cpu_u64 allocated;
    cpu_u64 count;
    cpu_u64 reader_open;
    cpu_u64 writer_open;
    cpu_u64 reader_slot;
    cpu_u64 writer_slot;
    cpu_u64 full_stalls;   /* writes refused: full with reader live */
    cpu_u64 empty_stalls;  /* reads refused: empty with writer live */
    cpu_u64 turns;         /* ring-index wrap events (both directions) */
    cpu_u64 total_written;
    cpu_u64 total_read;
};

/* Structural invariants (IF=0 foreground). Detects count over capacity,
 * out-of-range indices, allocated pipe with bad owners, duplicate
 * endpoints, free pipe retaining state, generation mismatch on live
 * endpoints, live endpoints on FREE slots, role mismatch against the
 * process table, and leaked storage. Fail-closed (returns 0). */
int pipe_check(void);
/* Nonzero while the single pipe is admitted. */
int pipe_allocated(void);
/* Allocate/free the 4096-byte ring (bounded heap, caller-owned until
 * pipe_attach takes ownership). Free is exact-balance with alloc. */
int pipe_ring_alloc(cpu_u8 **out);
int pipe_ring_free(cpu_u8 *ring);
/* Admit a zeroed ring between (wslot,wgen) and (rslot,rgen): both
 * endpoints open, indices/count zero, counters zero. Fails closed if a
 * pipe is already allocated (caller rolls back without touching live
 * state). The ring pointer is consumed (not freed) on success. */
int pipe_attach(cpu_u8 *ring, unsigned int wslot, cpu_u64 wgen,
                unsigned int rslot, cpu_u64 rgen);
/* Enqueue up to len staged bytes from the owning writer. Returns the
 * accepted count (0..len; 0 means full with a live reader: the frozen
 * short-0 backpressure signal, never a loss), (cpu_u64)-1 when the
 * reader endpoint is gone (frozen broken-pipe signal), or (cpu_u64)-2
 * on invalid arguments/authority (caller maps to its invalid class).
 * Never overwrites unread data; byte order preserved. */
cpu_u64 pipe_write(unsigned int slot, cpu_u64 gen,
                   const cpu_u8 *src, cpu_u64 len);
/* Stage up to len bytes for the owning reader without dequeuing.
 * Returns SYS_OK with *n>0 staged, SYS_AGAIN when empty with a live
 * writer (*n untouched), SYS_OK with *n==0 at terminal end-of-stream
 * (empty with writer gone), or SYS_INVAL. The caller publishes to
 * userspace first and dequeues with pipe_read_commit only on success,
 * so a failed copyout never loses bytes. */
int pipe_read_stage(unsigned int slot, cpu_u64 gen, cpu_u8 *dst,
                    cpu_u64 len, cpu_u64 *n);
/* Dequeue n staged bytes after successful userspace publication.
 * Returns 1, or 0 (fail-closed) when n exceeds the occupied count. */
int pipe_read_commit(cpu_u64 n);
/* Execution-terminal transition for (slot,gen): closes the owned
 * endpoint (writer, reader, or none for ordinary children), freeing
 * the ring exactly once when both endpoints are closed. Stale
 * generations (recycled slots) are a legitimate no-op. Returns 1, or
 * 0 on corruption (double close of a live-owned endpoint). */
int pipe_note_terminal(unsigned int slot, cpu_u64 gen);
/* Copy current counters/state for driver evidence. */
void pipe_snapshot(struct pipe_snapshot *out);

#endif
