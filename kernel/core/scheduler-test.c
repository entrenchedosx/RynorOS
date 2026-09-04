#include "ksched.h"
#include "irq.h"
#include "heap.h"
#include "io.h"
#include "serial.h"

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[SCHED] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
static void text(const char *s) { require(serial_write(s), "serial"); }
static void number(cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    text(b+i);
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
static void noop(void *arg) { (void)arg; }

static void stack_tests(void)
{
    struct accounting before = account();
    struct kstack h[KSTACK_MAX_THREADS] = {{0}}, extra = {0};
    for (unsigned int i = 0; i < KSTACK_MAX_THREADS; ++i) {
        require(kstack_alloc(&h[i]) && kstack_check(&h[i]), "stack_permissions");
        cpu_u64 lo = 0, hi = 0;
        require(kstack_bounds(&h[i], &lo, &hi) && lo == KSTACK_BASE + i*KSTACK_SLOT_SIZE + VM_PAGE_SIZE &&
                hi-lo == KSTACK_PAYLOAD_BYTES, "stack_nonoverlap");
        for (cpu_u64 j = 0; j < KSTACK_PAYLOAD_BYTES/8; ++j) {
            require(((volatile cpu_u64 *)lo)[j] == 0, "stack_zeroed");
            ((volatile cpu_u64 *)lo)[j] = ~0ULL;
        }
    }
    struct accounting full = account();
    thread_id unchanged = ~0ULL;
    require(!kstack_alloc(&extra) && !extra.generation && !thread_create(&unchanged, noop, 0) &&
            unchanged == ~0ULL, "stack_slot_exhaustion");
    balanced(full);
    struct kstack copy = h[0], saved = h[0];
    require(!kstack_free(&copy), "copied_stack_owner");
    h[0].slot = h[1].slot;
    require(!kstack_free(&h[0]), "wrong_stack_slot");
    h[0] = saved;
    h[0].generation ^= 1;
    require(!kstack_free(&h[0]), "wrong_stack_generation");
    h[0] = saved;
    /* Substitute someone else's mapped page: teardown must fail BEFORE mutation. */
    cpu_u64 va = KSTACK_BASE + VM_PAGE_SIZE;
    struct vm_mapping original, other;
    require(vm_query(vm_kernel_space(), va, &original) == VM_OK &&
            vm_query(vm_kernel_space(), va + KSTACK_SLOT_SIZE, &other) == VM_OK &&
            vm_unmap(vm_kernel_space(), va) == VM_OK &&
            vm_map(vm_kernel_space(), va, other.physical, VM_WRITE) == VM_OK, "foreign_mapping_setup");
    require(!kstack_free(&h[0]) && kstack_valid(&h[1]), "foreign_mapping_rejected");
    balanced(full);
    require(vm_unmap(vm_kernel_space(), va) == VM_OK &&
            vm_map(vm_kernel_space(), va, original.physical, VM_WRITE) == VM_OK, "restore_mapping");
    for (unsigned int i = 0; i < KSTACK_MAX_THREADS; ++i) require(kstack_free(&h[i]), "stack_free");
    require(!kstack_free(&h[0]), "stack_double_free");
    require(kstack_alloc(&h[0]) && kstack_check(&h[0]), "stack_reuse");
    struct kstack newer = h[0]; h[0] = saved;
    require(!kstack_free(&h[0]), "stale_stack_generation");
    h[0] = newer;
    for (cpu_u64 i = 0; i < KSTACK_PAYLOAD_BYTES/8; ++i)
        require(((volatile cpu_u64 *)(KSTACK_BASE+VM_PAGE_SIZE))[i] == 0, "reused_stack_zeroed");
    require(kstack_free(&h[0]), "reused_stack_free");
    balanced(before);

    /* Genuine mapping conflict, including the guard: no hidden guard frame. */
    cpu_u64 frame = 0;
    require(pmm_allocate(&frame) == PMM_OK, "conflict_frame");
    const cpu_u64 conflicts[] = {KSTACK_BASE, KSTACK_BASE+KSTACK_PAYLOAD_BYTES};
    for (unsigned int i = 0; i < 2; ++i) {
        require(vm_map(vm_kernel_space(), conflicts[i], frame, VM_WRITE) == VM_OK, "conflict_map");
        *(volatile cpu_u64 *)conflicts[i] = 0x12345;
        struct accounting conflict = account();
        require(!kstack_alloc(&extra) && !extra.generation &&
                *(volatile cpu_u64 *)conflicts[i] == 0x12345, "mapping_conflict");
        balanced(conflict);
        require(vm_unmap(vm_kernel_space(), conflicts[i]) == VM_OK, "conflict_unmap");
    }
    require(pmm_release(frame) == PMM_OK, "conflict_release");
    balanced(before);
    text("[SCHED] stacks ownership, guard mappings and reuse verified\r\n");
}
static void allocation_failure_tests(void)
{
    struct accounting before = account();
    cpu_u64 head = 0, frame = 0;
    enum pmm_result result;
    while ((result = pmm_allocate(&frame)) == PMM_OK) {
        volatile cpu_u64 *p = vm_frame_access(frame);
        require(p != 0, "oom_access"); *p = head; head = frame;
    }
    require(result == PMM_OUT_OF_MEMORY, "oom_exhaust");
    for (unsigned int available = 0; available <= 7; ++available) {
        if (available) {
            require(head != 0, "oom_pool");
            frame = head; head = *(volatile cpu_u64 *)vm_frame_access(frame);
            require(pmm_release(frame) == PMM_OK, "oom_release");
        }
        struct accounting attempt = account();
        struct kstack h = {0};
        if (available < 7) {
            thread_id id = ~0ULL;
            require(!kstack_alloc(&h) && !h.generation &&
                    !thread_create(&id, noop, 0) && id == ~0ULL, "partial_allocation_rollback");
        } else require(kstack_alloc(&h) && kstack_free(&h), "oom_exact_capacity");
        balanced(attempt);
        for (unsigned int i = 0; i <= KSTACK_PAGES; ++i) {
            struct vm_mapping m;
            require(vm_query(vm_kernel_space(), KSTACK_BASE+i*VM_PAGE_SIZE, &m) == VM_NOT_MAPPED,
                    "rollback_no_mappings");
        }
    }
    while (head) {
        frame = head; head = *(volatile cpu_u64 *)vm_frame_access(frame);
        require(pmm_release(frame) == PMM_OK, "oom_restore");
    }
    balanced(before);
    text("[SCHED] real OOM and mapping rollback verified\r\n");
}
static unsigned int completed;
static void cooperative_worker(void *arg)
{
    cpu_u64 f = irq_save();
    require(!thread_join(thread_current()) && !thread_current_stack_base(0), "self_join_null");
    require(thread_yield() && cpu_interrupts_disabled(), "yield_if0");
    irq_restore(f);
    require(thread_yield() && !cpu_interrupts_disabled(), "yield_if1");
    f = irq_save(); ++completed; irq_restore(f);
    (void)arg; /* Returning exercises trampoline -> exit, not an artificial state. */
}
static void lifecycle_tests(void)
{
    struct accounting before = account();
    thread_id stale = 0;
    for (unsigned int round = 0; round < 3; ++round) {
        thread_id ids[SCHED_THREADS-1];
        completed = 0;
        for (unsigned int i = 0; i < SCHED_THREADS-1; ++i)
            require(thread_create(&ids[i], cooperative_worker, 0), "lifecycle_create");
        struct accounting full = account();
        thread_id out = ~0ULL;
        enum thread_state state = THREAD_EXITED;
        require(!thread_create(&out, noop, 0) && out == ~0ULL &&
                !thread_join(ids[0]) && !thread_join(stale) && !thread_join(thread_current()) &&
                !thread_state(stale, &state) && state == THREAD_EXITED, "invalid_lifecycle");
        balanced(full);
        while (completed < SCHED_THREADS-1) require(thread_yield() && cpu_interrupts_disabled(), "lifecycle_yield");
        for (unsigned int i = 0; i < SCHED_THREADS-1; ++i) {
            require(thread_state(ids[i], &state) && state == THREAD_EXITED && thread_join(ids[i]) &&
                    !thread_join(ids[i]), "lifecycle_reap");
        }
        stale = ids[0];
        require(thread_ready_count() == 1 && scheduler_check(), "lifecycle_idle");
        balanced(before);
    }
    require(!thread_create(0, noop, 0) && !thread_create(&stale, 0, 0) && thread_yield(), "invalid_create_idle_yield");
    text("[SCHED] lifecycle exhaustion, stale IDs and repeated reap verified\r\n");
}
static void synchronization_tests(void)
{
    spinlock_t a = SPINLOCK_INIT, b = SPINLOCK_INIT;
    __asm__ volatile ("sti" ::: "memory"); /* PIC remains masked. */
    require(!spin_lock(&a), "lock_requires_if0");
    cpu_u64 outer = irq_save(), inner = irq_save();
    require(outer == 0x200 && inner == 0 && cpu_interrupts_disabled(), "irq_nesting");
    irq_restore(inner); require(cpu_interrupts_disabled(), "irq_inner_restore");
    require(spin_lock(&a) && !spin_lock(&a) && spin_lock(&b), "lock_recursive");
    spinlock_t copy = a;
    require(!spin_unlock(&copy) && !thread_yield() && spin_unlock(&b) &&
            spin_unlock(&a) && !spin_unlock(&a), "lock_ownership_yield");
    irq_restore(outer); require(!cpu_interrupts_disabled(), "irq_outer_restore");
    irq_restore(0); require(cpu_interrupts_disabled(), "irq_restore_zero");
    /* CPU-generated frames/IRETQ for the existing spurious PIC paths, not
       claims of real device IRQ7/15 delivery. No ISR bit means no EOI. */
    require(pic_in_service() == 0, "spurious_isr_before");
    __asm__ volatile ("int $39; int $47" ::: "memory");
    require(cpu_interrupts_disabled() && pic_in_service() == 0 && scheduler_check(),
            "spurious_irq_return");
    text("[SCHED] IRQ nesting and lock contracts verified\r\n");
}

struct probe { volatile cpu_u64 iterations, rsp, error; };
extern void sched_test_loop(volatile cpu_u64 *, struct probe *);
extern char sched_test_loop_begin[], sched_test_loop_end[];
static volatile cpu_u64 stop, test_ticks;
static volatile int irq_memory_context_checked;
static struct probe probes[3];
static void preempt_worker(void *arg)
{
    struct probe *p = arg;
    sched_test_loop(&stop, p); /* no yield, no kernel calls in the measured loop */
    require(!p->error && p->iterations, "register_or_flags_restore");
}
static void test_timer(void)
{
    require(irq_in_context(), "irq_context");
    if (!irq_memory_context_checked) {
        cpu_u64 frame = 0x1122334455667788ULL;
        void *allocation = (void *)0x1234;
        struct vm_space space = {0};
        require(pmm_allocate(&frame) == PMM_WRONG_CONTEXT &&
                frame == 0x1122334455667788ULL &&
                vm_create(&space) == VM_CONTEXT && !space.root && !space.identity &&
                heap_alloc(8, 8, &allocation) == HEAP_CONTEXT &&
                allocation == (void *)0x1234, "irq_memory_context");
        irq_memory_context_checked = 1;
    }
    spinlock_t lock = SPINLOCK_INIT;
    require(spin_lock(&lock) && spin_unlock(&lock), "irq_lock");
    struct kstack h = {0}; thread_id id = 0;
    require(!kstack_alloc(&h) && !thread_create(&id, noop, 0) &&
            !thread_join(thread_current()) && !thread_yield(), "irq_lifecycle_rejected");
    if (++test_ticks == 24) {
        stop = 1;
        require(irq_set_enabled(0, 0), "stop_irq");
    }
}
void scheduler_self_test(void)
{
    require(cpu_interrupts_disabled() && scheduler_initialize() && !scheduler_initialize(), "initialize");
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage7 kernel execution\r\n"
         "[SCHED] self-test started\r\n");
    struct accounting before = account();
    stack_tests(); allocation_failure_tests(); synchronization_tests(); lifecycle_tests();
    thread_id ids[3];
    for (unsigned int i = 0; i < 3; ++i) require(thread_create(&ids[i], preempt_worker, &probes[i]), "preempt_create");
    require(irq_set_handler(0, test_timer) && irq_set_enabled(0, 1), "start_irq");
    text("[SCHED] non-yielding timer probe started\r\n");
    __asm__ volatile ("sti" ::: "memory");
    while (!stop) __asm__ volatile ("pause" ::: "memory");
    __asm__ volatile ("cli" ::: "memory");
    while (thread_ready_count() > 1) require(thread_yield(), "finish_workers");
    struct sched_statistics stats;
    require(scheduler_statistics(&stats) && stats.ticks == 24 && stats.switches == 24 && stats.live == 1 &&
            pic_in_service() == 0 && io_in8(0x21) == 0xff, "timer_preemption");
    cpu_u64 runs = 0;
    for (unsigned int i = 0; i < 3; ++i) {
        struct thread_statistics t;
        require(thread_statistics(ids[i], &t) && t.preemptions >= 2 && t.dispatches >= 2 &&
                t.irq_rsp >= probes[i].rsp-8 && t.irq_rsp <= probes[i].rsp &&
                t.irq_rip >= (cpu_u64)sched_test_loop_begin && t.irq_rip < (cpu_u64)sched_test_loop_end &&
                !probes[i].error && probes[i].iterations, "hardware_preemption_evidence");
        field("[SCHED] worker=", i+1); field(" preemptions=", t.preemptions);
        field(" dispatches=", t.dispatches); field(" rsp=", probes[i].rsp);
        field(" irq_rsp=", t.irq_rsp); field(" irq_rip=", t.irq_rip); text("\r\n");
        for (unsigned int j = 0; j < i; ++j) require(probes[i].rsp != probes[j].rsp, "actual_stacks_distinct");
        runs += probes[i].iterations;
        require(thread_join(ids[i]), "preempt_reap");
    }
    balanced(before);
    /* Exactly two runnable contexts (bootstrap + one non-yielding worker). */
    test_ticks = 0; stop = 0; probes[0] = (struct probe){0};
    require(thread_create(&ids[0], preempt_worker, &probes[0]) && irq_set_enabled(0, 1), "two_runnable_start");
    __asm__ volatile ("sti" ::: "memory");
    while (!stop) __asm__ volatile ("pause" ::: "memory");
    __asm__ volatile ("cli" ::: "memory");
    while (thread_ready_count() > 1) require(thread_yield(), "two_runnable_finish");
    struct sched_statistics two;
    struct thread_statistics worker_stats;
    require(scheduler_statistics(&two) && two.ticks == 48 && two.switches == 48 &&
            thread_statistics(ids[0], &worker_stats) && worker_stats.preemptions == 12 &&
            worker_stats.dispatches == 12 && thread_join(ids[0]), "two_runnable_preemption");
    balanced(before);
    field("[SCHED] two-runnable ticks=", two.ticks-stats.ticks);
    field(" switches=", two.switches-stats.switches); text("\r\n");
    /* One runnable thread: real IRQs return the original frame, no fake switch. */
    test_ticks = 0; stop = 0;
    require(irq_set_enabled(0, 1), "solo_irq");
    while (!stop) __asm__ volatile ("sti; hlt; cli" ::: "memory");
    struct sched_statistics solo;
    require(scheduler_statistics(&solo) && solo.ticks == 72 && solo.switches == two.switches &&
            solo.live == 1 && scheduler_check(), "solo_no_switch");
    balanced(before);
    text("[SCHED] single-runnable timer return verified\r\n");
    field("[SCHED] final allocated_bytes=", before.pmm.allocated_bytes);
    field(" free_bytes=", before.pmm.free_bytes); field(" table_pages=", before.tables); text("\r\n");
    text("[TEST] scheduler self-test passed\r\n");
    field("[TEST] preemptions=", solo.switches); field(" runs=", runs); text("\r\n");
}
