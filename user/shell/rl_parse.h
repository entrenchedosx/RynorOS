/* Stage 18d Slice F item parser (CPL3, freestanding).
 *
 * Parses one `;`-free submission part (or one accepted session item
 * during rebuild) into a typed-later tree in submission-arena memory.
 * Mirrors the frozen host parser in the shell edition, narrowed to
 * the 18d-base subset:
 *
 * - precedence climbing 1..6 (||, &&, ==/!=, relational, additive,
 *   multiplicative), left associative, exactly like the host loop;
 * - precedence-0 left-associative |> pipelines, iterative, but at most
 *   TWO stages (a third |> is a parse error: Slice E parity, where
 *   three-stage lines are loud syntax errors with zero spawns);
 * - unary -/! (right associative), grouping parens (elided from the
 *   tree; depth still charged), print(...) calls;
 * - top-level annotated let (no trailing semicolon inside a part);
 * - host-identical command disambiguation: lone bare words and
 *   juxtaposition commands become Cmd nodes at stage level only;
 *   operands keep v1 meanings (no Cmd inside binary operands);
 * - depth charged exactly like the host (unary, grouping paren, call
 *   argument group, Cmd), with the Slice F bound RL_DEPTH_MAX 64.
 *
 * Nodes live in a fixed 256-entry pool of 16-byte records addressed
 * by u16 index (0xFFFF = none); string bytes land in caller scratch.
 * A 256-byte part holds at most ~256 tokens, hence at most ~256
 * nodes, so the pool cannot overflow on legal input (a defensive
 * cap error exists regardless).
 */
#ifndef RYNOR_RL_PARSE_H
#define RYNOR_RL_PARSE_H

#include "rl_lex.h"

/* Node kinds. */
#define RLN_INT 1
#define RLN_BOOL 2
#define RLN_STR 3
#define RLN_VAR 4
#define RLN_UNOP 5
#define RLN_BINOP 6
#define RLN_CALL 7   /* print(...) only (checked in semantics) */
#define RLN_CMD 8    /* external command candidate */
#define RLN_PIPE 9   /* two-stage |> */
#define RLN_LET 10
#define RLN_WORD 11  /* bare-word command argument (text or var bytes) */

/* Value types (filled by semantics; 0 = not yet typed). */
#define RLV_UNKNOWN 0
#define RLV_INT 1
#define RLV_BOOL 2
#define RLV_STR 3
#define RLV_UNIT 4

/* Operators. */
#define RLOP_NEG 1
#define RLOP_NOT 2
#define RLOP_ADD 3
#define RLOP_SUB 4
#define RLOP_MUL 5
#define RLOP_DIV 6
#define RLOP_MOD 7
#define RLOP_EQ 8
#define RLOP_NE 9
#define RLOP_LT 10
#define RLOP_GT 11
#define RLOP_LE 12
#define RLOP_GE 13
#define RLOP_AND 14
#define RLOP_OR 15

#define RL_NONODE 0xFFFFu
#define RL_POOL_MAX 256u
#define RL_DEPTH_MAX 64u

/* 16-byte node: kind/type/op/aux + payload + two child indices. */
struct rl_node {
    unsigned char kind;
    unsigned char vtype;
    unsigned char op;
    unsigned char aux;
    unsigned int e1;
    unsigned int e2;
    unsigned short k1;
    unsigned short k2;
};

/* Payload conventions:
 * INT:  ival bits across e1(low)+e2(high) (reconstructed by sem).
 * BOOL: aux 0/1.
 * STR:  e1 = scratch offset, e2 = decoded length.
 * VAR/WORD/CMD-name/LET-name: e1 = source offset, e2 = source length
 *   (into the parsed text; stable for the submission).
 * CMD: aux = nargs; k1 = first command-arg node (args chained via
 *   k2); each arg is INT/STR/BOOL/WORD/UNOP(-int)/flag-WORD.
 *   Flags are WORD nodes with aux = 1 and text excluding the dash.
 *   Redirect targets are STR nodes chained after args with aux = 2
 *   on the target node and text of op in ... (op char in aux of a
 *   tiny marker? redirects need op >/>>: encode as WORD aux=3/4?
 *   Simpler: redirect target STR node aux = 2 (>) or 3 (>>).)
 * CALL: k1 = single argument node (arity checked in semantics).
 * LET:  e1/e2 = name span; aux = declared type (RLV_*); k1 = init.
 * PIPE: k1/k2 = stages.
 */

struct rl_ppool {
    struct rl_node *nodes;
    unsigned int cap;
    unsigned int used;
};

/* Parse outcome. */
#define RLP_OK 0
#define RLP_SYNTAX 1  /* any grammar rejection (-> [SH] error syntax) */
#define RLP_TOODEEP 2 /* depth bound (-> [SH] error syntax row as well) */
#define RLP_NOMEM 3   /* defensive pool/space exhaustion (unreachable) */

struct rl_proot {
    int rc;
    unsigned short root; /* node index, valid iff rc == RLP_OK */
    unsigned short pad;
};

/* Parse one part (no top-level ';' inside). Scratch/string memory
 * comes from the caller's submission arena via pool + strscratch.
 * argspace holds command argument index vectors (u16 each); the
 * opstack/valstack pair backs the iterative shunting-yard driver
 * (O(1) C stack regardless of expression depth). */
void rl_parse_part(const char *src, unsigned int len,
                   struct rl_ppool *pool, struct rl_strscratch *ss,
                   unsigned short *argspace, unsigned int argcap,
                   unsigned int *argused, unsigned int *opstack,
                   unsigned int ocap, unsigned short *valstack,
                   unsigned int vcap, struct rl_proot *out);

#endif
