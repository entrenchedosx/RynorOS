/* Slice C probe: nested spawn/wait/terminate through the real syscalls.
 * Spawns /t/exit42.rnx absolutely (no discovery), polls wait to EXITED,
 * checks code 42, then proves consume-once (second wait BADHANDLE) and
 * terminate-after-natural-exit (ALREADY_GONE). Exit 0 = chain green;
 * 110+ = violation class. Uses rt_gate6 directly (audited frozen
 * register file); no rt wrappers exist for 4-6 in Slice C by design.
 */
#include "rt.h"

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
    static struct spawn_spec spec;
    unsigned long long h = 0xAAAAAAAAAAAAAAAAULL;
    struct proc_status st;
    unsigned long long rc;
    unsigned int polls = 0;
    _Static_assert(sizeof(struct spawn_spec) == 96, "spec layout");
    _Static_assert(sizeof(struct proc_status) == 16, "status layout");
    spec.path_ptr = (unsigned long long)path;
    spec.path_len = 13;
    spec.args_ptr = 0;
    spec.nargs = 0;
    spec.stdin_sel = 1;
    spec.stdout_sel = 0;
    spec.stderr_sel = 0;
    rc = rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                  0, 0, 0, 0);
    if (rc != 0)
        rt_exit(110);
    if (h == 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(111);
    for (;;) {
        st.state = 99;
        rc = rt_gate6(5, h, (unsigned long long)&st, 0, 0, 0, 0);
        if (rc != 0)
            rt_exit(112);
        if (st.state == 1)
            break;
        if (st.state != 0)
            rt_exit(140 + (int)st.state);
        if (++polls > 1000000)
            rt_exit(114);
        if (rt_nap(1) != RT_OK)
            rt_exit(115);
    }
    if (st.code != 42)
        rt_exit(116);
    rc = rt_gate6(5, h, (unsigned long long)&st, 0, 0, 0, 0);
    if (rc != 5)
        rt_exit(117);
    rc = rt_gate6(6, h, 0, 0, 0, 0, 0);
    if (rc != 5 && rc != 9)
        rt_exit(118);
    rt_exit(0);
    return 0;
}
