/* Slice D consumer: reads exactly len bytes from fd0 in deliberately
 * unaligned read chunks, verifies every byte against the producer
 * pattern (byte[i] = (i*13 + 0x41 + seed*7) & 0xFF, shared verbatim
 * with p_prod.c), then expects terminal EOF and exits 42.
 *
 * argv[0] = decimal byte count (required, <= 1048576).
 * argv[1] = decimal seed (optional, default 0).
 * argv[2] = flag chars (optional): 't' yields twice after every
 *           successful read (throttled consumer for producer-side
 *           stall proofs).
 *
 * Exit codes: 42 byte-exact + clean EOF; 11 early EOF (short stream);
 * 12 content mismatch; 13 read error; 14 retry cap; 15 trailing data
 * after the expected bytes; 60+ argument/usage violations.
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
    static const unsigned long long chunks[] = {113, 1021, 37, 512, 699, 43, 1500, 271};
    static unsigned char buf[1500];
    unsigned long long len = 0, seed = 0, at = 0, ci = 0;
    unsigned long long n = 0xAAAAAAAAAAAAAAAAULL, retries = 0;
    unsigned int k, fi;
    int throttle = 0;
    enum rt_err rc;
    if (argc < 1 || argc > 3)
        rt_exit(60);
    if (!parse_u32(argv[0], &len))
        rt_exit(61);
    if (argc >= 2 && !parse_u32(argv[1], &seed))
        rt_exit(62);
    if (argc == 3) {
        for (fi = 0; argv[2][fi] != 0; ++fi) {
            if (argv[2][fi] == 't')
                throttle = 1;
            else
                rt_exit(63);
            if (fi > 8)
                rt_exit(63);
        }
    }
    while (at < len) {
        unsigned long long want = chunks[ci % 8];
        if (want > len - at)
            want = len - at;
        n = 0xAAAAAAAAAAAAAAAAULL;
        rc = rt_pipe_read_once(buf, want, &n);
        if (rc == RT_AGAIN) {
            if (++retries > RT_PIPE_RETRY_MAX)
                rt_exit(14);
            if (rt_nap(1) != RT_OK)
                rt_exit(16);
            continue;
        }
        if (rc != RT_OK)
            rt_exit(13);
        if (n > want)
            rt_exit(13);
        if (n == 0)
            rt_exit(11);
        for (k = 0; k < n; ++k)
            if (buf[k] != pat(at + k, seed))
                rt_exit(12);
        at += n;
        ++ci;
        if (throttle && rt_nap(2) != RT_OK)
            rt_exit(16);
    }
    /* Exactly len bytes seen: drain to terminal EOF (the producer has
       exited or will exit; AGAIN retries until the writer closes). */
    retries = 0;
    for (;;) {
        n = 0xAAAAAAAAAAAAAAAAULL;
        rc = rt_pipe_read_once(buf, 37, &n);
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
            break;
        rt_exit(15);
    }
    rt_exit(42);
    return 0;
}
