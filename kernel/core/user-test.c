#include "user.h"
#include "ksched.h"
#include "irq.h"
#include "heap.h"
#include "pmm.h"
#include "io.h"
#include "serial.h"

/* Stage 18a self-test: lifecycle/admission, OOM rollback, gate
   exit/yield, fault matrix, deterministic 2-task CPL3 preemption.
   Runs after the filesystem section; its [USER] lines trail [FS]. */

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[USER] failure="); (void)serial_write(why);
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
static void hex(cpu_u64 v)
{
    char b[19] = "0x0000000000000000";
    for (unsigned int n = 0; n < 16; ++n) b[17 - n] = "0123456789abcdef"[(v >> (n * 4)) & 15];
    text(b);
}
struct accounting { struct pmm_statistics pmm; struct heap_statistics heap; cpu_u64 tables; };
static void worker_main(void *arg);
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
            vm_check(vm_kernel_space()) && heap_check() && user_check(), "resource_balance");
    text("[USER] accounting balanced\r\n");
}

/* Run to a terminal code (exit/fault); yields and preemptions resume. */
static cpu_u64 run_link(struct user_link *link)
{
    cpu_u64 rc = user_enter(link);
    while (rc == USER_RUN_PREEMPTED || rc == USER_RUN_YIELDED) rc = user_resume(link);
    return rc;
}

static void create_evidence(struct user_context *c)
{
    text("[USER] create slot=");
    number(c->slot);
    field(" code_size=", c->code_size);
    field(" tables=", c->table_pages_at_create);
    text("\r\n");
}

static void map_evidence(struct user_context *c)
{    static const struct { cpu_u64 va; unsigned int perm; const char *kind, *ps; } rows[] = {
        {USER_CODE_BASE, VM_USER | VM_EXECUTE, "code", "rx"},
        {USER_DATA_BASE, VM_USER | VM_WRITE, "data", "rw"},
        {USER_STACK_PAGE, VM_USER | VM_WRITE, "stack", "rw"},
    };
    for (unsigned int i = 0; i < 3; ++i) {
        struct vm_mapping m;
        require(vm_query(&c->space, rows[i].va, &m) == VM_OK &&
                m.permissions == rows[i].perm, "map_perm");
        text("[USER] map slot=");
        number(c->slot);
        text(" kind=");
        text(rows[i].kind);
        text(" va=");
        hex(rows[i].va);
        text(" pa=");
        hex(m.physical);
        text(" perm=");
        text(rows[i].ps);
        text("\r\n");
        require(!(m.physical & (VM_PAGE_SIZE - 1)), "map_align");
    }
}

static void lifecycle_tests(void)
{
    struct accounting before = account();
    struct user_context *a = 0, *b = 0, *d = 0;
    require(user_create(&a, USER_BLOB_EXIT) && a, "create_a");
    create_evidence(a);
    map_evidence(a);
    require(user_create(&b, USER_BLOB_EXIT) && b && b != a, "create_b");
    create_evidence(b);
    require(b->table_pages_at_create == a->table_pages_at_create, "tables_equal");
    require(!user_create(&d, USER_BLOB_EXIT) && !d, "admission_full");
    text("[USER] admission rejected slots=2\r\n");
    thread_id junk = ~0ULL;
    require(!thread_create_with_flags(&junk, worker_main, 0, 0x402) &&
            !thread_create_with_flags(&junk, worker_main, 0, 0) &&
            !thread_create_with_flags(&junk, worker_main, 0, 0x200) &&
            junk == ~0ULL, "flags_rejected");
    require(user_check(), "check_full");
    require(user_destroy(a), "destroy_a");
    text("[USER] destroy slot=");
    number(a->slot);
    text("\r\n");
    require(user_create(&d, USER_BLOB_EXIT) && d, "create_reuse");
    create_evidence(d);
    cpu_u64 bslot = b->slot, dslot = d->slot;
    require(user_destroy(b), "destroy_b");
    text("[USER] destroy slot=");
    number(bslot);
    text("\r\n");
    require(user_destroy(d), "destroy_d");
    text("[USER] destroy slot=");
    number(dslot);
    text("\r\n");
    require(!user_destroy(d), "destroy_twice");
    balanced(before);
    text("[USER] lifecycle verified\r\n");
}

static void oom_tests(void)
{
    struct accounting before = account();
    cpu_u64 head = 0, frame = 0;
    while (pmm_allocate(&frame) == PMM_OK) {
        volatile cpu_u64 *p = vm_frame_access(frame);
        require(p != 0, "oom_access");
        *p = head; head = frame;
    }
    struct user_context *x = (void *)1;
    struct accounting drained = account();
    require(!user_create(&x, USER_BLOB_EXIT) && x == (void *)1, "oom_fail_empty");
    balanced(drained);
    for (unsigned int i = 0; i < 5; ++i) {
        require(head != 0, "oom_pool");
        frame = head;
        head = *(volatile cpu_u64 *)vm_frame_access(frame);
        require(pmm_release(frame) == PMM_OK, "oom_return");
    }
    drained = account();
    require(!user_create(&x, USER_BLOB_EXIT), "oom_fail_partial");
    balanced(drained);
    while (head) {
        frame = head;
        head = *(volatile cpu_u64 *)vm_frame_access(frame);
        require(pmm_release(frame) == PMM_OK, "oom_restore");
    }
    balanced(before);
    text("[USER] oom rollback verified\r\n");
}

static void gate_tests(void)
{
    struct accounting before = account();
    struct user_context *c = 0;
    require(user_create(&c, USER_BLOB_EXIT) && c, "exit_create");
    create_evidence(c);
    require(run_link(&c->link) == USER_RUN_EXITED, "exit_code_class");
    require(c->state == USER_EXITED && c->exit_code == 42 && c->gate_exits == 1, "exit_record");
    require(thread_detach_user(), "exit_detach");
    cpu_u64 eslot = c->slot;
    require(user_destroy(c), "exit_destroy");
    text("[USER] destroy slot=");
    number(eslot);
    text("\r\n");
    require(user_create(&c, USER_BLOB_YIELD) && c, "yield_create");
    create_evidence(c);
    require(run_link(&c->link) == USER_RUN_EXITED, "yield_end");
    require(c->state == USER_EXITED && c->exit_code == 9 && c->yields == 1, "yield_record");
    require(thread_detach_user(), "yield_detach");
    cpu_u64 yslot = c->slot;
    require(user_destroy(c), "yield_destroy");
    text("[USER] destroy slot=");
    number(yslot);
    text("\r\n");
    balanced(before);
    text("[USER] gate verified\r\n");
}

static void fault_tests(void)
{
    static const struct {
        enum user_blob blob; cpu_u64 vector, error, cr2, rip; int check_cr2; const char *why;
    } rows[] = {
        {USER_BLOB_UD2, 6, 0, 0, 0, 0, "ud2"},
        {USER_BLOB_READKERN_LO, 14, 0x05, 0x8000, 0, 1, "readkern_lo"},
        {USER_BLOB_READKERN_HI, 14, 0x04, 0xFFFFFFFF80000000ULL, 0, 1, "readkern_hi"},
        {USER_BLOB_WRITE_RX, 14, 0x07, USER_CODE_BASE, 0, 1, "write_rx"},
        /* Fetch faults report the target as RIP: exact data address. */
        {USER_BLOB_EXEC_DATA, 14, 0x15, USER_DATA_BASE, USER_DATA_BASE, 1, "exec_data"},
        {USER_BLOB_CLI, 13, 0, 0, 0, 0, "cli"},
        {USER_BLOB_NULL, 14, 0x04, 0, 0, 1, "null"},
        /* Adversarial boundary probes. Kernel text/data landmarks come
           from the data-page parameters (same addresses the blobs use);
           the test asserts the exact fault CR2, the host asserts the
           supervisor-violation shape. */
        {USER_BLOB_READKERN_TEXT, 14, 0x05, (cpu_u64)&user_enter, 0, 1, "readkern_text"},
        {USER_BLOB_WRITEKERN_DATA, 14, 0x07, (cpu_u64)&user_kernel_cr3, 0, 1, "writekern_data"},
        {USER_BLOB_EXECKERN_TEXT, 14, 0x15, (cpu_u64)&user_enter, (cpu_u64)&user_enter, 1, "execkern_text"},
        {USER_BLOB_EXECSTACK, 14, 0x15, USER_STACK_PAGE, USER_STACK_PAGE, 1, "execstack"},
        {USER_BLOB_READCR3, 13, 0, 0, 0, 0, "readcr3"},
        {USER_BLOB_KERNSEL, 13, 0x10, 0, 0, 0, "kernsel"},
        {USER_BLOB_BADSEL, 13, 0x40, 0, 0, 0, "badsel"},
        /* TI-bit selector with an explicitly invalid LDT: canonical
           #GP(index 3, LDT) before any memory access. */
        {USER_BLOB_TIBIT, 13, 0x1c, 0, 0, 0, "tibit"},
        {USER_BLOB_FARJMP_KCS, 13, 0x08, 0, 0, 0, "farjmp_kcs"},
        {USER_BLOB_FARJMP_UDATA, 13, 0x18, 0, 0, 0, "farjmp_udata"},
        {USER_BLOB_MOVSS, 13, 0x20, 0, 0, 0, "movss"},
        {USER_BLOB_DIVZERO, 0, 0, 0, 0, 0, "divzero"},
        {USER_BLOB_SYSCALL, 6, 0, 0, 0, 0, "syscall_ud"},
        /* No SYSENTER blob: with SYSENTER_CS==0 (enforced in probe)
           hardware raises #GP(0) but QEMU TCG raises #UD, so neither
           fault value is portable truth. Containment is still proven:
           the opcode cannot enter CPL0 either way. */
        /* Stack-pointer corruption: the fault must deliver on the TSS
           exit stack and record a kill, never touch user-controlled
           memory. Silicon raises #SS here; QEMU TCG raises #GP(0) for
           the noncanonical access instead (same containment). The
           pinned value is the emulator-observed one; the property
           under test is delivery + kill, not the vector encoding. */
        {USER_BLOB_SS_RSP, 13, 0, 0, 0, 0, "ss_rsp"},
        /* The fault address is the push target (RSP already decremented),
           one word below the loaded stack pointer. */
        {USER_BLOB_KERN_RSP, 14, 0x07, (cpu_u64)&user_kernel_cr3 - 8, 0, 1, "kern_rsp"},
    };
    struct accounting before = account();
    for (unsigned int i = 0; i < sizeof(rows) / sizeof(rows[0]); ++i) {
        struct user_context *c = 0;
        require(user_create(&c, rows[i].blob) && c, rows[i].why);
        create_evidence(c);
        require(run_link(&c->link) == USER_RUN_FAULTED, rows[i].why);
        require(c->state == USER_FAULTED && c->fault_class == 1 &&
                c->fault_vector == rows[i].vector && c->fault_error == rows[i].error,
                rows[i].why);
        if (rows[i].check_cr2) require(c->fault_cr2 == rows[i].cr2, rows[i].why);
        if (rows[i].rip) require(c->rip == rows[i].rip, rows[i].why);
        else require(c->rip >= USER_CODE_BASE && c->rip < USER_CODE_BASE + c->code_size,
                     rows[i].why);
        require(thread_detach_user(), rows[i].why);
        cpu_u64 fslot = c->slot;
        require(user_destroy(c), rows[i].why);
        text("[USER] destroy slot=");
        number(fslot);
        text("\r\n");
    }
    struct user_context *c = 0;
    require(user_create(&c, USER_BLOB_BADCALL) && c, "badcall_create");
    create_evidence(c);
    require(run_link(&c->link) == USER_RUN_FAULTED, "badcall_class");
    require(c->state == USER_FAULTED && c->fault_class == 2 &&
            c->fault_vector == 128 && c->fault_error == 0x99, "badcall_record");
    require(thread_detach_user(), "badcall_detach");
    cpu_u64 bcslot = c->slot;
    require(user_destroy(c), "badcall_destroy");
    text("[USER] destroy slot=");
    number(bcslot);
    text("\r\n");
    balanced(before);
    text("[USER] faults verified\r\n");
}

static void user_drive(void)
{
    /* Runs on either CR3: kernel-half statics only, no serial/heap/VM. */
    require(irq_in_context(), "drive_context");
}

static volatile int worker_finished;
static cpu_u64 worker_preemptions, worker_code, worker_data_frame, worker_slot;
static struct user_context *worker_ctx_ptr;
static void worker_main(void *arg)
{
    (void)arg;
    /* The worker must never execute kernel code with IF=1: a timer IRQ in
       the entry trampoline's STI-to-entry window would count a
       kernel-origin tick inside the exact-count phase (and rotate the
       victim parity). Created IF=0; the CLI below is belt-and-braces. */
    __asm__ volatile ("cli" ::: "memory");
    struct user_context *c = 0;
    require(user_create(&c, USER_BLOB_SPIN), "w_create");
    worker_slot = c->slot;
    create_evidence(c);
    cpu_u64 rc = user_enter(&c->link);
    while (rc == USER_RUN_PREEMPTED || rc == USER_RUN_YIELDED) rc = user_resume(&c->link);
    require(rc == USER_RUN_EXITED && c->exit_code == 7 && c->state == USER_EXITED, "w_exit");
    worker_preemptions = c->preemptions;
    worker_code = c->code_size;
    worker_data_frame = c->data_frame;
    require(thread_detach_user(), "w_detach");
    /* Leave teardown to bootstrap: it verifies the data frame (spill
       stability) after the join, then destroys. Destroying here would
       release the frame out from under that check. */
    worker_ctx_ptr = c;
    worker_finished = 1;
    thread_exit();
}

static void check_spill(cpu_u64 data_frame, unsigned int slot)
{
    static const cpu_u64 pat[15] = {
        0xF00D000000000000ULL, 0xF00D000000000001ULL, 0xF00D000000000002ULL,
        0xF00D000000000003ULL, 0xF00D000000000004ULL, 0xF00D000000000005ULL,
        0xF00D000000000006ULL, 0xF00D000000000007ULL, 0xF00D000000000008ULL,
        0xF00D000000000009ULL, 0x600000ULL, 0xF00D00000000000bULL,
        0xF00D00000000000cULL, 0xF00D00000000000dULL, 0xF00D00000000000eULL,
    };
    volatile cpu_u64 *d = vm_frame_access(data_frame);
    require(d != 0, "spill_access");
    for (unsigned int i = 0; i < 15; ++i)
        require(d[2 + i] == pat[i], "spill_gprs");
    /* Stop flag must be set (the flag mechanism fired), and the counter
       is timing-dependent so only progress is asserted. Patterns exact. */
    require(d[0] == 1 && d[1] > 0, "spill_count");
    text("[USER] gprs stable slot=");
    number(slot);
    field(" counter=", d[1]);
    text("\r\n");
}

static void preempt_tests(void)
{
    struct accounting before = account();
    struct sched_statistics s0;
    require(scheduler_statistics(&s0), "preempt_stats0");
    struct thread_statistics bstats0;
    require(thread_statistics(thread_current(), &bstats0), "preempt_bstats0");
    require(irq_set_handler(0, user_drive) && irq_set_enabled(0, 1), "drive_start");
    thread_id wid = 0;
    require(thread_create_with_flags(&wid, worker_main, 0, 0x002), "preempt_worker");
    struct user_context *b = 0;
    require(user_create(&b, USER_BLOB_SPIN) && b, "preempt_create");
    create_evidence(b);
    cpu_u64 rc = user_enter(&b->link);
    while (rc == USER_RUN_PREEMPTED || rc == USER_RUN_YIELDED) rc = user_resume(&b->link);
    require(rc == USER_RUN_EXITED && b->exit_code == 7 && b->state == USER_EXITED, "preempt_exit");
    cpu_u64 b_pre = b->preemptions, b_code = b->code_size, b_data = b->data_frame;
    require(thread_detach_user(), "preempt_detach");
    while (thread_ready_count() > 1) require(thread_yield(), "preempt_join");
    struct thread_statistics ws;
    require(thread_statistics(wid, &ws), "preempt_wstats");
    require(worker_finished, "preempt_wdone");
    require(worker_slot == 1 && b->slot == 0, "preempt_slots");
    require(thread_join(wid), "preempt_reap");
    struct sched_statistics s1;
    require(scheduler_statistics(&s1), "preempt_stats1");
    struct thread_statistics bs;
    require(thread_statistics(thread_current(), &bs), "preempt_bstats");
    require(s1.ticks - s0.ticks == USER_PREEMPT_TICKS, "preempt_ticks");
    require(s1.switches - s0.switches == USER_PREEMPT_TICKS + 2, "preempt_switches");
    require(user_cpl3_ticks() == USER_PREEMPT_TICKS, "preempt_cpl3");
    require(b_pre == USER_PREEMPT_TICKS / 2 && worker_preemptions == USER_PREEMPT_TICKS / 2,
            "preempt_split");
    /* 13 tick dispatches plus exactly one exit-path dispatch (each exit
       selects the other thread; order-independent). Preemptions stay 13:
       exits are not ticks. */
    require(ws.preemptions == USER_PREEMPT_TICKS / 2 &&
            ws.dispatches == USER_PREEMPT_TICKS / 2 + 1, "preempt_wsplit");
    /* Bootstrap stats are lifetime-cumulative (earlier phases preempted
       it too): assert phase deltas. The worker thread is phase-born, so
       its raw counts already are deltas. */
    require(bs.preemptions - bstats0.preemptions == USER_PREEMPT_TICKS / 2 &&
            bs.dispatches - bstats0.dispatches == USER_PREEMPT_TICKS / 2 + 1,
            "preempt_bsplit");
    require(bs.irq_rip >= USER_CODE_BASE && bs.irq_rip < USER_CODE_BASE + b_code &&
            ws.irq_rip >= USER_CODE_BASE && ws.irq_rip < USER_CODE_BASE + worker_code,
            "preempt_rip");
    require(bs.irq_rsp > USER_STACK_PAGE && bs.irq_rsp <= USER_STACK_TOP &&
            ws.irq_rsp > USER_STACK_PAGE && ws.irq_rsp <= USER_STACK_TOP, "preempt_rsp");
    text("[USER] preempt slot=");
    number(b->slot);
    field(" preemptions=", b_pre);
    text("\r\n[USER] preempt worker preemptions=");
    number(worker_preemptions);
    text("\r\n[USER] cpl3_ticks=");
    number(user_cpl3_ticks());
    field(" ticks=", s1.ticks - s0.ticks);
    field(" switches=", s1.switches - s0.switches);
    text("\r\n");
    check_spill(b_data, b->slot);
    check_spill(worker_data_frame, (unsigned int)worker_slot);
    require(worker_ctx_ptr && worker_ctx_ptr->state == USER_EXITED, "preempt_wctx");
    cpu_u64 wslot = worker_ctx_ptr->slot;
    require(user_destroy(worker_ctx_ptr), "preempt_wdestroy");
    text("[USER] destroy slot=");
    number(wslot);
    text("\r\n");
    worker_ctx_ptr = 0;
    cpu_u64 bslot = b->slot;
    require(user_destroy(b), "preempt_destroy");
    text("[USER] destroy slot=");
    number(bslot);
    text("\r\n");
    require(io_in8(0x21) == 0xff && pic_in_service() == 0 && cpu_interrupts_disabled(),
            "preempt_quiet");
    balanced(before);
    text("[USER] preemption verified\r\n");
}

void user_self_test(void)
{
    require(cpu_interrupts_disabled() && user_initialize(), "initialize");
    /* Stage banner lives inside the [USER] run (after initialized) so the
       trailing section stays one contiguous run for the host splitter. */
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage18a protected userspace\r\n"
         "[USER] self-test started\r\n");
    lifecycle_tests();
    oom_tests();
    gate_tests();
    fault_tests();
    preempt_tests();
    text("[TEST] userspace self-test passed\r\n[USER] user verified\r\n");
    (void)serial_flush();
}
