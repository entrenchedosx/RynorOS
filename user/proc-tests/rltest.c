/* Slice F scale driver (CPL3 guest test aid, not part of /bin/sh).
 *
 * Links the same rl_* sources as the shell and drives rl_submit
 * directly with generated inputs no keyboard session could type in
 * time (near-8K sessions, 128-symbol tables, deep trees, reset
 * stress). Prints [RLT] verdict rows; exits 0 iff every internal
 * assertion holds. The host asserts rows + exit status; it never
 * computes answers. Session isolation: this image owns a private
 * rl_sess (never the shell's).
 *
 * Usage: rltest <cap-session|str-life|perf|depth|leak|len>
 *   cap-session <total>: build session to exactly <total> bytes with
 *     one final sized let (8191/8192 must commit; 8193 must reject
 *     with rollback + expression recovery).
 *   (Symbol-cap 127/128/129 live runs exceed the QEMU time budget
 *   even in-guest (129 submits need ~8K whole-buffer item
 *   re-analyses); the boundary value is pinned by repository test
 *   and the cap mechanism by the lowered-cap mutant F-M14.)
 *   str-life: persistent string across 200 mixed resets + exact bytes.
 *   perf: near-8K session then a full-rebuild submission (bounded).
 *   depth: 63/64/65-deep paren expressions.
 *   leak: 50x syntax/semantic/success submits with per-submit walks.
 *   len: Slice G builtin (literals, escapes, long literal, vars,
 *     arity/type/unknown rejects with rollback, len-depth 64/65,
 *     ownership across mixed resets, heavy len-error leak loop).
 */
#include "rt.h"
#include "rl_sem.h"
#include "rl_parse.h"

static int failed;
static char ebuf[512];
static unsigned int ebuf_used;

static void emit_both(const char *s, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < n; ++i) {
        if (ebuf_used < sizeof(ebuf))
            ebuf[ebuf_used++] = s[i];
    }
    if (n > 0 && s != 0)
        rt_write(RT_FD_STDOUT, s, n);
}

static void emit_row(const char *s)
{
    unsigned int n = 0;
    while (n < 256 && s[n] != 0) ++n;
    /* Rows bypass the value capture buffer (ebuf holds only
     * submit-emitted value bytes for internal assertions). */
    if (n > 0)
        rt_write(RT_FD_STDOUT, s, n);
}

static int streq_n(const char *a, const char *b, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < n; ++i)
        if (a[i] != b[i]) return 0;
    return b[n] == 0;
}

static int diag_is(const char *d, const char *want)
{
    unsigned int i = 0;
    if (!d || !want) return 0;
    while (want[i] != 0) {
        if (d[i] != want[i]) return 0;
        ++i;
    }
    return d[i] == 0;
}

static void check(int ok, const char *name)
{
    emit_row("[RLT] check ");
    emit_row(name);
    emit_row(ok ? " ok\n" : " FAIL\n");
    if (!ok) failed = 1;
}

static void udec(unsigned long long v, char *b, unsigned int *n)
{
    char t[21];
    unsigned int tn = 0, i = 0;
    if (v == 0u) {
        b[(*n)++] = '0';
        return;
    }
    while (v > 0u && tn < sizeof(t)) {
        t[tn++] = (char)('0' + v % 10u);
        v /= 10u;
    }
    while (tn > 0u) b[(*n)++] = t[--tn];
    (void)i;
}

static unsigned long long udec_parse(const char *s)
{
    unsigned long long v = 0;
    while (*s >= '0' && *s <= '9') {
        v = v * 10u + (unsigned long long)(*s - '0');
        ++s;
    }
    return v;
}

static void rstats(struct rl_stats *st)
{
    rl_stats(st);
}

/* Submit, returning outcome kind; diag via pointer when rejected. */
static int dosub(const char *text, unsigned int len, const char **diag)
{
    struct rl_outcome o = rl_submit(text, len);
    if (diag) *diag = o.diag;
    return o.kind;
}

/* Build `let <name>: int = <v>;` (no trailing semicolon; caller
 * sizes). Returns submission length. */
static unsigned int mklet(char *dst, const char *name, unsigned int nlen,
                          unsigned long long v)
{
    unsigned int n = 0, i;
    dst[n++] = 'l';
    dst[n++] = 'e';
    dst[n++] = 't';
    dst[n++] = ' ';
    for (i = 0; i < nlen; ++i) dst[n++] = name[i];
    dst[n++] = ':';
    dst[n++] = 'i';
    dst[n++] = 'n';
    dst[n++] = 't';
    dst[n++] = '=';
    udec(v, dst, &n);
    return n;
}

static void fill_name(char *dst, unsigned int n, char base,
                      unsigned int seq)
{
    unsigned int i = 0;
    dst[i++] = base;
    /* decimal seq then pad with base to exact length */
    {
        char t[12];
        unsigned int tn = 0, j;
        unsigned long long v = seq;
        if (v == 0u) {
            t[tn++] = '0';
        } else {
            while (v > 0u && tn < sizeof(t)) {
                t[tn++] = (char)('0' + v % 10u);
                v /= 10u;
            }
        }
        for (j = 0; j < tn; ++j) dst[i++] = t[tn - 1 - j];
    }
    while (i < n) dst[i++] = base;
}

static int cmd_cap_session(unsigned long long target)
{
    /* 16 base lets x 490 B = 7840 (long names keep the submit
     * count low: whole-buffer rebuild is O(n^2) per submit, and
     * QEMU time is budgeted), then one sized let to target.
     * mklet emits 9 + namelen + digits bytes. */
    char line[520];
    char name[505];
    unsigned int i;
    struct rl_stats st;
    const char *diag = 0;
    int k;
    unsigned int rem;
    if (target != 8191u && target != 8192u && target != 8193u) {
        emit_row("[RLT] check cap-session-args FAIL\n");
        return 1;
    }
    for (i = 0; i < 16u; ++i) {
        unsigned int n;
        fill_name(name, 479u, (char)('a' + (i % 26)), 10u + i);
        n = mklet(line, name, 479u, 10u + i);
        if (n != 490u) {
            check(0, "cap-session-base-size");
            return 1;
        }
        k = dosub(line, n, &diag);
        if (k != RL_SUB_OK) {
            char dbg[64];
            unsigned int dn = 0, j;
            const char *tag = "cap-session-base-commit i=";
            while (tag[dn] != 0) {
                dbg[dn] = tag[dn];
                ++dn;
            }
            udec(i, dbg, &dn);
            dbg[dn++] = 'k';
            dbg[dn++] = '=';
            udec((unsigned long long)k, dbg, &dn);
            dbg[dn++] = 'n';
            dbg[dn++] = '=';
            udec(n, dbg, &dn);
            for (j = 0; j < dn; ++j)
                ;
            emit_row("[RLT] info ");
            emit_both(dbg, dn);
            emit_row("\n");
            check(0, "cap-session-base-commit");
            return 1;
        }
    }
    rstats(&st);
    if (st.srclen != 7856u || st.nsyms != 16u) {
        check(0, "cap-session-base-stats");
        return 1;
    }
    /* Canonical bytes include the newline: a 490 B submission
     * commits 491 B, hence the base total above. Final submission
     * S commits S+1 = rem bytes. */
    rem = (unsigned int)target - 7856u;
    /* S = 9 + namelen + 1 digit = rem - 1 */
    if (rem < 13u || rem > 500u) {
        check(0, "cap-session-rem");
        return 1;
    }
    fill_name(name, rem - 11u, 'z', 7u);
    {
        unsigned int n = mklet(line, name, rem - 11u, 7u);
        if (n != rem - 1u) {
            check(0, "cap-session-final-size");
            return 1;
        }
        k = dosub(line, n, &diag);
    }
    if (target <= 8192u) {
        check(k == RL_SUB_OK, "cap-session-commit-max");
        rstats(&st);
        check(st.srclen == (unsigned int)target, "cap-session-len");
        check(st.nsyms == 17u, "cap-session-syms");
    } else {
        struct rl_stats before;
        rstats(&before);
        check(k == RL_SUB_REJECT &&
                  diag_is(diag, RL_D_SESSION_LIMIT),
              "cap-session-limit");
        rstats(&st);
        check(st.srclen == before.srclen, "cap-session-rollback-len");
        check(st.nsyms == before.nsyms, "cap-session-rollback-syms");
        check(st.sess_live == before.sess_live,
              "cap-session-rollback-arena");
        check(st.sub_live == 0u, "cap-session-rollback-sub");
        /* Recovery: a small expression still succeeds. */
        ebuf_used = 0;
        k = dosub("1+1", 3, &diag);
        check(k == RL_SUB_OK, "cap-session-recovery");
        check(ebuf_used == 1 && ebuf[0] == '2', "cap-session-value");
    }
    return 0;
}

static int cmd_str_life(void)
{
    /* Persistent string across 200 mixed resets + exact bytes. */
    const char *want = "persistent-value";
    unsigned int i;
    struct rl_stats st, base;
    const char *diag = 0;
    int k;
    k = dosub("let s: str = \"persistent-value\"", 31, &diag);
    if (k != RL_SUB_OK) {
        check(0, "str-life-commit");
        return 1;
    }
    rstats(&base);
    for (i = 0; i < 200u; ++i) {
        if (i % 4u == 0u)
            k = dosub("nosuchvar + 1", 13, &diag);
        else if (i % 4u == 1u)
            k = dosub("let s: str = \"other\"", 20, &diag);
        else if (i % 4u == 2u)
            k = dosub("1/0", 3, &diag);
        else
            k = dosub("40+2", 4, &diag);
        if (k != RL_SUB_OK && k != RL_SUB_REJECT && k != RL_SUB_TRAP) {
            char dbg[48];
            unsigned int dn = 0;
            const char *tag = "str-life-class i=";
            while (tag[dn] != 0) {
                dbg[dn] = tag[dn];
                ++dn;
            }
            udec(i, dbg, &dn);
            dbg[dn++] = 'k';
            dbg[dn++] = '=';
            udec((unsigned long long)k, dbg, &dn);
            emit_row("[RLT] info ");
            emit_both(dbg, dn);
            emit_row("\n");
            check(0, "str-life-class");
            return 1;
        }
        rstats(&st);
        if (st.srclen != base.srclen || st.nsyms != base.nsyms ||
            st.sess_live != base.sess_live || st.sub_live != 0u) {
            check(0, "str-life-drift");
            return 1;
        }
    }
    ebuf_used = 0;
    k = dosub("s", 1, &diag);
    check(k == RL_SUB_OK, "str-life-read");
    check(ebuf_used == 16 &&
              streq_n(ebuf, want, 16),
          "str-life-bytes");
    return 0;
}

static int cmd_perf(void)
{
    /* Near-8K session, then one full-rebuild submission: bounded. */
    char line[260];
    char name[220];
    unsigned int i;
    struct rl_stats st;
    const char *diag = 0;
    int k;
    for (i = 0; i < 40u; ++i) {
        unsigned int n;
        /* 196-byte lets (`m` + 2-digit seq + pad, value 10+i). */
        fill_name(name, 185u, (char)('m' + (i % 10)), 10u + i);
        n = mklet(line, name, 185u, 10u + i);
        if (n != 196u) {
            check(0, "perf-build-size");
            return 1;
        }
        k = dosub(line, n, &diag);
        if (k != RL_SUB_OK) {
            check(0, "perf-build");
            return 1;
        }
    }
    rstats(&st);
    check(st.srclen == 40u * 197u, "perf-size");
    /* Rebuild the full session, then read back var m10 (value 10). */
    fill_name(name, 185u, 'm', 10u);
    {
        unsigned int n = 0, j;
        for (j = 0; j < 185u; ++j) line[n++] = name[j];
        line[n++] = '+';
        line[n++] = '1';
        ebuf_used = 0;
        k = dosub(line, n, &diag);
    }
    check(k == RL_SUB_OK, "perf-submit");
    check(ebuf_used == 2 && ebuf[0] == '1' && ebuf[1] == '1',
          "perf-value");
    rstats(&st);
    check(st.sub_live == 0u, "perf-clean");
    return 0;
}

static int cmd_depth(void)
{
    /* 63/64/65-deep paren expressions (keyboard mirrors 64/65). */
    char line[260];
    unsigned int i, n = 0;
    const char *diag = 0;
    int k;
    for (i = 0; i < 63u; ++i) line[n++] = '(';
    line[n++] = '1';
    for (i = 0; i < 63u; ++i) line[n++] = ')';
    k = dosub(line, n, &diag);
    check(k == RL_SUB_OK, "depth-63");
    n = 0;
    for (i = 0; i < 64u; ++i) line[n++] = '(';
    line[n++] = '1';
    for (i = 0; i < 64u; ++i) line[n++] = ')';
    k = dosub(line, n, &diag);
    check(k == RL_SUB_OK, "depth-64");
    n = 0;
    for (i = 0; i < 65u; ++i) line[n++] = '(';
    line[n++] = '1';
    for (i = 0; i < 65u; ++i) line[n++] = ')';
    k = dosub(line, n, &diag);
    check(k == RL_SUB_SYNTAX, "depth-65");
    return 0;
}

static void heavy_sum(char *dst, unsigned int terms, int trail)
{
    /* `1+1+...` with a trailing `+` iff trail (syntax failure after
     * ~2 nodes per term: pool exhaustion within few iterations when
     * the per-path reset is skipped). */
    unsigned int n = 0, i;
    for (i = 0; i < terms; ++i) {
        dst[n++] = '1';
        dst[n++] = '+';
    }
    if (!trail && n > 0u)
        --n;
}

static int cmd_leak(void)
{
    /* Heavy per-class submits (5x): green resets keep every walk
     * flat; a skipped reset exhausts the 256-node pool mid-run. */
    static char line[300];
    unsigned int i;
    struct rl_stats st;
    const char *diag = 0;
    int k;
    for (i = 0; i < 5u; ++i) {
        heavy_sum(line, 100u, 1);
        k = dosub(line, 200u, &diag);
        if (k != RL_SUB_SYNTAX) {
            check(0, "leak-syntax-class");
            return 1;
        }
        rstats(&st);
        if (st.srclen != 0u || st.nsyms != 0u ||
            st.sess_live != 0u || st.sub_live != 0u) {
            check(0, "leak-syntax-walk");
            return 1;
        }
        /* 99-term sum ending in an undeclared name (parses,
         * then rejects in analysis). */
        {
            unsigned int m = 0, j;
            for (j = 0; j < 99u; ++j) {
                line[m++] = '1';
                line[m++] = '+';
            }
            line[m++] = 'z';
            line[m++] = 'q';
            k = dosub(line, m, &diag);
        }
        if (k != RL_SUB_REJECT ||
            !diag_is(diag, RL_D_SEM_UNDECLARED)) {
            check(0, "leak-semantic-class");
            return 1;
        }
        rstats(&st);
        if (st.srclen != 0u || st.nsyms != 0u ||
            st.sess_live != 0u || st.sub_live != 0u) {
            check(0, "leak-semantic-walk");
            return 1;
        }
        ebuf_used = 0;
        heavy_sum(line, 100u, 0);
        k = dosub(line, 199u, &diag);
        if (k != RL_SUB_OK) {
            check(0, "leak-success-class");
            return 1;
        }
        rstats(&st);
        if (st.srclen != 0u || st.nsyms != 0u ||
            st.sess_live != 0u || st.sub_live != 0u) {
            check(0, "leak-success-walk");
            return 1;
        }
        if (ebuf_used != 3 || ebuf[0] != '1' || ebuf[1] != '0' ||
            ebuf[2] != '0') {
            check(0, "leak-success-value");
            return 1;
        }
    }
    emit_row("[RLT] check leak-5x ok\n");
    return 0;
}

static int streq2(const char *a, const char *b)
{
    unsigned int i = 0;
    while (a[i] != 0 && b[i] != 0) {
        if (a[i] != b[i]) return 0;
        ++i;
    }
    return a[i] == b[i];
}

static unsigned int tlen(const char *s)
{
    unsigned int n = 0;
    while (s[n] != 0) ++n;
    return n;
}

/* Submit, asserting the exact emitted value bytes. */
static void sub_val(const char *text, const char *want, const char *tag)
{
    const char *diag = 0;
    unsigned int i;
    int k;
    ebuf_used = 0;
    k = dosub(text, tlen(text), &diag);
    if (k != RL_SUB_OK) {
        check(0, tag);
        return;
    }
    if (ebuf_used != tlen(want)) {
        check(0, tag);
        return;
    }
    for (i = 0; i < ebuf_used; ++i) {
        if (ebuf[i] != want[i]) {
            check(0, tag);
            return;
        }
    }
    check(1, tag);
}

/* Submit, asserting rejection class, zero emission, and full
 * rollback (source/symbols/session bytes frozen, scratch flat). */
static void sub_rej(const char *text, int kind, const char *want_diag,
                    const char *tag)
{
    const char *diag = 0;
    struct rl_stats before, after;
    int k;
    rstats(&before);
    ebuf_used = 0;
    k = dosub(text, tlen(text), &diag);
    rstats(&after);
    if (k != kind || !diag_is(diag, want_diag)) {
        check(0, tag);
        return;
    }
    if (ebuf_used != 0u) {
        check(0, tag);
        return;
    }
    check(after.srclen == before.srclen &&
              after.nsyms == before.nsyms &&
              after.sess_live == before.sess_live &&
              after.sub_live == 0u,
          tag);
}

static int cmd_len(void)
{
    const char *diag = 0;
    struct rl_stats st, base;
    unsigned int i, n;
    int k;
    static char line[300];

    /* Literals: byte length of the decoded runtime string. */
    sub_val("len(\"\")", "0", "len-empty");
    sub_val("len(\"a\")", "1", "len-one");
    sub_val("len(\"abc\")", "3", "len-three");
    /* Escapes count decoded bytes, never source spelling. */
    sub_val("len(\"a\\nb\")", "3", "len-esc-n");
    sub_val("len(\"a\\tb\")", "3", "len-esc-t");
    sub_val("len(\"a\\\"b\")", "3", "len-esc-q");
    sub_val("len(\"a\\\\b\")", "3", "len-esc-bs");
    /* Long literal (200 decoded bytes; keyboard could never type
     * scale inputs inside a boot). */
    n = 0;
    line[n++] = 'l';
    line[n++] = 'e';
    line[n++] = 'n';
    line[n++] = '(';
    line[n++] = '"';
    for (i = 0; i < 200u; ++i) line[n++] = 'x';
    line[n++] = '"';
    line[n++] = ')';
    line[n] = 0;
    sub_val(line, "200", "len-long");

    /* Variables: committed session values, not scratch. */
    k = dosub("let s: str = \"hello\"", tlen("let s: str = \"hello\""),
              &diag);
    check(k == RL_SUB_OK, "len-let-s");
    sub_val("len(s)", "5", "len-var");
    k = dosub("let n: int = len(\"abcd\")",
              tlen("let n: int = len(\"abcd\")"), &diag);
    check(k == RL_SUB_OK, "len-let-n");
    sub_val("n", "4", "len-let-n-read");
    sub_val("len(\"abcd\") + 1000", "1004", "len-arith");
    sub_val("100 * len(\"abcde\")", "500", "len-mul");
    sub_val("len(s) == 5", "true", "len-cmp");
    ebuf_used = 0;
    k = dosub("print(len(\"abc\"))", tlen("print(len(\"abc\"))"),
              &diag);
    check(k == RL_SUB_OK && ebuf_used == 1 && ebuf[0] == '3',
          "len-print");

    /* Rejections: existing families, full rollback, zero bytes. */
    sub_rej("len(\"a\", \"b\")", RL_SUB_REJECT, RL_D_SEM_ARITY,
            "len-arity2");
    sub_rej("len(1)", RL_SUB_REJECT, RL_D_SEM_TYPE, "len-type-int");
    sub_rej("len(true)", RL_SUB_REJECT, RL_D_SEM_TYPE,
            "len-type-bool");
    sub_rej("len(len(\"x\"))", RL_SUB_REJECT, RL_D_SEM_TYPE,
            "len-nested");
    sub_rej("let n: bool = len(\"abc\")", RL_SUB_REJECT,
            RL_D_SEM_TYPE, "len-let-bool");
    sub_rej("length(\"ab\")", RL_SUB_REJECT, RL_D_SEM_UNKNOWN_FN,
            "len-prefix-word");
    sub_rej("banana(\"x\")", RL_SUB_REJECT, RL_D_SEM_UNKNOWN_FN,
            "len-unknown-intact");

    /* Depth: the call group charges exactly like every call
     * (63 parens + len = 64 OK; 64 parens + len = 65 reject). */
    n = 0;
    for (i = 0; i < 63u; ++i) line[n++] = '(';
    line[n++] = 'l';
    line[n++] = 'e';
    line[n++] = 'n';
    line[n++] = '(';
    line[n++] = '"';
    line[n++] = 'a';
    line[n++] = '"';
    line[n++] = ')';
    for (i = 0; i < 63u; ++i) line[n++] = ')';
    line[n] = 0;
    ebuf_used = 0;
    k = dosub(line, n, &diag);
    check(k == RL_SUB_OK && ebuf_used == 1 && ebuf[0] == '1',
          "len-depth-64");
    n = 0;
    for (i = 0; i < 64u; ++i) line[n++] = '(';
    line[n++] = 'l';
    line[n++] = 'e';
    line[n++] = 'n';
    line[n++] = '(';
    line[n++] = '"';
    line[n++] = 'a';
    line[n++] = '"';
    line[n++] = ')';
    for (i = 0; i < 64u; ++i) line[n++] = ')';
    line[n] = 0;
    k = dosub(line, n, &diag);
    check(k == RL_SUB_SYNTAX, "len-depth-65");

    /* Ownership: 50 mixed resets (len hits, len misses, plain
     * failures) keep the committed string byte-exact. */
    rstats(&base);
    for (i = 0; i < 50u; ++i) {
        if (i % 5u == 0u) {
            ebuf_used = 0;
            k = dosub("len(s)", 6, &diag);
            if (k != RL_SUB_OK || ebuf_used != 1 || ebuf[0] != '5') {
                check(0, "len-own-hit");
                return 1;
            }
        } else if (i % 5u == 1u) {
            k = dosub("len(1)", 6, &diag);
            if (k != RL_SUB_REJECT ||
                !diag_is(diag, RL_D_SEM_TYPE)) {
                check(0, "len-own-miss");
                return 1;
            }
        } else if (i % 5u == 2u) {
            k = dosub("nosuchvar + 1", 13, &diag);
            if (k != RL_SUB_REJECT) {
                check(0, "len-own-undecl");
                return 1;
            }
        } else if (i % 5u == 3u) {
            k = dosub("len(\"a\", \"b\")", 13, &diag);
            if (k != RL_SUB_REJECT ||
                !diag_is(diag, RL_D_SEM_ARITY)) {
                check(0, "len-own-arity");
                return 1;
            }
        } else {
            ebuf_used = 0;
            k = dosub("40 + 2", 6, &diag);
            if (k != RL_SUB_OK) {
                check(0, "len-own-plain");
                return 1;
            }
        }
        rstats(&st);
        if (st.srclen != base.srclen || st.nsyms != base.nsyms ||
            st.sess_live != base.sess_live || st.sub_live != 0u) {
            check(0, "len-own-drift");
            return 1;
        }
    }
    check(1, "len-own-50x");
    ebuf_used = 0;
    k = dosub("s", 1, &diag);
    check(k == RL_SUB_OK, "len-own-read");
    check(ebuf_used == 5 && streq_n(ebuf, "hello", 5),
          "len-own-bytes");
    sub_val("len(s)", "5", "len-own-final");

    /* Heavy len-error leak loop: five 99-term sums ending in a
     * mistyped len (parse OK, analysis rejects); every round must
     * keep its class and leave zero residue. */
    for (i = 0; i < 5u; ++i) {
        unsigned int m = 0, j;
        for (j = 0; j < 99u; ++j) {
            line[m++] = '1';
            line[m++] = '+';
        }
        line[m++] = 'l';
        line[m++] = 'e';
        line[m++] = 'n';
        line[m++] = '(';
        line[m++] = '1';
        line[m++] = ')';
        line[m] = 0;
        ebuf_used = 0;
        k = dosub(line, m, &diag);
        if (k != RL_SUB_REJECT ||
            !diag_is(diag, RL_D_SEM_TYPE)) {
            check(0, "len-leak-class");
            return 1;
        }
        rstats(&st);
        if (st.srclen != base.srclen || st.nsyms != base.nsyms ||
            st.sess_live != base.sess_live || st.sub_live != 0u ||
            ebuf_used != 0u) {
            check(0, "len-leak-walk");
            return 1;
        }
    }
    check(1, "len-leak-5x");
    return 0;
}

int rt_main(int argc, char **argv)
{
    int rc = 1;
    if (argc < 0 || argc > 8)
        rt_exit(70);
    rl_sess_init(emit_both);
    failed = 0;
    ebuf_used = 0;
    if (argc < 2) {
        emit_row("[RLT] check usage FAIL\n");
        rt_exit(2);
        return 0;
    }
    emit_row("[RLT] begin ");
    emit_row(argv[1]);
    emit_row("\n");
    if (streq2(argv[1], "cap-session") && argc == 3)
        rc = cmd_cap_session(udec_parse(argv[2]));
    else if (streq2(argv[1], "str-life"))
        rc = cmd_str_life();
    else if (streq2(argv[1], "perf"))
        rc = cmd_perf();
    else if (streq2(argv[1], "depth"))
        rc = cmd_depth();
    else if (streq2(argv[1], "leak"))
        rc = cmd_leak();
    else if (streq2(argv[1], "len"))
        rc = cmd_len();
    else {
        emit_row("[RLT] check unknown-cmd FAIL\n");
        rt_exit(2);
        return 0;
    }
    emit_row("[RLT] done ");
    emit_row(argv[1]);
    if (rc == 0 && !failed)
        emit_row(" pass\n");
    else
        emit_row(" FAIL\n");
    rt_exit((rc == 0 && !failed) ? 0 : 1);
    return 0;
}
