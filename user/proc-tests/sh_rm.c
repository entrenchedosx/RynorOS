/* P1-A3 /bin/rm: argv[1] through the PUBLIC unlink gate. Exit 0
 * or the raw sys_err (a second rm of the same path reports
 * NOTFOUND). Usage error exits 66 (fput convention). Directories
 * and root are refused by the kernel (a type refusal, never a
 * silent success).
 */
#include "rt.h"
#include "rt_fs.h"

static unsigned long long rm_slen(const char *s)
{
    unsigned long long n = 0;
    /* No silent truncation: over-long paths reach the kernel, which
       rejects them (the gate then reports failure). */
    while (n < 64 && s[n] != 0)
        ++n;
    return n;
}

int rt_main(int argc, char **argv)
{
    unsigned long long rc;
    if (argc < 2 || argv == 0 || argv[1] == 0)
        rt_exit(66);
    rc = rt_gate6(RT_SYS_UNLINK, (unsigned long long)argv[1],
                  rm_slen(argv[1]), 0, 0, 0, 0);
    rt_exit((int)rc);
    return 0;
}
