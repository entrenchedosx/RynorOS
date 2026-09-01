#ifndef RYNOR_KBUF_H
#define RYNOR_KBUF_H
#include "cpu.h"

/* Bounded FIFO byte buffer over caller-owned storage. Ring semantics with
   wrap-around; capacity is fixed at init and need not be a power of two. No
   heap use, no allocation, no shared state: each buffer owns its caller region
   and callers may share a buffer only under their own
   synchronization. All indices and counts stay within [0, cap] and no helper
   reads/writes outside the caller-owned [data, data+cap) region. Storage and
   metadata must not overlap; external read/append/peek regions cannot alias
   either. Zero-byte read/append permit NULL and leave state unchanged. Clear
   discards logical data, does not erase storage, and does not repair corruption.
   Metadata must be initialized and not modified directly. Structural checks
   cannot validate arbitrary pointer provenance or detect every forged capacity.
   No allocation, blocking or IF change; callers supply synchronization. */

enum kbuf_result {
    KBUF_OK = 0,
    KBUF_INVALID,  /* null pointer, zero/oversized capacity, or bad direction */
    KBUF_FULL,     /* append would exceed remaining capacity (no partial write) */
    KBUF_EMPTY,    /* read/consume past the logical end */
};

#define KBUF_MAX_CAP (1ULL << 40)

struct kbuffer {
    cpu_u8 *data;
    cpu_u64 cap;    /* total capacity in bytes (fixed) */
    cpu_u64 head;   /* physical read index, 0..cap-1 (also when empty) */
    cpu_u64 count;  /* bytes currently buffered, 0..cap */
};

int kbuf_init(struct kbuffer *b, cpu_u8 *storage, cpu_u64 cap);
int kbuf_clear(struct kbuffer *b);
cpu_u64 kbuf_capacity(const struct kbuffer *b);
cpu_u64 kbuf_used(const struct kbuffer *b);
cpu_u64 kbuf_remaining(const struct kbuffer *b);
int kbuf_append(struct kbuffer *b, const void *src, cpu_u64 n);
int kbuf_append_byte(struct kbuffer *b, cpu_u8 v);
int kbuf_peek(const struct kbuffer *b, cpu_u64 offset, cpu_u8 *out);
int kbuf_consume(struct kbuffer *b, cpu_u64 n);
int kbuf_read(struct kbuffer *b, void *dst, cpu_u64 n);

#endif /* RYNOR_KBUF_H */
