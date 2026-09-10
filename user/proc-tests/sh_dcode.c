/* Slice E helper: CPL3 decoder self-test. Feeds synthetic raw scan
 * vectors (shared sh_key.c) and prints PASS rows; exit 0 iff every
 * vector matches. Covers Pause, LOST, Ctrl, shifts, breaks, and the
 * full typeable alphabet. 200+ = violation class. */
#include "rt.h"
#include "sh_key.h"

static int fails;

static void check_vec(const unsigned char *bytes, unsigned long long n,
                      int want_kind, char want_chr, int id)
{
    struct shk_state st;
    struct shk_event ev;
    unsigned long long k;
    unsigned int i;
    st.shift = 0;
    st.ctrl = 0;
    st.e0 = 0;
    st.e1 = 0;
    ev.kind = 99;
    ev.chr = 0;
    for (k = 0; k < n; ++k) {
        if (!shk_feed(&st, bytes[k], &ev)) {
            fails = id;
            return;
        }
    }
    if ((int)ev.kind != want_kind || ev.chr != want_chr)
        fails = id;
    (void)i;
}

static void check_alpha(void)
{
    /* Every row of the US map both cases (make codes only). */
    static const struct {
        unsigned char scan;
        char plain;
        char shifted;
    } rows[] = {
        {0x02, '1', '!'}, {0x03, '2', '@'}, {0x04, '3', '#'},
        {0x05, '4', '$'}, {0x06, '5', '%'}, {0x07, '6', '^'},
        {0x08, '7', '&'}, {0x09, '8', '*'}, {0x0a, '9', '('},
        {0x0b, '0', ')'}, {0x0c, '-', '_'}, {0x0d, '=', '+'},
        {0x10, 'q', 'Q'}, {0x11, 'w', 'W'}, {0x12, 'e', 'E'},
        {0x13, 'r', 'R'}, {0x14, 't', 'T'}, {0x15, 'y', 'Y'},
        {0x16, 'u', 'U'}, {0x17, 'i', 'I'}, {0x18, 'o', 'O'},
        {0x19, 'p', 'P'}, {0x1a, '[', '{'}, {0x1b, ']', '}'},
        {0x1e, 'a', 'A'}, {0x1f, 's', 'S'}, {0x20, 'd', 'D'},
        {0x21, 'f', 'F'}, {0x22, 'g', 'G'}, {0x23, 'h', 'H'},
        {0x24, 'j', 'J'}, {0x25, 'k', 'K'}, {0x26, 'l', 'L'},
        {0x27, ';', ':'}, {0x28, '\'', '"'}, {0x29, '`', '~'},
        {0x2b, '\\', '|'}, {0x2c, 'z', 'Z'}, {0x2d, 'x', 'X'},
        {0x2e, 'c', 'C'}, {0x2f, 'v', 'V'}, {0x30, 'b', 'B'},
        {0x31, 'n', 'N'}, {0x32, 'm', 'M'}, {0x33, ',', '<'},
        {0x34, '.', '>'}, {0x35, '/', '?'}, {0x39, ' ', ' '},
    };
    unsigned int r;
    for (r = 0; r < sizeof(rows) / sizeof(rows[0]); ++r) {
        unsigned char b1[1] = {rows[r].scan};
        unsigned char b2[2] = {0x2a, rows[r].scan};
        check_vec(b1, 1, 1, rows[r].plain, 300 + (int)r);
        if (fails) return;
        check_vec(b2, 2, 1, rows[r].shifted, 400 + (int)r);
        if (fails) return;
    }
}

int rt_main(void)
{
    /* Enter / Backspace makes. */
    static const unsigned char enter_m[] = {0x1c};
    static const unsigned char bsp_m[] = {0x0e};
    /* Ctrl chords. */
    static const unsigned char lctrl_c[] = {0x1d, 0x2e};
    static const unsigned char rctrl_c[] = {0xe0, 0x1d, 0x2e};
    static const unsigned char ctrl_x[] = {0x1d, 0x2d};
    static const unsigned char ctrl_rel[] = {0x1d, 0x9d, 0x2e};
    /* Full Pause press+release: silent, never Ctrl. */
    static const unsigned char pause_full[] = {0xe1, 0x1d, 0x45, 0xe1,
                                               0x9d, 0xc5};
    /* Pause then real Ctrl+C afterwards still aborts. */
    static const unsigned char pause_then_ctrlc[] = {
        0xe1, 0x1d, 0x45, 0xe1, 0x9d, 0xc5, 0x1d, 0x2e};
    /* Malformed Pause tail: dropped, never Ctrl. */
    static const unsigned char pause_bad[] = {0xe1, 0x1d, 0x46};
    /* Pause leader then an ordinary key: the E1 machine is mid-tail,
       so the key byte is dropped as tail (never Ctrl, never text).
       A simplistic scan-0x1D handler reports CTRL_C here instead. */
    static const unsigned char pause_trap[] = {0xe1, 0x1d, 0x2e};
    /* LOST clears everything (shift+ctrl held, prefix pending). */
    static const unsigned char lost_mid[] = {0x2a, 0x1d, 0xe0, 0x00, 0x2e};
    /* Breaks are silent; arrows/E0 ignored. */
    static const unsigned char brk[] = {0x1e, 0x9e};
    static const unsigned char arrows[] = {0xe0, 0x4b, 0xe0, 0x4d};
    static const unsigned char fkey[] = {0x3b};
    fails = 0;
    check_vec(enter_m, 1, 2, 0, 201);
    check_vec(bsp_m, 1, 3, 0, 202);
    check_vec(lctrl_c, 2, 4, 0, 203);
    check_vec(rctrl_c, 3, 4, 0, 204);
    check_vec(ctrl_x, 2, 0, 0, 205);
    check_vec(ctrl_rel, 3, 1, 'c', 206);
    check_vec(pause_full, 6, 0, 0, 207);
    check_vec(pause_then_ctrlc, 8, 4, 0, 208);
    check_vec(pause_bad, 3, 0, 0, 209);
    check_vec(pause_trap, 3, 0, 0, 214);
    check_vec(lost_mid, 5, 1, 'c', 210);
    check_vec(brk, 2, 0, 0, 211);
    check_vec(arrows, 4, 0, 0, 212);
    check_vec(fkey, 1, 0, 0, 213);
    if (!fails) check_alpha();
    if (fails) rt_exit(200 + (fails % 50));
    if (rt_print("dcode ok\n") != RT_OK) rt_exit(220);
    rt_exit(0);
    return 0;
}
