/* Slice D faulting producer: emits exactly 1000 pattern bytes
 * (seed 0, same formula as p_prod.c) through fd1, then faults (ud2).
 * The driver pairs it with p_cons (len 4096): the consumer must drain
 * the buffered prefix, observe terminal EOF, and exit 11 (early EOF)
 * while this process reports FAULTED. No wedge, no lost bytes. */
#include "rt.h"
#include "rt_pipe.h"

int rt_main(void)
{
    static unsigned char buf[1000];
    unsigned long long k, at = 0;
    for (k = 0; k < sizeof(buf); ++k)
        buf[k] = (unsigned char)((k * 13u + 0x41u) & 0xffu);
    while (at < sizeof(buf)) {
        unsigned long long got = rt_gate6(RT_SYS_WRITE, RT_FD_STDOUT,
                                          (unsigned long long)(buf + at),
                                          sizeof(buf) - at, 0, 0, 0);
        if (got == (unsigned long long)-1)
            rt_exit(77);
        if (got > sizeof(buf) - at)
            rt_exit(79);
        at += got;
        if (at < sizeof(buf) && got == 0) {
            if (rt_nap(1) != RT_OK)
                rt_exit(80);
        }
    }
    __asm__ volatile ("ud2" ::: "memory");
    rt_exit(99);
    return 0;
}
