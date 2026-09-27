/* P1-A2 Slice P1-A file-mutation helpers (CPL3, freestanding).
 *
 * Header-only companions to rt.h (which stays frozen): programs opt in
 * by staging this file alongside their sources (see the P1-A2
 * integration test). Gate numbers mirror kernel/include/syscall.h;
 * the batch cap mirrors UAPI_FWRITE_MAX; a repository test pins them
 * equal. No new gate instructions here (rt_gate6 from rt_gate.asm
 * only).
 *
 * Contracts (frozen P1-A2 ABI):
 * - rt_fcreate creates a zero-length file. The target must not exist:
 *   an existing target (any type) fails; there is no truncate,
 *   overwrite, or append in this slice.
 * - rt_fwrite writes exactly len bytes at offset. OK means every byte
 *   landed (*nwritten == len); any failure leaves *nwritten
 *   untouched. Offsets past end-of-file are rejected (no sparse
 *   holes, even for zero-length writes).
 * Thin-wrapper collapse rule (identical to rt_fread in rt_pipe.h):
 * OK -> RT_OK, everything else -> RT_INVAL. Exact sys_err codes are
 * asserted in-guest through rt_gate6 by the probe programs; shells
 * use the raw gate for exact codes (the established Slice E
 * convention) and these mirrors for readability only.
 */
#ifndef RYNOR_RT_FS_H
#define RYNOR_RT_FS_H

#include "rt.h"

#define RT_SYS_FCREATE 9u
#define RT_SYS_FWRITE 10u

#define RT_FWRITE_MAX 16384u
#define RT_FCREATE_PATH_MAX 32u

extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

/* Thin fcreate wrapper. The kernel takes (path_ptr, path_len) with
 * four reserved zero words; the wrapper passes them explicitly. */
static enum rt_err __attribute__((unused))
rt_fcreate(const char *path, unsigned long long path_len)
{
    unsigned long long rc;
    if (path_len == 0 || path_len > RT_FCREATE_PATH_MAX)
        return RT_INVAL;
    if (path == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_FCREATE, (unsigned long long)path, path_len,
                  0, 0, 0, 0);
    if (rc == 0)
        return RT_OK;
    return RT_INVAL;
}

/* Thin fwrite wrapper. nwritten is published by the kernel only on
 * success (always == len); failures leave it untouched. */
static enum rt_err __attribute__((unused))
rt_fwrite(const char *path, unsigned long long path_len,
          unsigned long long offset, const void *buf,
          unsigned long long len, unsigned long long *nwritten)
{
    unsigned long long rc;
    if (path_len == 0 || path_len > RT_FCREATE_PATH_MAX)
        return RT_INVAL;
    if (len > RT_FWRITE_MAX)
        return RT_RANGE;
    if (nwritten == 0)
        return RT_INVAL;
    if (len != 0 && buf == 0)
        return RT_INVAL;
    if (path == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_FWRITE, (unsigned long long)path, path_len,
                  offset, (unsigned long long)buf, len,
                  (unsigned long long)nwritten);
    if (rc == 0)
        return RT_OK;
    return RT_INVAL;
}

#endif
