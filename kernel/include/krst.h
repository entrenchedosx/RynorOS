#ifndef RYNOR_KRST_H
#define RYNOR_KRST_H
#include "cpu.h"
#include "kstring.h"
#include "kbuf.h"

/* Ring-0 kernel runtime services: a narrow, validated, bounded dispatch
   boundary built on the kstring/kbuf primitives. A service has a defined
   request (op + bounded input region + bounded output region), rejects
   invalid/boundary requests explicitly, runs in bounded time, and never
   mutates shared kernel state (each service is stateless and reentrant, so it
   is safe to preempt between calls and to drive from several worker threads
   writing to distinct caller-owned outputs).

   This is not a syscall or a userspace ABI; callers are trusted ring-0 kernel
   threads. Payload sizes are bounded to KRST_MAX_BYTES so no service can
   traverse or copy an unbounded region. */

enum krst_op {
    KRST_SVC_DIGEST,      /* FNV-1a 64-bit digest of the input region */
    KRST_SVC_UPPER,       /* bounded ASCII uppercase of the input region */
    KRST_SVC_COUNT_DIGITS /* count of ASCII digits (0x30..0x39) in the region */
};
#define KRST_OP_COUNT 3u

enum krst_result {
    KRST_OK = 0,
    KRST_BAD_OP,      /* op outside the defined service set */
    KRST_BAD_ARGS,    /* null/overlapped/oversized input or output pointers */
    KRST_TOO_SMALL,   /* output region too small for the result; no partial write */
    KRST_BAD_CONTEXT, /* IRQ context forbidden; outputs unchanged */
};

#define KRST_DIGEST_BYTES 8u
#define KRST_COUNT_BYTES  8u
#define KRST_MAX_BYTES 65536u

/* Dispatches one runtime service. in_len/in bounded, out_cap bounded. On
   success out_len holds the number of bytes written to out. Every failure
   leaves out/out_len untouched. Foreground only (IRQ calls rejected), IF=0 or
   IF=1 preserved. No allocation/yield/blocking. Reentrant for disjoint outputs
   and immutable inputs. out must be nonnull even for zero capacity. out_len
   is a separate aligned writable u64, disjoint from input and full output
   extent. Trusted callers provide mapped live objects; this is not a memory
   protection boundary. Empty input permits NULL; empty UPPER writes length 0.
   All other overlaps and wrapping ranges are rejected before processing. */
enum krst_result krst_call(enum krst_op op,
                           const void *in, cpu_u64 in_len,
                           void *out, cpu_u64 out_cap, cpu_u64 *out_len);

/* Trusted helper, not a validating dispatch: data must be readable for len
   bytes (NULL only for len=0); callers bound len. No allocation or IF change. */
cpu_u64 krst_digest(const void *data, cpu_u64 len);

/* Stage 10 kernel runtime self-test: bounded strings, buffers, services, then
   the services driven from worker threads through the Stage 7 scheduler. */
void runtime_self_test(void);

#endif /* RYNOR_KRST_H */
