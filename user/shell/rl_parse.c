/* Stage 18d Slice F item parser. See rl_parse.h for the contract. */
#include "rl_parse.h"

#define RLN_NONE RL_NONODE

struct rlp {
    struct rl_lex lx;
    struct rl_strscratch ss;
    unsigned int ss_mark;
    struct rl_tok cur;
    int lrc;
    int have;
    struct rl_ppool *pool;
    unsigned short *args;
    unsigned int acap;
    unsigned int *aused;
    unsigned int *ops;
    unsigned int ocap;
    unsigned short *vals;
    unsigned int vcap;
    unsigned int depth;
    int err;
};

static void p_init(struct rlp *p, const char *src, unsigned int len,
                   struct rl_ppool *pool, struct rl_strscratch *ss,
                   unsigned short *argspace, unsigned int argcap,
                   unsigned int *argused, unsigned int *opstack,
                   unsigned int ocap, unsigned short *valstack,
                   unsigned int vcap)
{
    rl_lex_init(&p->lx, src, len);
    p->ss = *ss;
    p->ss_mark = ss->used;
    p->lrc = RLL_OK;
    p->have = 0;
    p->pool = pool;
    p->args = argspace;
    p->acap = argcap;
    p->aused = argused;
    p->ops = opstack;
    p->ocap = ocap;
    p->vals = valstack;
    p->vcap = vcap;
    p->depth = 0;
    p->err = RLP_OK;
}

/* Pull the current token (first call primes). Lex failures become
 * plain syntax errors (E row parity). */
static struct rl_tok *p_cur(struct rlp *p)
{
    if (!p->have) {
        rl_lex_next(&p->lx, &p->ss, &p->cur, &p->lrc);
        p->have = 1;
        if (p->lrc != RLL_OK)
            p->err = RLP_SYNTAX;
    }
    return &p->cur;
}

static void p_next(struct rlp *p)
{
    p->have = 0;
}

static unsigned short p_mknode(struct rlp *p, unsigned char kind)
{
    struct rl_node *nd;
    unsigned short id;
    if (p->err) return RLN_NONE;
    if (p->pool->used >= p->pool->cap) {
        p->err = RLP_NOMEM;
        return RLN_NONE;
    }
    id = (unsigned short)p->pool->used++;
    nd = &p->pool->nodes[id];
    nd->kind = kind;
    nd->vtype = RLV_UNKNOWN;
    nd->op = 0;
    nd->aux = 0;
    nd->e1 = 0;
    nd->e2 = 0;
    nd->k1 = RLN_NONE;
    nd->k2 = RLN_NONE;
    return id;
}

static int p_enter(struct rlp *p)
{
    if (p->err) return 0;
    if (++p->depth > RL_DEPTH_MAX) {
        p->err = RLP_TOODEEP;
        return 0;
    }
    return 1;
}

static void p_leave(struct rlp *p)
{
    if (p->depth) p->depth--;
}

static int p_at(struct rlp *p, unsigned char kind)
{
    if (p->err) return 0;
    return p_cur(p)->kind == kind;
}

/* One-token lookahead without consuming (for command follow sets).
 * String scratch is rewound so peeked strings decode idempotently. */
static int p_peek(struct rlp *p, struct rl_tok *out)
{
    unsigned int m = rl_lex_mark(&p->lx);
    unsigned int sm = p->ss.used;
    int rc = RLL_OK;
    int have_save = p->have;
    struct rl_tok cur_save = p->cur;
    int lrc_save = p->lrc;
    if (!p->have) {
        rl_lex_next(&p->lx, &p->ss, &p->cur, &p->lrc);
        p->have = 1;
    }
    if (p->lrc != RLL_OK) {
        p->lx.pos = m;
        p->ss.used = sm;
        p->have = have_save;
        p->cur = cur_save;
        p->lrc = lrc_save;
        (void)rc;
        return 0;
    }
    p->have = 0;
    rl_lex_next(&p->lx, &p->ss, out, &rc);
    p->lx.pos = m;
    p->ss.used = sm;
    p->have = have_save;
    p->cur = cur_save;
    p->lrc = lrc_save;
    return rc == RLL_OK;
}

struct rl_psave {
    unsigned int pos;
    unsigned int ss_used;
    unsigned int pool_used;
    unsigned int arg_used;
    unsigned int depth;
    int have;
    struct rl_tok cur;
    int lrc;
};

static void p_save(struct rlp *p, struct rl_psave *s)
{
    s->pos = rl_lex_mark(&p->lx);
    s->ss_used = p->ss.used;
    s->pool_used = p->pool->used;
    s->arg_used = *p->aused;
    s->depth = p->depth;
    s->have = p->have;
    s->cur = p->cur;
    s->lrc = p->lrc;
}

static void p_restore(struct rlp *p, const struct rl_psave *s)
{
    rl_lex_rewind(&p->lx, s->pos);
    p->ss.used = s->ss_used;
    p->pool->used = s->pool_used;
    *p->aused = s->arg_used;
    p->depth = s->depth;
    p->have = s->have;
    p->cur = s->cur;
    p->lrc = s->lrc;
    if (p->err == RLP_SYNTAX || p->err == RLP_TOODEEP)
        p->err = RLP_OK;
}

static int p_is_binop(unsigned char kind, unsigned int *prec,
                      unsigned char *op)
{
    switch (kind) {
    case RLT_OROR: *prec = 1; *op = RLOP_OR; return 1;
    case RLT_ANDAND: *prec = 2; *op = RLOP_AND; return 1;
    case RLT_EQEQ: *prec = 3; *op = RLOP_EQ; return 1;
    case RLT_BANGEQ: *prec = 3; *op = RLOP_NE; return 1;
    case RLT_LESS: *prec = 4; *op = RLOP_LT; return 1;
    case RLT_GREATER: *prec = 4; *op = RLOP_GT; return 1;
    case RLT_LESSEQ: *prec = 4; *op = RLOP_LE; return 1;
    case RLT_GREATEREQ: *prec = 4; *op = RLOP_GE; return 1;
    case RLT_PLUS: *prec = 5; *op = RLOP_ADD; return 1;
    case RLT_MINUS: *prec = 5; *op = RLOP_SUB; return 1;
    case RLT_STAR: *prec = 6; *op = RLOP_MUL; return 1;
    case RLT_SLASH: *prec = 6; *op = RLOP_DIV; return 1;
    case RLT_PERCENT: *prec = 6; *op = RLOP_MOD; return 1;
    default: return 0;
    }
}

/* Decimal lexeme (digits only, <= INT64_MAX by lexer) to u64. */
static unsigned long long p_tou64(const char *src, unsigned int off,
                                  unsigned int len)
{
    unsigned long long v = 0;
    unsigned int i;
    for (i = 0; i < len; ++i)
        v = v * 10u + (unsigned long long)(src[off + i] - '0');
    return v;
}

/* Slice G: exact `len` callee match (call position only). Bare
 * `len` (variable/command word) never reaches here as a call; the
 * length check is load-bearing (a prefix-only match would accept
 * `length(...)` and friends: G-M9 tripwire). */
static int p_is_len_call(const char *src, unsigned int off,
                         unsigned int len)
{
    if (len != 3u) return 0;
    return src[off] == 'l' && src[off + 1u] == 'e' &&
        src[off + 2u] == 'n';
}

/* Flat atom: literals and bare words only (no parens, calls, or
 * operators: the shunting-yard driver owns all nesting, so C-stack
 * use stays O(1) regardless of expression depth). */
static unsigned short p_atom(struct rlp *p, const char *src)
{
    struct rl_tok *t = p_cur(p);
    unsigned short id;
    if (p->err) return RLN_NONE;
    if (t->kind == RLT_IDENT) {
        id = p_mknode(p, RLN_VAR);
        if (p->err) return RLN_NONE;
        p->pool->nodes[id].e1 = t->off;
        p->pool->nodes[id].e2 = t->len;
        p_next(p);
        return id;
    }
    if (t->kind == RLT_INT) {
        unsigned long long v = p_tou64(src, t->off, t->len);
        id = p_mknode(p, RLN_INT);
        if (p->err) return RLN_NONE;
        p->pool->nodes[id].e1 = (unsigned int)(v & 0xFFFFFFFFu);
        p->pool->nodes[id].e2 = (unsigned int)(v >> 32);
        p_next(p);
        return id;
    }
    if (t->kind == RLT_STR) {
        /* Decoded bytes live in submission string scratch; record
         * the scratch offset+length (total decoded per part is
         * bounded by the part length). */
        id = p_mknode(p, RLN_STR);
        if (p->err) return RLN_NONE;
        p->pool->nodes[id].e1 =
            (unsigned int)(t->val - p->ss.base);
        p->pool->nodes[id].e2 = t->vlen;
        p_next(p);
        return id;
    }
    if (t->kind == RLT_TRUE || t->kind == RLT_FALSE) {
        id = p_mknode(p, RLN_BOOL);
        if (p->err) return RLN_NONE;
        p->pool->nodes[id].aux = (unsigned char)(t->kind == RLT_TRUE);
        p_next(p);
        return id;
    }
    p->err = RLP_SYNTAX;
    return RLN_NONE;
}

/* Shunting-yard operator entry (packed u32): kind + precedence. */
#define RLY_BINOP 1u
#define RLY_UNOP 2u
#define RLY_LPAREN 3u
#define RLY_CALL 4u
#define RLY_OPKIND_MASK 7u
#define RLY_PREC_SHIFT 3
#define RLY_AUX_SHIFT 11
static unsigned int rly_op(unsigned int kind, unsigned int prec,
                           unsigned int aux)
{
    return kind | (prec << RLY_PREC_SHIFT) | (aux << RLY_AUX_SHIFT);
}

/* Iterative precedence-climbing expression parser (shunting-yard).
 * O(1) C stack: operator and value stacks live in caller aux
 * memory. Accepts exactly the host expression language (binary 1..6
 * left-assoc, prefix -/!, grouping parens elided, postfix calls)
 * and stops before PIPEGT/EOF/COMMA/RPAREN/RBRACE/SEMICOLON, which
 * the caller owns. Depth charges mirror the host (unary, grouping
 * paren, call group) against RL_DEPTH_MAX. */
static unsigned short p_expr_yd(struct rlp *p, const char *src)
{
    unsigned int *ops = p->ops;
    unsigned int ocap = p->ocap;
    unsigned short *vals = p->vals;
    unsigned int vcap = p->vcap;
    unsigned int oused = 0, vused = 0;
    unsigned int parens = 0, unpend = 0;
    int expect_operand = 1;
    int have_value = 0;
    for (;;) {
        unsigned char k;
        unsigned int prec;
        unsigned char op;
        if (p->err) return RLN_NONE;
        k = p_cur(p)->kind;
        if (expect_operand) {
            if (k == RLT_MINUS || k == RLT_BANG) {
                unsigned int now = parens + unpend + 1u;
                if (now > RL_DEPTH_MAX || oused >= ocap) {
                    p->err = now > RL_DEPTH_MAX ? RLP_TOODEEP : RLP_NOMEM;
                    return RLN_NONE;
                }
                ops[oused++] = rly_op(
                    RLY_UNOP, 7u,
                    (unsigned int)(k == RLT_MINUS ? RLOP_NEG : RLOP_NOT));
                unpend++;
                p_next(p);
                continue;
            }
            if (k == RLT_LPAREN) {
                unsigned int now = parens + unpend + 1u;
                if (now > RL_DEPTH_MAX || oused >= ocap) {
                    p->err = now > RL_DEPTH_MAX ? RLP_TOODEEP : RLP_NOMEM;
                    return RLN_NONE;
                }
                /* Call `(` directly after a value, else a group. */
                if (have_value)
                    ops[oused++] = rly_op(RLY_CALL, 0u, 0u);
                else
                    ops[oused++] = rly_op(RLY_LPAREN, 0u, 0u);
                parens++;
                p_next(p);
                have_value = 0;
                continue;
            }
            if (k == RLT_IDENT || k == RLT_INT || k == RLT_STR ||
                k == RLT_TRUE || k == RLT_FALSE) {
                unsigned short id = p_atom(p, src);
                if (p->err) return RLN_NONE;
                if (vused >= vcap) {
                    p->err = RLP_NOMEM;
                    return RLN_NONE;
                }
                vals[vused++] = id;
                /* Apply pending prefix operators immediately (they
                 * bind tighter than everything binary). */
                while (oused > 0 &&
                       (ops[oused - 1] & RLY_OPKIND_MASK) == RLY_UNOP) {
                    unsigned short a, id2;
                    oused--;
                    unpend--;
                    if (vused < 1u) {
                        p->err = RLP_SYNTAX;
                        return RLN_NONE;
                    }
                    a = vals[--vused];
                    id2 = p_mknode(p, RLN_UNOP);
                    if (p->err) return RLN_NONE;
                    p->pool->nodes[id2].op = (unsigned char)
                        ((ops[oused] >> RLY_AUX_SHIFT) & 0xFFu);
                    p->pool->nodes[id2].k1 = a;
                    if (vused >= vcap) {
                        p->err = RLP_NOMEM;
                        return RLN_NONE;
                    }
                    vals[vused++] = id2;
                }
                expect_operand = 0;
                have_value = 1;
                continue;
            }
            p->err = RLP_SYNTAX;
            return RLN_NONE;
        }
        /* Operator position. */
        if (p_is_binop(k, &prec, &op)) {
            for (;;) {
                unsigned int top, tk, tp;
                unsigned short l, r, id;
                if (oused == 0) break;
                top = ops[oused - 1];
                tk = top & RLY_OPKIND_MASK;
                if (tk != RLY_BINOP && tk != RLY_UNOP) break;
                tp = (top >> RLY_PREC_SHIFT) & 0x7Fu;
                if (tp < prec) break;
                oused--;
                if (tk == RLY_UNOP) unpend--;
                if (vused < (tk == RLY_BINOP ? 2u : 1u)) {
                    p->err = RLP_SYNTAX;
                    return RLN_NONE;
                }
                if (tk == RLY_BINOP) {
                    r = vals[--vused];
                    l = vals[--vused];
                    id = p_mknode(p, RLN_BINOP);
                    if (p->err) return RLN_NONE;
                    p->pool->nodes[id].op =
                        (unsigned char)((top >> RLY_AUX_SHIFT) & 0xFFu);
                    p->pool->nodes[id].k1 = l;
                    p->pool->nodes[id].k2 = r;
                } else {
                    l = vals[--vused];
                    id = p_mknode(p, RLN_UNOP);
                    if (p->err) return RLN_NONE;
                    p->pool->nodes[id].op =
                        (unsigned char)((top >> RLY_AUX_SHIFT) & 0xFFu);
                    p->pool->nodes[id].k1 = l;
                }
                if (vused >= vcap) {
                    p->err = RLP_NOMEM;
                    return RLN_NONE;
                }
                vals[vused++] = id;
            }
            if (oused >= ocap) {
                p->err = RLP_NOMEM;
                return RLN_NONE;
            }
            ops[oused++] = rly_op(RLY_BINOP, prec, op);
            p_next(p);
            expect_operand = 1;
            have_value = 0;
            continue;
        }
        if (k == RLT_LPAREN && have_value) {
            /* Postfix call on the value just parsed. */
            unsigned int now = parens + unpend + 1u;
            if (now > RL_DEPTH_MAX || oused >= ocap) {
                p->err = now > RL_DEPTH_MAX ? RLP_TOODEEP : RLP_NOMEM;
                return RLN_NONE;
            }
            ops[oused++] = rly_op(RLY_CALL, 0u, 0u);
            parens++;
            p_next(p);
            expect_operand = 1;
            have_value = 0;
            continue;
        }
        if (k == RLT_COMMA || k == RLT_RPAREN) {
            /* Find the enclosing call/group mark, fold operators
             * back to it, then handle the separator/closer. */
            unsigned int mi = oused;
            int found = 0, iscall = 0;
            while (mi > 0) {
                unsigned int tk = ops[mi - 1] & RLY_OPKIND_MASK;
                if (tk == RLY_CALL || tk == RLY_LPAREN) {
                    found = 1;
                    iscall = (tk == RLY_CALL);
                    break;
                }
                mi--;
            }
            if (!found) {
                p->err = RLP_SYNTAX;
                return RLN_NONE;
            }
            while (oused > mi) {
                unsigned int top = ops[oused - 1];
                unsigned int tk = top & RLY_OPKIND_MASK;
                unsigned short l, r, id;
                if (tk != RLY_BINOP && tk != RLY_UNOP) {
                    p->err = RLP_SYNTAX;
                    return RLN_NONE;
                }
                oused--;
                if (tk == RLY_UNOP) unpend--;
                if (tk == RLY_BINOP) {
                    if (vused < 2u) {
                        p->err = RLP_SYNTAX;
                        return RLN_NONE;
                    }
                    r = vals[--vused];
                    l = vals[--vused];
                    id = p_mknode(p, RLN_BINOP);
                    if (p->err) return RLN_NONE;
                    p->pool->nodes[id].op = (unsigned char)
                        ((top >> RLY_AUX_SHIFT) & 0xFFu);
                    p->pool->nodes[id].k1 = l;
                    p->pool->nodes[id].k2 = r;
                } else {
                    if (vused < 1u) {
                        p->err = RLP_SYNTAX;
                        return RLN_NONE;
                    }
                    l = vals[--vused];
                    id = p_mknode(p, RLN_UNOP);
                    if (p->err) return RLN_NONE;
                    p->pool->nodes[id].op = (unsigned char)
                        ((top >> RLY_AUX_SHIFT) & 0xFFu);
                    p->pool->nodes[id].k1 = l;
                }
                if (vused >= vcap) {
                    p->err = RLP_NOMEM;
                    return RLN_NONE;
                }
                vals[vused++] = id;
            }
            if (k == RLT_COMMA) {
                unsigned int mt;
                if (!iscall) {
                    p->err = RLP_SYNTAX;
                    return RLN_NONE;
                }
                /* One more separator; the next operand (or the
                 * trailing-comma rejection at `)`) follows. */
                mt = ops[oused - 1];
                ops[oused - 1] = mt + (1u << RLY_AUX_SHIFT);
                p_next(p);
                expect_operand = 1;
                have_value = 0;
                continue;
            }
            /* RPAREN: close the group or build the call. */
            {
                unsigned int mt = ops[--oused];
                unsigned int commas =
                    (mt >> RLY_AUX_SHIFT) & 0xFFFFu;
                if (parens > 0) parens--;
                p_next(p);
                if (!iscall) {
                    if (expect_operand) {
                        /* `()` holds no value. */
                        p->err = RLP_SYNTAX;
                        return RLN_NONE;
                    }
                } else {
                    unsigned int argc;
                    unsigned short callee, arg1 = RLN_NONE, id;
                    if (expect_operand) {
                        /* `f()` takes zero args; `f(a,)` is a
                         * trailing comma like the host rejects. */
                        if (commas > 0u) {
                            p->err = RLP_SYNTAX;
                            return RLN_NONE;
                        }
                        argc = 0;
                    } else {
                        argc = commas + 1u;
                    }
                    if (vused < argc + 1u) {
                        p->err = RLP_SYNTAX;
                        return RLN_NONE;
                    }
                    if (argc > 0u)
                        arg1 = vals[vused - argc];
                    vused -= argc;
                    callee = vals[--vused];
                    if (callee != RLN_NONE && callee < p->pool->cap &&
                        p->pool->nodes[callee].kind == RLN_VAR &&
                        p_is_len_call(src,
                                      p->pool->nodes[callee].e1,
                                      p->pool->nodes[callee].e2)) {
                        /* Slice G len(...) builtin: same call shape,
                         * depth charge, and argc capture as CALL; the
                         * callee word stays an ordinary node (never
                         * walked: k2 is none). Bare `len` and longer
                         * words (`length`) keep their frozen meanings. */
                        id = p_mknode(p, RLN_LEN);
                        if (p->err) return RLN_NONE;
                        p->pool->nodes[id].k1 = arg1;
                        p->pool->nodes[id].k2 = RLN_NONE;
                        p->pool->nodes[id].aux = (unsigned char)
                            (argc > 255u ? 255u : argc);
                    } else {
                        id = p_mknode(p, RLN_CALL);
                        if (p->err) return RLN_NONE;
                        p->pool->nodes[id].k1 = arg1;
                        p->pool->nodes[id].k2 = callee;
                        p->pool->nodes[id].aux = (unsigned char)
                            (argc > 255u ? 255u : argc);
                    }
                    if (vused >= vcap) {
                        p->err = RLP_NOMEM;
                        return RLN_NONE;
                    }
                    vals[vused++] = id;
                }
                expect_operand = 0;
                have_value = 1;
                continue;
            }
        }
        /* End of this expression level for the caller. */
        break;
    }
    /* Drain remaining operators (groups/calls left open are errors). */
    while (oused > 0) {
        unsigned int top = ops[oused - 1];
        unsigned int tk = top & RLY_OPKIND_MASK;
        unsigned short l, r, id;
        if (tk == RLY_LPAREN || tk == RLY_CALL) {
            p->err = RLP_SYNTAX;
            return RLN_NONE;
        }
        oused--;
        if (tk == RLY_UNOP) unpend--;
        if (tk == RLY_BINOP) {
            if (vused < 2u) {
                p->err = RLP_SYNTAX;
                return RLN_NONE;
            }
            r = vals[--vused];
            l = vals[--vused];
            id = p_mknode(p, RLN_BINOP);
            if (p->err) return RLN_NONE;
            p->pool->nodes[id].op =
                (unsigned char)((top >> RLY_AUX_SHIFT) & 0xFFu);
            p->pool->nodes[id].k1 = l;
            p->pool->nodes[id].k2 = r;
        } else {
            if (vused < 1u) {
                p->err = RLP_SYNTAX;
                return RLN_NONE;
            }
            l = vals[--vused];
            id = p_mknode(p, RLN_UNOP);
            if (p->err) return RLN_NONE;
            p->pool->nodes[id].op =
                (unsigned char)((top >> RLY_AUX_SHIFT) & 0xFFu);
            p->pool->nodes[id].k1 = l;
        }
        if (vused >= vcap) {
            p->err = RLP_NOMEM;
            return RLN_NONE;
        }
        vals[vused++] = id;
    }
    if (vused != 1u || unpend != 0u) {
        p->err = RLP_SYNTAX;
        return RLN_NONE;
    }
    return vals[0];
}

static unsigned short p_pipeline(struct rlp *p, const char *src);


static unsigned short p_stage(struct rlp *p, const char *src);
static unsigned short p_cmd_or_expr(struct rlp *p, const char *src);

/* Command-argument starter set (frozen host rule). */
static int p_arg_starter(struct rlp *p)
{
    unsigned char k;
    if (p->err) return 0;
    k = p_cur(p)->kind;
    return k == RLT_IDENT || k == RLT_MINUS || k == RLT_INT ||
        k == RLT_STR || k == RLT_TRUE || k == RLT_FALSE ||
        k == RLT_GREATER;
}

/* Lone-word follow set: the word is a zero-arg command candidate
 * (Slice E parity: `ls |> count` reads as shell here). */
static int p_lone_follow(unsigned char k)
{
    return k == RLT_PIPEGT || k == RLT_SEMI || k == RLT_EOF ||
        k == RLT_RBRACE || k == RLT_RPAREN || k == RLT_COMMA;
}

/* Span adjacency: b starts exactly where a ends (frozen flag rule). */
static int p_adjacent(const struct rl_tok *a, const struct rl_tok *b)
{
    return (unsigned int)a->off + (unsigned int)a->len == (unsigned int)b->off;
}

/* Append an argument node index to the command vector. */
static int p_arg_push(struct rlp *p, unsigned short idx)
{
    if (*p->aused >= p->acap) {
        p->err = RLP_NOMEM;
        return 0;
    }
    p->args[*p->aused] = idx;
    (*p->aused)++;
    return 1;
}

/* Redirect shape check (> "f" or >> "f"); builds nothing here. */
static int p_redirect_ok(struct rlp *p)
{
    struct rl_tok first, after, after2;
    unsigned int m = rl_lex_mark(&p->lx);
    unsigned int sm = p->ss.used;
    int have_save = p->have;
    struct rl_tok cur_save = p->cur;
    int lrc_save = p->lrc;
    int rc, ok = 0;
    /* current is GREATER (checked by caller) */
    p->have = 0;
    rl_lex_next(&p->lx, &p->ss, &first, &rc);
    if (rc != RLL_OK || first.kind != RLT_GREATER) goto done;
    rl_lex_next(&p->lx, &p->ss, &after, &rc);
    if (rc != RLL_OK) goto done;
    if (after.kind == RLT_STR) {
        ok = 1;
        goto done;
    }
    if (after.kind == RLT_GREATER &&
        (unsigned int)after.off == (unsigned int)first.off + 1u) {
        rl_lex_next(&p->lx, &p->ss, &after2, &rc);
        if (rc == RLL_OK && after2.kind == RLT_STR) ok = 1;
    }
done:
    p->lx.pos = m;
    p->ss.used = sm;
    p->have = have_save;
    p->cur = cur_save;
    p->lrc = lrc_save;
    (void)first;
    return ok;
}

/* Try a juxtaposition command. Returns node, or RLN_NONE for
 * "not a command" (caller restores and tries expression). Mirrors
 * the host: malformed command text also falls back to expression
 * (the host's _Abort is caught by parse_cmd_or_expr). */
static unsigned short p_cmd(struct rlp *p, const char *src)
{
    struct rl_tok name, nxt, after;
    unsigned short id, base, nargs = 0;
    if (p->err) return RLN_NONE;
    name = *p_cur(p);
    p_next(p);
    if (!p_arg_starter(p)) return RLN_NONE;
    nxt = *p_cur(p);
    if (nxt.kind == RLT_MINUS) {
        if (!p_peek(p, &after)) return RLN_NONE;
        if (!((after.kind == RLT_IDENT || after.kind == RLT_INT) &&
              p_adjacent(&nxt, &after)))
            return RLN_NONE; /* `a - b` stays subtraction */
    }
    if (nxt.kind == RLT_GREATER) {
        if (!p_redirect_ok(p)) return RLN_NONE; /* `a > b` compares */
    }
    base = (unsigned short)*p->aused;
    for (;;) {
        struct rl_tok *t = p_cur(p);
        unsigned short aid;
        if (p->err) return RLN_NONE;
        if (t->kind == RLT_IDENT) {
            aid = p_mknode(p, RLN_WORD);
            if (p->err) return RLN_NONE;
            p->pool->nodes[aid].e1 = t->off;
            p->pool->nodes[aid].e2 = t->len;
            p_next(p);
        } else if (t->kind == RLT_INT || t->kind == RLT_STR ||
                   t->kind == RLT_TRUE || t->kind == RLT_FALSE) {
            aid = p_atom(p, src);
            if (p->err) return RLN_NONE;
        } else if (t->kind == RLT_MINUS) {
            struct rl_tok fl;
            if (!p_peek(p, &fl)) return RLN_NONE;
            if (fl.kind == RLT_IDENT && p_adjacent(t, &fl)) {
                p_next(p); /* dash */
                aid = p_mknode(p, RLN_WORD);
                if (p->err) return RLN_NONE;
                p->pool->nodes[aid].e1 = p_cur(p)->off;
                p->pool->nodes[aid].e2 = p_cur(p)->len;
                p->pool->nodes[aid].aux = 1; /* flag: text w/o dash */
                p_next(p);
            } else if (fl.kind == RLT_INT && p_adjacent(t, &fl)) {
                unsigned short lit;
                p_next(p); /* dash */
                lit = p_atom(p, src);
                if (p->err) return RLN_NONE;
                aid = p_mknode(p, RLN_UNOP);
                if (p->err) return RLN_NONE;
                p->pool->nodes[aid].op = RLOP_NEG;
                p->pool->nodes[aid].k1 = lit;
            } else {
                return RLN_NONE;
            }
        } else if (t->kind == RLT_GREATER) {
            /* Redirect: > "f" or >> "f" (target checked here; the
             * E-path never sees value-position redirects). */
            int dbl = 0;
            struct rl_tok tgt;
            p_next(p);
            if (p_at(p, RLT_GREATER) &&
                (unsigned int)p_cur(p)->off ==
                    (unsigned int)t->off + 1u) {
                dbl = 1;
                p_next(p);
            }
            if (!p_at(p, RLT_STR)) return RLN_NONE;
            tgt = *p_cur(p);
            aid = p_mknode(p, RLN_STR);
            if (p->err) return RLN_NONE;
            p->pool->nodes[aid].e1 =
                (unsigned int)(tgt.val - p->ss.base);
            p->pool->nodes[aid].e2 = tgt.vlen;
            p->pool->nodes[aid].aux = (unsigned char)(dbl ? 3 : 2);
            p_next(p);
        } else {
            break;
        }
        if (!p_arg_push(p, aid)) return RLN_NONE;
        nargs++;
        if (nargs > 250u) {
            p->err = RLP_NOMEM;
            return RLN_NONE;
        }
    }
    if (nargs == 0) return RLN_NONE;
    if (base > 255u || nargs > 255u) {
        p->err = RLP_NOMEM;
        return RLN_NONE;
    }
    if (!p_enter(p)) return RLN_NONE;
    id = p_mknode(p, RLN_CMD);
    p_leave(p);
    if (p->err) return RLN_NONE;
    p->pool->nodes[id].e1 =
        (unsigned int)name.off | ((unsigned int)name.len << 16);
    p->pool->nodes[id].e2 =
        (unsigned int)(nargs & 255u) | ((unsigned int)base << 8);
    p->pool->nodes[id].aux = (unsigned char)(nargs > 255u ? 255u : nargs);
    (void)src;
    return id;
}

static unsigned short p_cmd_or_expr(struct rlp *p, const char *src)
{
    struct rl_psave sv;
    unsigned short r;
    if (p->err) return RLN_NONE;
    if (p_cur(p)->kind != RLT_IDENT)
        return p_expr_yd(p, src);
    p_save(p, &sv);
    r = p_cmd(p, src);
    if (p->err == RLP_NOMEM) return RLN_NONE; /* defensive: propagate */
    if (p->err) {
        /* Defensive: p_cmd only returns NONE cleanly, but never
         * leak a half error into expression fallback. */
        p_restore(p, &sv);
        return p_expr_yd(p, src);
    }
    if (r != RLN_NONE) return r;
    p_restore(p, &sv);
    return p_expr_yd(p, src);
}

static unsigned short p_stage(struct rlp *p, const char *src)
{
    struct rl_tok nxt;
    unsigned short id;
    if (p->err) return RLN_NONE;
    if (p_cur(p)->kind == RLT_IDENT && p_peek(p, &nxt) &&
        p_lone_follow(nxt.kind)) {
        struct rl_tok name = *p_cur(p);
        p_next(p);
        if (!p_enter(p)) return RLN_NONE;
        id = p_mknode(p, RLN_CMD);
        p_leave(p);
        if (p->err) return RLN_NONE;
        p->pool->nodes[id].e1 =
            (unsigned int)name.off | ((unsigned int)name.len << 16);
        p->pool->nodes[id].e2 = 0;
        p->pool->nodes[id].aux = 0;
        p->pool->nodes[id].k1 = RLN_NONE;
        return id;
    }
    return p_cmd_or_expr(p, src);
}

static unsigned short p_pipeline(struct rlp *p, const char *src)
{
    unsigned short left = p_stage(p, src);
    unsigned short stages = 1, id;
    if (p->err) return RLN_NONE;
    if (!p_at(p, RLT_PIPEGT)) return left;
    for (;;) {
        unsigned short st;
        p_next(p);
        if (p_at(p, RLT_EOF)) {
            p->err = RLP_SYNTAX;
            return RLN_NONE;
        }
        st = p_stage(p, src);
        if (p->err) return RLN_NONE;
        stages++;
        if (stages > 2u) {
            /* Slice E parity: three-stage lines are loud syntax
             * errors with zero spawns (host accepts; guest rejects). */
            p->err = RLP_SYNTAX;
            return RLN_NONE;
        }
        id = p_mknode(p, RLN_PIPE);
        if (p->err) return RLN_NONE;
        p->pool->nodes[id].k1 = left;
        p->pool->nodes[id].k2 = st;
        left = id;
        if (!p_at(p, RLT_PIPEGT)) return left;
    }
}

static unsigned short p_let(struct rlp *p, const char *src)
{
    struct rl_tok name, tn;
    unsigned short init, id;
    unsigned char dt = 0;
    p_next(p); /* let */
    if (!p_at(p, RLT_IDENT)) {
        p->err = RLP_SYNTAX;
        return RLN_NONE;
    }
    name = *p_cur(p);
    p_next(p);
    if (!p_at(p, RLT_COLON)) {
        p->err = RLP_SYNTAX;
        return RLN_NONE;
    }
    p_next(p);
    if (p->err) return RLN_NONE;
    tn = *p_cur(p);
    if (tn.kind == RLT_INT_T) dt = RLV_INT;
    else if (tn.kind == RLT_BOOL_T) dt = RLV_BOOL;
    else if (tn.kind == RLT_STR_T) dt = RLV_STR;
    else {
        p->err = RLP_SYNTAX;
        return RLN_NONE;
    }
    p_next(p);
    if (!p_at(p, RLT_EQUAL)) {
        p->err = RLP_SYNTAX;
        return RLN_NONE;
    }
    p_next(p);
    init = p_pipeline(p, src);
    if (p->err) return RLN_NONE;
    id = p_mknode(p, RLN_LET);
    if (p->err) return RLN_NONE;
    p->pool->nodes[id].e1 = name.off;
    p->pool->nodes[id].e2 = name.len;
    p->pool->nodes[id].aux = dt;
    p->pool->nodes[id].k1 = init;
    return id;
}

void rl_parse_part(const char *src, unsigned int len,
                   struct rl_ppool *pool, struct rl_strscratch *ss,
                   unsigned short *argspace, unsigned int argcap,
                   unsigned int *argused, unsigned int *opstack,
                   unsigned int ocap, unsigned short *valstack,
                   unsigned int vcap, struct rl_proot *out)
{
    struct rlp p;
    unsigned short root;
    out->rc = RLP_SYNTAX;
    out->root = RLN_NONE;
    out->pad = 0;
    if (!src || !pool || !pool->nodes || !ss || !argspace || !argused ||
        !opstack || !valstack || !out) {
        out->rc = RLP_NOMEM;
        return;
    }
    pool->used = 0;
    *argused = 0;
    p_init(&p, src, len, pool, ss, argspace, argcap, argused, opstack,
           ocap, valstack, vcap);
    if (p_at(&p, RLT_LET))
        root = p_let(&p, src);
    else
        root = p_pipeline(&p, src);
    if (p.err) {
        out->rc = p.err == RLP_TOODEEP ? RLP_TOODEEP
            : p.err == RLP_NOMEM       ? RLP_NOMEM
                                       : RLP_SYNTAX;
        return;
    }
    if (!p_at(&p, RLT_EOF)) {
        out->rc = RLP_SYNTAX; /* trailing garbage */
        return;
    }
    ss->used = p.ss.used;
    out->rc = RLP_OK;
    out->root = root;
}
