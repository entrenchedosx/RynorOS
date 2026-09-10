/* Slice E helper: relay fd0 to fd1 uppercasing ASCII a-z (pipe
 * transform stage: sequential-execution mutants show lowercase). */
#include "rt.h"
#include "rt_pipe.h"

int rt_main(void)
{
    static unsigned char buf[1024];
    unsigned long long retries = 0, k;
    for (;;) {
        unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
        enum rt_err rc = rt_pipe_read_once(buf, sizeof(buf), &n);
        if (rc == RT_AGAIN) {
            if (++retries > RT_PIPE_RETRY_MAX)
                rt_exit(14);
            if (rt_nap(1) != RT_OK)
                rt_exit(16);
            continue;
        }
        if (rc != RT_OK)
            rt_exit(13);
        if (n == 0)
            break;
        for (k = 0; k < n; ++k)
            if (buf[k] >= 'a' && buf[k] <= 'z')
                buf[k] = (unsigned char)(buf[k] - 'a' + 'A');
        if (rt_pipe_write_all(RT_FD_STDOUT, buf, n) != RT_OK)
            rt_exit(15);
    }
    rt_exit(0);
    return 0;
}
