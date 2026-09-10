/* Stage 18d Slice D pipe/fread helpers (CPL3, freestanding).
 *
 * Header-only companions to rt.h (which stays frozen): programs opt in
 * by staging this file alongside their sources (see the Slice D
 * integration test). Gate numbers mirror kernel/include/syscall.h and
 * docs/design/stage18d-abi.md §D; a repository test pins them equal.
 * No new gate instructions here (rt_gate6 from rt_gate.asm only).
 *
 * Retry discipline (frozen ABI: nonblocking primitive + cooperative
 * retry, no kernel waits, no scheduler changes): short-0 writes and
 * AGAIN reads yield (rt_nap) and retry under a generous iteration cap.
 * The cap is effectively unreachable while the peer lives (every live
 * peer drains/fills and terminates); it only bounds true wedges, which
 * surface as a distinct code instead of a hang. Broken pipe ((u64)-1
 * from a write with readers gone) is terminal, never retried: the
 * caller decides (no SIGPIPE exists).
 */
#ifndef RYNOR_RT_PIPE_H
#define RYNOR_RT_PIPE_H

#include "rt.h"

#define RT_SYS_FREAD 7u
#define RT_SYS_SPAWN_PIPE 8u

#define RT_PIPE_BUF 4096u
#define RT_FREAD_MAX 16384u
#define RT_PIPE_RETRY_MAX 1000000u

extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

/* Thin fread wrapper. Kernel sys_err collapses like rt_fd_read:
 * OK -> RT_OK, everything else -> RT_INVAL (exact codes are asserted
 * in-guest through rt_gate6 by the probe programs). */
static enum rt_err __attribute__((unused))
rt_fread(const char *path, unsigned long long path_len,
         unsigned long long offset, void *buf,
         unsigned long long len, unsigned long long *nread)
{
    unsigned long long rc;
    if (path_len == 0 || path_len > 32u)
        return RT_INVAL;
    if (len > RT_FREAD_MAX)
        return RT_RANGE;
    if (nread == 0)
        return RT_INVAL;
    if (len != 0 && buf == 0)
        return RT_INVAL;
    if (path == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_FREAD, (unsigned long long)path, path_len, offset,
                  (unsigned long long)buf, len, (unsigned long long)nread);
    if (rc == 0)
        return RT_OK;
    return RT_INVAL;
}

/* Thin spawn_pipe wrapper (same collapsing rule). Specs use the frozen
 * 96-byte layout; see the probe programs for the explicit shape. */
static enum rt_err __attribute__((unused))
rt_spawn_pipe(const void *spec_a, const void *spec_b,
              unsigned long long *handle_a_out,
              unsigned long long *handle_b_out)
{
    unsigned long long rc;
    if (spec_a == 0 || spec_b == 0 || handle_a_out == 0 || handle_b_out == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_SPAWN_PIPE, (unsigned long long)spec_a,
                  (unsigned long long)spec_b, (unsigned long long)handle_a_out,
                  (unsigned long long)handle_b_out, 0, 0);
    if (rc == 0)
        return RT_OK;
    return RT_INVAL;
}

/* Blocking-in-effect pipe write: full calls with yield-retry on
 * short-0. Returns RT_OK when all n bytes are accepted, RT_INVAL on a
 * broken pipe or bad arguments, RT_AGAIN when the retry cap trips. */
static enum rt_err __attribute__((unused))
rt_pipe_write_all(unsigned int fd, const void *buf, unsigned long long n)
{
    unsigned long long at = 0;
    unsigned long long retries = 0;
    if (fd != RT_FD_STDOUT)
        return RT_INVAL;
    if (n > 0x100000u)
        return RT_RANGE;
    if (n != 0 && buf == 0)
        return RT_INVAL;
    while (at < n) {
        unsigned long long chunk = n - at;
        unsigned long long got;
        if (chunk > RT_PIPE_BUF)
            chunk = RT_PIPE_BUF;
        got = rt_gate6(RT_SYS_WRITE, (unsigned long long)fd,
                       (unsigned long long)((const char *)buf + at), chunk,
                       0, 0, 0);
        if (got == (unsigned long long)-1)
            return RT_INVAL;
        if (got > chunk)
            return RT_INVAL;
        at += got;
        if (at < n) {
            if (++retries > RT_PIPE_RETRY_MAX)
                return RT_AGAIN;
            if (rt_nap(1) != RT_OK)
                return RT_INVAL;
        }
    }
    return RT_OK;
}

/* Single pipe read attempt: RT_OK (count in *nread, zero means EOF),
 * RT_AGAIN (empty with a live writer; outputs untouched), or RT_INVAL. */
static enum rt_err __attribute__((unused))
rt_pipe_read_once(void *buf, unsigned long long n, unsigned long long *nread)
{
    unsigned long long rc;
    if (n > RT_PIPE_BUF)
        return RT_RANGE;
    if (nread == 0)
        return RT_INVAL;
    if (n != 0 && buf == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_READ, (unsigned long long)RT_FD_STDIN,
                  (unsigned long long)buf, n, (unsigned long long)nread, 0, 0);
    if (rc == 0)
        return RT_OK;
    if (rc == 1)
        return RT_AGAIN;
    return RT_INVAL;
}

#endif
