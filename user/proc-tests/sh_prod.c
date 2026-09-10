/* Slice E producer: Unix-argv twin of p_prod (Slice D bare-argv
 * programs cannot be reused: the shell passes argv[0]=command name
 * per the frozen spawn convention, so shell-side programs read
 * arguments from argv[1..]).
 *
 * Emits argv[1] bytes of the offset-indexed pattern (argv[2] seed,
 * argv[3] flag chars: 'y' yield-first, 't' throttle) through fd1 in
 * deliberately unaligned chunks, then exits 42 (77 broken pipe,
 * 78 retry cap, 70+ usage violations). Pattern shared verbatim with
 * p_prod.c/sh_cons.py-side verifiers: byte[i]=(i*13+0x41+seed*7)&FF.
 */
#include "rt.h"
#include "rt_pipe.h"

static unsigned char pat(unsigned long long i, unsigned long long seed)
{
    return (unsigned char)((i * 13u + 0x41u + seed * 7u) & 0xffu);
}

static int parse_u32(const char *s, unsigned long long *out)
{
    unsigned long long v = 0;
    unsigned int digits = 0;
    if (s == 0)
        return 0;
    while (*s) {
        if (*s < '0' || *s > '9')
            return 0;
        v = v * 10u + (unsigned long long)(unsigned int)(*s - '0');
        if (v > 1048576u)
            return 0;
        ++digits;
        ++s;
    }
    if (!digits)
        return 0;
    *out = v;
    return 1;
}

int rt_main(int argc, char **argv)
{
    static const unsigned long long chunks[] = {997, 503, 2048, 127, 1500, 64, 3000, 311};
    static unsigned char buf[3000];
    unsigned long long len = 0, seed = 0, at = 0, ci = 0;
    unsigned int k;
    int yield_first = 0, throttle = 0;
    unsigned int fi = 0;
    if (argc < 2 || argc > 4)
        rt_exit(70);
    if (!parse_u32(argv[1], &len))
        rt_exit(71);
    if (argc >= 3 && !parse_u32(argv[2], &seed))
        rt_exit(72);
    if (argc == 4) {
        while (argv[3][fi] != 0) {
            if (argv[3][fi] == 'y')
                yield_first = 1;
            else if (argv[3][fi] == 't')
                throttle = 1;
            else
                rt_exit(73);
            ++fi;
            if (fi > 8)
                rt_exit(73);
        }
        if (yield_first && rt_nap(64) != RT_OK)
            rt_exit(74);
    }
    while (at < len) {
        unsigned long long want = chunks[ci % 8];
        unsigned long long n, retries = 0;
        if (want > len - at)
            want = len - at;
        for (k = 0; k < want; ++k)
            buf[k] = pat(at + k, seed);
        n = 0;
        for (;;) {
            unsigned long long got = rt_gate6(RT_SYS_WRITE, RT_FD_STDOUT,
                                              (unsigned long long)(buf + n),
                                              want - n, 0, 0, 0);
            if (got == (unsigned long long)-1)
                rt_exit(77);
            if (got > want - n)
                rt_exit(79);
            n += got;
            if (n >= want)
                break;
            if (++retries > RT_PIPE_RETRY_MAX)
                rt_exit(78);
            if (rt_nap(1) != RT_OK)
                rt_exit(80);
        }
        at += want;
        ++ci;
        if (throttle && rt_nap(2) != RT_OK)
            rt_exit(80);
    }
    rt_exit(42);
    return 0;
}
