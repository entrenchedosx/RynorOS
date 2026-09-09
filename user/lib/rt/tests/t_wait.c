/* Stage 18c conformance: cooperative flag wait (bounded, single ctx).
 * Class 5: internal mismatch exits 69; success exits 0.
 */
#include "rt.h"

static unsigned long long flag;

static void emit(const char *s)
{
    if (rt_print(s) != RT_OK)
        rt_exit(69);
}

static void check(int cond)
{
    if (!cond)
        rt_exit(69);
}

int rt_main(void)
{
    check(flag == 0u);
    check(rt_wait_flag(&flag, 2u) == RT_AGAIN);
    emit("[RT] wait again rc=4\r\n");
    check(rt_set_flag(&flag) == RT_OK);
    check(rt_wait_flag(&flag, 0u) == RT_OK);
    emit("[RT] wait ok rc=0\r\n");
    return 0;
}
