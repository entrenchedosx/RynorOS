/* Slice C probe: argv startup-block verification (frozen entry contract).
 *
 * Standard C signature (argc/argv forwarded by _start in rdi/rsi):
 * checks argc<=8, argv[argc]==NULL, per-string NUL termination inside a
 * 256-byte budget, then writes the concatenated argument bytes and
 * exits with argc. Violation classes exit 100+N (never success).
 */
#include "rt.h"

#define STACK_PAGE 0x7FF000ULL
#define STACK_TOP 0x800000ULL

int rt_main(int argc, char **argv)
{
    unsigned long long total = 0;
    int i;
    if (argc < 0 || argc > 8)
        rt_exit(103);
    if (argv[argc] != 0)
        rt_exit(104);
    for (i = 0; i < argc; ++i) {
        unsigned long long ptr = (unsigned long long)argv[i];
        unsigned long long len = 0;
        const char *s;
        if (ptr <= STACK_PAGE || ptr >= STACK_TOP)
            rt_exit(105);
        s = (const char *)ptr;
        while (len <= 256) {
            if (s[len] == 0)
                break;
            ++len;
        }
        if (len > 256)
            rt_exit(106);
        total += len;
        if (total > 256)
            rt_exit(107);
        if (rt_write(RT_FD_STDOUT, s, len) != RT_OK)
            rt_exit(108);
    }
    rt_exit(argc);
    return 0;
}
