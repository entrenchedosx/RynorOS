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
    /* Stage 18d Slice D: frozen endpoint selectors (fd0/fd1 routing).
       Ordinary children: stdin CLOSED/KBD, stdout SERIAL. Pipeline
       producer: stdout PIPE_W; consumer: stdin PIPE_R. Stale while
       FREE; validated for every live slot by proc_check. */
    unsigned int stdin_sel;
    unsigned int stdout_sel;
    cpu_u64 exit_code;
    cpu_u64 fault_vector;
    cpu_u64 fault_error;
};
/* Slot introspection for the pipe layer (pipe_check, syscall routing):
   reports generation, endpoint selectors, and liveness (state != FREE).
   Callers hold IF=0. Returns 0 on bad slot (fail-closed). */
int proc_slot_info(unsigned int slot, cpu_u64 *gen,
                   unsigned int *stdin_sel, unsigned int *stdout_sel,
                   int *live);
/* Gate-side authoritative terminal records (Slice D): called from the
   gate/fault entry path (IF=0, on the exiting thread) immediately after
   the context record is written. Sets PL_EXITED/FAULTED with the
   recorded codes and closes the owned pipe endpoint atomically with the
   context record, so no observer (driver or in-guest waiter) can ever
   see a context-terminal/process-active transient. The worker-side
   record stays as a guarded backstop; the kill path is untouched
   (abort is observed on the worker loop with record+close already
   atomic there). No-ops for contexts outside any process. */
void proc_note_gate_exit(struct user_context *c);
void proc_note_gate_fault(struct user_context *c);

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
int sys_spawn_pipe(struct user_context *caller, cpu_u64 spec_a,
                   cpu_u64 spec_b, cpu_u64 handle_a_out, cpu_u64 handle_b_out);
/* Final executable discovery (Slice D): staged is a NUL-terminated
   kernel buffer with slen=strlen bytes (1..32). Absolute inputs (any
   '/') must pass fs_path_ok and are used as-is; bare names map to
   "/bin/"+name with name<=UAPI_MAX_BIN_NAME (never truncate; longer is
   BADARG) and the construction is re-validated. out is a 33-byte kernel
   buffer receiving the NUL-terminated absolute path. */
int resolve_exec_path(const char *staged, cpu_u64 slen, char *out);
/* Atomic two-child admission (Slice D): resolved absolute executable
   paths plus staged kernel argv vectors admit exactly two ACTIVE
   children sharing one freshly attached pipe ring (A.stdout=PIPE_W,
   B.stdin=PIPE_R; A.stdin selects CLOSED/KBD, B.stdout is SERIAL).
   Images are fetched (heap-staged one at a time, so two maximum-size
   images never coexist) and validated here (NOTFOUND/MALFORMED like
   spawn). All-or-nothing: every pre-commit failure restores slots
   (generations unchanged), contexts, threads, heap, and pipe state
   with zero visible children. */
int proc_spawn_pipe(const char *path_a,
                    const cpu_u64 *arg_ptrs_a, const cpu_u64 *arg_lens_a,
                    cpu_u64 nargs_a, unsigned int stdin_a,
                    const char *path_b,
                    const cpu_u64 *arg_ptrs_b, const cpu_u64 *arg_lens_b,
                    cpu_u64 nargs_b,
                    int owner, cpu_u64 *handle_a_out, cpu_u64 *handle_b_out);

#endif
