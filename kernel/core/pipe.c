#include "pipe.h"
#include "proc.h"
#include "heap.h"
#include "irq.h"
#include "io.h"
#include "serial.h"

/* Stage 18d Slice D: one bounded kernel-owned pipe (ring + endpoint
 * ownership + backpressure counters). Single CPU, IF=0 foreground on
 * every path (syscall handlers, worker terminal transitions, driver);
 * the IRQ/tick paths never touch this state, so no locks exist. */

static void panic(const char *why) __attribute__((noreturn));
static void panic(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[PIPE] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}
static int foreground(void) { return cpu_interrupts_disabled() && !irq_in_context(); }

static int live;                /* nonzero while admitted */
static cpu_u8 *ring;            /* heap ring, valid iff live */
static cpu_u64 rpos, wpos;      /* ring indices, always < PIPE_CAP */
static cpu_u64 count;           /* occupied bytes, always <= PIPE_CAP */
static unsigned int rslot, wslot;
static cpu_u64 rgen, wgen;
static int reader_open, writer_open;
static cpu_u64 full_stalls, empty_stalls, turns;
static cpu_u64 total_written, total_read;

int pipe_allocated(void) { return live; }

int pipe_check(void)
{
    cpu_u64 gen = 0;
    unsigned int stdin_sel = 0, stdout_sel = 0;
    int slot_live = 0;
    if (!cpu_interrupts_disabled()) return 0;
    if (count > PIPE_CAP) return 0;
    if (rpos >= PIPE_CAP || wpos >= PIPE_CAP) return 0;
    if (!live) {
        /* Free: nothing retained (no bytes, no storage, no owners). */
        if (ring || count || rpos || wpos) return 0;
        if (reader_open || writer_open) return 0;
        if (rgen || wgen) return 0;
        return 1;
    }
    if (!ring) return 0;
    /* Endpoints must be distinct processes. */
    if (rslot >= UAPI_MAX_PROCS || wslot >= UAPI_MAX_PROCS) return 0;
    if (rslot == wslot) return 0;
    /* A live (open) endpoint must sit on a live slot with a matching
       generation and a matching pipe role. Closed endpoints carry no
       authority (their slots may already be recycled). */
    if (reader_open) {
        if (!proc_slot_info(rslot, &gen, &stdin_sel, &stdout_sel, &slot_live))
            return 0;
        if (!slot_live || gen != rgen || stdin_sel != STDIN_PIPE) return 0;
    }
    if (writer_open) {
        if (!proc_slot_info(wslot, &gen, &stdin_sel, &stdout_sel, &slot_live))
            return 0;
        if (!slot_live || gen != wgen || stdout_sel != STDOUT_PIPE) return 0;
    }
    return 1;
}

int pipe_ring_alloc(cpu_u8 **out)
{
    void *p = 0;
    cpu_u64 i;
    if (!foreground() || !out) return 0;
    if (heap_alloc(PIPE_CAP, 8, &p) != HEAP_OK) return 0;
    /* Zero once so a reused heap block can never leak stale bytes into
       a fresh pipe (count==0 already forbids reads; this is defense). */
    for (i = 0; i < PIPE_CAP; ++i) ((cpu_u8 *)p)[i] = 0;
    *out = (cpu_u8 *)p;
    return 1;
}

int pipe_ring_free(cpu_u8 *ring_ptr)
{
    if (!foreground() || !ring_ptr) return 0;
    if (heap_free(ring_ptr) != HEAP_OK) panic("ring_free");
    return 1;
}

int pipe_attach(cpu_u8 *ring_ptr, unsigned int wslot_in, cpu_u64 wgen_in,
                unsigned int rslot_in, cpu_u64 rgen_in)
{
    if (!foreground() || !ring_ptr) return 0;
    if (live || ring) return 0;
    if (wslot_in >= UAPI_MAX_PROCS || rslot_in >= UAPI_MAX_PROCS) return 0;
    if (wslot_in == rslot_in || wgen_in == 0 || rgen_in == 0) return 0;
    ring = ring_ptr;
    wslot = wslot_in;
    wgen = wgen_in;
    rslot = rslot_in;
    rgen = rgen_in;
    rpos = 0;
    wpos = 0;
    count = 0;
    reader_open = 1;
    writer_open = 1;
    full_stalls = 0;
    empty_stalls = 0;
    turns = 0;
    total_written = 0;
    total_read = 0;
    live = 1;
    if (!pipe_check()) panic("attach_check");
    return 1;
}

cpu_u64 pipe_write(unsigned int slot, cpu_u64 gen,
                   const cpu_u8 *src, cpu_u64 len)
{
    cpu_u64 space, n, i;
    if (!foreground() || !src) return (cpu_u64)-2;
    if (!live || !ring) return (cpu_u64)-2;
    if (len > PIPE_CAP) return (cpu_u64)-2;
    if (slot != wslot || gen != wgen) return (cpu_u64)-2;
    if (!writer_open) return (cpu_u64)-2;
    /* Broken reader: the frozen (u64)-1 signal, even with space free.
       Never silent loss (0 accepted is reported, never hidden). */
    if (!reader_open) return (cpu_u64)-1;
    if (count > PIPE_CAP) return (cpu_u64)-2;
    space = PIPE_CAP - count;
    if (!space || !len) {
        if (!len) return 0;
        /* Full with a live reader: short-0 backpressure (the runtime
           yield-retries); the producer keeps every byte. */
        ++full_stalls;
        return 0;
    }
    n = len < space ? len : space;
    for (i = 0; i < n; ++i) {
        ring[wpos++] = src[i];
        if (wpos == PIPE_CAP) {
            wpos = 0;
            ++turns;
        }
    }
    count += n;
    total_written += n;
    if (!pipe_check()) panic("write_check");
    return n;
}

int pipe_read_stage(unsigned int slot, cpu_u64 gen, cpu_u8 *dst,
                    cpu_u64 len, cpu_u64 *n)
{
    cpu_u64 take, i;
    if (!foreground() || !dst || !n) return SYS_INVAL;
    if (!live || !ring) return SYS_INVAL;
    if (len > PIPE_CAP) return SYS_INVAL;
    if (slot != rslot || gen != rgen) return SYS_INVAL;
    if (!reader_open) return SYS_INVAL;
    if (count > PIPE_CAP) return SYS_INVAL;
    if (!count) {
        /* Empty with a live writer is retryable (AGAIN, outputs
           untouched); empty with the writer gone is terminal EOF
           (OK + zero). The two never conflate. */
        if (writer_open) {
            ++empty_stalls;
            return SYS_AGAIN;
        }
        *n = 0;
        return SYS_OK;
    }
    take = len < count ? len : count;
    if (!take) {
        ++empty_stalls;
        return SYS_AGAIN;
    }
    for (i = 0; i < take; ++i) {
        dst[i] = ring[(rpos + i) % PIPE_CAP];
    }
    *n = take;
    return SYS_OK;
}

int pipe_read_commit(cpu_u64 n)
{
    cpu_u64 i;
    if (!foreground()) return 0;
    if (!live || !ring) return 0;
    if (n > count || n > PIPE_CAP || rpos >= PIPE_CAP) return 0;
    for (i = 0; i < n; ++i) {
        if (++rpos == PIPE_CAP) {
            rpos = 0;
            ++turns;
        }
    }
    count -= n;
    total_read += n;
    if (!pipe_check()) panic("commit_check");
    return 1;
}

int pipe_note_terminal(unsigned int slot, cpu_u64 gen)
{
    if (!foreground()) return 0;
    /* No pipe, or a pipe this process does not own: no-op (ordinary
       children and recycled-slot occupants never touch endpoints). A
       slot match with a stale generation is the recycled-occupant case:
       the recorded owner is gone, so this termination closes nothing. */
    if (!live) return 1;
    if (slot == wslot) {
        if (gen != wgen) return 1;
        if (!writer_open) return 0; /* same-generation double close */
        writer_open = 0;
    } else if (slot == rslot) {
        if (gen != rgen) return 1;
        if (!reader_open) return 0; /* same-generation double close */
        reader_open = 0;
    } else {
        return 1;
    }
    if (!reader_open && !writer_open) {
        /* Both endpoints closed: free exactly once. Buffered bytes (if
           any) are discarded with the storage; readers already terminal
           observe EOF before this point, never stale data after. */
        cpu_u8 *old = ring;
        ring = 0;
        rpos = 0;
        wpos = 0;
        count = 0;
        rgen = 0;
        wgen = 0;
        live = 0;
        if (!pipe_ring_free(old)) return 0;
    }
    if (!pipe_check()) panic("close_check");
    return 1;
}

void pipe_snapshot(struct pipe_snapshot *out)
{
    if (!foreground() || !out) return;
    out->allocated = (cpu_u64)live;
    out->count = count;
    out->reader_open = (cpu_u64)reader_open;
    out->writer_open = (cpu_u64)writer_open;
    out->reader_slot = (cpu_u64)rslot;
    out->writer_slot = (cpu_u64)wslot;
    out->full_stalls = full_stalls;
    out->empty_stalls = empty_stalls;
    out->turns = turns;
    out->total_written = total_written;
    out->total_read = total_read;
}
