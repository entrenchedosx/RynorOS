/* Slice C probe: CLOSED stdin reads EOF (OK + nread 0). Spawned with
 * STDIN_CLOSED; any other outcome is a violation class. */
#include "rt.h"

int rt_main(void)
{
    char buf[16];
    unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
    unsigned int i;
    for (i = 0; i < sizeof(buf); ++i)
        buf[i] = (char)0x5a;
    if (rt_fd_read(RT_FD_STDIN, buf, sizeof(buf), &n, 0) != RT_OK)
        rt_exit(160);
    if (n != 0)
        rt_exit(161);
    for (i = 0; i < sizeof(buf); ++i)
        if (buf[i] != (char)0x5a)
            rt_exit(162);
    rt_exit(0);
    return 0;
}
