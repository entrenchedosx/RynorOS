/* P1-A3 /bin/stat: argv[1] through the PUBLIC fstat gate (raw
 * gate: exact sys_err exits). Prints the path, the truthful type,
 * and the logical size; nothing else exists to report (no
 * timestamps/permissions/owners in this filesystem). Usage error
 * exits 66 (fput convention); file errors exit the raw sys_err.
 */
#include "rt.h"
#include "rt_fs.h"

static unsigned long long stat_slen(const char *s)
{
    unsigned long long n = 0;
    /* No silent truncation: over-long paths reach the kernel, which
       rejects them (the gate then reports failure). */
    while (n < 64 && s[n] != 0)
        ++n;
    return n;
}

static void stat_put(const char *s, unsigned long long n)
{
    if (rt_write(RT_FD_STDOUT, (const unsigned char *)s, n) != RT_OK)
        rt_exit(1);
}

static void stat_put_u64(unsigned long long v)
{
    char tmp[20];
    unsigned long long n = 0;
    unsigned long long i;
    if (v == 0) {
        stat_put("0", 1);
        return;
    }
    while (v > 0) {
        tmp[n] = (char)((unsigned long long)'0' + (v % 10));
        v /= 10;
        ++n;
    }
    for (i = 0; i < n / 2; ++i) {
        char t = tmp[i];
        tmp[i] = tmp[n - 1 - i];
        tmp[n - 1 - i] = t;
    }
    stat_put(tmp, n);
}

int rt_main(int argc, char **argv)
{
    static struct rt_stat st;
    unsigned long long rc;
    unsigned long long plen;
    if (argc < 2 || argv == 0 || argv[1] == 0)
        rt_exit(66);
    plen = stat_slen(argv[1]);
    rc = rt_gate6(RT_SYS_FSTAT, (unsigned long long)argv[1], plen,
                  (unsigned long long)&st, 0, 0, 0);
    if (rc != 0)
        rt_exit((int)rc);
    if ((st.type != RT_FTYPE_FILE && st.type != RT_FTYPE_DIR) ||
        st.reserved[0] != 0 || st.reserved[1] != 0)
        rt_exit(65);
    if (st.type == RT_FTYPE_DIR && st.size != 0)
        rt_exit(65);
    stat_put("path: ", 6);
    stat_put(argv[1], plen);
    stat_put("\n", 1);
    stat_put("type: ", 6);
    if (st.type == RT_FTYPE_FILE)
        stat_put("file\n", 5);
    else
        stat_put("dir\n", 4);
    stat_put("size: ", 6);
    stat_put_u64(st.size);
    stat_put("\n", 1);
    rt_exit(0);
    return 0;
}
