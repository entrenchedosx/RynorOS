/* Slice C probe: aliased spawn arguments through the real syscall.
 * The argument-descriptor array overlaps the string bytes in the
 * caller's own memory; the kernel must copy descriptors once and then
 * strings from the staged copy (same static bytes either way). Success
 * is a normally-spawned grandchild: wait EXITED+42, then exit 0.
 * 130+ = violation class.
 */
#include "rt.h"

struct user_arg {
    unsigned long long ptr;
    unsigned long long len;
};

struct spawn_spec {
    unsigned long long path_ptr;
    unsigned long long path_len;
    unsigned long long args_ptr;
    unsigned int nargs;
    unsigned int stdin_sel;
    unsigned int stdout_sel;
    unsigned int stderr_sel;
    unsigned long long file_in;
    unsigned long long file_out;
    unsigned long long file_err;
    unsigned long long reserved[4];
};

struct proc_status {
    unsigned int state;
    unsigned int code;
    unsigned int detail;
    unsigned int reserved;
};

extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

static const char path[] = "/t/exit42.rnx";

int rt_main(void)
{
    /* One buffer: descriptors at +0, strings at +32 (the second
       descriptor's tail overlaps the first string bytes). */
    static unsigned char buf[64];
    struct spawn_spec spec;
    struct user_arg *vec = (struct user_arg *)buf;
    unsigned char *strs = buf + 32;
    unsigned long long h = 0;
    struct proc_status st;
    unsigned long long rc;
    unsigned int polls = 0;
    strs[0] = 'A';
    strs[1] = 'B';
    strs[2] = 0;
    strs[3] = 'C';
    strs[4] = 'D';
    strs[5] = 0;
    vec[0].ptr = (unsigned long long)(strs + 0);
    vec[0].len = 2;
    vec[1].ptr = (unsigned long long)(strs + 3);
    vec[1].len = 2;
    spec.path_ptr = (unsigned long long)path;
    spec.path_len = 13;
    spec.args_ptr = (unsigned long long)vec;
    spec.nargs = 2;
    spec.stdin_sel = 1;
    spec.stdout_sel = 0;
    spec.stderr_sel = 0;
    rc = rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                  0, 0, 0, 0);
    if (rc != 0)
        rt_exit(130);
    for (;;) {
        rc = rt_gate6(5, h, (unsigned long long)&st, 0, 0, 0, 0);
        if (rc != 0)
            rt_exit(131);
        if (st.state == 1)
            break;
        if (st.state != 0)
            rt_exit(132);
        if (++polls > 1000000)
            rt_exit(133);
        if (rt_nap(1) != RT_OK)
            rt_exit(134);
    }
    if (st.code != 42)
        rt_exit(135);
    rt_exit(0);
    return 0;
}
