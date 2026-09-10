/* Slice D probe: final program discovery through syscall 4.
 * Bare names resolve under /bin/ (frozen 27-byte bound); absolute
 * paths keep Slice C behavior. Every row expects an exact code;
 * 160+ = violation class. Exit 0 = green.
 *
 * Binaries (Slice D image): /bin/ok.rnx (exit 42), /bin/bad.rnx (bad
 * magic), /bin/ccccccccccccccccccccccccccc.rnx (27 c's, exit 42).
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

static const char bare_ok[] = "ok.rnx";
static const char abs_ok[] = "/bin/ok.rnx";
static const char bare_missing[] = "nope.rnx";
static const char abs_missing[] = "/bin/nope.rnx";
static const char bare_bad[] = "bad.rnx";
static const char abs_bad[] = "/bin/bad.rnx";
static const char abs_t[] = "/t/exit42.rnx";
static const char abs_dir_bin[] = "/bin";
static const char abs_dir_f[] = "/f";
static const char bare_d[] = "d";
static const char trav[] = "x/../ok.rnx";
static const char name27[] = "ccccccccccccccccccccccccccc";
static const char name28[] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbb";
static const char name27missing[] = "ddddddddddddddddddddddddddd";

static unsigned long long slen(const char *s)
{
    unsigned long long n = 0;
    while (n < 40 && s[n] != 0)
        ++n;
    return n;
}

/* Spawn path/len, expect rc; on OK wait for EXITED+code and consume. */
static void expect_run(const char *p, unsigned long long plen,
                       unsigned long long want_code, int fail)
{
    struct spawn_spec spec;
    struct proc_status st;
    unsigned long long h = 0xAAAAAAAAAAAAAAAAULL;
    unsigned long long rc;
    unsigned int polls = 0;
    spec.path_ptr = (unsigned long long)p;
    spec.path_len = plen;
    spec.args_ptr = 0;
    spec.nargs = 0;
    spec.stdin_sel = 1;
    spec.stdout_sel = 0;
    spec.stderr_sel = 0;
    spec.file_in = 0;
    spec.file_out = 0;
    spec.file_err = 0;
    spec.reserved[0] = 0;
    spec.reserved[1] = 0;
    spec.reserved[2] = 0;
    spec.reserved[3] = 0;
    rc = rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                  0, 0, 0, 0);
    if (rc != 0)
        rt_exit(fail);
    for (;;) {
        rc = rt_gate6(5, h, (unsigned long long)&st, 0, 0, 0, 0);
        if (rc != 0)
            rt_exit(fail + 1);
        if (st.state == 1)
            break;
        if (st.state != 0)
            rt_exit(fail + 2);
        if (++polls > 1000000)
            rt_exit(fail + 3);
        if (rt_nap(1) != RT_OK)
            rt_exit(fail + 4);
    }
    if (st.code != want_code)
        rt_exit(fail + 5);
}

static void expect_rc(const char *p, unsigned long long plen,
                      unsigned long long want, int fail)
{
    struct spawn_spec spec;
    unsigned long long h = 0xAAAAAAAAAAAAAAAAULL;
    unsigned long long rc;
    spec.path_ptr = (unsigned long long)p;
    spec.path_len = plen;
    spec.args_ptr = 0;
    spec.nargs = 0;
    spec.stdin_sel = 1;
    spec.stdout_sel = 0;
    spec.stderr_sel = 0;
    spec.file_in = 0;
    spec.file_out = 0;
    spec.file_err = 0;
    spec.reserved[0] = 0;
    spec.reserved[1] = 0;
    spec.reserved[2] = 0;
    spec.reserved[3] = 0;
    rc = rt_gate6(4, (unsigned long long)&spec, (unsigned long long)&h,
                  0, 0, 0, 0);
    if (rc != want)
        rt_exit(fail);
    if (h != 0xAAAAAAAAAAAAAAAAULL)
        rt_exit(fail + 1);
}

int rt_main(void)
{
    _Static_assert(sizeof(struct spawn_spec) == 96, "spec layout");
    /* Bare and absolute forms of a good executable. */
    expect_run(bare_ok, slen(bare_ok), 42, 160);
    expect_run(abs_ok, slen(abs_ok), 42, 170);
    /* Missing: NOTFOUND (3) in both forms; no truncation of a
       27-byte missing name (still NOTFOUND, never BADARG). */
    expect_rc(bare_missing, slen(bare_missing), 3, 180);
    expect_rc(abs_missing, slen(abs_missing), 3, 182);
    expect_rc(name27missing, slen(name27missing), 3, 184);
    /* Malformed: MALFORMED (4) in both forms. */
    expect_rc(bare_bad, slen(bare_bad), 4, 186);
    expect_rc(abs_bad, slen(abs_bad), 4, 188);
    /* 27-byte boundary: existing 27-name runs; 28-byte name is BADARG
       (never truncated to a runnable prefix). */
    expect_run(name27, slen(name27), 42, 190);
    expect_rc(name28, slen(name28), 8, 200);
    /* Traversal through a bare-form slash is a shape error. */
    expect_rc(trav, slen(trav), 8, 202);
    /* Absolute Slice C behavior unchanged. */
    expect_run(abs_t, slen(abs_t), 42, 204);
    /* Directories exist but are not executables: MALFORMED, never
       NOTFOUND (the frozen missing-vs-malformed distinction). */
    expect_rc(abs_dir_bin, slen(abs_dir_bin), 4, 214);
    expect_rc(abs_dir_f, slen(abs_dir_f), 4, 216);
    expect_rc(bare_d, slen(bare_d), 4, 218);
    /* Length bounds. */
    expect_rc(bare_ok, 0, 8, 220);
    expect_rc(bare_ok, 33, 8, 222);
    rt_exit(0);
    return 0;
}
