/* Ring-0 kernel runtime services over the kstring/kbuf primitives. Each
   service is a pure, bounded, reentrant computation validated at dispatch;
   callers are trusted kernel threads. Not a userspace ABI. */

#include "krst.h"
#include "region.h"
#include "irq.h"

/* FNV-1a 64-bit digest: deterministic and independently recomputable by the
   host validation layer. */
cpu_u64 krst_digest(const void *data, cpu_u64 len)
{
    const cpu_u8 *p = data;
    cpu_u64 h = 0xcbf29ce484222325ULL;
    for (cpu_u64 i = 0; i < len; ++i) {
        h ^= p[i];
        h *= 0x100000001b3ULL;
    }
    return h;
}

static void put_u64_le(cpu_u8 *out, cpu_u64 v)
{
    for (unsigned int i = 0; i < 8; ++i) { out[i] = (cpu_u8)(v & 0xff); v >>= 8; }
}

__attribute__((section(".text.runtime_service"), noinline))
enum krst_result krst_call(enum krst_op op,
                           const void *in, cpu_u64 in_len,
                           void *out, cpu_u64 out_cap, cpu_u64 *out_len)
{
    if (irq_in_context()) return KRST_BAD_CONTEXT;
    if (!out_len || (cpu_u64)out_len % _Alignof(cpu_u64) ||
        !rt_region(out_len, sizeof *out_len)) return KRST_BAD_ARGS;
    if ((cpu_u64)op >= KRST_OP_COUNT) return KRST_BAD_OP;
    if (in_len > KRST_MAX_BYTES || out_cap > KRST_MAX_BYTES) return KRST_BAD_ARGS;
    if (in_len && !in) return KRST_BAD_ARGS;
    if (!out) return KRST_BAD_ARGS;
    if (!rt_region(in, in_len) || !rt_region(out, out_cap) ||
        rt_overlap(in, in_len, out, out_cap) ||
        rt_overlap(in, in_len, out_len, sizeof *out_len) ||
        rt_overlap(out, out_cap, out_len, sizeof *out_len)) return KRST_BAD_ARGS;

    switch (op) {
    case KRST_SVC_DIGEST: {
        if (out_cap < KRST_DIGEST_BYTES) return KRST_TOO_SMALL;
        put_u64_le(out, krst_digest(in, in_len));
        *out_len = KRST_DIGEST_BYTES;
        return KRST_OK;
    }
    case KRST_SVC_COUNT_DIGITS: {
        if (out_cap < KRST_COUNT_BYTES) return KRST_TOO_SMALL;
        const cpu_u8 *p = in;
        cpu_u64 count = 0;
        for (cpu_u64 i = 0; i < in_len; ++i) {
            cpu_u8 c = p[i];
            if (c >= '0' && c <= '9') ++count;
        }
        put_u64_le(out, count);
        *out_len = KRST_COUNT_BYTES;
        return KRST_OK;
    }
    case KRST_SVC_UPPER: {
        if (out_cap < in_len) return KRST_TOO_SMALL;
        const cpu_u8 *p = in;
        cpu_u8 *q = out;
        for (cpu_u64 i = 0; i < in_len; ++i) {
            cpu_u8 c = p[i];
            if (c >= 'a' && c <= 'z') c = (cpu_u8)(c - 32);
            q[i] = c;
        }
        *out_len = in_len;
        return KRST_OK;
    }
    default:
        return KRST_BAD_OP;
    }
}
