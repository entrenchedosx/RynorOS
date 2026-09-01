#ifndef RYNOR_SHELL_H
#define RYNOR_SHELL_H
#include "cpu.h"

/* Ring-0 kernel monitor (Stage 11). Runs in the bootstrap thread after all
   self-tests. Reads real keyboard input (Set-1 scan codes -> ASCII via a
   bounded table), accumulates bounded command lines with echo/backspace,
   parses them and dispatches to the implemented runtime services
   (KRST_SVC_UPPER / KRST_SVC_COUNT_DIGITS / KRST_SVC_DIGEST) plus the shell
   built-ins help/version/echo/clear. Unsupported or malformed commands are
   rejected honestly; nothing here simulates a userspace ABI or program
   execution. This is kernel-mode, ring 0, single CPU, IF=0 at entry. */

#define SHELL_LINE_MAX 64u      /* max command-line bytes, excluding NUL */
#define SHELL_ARG_MAX  12u      /* max number of white-space-separated tokens */
#define SHELL_CMD_MAX  16u      /* max command name length */

enum shell_result {
    SHELL_OK = 0,
    SHELL_INVALID,     /* null pointer or impossible bounds */
    SHELL_TOO_LONG,    /* a command line exceeded SHELL_LINE_MAX */
    SHELL_TOO_MANY,    /* too many tokens on a line */
    SHELL_UNKNOWN,     /* unknown command name */
    SHELL_ARGS,        /* wrong number of arguments for a command */
    SHELL_SERVICE,     /* a runtime service returned a non-OK result */
};

/* Separates a command line into up to SHELL_ARG_MAX tokens on whitespace.
   Each token is a NUL-terminated substring of line. Returns token count, or
   SHELL_TOO_MANY (negative) when the line has more than SHELL_ARG_MAX. line
   is modified in place (spaces become NULs). */
int shell_tokenize(char *line, cpu_u64 cap, char *tokens[SHELL_ARG_MAX]);

/* Parses and dispatches one NUL-terminated command line (exactly like the
   live session does on Enter). cap is the capacity of cmd_line including its
   final NUL. Returns a shell_result code (JSON-ish OK/UNKNOWN/etc.); all
   human-readable evidence is written to the serial channel for the host
   validator. Used by the synthetic self-test and by the live session. */
int shell_execute(char *cmd_line, cpu_u64 cap);

/* Public shell entry: runs the interactive monitor loop. It does not return
   until the requested number of command lines has been serviced (bounded
   budget for the host harness) so the shell can be driven deterministically;
   lines==0 means run forever. Requires IF=0 and an initialized keyboard. */
void shell_run(cpu_u64 lines);

/* Stage 11 self-test: synthetic tokenizer/parser/error tests, then a real
   interactive session driven from the host through kbd_poll, verifying that
   each command's serial evidence is produced and resources stay balanced. */
void shell_self_test(void);

#endif /* RYNOR_SHELL_H */
