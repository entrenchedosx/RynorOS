/* Slice C probe: register-level and hostile-pointer behavior of
 * syscalls 4-6 through the real gate. Every row expects an exact code;
 * 140+ = violation class. Exit 0 = matrix green.
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

struct user_arg {
    unsigned long long ptr;
    unsigned long long len;
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
static const char arg[] = "x";
static const char nularg[3] = {'a', 0, 'b'};

int rt_main(void)
{
    struct spawn_spec spec;
    struct user_arg vec[1];
    struct proc_status st;
    unsigned long long h = 0;
    unsigned long long rc;
    spec.path_ptr = (unsigned long long)path;
    spec.path_len = 13;
    spec.args_ptr = 0;
    spec.nargs = 0;
    spec.stdin_sel = 1;
    spec.stdout_sel = 0;
    spec.stderr_sel = 0;
    /* Unused-register discipline: nonzero reserved regs are INVAL (2). */
    if (rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                 1, 0, 0, 0) != 2)
        rt_exit(140);
    if (rt_gate6(5, h, (unsigned long long)&st, 0, 0, 0, 1) != 2)
        rt_exit(141);
    if (rt_gate6(6, h, 1, 0, 0, 0, 0) != 2)
        rt_exit(142);
    /* Bad spec pointer class: BADARG (8). */
    if (rt_gate6(4, 0x500000ULL, (unsigned long long)&h, 0, 0, 0, 0) != 8)
        rt_exit(143);
    /* Bad handle class: BADHANDLE (5). */
    if (rt_gate6(5, 0xFFFFFFFFULL, (unsigned long long)&st, 0, 0, 0, 0) != 5)
        rt_exit(144);
    if (rt_gate6(6, 0xFFFFFFFFULL, 0, 0, 0, 0, 0) != 5)
        rt_exit(145);
    /* Real spawn for the status-pointer rows. */
    if (rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                 0, 0, 0, 0) != 0)
        rt_exit(146);
    /* RX status pointer: BADARG, zombie intact, valid retry consumes. */
    if (rt_gate6(5, h, 0x400000ULL, 0, 0, 0, 0) != 8)
        rt_exit(147);
    {
        unsigned int polls = 0;
        for (;;) {
            rc = rt_gate6(5, h, (unsigned long long)&st, 0, 0, 0, 0);
            if (rc != 0)
                rt_exit(148);
            if (st.state == 1)
                break;
            if (st.state != 0)
                rt_exit(149);
            if (++polls > 1000000)
                rt_exit(150);
            if (rt_nap(1) != RT_OK)
                rt_exit(151);
        }
    }
    if (st.code != 42)
        rt_exit(152);
    /* Hostile argv: nargs 9, NUL in string, bad string pointer. */
    vec[0].ptr = (unsigned long long)arg;
    vec[0].len = 1;
    spec.args_ptr = (unsigned long long)vec;
    spec.nargs = 9;
    if (rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                 0, 0, 0, 0) != 8)
        rt_exit(153);
    spec.nargs = 1;
    vec[0].ptr = 0x500000ULL;
    if (rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                 0, 0, 0, 0) != 8)
        rt_exit(154);
    vec[0].ptr = (unsigned long long)nularg;
    vec[0].len = 3;
    if (rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                 0, 0, 0, 0) != 8)
        rt_exit(155);
    rt_exit(0);
    return 0;
}
