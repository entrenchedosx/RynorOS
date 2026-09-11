/* Stage 18d Slice F tree-walk evaluator (CPL3, freestanding).
 *
 * Iterative post-order walk (O(1) C stack at any expression depth):
 * explicit (node,visited) walk stack plus a live-value stack bounded
 * by depth+1. Only pure language nodes are reachable here: literals,
 * variables (resolved to symbol indices by semantics), unary/binary
 * operators, print calls, and the Slice G len builtin (pure int
 * result, no emission). Command/pipeline/let nodes in value
 * position are statically rejected before evaluation (commands yield
 * unit; 18d-base has no output-capture channel), so reaching one is
 * an internal error, never user input.
 *
 * Integer semantics mirror the frozen C/x86-64 contract (see
 * the frozen host integer-constant oracle, re-derived here, never imported):
 * two's-complement wrap on +,-,*; trunc-toward-zero division with
 * dividend-signed remainder; trap on zero divisor and INT_MIN/-1;
 * signed comparisons; eager canonical-bool logic. Implemented with
 * unsigned arithmetic only (no C signed-overflow UB anywhere).
 */
#ifndef RYNOR_RL_EVAL_H
#define RYNOR_RL_EVAL_H

#include "rl_parse.h"
#include "rl_sem.h"

#define RL_EV_OK 0
#define RL_EV_TRAP 1
#define RL_EV_INTERNAL 2

struct rl_eval_ctx {
    struct rl_node *pool;
    const char *strbase;      /* submission string scratch */
    const struct rl_sym *syms; /* candidate table under construction */
    unsigned int nsyms;
    const char *sess_arena;   /* committed string bytes */
    unsigned short *walk;   /* (idx | visited<<15) stack */
    unsigned int walkcap;
    struct rl_val *vstack;
    unsigned int vcap;
    void (*emit)(const char *s, unsigned int n); /* print sink */
};

/* Evaluate a typed subtree to a value. TRAP iff div0/INT_MIN/-1. */
int rl_eval(struct rl_eval_ctx *ctx, unsigned short root,
            struct rl_val *out);

#endif
