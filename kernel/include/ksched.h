#ifndef RYNOR_KSCHED_H
#define RYNOR_KSCHED_H
#include "cpu.h"
#include "vm.h"
#include "user.h"

#define KSTACK_BASE 0xffffe00000000000ULL /* PML4 slot 448 */
#define KSTACK_PAGES 4u
#define KSTACK_GUARD_BYTES VM_PAGE_SIZE
#define KSTACK_PAYLOAD_BYTES (KSTACK_PAGES * VM_PAGE_SIZE)
#define KSTACK_SLOT_SIZE (KSTACK_GUARD_BYTES + KSTACK_PAYLOAD_BYTES)
#define KSTACK_MAX_THREADS 8u
#define SCHED_THREADS 8u /* bootstrap plus seven workers */

/* Immovable, zero-initialized handle. Frames live only in the private registry.
   A copied/stale handle is not an owner. All operations require IF=0.
   Allocation, free and full mapping checks also require foreground context;
   registry-only valid/bounds queries are IRQ-safe. Freeing the executing stack
   is rejected. */
struct kstack { cpu_u64 slot, generation; };
int kstack_alloc(struct kstack *out);
int kstack_free(struct kstack *handle);
int kstack_valid(const struct kstack *handle);
int kstack_bounds(const struct kstack *handle, cpu_u64 *low, cpu_u64 *high);
int kstack_check(const struct kstack *handle);

enum thread_state { THREAD_FREE, THREAD_READY, THREAD_RUNNING, THREAD_EXITED };
typedef void (*thread_fn)(void *arg);
/* Value ID, not a pointer to a recycled slot. Zero is invalid; never reused. */
typedef cpu_u64 thread_id;
int scheduler_initialize(void); /* foreground IF=0, once, after VM and IRQ setup */
int scheduler_check(void);      /* live structural invariants, IF=0 */
int thread_create(thread_id *out, thread_fn entry, void *arg);
int thread_join(thread_id id);  /* nonblocking reap of an EXITED worker */
/* thread_create with an explicit initial RFLAGS image. Only 0x002 (IF=0,
   for threads that must never take an interrupt in kernel code, e.g. the
   userspace test worker) and 0x202 (default) are accepted; anything else
   fails closed. The image must still satisfy frame_valid. */
int thread_create_with_flags(thread_id *out, thread_fn entry, void *arg, cpu_u64 rflags);
int thread_state(thread_id id, enum thread_state *out);
thread_id thread_current(void);
int thread_current_stack_base(cpu_u64 *base);
int thread_ready_count(void);
int thread_yield(void); /* saves/restores caller IF; rejects IRQ/lock context */
void thread_exit(void) __attribute__((noreturn)); /* never frees its live stack */

struct sched_statistics { cpu_u64 ticks, switches, live; };
struct thread_statistics { cpu_u64 preemptions, dispatches, irq_rsp, irq_rip; };
int thread_statistics(thread_id id, struct thread_statistics *out);
int scheduler_statistics(struct sched_statistics *out);
struct exception_frame *sched_tick(struct exception_frame *frame);
/* Stage 18d Slice A: park a non-timer IRQ that interrupted CPL3. Kernel
   frames pass through unchanged. CPL3 frames are validated and recorded
   with the audited user_save_state (pre-switch, fail-closed), parked in
   the thread's kern_save with USER_RUN_PREEMPTED, and returned through
   the normal handoff machinery. No tick accounting, no stop-flag store,
   no thread selection: the current thread resumes (IRQ1 is a preemption
   point, not a scheduling point, until a later slice needs otherwise).
   IRQ context required (same as sched_tick). */
struct exception_frame *sched_park_cpl3(struct exception_frame *frame);
/* Number of CPL3 parks performed (test evidence; monotonic). */
cpu_u64 sched_park_count(void);
/* Last C validation before assembly changes RSP; unknown pointers never read. */
struct exception_frame *sched_handoff(struct exception_frame *original,
                                       struct exception_frame *selected);
/* One-way resume used by userspace gate/fault exits (noreturn). */
void sched_resume(struct exception_frame *next) __attribute__((noreturn));
/* Userspace binding: the current thread's link, or 0. IF=0 required. */
struct user_link *thread_user_link(void);
int thread_attach_user(struct user_link *link); /* foreground, once */
int thread_detach_user(void);                   /* foreground, bound only */
/* Record kern_save with retcode, select next, return its frame. The
   caller finishes with sched_resume. Never IRQ context. */
struct exception_frame *user_schedule_next(struct user_link *link, cpu_u64 retcode);

/* Single CPU. irq_restore restores IF exactly, not arithmetic flags. Locks
   require IF=0; contention/recursive lock/wrong unlock fail, never spin forever.
   Yield/exit are forbidden with any held lock. IRQ context cannot yield/reap. */
cpu_u64 irq_save(void);
void irq_restore(cpu_u64 flags);
typedef struct { cpu_u64 owner; void *identity; } spinlock_t;
#define SPINLOCK_INIT {0}
int spin_lock(spinlock_t *lock);
int spin_unlock(spinlock_t *lock);

void scheduler_self_test(void);
#endif
