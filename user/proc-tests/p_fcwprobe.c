/* P1-A2 CPL3 syscall probe: the full fcreate/fwrite matrix through
 * the real gate (syscalls 9/10), in-guest. Exit 0 when every row
 * matches; otherwise the failing row's code (140-199 fresh mode,
 * 200-209 reboot mode). A row code covers its row plus its trailing
 * alive check (a kernel that survived returns precise codes; a
 * wedged one cannot fake them).
 *
 * Fresh mode (first boot): create matrix (boundaries, hostile
 * pointers, reserved words, nested paths), write matrix (growth,
 * overwrite, holes, caps, hostile buffers), binary content,
 * block-boundary crossing, multi-write growth, A/B/C relocation with
 * prefix/neighbor preservation, and the rt_fcreate/rt_fwrite wrapper
 * collapse checks. Reboot mode (the files already exist): verify-only
 * readback of every created byte.
 *
 * Mode detection is exact: a raw-gate fread of /w/a returning
 * NOTFOUND means fresh, OK means reboot; anything else exits 140.
 */
#include "rt.h"
#include "rt_pipe.h"
#include "rt_fs.h"

#define SENT 0xAAAAAAAAAAAAAAAAULL
#define STACK_SPILL 0x7FFFF0ULL
#define KSPACE 0xffffff0000000000ULL

/* 16 KiB staging (v2 envelope): the max-batch row issues one 16384
   byte call, proving the kernel's 4 KiB chunk loop end to end. */
static unsigned char buf[16384];

/* All honest rows size their paths with slen (miscounted literals
   once cost a debug cycle); only the deliberate wrong-length rows
   pass numeric lengths. */
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

static void stagebin(void)
{
    unsigned long long i;
    /* (i*7+3)&0xFF hits 0x00/0x01/0x7F/0x80/0xFF at i =
       219/146/164/91/36 (7 is invertible mod 256). */
    for (i = 0; i < 256; ++i)
        buf[i] = (unsigned char)((i * 7u + 3u) & 0xffu);
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
    *n = SENT;
    return rt_gate6(7u, (unsigned long long)path, plen, off,
                    (unsigned long long)buf, len, (unsigned long long)n);
}

/* Alive check shared by hostile rows: the oldest file must still
   collide (proves the fs + gate path survived the hostile call). */
static void alive(int code)
{
    if (gate_create("/w/a", slen("/w/a", 64), 0) != 11u)
        rt_exit(code);
    {
        unsigned long long n = SENT;
        if (gate_read("/w/a", slen("/w/a", 64), 0, 0, &n) != 0u || n != 0u)
            rt_exit(code);
    }
}

/* Formula checks for each file (absolute-offset patterns). */
static int check_a(void)
{
    unsigned long long n = SENT;
    unsigned long long i;
    if (gate_read("/w/a", slen("/w/a", 64), 0, 1550, &n) != 0u || n != 1550u)
        return 0;
    for (i = 0; i < 1550u; ++i) {
        unsigned seed = i < 100u ? 0xA2u : i < 1450u ? 0xA1u : 0xA3u;
        if (buf[i] != (unsigned char)((i * 13u + seed) & 0xffu))
            return 0;
    }
    return 1;
}

static int check_b16k(void)
{
    unsigned long long off;
    for (off = 0; off < 16384u; off += 2048u) {
        unsigned long long n = SENT;
        unsigned long long i;
        if (gate_read("/w/b", slen("/w/b", 64), off, 2048, &n) != 0u || n != 2048u)
            return 0;
        for (i = 0; i < 2048u; ++i) {
            if (buf[i] != (unsigned char)(((off + i) * 13u + 0xB0u) & 0xffu))
                return 0;
        }
    }
    return 1;
}

static int check_c(void)
{
    unsigned long long n = SENT;
    unsigned long long i;
    if (gate_read("/w/c", slen("/w/c", 64), 0, 1000, &n) != 0u || n != 1000u)
        return 0;
    for (i = 0; i < 1000u; ++i) {
        unsigned seed = i < 400u ? 0xC1u : 0xC2u;
        if (buf[i] != (unsigned char)((i * 13u + seed) & 0xffu))
            return 0;
    }
    if (gate_read("/w/c", slen("/w/c", 64), 1000, 256, &n) != 0u || n != 256u)
        return 0;
    for (i = 0; i < 256u; ++i) {
        if (buf[i] != (unsigned char)((i * 7u + 3u) & 0xffu))
            return 0;
    }
    return 1;
}

static int check_abc(int grown)
{
    unsigned long long n = SENT;
    unsigned long long i;
    if (gate_read("/w/ra", slen("/w/ra", 64), 0, 1500, &n) != 0u || n != 1500u)
        return 0;
    for (i = 0; i < 1500u; ++i) {
        if (buf[i] != (unsigned char)((i * 13u + 0xD1u) & 0xffu))
            return 0;
    }
    if (gate_read("/w/rc", slen("/w/rc", 64), 0, 1500, &n) != 0u || n != 1500u)
        return 0;
    for (i = 0; i < 1500u; ++i) {
        if (buf[i] != (unsigned char)((i * 13u + 0xD4u) & 0xffu))
            return 0;
    }
    if (!grown)
        return 1;
    if (gate_read("/w/rb", slen("/w/rb", 64), 0, 2048, &n) != 0u || n != 2048u)
        return 0;
    for (i = 0; i < 2048u; ++i) {
        unsigned seed = i < 200u ? 0xD2u : 0xD3u;
        if (grown == 2 && i >= 100u && i < 200u)
            seed = 0xD5u;
        if (buf[i] != (unsigned char)((i * 13u + seed) & 0xffu))
            return 0;
    }
    if (gate_read("/w/rb", slen("/w/rb", 64), 2048, 152, &n) != 0u || n != 152u)
        return 0;
    for (i = 0; i < 152u; ++i) {
        if (buf[i] != (unsigned char)(((2048u + i) * 13u + 0xD3u) & 0xffu))
            return 0;
    }
    return 1;
}

int rt_main(void)
{
    unsigned long long n = SENT;
    unsigned long long rc;
    /* Fresh vs reboot: /w/a absent means first boot. */
    rc = gate_read("/w/a", slen("/w/a", 64), 0, 1, &n);
    if (rc != 0u && rc != 3u)
        rt_exit(140);
    if (rc == 0u) {
        if (!check_a())
            rt_exit(200);
        if (!check_b16k())
            rt_exit(201);
        if (!check_c())
            rt_exit(202);
        if (!check_abc(2))
            rt_exit(203);
        {
            /* Nested + wrapper-created files persist too. */
            unsigned long long m = SENT;
            unsigned long long i;
            if (gate_read("/test/w-nested", slen("/test/w-nested", 64), 0, 100, &m) != 0u ||
                m != 100u)
                rt_exit(204);
            for (i = 0; i < 100u; ++i) {
                if (buf[i] != (unsigned char)((i * 13u + 0xE0u) & 0xffu))
                    rt_exit(204);
            }
            if (gate_read("/w/w1", slen("/w/w1", 64), 0, 10, &m) != 0u || m != 10u)
                rt_exit(204);
            for (i = 0; i < 10u; ++i) {
                if (buf[i] != (unsigned char)((i * 13u + 0xE1u) & 0xffu))
                    rt_exit(204);
            }
        }
        rt_exit(0);
    }
    /* --- create matrix --- */
    if (gate_create("/w/a", slen("/w/a", 64), 0) != 0u)
        rt_exit(141);
    if (gate_create("/w/a", slen("/w/a", 64), 0) != 11u)
        rt_exit(142);
    if (gate_create("/w/b", slen("/w/b", 64), 0) != 0u)
        rt_exit(143);
    if (gate_create("/docs", slen("/docs", 64), 0) != 11u)
        rt_exit(144);
    if (gate_create("/", slen("/", 64), 0) != 11u)
        rt_exit(145);
    if (gate_create("", 0, 0) != 8u)
        rt_exit(146);
    if (gate_create("/a//b", slen("/a//b", 64), 0) != 8u)
        rt_exit(147);
    if (gate_create("/12345678901234567890123456789012", 33, 0) != 8u)
        rt_exit(148);
    if (gate_create("/1234567890123456789012345678901", slen("/1234567890123456789012345678901", 64), 0) != 0u)
        rt_exit(149);
    if (gate_create("/123456789012345678901234567890", slen("/123456789012345678901234567890", 64), 0) != 0u)
        rt_exit(150);
    if (gate_create("/no/such/f", slen("/no/such/f", 64), 0) != 3u)
        rt_exit(151);
    if (gate_create("/w/a/x", slen("/w/a/x", 64), 0) != 4u)
        rt_exit(152);
    if (gate_create("/test/w-nested", slen("/test/w-nested", 64), 0) != 0u)
        rt_exit(153);
    stage(0, 100, 0xE0u);
    n = SENT;
    if (gate_write("/test/w-nested", slen("/test/w-nested", 64), 0, (unsigned long long)buf, 100,
                   (unsigned long long)&n) != 0u || n != 100u)
        rt_exit(153);
    /* Hostile create pointers (each followed by an alive check). */
    if (gate_create((const char *)0, 5, 0) != 8u)
        rt_exit(154);
    alive(154);
    if (gate_create((const char *)0x500000ULL, 5, 0) != 8u)
        rt_exit(155);
    alive(155);
    if (gate_create((const char *)KSPACE, 5, 0) != 8u)
        rt_exit(156);
    alive(156);
    if (gate_create((const char *)0xFFFFFFFFFFFFFFFBULL, 16, 0) != 8u)
        rt_exit(157);
    alive(157);
    if (gate_create((const char *)STACK_SPILL, 32, 0) != 8u)
        rt_exit(158);
    alive(158);
    if (gate_create("/w/zz", slen("/w/zz", 64), 1) != 2u)
        rt_exit(159);
    alive(159);
    if (gate_create("/w/c", slen("/w/c", 64), 0) != 0u)
        rt_exit(160);
    /* --- write matrix on /w/a --- */
    stage(0, 1500, 0xA1u);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, (unsigned long long)buf, 1500,
                   (unsigned long long)&n) != 0u || n != 1500u)
        rt_exit(161);
    stage(1450, 100, 0xA3u);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 1450, (unsigned long long)buf, 100,
                   (unsigned long long)&n) != 0u || n != 100u)
        rt_exit(162);
    stage(0, 100, 0xA2u);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, (unsigned long long)buf, 100,
                   (unsigned long long)&n) != 0u || n != 100u)
        rt_exit(163);
    if (!check_a())
        rt_exit(164);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 1550, (unsigned long long)buf, 0,
                   (unsigned long long)&n) != 0u || n != 0u)
        rt_exit(165);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 1551, (unsigned long long)buf, 10,
                   (unsigned long long)&n) != 8u || n != SENT)
        rt_exit(166);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 1551, (unsigned long long)buf, 0,
                   (unsigned long long)&n) != 8u || n != SENT)
        rt_exit(167);
    n = SENT;
    if (gate_write("/w/nope", slen("/w/nope", 64), 0, (unsigned long long)buf, 10,
                   (unsigned long long)&n) != 3u || n != SENT)
        rt_exit(168);
    n = SENT;
    if (gate_write("/docs", slen("/docs", 64), 0, (unsigned long long)buf, 10,
                   (unsigned long long)&n) != 4u || n != SENT)
        rt_exit(169);
    n = SENT;
    if (gate_write("", 0, 0, (unsigned long long)buf, 10,
                   (unsigned long long)&n) != 8u || n != SENT)
        rt_exit(170);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, (unsigned long long)buf, 16385,
                   (unsigned long long)&n) != 2u || n != SENT)
        rt_exit(171);
    alive(171);
    /* Max batch: 16384 bytes in one call (4 kernel chunks). */
    stage(0, 16384, 0xB0u);
    n = SENT;
    if (gate_write("/w/b", slen("/w/b", 64), 0, (unsigned long long)buf, 16384,
                   (unsigned long long)&n) != 0u || n != 16384u)
        rt_exit(172);
    if (!check_b16k())
        rt_exit(173);
    /* Hostile write pointers (each followed by an alive check). */
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, 0, 10, (unsigned long long)&n) != 8u ||
        n != SENT)
        rt_exit(174);
    alive(174);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, 0x500000ULL, 10,
                   (unsigned long long)&n) != 8u || n != SENT)
        rt_exit(175);
    alive(175);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, 0xFFFFFFFFFFFFFFFBULL, 16,
                   (unsigned long long)&n) != 8u || n != SENT)
        rt_exit(176);
    alive(176);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, STACK_SPILL, 64,
                   (unsigned long long)&n) != 8u || n != SENT)
        rt_exit(177);
    alive(177);
    if (gate_write("/w/a", slen("/w/a", 64), 0, (unsigned long long)buf, 10,
                   0x400000ULL) != 8u)
        rt_exit(178);
    alive(178);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0, (unsigned long long)buf, 10,
                   0x500000ULL) != 8u || n != SENT)
        rt_exit(179);
    alive(179);
    n = SENT;
    if (gate_write("/w/a", slen("/w/a", 64), 0xFFFFFFFFFFFFFFFFULL,
                   (unsigned long long)buf, 10,
                   (unsigned long long)&n) != 8u || n != SENT)
        rt_exit(180);
    alive(180);
    /* Block-boundary crossing + binary content on /w/c. */
    stage(0, 400, 0xC1u);
    n = SENT;
    if (gate_write("/w/c", slen("/w/c", 64), 0, (unsigned long long)buf, 400,
                   (unsigned long long)&n) != 0u || n != 400u)
        rt_exit(181);
    stage(400, 600, 0xC2u);
    n = SENT;
    if (gate_write("/w/c", slen("/w/c", 64), 400, (unsigned long long)buf, 600,
                   (unsigned long long)&n) != 0u || n != 600u)
        rt_exit(182);
    stagebin();
    n = SENT;
    if (gate_write("/w/c", slen("/w/c", 64), 1000, (unsigned long long)buf, 256,
                   (unsigned long long)&n) != 0u || n != 256u)
        rt_exit(183);
    if (!check_c())
        rt_exit(184);
    /* A/B/C relocation through the gate. */
    if (gate_create("/w/ra", slen("/w/ra", 64), 0) != 0u)
        rt_exit(185);
    if (gate_create("/w/rb", slen("/w/rb", 64), 0) != 0u)
        rt_exit(185);
    if (gate_create("/w/rc", slen("/w/rc", 64), 0) != 0u)
        rt_exit(185);
    stage(0, 1500, 0xD1u);
    n = SENT;
    if (gate_write("/w/ra", slen("/w/ra", 64), 0, (unsigned long long)buf, 1500,
                   (unsigned long long)&n) != 0u || n != 1500u)
        rt_exit(186);
    stage(0, 200, 0xD2u);
    n = SENT;
    if (gate_write("/w/rb", slen("/w/rb", 64), 0, (unsigned long long)buf, 200,
                   (unsigned long long)&n) != 0u || n != 200u)
        rt_exit(186);
    stage(0, 1500, 0xD4u);
    n = SENT;
    if (gate_write("/w/rc", slen("/w/rc", 64), 0, (unsigned long long)buf, 1500,
                   (unsigned long long)&n) != 0u || n != 1500u)
        rt_exit(186);
    if (!check_abc(0))
        rt_exit(187);
    stage(200, 2000, 0xD3u);
    n = SENT;
    if (gate_write("/w/rb", slen("/w/rb", 64), 200, (unsigned long long)buf, 2000,
                   (unsigned long long)&n) != 0u || n != 2000u)
        rt_exit(188);
    if (!check_abc(1))
        rt_exit(189);
    stage(100, 100, 0xD5u);
    n = SENT;
    if (gate_write("/w/rb", slen("/w/rb", 64), 100, (unsigned long long)buf, 100,
                   (unsigned long long)&n) != 0u || n != 100u)
        rt_exit(190);
    {
        /* Post-relocation overwrite: neighbors intact, B patched. */
        unsigned long long m = SENT;
        unsigned long long i;
        if (gate_read("/w/ra", slen("/w/ra", 64), 0, 1500, &m) != 0u || m != 1500u)
            rt_exit(191);
        if (gate_read("/w/rb", slen("/w/rb", 64), 0, 2200, &m) != 0u || m != 2200u)
            rt_exit(191);
        for (i = 0; i < 2200u; ++i) {
            unsigned seed = i < 100u ? 0xD2u : i < 200u ? 0xD5u : 0xD3u;
            if (buf[i] != (unsigned char)((i * 13u + seed) & 0xffu))
                rt_exit(191);
        }
    }
    /* Wrapper collapse checks (rt_fs.h under test). */
    if (rt_fcreate("/w/w1", slen("/w/w1", 64)) != RT_OK)
        rt_exit(192);
    if (rt_fcreate("/w/w1", slen("/w/w1", 64)) != RT_INVAL)
        rt_exit(192);
    stage(0, 10, 0xE1u);
    n = SENT;
    if (rt_fwrite("/w/w1", slen("/w/w1", 64), 0, buf, 10, &n) != RT_OK || n != 10u)
        rt_exit(193);
    n = SENT;
    if (rt_fwrite("/w/nope", slen("/w/nope", 64), 0, buf, 10, &n) != RT_INVAL || n != SENT)
        rt_exit(193);
    n = SENT;
    if (rt_fwrite("/w/w1", slen("/w/w1", 64), 0, buf, 16385, &n) != RT_RANGE || n != SENT)
        rt_exit(194);
    if (rt_fcreate("/w/w2", 0) != RT_INVAL)
        rt_exit(195);
    if (rt_fwrite("/w/w1", slen("/w/w1", 64), 0, 0, 10, &n) != RT_INVAL)
        rt_exit(195);
    n = SENT;
    if (gate_read("/w/a", slen("/w/a", 64), 0, 0, &n) != 0u || n != 0u)
        rt_exit(196);
    rt_exit(0);
    return 0;
}
