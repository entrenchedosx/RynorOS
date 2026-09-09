/* Stage 18c conformance: write/print validation classes.
 * Class 3: internal mismatch exits 67; success exits 0. Rejections must
 * return before any syscall, so no kernel row exists for them.
 */
#include "rt.h"

static void emit(const char *s)
{
    if (rt_print(s) != RT_OK)
        rt_exit(67);
}

static void check(int cond)
{
    if (!cond)
        rt_exit(67);
}

int rt_main(void)
{
    check(rt_print("hello") == RT_OK);
    emit("[RT] write hello rc=0\r\n");
    check(rt_write(2u, "hi", 2u) == RT_INVAL);
    check(rt_write(1u, "hi", 5000u) == RT_RANGE);
    emit("[RT] write badfd rc=1 overlen rc=2\r\n");
    check(rt_arena_watermark() == 0u);
    check(rt_live_count() == 0u);
    emit("[RT] write wm0=0 live=0\r\n");
    return 0;
}
