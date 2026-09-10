/* Slice D faulting consumer: reads and verifies 500 pattern bytes
 * (seed 0) from fd0, then faults (ud2). The driver pairs it with
 * p_prod (len 4096): the producer must observe the broken reader
 * ((u64)-1) and exit 77 while this process reports FAULTED. No wedge:
 * the producer never spins against the dead reader. */
#include "rt.h"
#include "rt_pipe.h"

int rt_main(void)
{
    static unsigned char buf[500];
    unsigned long long at = 0, retries = 0;
    unsigned long long k;
    for (;;) {
        unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
        enum rt_err rc = rt_pipe_read_once(buf + at, sizeof(buf) - at, &n);
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
        at += n;
        if (at >= sizeof(buf))
            break;
    }
    for (k = 0; k < sizeof(buf); ++k)
        if (buf[k] != (unsigned char)((k * 13u + 0x41u) & 0xffu))
            rt_exit(12);
    __asm__ volatile ("ud2" ::: "memory");
    rt_exit(99);
    return 0;
}
