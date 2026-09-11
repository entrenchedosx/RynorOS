/* Stage 18d Slice F lexer. See rl_lex.h for the frozen surface. */
#include "rl_lex.h"

#define RL_I64_MAX_TXT "9223372036854775807"
#define RL_I64_MAX_LEN 19u

static int rl_is_alpha(unsigned char c)
{
    return c == '_' || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
}

static int rl_is_digit(unsigned char c)
{
    return c >= '0' && c <= '9';
}

static int rl_is_alnum(unsigned char c)
{
    return rl_is_alpha(c) || rl_is_digit(c);
}

/* Keyword table (exact match, frozen spellings). */
static unsigned char rl_keyword(const char *s, unsigned int n)
{
    if (n == 2) {
        if (s[0] == 'f' && s[1] == 'n') return RLT_FN;
        if (s[0] == 'i' && s[1] == 'f') return RLT_IF;
    } else if (n == 3) {
        if (s[0] == 'l' && s[1] == 'e' && s[2] == 't') return RLT_LET;
        if (s[0] == 'i' && s[1] == 'n' && s[2] == 't') return RLT_INT_T;
        if (s[0] == 's' && s[1] == 't' && s[2] == 'r') return RLT_STR_T;
    } else if (n == 4) {
        if (s[0] == 't' && s[1] == 'r' && s[2] == 'u' && s[3] == 'e')
            return RLT_TRUE;
        if (s[0] == 'e' && s[1] == 'l' && s[2] == 's' && s[3] == 'e')
            return RLT_ELSE;
        if (s[0] == 'b' && s[1] == 'o' && s[2] == 'o' && s[3] == 'l')
            return RLT_BOOL_T;
    } else if (n == 5) {
        if (s[0] == 'w' && s[1] == 'h' && s[2] == 'i' && s[3] == 'l' &&
            s[4] == 'e')
            return RLT_WHILE;
        if (s[0] == 'f' && s[1] == 'a' && s[2] == 'l' && s[3] == 's' &&
            s[4] == 'e')
            return RLT_FALSE;
    } else if (n == 6) {
        if (s[0] == 'r' && s[1] == 'e' && s[2] == 't' && s[3] == 'u' &&
            s[4] == 'r' && s[5] == 'n')
            return RLT_RETURN;
    }
    return RLT_IDENT;
}

void rl_lex_init(struct rl_lex *lx, const char *src, unsigned int len)
{
    lx->src = src;
    lx->len = len;
    lx->pos = 0;
}

unsigned int rl_lex_mark(const struct rl_lex *lx)
{
    return lx->pos;
}

void rl_lex_rewind(struct rl_lex *lx, unsigned int mark)
{
    if (mark <= lx->len)
        lx->pos = mark;
}

/* Textual i64-max check (leading zeros stripped): mirrors the host
 * rule exactly without any arithmetic that could overflow. */
static int rl_int_overflow(const char *s, unsigned int n)
{
    unsigned int i = 0;
    while (i < n && s[i] == '0') ++i;
    if (i == n) return 0; /* all zeros */
    n -= i;
    s += i;
    if (n > RL_I64_MAX_LEN) return 1;
    if (n < RL_I64_MAX_LEN) return 0;
    for (i = 0; i < n; ++i) {
        char a = s[i], b = RL_I64_MAX_TXT[i];
        if (a != b) return a > b;
    }
    return 0;
}

static void rl_emit(struct rl_tok *out, unsigned char kind,
                    unsigned int off, unsigned int len)
{
    out->kind = kind;
    out->pad = 0;
    out->off = (unsigned short)off;
    out->len = (unsigned short)len;
    out->vlen = 0;
    out->val = 0;
}

void rl_lex_next(struct rl_lex *lx, struct rl_strscratch *ss,
                 struct rl_tok *out, int *rc)
{
    const char *s = lx->src;
    unsigned int n = lx->len;
    *rc = RLL_OK;
    for (;;) {
        unsigned char c;
        if (lx->pos >= n) {
            rl_emit(out, RLT_EOF, n, 0);
            return;
        }
        c = (unsigned char)s[lx->pos];
        if (c > 0x7Fu) {
            rl_emit(out, RLT_EOF, lx->pos, 0);
            *rc = RLL_BADCHAR;
            return;
        }
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            lx->pos++;
            continue;
        }
        if (c == '/' && lx->pos + 1 < n && s[lx->pos + 1] == '/') {
            lx->pos += 2;
            while (lx->pos < n && s[lx->pos] != '\n') {
                if ((unsigned char)s[lx->pos] > 0x7Fu) {
                    rl_emit(out, RLT_EOF, lx->pos, 0);
                    *rc = RLL_BADCHAR;
                    return;
                }
                lx->pos++;
            }
            continue;
        }
        break;
    }
    {
        unsigned char c = (unsigned char)s[lx->pos];
        unsigned int off = lx->pos;
        /* Identifiers / keywords. */
        if (rl_is_alpha(c)) {
            lx->pos++;
            while (lx->pos < n && rl_is_alnum((unsigned char)s[lx->pos]))
                lx->pos++;
            rl_emit(out, rl_keyword(s + off, lx->pos - off), off,
                    lx->pos - off);
            return;
        }
        /* Decimal integers. */
        if (rl_is_digit(c)) {
            lx->pos++;
            while (lx->pos < n && rl_is_digit((unsigned char)s[lx->pos]))
                lx->pos++;
            if (rl_int_overflow(s + off, lx->pos - off)) {
                rl_emit(out, RLT_EOF, off, 0);
                *rc = RLL_INTOFLOW;
                return;
            }
            rl_emit(out, RLT_INT, off, lx->pos - off);
            return;
        }
        /* Strings. */
        if (c == '"') {
            unsigned int w;
            lx->pos++;
            w = ss->used;
            for (;;) {
                unsigned char d;
                if (lx->pos >= n) {
                    rl_emit(out, RLT_EOF, off, 0);
                    *rc = RLL_UNTERM;
                    return;
                }
                d = (unsigned char)s[lx->pos];
                if (d == '"') {
                    lx->pos++;
                    break;
                }
                if (d > 0x7Fu) {
                    rl_emit(out, RLT_EOF, lx->pos, 0);
                    *rc = RLL_BADCHAR;
                    return;
                }
                if (d == '\r' || d == '\n') {
                    rl_emit(out, RLT_EOF, off, 0);
                    *rc = RLL_UNTERM;
                    return;
                }
                if (d == '\\') {
                    unsigned char e;
                    char v;
                    lx->pos++;
                    if (lx->pos >= n) {
                        rl_emit(out, RLT_EOF, off, 0);
                        *rc = RLL_UNTERM;
                        return;
                    }
                    e = (unsigned char)s[lx->pos];
                    if (e == '\\') v = '\\';
                    else if (e == '"') v = '"';
                    else if (e == 'n') v = '\n';
                    else if (e == 't') v = '\t';
                    else {
                        rl_emit(out, RLT_EOF, lx->pos - 1, 0);
                        *rc = RLL_BADESC;
                        return;
                    }
                    lx->pos++;
                    if (w < ss->cap)
                        ss->base[w] = v;
                    ++w;
                    if (w > ss->cap) {
                        rl_emit(out, RLT_EOF, off, 0);
                        *rc = RLL_UNTERM;
                        return;
                    }
                    continue;
                }
                lx->pos++;
                if (w < ss->cap)
                    ss->base[w] = (char)d;
                ++w;
                if (w > ss->cap) {
                    rl_emit(out, RLT_EOF, off, 0);
                    *rc = RLL_UNTERM;
                    return;
                }
            }
            rl_emit(out, RLT_STR, off, lx->pos - off);
            out->val = ss->base + ss->used;
            out->vlen = (unsigned short)(w - ss->used);
            ss->used = w;
            return;
        }
        /* Two-byte operators first (maximal munch). */
        if (lx->pos + 1 < n) {
            unsigned char d = (unsigned char)s[lx->pos + 1];
            unsigned char k = 0;
            if (c == '=' && d == '=') k = RLT_EQEQ;
            else if (c == '!' && d == '=') k = RLT_BANGEQ;
            else if (c == '<' && d == '=') k = RLT_LESSEQ;
            else if (c == '>' && d == '=') k = RLT_GREATEREQ;
            else if (c == '&' && d == '&') k = RLT_ANDAND;
            else if (c == '|' && d == '|') k = RLT_OROR;
            else if (c == '-' && d == '>') k = RLT_ARROW;
            else if (c == '|' && d == '>') k = RLT_PIPEGT;
            if (k) {
                lx->pos += 2;
                rl_emit(out, k, off, 2);
                return;
            }
        }
        /* Single-byte operators. */
        {
            unsigned char k = 0;
            if (c == '+') k = RLT_PLUS;
            else if (c == '-') k = RLT_MINUS;
            else if (c == '*') k = RLT_STAR;
            else if (c == '/') k = RLT_SLASH;
            else if (c == '%') k = RLT_PERCENT;
            else if (c == '!') k = RLT_BANG;
            else if (c == '=') k = RLT_EQUAL;
            else if (c == '<') k = RLT_LESS;
            else if (c == '>') k = RLT_GREATER;
            else if (c == '(') k = RLT_LPAREN;
            else if (c == ')') k = RLT_RPAREN;
            else if (c == '{') k = RLT_LBRACE;
            else if (c == '}') k = RLT_RBRACE;
            else if (c == ';') k = RLT_SEMI;
            else if (c == ',') k = RLT_COMMA;
            else if (c == ':') k = RLT_COLON;
            if (k) {
                lx->pos++;
                rl_emit(out, k, off, 1);
                return;
            }
        }
        rl_emit(out, RLT_EOF, off, 0);
        *rc = RLL_BADCHAR;
        return;
    }
}
