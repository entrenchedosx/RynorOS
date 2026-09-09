#ifndef RYNOR_PROC_H
#define RYNOR_PROC_H
#include "cpu.h"
#include "user.h"
#include "uapi.h"
#include "ksched.h"

/* Stage 18d Slice C: bounded process table (shell + two children).
 * One kernel thread per process; no wait queues; no scheduler changes.
 * See docs/design/stage18d-abi.md §§5-8 and kernel/core/proc.c. */

#define PROC_MAX UAPI_MAX_PROCS
/* Owner: kernel/bootstrap (-1) or a live proc slot. Children of a freed
   owner are reparented to PROC_OWNER_KERNEL (ACTIVE ones also killed);
   terminal owned children are freed synchronously with the owner. */
#define PROC_OWNER_KERNEL (-1)

/* Kernel lifecycle (distinct from the UAPI proc_state wire values:
   FREE/LOADING never cross the ABI; ACTIVE maps to PROC_RUNNING). */
enum proc_life {
    PL_FREE,
    PL_LOADING,
    PL_ACTIVE,
    PL_EXITED,
    PL_FAULTED,
    PL_ABORTED
};

struct proc_slot {
    enum proc_life state;
    cpu_u64 gen; /* starts 1; ++ on free, skip 0; never 0 while checked */
    int owner;   /* PROC_OWNER_KERNEL or a proc slot */
    struct user_context *ctx;
    thread_id worker;
    int worker_valid;
    int kill_requested;
    int stdin_closed; /* stdin_sel==STDIN_CLOSED at spawn */
    cpu_u64 exit_code;
    cpu_u64 fault_vector;
    cpu_u64 fault_error;
};

/* Structural invariants (IF=0 foreground). Detects illegal states,
   thread/context mismatches, dangling owners, bad generations,
   double-owned contexts, terminal/free inconsistency, and reserved-VA
   occupation. Fail-closed (returns 0) on any violation. */
int proc_check(void);
/* One-time table init (idempotent; foreground). Safe to call repeatedly. */
int proc_initialize(void);
/* Generation currently on a slot (for evidence; 0 only pre-init). */
cpu_u64 proc_gen(unsigned int slot);
/* Owning proc slot of a user context, or PROC_OWNER_KERNEL. */
int proc_owner_of(struct user_context *c);
/* stdin EOF for read(): nonzero when the caller's process spawned with
   STDIN_CLOSED. No proc entry (probes, bootstrap) means keyboard. */
int proc_stdin_eof(struct user_context *c);
/* Spawn from a kernel-memory image (driver/tests) or staged syscall
   path: validate -> stage -> load -> prepare -> atomic admit -> handle.
   arg_ptrs/arg_lens point into kernel memory (already bounded);
   owner is PROC_OWNER_KERNEL or a non-FREE slot. */
int proc_spawn_image(const cpu_u8 *img, cpu_u64 len,
                     const cpu_u64 *arg_ptrs, const cpu_u64 *arg_lens,
                     cpu_u64 nargs, unsigned int stdin_sel,
                     int owner, cpu_u64 *handle_out);
/* Poll + reap (no blocking): RUNNING never consumes; terminal publishes
   into kern_status, then joins, destroys, frees (consume-once).
   status points into kernel memory. self_slot is the caller's own proc
   slot or -1 (driver): a live self-handle reports RUNNING without
   consuming and without ownership checks (dead selves cannot call). */
int proc_wait(cpu_u64 handle, struct proc_status *status, int owner,
              int self_slot);
/* Request abort: ACTIVE -> kill flag (observed on the worker run loop);
   terminal -> ALREADY_GONE. Never destroys live state here. A caller's
   own live handle is a defined INVAL rejection (never self-destruction),
   checked before ownership. */
int proc_terminate(cpu_u64 handle, int owner, int self_slot);
/* Raw syscall bodies (caller context known for ownership + copy): */
int sys_spawn(struct user_context *caller, cpu_u64 spec_ptr, cpu_u64 handle_out);
int sys_wait(struct user_context *caller, cpu_u64 handle, cpu_u64 status_out);
int sys_terminate(struct user_context *caller, cpu_u64 handle);

#endif
