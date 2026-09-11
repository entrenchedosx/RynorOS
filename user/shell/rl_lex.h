/* Stage 18d Slice F RynorLang lexer (CPL3, freestanding, pure).
 *
 * Frozen token surface mirroring the frozen host lexer in the shell
 * edition: ASCII-only source, `//` line comments, identifiers +
 * keywords (fn let if else while return true false int bool str),
 * decimal integers (lexical i64-max bound), double-quoted strings
 * with \\ \" \n \t escapes only, double operators (== != <= >= &&
 * || ->), shell-only |> , and single operators. `|`, `.`, `$`, `?`
 * and all other bytes are invalid (Slice E parity: they stay loud
 * syntax errors, never silent).
 *
 * Position-based (no token array): the parser pulls one token at a
 * time and may save/restore the lexer position for command-vs-
 * expression backtracking. String values decode into caller scratch
 * (bounded: decoded output never exceeds the token span).
 *
 * No spans are tracked (guest rows carry deterministic classes only;
 * host-vs-guest span parity is out of scope by design).
 */
#ifndef RYNOR_RL_LEX_H
#define RYNOR_RL_LEX_H

/* Token kinds (stable for tests). */
#define RLT_EOF 0
#define RLT_IDENT 1
#define RLT_INT 2
#define RLT_STR 3
#define RLT_TRUE 4
#define RLT_FALSE 5
#define RLT_LET 6
#define RLT_FN 7
#define RLT_IF 8
#define RLT_ELSE 9
#define RLT_WHILE 10
#define RLT_RETURN 11
#define RLT_INT_T 12
#define RLT_BOOL_T 13
#define RLT_STR_T 14
#define RLT_PLUS 15
#define RLT_MINUS 16
#define RLT_STAR 17
#define RLT_SLASH 18
#define RLT_PERCENT 19
#define RLT_BANG 20
#define RLT_EQUAL 21
#define RLT_EQEQ 22
#define RLT_BANGEQ 23
#define RLT_LESS 24
#define RLT_GREATER 25
#define RLT_LESSEQ 26
#define RLT_GREATEREQ 27
#define RLT_ANDAND 28
#define RLT_OROR 29
#define RLT_ARROW 30
#define RLT_PIPEGT 31
#define RLT_LPAREN 32
#define RLT_RPAREN 33
#define RLT_LBRACE 34
#define RLT_RBRACE 35
#define RLT_SEMI 36
#define RLT_COMMA 37
#define RLT_COLON 38

/* Lex outcome codes (mapped to PAR_LEX_ERROR family rows by caller). */
#define RLL_OK 0
#define RLL_BADCHAR 1  /* invalid byte (incl. non-ASCII, lone | . $ ?) */
#define RLL_INTOFLOW 2 /* decimal exceeds 9223372036854775807 */
#define RLL_UNTERM 3   /* unterminated string (EOF or raw newline) */
#define RLL_BADESC 4   /* unsupported backslash escape */

struct rl_tok {
    unsigned char kind;
    unsigned char pad;
    unsigned short off;  /* byte offset into the source */
    unsigned short len;  /* lexeme length in bytes */
    unsigned short vlen; /* decoded value length (strings only) */
    const char *val;     /* decoded string bytes (strings only) */
};

struct rl_lex {
    const char *src;
    unsigned int len;
    unsigned int pos;
};

/* Scratch for decoded string bytes (caller-owned, e.g. submission
 * arena bump space; decoded output is always shorter than input). */
struct rl_strscratch {
    char *base;
    unsigned int cap;
    unsigned int used;
};

void rl_lex_init(struct rl_lex *lx, const char *src, unsigned int len);
/* Save/restore for backtracking (position only; string scratch
 * rewind is the caller's job via rl_strscratch.used). */
unsigned int rl_lex_mark(const struct rl_lex *lx);
void rl_lex_rewind(struct rl_lex *lx, unsigned int mark);
/* Next token (kind RLT_EOF at end). Never fails except via *rc for
 * BADCHAR/INTOFLOW/UNTERM/BADESC; token contents valid iff RLL_OK. */
void rl_lex_next(struct rl_lex *lx, struct rl_strscratch *ss,
                 struct rl_tok *out, int *rc);

#endif
