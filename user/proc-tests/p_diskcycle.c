/* P1-A3 data-exhaustion cycle probe: write onto an exactly-full
 * disk, free one block by deleting /one, land /q in the freed
 * block, and prove the disk is full again. Exit 0 when every row
 * matches; otherwise the failing row's code (210-215). The host
 * proves from the disk image that /q's extent starts exactly where
 * /one's did.
 *
 * Drive contract: data_slack=0 (data region exactly full) with
 * directory slack for /q and /q2 (the test uses dir_slack=1).
 * /one is one block (the 17c battery grows it to 2 bytes in
 * place), so deleting it frees exactly one block.
 */
#include "rt.h"
#include "rt_pipe.h"
#include "rt_fs.h"

static unsigned char one[1];
static struct rt_stat st;

static unsigned long long slen(const char *s, unsigned long long cap)
{
    unsigned long long n = 0;
    while (n < cap && s[n] != 0)
        ++n;
    return n;
}

int rt_main(void)
{
    unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
    unsigned long long rc;
    one[0] = 0x51u;
    if (rt_gate6(RT_SYS_FCREATE, (unsigned long long)"/q",
                slen("/q", 64), 0, 0, 0, 0) != 0u)
        rt_exit(210);
    /* Full disk: the 1-byte growth has nowhere to go. */
    rc = rt_gate6(RT_SYS_FWRITE, (unsigned long long)"/q",
                 slen("/q", 64), 0, (unsigned long long)one, 1,
                 (unsigned long long)&n);
    if (rc != 12u)
        rt_exit(210);
    /* Free exactly one block, land /q in it. */
    if (rt_gate6(RT_SYS_UNLINK, (unsigned long long)"/one",
                slen("/one", 64), 0, 0, 0, 0) != 0u)
        rt_exit(211);
    n = 0xAAAAAAAAAAAAAAAAULL;
    rc = rt_gate6(RT_SYS_FWRITE, (unsigned long long)"/q",
                 slen("/q", 64), 0, (unsigned long long)one, 1,
                 (unsigned long long)&n);
    if (rc != 0u || n != 1u)
        rt_exit(212);
    rc = rt_gate6(RT_SYS_FSTAT, (unsigned long long)"/q",
                 slen("/q", 64), (unsigned long long)&st, 0, 0, 0);
    if (rc != 0u || st.type != 1u || st.size != 1u)
        rt_exit(213);
    /* Full again: /q2 creates (slot slack) but cannot grow. */
    if (rt_gate6(RT_SYS_FCREATE, (unsigned long long)"/q2",
                slen("/q2", 64), 0, 0, 0, 0) != 0u)
        rt_exit(214);
    n = 0xAAAAAAAAAAAAAAAAULL;
    rc = rt_gate6(RT_SYS_FWRITE, (unsigned long long)"/q2",
                 slen("/q2", 64), 0, (unsigned long long)one, 1,
                 (unsigned long long)&n);
    if (rc != 12u)
        rt_exit(214);
    /* Readback of the reused block. */
    one[0] = 0u;
    n = 0xAAAAAAAAAAAAAAAAULL;
    rc = rt_gate6(7u, (unsigned long long)"/q", slen("/q", 64), 0,
                 (unsigned long long)one, 1, (unsigned long long)&n);
    if (rc != 0u || n != 1u || one[0] != 0x51u)
        rt_exit(215);
    rt_exit(0);
    return 0;
}
