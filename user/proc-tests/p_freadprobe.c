/* Slice D probe: syscall-7 fread matrix through the real gate.
 * Every row expects an exact code; 140+ = violation class. Content
 * echoes go to serial for host-side byte comparison; exit 0 = green.
 *
 * Files (Slice D image): /f/hello.txt (short text), /f/empty (0 B),
 * /f/big.bin (20000 pattern bytes: byte[i]=(i*13+0x41)&0xFF),
 * /f/bad.rnx (bad magic: fread reads it as plain data).
 */
#include "rt.h"

extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

static const char hello[] = "/f/hello.txt";
static const char big[] = "/f/big.bin";
static const char empty[] = "/f/empty.txt";
static const char bad[] = "/f/bad.rnx";
static const char missing[] = "/f/nope.txt";
static const char isdir[] = "/f";
static const char traversal[] = "/f/../t/exit42.rnx";
static const char dblslash[] = "/f//hello.txt";
static const char trailslash[] = "/f/hello.txt/";

static unsigned long long fr(const char *p, unsigned long long plen,
                             unsigned long long off, void *buf,
                             unsigned long long len, unsigned long long *n)
{
    return rt_gate6(7, (unsigned long long)p, plen, off,
                    (unsigned long long)buf, len, (unsigned long long)n);
}

static unsigned char pat(unsigned long long i)
{
    return (unsigned char)((i * 13u + 0x41u) & 0xffu);
}

/* Bounded length (cap 32, the frozen path bound). */
static unsigned long long slen(const char *s)
{
    unsigned long long n = 0;
    while (n < 32 && s[n] != 0)
        ++n;
    return n;
}

int rt_main(void)
{
    static unsigned char buf[2048];
    unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
    unsigned long long k;
    for (k = 0; k < sizeof(buf); ++k)
        buf[k] = (unsigned char)0x5a;
    /* Valid short read + echo for host goldens. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(hello, slen(hello), 0, buf, 8, &n) != 0)
        rt_exit(140);
    if (n != 8)
        rt_exit(141);
    /* Exact-EOF and crossing-EOF on the short file are length-driven;
       the host pins exact bytes from the echo below. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(hello, slen(hello), 0, buf, sizeof(buf), &n) != 0)
        rt_exit(142);
    if (n == 0 || n > sizeof(buf))
        rt_exit(143);
    if (rt_write(RT_FD_STDOUT, buf, n) != RT_OK)
        rt_exit(144);
    /* Zero length: OK + 0, buf untouched. */
    for (k = 0; k < 16; ++k)
        buf[k] = (unsigned char)0x5a;
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(hello, slen(hello), 0, buf, 0, &n) != 0)
        rt_exit(145);
    if (n != 0)
        rt_exit(146);
    for (k = 0; k < 16; ++k)
        if (buf[k] != (unsigned char)0x5a)
            rt_exit(147);
    /* Zero length on a missing file still names nothing: NOTFOUND. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(missing, slen(missing), 0, buf, 0, &n) != 3)
        rt_exit(148);
    /* Max chunk in 2048 eighths (userspace loops by design; the 2 KiB
       static buffer keeps the v1 data window): each eighth full,
       pattern-exact at absolute offsets. */
    for (k = 0; k < 8; ++k) {
        unsigned long long j;
        n = 0xAAAAAAAAAAAAAAAAULL;
        if (fr(big, slen(big), k * 2048u, buf, sizeof(buf), &n) != 0)
            rt_exit(149);
        if (n != sizeof(buf))
            rt_exit(150);
        for (j = 0; j < n; ++j)
            if (buf[j] != pat(k * 2048u + j))
                rt_exit(151);
    }
    /* Max+1 rejects with INVAL; outputs untouched. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(big, slen(big), 0, buf, 16385, &n) != 2)
        rt_exit(152);
    if (n != 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(153);
    /* Missing vs malformed vs directory classes. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(missing, slen(missing), 0, buf, 16, &n) != 3)
        rt_exit(154);
    if (n != 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(155);
    if (fr(isdir, slen(isdir), 0, buf, 16, &n) != 4)
        rt_exit(156);
    if (fr("/", 1, 0, buf, 16, &n) != 4)
        rt_exit(157);
    /* Bad shapes: traversal, doubled slash, trailing slash. */
    if (fr(traversal, slen(traversal), 0, buf, 16, &n) != 8)
        rt_exit(158);
    if (fr(dblslash, slen(dblslash), 0, buf, 16, &n) != 8)
        rt_exit(159);
    if (fr(trailslash, slen(trailslash), 0, buf, 16, &n) != 8)
        rt_exit(160);
    if (fr(hello, 0, 0, buf, 16, &n) != 8)
        rt_exit(161);
    if (fr(hello, 33, 0, buf, 16, &n) != 8)
        rt_exit(162);
    /* Hostile pointers return (never die): unmapped, wrapping, RX
       destination, cross-page spill, RX/wrapping count output. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr((const char *)0x500000ULL, 11, 0, buf, 16, &n) != 8)
        rt_exit(163);
    if (fr((const char *)0xFFFFFFFFFFFFFFF0ULL, 32, 0, buf, 16, &n) != 8)
        rt_exit(164);
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(hello, slen(hello), 0, (void *)0x400000ULL, 16, &n) != 8)
        rt_exit(165);
    if (n != 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(166);
    if (fr(hello, slen(hello), 0, (void *)0x600FF0ULL, 32, &n) != 8)
        rt_exit(167);
    if (fr(hello, slen(hello), 0, buf, 16, (unsigned long long *)0x400100ULL) != 8)
        rt_exit(168);
    if (fr(hello, slen(hello), 0, buf, 16,
           (unsigned long long *)0xFFFFFFFFFFFFFFF8ULL) != 8)
        rt_exit(169);
    /* Offset boundaries on the 20000-byte file. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(big, slen(big), 20000, buf, 16, &n) != 0)
        rt_exit(170);
    if (n != 0)
        rt_exit(171);
    if (fr(big, slen(big), 20001, buf, 16, &n) != 8)
        rt_exit(172);
    if (fr(big, slen(big), 19990, buf, 64, &n) != 0)
        rt_exit(173);
    if (n != 10)
        rt_exit(174);
    for (k = 0; k < n; ++k)
        if (buf[k] != pat(19990u + k))
            rt_exit(175);
    /* Empty file: exact EOF. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(empty, slen(empty), 0, buf, 16, &n) != 0)
        rt_exit(176);
    if (n != 0)
        rt_exit(177);
    /* Non-executable input is plain data to fread (no RYNX check). */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(bad, slen(bad), 0, buf, 64, &n) != 0)
        rt_exit(178);
    if (n == 0)
        rt_exit(179);
    /* 64-bit truncation probes: high bits set must not alias low
       values (a truncated offset 0 would read data; a truncated
       length 8 would succeed). */
    if (fr(hello, slen(hello), 0x100000000ULL, buf, 16, &n) != 8)
        rt_exit(180);
    if (fr(big, 0x100000009ULL, 0, buf, 16, &n) != 8)
        rt_exit(181);
    /* Head echo of big.bin for host-side pattern confirmation. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (fr(big, slen(big), 0, buf, 64, &n) != 0)
        rt_exit(182);
    if (n != 64)
        rt_exit(183);
    if (rt_write(RT_FD_STDOUT, buf, n) != RT_OK)
        rt_exit(184);
    rt_exit(0);
    return 0;
}
