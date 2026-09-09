/* Stage 18c conformance: honest NOSYS stubs.
 * Class 6: internal mismatch exits 70; success exits 0.
 */
#include "rt.h"

static void emit(const char *s)
{
    if (rt_print(s) != RT_OK)
        rt_exit(70);
}

static void check(int cond)
{
    if (!cond)
        rt_exit(70);
}

int rt_main(void)
{
    check(rt_open("/x") == RT_NOSYS);
    check(rt_read(0, (void *)0x600000u, 8u) == RT_NOSYS);
    emit("[RT] nosys open rc=3 read rc=3\r\n");
    return 0;
}
