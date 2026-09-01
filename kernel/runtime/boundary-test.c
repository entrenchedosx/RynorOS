/* Guest execution probes. Guard mappings are real PMM/VM pages, not a mock. */
#include "krst.h"
#include "vm.h"
#include "serial.h"
#include "ksched.h"
#include "io.h"

static volatile unsigned int completed;

static void check(int ok, const char *why)
{
    if (ok) return;
    (void)serial_write("[RUNTIME] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}

static void service_worker(void *arg)
{
    (void)arg;
    char out[8]; cpu_u64 len = 0;
    for (unsigned int i = 0; i < 8; ++i) {
        check(krst_call(KRST_SVC_UPPER, "ab12", 4, out, 8, &len) == KRST_OK && len == 4 &&
              kstr_cmpmem(out, "AB12", 4) == 0 && !cpu_interrupts_disabled(), "reuse_worker_upper");
        check(krst_call(KRST_SVC_COUNT_DIGITS, out, 4, &len, 8, (cpu_u64 *)0) == KRST_BAD_ARGS,
              "reuse_worker_invalid");
        cpu_u64 f = irq_save();
        check(krst_call(KRST_SVC_COUNT_DIGITS, "ab12", 4, out, 8, &len) == KRST_OK &&
              out[0] == 2 && cpu_interrupts_disabled(), "reuse_worker_if0");
        irq_restore(f);
        check(thread_yield(), "reuse_yield");
    }
    cpu_u64 f = irq_save(); ++completed; irq_restore(f);
}

static void lifecycle_tests(void)
{
    struct pmm_statistics before, after;
    check(pmm_statistics(&before) == PMM_OK, "lifecycle_before");
    cpu_u64 tables = vm_kernel_space()->table_pages;
    for (unsigned int cycle = 0; cycle < 3; ++cycle) {
        thread_id ids[SCHED_THREADS - 1], extra = ~(cpu_u64)0;
        completed = 0;
        for (unsigned int i = 0; i < SCHED_THREADS - 1; ++i)
            check(thread_create(&ids[i], service_worker, 0), "reuse_create");
        check(!thread_create(&extra, service_worker, 0) && extra == ~(cpu_u64)0, "runtime_slot_full");
        while (thread_ready_count() > 1) check(thread_yield(), "reuse_run");
        for (unsigned int i = 0; i < SCHED_THREADS - 1; ++i)
            check(thread_join(ids[i]), "reuse_join");
        check(completed == SCHED_THREADS - 1 && scheduler_check(), "reuse_complete");
    }
    /* Real exhaustion, with one live owned worker. Failed creation must not
       damage it. All exhausted frames stay on an intrusive ownership list. */
    thread_id held, rejected = ~(cpu_u64)0;
    check(thread_create(&held, service_worker, 0), "oom_held_create");
    cpu_u64 head = 0, frame;
    enum pmm_result result;
    while ((result = pmm_allocate(&frame)) == PMM_OK) {
        volatile cpu_u64 *p = vm_frame_access(frame);
        check(p != 0, "runtime_oom_access"); *p = head; head = frame;
    }
    check(result == PMM_OUT_OF_MEMORY, "runtime_oom_exhaust");
    cpu_u64 out, len;
    check(krst_call(KRST_SVC_DIGEST, "ab", 2, &out, 8, &len) == KRST_OK && len == 8,
          "service_under_oom");
    check(!thread_create(&rejected, service_worker, 0) && rejected == ~(cpu_u64)0,
          "runtime_create_oom");
    while (head) {
        frame = head;
        volatile cpu_u64 *p = vm_frame_access(frame);
        check(p != 0, "runtime_oom_restore_access"); head = *p;
        check(pmm_release(frame) == PMM_OK, "runtime_oom_restore");
    }
    while (thread_ready_count() > 1) check(thread_yield(), "oom_held_run");
    check(thread_join(held) && pmm_statistics(&after) == PMM_OK &&
          before.allocated_bytes == after.allocated_bytes && before.free_bytes == after.free_bytes &&
          tables == vm_kernel_space()->table_pages && scheduler_check() && pmm_check(), "runtime_lifecycle_balance");
}

void runtime_boundary_tests(void)
{
    char b[32] = "unchanged";
    check(kstr_copy_n(b, 1, "", 1) == KSTR_OK && b[0] == 0 &&
          kstr_cat_n(b, 1, "", 1) == KSTR_OK, "empty_copy_cat");
    check(kstr_format(b, 5, "%s", "abcd") == KSTR_OK && b[4] == 0, "format_exact");
    check(kstr_format(b, 1, "%s", "") == KSTR_OK && b[0] == 0, "format_empty");
    check(kstr_format(b, 4, "%s", "abcd") == KSTR_OVERFLOW && b[0] == 0, "format_overflow");
    check(kstr_format(b, sizeof b, "bad%") == KSTR_INVALID && b[0] == 0, "format_invalid");
    check(kstr_copy(b, sizeof b, "%s") == KSTR_OK &&
          kstr_format(b, sizeof b, b, "x") == KSTR_INVALID && b[0] == '%', "format_alias");
    check(kstr_format(b, sizeof b, "x%s", b) == KSTR_INVALID && b[0] == '%', "format_arg_alias");
    check(kstr_copy(b, sizeof b, "abcd") == KSTR_OK &&
          kstr_copy(b + 1, 8, b) == KSTR_OK && kstr_cmp(b, "aabcd", 6) == 0, "copy_overlap");
    check(kstr_copy(b, sizeof b, "ab") == KSTR_OK && kstr_cat(b, sizeof b, b) == KSTR_OK &&
          kstr_cmp(b, "abab", 5) == 0, "cat_overlap");
    check(kstr_move(b, b + 1, 4) == KSTR_OK && kstr_cmp(b, "bab", 4) == 0, "move_left");
    check(kstr_move(0, 0, 0) == KSTR_OK &&
          kstr_move((void *)~(cpu_u64)0, b, 2) == KSTR_INVALID, "move_wrap");
    check(kstr_utoa(b, 21, ~(cpu_u64)0) == KSTR_OK &&
          kstr_cmp(b, "18446744073709551615", 21) == 0, "utoa_max");
    check(kstr_utoa_hex(b, 17, ~(cpu_u64)0, 0) == KSTR_OK &&
          kstr_cmp(b, "ffffffffffffffff", 17) == 0, "hex_max");
    char bytes[] = {'a', 0, 'z'}; cpu_u64 pos = 8;
    check(kstr_nlen(bytes, 3) == 1 && kstr_chr(bytes, 3, 0, &pos) && pos == 1 &&
          kstr_cmpmem(bytes, "a\0z", 3) == 0, "embedded_nul");

    /* Ends touch non-present pages; a single out-of-bound access faults. */
    const cpu_u64 va = VM_TEST_BASE;
    struct vm_mapping m;
    cpu_u64 frame;
    check(vm_query(vm_kernel_space(), va - VM_PAGE_SIZE, &m) == VM_NOT_MAPPED &&
          vm_query(vm_kernel_space(), va + VM_PAGE_SIZE, &m) == VM_NOT_MAPPED, "guard_unmapped");
    check(pmm_allocate(&frame) == PMM_OK &&
          vm_map(vm_kernel_space(), va, frame, VM_WRITE) == VM_OK, "guard_map");
    char *page = (char *)va;
    for (cpu_u64 i = 0; i < VM_PAGE_SIZE; ++i) page[i] = 'Q';
    char *edge = page + VM_PAGE_SIZE - 8;
    check(kstr_nlen(edge, 8) == 8 &&
          kstr_cat(edge, 8, "x") == KSTR_TERMINATION, "cat_guard");
    check(kstr_copy_n(b, sizeof b, edge, 8) == KSTR_TERMINATION &&
          kstr_cat_n(b, sizeof b, edge, 8) == KSTR_TERMINATION, "source_guard");
    check(kstr_format(b, sizeof b, "%s", page) == KSTR_TERMINATION &&
          kstr_format(b, sizeof b, page) == KSTR_TERMINATION, "format_guard");
    check(kstr_copy(edge, 8, "ABCDEFG") == KSTR_OK && edge[7] == 0 &&
          kstr_copy(edge, 8, "ABCDEFGH") == KSTR_OVERFLOW && edge[7] == 0, "copy_guard_exact");
    check(vm_unmap(vm_kernel_space(), va) == VM_OK && pmm_release(frame) == PMM_OK, "guard_release");

    /* Check all FIFO positions across many true wraps, odd capacities included. */
    cpu_u8 storage[11], out[9];
    for (cpu_u64 cap = 1; cap <= 9; ++cap) {
        struct kbuffer q;
        storage[0] = 0xA5; storage[cap + 1] = 0x5A;
        check(kbuf_init(&q, storage + 1, cap) == KBUF_OK, "ring_init");
        for (unsigned int cycle = 0; cycle < 32; ++cycle) {
            for (cpu_u64 j = 0; j < cap; ++j)
                check(kbuf_append_byte(&q, (cpu_u8)j) == KBUF_OK, "ring_fill");
            check(kbuf_used(&q) == cap && !kbuf_remaining(&q) &&
                  kbuf_append(&q, "x", 1) == KBUF_FULL, "ring_full");
            for (cpu_u64 j = 0; j < cap; ++j) {
                cpu_u8 v = 0xFF;
                check(kbuf_read(&q, &v, 1) == KBUF_OK && v == j, "ring_order");
                check(kbuf_append_byte(&q, (cpu_u8)(j + 16)) == KBUF_OK, "ring_wrap");
            }
            check(kbuf_read(&q, out, cap) == KBUF_OK && !kbuf_used(&q), "ring_drain");
            for (cpu_u64 j = 0; j < cap; ++j) check(out[j] == j + 16, "ring_all_bytes");
            check(kbuf_append(&q, 0, 0) == KBUF_OK && kbuf_read(&q, 0, 0) == KBUF_OK &&
                  kbuf_consume(&q, 1) == KBUF_EMPTY && kbuf_clear(&q) == KBUF_OK &&
                  storage[0] == 0xA5 && storage[cap + 1] == 0x5A, "ring_sentinels");
        }
        check(kbuf_append(&q, storage + 1, 1) == KBUF_INVALID &&
              kbuf_read(&q, &q, 1) == KBUF_INVALID, "ring_alias");
        q.cap = 0;
        check(kbuf_consume(&q, 0) == KBUF_INVALID && kbuf_clear(&q) == KBUF_INVALID, "ring_zero_corrupt");
        q.cap = cap; q.head = cap;
        check(kbuf_append_byte(&q, 1) == KBUF_INVALID, "ring_head_corrupt");
        q.head = 0; q.count = cap + 1;
        check(!kbuf_remaining(&q) && kbuf_read(&q, out, 1) == KBUF_INVALID, "ring_count_corrupt");
    }

    cpu_u64 alias = 123, len = 456;
    cpu_u8 result[16];
    for (unsigned int op = 0; op < KRST_OP_COUNT; ++op) {
        for (unsigned int j = 0; j < sizeof result; ++j) result[j] = 0xA5;
        check(krst_call(op, "ab", 2, 0, 0, &len) == KRST_BAD_ARGS && len == 456, "service_null_all");
        check(krst_call(op, "ab", 2, &alias, 8, &alias) == KRST_BAD_ARGS && alias == 123, "service_length_alias");
        check(krst_call(op, &alias, 8, result, 16, &alias) == KRST_BAD_ARGS && alias == 123, "service_input_length_alias");
        check(krst_call(op, (void *)(~(cpu_u64)0 - 1), 3, result, 16, &len) == KRST_BAD_ARGS &&
              krst_call(op, "ab", 2, (void *)~(cpu_u64)0, 2, &len) == KRST_BAD_ARGS, "service_wrap");
        check(krst_call(op, "ab", 2, result, 1, &len) == KRST_TOO_SMALL && len == 456, "service_small_all");
        for (unsigned int j = 0; j < sizeof result; ++j) check(result[j] == 0xA5, "service_unchanged");
    }
    check(krst_call(KRST_SVC_UPPER, 0, 0, result, 0, &len) == KRST_OK && !len, "upper_empty");
    check(krst_call(KRST_SVC_DIGEST, 0, 0, result, 8, &len) == KRST_OK && len == 8, "digest_empty");
    check(krst_call(KRST_SVC_COUNT_DIGITS, 0, 0, result, 8, &len) == KRST_OK &&
          kstr_cmpmem(result, "\0\0\0\0\0\0\0\0", 8) == 0, "count_empty");
    check(krst_call(KRST_SVC_DIGEST, b, KRST_MAX_BYTES + 1, result, 8, &len) == KRST_BAD_ARGS, "service_max");
    cpu_u8 raw[] = {0, 0xFF, 'a', '9'}, upper[] = {0, 0xFF, 'A', '9'};
    for (unsigned int j = 0; j < sizeof result; ++j) result[j] = 0xA5;
    check(krst_call(KRST_SVC_UPPER, raw, sizeof raw, result, sizeof result, &len) == KRST_OK &&
          len == sizeof raw && kstr_cmpmem(result, upper, sizeof upper) == 0, "service_binary_upper");
    for (unsigned int j = sizeof raw; j < sizeof result; ++j) check(result[j] == 0xA5, "service_tail");
    check(krst_call(KRST_SVC_DIGEST, b, 1, result, sizeof result, (cpu_u64 *)(result + 1)) == KRST_BAD_ARGS,
          "service_length_alignment");
    lifecycle_tests();
}
