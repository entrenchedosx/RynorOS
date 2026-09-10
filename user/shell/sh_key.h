/* Stage 18d Slice E CPL3 Set-1 decoder (pure: no syscalls, no I/O).
 *
 * The kernel delivers raw scan bytes (E0/E1 prefixes included; 0x00 is
 * the reserved LOST epoch marker, never a physical byte). This decoder
 * turns them into ASCII events for the shell line editor. Shared
 * verbatim by the shell and the dcode self-test program.
 *
 * Policy: full US layout for typeable punctuation (the PARSER rejects
 * non-grammar characters loudly; the decoder never silently drops
 * text); no-text keys (F-keys, arrows, Tab, Esc, Alt, Caps) ignored;
 * Ctrl tracked left+right; E1 Pause consumed silently (never Ctrl);
 * LOST resets all prefix/modifier state.
 */
#ifndef RYNOR_SH_KEY_H
#define RYNOR_SH_KEY_H

/* Decoder events. */
#define SHK_CHAR 1   /* printable ASCII in chr */
#define SHK_ENTER 2  /* Enter make */
#define SHK_BSPACE 3 /* Backspace make */
#define SHK_CTRL_C 4 /* Ctrl+C chord (make of c/C while Ctrl held) */
#define SHK_NONE 0   /* no event (break, ignored key, prefix byte) */

struct shk_event {
    unsigned int kind;
    char chr;
};

struct shk_state {
    unsigned int shift;
    unsigned int ctrl;
    unsigned int e0;
    unsigned int e1; /* 0 idle, else 1..5 progress through tail */
};

/* Feed one raw scan byte (or 0x00 LOST marker). Returns 1 with *ev set
 * (possibly SHK_NONE), 0 only on a null state/event pointer. */
int shk_feed(struct shk_state *st, unsigned char scan, struct shk_event *ev);

#endif
