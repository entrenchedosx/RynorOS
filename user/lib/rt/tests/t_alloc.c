/* Stage 18c conformance: bounded arena alloc/free.
 * Class 2: internal mismatch exits 66; success exits 0. Offsets (not
 * addresses) keep every number deterministic across builds.boots.
 */
#include "rt.h"

static void emit(const char *s)
{
    if (rt_print(s) != RT_OK)
        rt_exit(66);
}

/* Length-exact emit for rt_fmt output (never NUL-terminated). */
static void emit_n(const char *s, long long n)
{
    if (n < 0 || rt_print_bytes(s, (unsigned long long)n) != RT_OK)
        rt_exit(66);
}

static void check(int cond)
{
    if (!cond)
        rt_exit(66);
}

int rt_main(void)
{
    void *a1 = 0;
    void *a2 = 0;
    void *fill[16];
    char line[96];
    int i;
    check(rt_alloc(8u, 16u, &a1) == RT_OK);
    check(rt_alloc(8u, 32u, &a2) == RT_OK);
    check(rt_ptr_off(a1) == 0u);
    check(rt_ptr_off(a2) == 16u);
    check(rt_arena_watermark() == 48u);
    check(rt_live_count() == 2u);
    emit("[RT] alloc a1off=0 a2off=16 wm1=48 live=2\r\n");
    {
        enum rt_err fr1 = rt_free(a1);
        enum rt_err fr2 = rt_free(a2);
        check(fr1 == RT_OK && fr2 == RT_OK);
        check(rt_live_count() == 0u);
        check(rt_arena_watermark() == 48u);
        long long frc = rt_fmt(line, sizeof line,
                                "[RT] alloc fr1=%u fr2=%u live=0 wm2=48\r\n",
                                0u, 0u);
        if (frc != 38)
            rt_exit(66);
        emit_n(line, frc);
    }
    /* Fifteen 128-byte blocks from watermark 48 end exactly at 1968
       (48 + 15*128); a sixteenth would need 2096 > 2048. */
    for (i = 0; i < 15; ++i)
        check(rt_alloc(8u, 128u, &fill[i]) == RT_OK);
    check(rt_live_count() == 15u);
    check(rt_arena_watermark() == 1968u);
    emit("[RT] alloc fill live=15 wm3=1968\r\n");
    /* Free one slot first so the exhaustion below isolates the bump-end
       check (ledger space exists; only the arena end refuses). A mutant
       dropping the end check would succeed here and move the watermark:
       base 1968 + 81 = 2049 overflows the arena. */
    check(rt_free(fill[0]) == RT_OK);
    {
        void *nope = 0;
        check(rt_alloc(8u, 81u, &nope) == RT_NOMEM);
        check(nope == 0);
        check(rt_arena_watermark() == 1968u);
        check(rt_live_count() == 14u);
        emit("[RT] alloc nomem rc=5 wm4=1968 live=14\r\n");
    }
    check(rt_free(fill[0]) == RT_INVAL);
    for (i = 1; i < 15; ++i)
        check(rt_free(fill[i]) == RT_OK);
    check(rt_live_count() == 0u);
    emit("[RT] alloc drained live=0\r\n");
    {
        void *bogus = (void *)0x600100u;
        check(rt_alloc(3u, 8u, &bogus) == RT_INVAL);
        check(rt_alloc(8u, 4096u, &bogus) == RT_RANGE);
        check(rt_free((void *)0x600100u) == RT_INVAL);
        check(rt_free(a1) == RT_INVAL);
        emit("[RT] alloc badalign rc=1 oversize rc=2 badfree rc=1 dblfree rc=1\r\n");
    }
    return 0;
}
