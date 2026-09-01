/* Bounded, freestanding string and memory primitives for the ring-0 kernel.
   No libc dependency. Every helper reads only within its declared source
   region and writes only within its declared destination capacity. Callers
   rely on the explicit bounds, never on an assumed trailing NUL past a
   declared maximum. */

#include "kstring.h"
#include "region.h"

cpu_u64 kstr_nlen(const char *s, cpu_u64 max)
{
    if (!rt_region(s, max)) return max;
    cpu_u64 n = 0;
    while (n < max && s[n] != 0) ++n;
    return n;
}

int kstr_copy(char *dst, cpu_u64 cap, const char *src)
{
    return kstr_copy_n(dst, cap, src, cap);
}

int kstr_copy_n(char *dst, cpu_u64 cap, const char *src, cpu_u64 src_max)
{
    if (!dst || !src || !cap || cap > KSTR_MAX_CAP || !src_max ||
        src_max > KSTR_MAX_CAP || !rt_region(dst, cap) || !rt_region(src, src_max)) return KSTR_INVALID;
    cpu_u64 n = 0;
    /* Bounded source scan: we can never copy more than cap-1 bytes. Sources
       without a NUL inside the window are treated as not fitting. */
    while (n < cap - 1 && n < src_max && src[n] != 0) ++n;
    if (n == src_max) return KSTR_TERMINATION;
    if (src[n] != 0) return KSTR_OVERFLOW;
    return kstr_move(dst, src, n + 1); /* includes NUL, permits overlap */
}

int kstr_cat(char *dst, cpu_u64 cap, const char *src)
{
    return kstr_cat_n(dst, cap, src, cap);
}

int kstr_cat_n(char *dst, cpu_u64 cap, const char *src, cpu_u64 src_max)
{
    if (!dst || !src || !cap || cap > KSTR_MAX_CAP || !src_max ||
        src_max > KSTR_MAX_CAP || !rt_region(dst, cap) || !rt_region(src, src_max)) return KSTR_INVALID;
    cpu_u64 len = kstr_nlen(dst, cap);
    if (len == cap) return KSTR_TERMINATION;
    char *tail = dst + len;
    cpu_u64 room = cap - len - 1;
    cpu_u64 n = 0;
    while (n < room && n < src_max && src[n] != 0) ++n;
    if (n == src_max) return KSTR_TERMINATION;
    if (src[n] != 0) return KSTR_OVERFLOW;
    return kstr_move(tail, src, n + 1);
}

int kstr_cmp(const char *a, const char *b, cpu_u64 max)
{
    if (!a || !b) return 0;
    for (cpu_u64 i = 0; i < max; ++i) {
        unsigned char ca = (unsigned char)a[i], cb = (unsigned char)b[i];
        if (ca != cb) return (int)ca - (int)cb;
        if (ca == 0) return 0;
    }
    return 0;
}

int kstr_cmpmem(const void *a, const void *b, cpu_u64 n)
{
    if (!a || !b || n == 0) return 0;
    const unsigned char *pa = a, *pb = b;
    for (cpu_u64 i = 0; i < n; ++i) {
        if (pa[i] != pb[i]) return (int)pa[i] - (int)pb[i];
    }
    return 0;
}

int kstr_chr(const char *s, cpu_u64 max, char c, cpu_u64 *pos)
{
    if (!s || !pos) return 0;
    for (cpu_u64 i = 0; i < max; ++i) {
        if (s[i] == c) { *pos = i; return 1; }
    }
    return 0;
}

int kstr_move(void *dst, const void *src, cpu_u64 n)
{
    if (!rt_region(dst, n) || !rt_region(src, n) || n > KSTR_MAX_CAP) return KSTR_INVALID;
    if (n == 0 || dst == src) return KSTR_OK;
    unsigned char *d = dst;
    const unsigned char *s = src;
    if ((cpu_u64)d < (cpu_u64)s) {
        for (cpu_u64 i = 0; i < n; ++i) d[i] = s[i];
    } else {
        for (cpu_u64 i = n; i != 0; --i) d[i - 1] = s[i - 1];
    }
    return KSTR_OK;
}

int kstr_utoa(char *dst, cpu_u64 cap, cpu_u64 value)
{
    char tmp[21]; unsigned int k = 20; tmp[k] = 0;
    if (value == 0) tmp[--k] = '0';
    while (value) { tmp[--k] = (char)('0' + value % 10); value /= 10; }
    return kstr_copy(dst, cap, tmp + k);
}

int kstr_utoa_hex(char *dst, cpu_u64 cap, cpu_u64 value, int upper)
{
    static const char low[] = "0123456789abcdef";
    static const char up[] = "0123456789ABCDEF";
    const char *tab = upper ? up : low;
    char tmp[17]; unsigned int k = 16; tmp[k] = 0;
    if (value == 0) tmp[--k] = '0';
    while (value) { tmp[--k] = tab[value & 15]; value >>= 4; }
    return kstr_copy(dst, cap, tmp + k);
}

/* Single bounded walk of the format string. When writing != 0 it commits bytes
   into dst; otherwise it only measures. In measuring mode it may stop as soon
   as the length would overflow cap (leaving dst untouched). Failure is
   transactional for the write pass because the two-pass entry ensures space
   before committing. %u/%x/%X read a cpu_u64 argument; %c an int; %s a bounded
   NUL string. */
static enum kstr_result fmt_walk(char *dst, cpu_u64 cap, cpu_u64 *u, const char *fmt,
                                 kstr_va_list ap, int writing)
{
    cpu_u64 used = *u;
    for (cpu_u64 i = 0; i < KSTR_NLEN_MAX; ++i) {
        char c = fmt[i];
        if (c == 0) { *u = used; return KSTR_OK; }
        if (c != '%') {
            if (!writing && used + 1 >= cap) return KSTR_OVERFLOW;
            if (writing) dst[used] = c;
            used += 1;
            continue;
        }
        if (++i == KSTR_NLEN_MAX) return KSTR_TERMINATION;
        char spec = fmt[i];
        if (spec == 0) return KSTR_INVALID;
        if (spec == '%') {
            if (!writing && used + 1 >= cap) return KSTR_OVERFLOW;
            if (writing) dst[used] = '%';
            used += 1;
            continue;
        }
        if (spec == 's') {
            const char *s = __builtin_va_arg(ap, const char *);
            if (!s || !rt_region(s, KSTR_NLEN_MAX)) return KSTR_INVALID;
            cpu_u64 n = kstr_nlen(s, KSTR_NLEN_MAX);
            if (n == KSTR_NLEN_MAX) return KSTR_TERMINATION;
            if (rt_overlap(dst, cap, s, n + 1)) return KSTR_INVALID;
            if (n > cap - 1 - used) return KSTR_OVERFLOW;
            for (cpu_u64 j = 0; j < n; ++j) { if (writing) dst[used] = s[j]; used += 1; }
        } else if (spec == 'c') {
            char ch = (char)__builtin_va_arg(ap, int);
            if (!writing && used + 1 >= cap) return KSTR_OVERFLOW;
            if (writing) dst[used] = ch;
            used += 1;
        } else if (spec == 'u' || spec == 'x' || spec == 'X') {
            cpu_u64 v = __builtin_va_arg(ap, cpu_u64);
            static const char lower[] = "0123456789abcdef";
            static const char upper[] = "0123456789ABCDEF";
            const char *tab = (spec == 'X') ? upper : lower;
            char tmp[21]; unsigned int k = 20; tmp[k] = 0;
            if (v == 0) tmp[--k] = '0';
            if (spec == 'u') { while (v) { tmp[--k] = (char)('0' + v % 10); v /= 10; } }
            else { while (v) { tmp[--k] = tab[v & 15]; v >>= 4; } }
            const char *p = tmp + k;
            while (*p) {
                if (!writing && used + 1 >= cap) return KSTR_OVERFLOW;
                if (writing) dst[used] = *p;
                used += 1; p += 1;
            }
        } else {
            return KSTR_INVALID;
        }
    }
    return KSTR_TERMINATION;
}

enum kstr_result kstr_vformat(char *dst, cpu_u64 cap, const char *fmt, kstr_va_list ap)
{
    if (!dst || !cap || cap > KSTR_MAX_CAP || !fmt || !rt_region(dst, cap) ||
        !rt_region(fmt, KSTR_NLEN_MAX)) return KSTR_INVALID;
    cpu_u64 fmt_len = kstr_nlen(fmt, KSTR_NLEN_MAX);
    if (fmt_len == KSTR_NLEN_MAX) return KSTR_TERMINATION;
    if (rt_overlap(dst, cap, fmt, fmt_len + 1)) return KSTR_INVALID;
    kstr_va_list first;
    __builtin_va_copy(first, ap);
    cpu_u64 need = 0;
    enum kstr_result r = fmt_walk(dst, cap, &need, fmt, first, 0);
    __builtin_va_end(first);
    if (r != KSTR_OK) return r;
    if (need + 1 > cap) return KSTR_OVERFLOW;
    cpu_u64 used = 0;
    r = fmt_walk(dst, cap, &used, fmt, ap, 1);
    if (r != KSTR_OK) return r;
    dst[used] = 0;
    return KSTR_OK;
}

enum kstr_result kstr_format(char *dst, cpu_u64 cap, const char *fmt, ...)
{
    kstr_va_list ap;
    __builtin_va_start(ap, fmt);
    enum kstr_result r = kstr_vformat(dst, cap, fmt, ap);
    __builtin_va_end(ap);
    return r;
}
