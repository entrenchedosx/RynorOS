/* Slice C probe: multi-page v2 image (2 code pages + 2 data pages).
 * The 5000-nop pad forces the code window past one page (never called);
 * bigdata forces two data pages. The program verifies its data window
 * (zeros, then readback) and exits 0: entry on page 0 plus kernel
 * per-page mapping checks carry the cross-page proof.
 */
#include "rt.h"

static char bigdata[5000];

/* Second code page filler: kept by KEEP(*(.text.probe_pad)) in the v2
   link script (never executed; content irrelevant, zeros fine). */
__attribute__((section(".text.probe_pad"), used)) static const char code_pad[5000] = {0};

int rt_main(void)
{
    unsigned int i;
    if (code_pad[0] != 0 || code_pad[sizeof(code_pad) - 1] != 0)
        rt_exit(109);
    for (i = 0; i < sizeof(bigdata); ++i)
        if (bigdata[i] != 0)
            rt_exit(110 + (i & 3));
    for (i = 0; i < sizeof(bigdata); ++i)
        bigdata[i] = (char)(i & 0x7f);
    for (i = 0; i < sizeof(bigdata); ++i)
        if (bigdata[i] != (char)(i & 0x7f))
            rt_exit(120 + (i & 3));
    if (rt_write(RT_FD_STDOUT, "bigok", 5) != RT_OK)
        rt_exit(130);
    rt_exit(0);
    return 0;
}
