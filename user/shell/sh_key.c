/* Stage 18d Slice E CPL3 Set-1 decoder. See sh_key.h. */
#include "sh_key.h"

/* Pause tail after a leading E1: E1 1D 45 E1 9D C5 (kernel consumes
   the same sequence; the 0x1D inside must never read as Ctrl). */
static const unsigned char pause_tail[5] = {0x1d, 0x45, 0xe1, 0x9d, 0xc5};

/* Row: make scan -> ASCII without shift, with shift. 0 means no text
   (function/arrows/Tab/Esc/Alt/Caps and friends are ignored). Full US
   layout so the parser (not the decoder) decides grammar membership. */
struct keyrow {
    unsigned char scan;
    char plain;
    char shifted;
};

static const struct keyrow keymap[] = {
    {0x02, '1', '!'}, {0x03, '2', '@'}, {0x04, '3', '#'}, {0x05, '4', '$'},
    {0x06, '5', '%'}, {0x07, '6', '^'}, {0x08, '7', '&'}, {0x09, '8', '*'},
    {0x0a, '9', '('}, {0x0b, '0', ')'}, {0x0c, '-', '_'}, {0x0d, '=', '+'},
    {0x10, 'q', 'Q'}, {0x11, 'w', 'W'}, {0x12, 'e', 'E'}, {0x13, 'r', 'R'},
    {0x14, 't', 'T'}, {0x15, 'y', 'Y'}, {0x16, 'u', 'U'}, {0x17, 'i', 'I'},
    {0x18, 'o', 'O'}, {0x19, 'p', 'P'}, {0x1a, '[', '{'}, {0x1b, ']', '}'},
    {0x1e, 'a', 'A'}, {0x1f, 's', 'S'}, {0x20, 'd', 'D'}, {0x21, 'f', 'F'},
    {0x22, 'g', 'G'}, {0x23, 'h', 'H'}, {0x24, 'j', 'J'}, {0x25, 'k', 'K'},
    {0x26, 'l', 'L'}, {0x27, ';', ':'}, {0x28, '\'', '"'}, {0x29, '`', '~'},
    {0x2b, '\\', '|'}, {0x2c, 'z', 'Z'}, {0x2d, 'x', 'X'}, {0x2e, 'c', 'C'},
    {0x2f, 'v', 'V'}, {0x30, 'b', 'B'}, {0x31, 'n', 'N'}, {0x32, 'm', 'M'},
    {0x33, ',', '<'}, {0x34, '.', '>'}, {0x35, '/', '?'}, {0x39, ' ', ' '},
};

int shk_feed(struct shk_state *st, unsigned char scan, struct shk_event *ev)
{
    unsigned int i;
    if (!st || !ev) return 0;
    ev->kind = SHK_NONE;
    ev->chr = 0;
    /* LOST epoch: discard unsafe prefix state and clear held
       modifiers (especially Ctrl, which must never stick). */
    if (scan == 0x00) {
        st->shift = 0;
        st->ctrl = 0;
        st->e0 = 0;
        st->e1 = 0;
        return 1;
    }
    /* E1 Pause machine: consume the exact tail silently. A mismatch
       resets (the byte is dropped, never reinterpreted as a key, and
       must not set Ctrl even when it is 0x1D). */
    if (st->e1) {
        unsigned int n = st->e1 - 1;
        if (n < 5 && scan == pause_tail[n] && n + 1 < 5)
            st->e1 = st->e1 + 1;
        else
            st->e1 = 0;
        st->e0 = 0;
        return 1;
    }
    if (scan == 0xe1) {
        st->e0 = 0;
        st->e1 = 1;
        return 1;
    }
    if (scan == 0xe0) {
        st->e0 = 1;
        return 1;
    }
    if (st->e0) {
        st->e0 = 0;
        /* Right Ctrl is the only E0 pair with shell meaning; every
           other extended key (arrows et al.) is ignored. */
        if (scan == 0x1d)
            st->ctrl = 1;
        else if (scan == 0x9d)
            st->ctrl = 0;
        return 1;
    }
    /* Modifier makes/breaks. */
    if (scan == 0x2a || scan == 0x36) {
        st->shift = 1;
        return 1;
    }
    if (scan == 0xaa || scan == 0xb6) {
        st->shift = 0;
        return 1;
    }
    if (scan == 0x1d) {
        st->ctrl = 1;
        return 1;
    }
    if (scan == 0x9d) {
        st->ctrl = 0;
        return 1;
    }
    /* Breaks of text keys carry no event. */
    if (scan & 0x80)
        return 1;
    /* Enter / Backspace makes. */
    if (scan == 0x1c) {
        ev->kind = SHK_ENTER;
        return 1;
    }
    if (scan == 0x0e) {
        ev->kind = SHK_BSPACE;
        return 1;
    }
    for (i = 0; i < sizeof(keymap) / sizeof(keymap[0]); ++i) {
        if (keymap[i].scan != scan)
            continue;
        {
            char ch = st->shift ? keymap[i].shifted : keymap[i].plain;
            /* Ctrl+C chord (either case while Ctrl is held). Other
               Ctrl chords are ignored (no output, no abort). */
            if (st->ctrl && (ch == 'c' || ch == 'C')) {
                ev->kind = SHK_CTRL_C;
                return 1;
            }
            if (st->ctrl)
                return 1;
            ev->kind = SHK_CHAR;
            ev->chr = ch;
            return 1;
        }
    }
    return 1;
}
