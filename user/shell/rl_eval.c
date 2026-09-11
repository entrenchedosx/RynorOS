/* Stage 18d Slice F tree-walk evaluator. See rl_eval.h. */
#include "rl_eval.h"

#define RL_MASK64 0xFFFFFFFFFFFFFFFFull
#define RL_SIGNBIT 0x8000000000000000ull
#define RL_INT_MIN 0x8000000000000000ull

static unsigned long long rl_uadd(unsigned long long a,
                                  unsigned long long b)
{
    return (a + b) & RL_MASK64;
}

static unsigned long long rl_usub(unsigned long long a,
                                  unsigned long long b)
{
    return (a - b) & RL_MASK64;
}

static unsigned long long rl_umul(unsigned long long a,
                                  unsigned long long b)
{
    return (a * b) & RL_MASK64;
}

static unsigned long long rl_uneg(unsigned long long a)
{
    return (0ull - a) & RL_MASK64;
}

/* Trunc-toward-zero quotient magnitude; signs handled by caller. */
static unsigned long long rl_udivmag(unsigned long long ma,
                                     unsigned long long mb)
{
    return mb == 0u ? 0u : ma / mb;
}

/* Signed less-than via bias flip (portable, no signed UB). */
static int rl_slt(unsigned long long a, unsigned long long b)
{
    return (a ^ RL_SIGNBIT) < (b ^ RL_SIGNBIT);
}

static int rl_seq64(unsigned long long a, unsigned long long b)
{
    return a == b;
}

static int rl_memeq(const char *a, const char *b, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < n; ++i)
        if (a[i] != b[i]) return 0;
    return 1;
}

static void rl_vint(struct rl_val *v, unsigned long long bits)
{
    v->type = RLV_INT;
    v->pad[0] = 0;
    v->pad[1] = 0;
    v->pad[2] = 0;
    v->len = 0;
    v->a = bits & RL_MASK64;
}

static void rl_vbool(struct rl_val *v, unsigned long long b)
{
    v->type = RLV_BOOL;
    v->pad[0] = 0;
    v->pad[1] = 0;
    v->pad[2] = 0;
    v->len = 0;
    v->a = b ? 1u : 0u;
}

static void rl_vstr(struct rl_val *v, const char *p, unsigned int n)
{
    v->type = RLV_STR;
    v->pad[0] = 0;
    v->pad[1] = 0;
    v->pad[2] = 0;
    v->len = n;
    v->a = (unsigned long long)p;
}

static void rl_vunit(struct rl_val *v)
{
    v->type = RLV_UNIT;
    v->pad[0] = 0;
    v->pad[1] = 0;
    v->pad[2] = 0;
    v->len = 0;
    v->a = 0;
}

int rl_eval(struct rl_eval_ctx *ctx, unsigned short root,
            struct rl_val *out)
{
    unsigned int wtop = 0, vtop = 0;
    if (!ctx || !ctx->pool || !ctx->walk || !ctx->vstack || !out)
        return RL_EV_INTERNAL;
    if (root == RL_NONODE || root >= RL_POOL_MAX)
        return RL_EV_INTERNAL;
    if (ctx->walkcap == 0u || ctx->vcap == 0u)
        return RL_EV_INTERNAL;
    ctx->walk[wtop++] = (unsigned int)root;
    while (wtop > 0) {
        unsigned int e = ctx->walk[--wtop];
        unsigned short i = (unsigned short)(e & 0x7FFFu);
        unsigned int vis = (e >> 15) & 1u;
        struct rl_node *nd;
        if (i >= RL_POOL_MAX) return RL_EV_INTERNAL;
        nd = &ctx->pool[i];
        if (!vis) {
            switch (nd->kind) {
            case RLN_INT: {
                struct rl_val v;
                unsigned long long bits =
                    ((unsigned long long)nd->e2 << 32) |
                    (unsigned long long)nd->e1;
                rl_vint(&v, bits);
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
                break;
            }
            case RLN_BOOL: {
                struct rl_val v;
                rl_vbool(&v, nd->aux);
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
                break;
            }
            case RLN_STR: {
                struct rl_val v;
                const char *p = ctx->strbase + nd->e1;
                if (!ctx->strbase) return RL_EV_INTERNAL;
                rl_vstr(&v, p, nd->e2);
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
                break;
            }
            case RLN_VAR: {
                struct rl_val v;
                unsigned int s = nd->k2;
                if (!ctx->syms || s >= ctx->nsyms)
                    return RL_EV_INTERNAL;
                if (ctx->syms[s].type == RLV_INT)
                    rl_vint(&v, ctx->syms[s].pay);
                else if (ctx->syms[s].type == RLV_BOOL)
                    rl_vbool(&v, ctx->syms[s].pay);
                else if (ctx->syms[s].type == RLV_STR) {
                    const char *p;
                    if (!ctx->sess_arena) return RL_EV_INTERNAL;
                    p = ctx->sess_arena + ctx->syms[s].pay;
                    rl_vstr(&v, p, ctx->syms[s].str_len);
                } else {
                    return RL_EV_INTERNAL;
                }
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
                break;
            }
            case RLN_UNOP:
                if (nd->k1 == RL_NONODE) return RL_EV_INTERNAL;
                if (wtop + 2u > ctx->walkcap) return RL_EV_INTERNAL;
                ctx->walk[wtop++] =
                    (unsigned short)(((unsigned int)i) | (1u << 15));
                ctx->walk[wtop++] = (unsigned int)nd->k1;
                break;
            case RLN_BINOP:
                if (nd->k1 == RL_NONODE || nd->k2 == RL_NONODE)
                    return RL_EV_INTERNAL;
                if (wtop + 3u > ctx->walkcap) return RL_EV_INTERNAL;
                ctx->walk[wtop++] =
                    (unsigned short)(((unsigned int)i) | (1u << 15));
                ctx->walk[wtop++] = (unsigned int)nd->k2;
                ctx->walk[wtop++] = (unsigned int)nd->k1;
                break;
            case RLN_CALL:
                if (nd->k1 == RL_NONODE) return RL_EV_INTERNAL;
                if (wtop + 2u > ctx->walkcap) return RL_EV_INTERNAL;
                ctx->walk[wtop++] =
                    (unsigned short)(((unsigned int)i) | (1u << 15));
                ctx->walk[wtop++] = (unsigned int)nd->k1;
                break;
            case RLN_LEN:
                /* Slice G len(arg): same walk shape as CALL. Only
                 * str-typed arguments reach evaluation ( statically
                 * rejected otherwise); the result is an int. */
                if (nd->k1 == RL_NONODE) return RL_EV_INTERNAL;
                if (wtop + 2u > ctx->walkcap) return RL_EV_INTERNAL;
                ctx->walk[wtop++] =
                    (unsigned short)(((unsigned int)i) | (1u << 15));
                ctx->walk[wtop++] = (unsigned int)nd->k1;
                break;
            default:
                /* Commands, pipelines, and lets never evaluate to
                 * values (unit-typed, statically rejected in value
                 * position; statements funnel to the E executor). */
                return RL_EV_INTERNAL;
            }
        } else {
            if (nd->kind == RLN_UNOP) {
                struct rl_val a, v;
                if (vtop < 1u) return RL_EV_INTERNAL;
                a = ctx->vstack[--vtop];
                if (nd->op == RLOP_NEG) {
                    if (a.type != RLV_INT) return RL_EV_INTERNAL;
                    rl_vint(&v, rl_uneg(a.a));
                } else if (nd->op == RLOP_NOT) {
                    if (a.type != RLV_BOOL) return RL_EV_INTERNAL;
                    rl_vbool(&v, a.a == 0u);
                } else {
                    return RL_EV_INTERNAL;
                }
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
            } else if (nd->kind == RLN_BINOP) {
                struct rl_val a, b, v;
                if (vtop < 2u) return RL_EV_INTERNAL;
                b = ctx->vstack[--vtop];
                a = ctx->vstack[--vtop];
                switch (nd->op) {
                case RLOP_ADD:
                    if (a.type != RLV_INT || b.type != RLV_INT)
                        return RL_EV_INTERNAL;
                    rl_vint(&v, rl_uadd(a.a, b.a));
                    break;
                case RLOP_SUB:
                    if (a.type != RLV_INT || b.type != RLV_INT)
                        return RL_EV_INTERNAL;
                    rl_vint(&v, rl_usub(a.a, b.a));
                    break;
                case RLOP_MUL:
                    if (a.type != RLV_INT || b.type != RLV_INT)
                        return RL_EV_INTERNAL;
                    rl_vint(&v, rl_umul(a.a, b.a));
                    break;
                case RLOP_DIV:
                case RLOP_MOD: {
                    unsigned long long ma, mb, q, r;
                    int nega, negb, negq;
                    if (a.type != RLV_INT || b.type != RLV_INT)
                        return RL_EV_INTERNAL;
                    if (b.a == 0u ||
                        (a.a == RL_INT_MIN && b.a == RL_MASK64))
                        return RL_EV_TRAP;
                    nega = (a.a & RL_SIGNBIT) != 0u;
                    negb = (b.a & RL_SIGNBIT) != 0u;
                    ma = nega ? rl_uneg(a.a) : a.a;
                    mb = negb ? rl_uneg(b.a) : b.a;
                    q = rl_udivmag(ma, mb);
                    negq = (nega != negb);
                    if (nd->op == RLOP_DIV) {
                        rl_vint(&v, negq ? rl_uneg(q) : q);
                    } else {
                        r = rl_usub(a.a, rl_umul(negq ? rl_uneg(q) : q,
                                                b.a));
                        rl_vint(&v, r);
                    }
                    break;
                }
                case RLOP_EQ:
                case RLOP_NE: {
                    int eq;
                    if (a.type != b.type) return RL_EV_INTERNAL;
                    if (a.type == RLV_STR) {
                        const char *pa = (const char *)a.a;
                        const char *pb = (const char *)b.a;
                        eq = (a.len == b.len) &&
                            rl_memeq(pa, pb, a.len);
                    } else if (a.type == RLV_INT ||
                               a.type == RLV_BOOL) {
                        eq = rl_seq64(a.a, b.a);
                    } else {
                        return RL_EV_INTERNAL;
                    }
                    if (nd->op == RLOP_NE) eq = !eq;
                    rl_vbool(&v, (unsigned long long)eq);
                    break;
                }
                case RLOP_LT:
                case RLOP_GT:
                case RLOP_LE:
                case RLOP_GE: {
                    int r2;
                    if (a.type != RLV_INT || b.type != RLV_INT)
                        return RL_EV_INTERNAL;
                    if (nd->op == RLOP_LT) r2 = rl_slt(a.a, b.a);
                    else if (nd->op == RLOP_GT) r2 = rl_slt(b.a, a.a);
                    else if (nd->op == RLOP_LE) r2 = !rl_slt(b.a, a.a);
                    else r2 = !rl_slt(a.a, b.a);
                    rl_vbool(&v, (unsigned long long)r2);
                    break;
                }
                case RLOP_AND:
                case RLOP_OR: {
                    int x = (a.type == RLV_BOOL && a.a != 0u);
                    int y = (b.type == RLV_BOOL && b.a != 0u);
                    if (a.type != RLV_BOOL || b.type != RLV_BOOL)
                        return RL_EV_INTERNAL;
                    if (nd->op == RLOP_AND)
                        rl_vbool(&v, (unsigned long long)(x && y));
                    else
                        rl_vbool(&v, (unsigned long long)(x || y));
                    break;
                }
                default:
                    return RL_EV_INTERNAL;
                }
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
            } else if (nd->kind == RLN_CALL) {
                /* print(arg): render and emit, yield unit. Only
                 * print reaches evaluation (unknown callees and bad
                 * arity are static rejections). */
                struct rl_val a, v;
                char rbuf[24];
                unsigned int rn;
                if (vtop < 1u) return RL_EV_INTERNAL;
                a = ctx->vstack[--vtop];
                if (a.type != RLV_INT && a.type != RLV_BOOL &&
                    a.type != RLV_STR)
                    return RL_EV_INTERNAL;
                rn = rl_render(&a, rbuf, sizeof(rbuf));
                if (a.type == RLV_STR) {
                    if (ctx->emit)
                        ctx->emit((const char *)a.a, a.len);
                } else {
                    if (rn == 0u) return RL_EV_INTERNAL;
                    if (ctx->emit) ctx->emit(rbuf, rn);
                }
                rl_vunit(&v);
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
            } else if (nd->kind == RLN_LEN) {
                /* len(arg): pure byte length of the evaluated runtime
                 * string (decoded bytes, excluding any C NUL the
                 * implementation may use elsewhere). No emission, no
                 * spawn, no session effect: the value alone decides. */
                struct rl_val a, v;
                if (vtop < 1u) return RL_EV_INTERNAL;
                a = ctx->vstack[--vtop];
                if (a.type != RLV_STR) return RL_EV_INTERNAL;
                rl_vint(&v, (unsigned long long)a.len);
                if (vtop >= ctx->vcap) return RL_EV_INTERNAL;
                ctx->vstack[vtop++] = v;
            } else {
                return RL_EV_INTERNAL;
            }
        }
    }
    if (vtop != 1u) return RL_EV_INTERNAL;
    *out = ctx->vstack[0];
    return RL_EV_OK;
}
