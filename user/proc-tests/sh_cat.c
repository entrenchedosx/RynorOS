/* Slice E helper: relay fd0 to fd1 until terminal EOF (pipe
 * consumer / empty-input probe). AGAIN yield-retries; any other read
 * error exits nonzero.
 *
 * P1-A2: with argv[1] present, cat is a file reader instead: it
 * streams argv[1] through the public fread wrapper to stdout and
 * exits 0 (any file error exits 1). Bare `cat` keeps the exact
 * Slice E pipe-relay behavior below.
 *
 * P1-A3: the file branch is stat-driven: fstat first (missing path
 * or non-file exits 1), then exactly size bytes through chunked
 * 1 KiB freads (no new ceiling: arbitrarily large files stream).
 */
#include "rt.h"
#include "rt_pipe.h"
#include "rt_fs.h"

static unsigned long long cat_slen(const char *s)
{
    unsigned long long n = 0;
    /* No silent truncation: over-long paths reach the kernel, which
       rejects them (the wrapper then reports failure). */
    while (n < 64 && s[n] != 0)
        ++n;
    return n;
}

int rt_main(int argc, char **argv)
{
    static unsigned char buf[1024];
    unsigned long long retries = 0;
    if (argc >= 2 && argv != 0 && argv[1] != 0) {
        static struct rt_stat st;
        unsigned long long off = 0;
        unsigned long long plen = cat_slen(argv[1]);
        unsigned long long size;
        if (rt_fstat(argv[1], plen, &st) != RT_OK)
            rt_exit(1);
        if (st.type != RT_FTYPE_FILE)
            rt_exit(1);
        size = st.size;
        while (off < size) {
            unsigned long long want = size - off;
            unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
            unsigned long long at = 0;
            if (want > sizeof(buf))
                want = sizeof(buf);
            if (rt_fread(argv[1], plen, off, buf, want, &n) != RT_OK)
                rt_exit(1);
            /* Exact length against a known size: a short read here
               (racing mutation has no writer in this shell) fails. */
            if (n != want)
                rt_exit(1);
            while (at < n) {
                unsigned long long chunk = n - at;
                if (chunk > RT_WRITE_MAX)
                    chunk = RT_WRITE_MAX;
                if (rt_write(RT_FD_STDOUT, buf + at, chunk) != RT_OK)
                    rt_exit(1);
                at += chunk;
            }
            off += n;
        }
        rt_exit(0);
        return 0;
    }
    for (;;) {
        unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
        enum rt_err rc = rt_pipe_read_once(buf, sizeof(buf), &n);
        if (rc == RT_AGAIN) {
            if (++retries > RT_PIPE_RETRY_MAX)
                rt_exit(14);
            if (rt_nap(1) != RT_OK)
                rt_exit(16);
            continue;
        }
        if (rc != RT_OK)
            rt_exit(13);
        if (n == 0)
            break;
        if (rt_pipe_write_all(RT_FD_STDOUT, buf, n) != RT_OK)
            rt_exit(15);
    }
    rt_exit(0);
    return 0;
}
