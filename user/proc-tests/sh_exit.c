/* Slice E helper: exit with argv[1] as the decimal code (status
 * tests, nonzero/fault-free failures, pipeline side outcomes).
 * Unix argv: argv[0] is the command name per the frozen spawn
 * convention, so exactly two words are required. */
#include "rt.h"

int rt_main(int argc, char **argv)
{
    unsigned long long v = 0;
    unsigned int digits = 0;
    const char *s;
    if (argc != 2)
        rt_exit(70);
    s = argv[1];
    while (*s) {
        if (*s < '0' || *s > '9')
            rt_exit(71);
        v = v * 10u + (unsigned long long)(unsigned int)(*s - '0');
        if (v > 0xffffffffu)
            rt_exit(71);
        ++digits;
        ++s;
    }
    if (!digits)
        rt_exit(71);
    rt_exit((int)(v & 0xffffffffu));
    return 0;
}
