/* P1-A3 directory-exhaustion cycle probe: self-calibrating fill
 * to exactly full, unlink-one, recreate-into-the-freed-slot, and a
 * full-again proof. Exit 0 when every row matches; otherwise the
 * failing row's code (200-206). The host proves from the disk image
 * that the directory is exactly full and that /zx occupies /z0's
 * old slot.
 *
 * Drive contract: enough directory slack for at least one filler
 * (the test uses dir_slack=1); fillers are empty so data slack is
 * irrelevant.
 */
#include "rt.h"
#include "rt_pipe.h"
#include "rt_fs.h"

static unsigned long long slen(const char *s, unsigned long long cap)
{
    unsigned long long n = 0;
    while (n < cap && s[n] != 0)
        ++n;
    return n;
}

static unsigned long long gate_create(const char *path,
                                      unsigned long long plen)
{
    return rt_gate6(RT_SYS_FCREATE, (unsigned long long)path, plen, 0,
                    0, 0, 0);
}

static unsigned long long gate_unlink(const char *path,
                                      unsigned long long plen)
{
    return rt_gate6(RT_SYS_UNLINK, (unsigned long long)path, plen, 0,
                    0, 0, 0);
}

static int name_eq(const unsigned char *a, const char *b)
{
    unsigned long long i;
    for (i = 0; i < 40; ++i) {
        if (a[i] != (unsigned char)b[i])
            return 0;
        if (a[i] == 0)
            return 1;
    }
    return 0;
}

int rt_main(void)
{
    static struct rt_dirent de;
    static char name[8];
    unsigned long long n0 = 0;
    unsigned long long made = 0;
    unsigned long long i;
    unsigned long long rc;
    unsigned long long zx_seen = 0;
    /* Baseline live count (the drive carries the shell tree, so the
       probe measures N0 instead of assuming it). */
    for (i = 0;; ++i) {
        rc = rt_gate6(RT_SYS_READDIR, i, (unsigned long long)&de, 0,
                      0, 0, 0);
        if (rc == RT_SYS_END)
            break;
        if (rc != 0u)
            rt_exit(200);
        if (i > 600)
            rt_exit(200);
    }
    n0 = i;
    /* Fill to exactly full: /z0, /z1, ... until NOSPC. */
    for (i = 0; i < 64; ++i) {
        name[0] = '/';
        name[1] = 'z';
        if (i < 10) {
            name[2] = (char)((unsigned long long)'0' + i);
            name[3] = 0;
        } else {
            name[2] = (char)((unsigned long long)'0' + i / 10);
            name[3] = (char)((unsigned long long)'0' + i % 10);
            name[4] = 0;
        }
        rc = gate_create(name, slen(name, 8));
        if (rc == 12u)
            break;
        if (rc != 0u)
            rt_exit(201);
        ++made;
    }
    if (i >= 64)
        rt_exit(201);
    if (made == 0)
        rt_exit(200);
    /* One slot frees, one file returns, the door shuts again. */
    if (gate_unlink("/z0", slen("/z0", 64)) != 0u)
        rt_exit(202);
    if (gate_create("/zx", slen("/zx", 64)) != 0u)
        rt_exit(203);
    if (gate_create("/zy", slen("/zy", 64)) != 12u)
        rt_exit(204);
    /* Walk: dense count restored, /zx live, /z0 gone. */
    for (i = 0;; ++i) {
        rc = rt_gate6(RT_SYS_READDIR, i, (unsigned long long)&de, 0,
                      0, 0, 0);
        if (rc == RT_SYS_END)
            break;
        if (rc != 0u)
            rt_exit(205);
        if (name_eq(de.name, "/zx"))
            zx_seen = 1;
        if (name_eq(de.name, "/z0"))
            rt_exit(206);
        if (i > 600)
            rt_exit(205);
    }
    if (i != n0 + made)
        rt_exit(205);
    if (!zx_seen)
        rt_exit(206);
    rt_exit(0);
    return 0;
}
