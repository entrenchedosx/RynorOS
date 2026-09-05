/* Stage 11 shell/monitor: a ring-0 kernel monitor that accepts real keyboard
   input and exposes only the services that actually exist (the Stage 10
   runtime services DIGEST/UPPER/COUNT_DIGITS) plus the honest built-ins
   help/version/echo/clear. Input is real: Set-1 scan codes are translated to
   ASCII through a bounded table, echoed, accumulated into a bounded command
   line, and dispatched on Enter. There is no canned output and no simulated
   program execution; unsupported or malformed commands are rejected. */

#include "shell.h"
#include "shell-internal.h"
#include "serial.h"
#include "cpu.h"
#include "kbd.h"
#include "krst.h"
#include "kstring.h"
#include "irq.h"
#include "io.h"

/* ------------------------------------------------------------------ */
/* Set-1 scan code -> ASCII translation. Only the keys the shell needs: */
/* letters, digits and space produce text; backspace and enter are handled */
/* separately in the line editor. This table is deliberately separate from */
/* the Stage 8 decoder (which only reports physical key identity). The scan */
/* passed in is a make code (bit 7 clear); releases are ignored by the */
/* caller, so the table keys on the low 7 bits. */
/* ------------------------------------------------------------------ */

enum shell_char { SHELL_CHAR_NONE = 0, SHELL_CHAR_ASCII, SHELL_CHAR_BACKSPACE, SHELL_CHAR_ENTER };

struct scan_map { cpu_u8 scan; unsigned char ascii; };

static const struct scan_map SHELL_SCAN_TABLE[] = {
    {0x1e, 'a'}, {0x30, 'b'}, {0x2e, 'c'}, {0x20, 'd'}, {0x12, 'e'}, {0x21, 'f'},
    {0x22, 'g'}, {0x23, 'h'}, {0x17, 'i'}, {0x24, 'j'}, {0x25, 'k'}, {0x26, 'l'},
    {0x32, 'm'}, {0x31, 'n'}, {0x18, 'o'}, {0x19, 'p'}, {0x10, 'q'}, {0x13, 'r'},
    {0x1f, 's'}, {0x14, 't'}, {0x16, 'u'}, {0x2f, 'v'}, {0x11, 'w'}, {0x2d, 'x'},
    {0x15, 'y'}, {0x2c, 'z'},
    {0x02, '1'}, {0x03, '2'}, {0x04, '3'}, {0x05, '4'}, {0x06, '5'}, {0x07, '6'},
    {0x08, '7'}, {0x09, '8'}, {0x0a, '9'}, {0x0b, '0'},
    {0x39, ' '},
};
#define SHELL_SCAN_TABLE_LEN (sizeof SHELL_SCAN_TABLE / sizeof SHELL_SCAN_TABLE[0])

/* ------------------------------------------------------------------ */
/* Text translation and line editor                                   */
/* ------------------------------------------------------------------ */

static enum shell_char shell_translate(cpu_u8 scan, char *ascii)
{
    if (scan == 0x1c) return SHELL_CHAR_ENTER;
    if (scan == 0x0e) return SHELL_CHAR_BACKSPACE;
    for (cpu_u64 i = 0; i < SHELL_SCAN_TABLE_LEN; ++i) {
        if (SHELL_SCAN_TABLE[i].scan == scan) { *ascii = (char)SHELL_SCAN_TABLE[i].ascii; return SHELL_CHAR_ASCII; }
    }
    return SHELL_CHAR_NONE;
}

/* Bounded line-editor insertion. Returns 1 if the buffer can hold one more
   byte, 0 otherwise; on overrun the byte is rejected (never overflows). */
int line_insert(struct shell_line *line, char c)
{
    if (line->len >= SHELL_LINE_MAX) return 0;
    line->data[line->len++] = c;
    line->data[line->len] = '\0';
    return 1;
}

void line_backspace(struct shell_line *line)
{
    if (line->len == 0) return;
    line->data[--line->len] = '\0';
}

/* ------------------------------------------------------------------ */
/* Tokenizer                                                          */
/* ------------------------------------------------------------------ */

int shell_tokenize(char *line, cpu_u64 cap, char *tokens[SHELL_ARG_MAX])
{
    if (!line || !tokens || cap == 0) return SHELL_INVALID;
    if (kstr_nlen(line, cap) == cap) return SHELL_INVALID;
    cpu_u64 i = 0, count = 0;
    while (count < SHELL_ARG_MAX) {
        while (i < cap && line[i] == ' ' && line[i]) line[i++] = '\0';
        if (i >= cap || !line[i]) break;
        tokens[count++] = &line[i];
        while (i < cap && line[i] && line[i] != ' ') ++i;
    }
    /* The contract requires SHELL_TOO_MANY when the line holds more than
       SHELL_ARG_MAX tokens; extra tokens must not be silently dropped. */
    while (i < cap && line[i] == ' ') line[i++] = '\0';
    if (i < cap && line[i]) return SHELL_TOO_MANY;
    return count;
}

/* ------------------------------------------------------------------ */
/* Output helpers (serial evidence channel)                           */
/* ------------------------------------------------------------------ */

static void say(const char *s) { (void)serial_write(s); }
static void say_char(char c)
{
    const char text[2] = {c, '\0'};
    say(text);
}
static void say_line(const char *s) { say(s); say("\r\n"); }
static void say_number(cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    say(b + i);
}

cpu_u64 shell_decode_u64_le(const cpu_u8 *buf, cpu_u64 len)
{
    if (!buf || len != 8) return 0;
    cpu_u64 v = 0;
    for (unsigned int i = 0; i < 8; ++i) v |= (cpu_u64)buf[i] << (i * 8);
    return v;
}

/* ------------------------------------------------------------------ */
/* Command dispatch. Returns the service/built-in result for evidence. */
/* ------------------------------------------------------------------ */

static int cmd_upper(const char *text, cpu_u64 len)
{
    char out[48];
    cpu_u64 n = 0;
    /* The bounded service contract is 40 input bytes; longer shell arguments
       are rejected honestly instead of silently truncated. */
    if (len > 40) { say_line("error: upper accepts at most 40 characters"); return SHELL_SERVICE; }
    int r = krst_call(KRST_SVC_UPPER, text, len, out, sizeof out, &n);
    if (r == KRST_OK) {
        if (n >= sizeof out) { say_line("error: service rejected request"); return SHELL_SERVICE; }
        out[n] = '\0'; say(out);
    }
    return r;
}

static void cmd_echo(const char *text)
{
    say(text);
}

static void cmd_help(void)
{
    say_line("commands: help version echo <text> upper <text> count <text> digest <text> clear");
}

int shell_execute(char *line, cpu_u64 cap)
{
    if (!line || cap == 0) return SHELL_INVALID;
    char *tokens[SHELL_ARG_MAX];
    int argc = shell_tokenize(line, cap, tokens);
    if (argc == SHELL_TOO_MANY) { say_line("[SHELL] error: too many arguments"); return SHELL_TOO_MANY; }
    if (argc == SHELL_INVALID) { say_line("[SHELL] error: invalid command line"); return SHELL_INVALID; }
    if (argc == 0) { say_line("[SHELL] error: empty command"); return SHELL_OK; }
    const char *cmd = tokens[0];
    const char *text = "";
    cpu_u64 text_len = 0;
    if (argc >= 2) {
        text = tokens[1];
        cpu_u64 offset = (cpu_u64)(tokens[1] - line);
        if (offset >= cap) return SHELL_INVALID;
        text_len = kstr_nlen(tokens[1], cap - offset);
    }
    say("[SHELL] exec="); say(cmd);
    if (argc >= 2) { say(" arg=\""); say(text); say("\""); }
    say("\r\n");
    if (kstr_cmp(cmd, "version", 8) == 0) {
        if (argc != 1) { say_line("error: version takes no arguments"); return SHELL_ARGS; }
        say_line("RynorOS " RYNOR_VERSION); return SHELL_OK;
    } else if (kstr_cmp(cmd, "help", 5) == 0) {
        if (argc != 1) { say_line("error: help takes no arguments"); return SHELL_ARGS; }
        cmd_help(); return SHELL_OK;
    } else if (kstr_cmp(cmd, "clear", 6) == 0) {
        if (argc != 1) { say_line("error: clear takes no arguments"); return SHELL_ARGS; }
        say_line("[SHELL] clear: display redraw requested"); return SHELL_OK;
    } else if (kstr_cmp(cmd, "echo", 5) == 0) {
        if (argc != 2) { say_line("error: echo requires one argument"); return SHELL_ARGS; }
        cmd_echo(text); say("\r\n"); return SHELL_OK;
    } else if (kstr_cmp(cmd, "upper", 6) == 0) {
        if (argc != 2) { say_line("error: upper requires one argument"); return SHELL_ARGS; }
        int r = cmd_upper(text, text_len);
        if (r == KRST_OK) say("\r\n");
        return r == KRST_OK ? SHELL_OK : SHELL_SERVICE;
    } else if (kstr_cmp(cmd, "count", 6) == 0) {
        if (argc != 2) { say_line("error: count requires one argument"); return SHELL_ARGS; }
        cpu_u8 n[8]; cpu_u64 nlen = 0;
        int r = krst_call(KRST_SVC_COUNT_DIGITS, text, text_len, n, sizeof n, &nlen);
        if (r == KRST_OK) {
            cpu_u64 v = shell_decode_u64_le(n, nlen);
            if (nlen == 8) { say_number(v); say("\r\n"); return SHELL_OK; }
        }
        say_line("error: service rejected request"); return SHELL_SERVICE;
    } else if (kstr_cmp(cmd, "digest", 7) == 0) {
        if (argc != 2) { say_line("error: digest requires one argument"); return SHELL_ARGS; }
        cpu_u8 d[8]; cpu_u64 nlen = 0;
        int r = krst_call(KRST_SVC_DIGEST, text, text_len, d, sizeof d, &nlen);
        if (r == KRST_OK && nlen == 8) {
            const char hex[17] = "0123456789ABCDEF";
            say("0x");
            for (unsigned int i = 0; i < 8; ++i) {
                char b[3];
                b[0] = hex[(d[i] >> 4) & 0xF];
                b[1] = hex[d[i] & 0xF];
                b[2] = '\0';
                say(b);
            }
            say("\r\n"); return SHELL_OK;
        } else { say_line("error: service rejected request"); return SHELL_SERVICE; }
    }
    say_line("error: unknown command");
    return SHELL_UNKNOWN;
}

/* ------------------------------------------------------------------ */
/* Reading help: consume key events until a PRESS arrives. Releases are */
/* drained within the same wait so one host sendkey (make+break) maps to */
/* exactly one wait marker. Returns the press event. */
/* ------------------------------------------------------------------ */

struct shell_prefix_state { unsigned int extended, pause; };

/* Keep the shell's raw Set-1 stream handling identical to the keyboard
   decoder: malformed Pause sequences consume only the mismatching byte and
   reset immediately.  Blindly swallowing five bytes after E1 can discard a
   later ordinary key when hardware emits a truncated/malformed sequence. */
static int shell_prefix_byte(struct shell_prefix_state *state, cpu_u8 scan)
{
    static const cpu_u8 pause_tail[] = {0x1d, 0x45, 0xe1, 0x9d, 0xc5};
    _Static_assert(sizeof pause_tail == 5, "Pause tail is the six-byte E1 sequence");
    if (state->pause) {
        unsigned int n = state->pause - 1;
        state->pause = n < sizeof pause_tail && scan == pause_tail[n] &&
                       n + 1 < sizeof pause_tail ? state->pause + 1 : 0;
        return 1;
    }
    if (scan == 0xe1) { state->extended = 0; state->pause = 1; return 1; }
    if (scan == 0xe0) { state->extended = 1; return 1; }
    if (state->extended) { state->extended = 0; return 1; }
    return 0;
}

int shell_prefix_self_test(void)
{
    struct shell_prefix_state state = {0};
    const cpu_u8 malformed[] = {0xe1, 0x00, 0x1e, 0x9e};
    const int malformed_discard[] = {1, 1, 0, 0};
    _Static_assert(sizeof malformed / sizeof malformed[0] ==
                   sizeof malformed_discard / sizeof malformed_discard[0],
                   "malformed probe and expected decisions must stay paired");
    for (unsigned int i = 0; i < sizeof malformed; ++i)
        if (shell_prefix_byte(&state, malformed[i]) != malformed_discard[i]) return 0;
    const cpu_u8 pause[] = {0xe1, 0x1d, 0x45, 0xe1, 0x9d, 0xc5};
    for (unsigned int i = 0; i < sizeof pause; ++i)
        if (!shell_prefix_byte(&state, pause[i])) return 0;
    if (state.extended || state.pause) return 0;
    return shell_prefix_byte(&state, 0xe0) && shell_prefix_byte(&state, 0x1c) &&
           !state.extended && !state.pause && !shell_prefix_byte(&state, 0x1e);
}

static void wait_key(struct kbd_event *press)
{
    struct kbd_event e;
    cpu_u8 make = 0;
    struct shell_prefix_state prefix = {0};
    for (;;) {
        enum kbd_result r;
        while ((r = kbd_poll(&e)) == KBD_EMPTY)
            __asm__ volatile ("sti; hlt; cli" ::: "memory");
        if (r != KBD_EVENT) { say_line("[SHELL] failure=input_loss"); cpu_halt(); }
        /* Preserve Stage 8's prefix-isolation contract while interpreting the
           wider ordinary-key set locally. Never turn E0 keypad Enter into the
           ordinary Enter command delimiter; consume bounded E1 Pause tails. */
        if (shell_prefix_byte(&prefix, e.scan)) continue;
        /* The shell translates raw Set-1 scans itself, so it must not depend on
           the Stage 8 decoder's 8-key subset: an unknown-key make arrives as
           KBD_EVENT_UNKNOWN and would otherwise stall the session forever.
           Drain releases by the Set-1 break bit so one host sendkey (make+
           break) maps to exactly one wait marker. */
        if (e.scan & 0x80) {
            /* One readiness marker owns one complete make/break packet.  Do
               not let the final release remain behind after IRQ1 is masked. */
            if (make && (e.scan & 0x7f) == make) return;
            continue;
        }
        if (!make) {
            *press = e;
            make = e.scan & 0x7f;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Interactive session: consumes exactly budget key presses. Command    */
/* lines are built across key presses and dispatched on Enter.         */
/* ------------------------------------------------------------------ */

static void interactive_session(cpu_u64 key_budget)
{
    say_line("[SHELL] interactive session started");
    if (!irq_set_enabled(1, 1)) { say_line("[SHELL] failure=irq_enable"); cpu_halt(); }
    struct shell_line line = { {0}, 0 };
    for (cpu_u64 n = 0; n < key_budget; ++n) {
        /* The marker lets the host advance its key stream; mirrors the */
        /* Stage 8 wait protocol so the injection harness is uniform.   */
        say("[SHELL] waiting for input="); say_number(n); say("\r\n");
        (void)serial_flush();
        struct kbd_event e;
        wait_key(&e);
        char ascii = 0;
        enum shell_char kind = shell_translate(e.scan & 0x7f, &ascii);
        say("[SHELL] key="); say_number(n); say(" scan=0x");
        { const char lh[17] = "0123456789abcdef"; cpu_u8 sc = e.scan & 0x7f;
          char hs[3]; hs[0] = lh[(sc >> 4) & 0xF]; hs[1] = lh[sc & 0xF]; hs[2] = '\0'; say(hs); }
        say(" ascii='");
        if (kind == SHELL_CHAR_ASCII && ascii >= 32 && ascii < 127) say_char(ascii);
        else say("?");
        say("' line=\""); say(line.data); say("\"\r\n");
        if (kind == SHELL_CHAR_ENTER) {
            say("[SHELL] line=\""); say(line.data); say("\"\r\n");
            (void)shell_execute(line.data, line.len + 1);
            line.len = 0; line.data[0] = '\0';
        } else if (kind == SHELL_CHAR_BACKSPACE) {
            line_backspace(&line);
        } else if (kind == SHELL_CHAR_ASCII) {
            (void)line_insert(&line, ascii);
        }
    }
    if (!irq_set_enabled(1, 0)) { say_line("[SHELL] failure=irq_disable"); cpu_halt(); }
    say_line("[SHELL] interactive session complete");
}

void shell_run(cpu_u64 key_budget)
{
    if (!cpu_interrupts_disabled()) { say_line("[SHELL] failure=if0"); cpu_halt(); }
    interactive_session(key_budget);
}

/* shell_self_test is defined in shell-test.c to keep the interactive run
   separate from the synthetic parser/dispatch tests. */
