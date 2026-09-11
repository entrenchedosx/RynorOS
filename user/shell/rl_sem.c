/* Stage 18d Slice F session/transaction/arenas. See rl_sem.h.
 *
 * Submission memory map (RL_SUB_ARENA bytes, carved per item):
 *   pool    256 nodes x 16 B            (parse trees)
 *   strb    decoded string bytes        (bounded by part length)
 *   args    128 command-arg u16 indices
 *   aux     phased scratch: parse op/val stacks, analyzer walk/type
 *           stacks, evaluator walk/value stacks (sequential overlay).
 */
#include "rl_sem.h"
#include "rl_eval.h"

/* Static session (bss; all counters zero-init to a valid empty). */
static struct rl_sess rl_sess;
static struct rl_arena rl_sub;

/* Staged-row flags (pre-commit only; never persist). */
#define RL_SPTR 4u
#define RL_PARTNAME 2u


void rl_sess_init(void (*emit)(const char *s, unsigned int n))
{
    unsigned int i;
    for (i = 0; i < sizeof(rl_sess.src); ++i) rl_sess.src[i] = 0;
    rl_sess.srclen = 0;
    rl_sess.nsyms = 0;
    rl_sess.ncand = 0;
    for (i = 0; i < sizeof(rl_sess.sess_arena); ++i)
        rl_sess.sess_arena[i] = 0;
    rl_sess.sess_used = 0;
    rl_sess.sess_high = 0;
    for (i = 0; i < sizeof(rl_sess.sub_arena); ++i)
        rl_sess.sub_arena[i] = 0;
    rl_sess.sub_high = 0;
    rl_arena_init(&rl_sub, rl_sess.sub_arena, RL_SUB_ARENA);
    rl_sess.emit = emit;
}

void rl_sub_reset(void)
{
    rl_arena_reset(&rl_sub);
}

int rl_carve(struct rl_carve *out)
{
    unsigned int pool_off, strb_off, args_off, aux_off;
    if (!out) return 0;
    pool_off = rl_arena_bump(&rl_sub, RL_SUB_POOL_NODES * 16u);
    strb_off = rl_arena_bump(&rl_sub, RL_SUB_STRB);
    args_off = rl_arena_bump(&rl_sub, RL_SUB_ARGS * 2u);
    aux_off = rl_arena_bump(&rl_sub, RL_SUB_AUX);
    if (pool_off == ~0u || strb_off == ~0u || args_off == ~0u ||
        aux_off == ~0u)
        return 0;
    out->pool = (struct rl_node *)(rl_sub.base + pool_off);
    out->strb = (char *)(rl_sub.base + strb_off);
    out->args = (unsigned short *)(rl_sub.base + args_off);
    out->ops = (unsigned int *)(rl_sub.base + aux_off);
    out->vals = (unsigned short *)(rl_sub.base + aux_off + 1024u);
    return 1;
}

static int rl_lookup(const struct rl_sym *tab, unsigned int ntab,
                     const char *sess_src, const char *nm,
                     unsigned int nlen);

/* Session-declared test for the lone-word rule (content compare). */
int rl_declared(const char *nm, unsigned int nlen)
{
    if (!nm || nlen == 0u) return 0;
    return rl_lookup(rl_sess.syms, rl_sess.nsyms, rl_sess.src, nm,
                     nlen) >= 0;
}

void rl_stats(struct rl_stats *out)
{
    if (!out) return;
    out->sess_live = rl_sess.sess_used;
    out->sess_high = rl_sess.sess_high;
    out->sub_live = rl_sub.used;
    out->sub_high = rl_sub.high;
    out->nsyms = rl_sess.nsyms;
    out->srclen = rl_sess.srclen;
}

int rl_own_check_tab(const struct rl_sym *tab, unsigned int ntab,
                     unsigned int sess_used)
{
    unsigned int i;
    if (!tab && ntab > 0u) return 0;
    for (i = 0; i < ntab; ++i) {
        const struct rl_sym *s = &tab[i];
        if (s->type != RLV_STR) continue;
        if (s->flags & (RL_PARTNAME | RL_SPTR)) return 0;
        if (s->pay >= sess_used) return 0;
        if (s->str_len > sess_used - (unsigned int)s->pay) return 0;
    }
    return 1;
}

unsigned int rl_render(const struct rl_val *v, char *buf,
                       unsigned int cap)
{
    unsigned int n = 0, i;
    unsigned long long mag;
    int neg;
    char tmp[21];
    unsigned int tn = 0;
    if (!v || !buf || cap == 0u) return 0;
    if (v->type == RLV_BOOL) {
        const char *s = v->a ? "true" : "false";
        unsigned int l = v->a ? 4u : 5u;
        if (l > cap) return 0;
        for (i = 0; i < l; ++i) buf[i] = s[i];
        return l;
    }
    if (v->type == RLV_STR) {
        const char *p = (const char *)v->a;
        if (!p || v->len > cap) return 0;
        for (i = 0; i < v->len; ++i) buf[i] = p[i];
        return v->len;
    }
    if (v->type != RLV_INT) return 0;
    neg = (v->a & 0x8000000000000000ull) != 0u;
    mag = neg ? (0ull - v->a) : v->a;
    if (mag == 0u) {
        buf[0] = '0';
        return 1;
    }
    while (mag > 0u && tn < sizeof(tmp)) {
        tmp[tn++] = (char)('0' + mag % 10u);
        mag /= 10u;
    }
    if (mag > 0u) return 0;
    if (neg) {
        if (tn + 1u > cap) return 0;
        buf[n++] = '-';
    } else if (tn > cap) {
        return 0;
    }
    while (tn > 0u) buf[n++] = tmp[--tn];
    return n;
}

/* Bounded byte helpers (no libc). */
static int rl_memeq(const char *a, const char *b, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < n; ++i)
        if (a[i] != b[i]) return 0;
    return 1;
}

static int rl_is_ws(char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static int rl_is_alpha(unsigned char c)
{
    return c == '_' || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
}

static int rl_is_digit(unsigned char c)
{
    return c >= '0' && c <= '9';
}

/* Canonicalize a part: trim ASCII whitespace; the `;` splitter
 * guarantees no top-level semicolon remains inside. Reports the
 * leading trim width for name-offset rebasing at commit. */
static unsigned int rl_canon(const char *part, unsigned int len,
                             char *out, unsigned int cap,
                             unsigned int *trim_out)
{
    unsigned int a = 0, b = len, i;
    while (a < b && rl_is_ws(part[a])) ++a;
    while (b > a && rl_is_ws(part[b - 1])) --b;
    if (b - a + 1u > cap) return ~0u;
    for (i = 0; i < b - a; ++i) out[i] = part[a + i];
    out[b - a] = '\n';
    if (trim_out) *trim_out = a;
    return b - a + 1u;
}

static int rl_name_eq(const char *a, unsigned int alen, const char *b,
                      unsigned int blen)
{
    return alen == blen && rl_memeq(a, b, alen);
}

/* String-literal identity for diagnostic classes (pointer equality
 * is not guaranteed across translation units). */
static int rl_streq(const char *a, const char *b)
{
    unsigned int i = 0;
    if (a == b) return 1;
    if (!a || !b) return 0;
    while (a[i] != 0 && b[i] != 0 && i < 64u) {
        if (a[i] != b[i]) return 0;
        ++i;
    }
    return a[i] == b[i];
}

/* Reserved shell builtins (never declarable, never values). */
static int rl_reserved(const char *s, unsigned int n)
{
    if (n == 5 && s[0] == 'p' && s[1] == 'r' && s[2] == 'i' &&
        s[3] == 'n' && s[4] == 't')
        return 1;
    if (n == 6 && s[0] == 's' && s[1] == 't' && s[2] == 'a' &&
        s[3] == 't' && s[4] == 'u' && s[5] == 's')
        return 1;
    return 0;
}

static int rl_is_print(const char *s, unsigned int n)
{
    return n == 5 && s[0] == 'p' && s[1] == 'r' && s[2] == 'i' &&
        s[3] == 'n' && s[4] == 't';
}
/* Name lookup in a session-based table by content. All committed
 * rows slice session source (rebased at commit); the candidate name
 * is an explicit slice, so no per-row base tags are needed. */
static int rl_lookup(const struct rl_sym *tab, unsigned int ntab,
                     const char *sess_src, const char *nm,
                     unsigned int nlen)
{
    unsigned int i;
    for (i = 0; i < ntab; ++i) {
        const char *s = sess_src + tab[i].name_off;
        if (rl_name_eq(s, tab[i].name_len, nm, nlen)) return (int)i;
    }
    return -1;
}



/* ---- submission driver: rebuild, analyze, evaluate, commit ----
 *
 * Accepted session items are re-lexed/re-parsed/re-analyzed from
 * source text on every submission. Names/types/duplicates derive
 * from text each time (history can never shift them); values ride
 * the committed rows they were computed from (a deterministic
 * function of the same bytes). The candidate part is fully
 * analyzed AND evaluated. Commit (lets only, all-green) appends
 * canonical source, promotes the new string if any, and swaps the
 * table; anything else discards everything candidate-side. */

static unsigned char rl_an_sub(struct rl_node *pool, unsigned short root,
                               const char *text,
                               struct rl_sym *tab, unsigned int ntab,
                               const char *sess_src,
                               const unsigned short *argspace,
                               unsigned int argcap, unsigned short *walk,
                               unsigned int walkcap, unsigned char *types,
                               unsigned int typecap, const char **diag);
static struct rl_outcome rl_fail(int kind, const char *diag)
{
    struct rl_outcome o;
    o.kind = kind;
    o.diag = diag ? diag : RL_D_INTERNAL;
    o.val.type = RLV_UNIT;
    o.val.pad[0] = 0;
    o.val.pad[1] = 0;
    o.val.pad[2] = 0;
    o.val.len = 0;
    o.val.a = 0;
    return o;
}

struct rl_outcome rl_submit(const char *part, unsigned int len)
{
    struct rl_node *pool;
    char *strb;
    unsigned short *args;
    unsigned int *auxops;
    unsigned short *auxvals;
    unsigned short *auxwalk;
    unsigned char *auxtypes;
    struct rl_val *auxvstack;
    struct rl_ppool pp;
    struct rl_strscratch sstr;
    unsigned int argused = 0;
    struct rl_proot proot;
    struct rl_node *root_nd;
    int islet;
    /* Canonical buffer covers the largest submittable part (512+1,
     * not just keyboard lines: in-guest drivers submit long lets). */
    char canon[514];
    unsigned int canonlen = 0, trim = 0;
    unsigned int ncand = 0;
    unsigned int rpos = 0, ridx = 0;
    const char *diag = RL_D_INTERNAL;
    unsigned char itype;
    struct rl_eval_ctx ectx;
    struct rl_val ev;
    int evrc;
    struct rl_carve cv;

    if (!part || len == 0u || len > 512u) {
        rl_arena_reset(&rl_sub);
        return rl_fail(RL_SUB_SYNTAX, RL_D_PAR_LEX);
    }
    /* No reset here by design: every completion path below resets,
     * so a skipped reset (F-M4/F-M5 shape) accumulates observably
     * instead of being masked at the next start. */
    if (!rl_carve(&cv)) {
        rl_arena_reset(&rl_sub);
        return rl_fail(RL_SUB_ARENAFULL, RL_D_ARENA_FULL);
    }
    pool = cv.pool;
    strb = cv.strb;
    args = cv.args;
    auxops = cv.ops;
    auxvals = cv.vals;
    /* Phased aux overlay (sequential, never concurrent): parse uses
     * ops+vals; analysis uses walkstack+types; evaluation uses
     * walkstack+value stack. Peak is evaluation at 1024+1056. */
    auxwalk = (unsigned short *)cv.ops;
    auxtypes = (unsigned char *)cv.ops + 1024u;
    auxvstack = (struct rl_val *)((unsigned char *)cv.ops + 1024u);
    pp.nodes = pool;
    pp.cap = RL_SUB_POOL_NODES;
    pp.used = 0;
    sstr.base = strb;
    sstr.cap = RL_SUB_STRB;
    sstr.used = 0;

    /* Let-shaped parts append canonical source; detect cheaply
     * (exact `let` + boundary) and confirm by full parse below.
     * Everything else is a bare submission (no persistence). */
    islet = 0;
    if (len >= 3u && part[0] == 'l' && part[1] == 'e' &&
        part[2] == 't' &&
        (len == 3u || (!rl_is_alpha((unsigned char)part[3]) &&
                       !rl_is_digit((unsigned char)part[3]))))
        islet = 1;

    /* Canonical form + session-text cap (lets only). */
    if (islet) {
        canonlen = rl_canon(part, len, canon, sizeof(canon), &trim);
        if (canonlen == ~0u ||
            canonlen > RL_SESS_MAX ||
            rl_sess.srclen > RL_SESS_MAX - canonlen) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_REJECT, RL_D_SESSION_LIMIT);
        }
    }

    /* Whole-buffer rebuild over accepted items. */
    ncand = 0;
    rpos = 0;
    ridx = 0;
    while (rpos < rl_sess.srclen) {
        unsigned int e = rpos, ilen;
        struct rl_proot iproot;
        struct rl_node *iroot;
        unsigned int strmark = sstr.used;
        while (e < rl_sess.srclen && rl_sess.src[e] != '\n') ++e;
        ilen = e - rpos;
        if (e < rl_sess.srclen) ++e; /* consume newline */
        if (ilen == 0u) {
            rpos = e;
            continue;
        }
        pp.used = 0;
        argused = 0;
        sstr.used = strmark;
        rl_parse_part(rl_sess.src + rpos, ilen, &pp, &sstr, args,
                      RL_SUB_ARGS, &argused, auxops, RL_SUB_OPS,
                      auxvals, RL_SUB_PVALS, &iproot);
        if (iproot.rc != RLP_OK || iproot.root >= RL_POOL_MAX) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        iroot = &pool[iproot.root];
        if (iroot->kind != RLN_LET) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        {
            unsigned short init = iroot->k1;
            unsigned char dt = iroot->aux;
            unsigned char t;
            if (init == RL_NONODE) {
                rl_arena_reset(&rl_sub);
                return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
            }
            t = rl_an_sub(pool, init, rl_sess.src + rpos, rl_sess.cand,
                          ncand, rl_sess.src, args, RL_SUB_ARGS, auxwalk,
                          RL_SUB_WALK, auxtypes, RL_SUB_POOL_NODES, &diag);
            if (t == RLV_UNKNOWN || t != dt) {
                rl_arena_reset(&rl_sub);
                return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
            }
            /* Duplicate across accepted items cannot occur on a
             * healthy session; a residue (mutant) wedges safely. */
            {
                const char *nm = rl_sess.src + rpos + iroot->e1;
                unsigned int nl = iroot->e2;
                if (rl_lookup(rl_sess.cand, ncand, rl_sess.src, nm,
                              nl) >= 0) {
                    rl_arena_reset(&rl_sub);
                    return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
                }
            }
        }
        if (ridx >= rl_sess.nsyms || ncand >= RL_SYM_MAX) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        rl_sess.cand[ncand] = rl_sess.syms[ridx];
        ncand++;
        ridx++;
        rpos = e;
        sstr.used = strmark;
    }
    if (ridx != rl_sess.nsyms) {
        rl_arena_reset(&rl_sub);
        return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
    }

    /* Candidate item. */
    pp.used = 0;
    argused = 0;
    sstr.used = 0;
    {
        /* Re-parse the candidate into a clean generation (the
         * classify-time parse was discarded with its arena). */
        rl_parse_part(part, len, &pp, &sstr, args, RL_SUB_ARGS,
                      &argused, auxops, RL_SUB_OPS, auxvals, RL_SUB_PVALS,
                      &proot);
        if (proot.rc != RLP_OK || proot.root >= RL_POOL_MAX) {
            const char *d = (proot.rc == RLP_NOMEM) ? RL_D_ARENA_FULL
                                                   : RL_D_PAR_LEX;
            int k = (proot.rc == RLP_NOMEM) ? RL_SUB_ARENAFULL
                                            : RL_SUB_SYNTAX;
            rl_arena_reset(&rl_sub);
            return rl_fail(k, d);
        }
        root_nd = &pool[proot.root];
        islet = (root_nd->kind == RLN_LET);
        if (islet && canonlen == 0u) {
            /* Defensive: the peek pre-check cannot miss a let, but
             * the cap gate must be airtight before any commit. */
            canonlen = rl_canon(part, len, canon, sizeof(canon), &trim);
            if (canonlen == ~0u ||
                canonlen > RL_SESS_MAX ||
                rl_sess.srclen > RL_SESS_MAX - canonlen) {
                rl_arena_reset(&rl_sub);
                return rl_fail(RL_SUB_REJECT, RL_D_SESSION_LIMIT);
            }
        }
    }
    if (islet) {
        unsigned short init = root_nd->k1;
        unsigned char dt = root_nd->aux;
        const char *nm;
        unsigned int nl;
        if (init == RL_NONODE) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        itype = rl_an_sub(pool, init, part, rl_sess.cand, ncand,
                          rl_sess.src, args, RL_SUB_ARGS, auxwalk,
                          RL_SUB_WALK, auxtypes, RL_SUB_POOL_NODES, &diag);
        if (itype == RLV_UNKNOWN) {
            int k = (rl_streq(diag, RL_D_ARENA_FULL) ||
                     rl_streq(diag, RL_D_INTERNAL))
                        ? RL_SUB_ARENAFULL
                        : RL_SUB_REJECT;
            if (rl_streq(diag, RL_D_INTERNAL)) k = RL_SUB_INTERNAL;
            rl_arena_reset(&rl_sub);
            return rl_fail(k, diag);
        }
        if (itype == RLV_UNIT) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_REJECT, RL_D_SEM_TYPE);
        }
        if (itype != dt) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_REJECT, RL_D_SEM_TYPE);
        }
        nm = part + root_nd->e1;
        nl = root_nd->e2;
        if (rl_reserved(nm, nl)) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_REJECT, RL_D_SEM_DUPLICATE);
        }
        if (rl_lookup(rl_sess.cand, ncand, rl_sess.src, nm, nl) >= 0) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_REJECT, RL_D_SEM_DUPLICATE);
        }
        if (rl_sess.nsyms >= RL_SYM_MAX) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_REJECT, RL_D_SYMBOL_LIMIT);
        }
        /* Evaluate the initializer (pure: no print/cmd reachable). */
        ectx.pool = pool;
        ectx.strbase = strb;
        ectx.syms = rl_sess.cand;
        ectx.nsyms = ncand;
        ectx.sess_arena = (const char *)rl_sess.sess_arena;
        ectx.walk = auxwalk;
        ectx.walkcap = RL_SUB_WALK;
        ectx.vstack = auxvstack;
        ectx.vcap = RL_SUB_VCAP;
        ectx.emit = rl_sess.emit;
        evrc = rl_eval(&ectx, init, &ev);
        if (evrc == RL_EV_TRAP) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_TRAP, RL_D_EVAL_TRAP);
        }
        if (evrc != RL_EV_OK) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        /* Stage the candidate row (part-relative name, submission
         * string for str). The bound below is defensive and
         * independent of the semantic symbol-cap check above: even
         * with the cap removed, the table can never overflow. */
        if (ncand >= RL_SYM_MAX) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        {
            struct rl_sym *row = &rl_sess.cand[ncand];
            row->name_off = (unsigned short)root_nd->e1;
            row->name_len = (unsigned short)root_nd->e2;
            row->type = dt;
            row->flags = RL_PARTNAME;
            row->str_len = 0;
            row->pay = 0;
            if (dt == RLV_INT) {
                if (ev.type != RLV_INT) {
                    rl_arena_reset(&rl_sub);
                    return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
                }
                row->pay = ev.a;
            } else if (dt == RLV_BOOL) {
                if (ev.type != RLV_BOOL) {
                    rl_arena_reset(&rl_sub);
                    return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
                }
                row->pay = ev.a;
            } else if (dt == RLV_STR) {
                const char *sp;
                unsigned int sl;
                if (ev.type != RLV_STR) {
                    rl_arena_reset(&rl_sub);
                    return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
                }
                /* Stage the evaluated address (literal scratch or
                 * session bytes, both stable until the commit below
                 * copies them into session ownership). */
                sp = (const char *)ev.a;
                sl = ev.len;
                if (!sp && sl > 0u) {
                    rl_arena_reset(&rl_sub);
                    return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
                }
                if (rl_sess.sess_used > RL_SESS_ARENA ||
                    sl > RL_SESS_ARENA - rl_sess.sess_used) {
                    rl_arena_reset(&rl_sub);
                    return rl_fail(RL_SUB_ARENAFULL, RL_D_ARENA_FULL);
                }
                row->pay = (unsigned long long)sp;
                row->str_len = sl;
                row->flags = RL_PARTNAME | RL_SPTR;
            } else {
                rl_arena_reset(&rl_sub);
                return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
            }
        }
        ncand++;
        /* ---- atomic commit ---- */
        {
            struct rl_sym *row = &rl_sess.cand[ncand - 1];
            unsigned int old_src = rl_sess.srclen;
            unsigned int old_used = rl_sess.sess_used;
            unsigned int k;
            if (row->type == RLV_STR) {
                /* Promote the staged bytes into session ownership
                 * (fit pre-verified, so this cannot fail partway). */
                const char *sp = (const char *)row->pay;
                for (k = 0; k < row->str_len; ++k)
                    rl_sess.sess_arena[rl_sess.sess_used + k] = sp[k];
                row->pay = old_used;
                rl_sess.sess_used += row->str_len;
                if (rl_sess.sess_used > rl_sess.sess_high)
                    rl_sess.sess_high = rl_sess.sess_used;
            }
            /* Rebase the name into session coordinates. */
            row->name_off = (unsigned short)(old_src +
                                             (row->name_off - trim));
            row->flags = 0;
            for (k = 0; k < canonlen; ++k)
                rl_sess.src[old_src + k] = canon[k];
            rl_sess.srclen = old_src + canonlen;
            rl_sess.src[rl_sess.srclen] = 0;
            if (!rl_own_check_tab(rl_sess.cand, ncand,
                                  rl_sess.sess_used)) {
                rl_sess.srclen = old_src;
                rl_sess.src[old_src] = 0;
                rl_sess.sess_used = old_used;
                rl_arena_reset(&rl_sub);
                return rl_fail(RL_SUB_OWNFAIL, RL_D_OWNFAIL);
            }
            for (k = 0; k < ncand; ++k)
                rl_sess.syms[k] = rl_sess.cand[k];
            rl_sess.nsyms = ncand;
        }
        rl_arena_reset(&rl_sub);
        {
            struct rl_outcome o = rl_fail(RL_SUB_OK, 0);
            o.val.type = RLV_UNIT;
            return o;
        }
    }
    /* Bare expression (or value-position command/pipeline): analyze,
     * evaluate, never persist. */
    {
        unsigned char t = rl_an_sub(pool, proot.root, part,
                                    rl_sess.cand, ncand, rl_sess.src,
                                    args, RL_SUB_ARGS, auxwalk,
                                    RL_SUB_WALK, auxtypes,
                                    RL_SUB_POOL_NODES, &diag);
        struct rl_outcome o;
        if (t == RLV_UNKNOWN) {
            int k = (rl_streq(diag, RL_D_ARENA_FULL) ||
                     rl_streq(diag, RL_D_INTERNAL))
                        ? RL_SUB_ARENAFULL
                        : RL_SUB_REJECT;
            if (rl_streq(diag, RL_D_INTERNAL)) k = RL_SUB_INTERNAL;
            rl_arena_reset(&rl_sub);
            return rl_fail(k, diag);
        }
        ectx.pool = pool;
        ectx.strbase = strb;
        ectx.syms = rl_sess.cand;
        ectx.nsyms = ncand;
        ectx.sess_arena = (const char *)rl_sess.sess_arena;
        ectx.walk = auxwalk;
        ectx.walkcap = RL_SUB_WALK;
        ectx.vstack = auxvstack;
        ectx.vcap = RL_SUB_VCAP;
        ectx.emit = rl_sess.emit;
        evrc = rl_eval(&ectx, proot.root, &ev);
        if (evrc == RL_EV_TRAP) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_TRAP, RL_D_EVAL_TRAP);
        }
        if (evrc != RL_EV_OK) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        /* Bare value emission (frozen REPL convention: non-unit
         * prints exactly like print; unit prints nothing). Emitted
         * here, before the reset, so no transient pointer escapes. */
        if (ev.type == RLV_INT || ev.type == RLV_BOOL) {
            char rbuf[24];
            unsigned int rn = rl_render(&ev, rbuf, sizeof(rbuf));
            if (rn == 0u) {
                rl_arena_reset(&rl_sub);
                return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
            }
            if (rl_sess.emit) rl_sess.emit(rbuf, rn);
        } else if (ev.type == RLV_STR) {
            if (rl_sess.emit)
                rl_sess.emit((const char *)ev.a, ev.len);
        } else if (ev.type != RLV_UNIT) {
            rl_arena_reset(&rl_sub);
            return rl_fail(RL_SUB_INTERNAL, RL_D_INTERNAL);
        }
        o = rl_fail(RL_SUB_OK, 0);
        o.val.type = RLV_UNIT;
        rl_arena_reset(&rl_sub);
        return o;
    }
}

static unsigned char rl_an_sub(struct rl_node *pool, unsigned short root,
                               const char *text,
                               struct rl_sym *tab, unsigned int ntab,
                               const char *sess_src,
                               const unsigned short *argspace,
                               unsigned int argcap, unsigned short *walk,
                               unsigned int walkcap, unsigned char *types,
                               unsigned int typecap, const char **diag)
{
    unsigned int wtop = 0;
    if (root == RL_NONODE || root >= RL_POOL_MAX) {
        *diag = RL_D_INTERNAL;
        return RLV_UNKNOWN;
    }
    walk[wtop++] = root;
    while (wtop > 0) {
        unsigned int e = walk[--wtop];
        unsigned short i = (unsigned short)(e & 0x7FFFu);
        unsigned int vis = (e >> 15) & 1u;
        struct rl_node *nd;
        if (i >= RL_POOL_MAX || (unsigned int)i >= typecap) {
            *diag = RL_D_INTERNAL;
            return RLV_UNKNOWN;
        }
        nd = &pool[i];
        if (!vis) {
            switch (nd->kind) {
            case RLN_INT:
                types[i] = RLV_INT;
                break;
            case RLN_BOOL:
                types[i] = RLV_BOOL;
                break;
            case RLN_STR:
                if (nd->e2 > 4096u) {
                    *diag = RL_D_SEM_LIMIT;
                    return RLV_UNKNOWN;
                }
                types[i] = RLV_STR;
                break;
            case RLN_VAR: {
                const char *nm = text + nd->e1;
                int f;
                if (rl_reserved(nm, (unsigned int)nd->e2)) {
                    *diag = RL_D_SEM_UNDECLARED;
                    return RLV_UNKNOWN;
                }
                f = rl_lookup(tab, ntab, sess_src, nm,
                              (unsigned int)nd->e2);
                if (f < 0) {
                    *diag = RL_D_SEM_UNDECLARED;
                    return RLV_UNKNOWN;
                }
                types[i] = tab[(unsigned int)f].type;
                nd->k2 = (unsigned short)f;
                break;
            }
            case RLN_CMD: {
                /* Value-position command: unit, always (statement
                 * commands never reach analysis). Redirects still
                 * reject: 18d-base has no files. Exception: a
                 * zero-arg command naming a declared variable IS
                 * that variable (lone-word rule; the classifier
                 * routes such singletons here for evaluation). */
                unsigned int base = (nd->e2 >> 8) & 0xFFFFu;
                unsigned int nargs = nd->aux;
                unsigned int j;
                if (nargs == 0u) {
                    const char *nm = text + (nd->e1 & 0xFFFFu);
                    unsigned int nl = (nd->e1 >> 16) & 0xFFFFu;
                    int f;
                    if (rl_reserved(nm, nl)) {
                        *diag = RL_D_SEM_UNDECLARED;
                        return RLV_UNKNOWN;
                    }
                    f = rl_lookup(tab, ntab, sess_src, nm, nl);
                    if (f < 0) {
                        *diag = RL_D_SEM_UNDECLARED;
                        return RLV_UNKNOWN;
                    }
                    /* Morph to a plain variable reference (the tree
                     * honestly is one now; evaluators never see
                     * command nodes). */
                    nd->kind = RLN_VAR;
                    types[i] = tab[(unsigned int)f].type;
                    nd->k2 = (unsigned short)f;
                    break;
                }
                for (j = 0; j < nargs; ++j) {
                    unsigned short a;
                    if (base + j >= argcap) {
                        *diag = RL_D_INTERNAL;
                        return RLV_UNKNOWN;
                    }
                    a = argspace[base + j];
                    if (a >= RL_POOL_MAX) {
                        *diag = RL_D_INTERNAL;
                        return RLV_UNKNOWN;
                    }
                    if (pool[a].kind == RLN_STR &&
                        (pool[a].aux == 2u || pool[a].aux == 3u)) {
                        *diag = RL_D_SH_REDIRECT;
                        return RLV_UNKNOWN;
                    }
                }
                types[i] = RLV_UNIT;
                break;
            }
            case RLN_PIPE: {
                struct rl_node *a, *b;
                if (nd->k1 == RL_NONODE || nd->k2 == RL_NONODE ||
                    nd->k1 >= RL_POOL_MAX || nd->k2 >= RL_POOL_MAX) {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
                a = &pool[nd->k1];
                b = &pool[nd->k2];
                if (a->kind != RLN_CMD || b->kind != RLN_CMD) {
                    *diag = RL_D_PIPELINE_STAGE;
                    return RLV_UNKNOWN;
                }
                types[i] = RLV_UNIT;
                break;
            }
            case RLN_UNOP:
                if (nd->k1 == RL_NONODE || nd->k1 >= RL_POOL_MAX) {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
                if (wtop + 2u > walkcap) {
                    *diag = RL_D_ARENA_FULL;
                    return RLV_UNKNOWN;
                }
                walk[wtop++] = (unsigned short)(((unsigned int)i) | (1u << 15));
                walk[wtop++] = (unsigned int)nd->k1;
                break;
            case RLN_BINOP:
                if (nd->k1 == RL_NONODE || nd->k2 == RL_NONODE ||
                    nd->k1 >= RL_POOL_MAX || nd->k2 >= RL_POOL_MAX) {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
                if (wtop + 3u > walkcap) {
                    *diag = RL_D_ARENA_FULL;
                    return RLV_UNKNOWN;
                }
                walk[wtop++] = (unsigned short)(((unsigned int)i) | (1u << 15));
                walk[wtop++] = (unsigned int)nd->k2;
                walk[wtop++] = (unsigned int)nd->k1;
                break;
            case RLN_CALL:
                if (wtop + 2u > walkcap) {
                    *diag = RL_D_ARENA_FULL;
                    return RLV_UNKNOWN;
                }
                walk[wtop++] = (unsigned short)(((unsigned int)i) | (1u << 15));
                if (nd->k1 != RL_NONODE) {
                    if (nd->k1 >= RL_POOL_MAX) {
                        *diag = RL_D_INTERNAL;
                        return RLV_UNKNOWN;
                    }
                    walk[wtop++] = (unsigned int)nd->k1;
                }
                break;
            default:
                *diag = RL_D_INTERNAL;
                return RLV_UNKNOWN;
            }
        } else {
            if (nd->kind == RLN_UNOP) {
                unsigned char ct = types[nd->k1];
                if (ct == RLV_UNKNOWN) {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
                if (nd->op == RLOP_NEG) {
                    if (ct != RLV_INT) {
                        *diag = RL_D_SEM_TYPE;
                        return RLV_UNKNOWN;
                    }
                    types[i] = RLV_INT;
                } else if (nd->op == RLOP_NOT) {
                    if (ct != RLV_BOOL) {
                        *diag = RL_D_SEM_TYPE;
                        return RLV_UNKNOWN;
                    }
                    types[i] = RLV_BOOL;
                } else {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
            } else if (nd->kind == RLN_BINOP) {
                unsigned char lt = types[nd->k1];
                unsigned char rt = types[nd->k2];
                if (lt == RLV_UNKNOWN || rt == RLV_UNKNOWN) {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
                if (lt == RLV_UNIT || rt == RLV_UNIT) {
                    *diag = RL_D_SEM_TYPE;
                    return RLV_UNKNOWN;
                }
                if (nd->op >= RLOP_ADD && nd->op <= RLOP_MOD) {
                    if (lt != RLV_INT || rt != RLV_INT) {
                        *diag = RL_D_SEM_TYPE;
                        return RLV_UNKNOWN;
                    }
                    types[i] = RLV_INT;
                } else if (nd->op == RLOP_EQ || nd->op == RLOP_NE) {
                    if (lt != rt || (lt != RLV_INT && lt != RLV_BOOL &&
                                     lt != RLV_STR)) {
                        *diag = RL_D_SEM_TYPE;
                        return RLV_UNKNOWN;
                    }
                    types[i] = RLV_BOOL;
                } else if (nd->op >= RLOP_LT && nd->op <= RLOP_GE) {
                    if (lt != RLV_INT || rt != RLV_INT) {
                        *diag = RL_D_SEM_TYPE;
                        return RLV_UNKNOWN;
                    }
                    types[i] = RLV_BOOL;
                } else if (nd->op == RLOP_AND || nd->op == RLOP_OR) {
                    if (lt != RLV_BOOL || rt != RLV_BOOL) {
                        *diag = RL_D_SEM_TYPE;
                        return RLV_UNKNOWN;
                    }
                    types[i] = RLV_BOOL;
                } else {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
            } else if (nd->kind == RLN_CALL) {
                struct rl_node *callee;
                const char *cn;
                unsigned char at;
                if (nd->k2 == RL_NONODE || nd->k2 >= RL_POOL_MAX) {
                    *diag = RL_D_SEM_UNKNOWN_FN;
                    return RLV_UNKNOWN;
                }
                callee = &pool[nd->k2];
                if (callee->kind != RLN_VAR) {
                    *diag = RL_D_SEM_UNKNOWN_FN;
                    return RLV_UNKNOWN;
                }
                cn = text + callee->e1;
                if (!rl_is_print(cn, callee->e2)) {
                    *diag = RL_D_SEM_UNKNOWN_FN;
                    return RLV_UNKNOWN;
                }
                if (nd->aux != 1u) {
                    *diag = RL_D_SEM_ARITY;
                    return RLV_UNKNOWN;
                }
                if (nd->k1 == RL_NONODE || nd->k1 >= RL_POOL_MAX) {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
                at = types[nd->k1];
                if (at == RLV_UNKNOWN) {
                    *diag = RL_D_INTERNAL;
                    return RLV_UNKNOWN;
                }
                if (at != RLV_INT && at != RLV_BOOL && at != RLV_STR) {
                    *diag = RL_D_SEM_TYPE;
                    return RLV_UNKNOWN;
                }
                types[i] = RLV_UNIT;
            } else {
                *diag = RL_D_INTERNAL;
                return RLV_UNKNOWN;
            }
        }
    }
    return types[root];
}

/* ---- iterative analyzer ---- */

struct rl_an {
    struct rl_node *pool;
    const char *strbase;   /* submission string scratch */
    const char *srctext;   /* text names slice (session or part) */
    struct rl_sym *tab;    /* candidate table under construction */
    unsigned int ntab;
    unsigned int *walk;    /* (idx | visited<<16) stack */
    unsigned int walkcap;
    unsigned char *types;  /* per-node types, indexed by node */
    unsigned int typecap;
    const char *diag;
};

