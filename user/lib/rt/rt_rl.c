/* Stage 18c RIR print rebind: the compiler-emitted rt_print_* helpers,
 * implemented through the library (rt_write) instead of raw gate calls.
 * This object replaces rt_rynor.asm's print helpers when the toolchain
 * selects runtime="rtlib"; it contains no gate instruction and no Linux
 * calls. Rendering matches the oracle and the direct runtime exactly:
 * signed decimal, "true"/"false", raw bytes, no newline.
 */
#include "rt.h"

int rl_12_rt_print_int(long long v)
{
    char buf[24];
    unsigned int n = 0;
    unsigned long long mag;
    int neg = v < 0;
    char rev[20];
    unsigned int nd = 0;
    if (neg)
        mag = (unsigned long long)(-(v + 1)) + 1u;
    else
        mag = (unsigned long long)v;
    do {
        rev[nd++] = (char)('0' + mag % 10u);
        mag /= 10u;
    } while (mag);
    if (neg)
        buf[n++] = '-';
    while (nd)
        buf[n++] = rev[--nd];
    if (rt_write(RT_FD_STDOUT, buf, n) != RT_OK)
        return -1;
    return (int)n;
}

int rl_13_rt_print_bool(long long v)
{
    const char *s = v ? "true" : "false";
    unsigned long long n = v ? 4u : 5u;
    if (rt_write(RT_FD_STDOUT, s, n) != RT_OK)
        return -1;
    return (int)n;
}

int rl_12_rt_print_str(const char *s, unsigned long long n)
{
    if (rt_write(RT_FD_STDOUT, s, n) != RT_OK)
        return -1;
    return (int)n;
}
