/* P1-A3 CPL3 lifecycle probe: the stat/readdir/unlink matrix
 * through the real gate (syscalls 11/12/13), in-guest. Exit 0 when
 * every row matches; otherwise the failing row's code (201-249
 * fresh mode, 250-259 reboot mode). A row code covers its row plus
 * its trailing alive check.
 *
 * Fresh mode (first boot): stat matrix (fixed set, neg shapes,
 * hostile pointers, reserved words), self-file creates, readdir
 * walks (dense ordinals, END stability, hostile outputs, reserved
 * words), unlink negs + lifecycles (missing, zero-length, double
 * unlink, readdir-after-delete), delete/recreate reuse shape, and
 * the rt_fstat/rt_readdir/rt_unlink wrapper collapse checks.
 * Reboot mode (/w/u3 already exists): verify-only stat/walk/
 * readback, no writes.
 *
 * Mode detection is exact: a raw-gate stat of /w/u3 returning
 * NOTFOUND means fresh, OK means reboot; anything else exits 200.
 */
#include "rt.h"
#include "rt_pipe.h"
#include "rt_fs.h"

#define SENTB 0xA5
#define STACK_SPILL 0x7FFFF0ULL
#define KSPACE 0xffffff0000000000ULL

static unsigned char buf[2048];
static struct rt_stat st;
static struct rt_dirent de;

static unsigned long long slen(const char *s, unsigned long long cap)
{
    unsigned long long n = 0;
    while (n < cap && s[n] != 0)
        ++n;
    return n;
}

static void stage(unsigned long long off, unsigned long long len,
                  unsigned seed)
{
    unsigned long long i;
    for (i = 0; i < len; ++i)
        buf[i] = (unsigned char)(((off + i) * 13u + seed) & 0xffu);
}

static int name_eq(const unsigned char *a, const char *b)
{
    unsigned long long i;
    /* Short literals only: the loop returns at the first NUL or
       mismatch, so b is never read past its terminator. */
    for (i = 0; i < 40; ++i) {
        if (a[i] != (unsigned char)b[i])
            return 0;
        if (a[i] == 0)
            return 1;
    }
    return 0;
}

static unsigned long long gate_create(const char *path,
                                      unsigned long long plen,
                                      unsigned long long rdx)
{
    return rt_gate6(RT_SYS_FCREATE, (unsigned long long)path, plen, rdx,
                    0, 0, 0);
}

static unsigned long long gate_write(const char *path,
                                     unsigned long long plen,
                                     unsigned long long off,
                                     unsigned long long ubuf,
                                     unsigned long long len,
                                     unsigned long long out)
{
    return rt_gate6(RT_SYS_FWRITE, (unsigned long long)path, plen, off,
                    ubuf, len, out);
}

static unsigned long long gate_read(const char *path,
                                    unsigned long long plen,
                                    unsigned long long off,
                                    unsigned long long len,
                                    unsigned long long *n)
{
    *n = 0xAAAAAAAAAAAAAAAAULL;
    return rt_gate6(7u, (unsigned long long)path, plen, off,
                    (unsigned long long)buf, len, (unsigned long long)n);
}

static unsigned long long gate_stat(const char *path,
                                    unsigned long long plen,
                                    unsigned long long out,
                                    unsigned long long rsi)
{
    return rt_gate6(RT_SYS_FSTAT, (unsigned long long)path, plen, out,
                    rsi, 0, 0);
}

static unsigned long long gate_readdir(unsigned long long ord,
                                       unsigned long long out,
                                       unsigned long long rdx)
{
    return rt_gate6(RT_SYS_READDIR, ord, out, rdx, 0, 0, 0);
}

static unsigned long long gate_unlink(const char *path,
                                      unsigned long long plen,
                                      unsigned long long rdx)
{
    return rt_gate6(RT_SYS_UNLINK, (unsigned long long)path, plen, rdx,
                    0, 0, 0);
}

static void preset_stat(void)
{
    unsigned long long i;
    for (i = 0; i < sizeof(st); ++i)
        ((unsigned char *)&st)[i] = SENTB;
}

static int stat_clean(void)
{
    unsigned long long i;
    for (i = 0; i < sizeof(st); ++i)
        if (((unsigned char *)&st)[i] != SENTB)
            return 0;
    return 1;
}

static void preset_dirent(void)
{
    unsigned long long i;
    for (i = 0; i < sizeof(de); ++i)
        ((unsigned char *)&de)[i] = SENTB;
}

static int dirent_clean(void)
{
    unsigned long long i;
    for (i = 0; i < sizeof(de); ++i)
        if (((unsigned char *)&de)[i] != SENTB)
            return 0;
    return 1;
}

/* The kernel zero-pads the whole name field: NUL inside, zeros to
   the end, leading slash. Returns the name length. */
static unsigned long long check_name(int code)
{
    unsigned long long i;
    unsigned long long nul = 40;
    if (de.name[0] != (unsigned char)'/')
        rt_exit(code);
    for (i = 0; i < 40; ++i)
        if (de.name[i] == 0) {
            nul = i;
            break;
        }
    if (nul >= 40)
        rt_exit(code);
    for (i = nul; i < 40; ++i)
        if (de.name[i] != 0)
            rt_exit(code);
    return nul;
}

/* Alive check shared by hostile rows: a fixed file must still stat
   and the first ordinal must still enumerate. */
static void alive(int code)
{
    preset_stat();
    if (gate_stat("/one", slen("/one", 64), (unsigned long long)&st, 0) != 0u)
        rt_exit(code);
    preset_dirent();
    if (gate_readdir(0, (unsigned long long)&de, 0) != 0u)
        rt_exit(code);
}

int rt_main(int argc, char **argv)
{
    unsigned long long n = 0;
    unsigned long long i;
    unsigned long long base = 0;
    unsigned long long sa_seen;
    unsigned long long sb_seen;
    unsigned long long u3_seen;
    unsigned long long rc;
    (void)argc;
    (void)argv;
    /* --- mode prelude: /w/u3 exists only after a fresh run --- */
    preset_stat();
    rc = gate_stat("/w/u3", slen("/w/u3", 64), (unsigned long long)&st, 0);
    if (rc != 3u && rc != 0u)
        rt_exit(200);
    if (rc == 0u) {
        /* --- reboot mode: verify-only, no writes --- */
        if (st.type != 1u || st.size != 1500u || st.reserved[0] != 0u ||
            st.reserved[1] != 0u)
            rt_exit(250);
        preset_stat();
        if (gate_stat("/w/sa", slen("/w/sa", 64),
                      (unsigned long long)&st, 0) != 3u || !stat_clean())
            rt_exit(251);
        preset_stat();
        if (gate_stat("/w/sb", slen("/w/sb", 64),
                      (unsigned long long)&st, 0) != 3u || !stat_clean())
            rt_exit(251);
        preset_stat();
        if (gate_stat("/w/u1", slen("/w/u1", 64),
                      (unsigned long long)&st, 0) != 3u || !stat_clean())
            rt_exit(251);
        u3_seen = 0;
        for (i = 0;; ++i) {
            preset_dirent();
            rc = gate_readdir(i, (unsigned long long)&de, 0);
            if (rc == 13u) {
                if (!dirent_clean())
                    rt_exit(252);
                break;
            }
            if (rc != 0u)
                rt_exit(252);
            check_name(252);
            if (de.reserved != 0u)
                rt_exit(252);
            if (name_eq(de.name, "/w/u3")) {
                if (de.type != 1u || de.size != 1500u)
                    rt_exit(253);
                u3_seen = 1;
            }
            if (name_eq(de.name, "/w/sa") || name_eq(de.name, "/w/sb") ||
                name_eq(de.name, "/w/u1"))
                rt_exit(253);
            if (i > 600)
                rt_exit(252);
        }
        if (!u3_seen)
            rt_exit(253);
        if (gate_read("/w/u3", slen("/w/u3", 64), 0, 1500, &n) != 0u ||
            n != 1500u)
            rt_exit(254);
        for (i = 0; i < 1500u; ++i)
            if (buf[i] != (unsigned char)((i * 13u + 0x63u) & 0xffu))
                rt_exit(254);
        if (rt_fstat("/w/nope", slen("/w/nope", 64), &st) != RT_INVAL)
            rt_exit(255);
        rt_exit(0);
        return 0;
    }
    /* --- fresh mode --- */
    preset_stat();
    if (gate_stat("/one", slen("/one", 64), (unsigned long long)&st, 0) != 0u ||
        st.type != 1u || st.size != 2u || st.reserved[0] != 0u ||
        st.reserved[1] != 0u)
        rt_exit(201);
    preset_stat();
    if (gate_stat("/docs", slen("/docs", 64),
                  (unsigned long long)&st, 0) != 0u || st.type != 2u ||
        st.size != 0u || st.reserved[0] != 0u || st.reserved[1] != 0u)
        rt_exit(202);
    preset_stat();
    if (gate_stat("/", slen("/", 64), (unsigned long long)&st, 0) != 0u ||
        st.type != 2u || st.size != 0u)
        rt_exit(203);
    preset_stat();
    if (gate_stat("/missing", slen("/missing", 64),
                  (unsigned long long)&st, 0) != 3u || !stat_clean())
        rt_exit(204);
    preset_stat();
    if (gate_stat("/one/x", slen("/one/x", 64),
                  (unsigned long long)&st, 0) != 4u || !stat_clean())
        rt_exit(205);
    preset_stat();
    if (gate_stat("/a//b", slen("/a//b", 64),
                  (unsigned long long)&st, 0) != 8u || !stat_clean())
        rt_exit(206);
    preset_stat();
    if (gate_stat("", 0, (unsigned long long)&st, 0) != 8u || !stat_clean())
        rt_exit(207);
    preset_stat();
    if (gate_stat("/12345678901234567890123456789012", 33,
                  (unsigned long long)&st, 0) != 8u || !stat_clean())
        rt_exit(208);
    preset_stat();
    if (gate_stat("/one", slen("/one", 64), 0, 0) != 8u || !stat_clean())
        rt_exit(209);
    preset_stat();
    if (gate_stat("/one", slen("/one", 64), KSPACE, 0) != 8u ||
        !stat_clean())
        rt_exit(210);
    preset_stat();
    if (gate_stat("/one", slen("/one", 64), 0xFFFFFFFFFFFFFFF0ULL, 0) != 8u ||
        !stat_clean())
        rt_exit(211);
    preset_stat();
    if (gate_stat((const char *)0, 5, (unsigned long long)&st, 0) != 8u ||
        !stat_clean())
        rt_exit(212);
    alive(212);
    preset_stat();
    if (gate_stat((const char *)KSPACE, 5,
                  (unsigned long long)&st, 0) != 8u || !stat_clean())
        rt_exit(213);
    alive(213);
    preset_stat();
    if (gate_stat((const char *)0xFFFFFFFFFFFFFFFBULL, 16,
                  (unsigned long long)&st, 0) != 8u || !stat_clean())
        rt_exit(214);
    alive(214);
    preset_stat();
    if (gate_stat("/one", slen("/one", 64), (unsigned long long)&st, 1) != 2u ||
        !stat_clean())
        rt_exit(215);
    alive(215);
    /* Self files: sa (1500 bytes) + sb (empty). */
    if (gate_create("/w/sa", slen("/w/sa", 64), 0) != 0u)
        rt_exit(216);
    stage(0, 1500, 0x61u);
    if (gate_write("/w/sa", slen("/w/sa", 64), 0,
                   (unsigned long long)buf, 1500,
                   (unsigned long long)&n) != 0u || n != 1500u)
        rt_exit(216);
    preset_stat();
    if (gate_stat("/w/sa", slen("/w/sa", 64),
                  (unsigned long long)&st, 0) != 0u || st.type != 1u ||
        st.size != 1500u)
        rt_exit(216);
    if (gate_create("/w/sb", slen("/w/sb", 64), 0) != 0u)
        rt_exit(217);
    preset_stat();
    if (gate_stat("/w/sb", slen("/w/sb", 64),
                  (unsigned long long)&st, 0) != 0u || st.type != 1u ||
        st.size != 0u)
        rt_exit(217);
    /* Walk 1: dense ordinals, self files with sizes, END stable. */
    sa_seen = 0;
    sb_seen = 0;
    for (i = 0;; ++i) {
        preset_dirent();
        rc = gate_readdir(i, (unsigned long long)&de, 0);
        if (rc == 13u) {
            if (!dirent_clean())
                rt_exit(220);
            break;
        }
        if (rc != 0u)
            rt_exit(220);
        check_name(220);
        if (de.reserved != 0u)
            rt_exit(220);
        if (de.type != 1u && de.type != 2u)
            rt_exit(220);
        if (name_eq(de.name, "/w/sa")) {
            if (de.type != 1u || de.size != 1500u)
                rt_exit(221);
            sa_seen = 1;
        }
        if (name_eq(de.name, "/w/sb")) {
            if (de.type != 1u || de.size != 0u)
                rt_exit(221);
            sb_seen = 1;
        }
        if (i > 600)
            rt_exit(220);
    }
    if (!sa_seen || !sb_seen)
        rt_exit(221);
    base = i;
    preset_dirent();
    if (gate_readdir(base + 100, (unsigned long long)&de, 0) != 13u ||
        !dirent_clean())
        rt_exit(222);
    preset_dirent();
    if (gate_readdir(0xFFFFFFFFFFFFFFFFULL, (unsigned long long)&de, 0) != 13u ||
        !dirent_clean())
        rt_exit(223);
    preset_dirent();
    if (gate_readdir(0, 0, 0) != 8u || !dirent_clean())
        rt_exit(224);
    alive(224);
    preset_dirent();
    if (gate_readdir(0, KSPACE, 0) != 8u || !dirent_clean())
        rt_exit(225);
    alive(225);
    preset_dirent();
    if (gate_readdir(0, 0xFFFFFFFFFFFFFFC0ULL, 0) != 8u || !dirent_clean())
        rt_exit(226);
    alive(226);
    preset_dirent();
    if (gate_readdir(0, (unsigned long long)&de, 1) != 2u || !dirent_clean())
        rt_exit(227);
    alive(227);
    /* Unlink negs (nothing deleted yet). */
    if (gate_unlink("/missing", slen("/missing", 64), 0) != 3u)
        rt_exit(230);
    if (gate_unlink("/docs", slen("/docs", 64), 0) != 4u)
        rt_exit(231);
    if (gate_unlink("/", slen("/", 64), 0) != 4u)
        rt_exit(231);
    if (gate_unlink("/one/x", slen("/one/x", 64), 0) != 4u)
        rt_exit(232);
    if (gate_unlink("/a//b", slen("/a//b", 64), 0) != 8u)
        rt_exit(233);
    if (gate_unlink("", 0, 0) != 8u)
        rt_exit(234);
    if (gate_unlink("/12345678901234567890123456789012", 33, 0) != 8u)
        rt_exit(234);
    if (gate_unlink((const char *)0, 5, 0) != 8u)
        rt_exit(235);
    alive(235);
    if (gate_unlink((const char *)KSPACE, 5, 0) != 8u)
        rt_exit(235);
    alive(235);
    if (gate_unlink((const char *)0xFFFFFFFFFFFFFFFBULL, 16, 0) != 8u)
        rt_exit(235);
    alive(235);
    if (gate_unlink("/missing", slen("/missing", 64), 1) != 2u)
        rt_exit(236);
    alive(236);
    /* Lifecycle: u1 (100 bytes) created, deleted, re-delete refused. */
    if (gate_create("/w/u1", slen("/w/u1", 64), 0) != 0u)
        rt_exit(237);
    stage(0, 100, 0x62u);
    if (gate_write("/w/u1", slen("/w/u1", 64), 0,
                   (unsigned long long)buf, 100,
                   (unsigned long long)&n) != 0u || n != 100u)
        rt_exit(237);
    if (gate_unlink("/w/u1", slen("/w/u1", 64), 0) != 0u)
        rt_exit(237);
    preset_stat();
    if (gate_stat("/w/u1", slen("/w/u1", 64),
                  (unsigned long long)&st, 0) != 3u || !stat_clean())
        rt_exit(237);
    if (gate_unlink("/w/u1", slen("/w/u1", 64), 0) != 3u)
        rt_exit(237);
    /* Zero-length delete: sb goes, stat refuses after. */
    if (gate_unlink("/w/sb", slen("/w/sb", 64), 0) != 0u)
        rt_exit(238);
    preset_stat();
    if (gate_stat("/w/sb", slen("/w/sb", 64),
                  (unsigned long long)&st, 0) != 3u || !stat_clean())
        rt_exit(238);
    /* Walk 2: u1/sb absent, ordinals shifted, sa still sized. */
    sa_seen = 0;
    for (i = 0;; ++i) {
        preset_dirent();
        rc = gate_readdir(i, (unsigned long long)&de, 0);
        if (rc == 13u) {
            if (!dirent_clean())
                rt_exit(239);
            break;
        }
        if (rc != 0u)
            rt_exit(239);
        check_name(239);
        if (name_eq(de.name, "/w/u1") || name_eq(de.name, "/w/sb"))
            rt_exit(239);
        if (name_eq(de.name, "/w/sa")) {
            if (de.size != 1500u)
                rt_exit(239);
            sa_seen = 1;
        }
        if (i > 600)
            rt_exit(239);
    }
    if (!sa_seen || i != base - 1)
        rt_exit(239);
    /* Delete sa, recreate the same size as u3 (host proves the
       first-fit gap reuse from the disk image). */
    if (gate_unlink("/w/sa", slen("/w/sa", 64), 0) != 0u)
        rt_exit(240);
    if (gate_create("/w/u3", slen("/w/u3", 64), 0) != 0u)
        rt_exit(240);
    stage(0, 1500, 0x63u);
    if (gate_write("/w/u3", slen("/w/u3", 64), 0,
                   (unsigned long long)buf, 1500,
                   (unsigned long long)&n) != 0u || n != 1500u)
        rt_exit(240);
    preset_stat();
    if (gate_stat("/w/u3", slen("/w/u3", 64),
                  (unsigned long long)&st, 0) != 0u || st.type != 1u ||
        st.size != 1500u)
        rt_exit(240);
    /* Walk 3: u3 present, sa gone. */
    u3_seen = 0;
    for (i = 0;; ++i) {
        preset_dirent();
        rc = gate_readdir(i, (unsigned long long)&de, 0);
        if (rc == 13u) {
            if (!dirent_clean())
                rt_exit(240);
            break;
        }
        if (rc != 0u)
            rt_exit(240);
        check_name(240);
        if (name_eq(de.name, "/w/u3")) {
            if (de.type != 1u || de.size != 1500u)
                rt_exit(240);
            u3_seen = 1;
        }
        if (name_eq(de.name, "/w/sa"))
            rt_exit(240);
        if (i > 600)
            rt_exit(240);
    }
    if (!u3_seen || i != base - 1)
        rt_exit(240);
    /* Wrapper collapse checks (rt_fs.h under test). */
    if (rt_fstat("/w/u3", slen("/w/u3", 64), &st) != RT_OK)
        rt_exit(241);
    if (rt_fstat("/w/nope", slen("/w/nope", 64), &st) != RT_INVAL)
        rt_exit(241);
    if (rt_fstat("/w/u3", 0, &st) != RT_INVAL)
        rt_exit(241);
    if (rt_readdir(0, &de) != RT_OK)
        rt_exit(242);
    if (rt_readdir(base + 100, &de) != RT_INVAL)
        rt_exit(242);
    if (rt_unlink("/w/nope", slen("/w/nope", 64)) != RT_INVAL)
        rt_exit(243);
    if (rt_unlink("/w/u3", 0) != RT_INVAL)
        rt_exit(243);
    if (rt_unlink("/w/u3", slen("/w/u3", 64)) != RT_OK)
        rt_exit(244);
    preset_stat();
    if (gate_stat("/w/u3", slen("/w/u3", 64),
                  (unsigned long long)&st, 0) != 3u || !stat_clean())
        rt_exit(244);
    if (gate_create("/w/u3", slen("/w/u3", 64), 0) != 0u)
        rt_exit(244);
    stage(0, 1500, 0x63u);
    if (gate_write("/w/u3", slen("/w/u3", 64), 0,
                   (unsigned long long)buf, 1500,
                   (unsigned long long)&n) != 0u || n != 1500u)
        rt_exit(244);
    /* Final readback of the recreated bytes. */
    if (gate_read("/w/u3", slen("/w/u3", 64), 0, 1500, &n) != 0u ||
        n != 1500u)
        rt_exit(245);
    for (i = 0; i < 1500u; ++i)
        if (buf[i] != (unsigned char)((i * 13u + 0x63u) & 0xffu))
            rt_exit(245);
    rt_exit(0);
    return 0;
}
