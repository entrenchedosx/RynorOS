/* Slice E helper: echo argv[1..] joined by single spaces plus a
 * trailing newline. No output (just newline) for zero args. */
#include "rt.h"

int rt_main(int argc, char **argv)
{
    int i;
    if (argc < 0 || argc > 8)
        rt_exit(70);
    /* Classic echo: argv[1..] joined by single spaces (argv[0] is the
       program name, never printed). */
    for (i = 1; i < argc; ++i) {
        unsigned long long len = 0;
        if (i > 1) {
            if (rt_write(RT_FD_STDOUT, " ", 1) != RT_OK)
                rt_exit(71);
        }
        while (len < 256 && argv[i][len] != 0)
            ++len;
        if (len > 0 && rt_write(RT_FD_STDOUT, argv[i], len) != RT_OK)
            rt_exit(71);
    }
    if (rt_write(RT_FD_STDOUT, "\n", 1) != RT_OK)
        rt_exit(71);
    rt_exit(0);
    return 0;
}
