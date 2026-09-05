#ifndef RYNOR_SHELL_INTERNAL_H
#define RYNOR_SHELL_INTERNAL_H
#include "shell.h"
/* Line-editor boundary shared with the self-test. The buffer holds at most
   SHELL_LINE_MAX bytes plus the NUL: data[len] is always addressable, so
   insertion must stop at len == SHELL_LINE_MAX (never at len > MAX). */
struct shell_line {
    char data[SHELL_LINE_MAX + 1];
    cpu_u64 len;
};
/* Bounded insertion (1 accepted, 0 rejected at capacity) and backspace.
   Test-visible so the synthetic self-test pins the exact 64/65 boundary;
   production callers are unchanged. */
int line_insert(struct shell_line *line, char c);
void line_backspace(struct shell_line *line);
#endif
