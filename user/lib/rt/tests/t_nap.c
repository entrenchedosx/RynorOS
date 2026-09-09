/* Stage 18c conformance: bounded nap (yield-count, never wall-clock).
 * Class 4: internal mismatch exits 68; success exits 0.
 */
#include "rt.h"

static void emit(const char *s)
{
    if (rt_print(s) != RT_OK)
        rt_exit(68);
}

static void check(int cond)
{
    if (!cond)
        rt_exit(68);
}

int rt_main(void)
{
    check(rt_nap(3u) == RT_OK);
    emit("[RT] nap ok rc=0\r\n");
    check(rt_nap(65u) == RT_RANGE);
    emit("[RT] nap over rc=2\r\n");
    return 0;
}
