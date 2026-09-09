/* Stage 18c conformance: rt_fmt transactional formatting.
 * Class 1: internal mismatch exits 65; success exits 0. Every evidence
 * line carries its own CRLF so the transcript splits deterministically.
 */
#include "rt.h"

/* Length-exact emit for rt_fmt output: rt_fmt never NUL-terminates (it
   reports the count), so scanning would read stale stack bytes. */
static void emit_n(const char *s, long long n)
{
    if (n < 0 || rt_print_bytes(s, (unsigned long long)n) != RT_OK)
        rt_exit(65);
}

int rt_main(void)
{
    char buf[64];
    char tiny[8];
    char line[96];
    long long rc;
    unsigned long long wm0 = rt_arena_watermark();
    int i;
    int untouched;
    for (i = 0; i < 8; ++i)
        tiny[i] = 'D';
    rc = rt_fmt(buf, sizeof buf, "s=%s u=%u x=%x c=%c", "hello", 42u,
                0x2au, 'Z');
    if (rc != 21)
        rt_exit(65);
    rc = rt_fmt(line, sizeof line,
                "[RT] fmt ok s=hello u=42 x=2a c=Z rc=%u\r\n", 21u);
    if (rc != 41)
        rt_exit(65);
    emit_n(line, rc);
    /* Unknown spec: invalid (-1), destination untouched. */
    rc = rt_fmt(tiny, sizeof tiny, "a%qb");
    if (rc != -1)
        rt_exit(65);
    untouched = 1;
    for (i = 0; i < 8; ++i)
        if (tiny[i] != 'D')
            untouched = 0;
    rc = rt_fmt(line, sizeof line,
                "[RT] fmt badspec rc=4294967295 untouched=%u\r\n",
                (unsigned int)untouched);
    if (rc != 44)
        rt_exit(65);
    emit_n(line, rc);
    if (!untouched)
        rt_exit(65);
    /* Over-cap output: range error (-2), destination untouched. */
    for (i = 0; i < 8; ++i)
        tiny[i] = 'D';
    rc = rt_fmt(tiny, 4u, "s=%s", "hello");
    if (rc != -2)
        rt_exit(65);
    untouched = 1;
    for (i = 0; i < 8; ++i)
        if (tiny[i] != 'D')
            untouched = 0;
    rc = rt_fmt(line, sizeof line,
                "[RT] fmt trunc rc=4294967294 untouched=%u\r\n",
                (unsigned int)untouched);
    if (rc != 42)
        rt_exit(65);
    emit_n(line, rc);
    if (!untouched)
        rt_exit(65);
    rc = rt_fmt(line, sizeof line, "[RT] fmt wm0=%u wm1=%u live=%u\r\n",
                (unsigned int)wm0,
                (unsigned int)rt_arena_watermark(),
                (unsigned int)rt_live_count());
    if (rc != 29)
        rt_exit(65);
    emit_n(line, rc);
    if (rt_arena_watermark() != wm0 || rt_live_count() != 0u)
        rt_exit(65);
    return 0;
}
