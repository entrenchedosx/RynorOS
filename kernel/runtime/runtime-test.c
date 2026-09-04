/* Stage 10 basic kernel runtime: bounded string, buffer and runtime-service
   self-tests, then the same services driven from real worker threads through
   the verified Stage 7 scheduler. The worker digests are pure and deterministic
   so the host can recompute them independently. Physical worker/IRQ records
   provide the separate execution evidence that fixed serial folds cannot. */

#include "kstring.h"
#include "kbuf.h"
#include "krst.h"
#include "ksched.h"
#include "heap.h"
#include "pmm.h"
#include "vm.h"
#include "irq.h"
#include "io.h"
#include "serial.h"

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[RUNTIME] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
static void text(const char *s) { require(serial_write(s), "serial"); }
static void number(cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    text(b + i);
}
static void field(const char *s, cpu_u64 n) { text(s); number(n); }
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

static void string_tests(void)
{
    char buf[64];
    /* nlen bounds: never reads past max. */
    require(kstr_nlen("abc", 10) == 3 && kstr_nlen("abcdef", 3) == 3 &&
            kstr_nlen("", 5) == 0 && kstr_nlen("zzz", 0) == 0, "nlen");
    /* copy fits / overflow / invalid. */
    require(kstr_copy(buf, 64, "hello") == KSTR_OK && kstr_cmp(buf, "hello", 64) == 0 &&
            buf[5] == 0, "copy_fit");
    require(kstr_copy(buf, 4, "hello") == KSTR_OVERFLOW && kstr_cmp(buf, "hello", 64) == 0,
            "copy_overflow_untouched");
    require(kstr_copy(buf, 0, "x") == KSTR_INVALID && kstr_copy(0, 8, "x") == KSTR_INVALID,
            "copy_invalid");
    /* cat fits / exactly-capacity / overflow. */
    require(kstr_copy(buf, 8, "AB") == KSTR_OK && kstr_cat(buf, 8, "CD") == KSTR_OK &&
            kstr_cmp(buf, "ABCD", 8) == 0, "cat_fit");
    require(kstr_copy(buf, 8, "ABCDEF") == KSTR_OK && kstr_cat(buf, 8, "G") == KSTR_OK &&
            kstr_cmp(buf, "ABCDEFG", 8) == 0, "cat_fit_exact");
    require(kstr_copy(buf, 8, "ABCDEF") == KSTR_OK && kstr_cat(buf, 8, "GH") == KSTR_OVERFLOW &&
            kstr_cmp(buf, "ABCDEF", 8) == 0, "cat_overflow");
    require(kstr_copy(buf, 4, "ABC") == KSTR_OK && kstr_cat(buf, 4, "D") == KSTR_OVERFLOW &&
            kstr_cmp(buf, "ABC", 4) == 0, "cat_exact_cap_overflow");
    /* cmp / cmpmem. */
    require(kstr_cmp("abc", "abc", 4) == 0 && kstr_cmp("abc", "abd", 4) < 0 &&
            kstr_cmp("abd", "abc", 4) > 0 && kstr_cmp("abc", "abcd", 3) == 0, "cmp");
    require(kstr_cmpmem("ab", "ab", 2) == 0 && kstr_cmpmem("ab", "ac", 2) < 0 &&
            kstr_cmpmem("ab", "a", 1) == 0, "cmpmem");
    /* chr found / not found. */
    cpu_u64 pos = 99;
    require(kstr_chr("axb", 3, 'x', &pos) == 1 && pos == 1, "chr_found");
    require(kstr_chr("axb", 3, 'z', &pos) == 0 && pos == 1, "chr_missing");
    /* move overlap-safe. */
    char mv[16];
    kstr_copy(mv, 16, "0123456789"); kstr_move(mv + 2, mv, 8);
    require(kstr_cmp(mv, "0101234567", 16) == 0, "move_overlap");
    /* bounded formatting, host-recomputed. %u/%x/%X take a cpu_u64 argument. */
    enum kstr_result fr;
    fr = kstr_format(buf, 64, "rynor %u %x", (cpu_u64)42, (cpu_u64)42);
    require(fr == KSTR_OK && kstr_cmp(buf, "rynor 42 2a", 64) == 0, "fmt_1");
    fr = kstr_format(buf, 64, "%u", (cpu_u64)334);
    require(fr == KSTR_OK && kstr_cmp(buf, "334", 64) == 0, "fmt_2");
    fr = kstr_format(buf, 64, "%X", (cpu_u64)255);
    require(fr == KSTR_OK && kstr_cmp(buf, "FF", 64) == 0, "fmt_3");
    fr = kstr_format(buf, 64, "ab%%cd %c", 'Z');
    require(fr == KSTR_OK && kstr_cmp(buf, "ab%cd Z", 64) == 0, "fmt_4");
    fr = kstr_format(buf, 4, "toolong");
    require(fr == KSTR_OVERFLOW && kstr_cmp(buf, "ab%cd Z", 64) == 0, "fmt_overflow_untouched");
    fr = kstr_format(buf, 64, "%s", "wrld");
    require(fr == KSTR_OK && kstr_cmp(buf, "wrld", 64) == 0, "fmt_s");
    /* utoa helpers. */
    require(kstr_utoa(buf, 64, 0) == KSTR_OK && kstr_cmp(buf, "0", 64) == 0, "utoa0");
    require(kstr_utoa(buf, 64, 12345) == KSTR_OK && kstr_cmp(buf, "12345", 64) == 0, "utoa");
    require(kstr_utoa_hex(buf, 64, 42, 0) == KSTR_OK && kstr_cmp(buf, "2a", 64) == 0, "hex_low");
    require(kstr_utoa_hex(buf, 64, 42, 1) == KSTR_OK && kstr_cmp(buf, "2A", 64) == 0, "hex_up");
    /* observed format outputs for independent host checks. */
    char f0[64], f1[64], f2[64];
    require(kstr_format(f0, sizeof f0, "rynor %u %x", (cpu_u64)42, (cpu_u64)42) == KSTR_OK, "f0");
    require(kstr_format(f1, sizeof f1, "%u", (cpu_u64)334) == KSTR_OK, "f1");
    require(kstr_format(f2, sizeof f2, "%X", (cpu_u64)255) == KSTR_OK, "f2");
    text("[STR] fmt0=\""); text(f0); text("\" fmt1=\""); text(f1);
    text("\" fmt2=\""); text(f2); text("\"\r\n");
    text("[STR] strings, bounds, overlap and formatting verified (synthetic)\r\n");
}

static void buffer_tests(void)
{
    cpu_u8 storage[8];
    struct kbuffer b;
    char rd[8];
    require(kbuf_init(&b, storage, 8) == KBUF_OK && kbuf_capacity(&b) == 8 &&
            kbuf_used(&b) == 0 && kbuf_remaining(&b) == 8, "buf_init");
    require(kbuf_init(&b, 0, 8) == KBUF_INVALID && kbuf_init(&b, storage, 0) == KBUF_INVALID,
            "buf_bad_init");
    /* append fit / full / no partial. */
    require(kbuf_append(&b, "abcd", 4) == KBUF_OK && kbuf_used(&b) == 4 &&
            kbuf_remaining(&b) == 4, "buf_append");
    require(kbuf_append(&b, "EFGH", 4) == KBUF_OK && kbuf_used(&b) == 8 &&
            kbuf_remaining(&b) == 0, "buf_fill");
    require(kbuf_append(&b, "x", 1) == KBUF_FULL && kbuf_used(&b) == 8, "buf_full_no_partial");
    require(kbuf_append_byte(&b, 'y') == KBUF_FULL, "buf_byte_full");
    /* peek / read / consume. */
    cpu_u8 p = 0;
    require(kbuf_peek(&b, 0, &p) == KBUF_OK && p == 'a' && kbuf_peek(&b, 3, &p) == KBUF_OK &&
            p == 'd', "buf_peek");
    require(kbuf_peek(&b, 8, &p) == KBUF_EMPTY, "buf_peek_oob");
    require(kbuf_read(&b, rd, 2) == KBUF_OK && kstr_cmpmem(rd, "ab", 2) == 0 && kbuf_used(&b) == 6,
            "buf_read");
    require(kbuf_read(&b, rd, 7) == KBUF_EMPTY && kbuf_used(&b) == 6, "buf_read_oob");
    require(kbuf_consume(&b, 2) == KBUF_OK && kbuf_used(&b) == 4, "buf_consume");
    require(kbuf_consume(&b, 5) == KBUF_EMPTY, "buf_consume_oob");
    /* wrap-around: drain front then append so the write index crosses the end. */
    require(kbuf_clear(&b) == KBUF_OK, "buf_clear");
    require(kbuf_append(&b, "uvwxyz", 6) == KBUF_OK &&
            kbuf_consume(&b, 6) == KBUF_OK, "wrap_offset");
    require(kbuf_append(&b, "abcd", 4) == KBUF_OK, "wrap_fill");
    require(kbuf_read(&b, rd, 2) == KBUF_OK && kstr_cmpmem(rd, "ab", 2) == 0, "wrap_drain");
    require(kbuf_append(&b, "ef", 2) == KBUF_OK, "wrap_append");
    require(kbuf_read(&b, rd, 4) == KBUF_OK && kstr_cmpmem(rd, "cdef", 4) == 0, "wrap_read");
    require(kbuf_used(&b) == 0 && kbuf_remaining(&b) == 8, "wrap_done");
    rd[4] = 0;
    text("[BUF] wrap=\""); text(rd); text("\"\r\n");
    /* repeated clear/reuse cycles are stable. */
    for (unsigned int k = 0; k < 50; ++k) {
        require(kbuf_clear(&b) == KBUF_OK && kbuf_init(&b, storage, 8) == KBUF_OK, "buf_recycle");
        for (unsigned int j = 0; j < 8; ++j)
            require(kbuf_append_byte(&b, (cpu_u8)('0' + k % 10)) == KBUF_OK, "buf_recycle_fill");
        require(kbuf_read(&b, rd, 8) == KBUF_OK, "buf_recycle_read");
        for (unsigned int j = 0; j < 8; ++j) require(rd[j] == (char)('0' + k % 10), "buf_recycle_val");
    }
    text("[BUF] buffers, wrap, capacity and bounds verified (synthetic)\r\n");
}

static void service_tests(void)
{
    /* Fixed inputs the host recomputes. */
    static const char up_in[] = "HeLlo WoRlD 123";
    char up_out[32]; cpu_u64 out_len = 0;
    cpu_u8 dig[8]; cpu_u8 cnt[8];
    require(krst_call(KRST_SVC_UPPER, up_in, sizeof up_in - 1, up_out, sizeof up_out, &out_len) ==
            KRST_OK && out_len == sizeof up_in - 1 &&
            kstr_cmpmem(up_out, "HELLO WORLD 123", sizeof up_in - 1) == 0, "svc_upper");
    require(krst_call(KRST_SVC_DIGEST, up_in, sizeof up_in - 1, dig, sizeof dig, &out_len) ==
            KRST_OK && out_len == 8, "svc_digest_shape");
    require(krst_call(KRST_SVC_COUNT_DIGITS, "a1b2c3x9", 8, cnt, sizeof cnt, &out_len) ==
            KRST_OK && out_len == 8 && cnt[0] == 4 && cnt[1] == 0, "svc_count");
    /* Negative dispatch. */
    require(krst_call((enum krst_op)99, up_in, 4, up_out, 32, &out_len) == KRST_BAD_OP, "bad_op");
    require(krst_call(KRST_SVC_UPPER, 0, 4, up_out, 32, &out_len) == KRST_BAD_ARGS, "bad_null_in");
    require(krst_call(KRST_SVC_UPPER, up_in, 4, 0, 0, &out_len) == KRST_BAD_ARGS, "bad_out");
    require(krst_call(KRST_SVC_UPPER, up_in, 4, up_out, 2, &out_len) == KRST_TOO_SMALL, "small_out");
    require(krst_call(KRST_SVC_DIGEST, up_in, 4, up_out, 4, &out_len) == KRST_TOO_SMALL, "small_digest");
    /* In/out overlap must be rejected instead of corrupting the caller's data. */
    char ovl[32]; kstr_copy(ovl, sizeof ovl, "HeLlo WoRlD 123");
    require(krst_call(KRST_SVC_UPPER, ovl, 8, ovl, 8, &out_len) == KRST_BAD_ARGS &&
            kstr_cmp(ovl, "HeLlo WoRlD 123", sizeof ovl) == 0, "overlap");
    /* Self-consistency: digest matches a direct call. */
    cpu_u64 d1 = krst_digest(up_in, sizeof up_in - 1);
    cpu_u64 d2 = 0; for (unsigned int i = 0; i < 8; ++i) d2 |= (cpu_u64)dig[i] << (i * 8);
    require(d1 == d2, "svc_digest_self");
    text("[SVC] digest, uppercase and count services verified (synthetic)\r\n");
    text("[RUNTIME] dispatch rejects invalid, overlapping and undersized requests\r\n");
}

#define WORKERS (SCHED_THREADS - 1) /* seven worker threads, max pool */
#define ROUNDS 40u
static const char *const W_INPUT[WORKERS] = {
    "w0:data0123", "w1:xyz987", "w2:kernelruntime", "w3:0123456789",
    "w4:abcdefghiz", "w5:rhinoS10SERV", "w6:q",
};
_Static_assert(sizeof W_INPUT / sizeof W_INPUT[0] == WORKERS,
               "worker input table must match the worker pool exactly");
/* Stable evidence layout, dumped through QEMU physical memory using ELF
   symbols. Each worker writes its own slot; bootstrap reads after join. */
struct worker_out { cpu_u64 acc, rounds, id, stack, preemptions, rip, rsp, probe, attempts; };
volatile struct worker_out runtime_evidence[WORKERS];
static volatile unsigned int irq_checks, irq_bad;
extern char __runtime_service_start[], __runtime_service_end[];
extern void runtime_boundary_tests(void);
static void runtime_timer(void)
{
    cpu_u64 len = 123, output = 456;
    for (unsigned int op = 0; op < KRST_OP_COUNT; ++op)
        if (krst_call(op, "ab", 2, &output, 8, &len) != KRST_BAD_CONTEXT ||
            len != 123 || output != 456 || !cpu_interrupts_disabled()) irq_bad = 1;
    ++irq_checks;
}

static void digester(void *arg)
{
    cpu_u64 slot = (cpu_u64)arg;
    require(slot < WORKERS && !cpu_interrupts_disabled(), "worker_context");
    cpu_u64 acc = 0;
    cpu_u8 out[8]; cpu_u64 n = 0;
    const char *in = W_INPUT[slot];
    cpu_u64 inlen = kstr_nlen(in, KSTR_NLEN_MAX);
    runtime_evidence[slot].id = thread_current();
    cpu_u64 stack;
    require(thread_current_stack_base(&stack), "worker_stack");
    runtime_evidence[slot].stack = stack;
    for (cpu_u64 r = 0; r < ROUNDS; ++r) {
        require(krst_call(KRST_SVC_DIGEST, in, inlen, out, sizeof out, &n) == KRST_OK &&
                n == KRST_DIGEST_BYTES, "worker_digest");
        cpu_u64 d = 0;
        for (unsigned int i = 0; i < 8; ++i) d |= (cpu_u64)out[i] << (i * 8);
        acc = acc * 131 + d;
        ++runtime_evidence[slot].rounds;
        require(thread_yield(), "worker_yield");
    }
    runtime_evidence[slot].acc = acc;
    /* No yield in this phase. Repeat real bounded service calls until two
       timer preemptions have been recorded with RIP inside the service code.
       Finite attempt limit, plus the external boot deadline, bounds failure. */
    cpu_u8 payload[4096];
    for (unsigned int i = 0; i < sizeof payload; ++i) payload[i] = (cpu_u8)(i + slot);
    cpu_u64 last = 0, hits = 0;
    for (cpu_u64 attempt = 1; attempt <= 131072; ++attempt) {
        require(krst_call(KRST_SVC_DIGEST, payload, sizeof payload, out, sizeof out, &n) == KRST_OK &&
                n == 8 && !cpu_interrupts_disabled(), "worker_probe");
        cpu_u64 flags = irq_save();
        struct thread_statistics ts;
        require(thread_statistics(thread_current(), &ts), "worker_statistics");
        if (ts.preemptions != last && ts.irq_rip >= (cpu_u64)__runtime_service_start &&
            ts.irq_rip < (cpu_u64)__runtime_service_end) {
            ++hits;
            runtime_evidence[slot].preemptions = hits;
            runtime_evidence[slot].rip = ts.irq_rip;
            runtime_evidence[slot].rsp = ts.irq_rsp;
        }
        last = ts.preemptions;
        irq_restore(flags);
        if (hits >= 2) {
            cpu_u64 probe = 0;
            for (unsigned int j = 0; j < 8; ++j) probe |= (cpu_u64)out[j] << (j * 8);
            runtime_evidence[slot].probe = probe;
            runtime_evidence[slot].attempts = attempt;
            return;
        }
    }
    require(0, "service_preemption_missing");
}

static void text_hex(cpu_u64 v, int upper)
{
    char b[17];
    require(kstr_utoa_hex(b, sizeof b, v, upper) == KSTR_OK, "hex_fmt");
    text(b);
}

static void thread_integration_tests(void)
{
    struct accounting before = account();
    thread_id ids[WORKERS];
    for (unsigned int i = 0; i < WORKERS; ++i) runtime_evidence[i] = (struct worker_out){0};
    irq_checks = 0; irq_bad = 0;
    require(irq_set_handler(0, runtime_timer) && irq_set_enabled(0, 1), "runtime_timer_start");
    for (unsigned int i = 0; i < WORKERS; ++i) {
        if (!thread_create(&ids[i], digester, (void *)(cpu_u64)i)) {
            while (thread_ready_count() > 1) require(thread_yield(), "partial_run");
            for (unsigned int j = 0; j < i; ++j) require(thread_join(ids[j]), "partial_join");
            require(irq_set_enabled(0, 0), "partial_mask");
            balanced(before);
            require(0, "worker_create_rolled_back");
        }
    }
    /* Run workers to completion with explicit yields; rejoin and reap each. */
    while (thread_ready_count() > 1) require(thread_yield(), "host_yield");
    require(irq_set_enabled(0, 0) && irq_checks && !irq_bad && !pic_in_service(), "runtime_irq_context");
    enum thread_state st = THREAD_FREE;
    for (unsigned int i = 0; i < WORKERS; ++i) {
        require(thread_state(ids[i], &st) && st == THREAD_EXITED, "worker_exited");
        require(thread_join(ids[i]), "worker_join");
    }
    /* Validate every digest against the same fold the host recomputes, and
       require the observed worker threads really ran the full round count. */
    cpu_u64 total = 0;
    for (unsigned int i = 0; i < WORKERS; ++i) {
        cpu_u64 expect = 0;
        for (cpu_u64 r = 0; r < ROUNDS; ++r)
            expect = expect * 131 + krst_digest(W_INPUT[i], kstr_nlen(W_INPUT[i], KSTR_NLEN_MAX));
        require(runtime_evidence[i].rounds == ROUNDS && runtime_evidence[i].acc == expect,
                "worker_digest_value");
        total += runtime_evidence[i].acc;
        field("[RUNTIME] worker=", (cpu_u64)i);
        text(" acc=0x"); text_hex(runtime_evidence[i].acc, 1);
        field(" rounds=", runtime_evidence[i].rounds); text("\r\n");
    }
    field("[RUNTIME] total=", total); text("\r\n");
    balanced(before);
    text("[RUNTIME] worker digests and round counts verified under preemption\r\n");
}

void runtime_self_test(void)
{
    require(cpu_interrupts_disabled(), "runtime_if0");
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage10 basic kernel runtime\r\n"
         "[RUNTIME] self-test started\r\n");
    struct accounting before = account();
    runtime_boundary_tests();
    string_tests();
    buffer_tests();
    service_tests();
    thread_integration_tests();
    balanced(before);
    struct accounting after = account();
    field("[RUNTIME] final allocated_bytes=", after.pmm.allocated_bytes);
    field(" free_bytes=", after.pmm.free_bytes);
    field(" table_pages=", after.tables); text("\r\n");
    text("[TEST] runtime api verified\r\n");
}
