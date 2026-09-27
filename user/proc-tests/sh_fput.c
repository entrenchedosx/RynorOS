/* P1-A2 redirect consumer: read stdin (pipe) to EOF, strip one
 * trailing newline, create argv[1] through fcreate, and write the
 * captured bytes through fwrite. The shell synthesizes this stage
 * for `echo TEXT > /path`; the shell itself never touches file
 * bytes (pipe capture, not parser-direct writes).
 *
 * Contract (frozen for this slice):
 * - argv: exactly ["fput", target] (argc != 2 exits 66).
 * - Input: all pipe bytes up to 2048 (echo output cannot exceed
 *   ~264 by the shell argv budget; larger input exits 65).
 * - Exactly one trailing '\n' is stripped (echo always appends it;
 *   the file holds the user's TEXT verbatim otherwise). Empty input
 *   after the strip still creates the file (zero-length) and still
 *   performs the (zero-length) fwrite.
 * - Exit 0 on success; the RAW sys_err of a failed fcreate/fwrite
 *   otherwise (11 EXISTS, 12 NOSPC, ...), so the shell reports the
 *   honest kernel reason. 65 = pipe/read integrity failure, 66 =
 *   bad arguments (the rt 64+class convention).
 * Manual `|> fput` composition works mechanically but is not a
 * supported interface (argv/exit contract may change); only the
 * shell's `echo TEXT > /path` is specified.
 */
#include "rt.h"
#include "rt_pipe.h"
#include "rt_fs.h"

static unsigned long long slen(const char *s, unsigned long long cap)
{
    unsigned long long n = 0;
    while (n < cap && s[n] != 0)
        ++n;
    return n;
}

int rt_main(int argc, char **argv)
{
    /* 2048 payload bytes + 1 overflow sentinel: reaching the
       sentinel proves input past the bound (echo output cannot
       exceed ~264 by the shell argv budget). */
    static unsigned char buf[2049];
    unsigned long long at = 0;
    unsigned long long retries = 0;
    unsigned long long n = 0;
    unsigned long long plen;
    unsigned long long rc;
    if (argc != 2 || argv == 0 || argv[1] == 0)
        rt_exit(66);
    for (;;) {
        unsigned long long got = 0xAAAAAAAAAAAAAAAAULL;
        enum rt_err rr = rt_pipe_read_once(buf + at, sizeof(buf) - at, &got);
        if (rr == RT_AGAIN) {
            if (++retries > RT_PIPE_RETRY_MAX)
                rt_exit(65);
            if (rt_nap(1) != RT_OK)
                rt_exit(65);
            continue;
        }
        if (rr != RT_OK)
            rt_exit(65);
        if (got == 0)
            break;
        at += got;
        if (at > 2048)
            rt_exit(65);
    }
    /* Strip exactly one trailing newline (the echo terminator). */
    if (at > 0 && buf[at - 1] == '\n')
        --at;
    plen = slen(argv[1], 64);
    rc = rt_gate6(RT_SYS_FCREATE, (unsigned long long)argv[1], plen,
                  0, 0, 0, 0);
    if (rc != 0)
        rt_exit((int)rc);
    n = 0xAAAAAAAAAAAAAAAAULL;
    rc = rt_gate6(RT_SYS_FWRITE, (unsigned long long)argv[1], plen, 0,
                  (unsigned long long)buf, at, (unsigned long long)&n);
    if (rc != 0)
        rt_exit((int)rc);
    if (n != at)
        rt_exit(65);
    rt_exit(0);
    return 0;
}
