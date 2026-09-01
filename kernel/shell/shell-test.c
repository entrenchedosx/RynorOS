/* Stage 11 shell/monitor self-test. Synthetic tokenizer/dispatch tests run in
   every build and produce deterministic serial evidence for the host
   validator. When built with RYNOR_SHELL_INTERACTIVE (only the dedicated
   integration tests set this), the self-test then drives a real interactive
   session: the host sends physical keyboard keys, the shell translates/echoes
   them, builds command lines and dispatches to the implemented runtime
   services. This proves the monitor accepts real input and exposes only real
   services. */

#include "shell.h"
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
    require(kstr_copy(many, sizeof many, "a b c d e f g h i j k l m") == KSTR_OK, "over_copy");
    require(shell_tokenize(many, sizeof many, tokens) == SHELL_TOO_MANY, "token_too_many");
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
    text("[SHELL] dispatch and error rejection verified (synthetic)\r\n");
}

#if defined(RYNOR_SHELL_INTERACTIVE)
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

void shell_self_test(void)
{
    require(cpu_interrupts_disabled(), "if0");
    struct accounting before = account();
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage11 shell monitor\r\n"
         "[SHELL] self-test started\r\n");
    parser_tests();
#if defined(RYNOR_SHELL_INTERACTIVE)
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


