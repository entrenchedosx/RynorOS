#ifndef RYNOR_KSCHED_H
#define RYNOR_KSCHED_H
#include "cpu.h"
#include "vm.h"

/* Kernel execution infrastructure (Stage 7): per-thread kernel stacks, real
   context switching and a minimal deterministic round-robin scheduler driven by
   the PIT IRQ. Single CPU, ring 0 only. This is the internal kernel scheduler,
   not a user-process or SMP facility. */

/* Per-thread kernel stack virtual region: a dedicated high PML4 index that is
   free (heap uses 384, VM window 510, 509/510/511 reserved). Each thread owns a
   contiguous slot: one faulting guard page (lowest address) below KSTACK_PAGES
   payload pages. The stack grows down from the top of the payload. */
#define KSTACK_PML4_INDEX 448ULL
#define KSTACK_BASE 0xffffe00000000000ULL
#define KSTACK_PAGES 4u            /* 16 KiB payload per thread stack. */
#define KSTACK_GUARD_PAGES 1u
#define KSTACK_SLOT_PAGES (KSTACK_PAGES + KSTACK_GUARD_PAGES)
#define KSTACK_SLOT_SIZE (KSTACK_SLOT_PAGES * VM_PAGE_SIZE)
#define KSTACK_GUARD_BYTES (KSTACK_GUARD_PAGES * VM_PAGE_SIZE)
#define KSTACK_PAYLOAD_BYTES (KSTACK_PAGES * VM_PAGE_SIZE)
/* Offset from slot base to the top (highest, exclusive) address of the payload.
   The initial RSP and the per-thread marker slot are derived from this. */
#define KSTACK_PAYLOAD_TOP_OFFSET (KSTACK_GUARD_BYTES + KSTACK_PAYLOAD_BYTES)
#define KSTACK_MAX_THREADS 8u

/* Unique stack slot index for a payload/base virtual address. */
static inline cpu_u64 kstack_slot(cpu_u64 base)
{
    return (base - KSTACK_BASE) / KSTACK_SLOT_SIZE;
}
static inline cpu_u64 kstack_base_of(cpu_u64 slot)
{
    return KSTACK_BASE + slot * KSTACK_SLOT_SIZE;
}

/* Thread lifecycle states. Single-CPU; current is RUNNING, runnable threads are
   READY, finished-but-not-yet-reaped threads are EXITED (zombie), and slots
   that have no live thread are EMPTY/DEAD. */
enum thread_state {
    THREAD_FREE = 0,     /* slot unused */
    THREAD_READY,        /* runnable, waiting on the run queue */
    THREAD_RUNNING,      /* currently executing */
    THREAD_EXITED        /* finished, awaiting reap/recycle */
};

typedef void (*thread_fn)(void *arg);

/* Kernel stack handle. A guard page (mapped then unmapped so reads fault) sits
   one page below payload. Owns KSTACK_PAGES payload frames + KSTACK_GUARD_PAGES
   guard frames from PMM, mapped RW/NX through the active kernel address space.
   The handle is a trusted kernel object, valid only between kstack_alloc/free. */
struct kstack {
    cpu_u64 slot;          /* slot index within the KSTACK region */
    cpu_u64 base;          /* slot base virtual address (guard page is base) */
    cpu_u64 guard_phys;    /* PMM frame backing the guard page (held while live) */
    cpu_u64 payload_phys[KSTACK_PAGES]; /* payload frames, low to high */
    cpu_u64 magic, magic_inv;
};
#define KSTACK_MAGIC 0x4b535441434bULL
#define KSTACK_MAGIC_INV (~KSTACK_MAGIC)

/* Scheduler public API. All calls require one CPU and IF=0 except those noted.
   IRQ handlers must never allocate or call kstack/thread lifecycle functions. */
int ksymbol_sched_ready(void);
int kstack_alloc(struct kstack *out);
int kstack_free(struct kstack *handle);
int kstack_valid(const struct kstack *handle);

struct thread;
int thread_create(struct thread **out, struct kstack *stack, thread_fn entry,
                  void *arg, struct vm_space *space);
void thread_exit(void) __attribute__((noreturn));
void thread_yield(void);
int thread_join(struct thread *thread, struct vm_space **reclaimed_space);
int thread_ready_count(void);
struct thread *thread_current(void);
int thread_current_stack_base(cpu_u64 *base);

/* Interrupt-context scheduler entry: called from the IRQ0 path while switching.
   Returns the exception frame to resume from. Never returns NULL. */
struct exception_frame *sched_tick(struct exception_frame *frame);

/* Synchronization (single CPU): spinlocks spin on a local flag and are only
   safe when the holder never yields; the irq-save critical section is the
   correct primitive when IRQ context or preemption can interleave. */
typedef volatile struct { int held; } spinlock_t;
#define SPINLOCK_INIT {0}
void spin_lock(spinlock_t *lock);
void spin_unlock(spinlock_t *lock);
struct irq_state { cpu_u64 flags; };
cpu_u64 irq_save(void);          /* CLI + return saved IF bit */
void irq_restore(cpu_u64 flags); /* restore saved IF (STI if it was set) */

/* Bounded guest self-test and integrity check. */
void scheduler_self_test(void);
int scheduler_check(void);
#endif
