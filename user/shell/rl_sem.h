/* Stage 18d Slice F session,transaction,arenas (CPL3, freestanding).
 *
 * Whole-buffer re-analysis with transactional commit:
 * - Persistent state = accepted session source bytes (<= RL_SESS_MAX)
 *   plus the committed symbol table derived from them. NOTHING else
 *   persists (no value cache: every success rebuilds values from text).
 * - Each submission rebuilds a candidate table from (accepted source
 *   + candidate) item-at-a-time in scratch space, evaluates the
 *   candidate, and swaps/commits only on total success. Failures
 *   discard everything candidate-side; persistent bytes, symbols,
 *   values, and the session arena are untouched.
 * - Dual arenas: session (committed string bytes only; repacked on
 *   commit so orphans cannot accumulate) and submission (all transient
 *   parse/analyze/eval state; reset per item during rebuild and
 *   wholesale after every submission attempt on every path).
 * - Ownership invariant: every committed str pointer addresses the
 *   session arena. rl_own_check() verifies it (F-M6 tripwire).
 *
 * Frozen bounds (sizes pinned by repository test reading these):
 *   session source <= 8192 bytes (RL_SESS_MAX)
 *   persistent lets <= 128 (RL_SYM_MAX)
 *   expression depth <= 64 (RL_DEPTH_MAX, rl_parse.h)
 * Diagnostic classes: the PAR, SEM, and SHELL families mirror the
 * frozen host analyzer; RL_SESSION_LIMIT, RL_SYMBOL_LIMIT,
 * RL_PIPELINE_STAGE, RL_NOTIMPL, RL_EVAL_TRAP, and RL_ARENA_FULL are
 * guest-bound classes with no host analog (documented divergences,
 * never prose-compared).
 */
#ifndef RYNOR_RL_SEM_H
#define RYNOR_RL_SEM_H

#include "rl_parse.h"
#include "rl_mem.h"

#define RL_SESS_MAX 8192u
#define RL_SYM_MAX 128u
#define RL_SESS_ARENA 4608u
#define RL_SUB_ARENA 6688u
/* Submission carve (sums to RL_SUB_ARENA): node pool, decoded
 * strings, command arg vectors, phased aux (parse op/val stacks,
 * analyzer walk/type stacks, evaluator walk/value stacks).
 * Walk stacks hold u16-packed (index|visited<<15) entries: binary
 * chains do not charge depth (host parity), so tree depth can reach
 * ~192 on legal input while C stack stays O(1); the 512-entry bound
 * covers 2x256 nodes with margin (defensive error beyond). */
#define RL_SUB_POOL_NODES 256u
#define RL_SUB_STRB 256u
#define RL_SUB_ARGS 128u
#define RL_SUB_AUX 2080u
#define RL_SUB_WALK 512u
#define RL_SUB_VCAP 66u
#define RL_SUB_TYPESTK 72u
#define RL_SUB_OPS 256u
#define RL_SUB_PVALS 256u

/* Host-parity diagnostic classes (exact strings). */
#define RL_D_PAR_LEX "PAR_LEX_ERROR"
#define RL_D_PAR_UNEXP_TOKEN "PAR_UNEXPECTED_TOKEN"
#define RL_D_PAR_UNEXP_EOF "PAR_UNEXPECTED_EOF"
#define RL_D_PAR_EXPECTED "PAR_EXPECTED_TOKEN"
#define RL_D_PAR_DEPTH "PAR_DEPTH_EXCEEDED"
#define RL_D_PAR_FILE "PAR_FILE_TOO_LARGE"
#define RL_D_SEM_UNDECLARED "SEM_UNDECLARED"
#define RL_D_SEM_DUPLICATE "SEM_DUPLICATE"
#define RL_D_SEM_TYPE "SEM_TYPE_MISMATCH"
#define RL_D_SEM_ARITY "SEM_ARITY_MISMATCH"
#define RL_D_SEM_UNKNOWN_FN "SEM_UNKNOWN_FUNCTION"
#define RL_D_SEM_LIMIT "SEM_LIMIT_EXCEEDED"
#define RL_D_SH_UNKNOWN_CMD "SHELL_UNKNOWN_COMMAND"
#define RL_D_SH_PIPE_TYPE "SHELL_PIPELINE_TYPE_MISMATCH"
#define RL_D_SH_UNIT_STAGE "SHELL_UNIT_STAGE"
#define RL_D_SH_REDIRECT "SHELL_REDIRECT_ERROR"
/* Defensive-unreachable class (internal resource/invariant fault;
 * never fires on legal input; distinct from user-error classes). */
#define RL_D_INTERNAL "RL_INTERNAL"
/* Ownership-checker violation (staged value escaped session
 * ownership; commit blocked). */
#define RL_D_OWNFAIL "RL_OWNERSHIP_FAIL"
/* Guest-bound classes (no host analog). */
#define RL_D_SESSION_LIMIT "RL_SESSION_LIMIT"
#define RL_D_SYMBOL_LIMIT "RL_SYMBOL_LIMIT"
#define RL_D_PIPELINE_STAGE "RL_PIPELINE_STAGE"
#define RL_D_NOTIMPL "RL_NOTIMPL"
#define RL_D_EVAL_TRAP "RL_EVAL_TRAP"
#define RL_D_ARENA_FULL "RL_ARENA_FULL"
/* NOTIMPL subjects (row detail). */
#define RL_K_FN "fn"
#define RL_K_IF "if"
#define RL_K_WHILE "while"
#define RL_K_RETURN "return"
#define RL_K_BLOCK "block"

/* Submit outcome kinds. */
#define RL_SUB_OK 0
#define RL_SUB_SYNTAX 1
#define RL_SUB_REJECT 2
#define RL_SUB_NOTIMPL 3
#define RL_SUB_TRAP 4
#define RL_SUB_ARENAFULL 5
#define RL_SUB_OWNFAIL 6
#define RL_SUB_INTERNAL 7

/* Evaluated value (inline; str references stable memory owned by
 * the arena or session indicated at the use site). */
struct rl_val {
    unsigned char type; /* RLV_* */
    unsigned char pad[3];
    unsigned int len;   /* str only */
    unsigned long long a; /* int bits / bool 0-1 / str address */
};

struct rl_outcome {
    int kind;
    const char *diag; /* class/keyword/detail (rodata literal) */
    struct rl_val val; /* valid iff kind == RL_SUB_OK (bare value) */
};

/* Committed/candidate symbol row (16 bytes). */
struct rl_sym {
    unsigned short name_off; /* session source offset (committed) */
    unsigned short name_len;
    unsigned char type;      /* RLV_INT/BOOL/STR */
    unsigned char flags;
    unsigned int str_len;    /* str only */
    unsigned long long pay;  /* int bits / bool / session arena off */
};

struct rl_stats {
    unsigned int sess_live;
    unsigned int sess_high;
    unsigned int sub_live;
    unsigned int sub_high;
    unsigned int nsyms;
    unsigned int srclen;
};

struct rl_sess {
    char src[RL_SESS_MAX + 1u];
    unsigned int srclen;
    struct rl_sym syms[RL_SYM_MAX];
    unsigned int nsyms;
    struct rl_sym cand[RL_SYM_MAX];
    unsigned int ncand;
    unsigned char sess_arena[RL_SESS_ARENA];
    unsigned int sess_used;
    unsigned int sess_high;
    unsigned char sub_arena[RL_SUB_ARENA];
    unsigned int sub_high;
    void (*emit)(const char *s, unsigned int n);
};

void rl_sess_init(void (*emit)(const char *s, unsigned int n));
/* Submit one `;`-free language part. Contract: sh.c routes ONLY
 * language-shaped parts here (E commands/NOTIMPL/syntax handled
 * outside); defensively total on any input (never hangs/crashes).
 * The outcome bare value (str case) references submission memory:
 * the caller must consume it before rl_sub_reset(). */
struct rl_outcome rl_submit(const char *part, unsigned int len);
/* Wholesale submission reset (every path: success, all errors,
 * Ctrl-C aborts, E parts after classification scratch). */
void rl_sub_reset(void);
/* 1 iff a session variable with these bytes is committed. */
int rl_declared(const char *nm, unsigned int nlen);
void rl_stats(struct rl_stats *out);
/* Submission scratch carve (node pool, string bytes, arg vector,
 * phased aux). Returns 0 when the arena cannot serve (defensive;
 * sized to fit by construction). Caller must rl_sub_reset() after
 * use on every path. */
struct rl_carve {
    struct rl_node *pool;
    char *strb;
    unsigned short *args;
    unsigned int *ops;
    unsigned short *vals;
};
int rl_carve(struct rl_carve *out);
/* Ownership invariant over a symbol table with a given session
 * arena watermark: every str row is promotion-clean (no submission
 * residue) and addresses live session bytes. Runs pre-swap on the
 * staged candidate (violations block the commit) and doubles as the
 * F-M6 tripwire. */
int rl_own_check_tab(const struct rl_sym *tab, unsigned int ntab,
                     unsigned int sess_used);
/* Render a value to bytes (no newline, host rt_print mirror):
 * int = signed decimal, bool = true/false, str = raw bytes.
 * Returns length (0 on bad type/overflow of buf). */
unsigned int rl_render(const struct rl_val *v, char *buf,
                       unsigned int cap);

#endif
