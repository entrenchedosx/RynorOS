/* Bounded FIFO byte buffer over caller-owned storage. Ring semantics with
   wrap-around. All reads/writes stay inside the caller-owned [data,data+cap)
   region; no partial writes on full; counts never exceed cap. */

#include "kbuf.h"
#include "region.h"

static int valid(const struct kbuffer *b)
{
    return b && b->data && b->cap && b->cap <= KBUF_MAX_CAP &&
           b->head < b->cap && b->count <= b->cap && rt_region(b->data, b->cap) &&
           !rt_overlap(b, sizeof *b, b->data, b->cap);
}
static int external(const struct kbuffer *b, const void *p, cpu_u64 n)
{
    return rt_region(p, n) && !rt_overlap(p, n, b, sizeof *b) &&
           !rt_overlap(p, n, b->data, b->cap);
}

int kbuf_init(struct kbuffer *b, cpu_u8 *storage, cpu_u64 cap)
{
    if (!b || !storage || !cap || cap > KBUF_MAX_CAP || !rt_region(storage, cap) ||
        rt_overlap(b, sizeof *b, storage, cap)) return KBUF_INVALID;
    b->data = storage; b->cap = cap; b->head = 0; b->count = 0;
    return KBUF_OK;
}

int kbuf_clear(struct kbuffer *b)
{
    if (!valid(b)) return KBUF_INVALID;
    b->head = 0; b->count = 0;
    return KBUF_OK;
}

cpu_u64 kbuf_capacity(const struct kbuffer *b)
{ return valid(b) ? b->cap : 0; }

cpu_u64 kbuf_used(const struct kbuffer *b)
{ return valid(b) ? b->count : 0; }

cpu_u64 kbuf_remaining(const struct kbuffer *b)
{
    if (!valid(b)) return 0;
    return b->cap - b->count; /* count <= cap by construction */
}

int kbuf_append(struct kbuffer *b, const void *src, cpu_u64 n)
{
    if (!valid(b) || n > KBUF_MAX_CAP || !external(b, src, n)) return KBUF_INVALID;
    if (b->count > b->cap) return KBUF_INVALID;          /* corrupted count */
    if (n > b->cap - b->count) return KBUF_FULL;         /* no partial write */
    const cpu_u8 *s = src;
    cpu_u64 write = (b->head + b->count) % b->cap;
    for (cpu_u64 i = 0; i < n; ++i) {
        b->data[(write + i) % b->cap] = s[i];
    }
    b->count += n;
    return KBUF_OK;
}

int kbuf_append_byte(struct kbuffer *b, cpu_u8 v)
{
    if (!valid(b)) return KBUF_INVALID;
    if (b->count >= b->cap) return KBUF_FULL;
    cpu_u64 write = (b->head + b->count) % b->cap;
    b->data[write] = v;
    b->count += 1;
    return KBUF_OK;
}

int kbuf_peek(const struct kbuffer *b, cpu_u64 offset, cpu_u8 *out)
{
    if (!valid(b) || !out || !external(b, out, 1)) return KBUF_INVALID;
    if (offset >= b->count) return KBUF_EMPTY;
    *out = b->data[(b->head + offset) % b->cap];
    return KBUF_OK;
}

int kbuf_consume(struct kbuffer *b, cpu_u64 n)
{
    if (!valid(b)) return KBUF_INVALID;
    if (n > b->count) return KBUF_EMPTY;
    b->head = (b->head + n) % b->cap;
    b->count -= n;
    return KBUF_OK;
}

int kbuf_read(struct kbuffer *b, void *dst, cpu_u64 n)
{
    if (!valid(b) || n > KBUF_MAX_CAP || !external(b, dst, n)) return KBUF_INVALID;
    if (n > b->count) return KBUF_EMPTY; /* no partial read */
    cpu_u8 *d = dst;
    for (cpu_u64 i = 0; i < n; ++i) {
        d[i] = b->data[(b->head + i) % b->cap];
    }
    b->head = (b->head + n) % b->cap;
    b->count -= n;
    return KBUF_OK;
}
