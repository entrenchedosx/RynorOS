/* P1-A3 /bin/ls: enumerate the directory through the PUBLIC
 * readdir gate and print one absolute path per line (slot order).
 * Raw gate (not the collapsing wrapper): end-of-directory must be
 * distinguished from real errors. Bounded loop: past a sane bound
 * without END is a kernel bug (exit 65). Any readdir error exits
 * its raw sys_err so tests observe the exact reason.
 */
#include "rt.h"
#include "rt_fs.h"

int rt_main(int argc, char **argv)
{
    static struct rt_dirent de;
    unsigned long long ord = 0;
    (void)argc;
    (void)argv;
    for (;;) {
        unsigned long long rc;
        unsigned long long n = 0;
        if (ord > 600)
            rt_exit(65);
        rc = rt_gate6(RT_SYS_READDIR, ord, (unsigned long long)&de,
                      0, 0, 0, 0);
        if (rc == RT_SYS_END)
            break;
        if (rc != 0)
            rt_exit((int)rc);
        while (n < sizeof(de.name) && de.name[n] != 0)
            ++n;
        if (n == 0 || n >= sizeof(de.name))
            rt_exit(65);
        if (de.type != RT_FTYPE_FILE && de.type != RT_FTYPE_DIR)
            rt_exit(65);
        if (de.reserved != 0)
            rt_exit(65);
        /* Overwrite the NUL with the line break (n <= 39: in bounds)
           and emit name + newline as one write. */
        de.name[n] = (unsigned char)'\n';
        if (rt_write(RT_FD_STDOUT, de.name, n + 1) != RT_OK)
            rt_exit(1);
        ++ord;
    }
    rt_exit(0);
    return 0;
}
