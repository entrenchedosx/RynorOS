/* Stage 18d Slice E shell grammar (CPL3, pure: no syscalls).
 *
 * script   ::= line { ";" line } ;
 * line     ::= [ pipeline ] [ "//" comment ] ;
 * pipeline ::= command { "|>" command } ;   (1 or 2 stages only)
 * command  ::= word { word } | "status" ;   ("status" only as a sole
 *                                            single command, never in
 *                                            a pipeline)
 * word     ::= bare | '"' { escape | nonquote } '"' ;
 * escape   ::= '\"' | '\\' ;
 *
 * Bounded, iterative, single pass. The same entry parses interactive
 * lines and script lines (parity by construction); only the caller
 * differs (echo/prompt vs script markers).
 */
#ifndef RYNOR_SH_PARSE_H
#define RYNOR_SH_PARSE_H

#define SHP_MAX_CMDS 2
#define SHP_MAX_ARGS 8
#define SHP_MAX_ARGBYTES 256
#define SHP_MAX_WORD 64
#define SHP_MAX_LINE 256

/* Parse outcome codes (shell-local; mapped to shell status by sh.c). */
#define SHP_OK 0
#define SHP_EMPTY 1      /* blank/comment-only line: silent no-op */
#define SHP_ERR_SYNTAX 2 /* deterministic rejection, 0 spawns */

/* One parsed command: argc words in argv storage. words point into
 * store (NUL-terminated each); store_used counts bytes incl. NULs. */
struct shp_cmd {
    unsigned int argc;
    const char *argv[SHP_MAX_ARGS];
    char store[SHP_MAX_ARGBYTES];
    unsigned int store_used;
};

/* One parsed line: ncmds (1..2) commands, or empty. */
struct shp_line {
    int empty;
    int status_only; /* sole `status` builtin invocation */
    unsigned int ncmds;
    struct shp_cmd cmds[SHP_MAX_CMDS];
};

/* Parse one semicolon/newline-delimited statement starting at text
 * (len bytes available, statement itself <= SHP_MAX_LINE). Returns
 * SHP_OK (filled), SHP_EMPTY (blank/comment/separator-only), or
 * SHP_ERR_SYNTAX. On success/empty, *used advances past the statement
 * and one trailing separator (always > 0 unless len == 0). On syntax
 * error *used may be 0 (first-byte rejection consumes nothing), so the
 * caller must guarantee progress itself (consume the failed
 * statement) — a submitted line or script never grows, and waiting
 * for more input would hang silently. Never spawns; fully validates
 * before the caller executes anything. */
int shp_parse_stmt(const char *text, unsigned long long len,
                   struct shp_line *out, unsigned long long *used);

#endif
