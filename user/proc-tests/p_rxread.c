/* Slice D hostile-output reader: proves a failed pipe-read copyout
 * dequeues nothing. First reads 512 bytes into an RX destination
 * (expects INVAL=2 with zero consumption), then reads 512 bytes for
 * real and verifies byte-exact continuity from offset 0 (any dequeued-
 * and-lost prefix would shift the stream and fail the check).
 *
 * argv[0] = decimal byte count, must be 512.
 * argv[1] = decimal seed, must be 0.
 *
 * Exit codes: 42 intact stream + clean EOF; 11 short; 12 shifted
 * stream (lost bytes); 13 read error; 14 retry cap; 20+ violations.
 */
#include "rt.h"
#include "rt_pipe.h"

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

extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

int rt_main(int argc, char **argv)
{
    static unsigned char buf[512];
    unsigned long long len = 0, seed = 0, n = 0, retries = 0;
    unsigned long long k;
    enum rt_err rc;
    if (argc != 2)
        rt_exit(20);
    if (!parse_u32(argv[0], &len) || len != 512)
        rt_exit(21);
    if (!parse_u32(argv[1], &seed) || seed != 0)
        rt_exit(22);
    /* Hostile destination first: RX code page, exact INVAL, no input. */
    n = 0xAAAAAAAAAAAAAAAAULL;
    if (rt_gate6(RT_SYS_READ, RT_FD_STDIN, 0x400000ULL, 512,
                 (unsigned long long)&n, 0, 0) != 2)
        rt_exit(23);
    /* Real read must start at offset zero (nothing was consumed):
       accumulate the full 512 across short/AGAIN rounds. */
    {
        unsigned long long at = 0;
        while (at < 512) {
            n = 0xAAAAAAAAAAAAAAAAULL;
            rc = rt_pipe_read_once(buf + at, 512 - at, &n);
            if (rc == RT_AGAIN) {
                if (++retries > RT_PIPE_RETRY_MAX)
                    rt_exit(14);
                if (rt_nap(1) != RT_OK)
                    rt_exit(16);
                continue;
            }
            if (rc != RT_OK)
                rt_exit(13);
            if (n == 0)
                rt_exit(11);
            if (n > 512 - at)
                rt_exit(13);
            at += n;
        }
    }
    for (k = 0; k < 512; ++k)
        if (buf[k] != (unsigned char)((k * 13u + 0x41u) & 0xffu))
            rt_exit(12);
    rt_exit(42);
    return 0;
}
