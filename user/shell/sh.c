/* Stage 18d Slice E CPL3 shell + Slice F resident evaluator.
 *
 * Boots from /bin/sh via the kernel shell driver (bootstrap thread,
 * stdin KBD, stdout SERIAL, optional argv[0] script path). Reads raw
 * scan bytes (syscall 3), decodes Set-1 in CPL3 (sh_key.c), edits one
 * bounded line, and executes via spawn/wait/
 * terminate/spawn_pipe/fread only. No kernel policy calls exist.
 *
 * Execution model: one foreground submission at a time. Script
 * buffers feed the frozen Slice E statement pump (parse one
 * ';'/newline-delimited statement, execute, repeat when idle).
 * Interactive lines are split on top-level ';' (string-, brace-,
 * and comment-aware) and each part is classified deterministically:
 * E-command-shaped parts funnel verbatim into the proven Slice E
 * machinery (identical rows/statuses by construction); RynorLang
 * parts evaluate in the resident CPL3 evaluator (rl_*; whole-buffer
 * re-analysis, transactional commit, dual arenas); fn/if/while/
 * return/blocks reject loudly as NOTIMPL. The event loop always
 * services keyboard (Ctrl-C) and child polls together.
 *
 * Status policy (CPL3-only, documented; kernel ABI unchanged):
 *   EXITED(c) -> c & 0xFF | FAULTED -> 129 | ABORTED -> 130
 *   NOTFOUND -> 127 | MALFORMED -> 126 | other spawn errors -> 125
 *   syntax/args/script-local errors -> 2 | initial -> 0
 *   pipelines report the right-hand (consumer) status.
 * Slice F evaluator mapping (no new codes; rows distinguish):
 *   language syntax -> 2 | semantic/bounds rejection -> 2
 *   NOTIMPL -> 2 | evaluation trap (div0) -> 129 | success -> 0.
 * The cache changes only on defined final outcomes, never on RUNNING
 * polls. `status` prints the cache without changing it.
 *
 * Transcript rows (all shell prints use these exact forms):
 *   [SH] ready | [SH] prompt | [SH] wantkey N | [SH] done status=N
 *   [SH] spawned a=S,G[, b=S,G] | [SH] reaped S,G STATE CODE
 *   [SH] overlap 1 | [SH] abort line | [SH] abort child status=N
 *   [SH] abort pipeline status=N | [SH] error <class>
 *   [SH] script PATH | [SH] script done status=N
 *   [RL] reject <class> | [RL] notimpl <kw> | [RL] trap div0
 *   [RL] error <class> | [RL] ownership-fail
 *   [RL] stats sess_live=N sess_high=N sub_live=N sub_high=N
 *            syms=N src=N   (after evaluator submissions only)
 * Typed input is echoed; child output passes through raw.
 */
#include "rt.h"
#include "rt_pipe.h"
#include "sh_key.h"
#include "sh_parse.h"
#include "rl_sem.h"

/* Frozen value mirrors (kernel/include/uapi.h + syscall domain;
   pinned equal by test; user builds never include kernel headers). */
#define SH_OK 0u
#define SH_AGAIN 1u
#define SH_INVAL 2u
#define SH_NOTFOUND 3u
#define SH_MALFORMED 4u
#define SH_BADHANDLE 5u
#define SH_BUSY 6u
#define SH_NOMEM 7u
#define SH_BADARG 8u
#define SH_GONE 9u
#define SH_IOERR 10u
#define SH_RUNNING 0u
#define SH_EXITED 1u
#define SH_FAULTED 2u
#define SH_ABORTED 3u
#define SH_STDIN_CLOSED 0u
#define SH_STDIN_KBD 1u
#define SH_STDIN_PIPE 2u
#define SH_STDOUT_SERIAL 0u
#define SH_STDOUT_PIPE 1u

/* Shell bounds (§43; data window keeps headroom for Slice F). */
#define SH_LINE_MAX 256
#define SH_SCRIPT_MAX 4096
#define SH_READ_CHUNK 512
#define SH_POLL_MAX 1000000u

/* Shell status codes (policy above). */
#define SH_ST_FAULT 129u
#define SH_ST_ABORT 130u
#define SH_ST_NOTFOUND 127u
#define SH_ST_MALFORMED 126u
#define SH_ST_SPAWNERR 125u
#define SH_ST_SYNTAX 2u

extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

static unsigned long long sh_sys_spawn(const struct rt_spawn_spec *spec,
                                       unsigned long long *handle_out)
{
    return rt_gate6(RT_SYS_SPAWN, (unsigned long long)spec,
                    (unsigned long long)handle_out, 0, 0, 0, 0);
}

static unsigned long long sh_sys_spawn_pipe(const struct rt_spawn_spec *a,
                                            const struct rt_spawn_spec *b,
                                            unsigned long long *ha,
                                            unsigned long long *hb)
{
    return rt_gate6(RT_SYS_SPAWN_PIPE, (unsigned long long)a,
                    (unsigned long long)b, (unsigned long long)ha,
                    (unsigned long long)hb, 0, 0);
}

static unsigned long long sh_sys_wait(unsigned long long h,
                                      struct rt_proc_status *st)
{
    return rt_gate6(RT_SYS_WAIT, h, (unsigned long long)st, 0, 0, 0, 0);
}

static unsigned long long sh_sys_terminate(unsigned long long h)
{
    return rt_gate6(RT_SYS_TERMINATE, h, 0, 0, 0, 0, 0);
}

/* ---- output helpers ---- */

static void sh_write(const char *s, unsigned long long n)
{
    unsigned long long at = 0;
    if (n == 0 || s == 0) return;
    while (at < n) {
        unsigned long long chunk = n - at;
        if (chunk > RT_WRITE_MAX) chunk = RT_WRITE_MAX;
        if (rt_write(RT_FD_STDOUT, s + at, chunk) != RT_OK) return;
        at += chunk;
    }
}

static unsigned long long sh_strlen(const char *s, unsigned long long cap)
{
    unsigned long long n = 0;
    while (n < cap && s[n] != 0) ++n;
    return n;
}

static void sh_print(const char *s) { sh_write(s, sh_strlen(s, 8192)); }

/* Atomic row printer: shell rows ([SH]/[SHD]) are assembled in a
   stack buffer and emitted with ONE write, so concurrent child
   output (same [LOAD] channel) can only interleave BETWEEN rows,
   never inside one. Every row fits easily; overflow truncates
   defensively (unreachable by construction). */
static char rowbuf[256];
static unsigned int rowlen;

static void row_begin(void) { rowlen = 0; }

static void row_str(const char *s)
{
    while (*s != 0 && rowlen < sizeof(rowbuf))
        rowbuf[rowlen++] = *s++;
}

static void row_num(unsigned long long v)
{
    char b[21];
    unsigned int i = 20;
    b[i] = 0;
    do {
        b[--i] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v);
    row_str(b + i);
}

static void row_handle(unsigned long long h)
{
    row_num((unsigned int)(h & 0xffffffffULL));
    row_str(",");
    row_num(h >> 32);
}

static void row_flush(void) { sh_write(rowbuf, rowlen); }

static void row_simple(const char *s)
{
    row_begin();
    row_str(s);
    row_flush();
}

/* ---- shell state ---- */

static struct shk_state kbd;
static char line[SH_LINE_MAX + 1];
static unsigned int linelen;
static unsigned int line_submitted; /* Enter received; pump owns line */
static unsigned long long line_pos; /* pump cursor into line */
static char script[SH_SCRIPT_MAX + 1];
static unsigned long long script_len;
static unsigned long long script_pos;
static int script_mode;
static int script_aborted;
static unsigned long long last_status;
static unsigned long long wantkey;
static unsigned int wantkey_printed;
/* Foreground children: 0, 1, or 2 live handles (second only pipes). */
static unsigned long long fh[2];
static unsigned int nfh;
static int fh_abort; /* a terminate actually killed (>= 1 live handle) */
static int overlap_seen;
static unsigned long long final_status;
static int final_kind; /* 0 none, 1 done, 2 abort-child, 3 abort-pipe */

static unsigned long long map_child(unsigned int state, unsigned long long code)
{
    if (state == SH_EXITED) return code & 0xffu;
    if (state == SH_FAULTED) return SH_ST_FAULT;
    return SH_ST_ABORT;
}

static unsigned long long map_spawn(unsigned long long rc)
{
    if (rc == SH_NOTFOUND) return SH_ST_NOTFOUND;
    if (rc == SH_MALFORMED) return SH_ST_MALFORMED;
    return SH_ST_SPAWNERR;
}

static void emit_prompt(void)
{
    row_simple("[SH] prompt\r\n");
    wantkey_printed = 0;
}

static void emit_done(void)
{
    if (overlap_seen) {
        row_simple("[SH] overlap 1\r\n");
        overlap_seen = 0;
    }
    if (final_kind == 1) {
        row_begin();
        row_str("[SH] done status=");
        row_num(final_status);
        row_str("\r\n");
        row_flush();
    } else if (final_kind == 2) {
        row_begin();
        row_str("[SH] abort child status=");
        row_num(final_status);
        row_str("\r\n");
        row_flush();
    } else if (final_kind == 3) {
        row_begin();
        row_str("[SH] abort pipeline status=");
        row_num(final_status);
        row_str("\r\n");
        row_flush();
    }
    final_kind = 0;
}

static void clear_line(void)
{
    unsigned int i;
    linelen = 0;
    line_pos = 0;
    line_submitted = 0;
    for (i = 0; i <= SH_LINE_MAX; ++i) line[i] = 0;
}

static void build_spec(struct rt_spawn_spec *spec, struct rt_user_arg *vec,
                       const char *path, unsigned long long path_len,
                       unsigned int argc, const char *const *argv,
                       unsigned int stdin_sel, unsigned int stdout_sel)
{
    unsigned int i;
    spec->path_ptr = (unsigned long long)path;
    spec->path_len = path_len;
    spec->args_ptr = (unsigned long long)vec;
    spec->nargs = argc;
    spec->stdin_sel = stdin_sel;
    spec->stdout_sel = stdout_sel;
    spec->stderr_sel = 0;
    spec->file_in = 0;
    spec->file_out = 0;
    spec->file_err = 0;
    for (i = 0; i < 4; ++i) spec->reserved[i] = 0;
    for (i = 0; i < argc; ++i) {
        vec[i].ptr = (unsigned long long)argv[i];
        vec[i].len = sh_strlen(argv[i], 256);
    }
}

/* Start one parsed command (shared single/pipe tail). Path length is
   CPL3-checked (the kernel would BADARG it; obvious cases fail here
   with the stable shell error). */
static unsigned long long cmd_path_len(const char *p)
{
    return sh_strlen(p, 65);
}

static void reap_children(void);

static void exec_single(struct shp_cmd *cmd)
{
    struct rt_spawn_spec spec;
    struct rt_user_arg vec[SHP_MAX_ARGS];
    const char *argv[SHP_MAX_ARGS];
    unsigned long long h = 0, rc, plen;
    unsigned int i;
    for (i = 0; i < cmd->argc; ++i) argv[i] = cmd->argv[i];
    plen = cmd_path_len(argv[0]);
    if (plen == 0 || plen > 32) {
        sh_print("[SH] error args\r\n");
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        return;
    }
    build_spec(&spec, vec, argv[0], plen, cmd->argc, argv,
               SH_STDIN_CLOSED, SH_STDOUT_SERIAL);
    rc = sh_sys_spawn(&spec, &h);
    if (rc != SH_OK) {
        row_begin();
        row_str("[SH] error spawn ");
        row_num(rc);
        row_str("\r\n");
        row_flush();
        last_status = map_spawn(rc);
        final_status = last_status;
        final_kind = 1;
        return;
    }
    row_begin();
    row_str("[SH] spawned a=");
    row_handle(h);
    row_str("\r\n");
    row_flush();
    fh[0] = h;
    nfh = 1;
    fh_abort = 0;
}

static void exec_pipe(struct shp_cmd *a, struct shp_cmd *b)
{
    struct rt_spawn_spec sa, sb;
    struct rt_user_arg va[SHP_MAX_ARGS], vb[SHP_MAX_ARGS];
    const char *aa[SHP_MAX_ARGS], *ab[SHP_MAX_ARGS];
    unsigned long long ha = 0, hb = 0, rc, lena, lenb;
    unsigned int i;
    for (i = 0; i < a->argc; ++i) aa[i] = a->argv[i];
    for (i = 0; i < b->argc; ++i) ab[i] = b->argv[i];
    lena = cmd_path_len(aa[0]);
    lenb = cmd_path_len(ab[0]);
    if (lena == 0 || lena > 32 || lenb == 0 || lenb > 32) {
        sh_print("[SH] error args\r\n");
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        return;
    }
    build_spec(&sa, va, aa[0], lena, a->argc, aa,
               SH_STDIN_CLOSED, SH_STDOUT_PIPE);
    build_spec(&sb, vb, ab[0], lenb, b->argc, ab,
               SH_STDIN_PIPE, SH_STDOUT_SERIAL);
    rc = sh_sys_spawn_pipe(&sa, &sb, &ha, &hb);
    if (rc != SH_OK) {
        row_begin();
        row_str("[SH] error spawn ");
        row_num(rc);
        row_str("\r\n");
        row_flush();
        last_status = map_spawn(rc);
        final_status = last_status;
        final_kind = 1;
        return;
    }
    row_begin();
    row_str("[SH] spawned a=");
    row_handle(ha);
    row_str(" b=");
    row_handle(hb);
    row_str("\r\n");
    row_flush();
    fh[0] = ha;
    fh[1] = hb;
    nfh = 2;
    fh_abort = 0;
}

/* Execute one parsed statement (already fully validated). */
static void exec_parsed(struct shp_line *parsed)
{
    if (parsed->status_only) {
        row_begin();
        row_num(last_status);
        row_str("\r\n");
        row_flush();
        final_status = last_status;
        final_kind = 1;
        return;
    }
    if (parsed->ncmds == 1) {
        exec_single(&parsed->cmds[0]);
        return;
    }
    exec_pipe(&parsed->cmds[0], &parsed->cmds[1]);
}

/* Frozen Slice E statement step over an explicit span (shared by
 * file scripts and interactive E-command parts alike). */
static void pump_e_span(const char *base, unsigned long long len,
                        unsigned long long *pos)
{
    for (;;) {
        struct shp_line parsed;
        unsigned long long used = 0;
        int rc;
        if (*pos >= len) return;
        rc = shp_parse_stmt(base + *pos, len - *pos, &parsed, &used);
        if (used == 0) {
            /* Progress guarantee: the parser reports a first-byte
               rejection without consuming input (used stays 0 on
               every error path), which would re-parse the same byte
               forever — a submitted line and a script never grow, so
               waiting is a silent hang. Consume through the end of
               the failed statement (one separator, like a successful
               parse); the loud syntax error below follows, and `;`
               sequencing after an error is preserved. */
            unsigned long long q = *pos;
            while (q < len && base[q] != ';' && base[q] != '\n' &&
                   base[q] != '\r')
                ++q;
            if (q < len) ++q;
            used = q - *pos;
        }
        *pos += used;
        if (rc == SHP_EMPTY) continue;
        if (rc != SHP_OK) {
            sh_print("[SH] error syntax\r\n");
            last_status = SH_ST_SYNTAX;
            final_status = last_status;
            final_kind = 1;
            return;
        }
        exec_parsed(&parsed);
        return;
    }
}

/* Split one top-level ';' part (string-, brace-, and comment-aware;
 * mirrors Slice E statement boundaries for command text). Comment
 * rule matches shp_parse_stmt: `//` opens a comment where a token
 * may begin (start/blank/separator); mid-word slashes stay literal
 * here and Slice E re-parses the part verbatim anyway. Sets
 * start/slen (trailing blanks trimmed), advances *pos past the part
 * and one separator. Returns 0 when nothing remains. */
static int split_part(const char *base, unsigned long long len,
                      unsigned long long *pos, unsigned long long *start,
                      unsigned long long *slen)
{
    unsigned long long i = *pos, st, depth = 0;
    int in_str = 0, esc = 0;
    while (i < len && (base[i] == ' ' || base[i] == '\t' ||
                       base[i] == '\r' || base[i] == '\n'))
        ++i;
    if (i >= len) {
        *pos = len;
        return 0;
    }
    st = i;
    while (i < len) {
        char c = base[i];
        if (in_str) {
            if (esc) esc = 0;
            else if (c == '\\') esc = 1;
            else if (c == '"') in_str = 0;
            ++i;
            continue;
        }
        if (c == '"') {
            in_str = 1;
            ++i;
            continue;
        }
        if (c == '/' && i + 1 < len && base[i + 1] == '/') {
            char p = (i == st) ? ' ' : base[i - 1];
            if (p == ' ' || p == '\t' || p == '\r' || p == '\n' ||
                p == ';') {
                while (i < len && base[i] != '\n') ++i;
                continue;
            }
            ++i;
            continue;
        }
        if (c == '{') {
            ++depth;
            ++i;
            continue;
        }
        if (c == '}') {
            if (depth) --depth;
            ++i;
            continue;
        }
        if (c == ';' && depth == 0) break;
        ++i;
    }
    *start = st;
    *slen = i > st ? i - st : 0;
    while (*slen > 0) {
        char t = base[st + *slen - 1];
        if (t != ' ' && t != '\t' && t != '\r' && t != '\n') break;
        (*slen)--;
    }
    *pos = (i < len && base[i] == ';') ? i + 1 : i;
    return 1;
}

/* Part classification (interactive lines only; scripts stay Slice E).
 * RLF_E funnels verbatim into the proven executor (identical
 * rows/statuses by construction, never reinterpreted); RLF_LANG
 * evaluates in the resident evaluator; RLF_NOTIMPL rejects blocked
 * constructs loudly. Anything the language parser refuses falls
 * back to RLF_E, so non-language text behaves exactly like Slice E.
 */
#define RLF_E 0
#define RLF_LANG 1
#define RLF_NOTIMPL 2
#define RLF_EMPTY 3

static int rl_classify(const char *pt, unsigned long long pn,
                       unsigned int *kw)
{
    /* Classify via the language lexer/parser on submission scratch
     * (reset before return on every path). */
    struct rl_lex lx;
    struct rl_strscratch ss;
    struct rl_tok t0;
    int lrc;
    char sscratch[8];
    struct rl_ppool pp;
    struct rl_proot proot;
    struct rl_node *pool;
    char *strb;
    unsigned short *args;
    unsigned int *ops;
    unsigned short *vals;
    unsigned int argused = 0;
    struct rl_node *root;
    unsigned int haves;
    *kw = 4;
    if (!pt || pn == 0u || pn > 512u) return RLF_EMPTY;
    /* Head-keyword scan (one token, kinds only). */
    rl_lex_init(&lx, pt, (unsigned int)pn);
    ss.base = sscratch;
    ss.cap = sizeof(sscratch);
    ss.used = 0;
    rl_lex_next(&lx, &ss, &t0, &lrc);
    if (lrc != RLL_OK) return RLF_E;
    if (t0.kind == RLT_LET) return RLF_LANG;
    if (t0.kind == RLT_FN || t0.kind == RLT_IF ||
        t0.kind == RLT_WHILE || t0.kind == RLT_RETURN) {
        *kw = (t0.kind == RLT_FN)       ? 0
              : (t0.kind == RLT_IF)     ? 1
              : (t0.kind == RLT_WHILE)  ? 2
                                        : 3;
        return RLF_NOTIMPL;
    }
    if (t0.kind == RLT_LBRACE) {
        *kw = 4;
        return RLF_NOTIMPL;
    }
    /* Full language parse on submission scratch. */
    {
        struct rl_carve cv;
        if (!rl_carve(&cv)) {
            rl_sub_reset();
            return RLF_E;
        }
        pool = cv.pool;
        strb = cv.strb;
        args = cv.args;
        ops = cv.ops;
        vals = cv.vals;
    }
    pp.nodes = pool;
    pp.cap = RL_SUB_POOL_NODES;
    pp.used = 0;
    {
        struct rl_strscratch pss;
        pss.base = strb;
        pss.cap = RL_SUB_STRB;
        pss.used = 0;
        rl_parse_part(pt, (unsigned int)pn, &pp, &pss, args,
                      RL_SUB_ARGS, &argused, ops, RL_SUB_OPS, vals,
                      RL_SUB_PVALS, &proot);
    }
    haves = (proot.rc == RLP_OK && proot.root < RL_POOL_MAX);
    if (haves) {
        unsigned char k;
        root = &pool[proot.root];
        k = root->kind;
        if (k == RLN_CMD) {
            /* Lone declared word evaluates as a variable (rule a:
             * lexical meaning first); every other command shape
             * funnels verbatim into Slice E. */
            int declared = 0;
            if (root->aux == 0u) {
                unsigned int off = root->e1 & 0xFFFFu;
                unsigned int len = (root->e1 >> 16) & 0xFFFFu;
                if (off < pn && len <= pn - off)
                    declared = rl_declared(pt + off, len);
            }
            rl_sub_reset();
            return declared ? RLF_LANG : RLF_E;
        }
        if (k == RLN_PIPE) {
            struct rl_node *a = &pool[root->k1];
            struct rl_node *b = &pool[root->k2];
            int allcmd = 0, i;
            /* All-command pipelines funnel to Slice E, unless a
             * stage is a lone declared word (language meaning
             * first, uniformly with the singleton rule). */
            if (root->k1 < RL_POOL_MAX && root->k2 < RL_POOL_MAX &&
                a->kind == RLN_CMD && b->kind == RLN_CMD) {
                unsigned short stages[2];
                allcmd = 1;
                stages[0] = root->k1;
                stages[1] = root->k2;
                for (i = 0; i < 2; ++i) {
                    struct rl_node *st = &pool[stages[i]];
                    if (st->aux == 0u) {
                        unsigned int off = st->e1 & 0xFFFFu;
                        unsigned int len =
                            (st->e1 >> 16) & 0xFFFFu;
                        if (off < pn && len <= pn - off &&
                            rl_declared(pt + off, len)) {
                            allcmd = 0;
                            break;
                        }
                    }
                }
            }
            rl_sub_reset();
            if (allcmd) return RLF_E;
            return RLF_LANG;
        }
        if (k == RLN_LET) {
            rl_sub_reset();
            return RLF_LANG;
        }
        if (k == RLN_VAR) {
            const char *nm;
            int declared;
            if (root->e1 >= pn || root->e2 > pn - root->e1) {
                rl_sub_reset();
                return RLF_E;
            }
            nm = pt + root->e1;
            declared = rl_declared(nm, root->e2);
            rl_sub_reset();
            return declared ? RLF_LANG : RLF_E;
        }
        rl_sub_reset();
        return RLF_LANG;
    }
    rl_sub_reset();
    return RLF_E;
}

static void emit_rl_stats(void);

/* Execute one language part (transactional submit + rows). */
static void pump_lang(const char *pt, unsigned long long pn)
{
    struct rl_outcome o = rl_submit(pt, (unsigned int)pn);
    switch (o.kind) {
    case RL_SUB_OK:
        last_status = 0;
        final_status = 0;
        final_kind = 1;
        break;
    case RL_SUB_SYNTAX:
        sh_print("[SH] error syntax\r\n");
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        break;
    case RL_SUB_REJECT:
        row_begin();
        row_str("[RL] reject ");
        row_str(o.diag ? o.diag : RL_D_INTERNAL);
        row_str("\r\n");
        row_flush();
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        break;
    case RL_SUB_NOTIMPL:
        row_begin();
        row_str("[RL] notimpl ");
        row_str(o.diag ? o.diag : "block");
        row_str("\r\n");
        row_flush();
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        break;
    case RL_SUB_TRAP:
        row_simple("[RL] trap div0\r\n");
        last_status = SH_ST_FAULT;
        final_status = last_status;
        final_kind = 1;
        break;
    case RL_SUB_ARENAFULL:
        row_begin();
        row_str("[RL] error ");
        row_str(RL_D_ARENA_FULL);
        row_str("\r\n");
        row_flush();
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        break;
    case RL_SUB_OWNFAIL:
        row_simple("[RL] ownership-fail\r\n");
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        break;
    default:
        row_begin();
        row_str("[RL] error ");
        row_str(RL_D_INTERNAL);
        row_str("\r\n");
        row_flush();
        last_status = SH_ST_SYNTAX;
        final_status = last_status;
        final_kind = 1;
        break;
    }
    emit_rl_stats();
}

static void emit_rl_stats(void)
{
    struct rl_stats st;
    rl_stats(&st);
    row_begin();
    row_str("[RL] stats sess_live=");
    row_num(st.sess_live);
    row_str(" sess_high=");
    row_num(st.sess_high);
    row_str(" sub_live=");
    row_num(st.sub_live);
    row_str(" sub_high=");
    row_num(st.sub_high);
    row_str(" syms=");
    row_num(st.nsyms);
    row_str(" src=");
    row_num(st.srclen);
    row_str("\r\n");
    row_flush();
}

static void pump_notimpl(unsigned int kw)
{
    static const char *names[5] = {"fn", "if", "while", "return",
                                   "block"};
    row_begin();
    row_str("[RL] notimpl ");
    row_str(names[kw < 5u ? kw : 4u]);
    row_str("\r\n");
    row_flush();
    last_status = SH_ST_SYNTAX;
    final_status = last_status;
    final_kind = 1;
    /* Leak-walk observability for rejected submissions (E paths
     * print no new rows, so Slice E transcripts stay identical). */
    emit_rl_stats();
}

/* Statement pump: scripts feed the frozen Slice E span pump;
 * interactive lines split into classified parts. Parses and starts
 * at most one foreground action per call; empty statements are
 * skipped silently. Unsubmitted interactive lines never execute. */
static void pump(void)
{
    if (nfh != 0 || final_kind != 0) return;
    if (script_mode) {
        if (script_aborted) return;
        pump_e_span(script, script_len, &script_pos);
        return;
    }
    if (!line_submitted) return;
    for (;;) {
        unsigned long long start, slen;
        int kind;
        unsigned int kw = 4;
        if (line_pos >= linelen) return;
        if (!split_part(line, linelen, &line_pos, &start, &slen))
            return;
        if (slen == 0u) continue;
        kind = rl_classify(line + start, slen, &kw);
        if (kind == RLF_E) {
            unsigned long long ppos = 0;
            pump_e_span(line + start, slen, &ppos);
            return;
        }
        if (kind == RLF_LANG) {
            pump_lang(line + start, slen);
            return;
        }
        if (kind == RLF_EMPTY) continue;
        pump_notimpl(kw);
        return;
    }
}

/* Poll foreground children once. Returns 1 if waiting continues,
 * 0 when fully reaped (final_* latched). RUNNING never touches the
 * cached status (E-M15). Overlap latches when both pipe sides are
 * observed RUNNING together after start. */
static int poll_children(void)
{
    unsigned int i, live = 0, term = 0;
    static unsigned long long tstate[2];
    static unsigned long long tcode[2];
    static int have_term[2] = {0, 0};
    if (nfh == 0) return 0;
    for (i = 0; i < nfh; ++i) {
        struct rt_proc_status st;
        unsigned long long rc;
        if (have_term[i]) {
            term++;
            continue;
        }
        st.state = 99;
        st.code = 0;
        st.detail = 0;
        st.reserved = 0;
        rc = sh_sys_wait(fh[i], &st);
        if (rc != SH_OK) {
            /* Unreachable: handles are retained until first terminal
               observation, and the kernel consumes exactly once. */
            have_term[i] = 1;
            tstate[i] = SH_ABORTED;
            tcode[i] = 0;
            term++;
            continue;
        }
        if (st.state == SH_RUNNING) {
            live++;
            continue;
        }
        have_term[i] = 1;
        tstate[i] = st.state;
        tcode[i] = st.code;
        term++;
        row_begin();
        row_str("[SH] reaped ");
        row_handle(fh[i]);
        row_str(st.state == SH_EXITED ? " exited " :
                st.state == SH_FAULTED ? " faulted " : " aborted ");
        row_num(st.code);
        row_str("\r\n");
        row_flush();
    }
    if (live > 1) overlap_seen = 1;
    if (term < nfh) return 1;
    /* All reaped: finalize exactly once. */
    if (nfh == 1) {
        final_status = map_child((unsigned int)tstate[0], tcode[0]);
    } else {
        /* Pipeline rule: right-hand (consumer) status. */
        final_status = map_child((unsigned int)tstate[1], tcode[1]);
    }
    if (fh_abort)
        final_status = SH_ST_ABORT;
    final_kind = fh_abort ? (nfh == 2 ? 3 : 2) : 1;
    last_status = final_status;
    nfh = 0;
    have_term[0] = 0;
    have_term[1] = 0;
    return 0;
}

static void reap_children(void)
{
    unsigned long long spins = 0;
    while (poll_children()) {
        if (++spins > SH_POLL_MAX) {
            sh_print("[SH] error wait-stuck\r\n");
            last_status = SH_ST_SPAWNERR;
            final_status = last_status;
            final_kind = 1;
            nfh = 0;
            return;
        }
        if (rt_nap(1) != RT_OK) return;
    }
}

/* Ctrl-C action by foreground state. Editing: discard the whole
 * submission, marker, new prompt, status preserved. Foreground work:
 * terminate live handles (ALREADY_GONE tolerated), reap, then the
 * abort outcome only if some terminate actually killed (else the
 * natural result stands — the E-C4 race rule); the remainder of the
 * submitted line is always discarded. */
static void rl_emit_wrap(const char *s, unsigned int n)
{
    sh_write(s, n);
}

static void ctrl_c(void)
{
    unsigned int i, killed = 0;
    sh_print("^C\r\n");
    /* Slice F: no candidate can be live here (language evaluation
     * is synchronous, commands stage nothing), but every abort path
     * resets submission scratch unconditionally. */
    rl_sub_reset();
    if (nfh == 0) {
        clear_line();
        sh_print("[SH] abort line\r\n");
        if (!script_mode) emit_prompt();
        return;
    }
    for (i = 0; i < nfh; ++i) {
        unsigned long long rc = sh_sys_terminate(fh[i]);
        if (rc == SH_OK) killed = 1;
    }
    fh_abort = killed ? 1 : 0;
    reap_children();
    clear_line();
    if (script_mode) {
        /* A Ctrl-C during a script aborts the script after reaping. */
        emit_done();
        script_aborted = 1;
        return;
    }
    emit_done();
    emit_prompt();
}

static void submit_line(void)
{
    sh_print("\r\n");
    line_submitted = 1;
    line_pos = 0;
}

/* Decode one raw byte into editor/executor actions. Returns 1 for a
   key event (drives harness pacing), 0 for silent bytes (breaks,
   prefixes, LOST): pacing counts KEYS, never raw bytes, so
   make/break timing can never desynchronize the marker stream. */
static int on_byte(unsigned char b)
{
    struct shk_event ev;
    if (!shk_feed(&kbd, b, &ev)) return 0;
    if (ev.kind == SHK_NONE) return 0;
    if (ev.kind == SHK_CTRL_C) {
        ctrl_c();
        return 1;
    }
    /* While a child runs, after submission, or in script mode, only
       Ctrl-C acts on keyboard text (no Slice E type-ahead: typing
       never mutates a submitted line; deterministic). Discards do not
       advance pacing. */
    if (nfh != 0 || script_mode || line_submitted) return 0;
    if (ev.kind == SHK_ENTER) {
        submit_line();
        return 1;
    }
    if (ev.kind == SHK_BSPACE) {
        if (linelen > 0) {
            linelen--;
            line[linelen] = 0;
            sh_print("\b \b");
        }
        return 1;
    }
    if (ev.kind == SHK_CHAR) {
        if (linelen >= SH_LINE_MAX) {
            sh_print("[SH] error line-too-long\r\n");
            last_status = SH_ST_SYNTAX;
            final_status = last_status;
            final_kind = 1;
            emit_done();
            clear_line();
            emit_prompt();
            return 1;
        }
        line[linelen++] = ev.chr;
        line[linelen] = 0;
        sh_write(&ev.chr, 1);
        return 1;
    }
    return 0;
}

/* Idle pacing: a bounded micro-spin (no gates) before yielding, so
   an interactive shell idles at human timescales instead of flooding
   the CPU trace with poll gates. Always followed by a real yield
   (workers still scheduled promptly); transfer-active iterations
   skip it for tight reap/Ctrl-C latency. */
static void idle_pause(void)
{
    volatile unsigned long long i = 0;
    while (i < 200000u) ++i;
}

/* Drain available raw input once. Returns key events consumed
   (pacing units, not bytes). */
static unsigned long long drain_input(void)
{
    static unsigned char buf[SH_READ_CHUNK];
    unsigned long long n = 0xAAAAAAAAAAAAAAAAULL, got = 0;
    enum rt_err rc = rt_fd_read(RT_FD_STDIN, buf, sizeof(buf), &n, 0);
    unsigned long long k;
    if (rc == RT_AGAIN) return 0;
    if (rc != RT_OK) return 0;
    for (k = 0; k < n; ++k)
        got += (unsigned long long)on_byte(buf[k]);
    return got;
}

/* Load a script file through stateless fread into the bounded buffer.
 * Returns 1 with script_len set, or prints a marker and returns 0. */
static int load_script(const char *path)
{
    unsigned long long plen = 0, off = 0;
    while (plen < 33 && path[plen] != 0) ++plen;
    if (plen == 0 || plen > 32) {
        sh_print("[SH] error script-path\r\n");
        last_status = SH_ST_SYNTAX;
        return 0;
    }
    for (;;) {
        unsigned long long n = 0xAAAAAAAAAAAAAAAAULL;
        unsigned long long want = SH_SCRIPT_MAX - off;
        enum rt_err rc;
        if (want == 0) {
            sh_print("[SH] error script-too-large\r\n");
            last_status = SH_ST_SYNTAX;
            return 0;
        }
        if (want > RT_FREAD_MAX) want = RT_FREAD_MAX;
        rc = rt_fread(path, plen, off, script + off, want, &n);
        if (rc != RT_OK) {
            sh_print("[SH] error script-read\r\n");
            last_status = SH_ST_SYNTAX;
            return 0;
        }
        off += n;
        if (n < want) break;
    }
    script_len = off;
    script[off] = 0;
    return 1;
}

int rt_main(int argc, char **argv)
{
    if (argc < 0 || argc > 1) rt_exit(64);
    script_mode = 0;
    script_aborted = 0;
    last_status = 0;
    wantkey = 0;
    wantkey_printed = 0;
    nfh = 0;
    fh_abort = 0;
    overlap_seen = 0;
    final_kind = 0;
    script_len = 0;
    script_pos = 0;
    clear_line();
    rl_sess_init(rl_emit_wrap);
    if (argc == 1) {
        unsigned long long L = 0;
        script_mode = 1;
        while (L < 33 && argv[0][L] != 0) ++L;
        if (L == 0 || L > 32) {
            sh_print("[SH] error script-path\r\n");
            last_status = SH_ST_SYNTAX;
            rt_exit(2);
        }
        row_begin();
        row_str("[SH] script ");
        row_str(argv[0]);
        row_str("\r\n");
        row_flush();
        if (!load_script(argv[0])) {
            row_begin();
            row_str("[SH] script done status=");
            row_num(last_status);
            row_str("\r\n");
            row_flush();
            rt_exit((int)last_status);
        }
    } else {
        sh_print("[SH] ready\r\n");
        emit_prompt();
    }
    for (;;) {
        unsigned long long progress = 0, keys = 0;
        /* Pacing marker for the harness: printed only when ready for
           fresh typing (no submitted line, no child, interactive).
           The harness sends exactly one key per marker. */
        if (!script_mode && !line_submitted && nfh == 0 &&
            !wantkey_printed) {
            row_begin();
            row_str("[SH] wantkey ");
            row_num(wantkey);
            row_str("\r\n");
            row_flush();
            wantkey_printed = 1;
        }
        keys = drain_input();
        progress += keys;
        /* Statement pump (starts foreground work when idle). */
        pump();
        if (nfh != 0) {
            if (!poll_children()) {
                /* Fully reaped: outcome row below. */
                emit_done();
            } else {
                progress++;
            }
        } else if (final_kind != 0) {
            /* Synchronous outcome (empty/builtin/error/spawn-fail). */
            emit_done();
            progress++;
        }
        /* Settle: fully idle with nothing pending. */
        if (nfh == 0 && final_kind == 0) {
            if (script_mode) {
                if (script_aborted || script_pos >= script_len) {
                    row_begin();
                    row_str("[SH] script done status=");
                    row_num(last_status);
                    row_str("\r\n");
                    row_flush();
                    rt_exit((int)last_status);
                }
            } else if (line_submitted && line_pos >= linelen) {
                clear_line();
                emit_prompt();
            }
        }
        /* Pacing: consumed keys retire their marker. */
        if (keys > 0) {
            wantkey += keys;
            wantkey_printed = 0;
        }
        /* Cooperative pacing: truly idle iterations (no keys, no
           live children, no fresh outcome) spin briefly WITHOUT
           gates, then always yield. Busy work (transfers, reaps)
           never spins, so completion latency stays tight. */
        if (progress == 0 && nfh == 0 && final_kind == 0)
            idle_pause();
        if (rt_nap(1) != RT_OK) rt_exit(66);
    }
    return 0;
}
