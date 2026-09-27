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
/* P1-A3 Slice P1-A discovery/deletion gates (mirror syscall.h; a
 * repository test pins them equal). RT_SYS_END mirrors the sys_err
 * end-of-directory value for raw-gate callers (ls): it shares its
 * number with RT_SYS_UNLINK across the separate namespaces (see
 * docs/design/p1a3-lifecycle-abi.md). */
#define RT_SYS_FSTAT 11u
#define RT_SYS_READDIR 12u
#define RT_SYS_UNLINK 13u
#define RT_SYS_END 13u

#define RT_FWRITE_MAX 16384u
#define RT_FCREATE_PATH_MAX 32u

/* P1-A3 entry types (mirror UAPI_FTYPE_*, pinned by test). */
#define RT_FTYPE_FILE 1u
#define RT_FTYPE_DIR 2u

/* P1-A3 stat payload mirror (32 bytes; layout pinned by test). */
struct rt_stat {
    unsigned long long type;
    unsigned long long size;
    unsigned long long reserved[2];
};

/* P1-A3 directory-entry payload mirror (64 bytes; layout pinned). */
struct rt_dirent {
    unsigned char name[40];
    unsigned long long type;
    unsigned long long size;
    unsigned long long reserved;
};

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

/* Thin fstat wrapper. The struct is published by the kernel only on
 * success (reserved zeroed); failures leave it untouched. */
static enum rt_err __attribute__((unused))
rt_fstat(const char *path, unsigned long long path_len,
          struct rt_stat *out)
{
    unsigned long long rc;
    if (path_len == 0 || path_len > RT_FCREATE_PATH_MAX)
        return RT_INVAL;
    if (out == 0)
        return RT_INVAL;
    if (path == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_FSTAT, (unsigned long long)path, path_len,
                  (unsigned long long)out, 0, 0, 0);
    if (rc == 0)
        return RT_OK;
    return RT_INVAL;
}

/* Thin readdir wrapper. The entry is published only on success;
 * SYS_END and every error leave the output untouched. Callers that
 * must distinguish end-of-directory use the raw gate and compare
 * against RT_SYS_END (the collapse rule erases it by design). */
static enum rt_err __attribute__((unused))
rt_readdir(unsigned long long ordinal, struct rt_dirent *out)
{
    unsigned long long rc;
    if (out == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_READDIR, ordinal, (unsigned long long)out,
                  0, 0, 0, 0);
    if (rc == 0)
        return RT_OK;
    return RT_INVAL;
}

/* Thin unlink wrapper. Directories and root are refused by the
 * kernel (a type refusal, not a missing name); a second unlink of
 * the same path fails. */
static enum rt_err __attribute__((unused))
rt_unlink(const char *path, unsigned long long path_len)
{
    unsigned long long rc;
    if (path_len == 0 || path_len > RT_FCREATE_PATH_MAX)
        return RT_INVAL;
    if (path == 0)
        return RT_INVAL;
    rc = rt_gate6(RT_SYS_UNLINK, (unsigned long long)path, path_len,
                  0, 0, 0, 0);
    if (rc == 0)
        return RT_OK;
    return RT_INVAL;
}

#endif
