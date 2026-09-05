/* Stage 11 shell/monitor self-test. Synthetic tokenizer/dispatch tests run in
   every build and produce deterministic serial evidence for the host
   validator. When built with RYNOR_SHELL_INTERACTIVE (only the dedicated
   integration tests set this), the self-test then drives a real interactive
   session: the host sends physical keyboard keys, the shell translates/echoes
   them, builds command lines and dispatches to the implemented runtime
   services. This proves the monitor accepts real input and exposes only real
   services. */

#include "shell.h"
#include "shell-internal.h"
#include "serial.h"
#include "cpu.h"
#include "kbd.h"
#include "krst.h"
#include "kstring.h"
#include "kbuf.h"
#include "ksched.h"
#include "pmm.h"
#include "vm.h"
#include "heap.h"
#include "irq.h"
#include "io.h"

/* The fixed number of key presses the interactive session consumes. The host
   validator injects exactly this many host-selected keys, one per
   "[SHELL] waiting for input=N" marker. Each host sendkey yields a make and a
   matching break scan byte before the next marker is emitted, so received
   scans = 2 * SHELL_SESSION_KEYS even for the final key. */
#define SHELL_SESSION_KEYS 39u

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[SHELL] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
static void text(const char *s) { require(serial_write(s), "serial"); }
static void field(const char *s, cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    text(s); text(b + i);
}
struct accounting { struct pmm_statistics pmm; struct heap_statistics heap; cpu_u64 tables; };
static struct accounting account(void)
{
    struct accounting a;
    require(pmm_statistics(&a.pmm) == PMM_OK && heap_statistics(&a.heap) == HEAP_OK, "statistics");
    a.tables = vm_kernel_space()->table_pages;
    return a;
}
static void balanced(struct accounting before)
{
    struct accounting after = account();
    require(after.pmm.allocated_bytes == before.pmm.allocated_bytes &&
            after.pmm.free_bytes == before.pmm.free_bytes && after.tables == before.tables &&
            after.heap.used_bytes == before.heap.used_bytes &&
            after.heap.free_blocks == before.heap.free_blocks && pmm_check() &&
            vm_check(vm_kernel_space()) && heap_check(), "resource_balance");
}

static void parser_tests(void)
{
    require(shell_prefix_self_test(), "prefix_recovery");
    /* Tokenizer: valid, empty, and invalid bounds. */
    char *tokens[SHELL_ARG_MAX];
    char b[64];
    require(kstr_copy(b, sizeof b, "digest ab") == KSTR_OK, "s_copy");
    int argc = shell_tokenize(b, sizeof b, tokens);
    require(argc == 2 && kstr_cmp(tokens[0], "digest", 8) == 0 &&
            kstr_cmp(tokens[1], "ab", 8) == 0, "token_ok");
    require(shell_tokenize(b, 0, tokens) == SHELL_INVALID, "token_invalid");
    char empty[4] = {0};
    require(shell_tokenize(empty, 4, tokens) == 0, "token_empty");
    char unterminated[4] = {'a', 'b', 'c', 'd'};
    require(shell_tokenize(unterminated, sizeof unterminated, tokens) == SHELL_INVALID,
            "token_unterminated");
    /* Exactly SHELL_ARG_MAX tokens fit; the 13th must be rejected, never
       silently dropped, and a full-but-exact line must still tokenize. */
    char many[SHELL_LINE_MAX + 1];
    require(kstr_copy(many, sizeof many, "a b c d e f g h i j k l") == KSTR_OK, "many_copy");
    require(shell_tokenize(many, sizeof many, tokens) == SHELL_ARG_MAX, "token_full");
    require(kstr_copy(many, sizeof many, "a b c d e f g h i j k l   ") == KSTR_OK,
            "many_trailing_copy");
    require(shell_tokenize(many, sizeof many, tokens) == SHELL_ARG_MAX &&
            kstr_cmp(tokens[SHELL_ARG_MAX - 1], "l", 2) == 0,
            "token_full_trailing_spaces");
    require(kstr_copy(many, sizeof many, "a b c d e f g h i j k l m") == KSTR_OK, "over_copy");
    require(shell_tokenize(many, sizeof many, tokens) == SHELL_TOO_MANY, "token_too_many");
    /* Trailing spaces after a full line must still surface a 13th token. */
    require(kstr_copy(many, sizeof many, "a b c d e f g h i j k l   m") == KSTR_OK,
            "over_trailing_copy");
    require(shell_tokenize(many, sizeof many, tokens) == SHELL_TOO_MANY,
            "token_full_trailing_too_many");
    /* Result codes must be negative and distinct from valid argc 0..12. */
    require(SHELL_TOO_MANY < 0 && SHELL_INVALID < 0 && SHELL_TOO_MANY != SHELL_INVALID, "result_negative");
    require(SHELL_TOO_MANY != 0 && SHELL_TOO_MANY != 1 && SHELL_TOO_MANY != 3, "result_collision");
    text("[SHELL] tokenizer, bounds and empty line verified (synthetic)\r\n");

    /* Dispatch: expose only implemented services and honest built-ins.
       Each statement's serial output is validated by the host. */
    char cmd[64];
    require(kstr_copy(cmd, sizeof cmd, "version") == KSTR_OK, "c_version");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_version");
    require(kstr_copy(cmd, sizeof cmd, "help") == KSTR_OK, "c_help");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_help");
    require(kstr_copy(cmd, sizeof cmd, "echo hi") == KSTR_OK, "c_echo");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_echo");
    require(kstr_copy(cmd, sizeof cmd, "upper abc123") == KSTR_OK, "c_upper");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_upper");
    /* 41-character upper argument exceeds the service bound: rejected, never
       silently truncated to 40. */
    require(kstr_copy(cmd, sizeof cmd, "upper 12345678901234567890123456789012345678901") == KSTR_OK,
            "c_upper_long");
    require(shell_execute(cmd, sizeof cmd) == SHELL_SERVICE, "r_upper_long");
    require(kstr_copy(cmd, sizeof cmd, "count a1b2") == KSTR_OK, "c_count");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_count");
    require(kstr_copy(cmd, sizeof cmd, "digest ab") == KSTR_OK, "c_digest");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_digest");
    require(kstr_copy(cmd, sizeof cmd, "clear") == KSTR_OK, "c_clear");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_clear");
    require(kstr_copy(cmd, sizeof cmd, "bogus") == KSTR_OK, "c_bogus");
    require(shell_execute(cmd, sizeof cmd) == SHELL_UNKNOWN, "r_bogus");
    empty[0] = 0;
    require(shell_execute(empty, 4) == SHELL_OK, "r_empty");
    /* 13-token line must not be misinterpreted as argc==3; shell_execute must
       return SHELL_TOO_MANY and perform no dispatch. */
    require(kstr_copy(many, sizeof many, "a b c d e f g h i j k l m") == KSTR_OK, "many13");
    require(shell_execute(many, sizeof many) == SHELL_TOO_MANY, "exec_too_many");
    /* Unterminated line (no NUL within cap) must fail deterministically. */
    char unterminated2[8] = {'x',' ','y',' ','z',' ','w',' '};
    require(shell_execute(unterminated2, sizeof unterminated2) == SHELL_INVALID, "exec_unterminated");
    /* Extra arguments must be rejected, not silently ignored. */
    require(kstr_copy(cmd, sizeof cmd, "echo hi extra") == KSTR_OK, "c_echo_extra");
    require(shell_execute(cmd, sizeof cmd) == SHELL_ARGS, "r_echo_extra");
    require(kstr_copy(cmd, sizeof cmd, "upper hi extra") == KSTR_OK, "c_upper_extra");
    require(shell_execute(cmd, sizeof cmd) == SHELL_ARGS, "r_upper_extra");
    require(kstr_copy(cmd, sizeof cmd, "count hi extra") == KSTR_OK, "c_count_extra");
    require(shell_execute(cmd, sizeof cmd) == SHELL_ARGS, "r_count_extra");
    require(kstr_copy(cmd, sizeof cmd, "digest hi extra") == KSTR_OK, "c_digest_extra");
    require(shell_execute(cmd, sizeof cmd) == SHELL_ARGS, "r_digest_extra");
    require(kstr_copy(cmd, sizeof cmd, "version extra") == KSTR_OK, "c_version_extra");
    require(shell_execute(cmd, sizeof cmd) == SHELL_ARGS, "r_version_extra");
    require(kstr_copy(cmd, sizeof cmd, "help extra") == KSTR_OK, "c_help_extra");
    require(shell_execute(cmd, sizeof cmd) == SHELL_ARGS, "r_help_extra");
    /* COUNT must handle >255 correctly (full 64-bit LE). */
    char big[64];
    for (cpu_u64 i = 0; i < 30; ++i) big[i] = '1';
    big[30] = 0;
    char count_big[64];
    require(kstr_copy(count_big, sizeof count_big, "count ") == KSTR_OK, "count_big_pre");
    require(kstr_cat(count_big, sizeof count_big, big) == KSTR_OK, "count_big_cat");
    require(shell_execute(count_big, sizeof count_big) == SHELL_OK, "r_count_big");
    /* Full-width COUNT: 300 digits must decode as 300, not low byte 44. */
    char big300[301];
    for (cpu_u64 i = 0; i < 300; ++i) big300[i] = '1';
    big300[300] = 0;
    cpu_u8 cnt[8]; cpu_u64 clen = 0;
    require(krst_call(KRST_SVC_COUNT_DIGITS, big300, 300, cnt, sizeof cnt, &clen) == KRST_OK && clen == 8, "count300_call");
    cpu_u64 cntv = 0; for (unsigned int i = 0; i < 8; ++i) cntv |= (cpu_u64)cnt[i] << (i * 8);
    require(cntv == 300, "count300_value");
    cpu_u8 le300b[8] = {44, 1, 0, 0, 0, 0, 0, 0};
    cpu_u8 le256b[8] = {0, 1, 0, 0, 0, 0, 0, 0};
    require(shell_decode_u64_le(le300b, 8) == 300, "decode300");
    require(shell_decode_u64_le(le256b, 8) == 256, "decode256");
    require(shell_decode_u64_le(le300b, 7) == 0, "decode_short");
    /* Shell must recover and continue after malformed command. */
    require(kstr_copy(cmd, sizeof cmd, "bogus") == KSTR_OK, "c_bogus2");
    require(shell_execute(cmd, sizeof cmd) == SHELL_UNKNOWN, "r_bogus2");
    require(kstr_copy(cmd, sizeof cmd, "version") == KSTR_OK, "c_version2");
    require(shell_execute(cmd, sizeof cmd) == SHELL_OK, "r_version2");
    text("[SHELL] dispatch and error rejection verified (synthetic)\r\n");
}

#if RYNOR_SHELL_INTERACTIVE
static void interactive_tests(void)
{
    struct kbd_statistics kb, kb_after;
    require(kbd_statistics(&kb), "kb_before");
    shell_run(SHELL_SESSION_KEYS);
    require(kbd_statistics(&kb_after), "kb_after");
    /* The host sent exactly this many make+break scan bytes with no loss. */
    require(kb_after.received == kb.received + 2 * SHELL_SESSION_KEYS &&
            kb_after.dropped == kb.dropped && !kb_after.errors && !kb_after.auxiliary,
            "kb_counts");
    field("[SHELL] keys=", SHELL_SESSION_KEYS);
    field(" received_scan_bytes=", kb_after.received - kb.received);
    text("\r\n[SHELL] real keyboard session verified\r\n");
}
#endif

static void editor_tests(void)
{
    /* Line-editor boundary: exactly SHELL_LINE_MAX bytes fit; the 65th is
       rejected with the len/NUL invariant intact. Silent on pass (the host
       validator requires an exact transcript); any require() failure halts
       with [SHELL] failure=. Regression: an off-by-one here once evaded
       every gate and wrote one NUL past data[]. */
    struct shell_line line;
    line.len = 0;
    line.data[0] = '\0';
    for (cpu_u64 i = 0; i < SHELL_LINE_MAX; ++i)
        require(line_insert(&line, (char)('a' + (i % 26))) == 1, "editor_fill");
    require(line.len == SHELL_LINE_MAX && line.data[SHELL_LINE_MAX] == '\0', "editor_full");
    require(line_insert(&line, 'z') == 0, "editor_reject");
    require(line.len == SHELL_LINE_MAX && line.data[SHELL_LINE_MAX] == '\0', "editor_stable");
    line_backspace(&line);
    require(line.len == SHELL_LINE_MAX - 1 && line.data[line.len] == '\0', "editor_backspace");
    require(line_insert(&line, 'q') == 1 && line.len == SHELL_LINE_MAX, "editor_refill");
    line.len = 0;
    line.data[0] = '\0';
    line_backspace(&line);
    require(line.len == 0 && line.data[0] == '\0', "editor_backspace_empty");
}

void shell_self_test(void)
{
    require(cpu_interrupts_disabled(), "if0");
    struct accounting before = account();
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage11 shell monitor\r\n"
         "[SHELL] self-test started\r\n");
    parser_tests();
    editor_tests();
#if RYNOR_SHELL_INTERACTIVE
    interactive_tests();
#else
    text("[SHELL] interactive session skipped (host did not request input)\r\n");
#endif
    balanced(before);
    field("[SHELL] final allocated_bytes=", before.pmm.allocated_bytes);
    field(" free_bytes=", before.pmm.free_bytes);
    field(" table_pages=", before.tables); text("\r\n");
    text("[TEST] shell monitor verified\r\n");
}


