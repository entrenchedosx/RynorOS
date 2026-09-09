/* Stage 18c native runtime: validated wrappers over exit/write/yield.
 *
 * Freestanding C11, no libc, no host calls. Syscall numbers mirror the
 * frozen 18b set in kernel/include/syscall.h (restated in rt.h, pinned
 * equal by test; user builds never include kernel headers). All
 * arithmetic is unsigned (no UB traps); every public entry validates
 * before touching memory. Wild caller pointers still fault as
 * USER_FAULTED (caller bug); there is no intentional trap/panic path.
 */
#include "rt.h"
#include <stdarg.h>

/* Raw gate: EAX number, EBX/ECX/EDX args, return in EAX, all else
   preserved by the kernel (verified 18b ABI). */
static unsigned long long rt_gate(unsigned int num, unsigned long long a,
                                  unsigned long long b, unsigned long long c)
{
    unsigned long long ret = (unsigned long long)num;
    __asm__ volatile ("int $0x80"
                      : "+a" (ret)
                      : "b" (a), "c" (b), "d" (c)
                      : "memory", "cc");
    return ret;
}

void rt_exit(int code)
{
    (void)rt_gate(RT_SYS_EXIT, (unsigned long long)(unsigned int)code, 0, 0);
    __builtin_unreachable();
}

static enum rt_err rt_yield(void)
{
    (void)rt_gate(RT_SYS_YIELD, 0, 0, 0);
    return RT_OK;
}

enum rt_err rt_write(unsigned int fd, const void *buf, unsigned long long n)
{
    if (fd != RT_FD_STDOUT)
        return RT_INVAL;
    if (n > RT_WRITE_MAX)
        return RT_RANGE;
    if (n == 0)
        return RT_OK;
    if (buf == 0)
        return RT_INVAL;
    if (rt_gate(RT_SYS_WRITE, (unsigned long long)fd,
                (unsigned long long)buf, n) == (unsigned long long)-1)
        return RT_INVAL;
    return RT_OK;
}

/* Bounded length: scans at most cap bytes; reports failure (never a
   length) when no NUL appears in range. */
static unsigned long long rt_strlen_bounded(const char *s, unsigned long long cap,
                                            int *ok)
{
    unsigned long long n = 0;
    while (n < cap && s[n] != 0)
        ++n;
    if (n == cap) {
        *ok = 0;
        return 0;
    }
    *ok = 1;
    return n;
}

enum rt_err rt_print(const char *s)
{
    int ok = 0;
    unsigned long long n;
    if (s == 0)
        return RT_INVAL;
    n = rt_strlen_bounded(s, RT_PRINT_MAX, &ok);
    if (!ok)
        return RT_RANGE;
    return rt_write(RT_FD_STDOUT, s, n);
}

enum rt_err rt_print_bytes(const char *s, unsigned long long n)
{
    if (n > RT_WRITE_MAX)
        return RT_RANGE;
    if (n == 0)
        return RT_OK;
    if (s == 0)
        return RT_INVAL;
    return rt_write(RT_FD_STDOUT, s, n);
}

/* Six-argument gate (asm in rt_gate.asm, not C constraints, so RBP
   placement is exact): num->EAX, a->EBX, b->ECX, c->EDX, d->ESI,
   e->EDI, f->EBP; return in RAX. */
extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

enum rt_err rt_fd_read(unsigned int fd, void *buf, unsigned long long n,
                       unsigned long long *nread, unsigned long long flags)
{
    unsigned long long rc;
    if (fd != RT_FD_STDIN)
        return RT_INVAL;
    if (n > RT_READ_MAX)
        return RT_RANGE;
    if (flags != 0)
        return RT_INVAL;
    if (nread == 0)
        return RT_INVAL;
    if (n != 0 && buf == 0)
        return RT_INVAL;
    /* Full 64-bit values reach the kernel (B3: truncating any argument
       to 32 bits here would alias e.g. fd 0x1_00000000 to stdin). */
    rc = rt_gate6(RT_SYS_READ, (unsigned long long)fd,
                  (unsigned long long)buf, n,
                  (unsigned long long)nread, flags, 0);
    /* Return codes mirror kernel/include/uapi.h (SYS_OK 0, SYS_AGAIN 1);
       a repository test pins the equality. Anything else collapses to
       RT_INVAL here (BADARG has no rt_err peer; in-guest probes prove
       the kernel distinction directly). */
    if (rc == 0)
        return RT_OK;
    if (rc == 1)
        return RT_AGAIN;
    return RT_INVAL;
}

/* Transactional %s/%u/%x/%c formatter. Pass one measures with checked
   bounds; pass two emits. Anything invalid leaves dst untouched.
   Never NUL-terminates: the caller must use the returned count.
   fmt must be NUL-terminated within RT_PRINT_MAX bytes; cap must be
   <= INT64_MAX so the count never collides with negative errors. */
long long rt_fmt(char *buf, unsigned long long cap, const char *fmt, ...)
{
    va_list ap;
    unsigned long long need = 0;
    int bad = 0;
    if (buf == 0 || fmt == 0)
        return -(long long)RT_INVAL;
    if (cap > (unsigned long long)0x7FFFFFFFFFFFFFFFu)
        return -(long long)RT_RANGE;
    /* Measure pass (counted fmt scan: bound checked before any deref,
       mirroring rt_strlen_bounded; unterminated fmt fails closed with
       NUL required at index < RT_PRINT_MAX). */
    va_start(ap, fmt);
    for (unsigned long long fi = 0; ; ++fi) {
        char ch;
        if (fi >= RT_PRINT_MAX) {
            bad = 1;
            break;
        }
        ch = fmt[fi];
        if (ch == 0)
            break;
        unsigned long long add = 0;
        if (ch != '%') {
            add = 1;
        } else {
            char spec;
            ++fi;
            if (fi >= RT_PRINT_MAX) {
                bad = 1;
                break;
            }
            spec = fmt[fi];
            if (spec == 'c') {
                (void)va_arg(ap, int);
                add = 1;
            } else if (spec == 'u' || spec == 'x') {
                unsigned long long v = (unsigned long long)va_arg(ap, unsigned int);
                unsigned long long base = (spec == 'u') ? 10u : 16u;
                add = 1;
                while (v >= base) {
                    v /= base;
                    ++add;
                }
            } else if (spec == 's') {
                const char *s = va_arg(ap, const char *);
                int ok = 0;
                unsigned long long n = 0;
                if (s == 0) {
                    bad = 1;
                    break;
                }
                n = rt_strlen_bounded(s, RT_PRINT_MAX, &ok);
                if (!ok) {
                    bad = 1;
                    break;
                }
                add = n;
            } else {
                bad = 1;
                break;
            }
        }
        /* Overflow-safe fullness check (`add <= cap` first, so `cap -
           add` cannot wrap): anything not fitting fails before any byte
           is stored. */
        if (add > cap || need > cap - add) {
            bad = 2;
            break;
        }
        need += add;
    }
    va_end(ap);
    if (bad == 1)
        return -(long long)RT_INVAL;
    if (bad == 2)
        return -(long long)RT_RANGE;
    /* need <= cap <= INT64_MAX here, so the cast cannot collide with
       negative errors. Defensive re-check for future callers. */
    if (need > (unsigned long long)0x7FFFFFFFFFFFFFFFu)
        return -(long long)RT_RANGE;
    /* Emit pass: counted walk mirroring the measure pass (bound before
       deref). %s is re-bounded here, so a mid-call mutation cannot turn
       into an unbounded read or a buffer overflow; output stays bounded
       even then. */
    va_start(ap, fmt);
    {
        unsigned long long at = 0;
        for (unsigned long long fi = 0; ; ++fi) {
            char ch;
            if (fi >= RT_PRINT_MAX)
                break;
            ch = fmt[fi];
            if (ch == 0)
                break;
            if (ch != '%') {
                if (at >= cap)
                    break;
                buf[at++] = ch;
            } else {
                char spec;
                ++fi;
                if (fi >= RT_PRINT_MAX)
                    break;
                spec = fmt[fi];
                if (spec == 'c') {
                    if (at >= cap)
                        break;
                    buf[at++] = (char)va_arg(ap, int);
                } else if (spec == 'u' || spec == 'x') {
                    unsigned long long v = (unsigned long long)va_arg(ap, unsigned int);
                    unsigned long long base = (spec == 'u') ? 10u : 16u;
                    char tmp[20];
                    unsigned int nd = 0;
                    do {
                        unsigned int d = (unsigned int)(v % base);
                        tmp[nd++] = (char)(d < 10u ? '0' + d : 'a' + (d - 10u));
                        v /= base;
                    } while (v);
                    while (nd) {
                        if (at >= cap)
                            break;
                        buf[at++] = tmp[--nd];
                    }
                } else { /* %s: re-bounded (measure pass already validated). */
                    const char *s = va_arg(ap, const char *);
                    int ok = 0;
                    unsigned long long n = rt_strlen_bounded(s == 0 ? "" : s,
                                                             RT_PRINT_MAX, &ok);
                    if (s == 0 || !ok)
                        break;
                    for (unsigned long long k = 0; k < n; ++k) {
                        if (at >= cap)
                            break;
                        buf[at++] = s[k];
                    }
                }
            }
        }
        (void)at;
    }
    va_end(ap);
    return (long long)need;
}

/* Bounded arena: fixed array in .bss (linker keeps it inside the data
   window; overflow fails at converter time, never here). Bump pointer
   with a 16-entry live ledger; frees never reclaim, so the watermark
   is monotonic and every number is deterministic. */
static unsigned char rt_arena[RT_ARENA_SIZE];
static unsigned long long rt_brk;
static struct {
    const void *ptr;
    unsigned long long size;
    int live;
} rt_ledger[RT_ARENA_SLOTS];

static int rt_align_ok(unsigned long long align)
{
    return align != 0 && align <= RT_ARENA_ALIGN_MAX &&
           (align & (align - 1u)) == 0;
}

enum rt_err rt_alloc(unsigned long long align, unsigned long long size, void **out)
{
    unsigned long long base, end;
    unsigned int slot;
    if (out == 0 || !rt_align_ok(align) || size == 0)
        return RT_INVAL;
    if (size > RT_ARENA_SIZE)
        return RT_RANGE;
    base = (rt_brk + align - 1u) & ~(align - 1u);
    if (base < rt_brk)
        return RT_NOMEM;
    end = base + size;
    if (end < base || end > RT_ARENA_SIZE)
        return RT_NOMEM;
    for (slot = 0; slot < RT_ARENA_SLOTS; ++slot)
        if (!rt_ledger[slot].live)
            break;
    if (slot == RT_ARENA_SLOTS)
        return RT_NOMEM;
    rt_ledger[slot].ptr = (const void *)&rt_arena[base];
    rt_ledger[slot].size = size;
    rt_ledger[slot].live = 1;
    rt_brk = end;
    *out = (void *)&rt_arena[base];
    return RT_OK;
}

enum rt_err rt_free(void *ptr)
{
    unsigned int slot;
    if (ptr == 0)
        return RT_INVAL;
    for (slot = 0; slot < RT_ARENA_SLOTS; ++slot) {
        if (rt_ledger[slot].live && rt_ledger[slot].ptr == ptr) {
            rt_ledger[slot].live = 0;
            return RT_OK;
        }
    }
    return RT_INVAL;
}

unsigned long long rt_arena_watermark(void) { return rt_brk; }

unsigned long long rt_live_count(void)
{
    unsigned long long n = 0;
    for (unsigned int slot = 0; slot < RT_ARENA_SLOTS; ++slot)
        n += (unsigned long long)rt_ledger[slot].live;
    return n;
}

unsigned long long rt_ptr_off(const void *ptr)
{
    /* Read-only evidence only: integer range check avoids UB pointer
       subtraction; NULL/out-of-arena yields a sentinel, never a trap.
       Never consults or mutates the ledger, so it cannot authorize a
       free (rt_free checks live-pointer identity only). */
    unsigned long long base, addr;
    if (ptr == 0)
        return (unsigned long long)-1;
    base = (unsigned long long)(const void *)rt_arena;
    addr = (unsigned long long)(const void *)ptr;
    if (addr < base || addr >= base + (unsigned long long)RT_ARENA_SIZE)
        return (unsigned long long)-1;
    return addr - base;
}

enum rt_err rt_nap(unsigned long long yields)
{
    if (yields > RT_NAP_MAX)
        return RT_RANGE;
    while (yields--)
        (void)rt_yield();
    return RT_OK;
}

enum rt_err rt_set_flag(unsigned long long *p)
{
    if (p == 0)
        return RT_INVAL;
    *(volatile unsigned long long *)p = 1u;
    return RT_OK;
}

enum rt_err rt_wait_flag(unsigned long long *p, unsigned long long max_yields)
{
    unsigned long long i = 0;
    if (p == 0)
        return RT_INVAL;
    for (;;) {
        if (*(volatile unsigned long long *)p)
            return RT_OK;
        if (i >= max_yields)
            return RT_AGAIN;
        ++i;
        (void)rt_yield();
    }
}

enum rt_err rt_open(const char *path)
{
    (void)path;
    return RT_NOSYS;
}

enum rt_err rt_read(int fd, void *buf, unsigned long long n)
{
    (void)fd;
    (void)buf;
    (void)n;
    return RT_NOSYS;
}
