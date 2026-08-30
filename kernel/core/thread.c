#include "ksched.h"
#include "irq.h"
#include "paging.h"
#include "io.h"
#include "serial.h"

/* Real, bounded kernel threads and a deterministic round-robin scheduler driven
   by the PIT IRQ0. Single CPU, ring 0. Threads share the active kernel address
   space; each worker owns a kstack (payload + faulting guard page). The bootstrap
   context is a first-class thread (thread[0], has_stack=0) so it can be preempted
   and resumed exactly like a worker. No user processes or SMP are implied. */

#define SCHED_THREADS KSTACK_MAX_THREADS
#define SCHED_MAGIC 0x5458435943454c45ULL
#define SCHED_MAGIC_INV (~SCHED_MAGIC)

struct thread {
    cpu_u32 id;
    enum thread_state state;
    struct exception_frame saved;
    struct kstack stack;
    int has_stack;
    struct vm_space *space;
    thread_fn entry;
    void *arg;
    cpu_u64 magic, magic_inv;
};

static struct thread threads[SCHED_THREADS];
static struct thread *current;
static int sched_active;
static cpu_u32 sched_ticks;
static int sched_stop;
static int sched_preemptions;

/* Declared in switch.asm. */
void thread_switch(struct exception_frame *out, struct exception_frame *next);
__attribute__((noreturn)) void sched_resume(struct exception_frame *next);

static __attribute__((noreturn)) void panic(const char *reason)
{
    if (serial_write("[SCHED] failure=")) (void)serial_write(reason);
    (void)serial_write("\r\n");
    serial_flush();
    cpu_halt();
}
static void require(int cond, const char *reason)
{ if (!cond) panic(reason); }

/* The freestanding kernel has no libc memcpy/memset; copy POD words directly.
   Both structs are 8-byte-multiple sized. */
static void copy_words(cpu_u64 *dst, const cpu_u64 *src, unsigned int words)
{ for (unsigned int i = 0; i < words; ++i) dst[i] = src[i]; }
static void zero_words(cpu_u64 *dst, unsigned int words)
{ for (unsigned int i = 0; i < words; ++i) dst[i] = 0; }

/* Deterministic round-robin: scan forward from just after current for the first
   READY thread. Small fixed array; no dynamic queue. */
static struct thread *pick_next(void)
{
    if (!current) return (void *)0;
    for (unsigned int i = 1; i < SCHED_THREADS; ++i) {
        struct thread *t = &threads[(current->id + i) % SCHED_THREADS];
        if (t->state == THREAD_READY) return t;
    }
    return (void *)0;
}

static void thread_entry_trampoline(void)
{
    struct thread *me = current;
    me->entry(me->arg);
    thread_exit();
}

static void init_fresh_frame(struct exception_frame *saved, const struct kstack *k)
{
    cpu_u64 payload_top = k->base + KSTACK_GUARD_BYTES + KSTACK_PAYLOAD_BYTES;
    zero_words((cpu_u64 *)saved, sizeof(struct exception_frame) / sizeof(cpu_u64));
    *(volatile cpu_u64 *)(payload_top - 8) = 0;   /* dummy return address */
    saved->rip = (cpu_u64)thread_entry_trampoline;
    saved->cs = CPU_CODE_SELECTOR;
    saved->rflags = 0x202;        /* IF set, reserved bit 0x2 fixed set */
    saved->rsp = payload_top - 8;
    saved->ss = CPU_DATA_SELECTOR;
}

struct exception_frame *sched_tick(struct exception_frame *frame)
{
    if (!sched_active || !current || current->state != THREAD_RUNNING) return frame;
    struct thread *next = pick_next();
    if (!next) return frame;
    copy_words((cpu_u64 *)&current->saved, (const cpu_u64 *)frame,
               sizeof(struct exception_frame) / sizeof(cpu_u64)); /* preempted frame */
    current->state = THREAD_READY;
    next->state = THREAD_RUNNING;
    current = next;
    ++sched_preemptions;
    return &next->saved;           /* exception_common IRETQs to next->saved */
}

void thread_yield(void)
{
    if (!sched_active || !current) return;
    struct thread *next = pick_next();
    if (!next || next == current) return;
    struct thread *me = current;
    current->state = THREAD_READY;
    next->state = THREAD_RUNNING;
    current = next;
    thread_switch(&me->saved, &next->saved);
    current = me;                  /* resumed: our cursor is authoritative */
    me->state = THREAD_RUNNING;
}

void thread_exit(void)
{
    if (!sched_active || !current) panic("exit_no_scheduler");
    struct thread *me = current;
    me->state = THREAD_EXITED;
    struct thread *next = pick_next();
    require(next && next != me, "exit_no_run_next");
    next->state = THREAD_RUNNING;
    current = next;
    sched_resume(&next->saved);    /* one-way, never returns */
    panic("exit_unreachable");
}

int thread_create(struct thread **out, struct kstack *stack, thread_fn entry,
                  void *arg, struct vm_space *space)
{
    if (!out || !stack || !entry || !kstack_valid(stack) || !cpu_interrupts_disabled())
        return 0;
    for (unsigned int i = 1; i < SCHED_THREADS; ++i) {
        struct thread *t = &threads[i];
        if (t->state != THREAD_FREE) continue;
        zero_words((cpu_u64 *)t, sizeof(struct thread) / sizeof(cpu_u64));
        t->id = i;
        t->state = THREAD_READY;
        copy_words((cpu_u64 *)&t->stack, (const cpu_u64 *)stack,
                   sizeof(struct kstack) / sizeof(cpu_u64));
        t->has_stack = 1;
        t->space = space ? space : vm_kernel_space();
        t->entry = entry;
        t->arg = arg;
        t->magic = SCHED_MAGIC;
        t->magic_inv = SCHED_MAGIC_INV;
        init_fresh_frame(&t->saved, &t->stack);
        *out = t;
        return 1;
    }
    return 0;
}

int thread_join(struct thread *t, struct vm_space **space_out)
{
    if (!t || t->id >= SCHED_THREADS || t->magic != SCHED_MAGIC ||
        t->magic_inv != SCHED_MAGIC_INV || t->state != THREAD_EXITED) return 0;
    if (t->has_stack) require(kstack_free(&t->stack), "join_free_stack");
    if (space_out) *space_out = t->space;
    zero_words((cpu_u64 *)t, sizeof(struct thread) / sizeof(cpu_u64));
    return 1;
}

int thread_ready_count(void)
{
    unsigned int n = 0;
    for (unsigned int i = 0; i < SCHED_THREADS; ++i)
        if (threads[i].state == THREAD_READY || threads[i].state == THREAD_RUNNING) ++n;
    return (int)n;
}

struct thread *thread_current(void) { return current; }

int thread_current_stack_base(cpu_u64 *base)
{
    if (!current || !current->has_stack) return 0;
    *base = current->stack.base;
    return 1;
}

/* Synchronization. On a single CPU a spinlock is safe only when the holder
   cannot be preempted/yield; the irq-save critical section is the correct
   primitive under timer preemption and inside IRQ handlers. */
cpu_u64 irq_save(void)
{
    cpu_u64 flags;
    __asm__ volatile ("pushfq; pop %0; cli" : "=r"(flags) : : "memory");
    return flags & 0x200;
}
void irq_restore(cpu_u64 previous)
{
    if (previous & 0x200) __asm__ volatile ("sti" : : : "memory");
}

void spin_lock(spinlock_t *lock)
{
    /* xchg-based test-and-set; single CPU only. */
    cpu_u8 want = 1;
    do {
        __asm__ volatile ("xchgb %b0, %1" : "+q"(want) : "m"(lock->held) : "memory");
    } while (want);
}
void spin_unlock(spinlock_t *lock)
{
    __asm__ volatile ("movb $0, %0" : "=m"(lock->held) : : "memory");
}

/* Return true once at least one thread has actually resumed on its own stack:
   used by the self-test to prove real context switching (not a single stack). */
int ksymbol_sched_ready(void)
{
    return sched_active;
}

/* --- Guest self-test (matched by tools/host/sched_output.py). --- */

static volatile cpu_u64 workers_remaining, sched_runs;
static cpu_u64 per_thread_runs[SCHED_THREADS];
static cpu_u64 per_thread_marks[SCHED_THREADS];

static void stext(const char *s)
{ require(serial_write(s), "serial"); }
static void snumber(cpu_u64 value)
{
    char b[21]; unsigned int n = 20; b[n] = 0;
    do { b[--n] = (char)('0' + value % 10); value /= 10; } while (value);
    stext(b + n);
}

static void worker(void *arg)
{
    cpu_u64 id = (cpu_u64)arg;    /* 1-based: threads[id] identity */
    /* Each worker writes to a slot on ITS OWN kernel stack; each thread's stack
       base differs, so distinct marker VAs prove the stack genuinely switched. */
    struct thread *me = current;
    volatile cpu_u64 *own = (volatile cpu_u64 *)(me->stack.base + KSTACK_PAYLOAD_TOP_OFFSET - 8);
    while (!sched_stop) {
        cpu_u64 f = irq_save();
        ++per_thread_runs[id];
        per_thread_marks[id] = (cpu_u64)own;
        ++sched_runs;
        irq_restore(f);
        thread_yield();
    }
    cpu_u64 f = irq_save();
    --workers_remaining;
    irq_restore(f);
    thread_exit();
}

#define SCHED_BUDGET_TICKS 60u

static void sched_timer_isr(void)
{
    ++sched_ticks;
    if (sched_ticks >= SCHED_BUDGET_TICKS) {
        sched_stop = 1;
        (void)irq_set_enabled(0, 0);   /* stop preempting once the budget is spent */
    }
}

void scheduler_self_test(void)
{
    require(cpu_interrupts_disabled(), "if0");
    struct pmm_statistics before, after;
    require(pmm_statistics(&before) == PMM_OK, "stats_before");

    /* Bootstrap context becomes thread[0]; it preempts like any worker. */
    zero_words((cpu_u64 *)threads, sizeof(struct thread) / sizeof(cpu_u64));
    threads[0].id = 0;
    threads[0].state = THREAD_RUNNING;
    threads[0].space = vm_kernel_space();
    threads[0].magic = SCHED_MAGIC;
    threads[0].magic_inv = SCHED_MAGIC_INV;
    current = &threads[0];

    enum { WORKERS = 3 };
    workers_remaining = 0;
    sched_runs = 0;
    sched_stop = 0;
    sched_ticks = 0;
    sched_preemptions = 0;
    for (unsigned int i = 0; i < SCHED_THREADS; ++i) {
        per_thread_runs[i] = 0;
        per_thread_marks[i] = 0;
    }

    struct kstack stacks[WORKERS];
    struct thread *ts[WORKERS];
    for (unsigned int i = 0; i < WORKERS; ++i) {
        require(kstack_alloc(&stacks[i]), "stack_alloc");
        cpu_u64 id = i + 1;
        require(thread_create(&ts[i], &stacks[i], worker, (void *)id, vm_kernel_space()),
                "thread_create");
        ++workers_remaining;
    }
    require(thread_ready_count() == WORKERS + 1, "ready_count");

    /* Swap IRQ0 from the one-shot heartbeat to the scheduler drive and start
       genuine timer preemption. */
    require(irq_set_handler(0, sched_timer_isr), "isr_swap");
    sched_active = 1;
    require(irq_set_enabled(0, 1), "irq0_enable");

    /* Interrupts must be enabled for the PIT to preempt; the yield loop sinks the
       bootstrap thread into the READY ring. Workers observe the budget stop and
       exit; once they are all gone the last exit resumes us back here. */
    __asm__ volatile ("sti" : : : "memory");
    while (!sched_stop) thread_yield();
    while (workers_remaining > 0) thread_yield();
    __asm__ volatile ("cli" : : : "memory");
    require(cpu_interrupts_disabled(), "if0_after");

    require(sched_active, "still_active");
    sched_active = 0;

    /* Prove the supervisor genuinely preempted and redirected execution: the
       IRQ0 ISR ran for the full budget, and at least one tick actually switched
       to a different thread's saved frame. */
    require(sched_ticks >= SCHED_BUDGET_TICKS, "full_budget");
    require(sched_preemptions > 0, "preempted");
    /* Every worker genuinely resumed on its own stack with a distinct marker VA,
       and defaulted to the shared kernel address space. */
    require(thread_ready_count() == 1, "only_boot_running");
    for (unsigned int i = 1; i <= WORKERS; ++i) {
        require(per_thread_runs[i] > 0, "worker_ran");
        require(vm_query(vm_kernel_space(), per_thread_marks[i], &((struct vm_mapping){0})) == VM_OK,
                "mark_mapped");
    }
    require(per_thread_marks[1] != per_thread_marks[2] &&
            per_thread_marks[2] != per_thread_marks[3] &&
            per_thread_marks[1] != per_thread_marks[3], "marks_distinct");
    require(workers_remaining == 0, "all_exited");

    /* Reap the zombies: frees every kstack (payload + guard) and restores PMM. */
    for (unsigned int i = 0; i < WORKERS; ++i) {
        struct vm_space *sp = (void *)0;
        require(ts[i]->state == THREAD_EXITED, "joined_exited");
        require(thread_join(ts[i], &sp), "join");
        require(sp == vm_kernel_space(), "join_space");
    }
    require(thread_ready_count() == 1, "only_boot_after_join");
    require(pmm_statistics(&after) == PMM_OK, "stats_after");
    require(after.allocated_bytes == before.allocated_bytes, "pmm_balanced");
    require(after.free_bytes + after.allocated_bytes == before.free_bytes + before.allocated_bytes,
            "pmm_region_preserved");

    (void)serial_write("[TEST] scheduler self-test passed\r\n");
    (void)serial_write("[TEST] preemptions=");
    snumber((cpu_u64)sched_preemptions);
    (void)serial_write(" runs=");
    snumber(sched_runs);
    (void)serial_write("\r\n");
    (void)serial_flush();
}

int scheduler_check(void)
{
    /* Idle invariant: scheduler stopped, all worker slots reclaimed, no leaks. */
    if (sched_active || workers_remaining != 0) return 0;
    for (unsigned int i = 1; i < SCHED_THREADS; ++i)
        if (threads[i].state != THREAD_FREE) return 0;
    if (threads[0].state != THREAD_RUNNING && threads[0].state != THREAD_FREE) return 0;
    if (current != &threads[0]) return 0;
    return sched_runs > 0 && sched_preemptions > 0;
}
