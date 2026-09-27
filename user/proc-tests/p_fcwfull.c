/* P1-A2 full-condition probe: argv[1] selects "dir" (create files
 * until the directory refuses with NOSPC) or "disk" (write 4 KiB
 * chunks until the allocator refuses with NOSPC). Exit 0 when NOSPC
 * arrives with prior content intact; 209 on bad arguments, 210-219
 * for dir rows, 220-229 for disk rows.
 *
 * Counts are asserted host-side (the probe cannot know the image's
 * fill level); the probe asserts NOSPC arrival, at least one success
 * on the way there (dir mode), and byte-exact preexisting content.
 */
#include "rt.h"
#include "rt_pipe.h"
#include "rt_fs.h"

#define SENT 0xAAAAAAAAAAAAAAAAULL

static unsigned char buf[4096];

static void stage(unsigned long long off, unsigned long long len,
                  unsigned seed)
{
    unsigned long long i;
    for (i = 0; i < len; ++i)
        buf[i] = (unsigned char)(((off + i) * 13u + seed) & 0xffu);
}

static int streq(const char *a, const char *b)
{
    while (*a && *b && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

/* /w/fNNN name builder (NNN decimal, zero-padded). */
static void fname(char *dst, unsigned n)
{
    dst[0] = '/';
    dst[1] = 'w';
    dst[2] = '/';
    dst[3] = 'f';
    dst[4] = (char)('0' + (n / 100u) % 10u);
    dst[5] = (char)('0' + (n / 10u) % 10u);
    dst[6] = (char)('0' + n % 10u);
    dst[7] = 0;
}

static int dir_mode(void)
{
    char name[8];
    unsigned created = 0;
    unsigned i;
    int saw_nospc = 0;
    for (i = 0; i < 600u; ++i) {
        unsigned long long rc;
        fname(name, i);
        rc = rt_gate6(RT_SYS_FCREATE, (unsigned long long)name, 7, 0, 0,
                      0, 0);
        if (rc == 0u) {
            ++created;
            continue;
        }
        if (rc == 12u) {
            saw_nospc = 1;
            break;
        }
        rt_exit(210);
    }
    if (!saw_nospc)
        rt_exit(211);
    if (created == 0u)
        rt_exit(212);
    /* One more create still refuses (sticky full, no wedging). */
    fname(name, 599u);
    if (rt_gate6(RT_SYS_FCREATE, (unsigned long long)name, 7, 0, 0, 0,
                 0) != 12u)
        rt_exit(213);
    /* Preexisting content intact (seeds: 64 bytes of index). */
    {
        unsigned long long n = SENT;
        unsigned long long k;
        if (rt_gate6(7u, (unsigned long long)"/w/s000", 7, 0,
                     (unsigned long long)buf, 64,
                     (unsigned long long)&n) != 0u || n != 64u)
            rt_exit(214);
        for (k = 0; k < 64u; ++k) {
            if (buf[k] != 0u)
                rt_exit(214);
        }
        n = SENT;
        if (rt_gate6(7u, (unsigned long long)"/w/last", 7, 0,
                     (unsigned long long)buf, 64,
                     (unsigned long long)&n) != 0u || n != 64u)
            rt_exit(215);
        for (k = 0; k < 64u; ++k) {
            if (buf[k] != 0xABu)
                rt_exit(215);
        }
    }
    /* A write to an old file still works and still allocates (data
       has room; only the directory is full): 64 + 536 crosses into
       a second block. */
    {
        unsigned long long n = SENT;
        stage(64, 536, 0xF1u);
        if (rt_gate6(RT_SYS_FWRITE, (unsigned long long)"/w/last", 7, 64,
                     (unsigned long long)buf, 536,
                     (unsigned long long)&n) != 0u || n != 536u)
            rt_exit(216);
        n = SENT;
        if (rt_gate6(7u, (unsigned long long)"/w/last", 7, 0,
                     (unsigned long long)buf, 600,
                     (unsigned long long)&n) != 0u || n != 600u)
            rt_exit(216);
        {
            unsigned long long k;
            for (k = 0; k < 64u; ++k) {
                if (buf[k] != 0xABu)
                    rt_exit(216);
            }
            for (k = 64u; k < 600u; ++k) {
                if (buf[k] != (unsigned char)((k * 13u + 0xF1u) & 0xffu))
                    rt_exit(216);
            }
        }
    }
    return 0;
}

static int disk_mode(void)
{
    unsigned long long n = SENT;
    unsigned long long off = 0;
    unsigned written = 0;
    int saw_nospc = 0;
    if (rt_gate6(RT_SYS_FCREATE, (unsigned long long)"/w/big", 6, 0, 0,
                 0, 0) != 0u)
        rt_exit(220);
    for (;;) {
        unsigned long long rc;
        stage(off, 4096, 0xB1u);
        n = SENT;
        rc = rt_gate6(RT_SYS_FWRITE, (unsigned long long)"/w/big", 6, off,
                      (unsigned long long)buf, 4096,
                      (unsigned long long)&n);
        if (rc == 0u) {
            if (n != 4096u)
                rt_exit(221);
            off += 4096u;
            ++written;
            if (written > 64u)
                rt_exit(222);
            continue;
        }
        if (rc == 12u && n == SENT) {
            saw_nospc = 1;
            break;
        }
        rt_exit(223);
    }
    if (!saw_nospc)
        rt_exit(224);
    if (written == 0u)
        rt_exit(225);
    /* Prior chunks intact (first chunk byte-exact). */
    {
        unsigned long long k;
        n = SENT;
        if (rt_gate6(7u, (unsigned long long)"/w/big", 6, 0,
                     (unsigned long long)buf, 4096,
                     (unsigned long long)&n) != 0u || n != 4096u)
            rt_exit(226);
        for (k = 0; k < 4096u; ++k) {
            if (buf[k] != (unsigned char)((k * 13u + 0xB1u) & 0xffu))
                rt_exit(226);
        }
    }
    /* One more chunk still refuses (sticky full, count untouched). */
    {
        unsigned long long rc;
        stage(off, 4096, 0xB1u);
        n = SENT;
        rc = rt_gate6(RT_SYS_FWRITE, (unsigned long long)"/w/big", 6, off,
                      (unsigned long long)buf, 4096,
                      (unsigned long long)&n);
        if (rc != 12u || n != SENT)
            rt_exit(227);
    }
    return 0;
}

int rt_main(int argc, char **argv)
{
    if (argc != 2 || argv == 0 || argv[0] == 0 || argv[1] == 0)
        rt_exit(209);
    if (streq(argv[1], "dir")) {
        if (dir_mode() != 0)
            rt_exit(218);
        rt_exit(0);
    }
    if (streq(argv[1], "disk")) {
        if (disk_mode() != 0)
            rt_exit(228);
        rt_exit(0);
    }
    rt_exit(209);
    return 0;
}
