/* Slice D probe: syscall-8 spawn_pipe matrix through the real gate.
 * Rejects exact codes (150+ = violation); then runs one live 256-byte
 * pipeline (absolute /bin producer/consumer) to EXITED 42/42 and one
 * KBD-stdin variant. Exit 0 = green.
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

static const char prod[] = "/bin/p_prod.rnx";
static const char cons[] = "/bin/p_cons.rnx";
static const char missing[] = "/bin/nope.rnx";
static const char bad[] = "/bin/bad.rnx";
static const char dirbin[] = "/bin";
static const char arg256[] = "256";

static unsigned long long slen(const char *s)
{
    unsigned long long n = 0;
    while (n < 40 && s[n] != 0)
        ++n;
    return n;
}

static void fill_spec(struct spawn_spec *s, const char *p,
                      unsigned long long plen, unsigned int stdin_sel,
                      unsigned int stdout_sel, unsigned long long args_ptr,
                      unsigned int nargs)
{
    s->path_ptr = (unsigned long long)p;
    s->path_len = plen;
    s->args_ptr = args_ptr;
    s->nargs = nargs;
    s->stdin_sel = stdin_sel;
    s->stdout_sel = stdout_sel;
    s->stderr_sel = 0;
    s->file_in = 0;
    s->file_out = 0;
    s->file_err = 0;
    s->reserved[0] = 0;
    s->reserved[1] = 0;
    s->reserved[2] = 0;
    s->reserved[3] = 0;
}

static void wait_for(unsigned long long h, unsigned long long want_state,
                     unsigned long long want_code, int fail)
{
    struct proc_status st;
    unsigned int polls = 0;
    for (;;) {
        unsigned long long rc = rt_gate6(5, h, (unsigned long long)&st,
                                         0, 0, 0, 0);
        if (rc != 0)
            rt_exit(fail);
        if (st.state == want_state)
            break;
        if (st.state != 0)
            rt_exit(fail + 1);
        if (++polls > 1000000)
            rt_exit(fail + 2);
        if (rt_nap(1) != RT_OK)
            rt_exit(fail + 3);
    }
    if (want_state == 1 && st.code != want_code)
        rt_exit(fail + 4);
}

int rt_main(void)
{
    struct spawn_spec a, b;
    struct user_arg vec[1];
    struct proc_status st;
    unsigned long long ha = 0xAAAAAAAAAAAAAAAAULL;
    unsigned long long hb = 0xAAAAAAAAAAAAAAAAULL;
    _Static_assert(sizeof(struct spawn_spec) == 96, "spec layout");
    vec[0].ptr = (unsigned long long)arg256;
    vec[0].len = 3;
    fill_spec(&a, prod, slen(prod), 0, 1, (unsigned long long)vec, 1);
    fill_spec(&b, cons, slen(cons), 2, 0, (unsigned long long)vec, 1);
    /* Reserved registers: nonzero EDI/EBP are INVAL (2). */
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 1, 0) != 2)
        rt_exit(150);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 1) != 2)
        rt_exit(151);
    /* Hostile spec pointers: BADARG (8), outputs untouched. */
    ha = 0xAAAAAAAAAAAAAAAAULL;
    hb = 0xAAAAAAAAAAAAAAAAULL;
    if (rt_gate6(8, 0x500000ULL, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 8)
        rt_exit(152);
    if (ha != 0xAAAAAAAAAAAAAAAAULL || hb != 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(153);
    if (rt_gate6(8, (unsigned long long)&a, 0x500000ULL,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 8)
        rt_exit(154);
    /* Endpoint selectors: producer must request PIPE_W, consumer PIPE_R. */
    fill_spec(&a, prod, slen(prod), 0, 0, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 8)
        rt_exit(155);
    fill_spec(&a, prod, slen(prod), 0, 1, (unsigned long long)vec, 1);
    fill_spec(&b, cons, slen(cons), 1, 0, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 8)
        rt_exit(156);
    fill_spec(&b, cons, slen(cons), 2, 0, (unsigned long long)vec, 1);
    /* Failure classes per side (admission atomicity is driver-proved;
       here the codes must be exact). */
    fill_spec(&a, missing, slen(missing), 0, 1, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 3)
        rt_exit(157);
    fill_spec(&a, bad, slen(bad), 0, 1, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 4)
        rt_exit(158);
    fill_spec(&a, prod, slen(prod), 0, 1, (unsigned long long)vec, 1);
    fill_spec(&b, missing, slen(missing), 2, 0, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 3)
        rt_exit(159);
    /* Output capability: RX and wrapping destinations are BADARG
       with both handles untouched (publication rule). */
    ha = 0xAAAAAAAAAAAAAAAAULL;
    hb = 0xAAAAAAAAAAAAAAAAULL;
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 0x400000ULL, (unsigned long long)&hb, 0, 0) != 8)
        rt_exit(163);
    if (ha != 0xAAAAAAAAAAAAAAAAULL || hb != 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(164);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, 0xFFFFFFFFFFFFFFF8ULL, 0, 0) != 8)
        rt_exit(165);
    if (ha != 0xAAAAAAAAAAAAAAAAULL || hb != 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(166);
    /* A directory is not an executable (MALFORMED), per side. */
    fill_spec(&a, dirbin, slen(dirbin), 0, 1, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 4)
        rt_exit(176);
    fill_spec(&a, prod, slen(prod), 0, 1, (unsigned long long)vec, 1);
    fill_spec(&b, dirbin, slen(dirbin), 2, 0, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 4)
        rt_exit(178);
    /* Live pipe, 256 bytes, CLOSED producer stdin. */
    fill_spec(&b, cons, slen(cons), 2, 0, (unsigned long long)vec, 1);
    ha = 0xAAAAAAAAAAAAAAAAULL;
    hb = 0xAAAAAAAAAAAAAAAAULL;
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 0)
        rt_exit(160);
    if (ha == 0xAAAAAAAAAAAAAAAAULL || hb == 0xAAAAAAAAAAAAAAAAULL || ha == hb)
        rt_exit(161);
    wait_for(ha, 1, 42, 162);
    wait_for(hb, 1, 42, 167);
    /* Consume-once on both handles. */
    if (rt_gate6(5, ha, (unsigned long long)&st, 0, 0, 0, 0) != 5)
        rt_exit(172);
    if (rt_gate6(5, hb, (unsigned long long)&st, 0, 0, 0, 0) != 5)
        rt_exit(173);
    /* KBD-stdin producer variant (spec may say otherwise for A.stdin). */
    fill_spec(&a, prod, slen(prod), 1, 1, (unsigned long long)vec, 1);
    if (rt_gate6(8, (unsigned long long)&a, (unsigned long long)&b,
                 (unsigned long long)&ha, (unsigned long long)&hb, 0, 0) != 0)
        rt_exit(174);
    wait_for(ha, 1, 42, 175);
    wait_for(hb, 1, 42, 180);
    rt_exit(0);
    return 0;
}
