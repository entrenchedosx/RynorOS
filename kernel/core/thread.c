#include "ksched.h"
#include "irq.h"
#include "io.h"
#include "serial.h"

/* Single CPU; all transitions are serialized with IF=0. No heap run queue,
   sleeping joins, alternate CR3, or IRQ-time allocation. */
struct thread {
    thread_id id;
    enum thread_state state;
    struct exception_frame saved;
    struct kstack stack;
    thread_fn entry;
    void *arg;
    struct thread_statistics statistics;
    struct user_link *user;
};
static struct thread threads[SCHED_THREADS];
static struct thread *current;
static thread_id next_id = 1;
static int initialized;
static cpu_u64 ticks, switches;
static unsigned int held_locks;
extern char __kernel_stack_start[], __kernel_stack_end[], __text_start[], __text_end[];
extern void thread_switch(struct exception_frame *, struct exception_frame *);

static void panic(const char *why) __attribute__((noreturn));
static void panic(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[SCHED] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
static void require(int ok, const char *why) { if (!ok) panic(why); }
static void frame_failure(const struct exception_frame *f)
{
    const cpu_u64 values[] = {f->rip, f->rsp, f->rflags, f->cs, f->ss, f->vector, f->error};
    const char *labels[] = {" rip=", " rsp=", " flags=", " cs=", " ss=", " vector=", " error="};
    (void)serial_write("[SCHED] rejected frame");
    for (unsigned int i = 0; i < 7; ++i) {
        char hex[19] = "0x0000000000000000";
        for (unsigned int n = 0; n < 16; ++n) hex[17-n] = "0123456789abcdef"[(values[i] >> (n*4)) & 15];
        (void)serial_write(labels[i]); (void)serial_write(hex);
    }
    (void)serial_write("\r\n"); panic("irq_frame");
}
static int foreground(void) { return cpu_interrupts_disabled() && !irq_in_context(); }
static int executable(cpu_u64 rip)
{ return rip >= (cpu_u64)__text_start && rip < (cpu_u64)__text_end; }
static unsigned int current_slot(void)
{
    /* Equality checks before dereference: corrupted current cannot be indexed. */
    for (unsigned int i = 0; i < SCHED_THREADS; ++i) if (current == &threads[i]) return i;
    panic("invalid_current");
}
static int bounds(struct thread *t, cpu_u64 *lo, cpu_u64 *hi)
{
    if (t == &threads[0]) {
        *lo = (cpu_u64)__kernel_stack_start; *hi = (cpu_u64)__kernel_stack_end;
        return 1;
    }
    return kstack_bounds(&t->stack, lo, hi);
}
static int frame_valid(struct thread *t, const struct exception_frame *f)
{
    cpu_u64 lo, hi;
    /* Arithmetic flags, DF, IF and RF are supported; TF/IOPL/NT/VM forbidden.
       Hardware may save RF=1 for an interrupted instruction; preserve it.
       Space for an IRQ frame is required, but this is not an IST overflow fix. */
    return bounds(t, &lo, &hi) && f->rsp >= lo + sizeof(*f) && f->rsp < hi &&
           executable(f->rip) && f->cs == CPU_CODE_SELECTOR && f->ss == CPU_DATA_SELECTOR &&
           (f->rflags & 2) && !(f->rflags & ~0x10ed7ULL) &&
           f->error == 0 && (f->vector == 0 ||
           (f->vector >= IRQ_BASE && f->vector < IRQ_BASE + IRQ_COUNT));
}
int scheduler_check(void)
{
    if (!cpu_interrupts_disabled() || !initialized) return 0;
    unsigned int running = 0;
    int found = 0;
    for (unsigned int i = 0; i < SCHED_THREADS; ++i) {
        struct thread *t = &threads[i];
        if (t == current) found = 1;
        if (t->state < THREAD_FREE || t->state > THREAD_EXITED) return 0;
        if (t->user && (!t->user->bound || !t->user->context)) return 0;
        if (t->state == THREAD_FREE) {
            if (t->id || t->stack.generation) return 0;
            continue;
        }
        if (!t->id || t->id >= next_id || (i && (!kstack_valid(&t->stack) || !executable((cpu_u64)t->entry))))
            return 0;
        for (unsigned int j = 0; j < i; ++j)
            if (threads[j].state != THREAD_FREE && t->id == threads[j].id) return 0;
        if (t->state == THREAD_RUNNING) { ++running; if (t != current) return 0; }
        if (t->state == THREAD_READY && !frame_valid(t, &t->saved)) return 0;
    }
    return found && running == 1 && current->state == THREAD_RUNNING &&
           threads[0].state != THREAD_FREE && threads[0].state != THREAD_EXITED;
}
static void check(void) { require(scheduler_check(), "state_or_context"); }
static struct thread *find(thread_id id)
{
    if (!id) return 0;
    for (unsigned int i = 0; i < SCHED_THREADS; ++i)
        if (threads[i].state != THREAD_FREE && threads[i].id == id) return &threads[i];
    return 0;
}
static struct thread *pick_next(void)
{
    unsigned int slot = current_slot();
    for (unsigned int n = 1; n < SCHED_THREADS; ++n) {
        struct thread *t = &threads[(slot + n) % SCHED_THREADS];
        if (t->state == THREAD_READY) return t;
    }
    return 0;
}
static void select_thread(struct thread *next, int exiting)
{
    require(frame_valid(next, &next->saved), "invalid_resume");
    current->state = exiting ? THREAD_EXITED : THREAD_READY;
    next->state = THREAD_RUNNING;
    current = next;
}
static void thread_entry_trampoline(void)
{
    cpu_u64 flags = irq_save();
    check();
    thread_fn entry = current->entry;
    void *arg = current->arg;
    irq_restore(flags);
    entry(arg);
    thread_exit();
}
int scheduler_initialize(void)
{
    if (!foreground() || initialized || !vm_kernel_space()) return 0;
    threads[0].id = next_id++;
    threads[0].state = THREAD_RUNNING;
    current = &threads[0];
    initialized = 1;
    return scheduler_check();
}
int thread_create(thread_id *out, thread_fn entry, void *arg)
{
    return thread_create_with_flags(out, entry, arg, 0x202);
}
int thread_create_with_flags(thread_id *out, thread_fn entry, void *arg, cpu_u64 rflags)
{
    if (!foreground() || !initialized || !out || !executable((cpu_u64)entry) || next_id >= (1ULL << 63))
        return 0;
    if ((rflags & 2) != 2 || (rflags & ~0x10ed7ULL))
        return 0;
    /* Only plain interruptable/interrupted images; no arithmetic, DF, or
       system bits in a fresh thread. Bit 1 is mandatory (RFLAGS.1). */
    if (rflags != 0x002 && rflags != 0x202) return 0;
    check();
    for (unsigned int i = 1; i < SCHED_THREADS; ++i) {
        struct thread *t = &threads[i];
        if (t->state != THREAD_FREE) continue;
        if (!kstack_alloc(&t->stack)) return 0;
        cpu_u64 lo, hi;
        require(kstack_bounds(&t->stack, &lo, &hi), "new_stack_bounds");
        t->id = next_id++;
        t->entry = entry; t->arg = arg;
        t->saved = (struct exception_frame){0};
        *(cpu_u64 *)(hi - 8) = 0;
        t->saved.rip = (cpu_u64)thread_entry_trampoline;
        t->saved.cs = CPU_CODE_SELECTOR; t->saved.ss = CPU_DATA_SELECTOR;
        t->saved.rsp = hi - 8; t->saved.rflags = rflags;
        t->state = THREAD_READY; /* publish only after successful initialization */
        *out = t->id;
        check();
        return 1;
    }
    return 0;
}
int thread_join(thread_id id)
{
    if (!foreground() || !initialized) return 0;
    check();
    struct thread *t = find(id);
    if (!t || t == current || t == &threads[0] || t->state != THREAD_EXITED) return 0;
    require(kstack_free(&t->stack), "reap_stack");
    *t = (struct thread){0};
    check();
    return 1;
}
int thread_state(thread_id id, enum thread_state *out)
{
    if (!foreground() || !initialized || !out) return 0;
    check();
    struct thread *t = find(id);
    if (!t) return 0;
    *out = t->state; return 1;
}
int thread_statistics(thread_id id, struct thread_statistics *out)
{
    if (!foreground() || !initialized || !out) return 0;
    check();
    struct thread *t = find(id);
    if (!t) return 0;
    *out = t->statistics; return 1;
}
thread_id thread_current(void)
{
    cpu_u64 f = irq_save();
    thread_id id = 0;
    if (initialized) { check(); id = current->id; }
    irq_restore(f); return id;
}
int thread_current_stack_base(cpu_u64 *base)
{
    cpu_u64 f = irq_save(), lo, hi;
    int ok = initialized && base;
    if (ok) {
        check();
        ok = current != &threads[0] && bounds(current, &lo, &hi);
        if (ok) *base = lo - KSTACK_GUARD_BYTES;
    }
    irq_restore(f); return ok;
}
int thread_ready_count(void)
{
    if (!cpu_interrupts_disabled() || !initialized) return 0;
    check();
    int count = 0;
    for (unsigned int i = 0; i < SCHED_THREADS; ++i)
        if (threads[i].state == THREAD_READY || threads[i].state == THREAD_RUNNING) ++count;
    return count;
}
int scheduler_statistics(struct sched_statistics *out)
{
    if (!foreground() || !initialized || !out) return 0;
    check();
    *out = (struct sched_statistics){ticks, switches, (cpu_u64)thread_ready_count()};
    return 1;
}
int thread_yield(void)
{
    cpu_u64 f = irq_save();
    if (!initialized || irq_in_context() || held_locks) { irq_restore(f); return 0; }
    check();
    struct thread *me = current, *next = pick_next();
    if (next) {
        select_thread(next, 0);
        thread_switch(&me->saved, &next->saved);
        /* The selecting path already published current; never overwrite it. */
        require(current == me && cpu_interrupts_disabled(), "yield_return");
        check();
    }
    irq_restore(f);
    return 1;
}
void thread_exit(void)
{
    (void)irq_save();
    require(initialized && !irq_in_context() && !held_locks, "exit_context");
    check();
    require(current != &threads[0], "bootstrap_exit");
    struct thread *next = pick_next();
    require(next != 0, "exit_no_next");
    select_thread(next, 1);
    sched_resume(&next->saved);
}
struct exception_frame *sched_tick(struct exception_frame *frame)
{
    if (!initialized) return frame;
    /* CPL3 origin: record user state (switches to kernel CR3 first),
       park the kernel resume, and run the unchanged selection path.
       The parked frame is frame_valid-compatible by construction. */
    if ((frame->cs & 3) == 3) {
        require(cpu_interrupts_disabled() && irq_in_context() && !held_locks,
                "user_tick_context");
        check();
        struct user_link *link = current->user;
        require(link && link->bound && link->context, "user_tick_link");
        /* Validate before touching anything: the stop-flag store below
           runs on the user CR3 and must only hit the current data page. */
        if (!user_origin_ok(link->context, frame)) frame_failure(frame);
        ++ticks;
        /* Tick accounting still runs for loaded programs (tickspin delta),
           but the stop-flag store is 18a-only: offset 0 of the data page
           is program data for RYNX images, not USER_DATA_STOP. Storing
           there would corrupt the program; loaded code exits via its own
           syscalls and needs preemption only, never the flag. */
        int flag_tick = user_note_cpl3_tick();
        if (!link->context->loaded && flag_tick &&
            !user_publish_stop(link->context, frame))
            frame_failure(frame);
        if (!user_save_state(link->context, frame)) frame_failure(frame);
        struct thread *next = pick_next();
        link->kern_save.rax = USER_RUN_PREEMPTED;
        current->saved = link->kern_save;
        ++current->statistics.preemptions;
        current->statistics.irq_rsp = frame->rsp;
        current->statistics.irq_rip = frame->rip;
        if (!next) return &current->saved;
        ++next->statistics.dispatches;
        select_thread(next, 0);
        ++switches;
        return &next->saved;
    }
    require(cpu_interrupts_disabled() && irq_in_context() && !held_locks, "tick_context");
    check();
    cpu_u64 lo, hi;
    require(bounds(current, &lo, &hi) && (cpu_u64)frame >= lo &&
            (cpu_u64)frame <= hi - sizeof(*frame), "irq_frame_owner");
    if (!frame_valid(current, frame) || !(frame->rflags & 0x200)) frame_failure(frame);
    ++ticks;
    struct thread *next = pick_next();
    if (!next) return frame;
    current->saved = *frame;
    ++current->statistics.preemptions;
    current->statistics.irq_rsp = frame->rsp;
    current->statistics.irq_rip = frame->rip;
    ++next->statistics.dispatches;
    select_thread(next, 0);
    ++switches;
    return &next->saved;
}
struct user_link *thread_user_link(void)
{
    if (!cpu_interrupts_disabled() || !initialized) return 0;
    return current ? current->user : 0;
}
int thread_attach_user(struct user_link *link)
{
    if (!foreground() || !initialized || !link || link->bound)
        return 0;
    /* Only a pristine ACTIVE context may bind: pinning a terminal or
       foreign record would leak the slot (destroy refuses bound links)
       and let stale state linger on a thread. Mirrors enter_valid. */
    struct user_context *c = link->context;
    if (!c || &c->link != link || c->state != USER_ACTIVE ||
        !c->space.root || c->space.identity != &c->space)
        return 0;
    check();
    if (current->user) return 0;
    link->bound = 1;
    current->user = link;
    check();
    return 1;
}
int thread_detach_user(void)
{
    if (!foreground() || !initialized || !current->user) return 0;
    check();
    current->user->bound = 0;
    current->user = 0;
    check();
    return 1;
}
struct exception_frame *user_schedule_next(struct user_link *link, cpu_u64 retcode)
{
    require(cpu_interrupts_disabled() && !irq_in_context(), "user_next_context");
    require(initialized, "user_next_init");
    check();
    require(link && current->user == link && link->bound && link->context,
            "user_next_link");
    require(retcode >= USER_RUN_EXITED && retcode <= USER_RUN_READ, "user_next_code");
    link->kern_save.rax = retcode;
    current->saved = link->kern_save;
    struct thread *next = pick_next();
    if (!next) return &current->saved;
    ++next->statistics.dispatches;
    select_thread(next, 0);
    ++switches;
    return &next->saved;
}
/* Monotonic CPL3-park evidence for the Slice A input tests. */
static cpu_u64 cpl3_parks;
cpu_u64 sched_park_count(void) { return cpl3_parks; }
struct exception_frame *sched_park_cpl3(struct exception_frame *frame)
{
    /* Kernel-mode frames resume untouched (same as the tick path's
       non-CPL3 branch shape): only CPL3 frames need parking. */
    if ((frame->cs & 3) != 3) return frame;
    require(cpu_interrupts_disabled() && irq_in_context() && !held_locks,
            "park_context");
    check();
    struct user_link *link = current->user;
    require(link && link->bound && link->context, "park_link");
    /* Audited validation + record + CR3 switch, exactly like the tick
       CPL3 branch (user.c): all pure checks run pre-switch, so a
       desynced frame fails here with the entry stack still valid. */
    if (!user_save_state(link->context, frame)) {
        frame_failure(frame);
    }
    /* Park through kern_save like a tick that selected the current
       thread (user_entry.asm: return path re-enters via user_resume):
       no tick accounting, no stop-flag store, no thread selection.
       Deliberately mirrors the tick tail without touching it. */
    link->kern_save.rax = USER_RUN_PREEMPTED;
    current->saved = link->kern_save;
    ++current->statistics.preemptions;
    current->statistics.irq_rsp = frame->rsp;
    current->statistics.irq_rip = frame->rip;
    if (cpl3_parks == ~0ULL) cpu_halt();
    ++cpl3_parks;
    return &current->saved;
}
struct exception_frame *sched_handoff(struct exception_frame *original, struct exception_frame *selected)
{
    require(cpu_interrupts_disabled() && !irq_in_context(), "handoff_context");
    if (!initialized) { require(selected == original, "handoff_pointer"); return selected; }
    check();
    require(selected == original || selected == &current->saved, "handoff_pointer");
    cpu_u64 lo, hi;
    if (selected == original)
        require(bounds(current, &lo, &hi) && (cpu_u64)original >= lo &&
                (cpu_u64)original <= hi - sizeof(*original), "handoff_stack");
    require(frame_valid(current, selected), "handoff_frame");
    return selected;
}

cpu_u64 irq_save(void)
{
    cpu_u64 flags;
    __asm__ volatile ("pushfq; pop %0; cli" : "=r"(flags) :: "memory");
    return flags & 0x200;
}
void irq_restore(cpu_u64 flags)
{
    require(!(flags & 0x200) || !held_locks, "restore_with_lock");
    /* IRQ context must never enable interrupts inside dispatch; a nested
       delivery would corrupt the in-service/EOI protocol. */
    require(!(flags & 0x200) || !irq_in_context(), "restore_in_irq");
    if (flags & 0x200) __asm__ volatile ("sti" ::: "memory");
    else __asm__ volatile ("cli" ::: "memory");
}
static cpu_u64 lock_owner(void)
{ return (initialized ? current->id : 1) | (irq_in_context() ? 1ULL << 63 : 0); }
int spin_lock(spinlock_t *lock)
{
    if (!cpu_interrupts_disabled() || !lock || lock->owner) return 0;
    if (initialized) check();
    lock->owner = lock_owner(); lock->identity = lock;
    ++held_locks;
    __asm__ volatile ("" ::: "memory");
    return 1;
}
int spin_unlock(spinlock_t *lock)
{
    if (!cpu_interrupts_disabled() || !lock || !held_locks ||
        lock->identity != lock || lock->owner != lock_owner()) return 0;
    __asm__ volatile ("" ::: "memory");
    lock->owner = 0; lock->identity = 0; --held_locks;
    return 1;
}
