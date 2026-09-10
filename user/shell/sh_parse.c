/* Stage 18d Slice E shell parser. See sh_parse.h for the grammar.
 *
 * Structure: shp_parse_stmt parses ONE semicolon-delimited statement
 * and reports the consumed length; the caller loops statements until
 * the input is exhausted (this unifies `;` sequencing and newlines:
 * the caller treats `\n`/`\r` as whitespace, so script buffers and
 * interactive lines feed the identical path).
 *
 * Tokenizer discipline: bare words are an explicit allowlist
 * ([A-Za-z0-9_./,:+=@%-]); every other byte outside double quotes is
 * structure (`|>`, `;`, `//`), whitespace, or a loud syntax error.
 * `//` starts a comment only at a token boundary (mid-word slashes
 * are literal, so absolute paths and `a//b` stay intact). Adjacent
 * quoted/bare words do not concatenate (each is its own argument).
 */
#include "sh_parse.h"

static int is_space(char c)
{
    return c == ' ' || c == '\t';
}

static int is_stmt_end(char c)
{
    return c == ';' || c == '\n' || c == '\r';
}

static int is_bare(char c)
{
    if (c >= 'a' && c <= 'z') return 1;
    if (c >= 'A' && c <= 'Z') return 1;
    if (c >= '0' && c <= '9') return 1;
    if (c == '_' || c == '.' || c == '/' || c == ',' || c == ':' ||
        c == '+' || c == '=' || c == '@' || c == '%' || c == '-')
        return 1;
    return 0;
}

static int streq(const char *a, const char *b)
{
    while (*a && *b && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

/* Copy one built command into the line (field-wise: freestanding
   builds provide no memcpy, and struct assignment would emit one).
   Argument pointers are rebased from the source store to the
   destination store by offset. */
static void commit_cmd(struct shp_cmd *dst, const struct shp_cmd *src)
{
    unsigned int i;
    dst->argc = src->argc;
    dst->store_used = src->store_used;
    for (i = 0; i < src->store_used; ++i)
        dst->store[i] = src->store[i];
    for (i = 0; i < SHP_MAX_ARGS; ++i)
        dst->argv[i] = (i < src->argc) ?
            dst->store + (src->argv[i] - (const char *)src->store) : 0;
}

/* Append one argument word to cmd (bounded). Empty words (from "")
   are legal argv entries (the spawn ABI carries zero-length strings);
   only the aggregate/argc caps bind. */
static int push_word(struct shp_cmd *cmd, const char *s, unsigned int n)
{
    unsigned int k;
    if (cmd->argc >= SHP_MAX_ARGS) return 0;
    if (n > SHP_MAX_WORD) return 0;
    /* +1 NUL must fit the aggregate budget. */
    if (cmd->store_used > (unsigned int)SHP_MAX_ARGBYTES - (n + 1)) return 0;
    cmd->argv[cmd->argc] = cmd->store + cmd->store_used;
    for (k = 0; k < n; ++k)
        cmd->store[cmd->store_used + k] = s[k];
    cmd->store[cmd->store_used + n] = 0;
    cmd->store_used += n + 1;
    cmd->argc += 1;
    return 1;
}

int shp_parse_stmt(const char *text, unsigned long long len,
                   struct shp_line *out, unsigned long long *used)
{
    struct shp_cmd cur;
    char word[SHP_MAX_WORD + 1];
    unsigned int wlen = 0;
    unsigned int ncmds = 0;
    unsigned int c;
    int have_cmd = 0;
    unsigned long long i = 0;
    if (!text || !out || !used) return SHP_ERR_SYNTAX;
    if (len > SHP_MAX_LINE) return SHP_ERR_SYNTAX;
    out->empty = 0;
    out->status_only = 0;
    out->ncmds = 0;
    for (c = 0; c < SHP_MAX_CMDS; ++c) {
        out->cmds[c].argc = 0;
        out->cmds[c].store_used = 0;
    }
    cur.argc = 0;
    cur.store_used = 0;
    *used = 0;
    for (;;) {
        char ch;
        /* Skip blanks between tokens. */
        while (i < len && is_space(text[i])) ++i;
        /* End of input or a statement separator ends the statement
           (consuming one separator char so the caller always makes
           progress). */
        if (i >= len) {
            *used = i;
            break;
        }
        if (is_stmt_end(text[i])) {
            *used = i + 1;
            break;
        }
        /* Line comment: '//' at a token boundary runs to the next
           newline (or end); a ';' inside a comment stays commented. */
        if (text[i] == '/' && i + 1 < len && text[i + 1] == '/') {
            while (i < len && text[i] != '\n') ++i;
            continue;
        }
        /* Pipeline separator: exactly "|>". */
        if (text[i] == '|') {
            if (!have_cmd) return SHP_ERR_SYNTAX;
            if (i + 1 >= len || text[i + 1] != '>') return SHP_ERR_SYNTAX;
            if (ncmds + 1 >= SHP_MAX_CMDS) return SHP_ERR_SYNTAX;
            commit_cmd(&out->cmds[ncmds], &cur);
            ++ncmds;
            cur.argc = 0;
            cur.store_used = 0;
            have_cmd = 0;
            i += 2;
            continue;
        }
        ch = text[i];
        /* Quoted word. */
        if (ch == '"') {
            ++i;
            wlen = 0;
            for (;;) {
                char q;
                if (i >= len) return SHP_ERR_SYNTAX;
                q = text[i];
                if (q == '"') {
                    ++i;
                    break;
                }
                if (q == '\\') {
                    ++i;
                    if (i >= len) return SHP_ERR_SYNTAX;
                    if (text[i] != '"' && text[i] != '\\')
                        return SHP_ERR_SYNTAX;
                    if (wlen >= SHP_MAX_WORD) return SHP_ERR_SYNTAX;
                    word[wlen++] = text[i++];
                    continue;
                }
                if (wlen >= SHP_MAX_WORD) return SHP_ERR_SYNTAX;
                word[wlen++] = text[i++];
            }
            if (!push_word(&cur, word, wlen)) return SHP_ERR_SYNTAX;
            have_cmd = 1;
            continue;
        }
        /* Bare word (allowlist) or loud rejection. */
        if (is_bare(ch)) {
            wlen = 0;
            while (i < len && is_bare(text[i])) {
                if (wlen >= SHP_MAX_WORD) return SHP_ERR_SYNTAX;
                word[wlen++] = text[i++];
            }
            if (!push_word(&cur, word, wlen)) return SHP_ERR_SYNTAX;
            have_cmd = 1;
            continue;
        }
        /* Lone '/' (not '//') is rejected (paths spell with more). */
        return SHP_ERR_SYNTAX;
    }
    if (!have_cmd && ncmds == 0) {
        out->empty = 1;
        return SHP_EMPTY;
    }
    if (!have_cmd) return SHP_ERR_SYNTAX;
    commit_cmd(&out->cmds[ncmds], &cur);
    ++ncmds;
    out->ncmds = ncmds;
    /* `status` only as a sole single command, never in a pipeline. */
    if (ncmds > 1) {
        for (c = 0; c < out->ncmds; ++c) {
            if (out->cmds[c].argc == 1 && streq(out->cmds[c].argv[0], "status"))
                return SHP_ERR_SYNTAX;
        }
    } else if (out->cmds[0].argc == 1 && streq(out->cmds[0].argv[0], "status")) {
        out->status_only = 1;
    }
    return SHP_OK;
}
