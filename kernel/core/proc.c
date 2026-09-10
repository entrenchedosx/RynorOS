#include "proc.h"
#include "load.h"
#include "fs.h"
#include "pipe.h"
#include "serial.h"
#include "vm.h"
#include "pmm.h"
#include "heap.h"
#include "irq.h"
#include "io.h"

/* Cross-unit helpers (declared in their own headers): */
extern int fs_path_ok(const char *path);
extern cpu_u64 copy_dest_ok(struct user_context *c, cpu_u64 uaddr, cpu_u64 len);

/* Stage 18d Slice C: bounded process table, spawn/wait/terminate.
 *
 * Model: one kernel thread per process. The spawner (driver or syscall,
 * always IF=0 foreground, hence atomically with respect to scheduling)
 * validates, stages, loads, prepares argv, creates the worker, admits,
 * and publishes the handle LAST. The worker attaches, enters via
 * user_enter_image (argv RSP preserved), and runs to a terminal state;
 * kill requests are observed on that loop only (no tick-path change, no
 * cross-thread destruction). wait() polls (RUNNING never consumes) and
 * reaps exactly once (join with bounded yields, destroy, free, gen++).
 * See docs/design/stage18d-abi.md §§5-8.
 */

static struct proc_slot slots[PROC_MAX];
static int proc_ready;

static void panic(const char *why) __attribute__((noreturn));
static void panic(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[PROC] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}
static void require(int ok, const char *why) { if (!ok) panic(why); }
static int foreground(void) { return cpu_interrupts_disabled() && !irq_in_context(); }

static void ensure_init(void)
{
    if (proc_ready) return;
    for (unsigned int i = 0; i < PROC_MAX; ++i) {
        slots[i].state = PL_FREE;
        slots[i].gen = 1;
        slots[i].owner = PROC_OWNER_KERNEL;
    }
    proc_ready = 1;
}

cpu_u64 proc_gen(unsigned int slot)
{
    if (slot >= PROC_MAX) return 0;
    return slots[slot].gen;
}

int proc_owner_of(struct user_context *c)
{
    if (!proc_ready || !c) return PROC_OWNER_KERNEL;
    for (unsigned int i = 0; i < PROC_MAX; ++i)
        if (slots[i].state != PL_FREE && slots[i].ctx == c) return (int)i;
    return PROC_OWNER_KERNEL;
}

int proc_stdin_eof(struct user_context *c)
{
    int owner;
    if (!c) return 0;
    owner = proc_owner_of(c);
    if (owner == PROC_OWNER_KERNEL) return 0;
    if (owner < 0 || owner >= (int)PROC_MAX) return 0;
    return slots[(unsigned int)owner].stdin_sel == STDIN_CLOSED;
}

int proc_slot_info(unsigned int slot, cpu_u64 *gen,
                   unsigned int *stdin_sel, unsigned int *stdout_sel,
                   int *live)
{
    if (slot >= PROC_MAX || !gen || !stdin_sel || !stdout_sel || !live)
        return 0;
    if (!proc_ready) return 0;
    *gen = slots[slot].gen;
    *stdin_sel = slots[slot].stdin_sel;
    *stdout_sel = slots[slot].stdout_sel;
    *live = (slots[slot].state != PL_FREE);
    return 1;
}

/* Final program discovery (Slice D): absolute paths pass through the
 * audited fs_path_ok rule; bare names resolve under /bin/ with the
 * frozen 27-byte bound (5 + 27 = 32 = FS_MAX_PATH). No PATH, no cwd, no
 * suffix guessing, no enumeration, never truncate. */
int resolve_exec_path(const char *staged, cpu_u64 slen, char *out)
{
    cpu_u64 i, eff = 0;
    int bare = 1;
    static const char prefix[] = "/bin/";
    if (!staged || !out) return SYS_BADARG;
    if (slen < 1 || slen > FS_MAX_PATH) return SYS_BADARG;
    /* Effective length stops at the first interior NUL (the same bytes
       the filesystem layer will resolve: validated == used). */
    while (eff < slen && staged[eff]) ++eff;
    if (eff < 1) return SYS_BADARG;
    for (i = 0; i < eff; ++i)
        if (staged[i] == '/') {
            bare = 0;
            break;
        }
    if (!bare) {
        if (!fs_path_ok(staged)) return SYS_BADARG;
        for (i = 0; i < eff; ++i) out[i] = staged[i];
        out[eff] = 0;
        return SYS_OK;
    }
    if (eff > UAPI_MAX_BIN_NAME) return SYS_BADARG;
    for (i = 0; i < 5; ++i) out[i] = prefix[i];
    for (i = 0; i < eff; ++i) out[5 + i] = staged[i];
    out[5 + eff] = 0;
    if (!fs_path_ok(out)) return SYS_BADARG;
    return SYS_OK;
}

/* Reserved future regions must never hold user mappings (map commitment
   without growth APIs): heap [0x610000,0x700000), arena [0x700000,0x7F0000). */
#define RESV_LO 0x610000ULL
#define RESV_HI 0x7F0000ULL
static int reserved_clear(struct user_context *c)
{
    for (cpu_u64 va = RESV_LO; va < RESV_HI; va += VM_PAGE_SIZE) {
        struct vm_mapping m;
        if (vm_query(&c->space, va, &m) != VM_NOT_MAPPED) return 0;
    }
    return 1;
}

int proc_check(void)
{
    if (!cpu_interrupts_disabled() || !proc_ready) return 0;
    for (unsigned int i = 0; i < PROC_MAX; ++i) {
        struct proc_slot *s = &slots[i];
        if (s->gen == 0) return 0;
        if (s->owner != PROC_OWNER_KERNEL &&
            (s->owner < 0 || s->owner >= (int)PROC_MAX ||
             slots[(unsigned int)s->owner].state == PL_FREE))
            return 0;
        if (s->state == PL_FREE) {
            if (s->ctx || s->worker_valid || s->kill_requested) return 0;
            continue;
        }
        if (!s->ctx) return 0;
        /* No double-owned user contexts. */
        for (unsigned int j = 0; j < i; ++j)
            if (slots[j].state != PL_FREE && slots[j].ctx == s->ctx) return 0;
        /* Frozen endpoint domain (Slice D): FILE selectors are never
           admitted; single spawn takes CLOSED/KBD+SERIAL, spawn_pipe
           adds PIPE_W producers and PIPE_R consumers. */
        if (s->stdin_sel != STDIN_CLOSED && s->stdin_sel != STDIN_KBD &&
            s->stdin_sel != STDIN_PIPE)
            return 0;
        if (s->stdout_sel != STDOUT_SERIAL && s->stdout_sel != STDOUT_PIPE)
            return 0;
        if (s->state == PL_LOADING) {
            if (s->worker_valid || s->kill_requested) return 0;
            continue;
        }
        if (s->state != PL_ACTIVE && s->state != PL_EXITED &&
            s->state != PL_FAULTED && s->state != PL_ABORTED)
            return 0;
        if (!s->worker_valid) return 0;
        if (s->state == PL_ACTIVE) {
            if (s->ctx->state != USER_ACTIVE) return 0;
        } else if (s->state == PL_EXITED) {
            if (s->ctx->state != USER_EXITED) return 0;
        } else if (s->state == PL_FAULTED) {
            if (s->ctx->state != USER_FAULTED) return 0;
        } else {
            if (s->ctx->state != USER_ABORTED) return 0;
        }
        if (!reserved_clear(s->ctx)) return 0;
    }
    return 1;
}

/* Decode a handle: slot bounds, nonzero generation, generation equality.
   Anything else is BADHANDLE (stale, fabricated, consumed, cross-type). */
static int decode(cpu_u64 handle, unsigned int *slot_out)
{
    cpu_u64 slot, gen;
    if (!proc_ready || !slot_out) return 0;
    slot = handle & 0xFFFFFFFFULL;
    gen = handle >> 32;
    if (slot >= PROC_MAX || gen == 0) return 0;
    if (slots[slot].state == PL_FREE) return 0;
    if (slots[slot].gen != gen) return 0;
    *slot_out = (unsigned int)slot;
    return 1;
}

/* Join a worker, yielding so it can run to its exit. Workers always
   exit after recording terminal state (detach + return, no blocking),
   so this converges; the bound fails closed on corruption. */
static int join_worker(struct proc_slot *s)
{
    unsigned int i;
    if (!s->worker_valid) return 0;
    for (i = 0; i < 1000000u; ++i) {
        if (thread_join(s->worker)) {
            s->worker_valid = 0;
            /* Join freed the kstack (high-half change): re-sync before
               any later comparison. */
            if (!user_sync_spaces()) panic("join_sync");
            return 1;
        }
        if (!thread_yield()) return 0;
    }
    return 0;
}

/* Free a terminal slot whose worker is already joined: first release
   everything it owns (recursively, depth-bounded by PROC_MAX), then
   destroy and recycle the generation. */
static int free_joined(unsigned int slot); /* fwd: recursion via cleanup */
static int cleanup_owned(int owner);       /* fwd */
static int free_joined(unsigned int slot)
{
    struct proc_slot *s = &slots[slot];
    if (!cleanup_owned((int)slot)) return 0;
    if (!user_destroy(s->ctx)) return 0;
    s->ctx = 0;
    s->state = PL_FREE;
    s->worker_valid = 0;
    s->kill_requested = 0;
    s->owner = PROC_OWNER_KERNEL;
    s->exit_code = 0;
    s->fault_vector = 0;
    s->fault_error = 0;
    if (++s->gen == 0) ++s->gen;
    return 1;
}

/* Reap one owned-terminal child: join worker, destroy, free. */
static int reap_child(unsigned int slot)
{
    struct proc_slot *k = &slots[slot];
    if (!join_worker(k)) return 0;
    return free_joined(slot);
}

/* Owner cleanup for a slot being freed: terminal owned children are
   reaped (reap_child recurses through free_joined, so depth is bounded
   by PROC_MAX); live ones are reparented to the kernel with a kill
   request (no orphans: they abort asynchronously and stay waitable by
   the kernel owner). */
static int cleanup_owned(int owner)
{
    for (unsigned int i = 0; i < PROC_MAX; ++i) {
        struct proc_slot *k = &slots[i];
        if (k->state == PL_FREE || k->owner != owner) continue;
        if (k->state == PL_ACTIVE || k->state == PL_LOADING) {
            k->owner = PROC_OWNER_KERNEL;
            k->kill_requested = 1;
        } else {
            if (!reap_child(i)) return 0;
        }
    }
    for (unsigned int i = 0; i < PROC_MAX; ++i)
        if (slots[i].state != PL_FREE && slots[i].owner == owner) return 0;
    return 1;
}

/* Gate-side terminal record shared by exit and fault kills. The gate
   already established c->state/codes; publish the process record and
   close the pipe endpoint here, atomically (same IF=0 section). */
static void note_gate_terminal(struct user_context *c, enum proc_life terminal)
{
    int owner;
    unsigned int slot;
    struct proc_slot *s;
    if (!foreground() || !c) panic("gate_note");
    owner = proc_owner_of(c);
    if (owner == PROC_OWNER_KERNEL) return;
    if (owner < 0 || owner >= (int)PROC_MAX) panic("gate_note");
    slot = (unsigned int)owner;
    s = &slots[slot];
    /* The gate fires only for live contexts of ACTIVE slots (entry
       panics otherwise, before this point). Anything else is kernel
       desync, never a hostile program. */
    if (s->ctx != c || s->state != PL_ACTIVE) panic("gate_note");
    s->state = terminal;
    if (terminal == PL_EXITED) {
        s->exit_code = c->exit_code;
    } else {
        s->fault_vector = c->fault_vector;
        s->fault_error = c->fault_error;
    }
    if (!pipe_note_terminal(slot, s->gen)) panic("gate_pipe");
}

void proc_note_gate_exit(struct user_context *c)
{
    ensure_init();
    note_gate_terminal(c, PL_EXITED);
}

void proc_note_gate_fault(struct user_context *c)
{
    ensure_init();
    note_gate_terminal(c, PL_FAULTED);
}

/* Child worker: attach, enter via the image path (argv RSP preserved),
   run to a terminal state, detach, return (the entry trampoline then
   performs thread_exit). Kill requests are observed at the top of every
   iteration, so even a non-yielding spinner aborts within one tick: the
   IRQ0 tick parks it back here with PREEMPTED. Terminal records written
   here are authoritative unless the gate already recorded one first
   (single CPU: the two can never interleave; the gate wins by order). */
static void proc_worker(void *arg)
{
    unsigned int slot = (unsigned int)(cpu_u64)arg;
    struct proc_slot *s;
    struct user_context *c;
    cpu_u64 rc;
    if (slot >= PROC_MAX) panic("worker_slot");
    if (!foreground()) panic("worker_fg");
    s = &slots[slot];
    if (s->state != PL_ACTIVE || !s->worker_valid || !s->ctx) panic("worker_state");
    c = s->ctx;
    if (c->state != USER_ACTIVE || c->link.bound || c->entries || c->resumes)
        panic("worker_ctx");
    /* user_enter_image attaches (like user_enter); the link must be free. */
    rc = user_enter_image(&c->link);
    for (;;) {
        if (rc == USER_RUN_EXITED) {
            if (s->state == PL_ACTIVE) {
                s->state = PL_EXITED;
                s->exit_code = c->exit_code;
                /* Slice D: execution-terminal closes the owned pipe
                   endpoint now (never at wait/reap): the peer can
                   observe EOF/broken-reader without a wait. */
                if (!pipe_note_terminal(slot, s->gen)) panic("worker_pipe");
            }
            break;
        }
        if (rc == USER_RUN_FAULTED) {
            if (s->state == PL_ACTIVE) {
                s->state = PL_FAULTED;
                s->fault_vector = c->fault_vector;
                s->fault_error = c->fault_error;
                if (!pipe_note_terminal(slot, s->gen)) panic("worker_pipe");
            }
            break;
        }
        /* Kill observed only while the context is still live: a gate
           that already terminated it wins by order (no rewrite). */
        if (s->kill_requested && s->state == PL_ACTIVE &&
            c->state == USER_ACTIVE) {
            if (!user_mark_aborted(c)) panic("worker_abort");
            s->state = PL_ABORTED;
            if (!pipe_note_terminal(slot, s->gen)) panic("worker_pipe");
            break;
        }
        if (rc != USER_RUN_YIELDED && rc != USER_RUN_WRITTEN &&
            rc != USER_RUN_READ && rc != USER_RUN_PREEMPTED &&
            rc != USER_RUN_SPAWNED && rc != USER_RUN_WAITED &&
            rc != USER_RUN_TERMINATED && rc != USER_RUN_FREAD &&
            rc != USER_RUN_SPAWN_PIPE)
            panic("worker_rc");
        rc = user_resume(&c->link);
    }
    if (!thread_detach_user()) panic("worker_detach");
    /* Return: thread_entry_trampoline performs thread_exit into a READY
       waiter (the waiter yields while joining, so a next thread always
       exists by then). */
}

/* Admission: create the user context from validated image bytes, prepare
   argv, create the worker, then flip ACTIVE. Callers hold IF=0
   foreground throughout (syscall handlers and the test driver), so no
   thread can observe or run the slot mid-admission: atomicity holds
   without locks. */
int proc_spawn_image(const cpu_u8 *img, cpu_u64 len,
                     const cpu_u64 *arg_ptrs, const cpu_u64 *arg_lens,
                     cpu_u64 nargs, unsigned int stdin_sel,
                     int owner, cpu_u64 *handle_out)
{
    struct rnyx_layout lay;
    struct user_context *c = 0;
    thread_id worker = 0;
    unsigned int slot;
    unsigned int i;
    if (!foreground() || !proc_ready || !img || !len || !handle_out) return SYS_INVAL;
    if (nargs > UAPI_MAX_ARGC) return SYS_BADARG;
    if (nargs && (!arg_ptrs || !arg_lens)) return SYS_BADARG;
    if (stdin_sel != STDIN_CLOSED && stdin_sel != STDIN_KBD) return SYS_BADARG;
    if (owner != PROC_OWNER_KERNEL &&
        (owner < 0 || owner >= (int)PROC_MAX || slots[(unsigned int)owner].state == PL_FREE))
        return SYS_BADARG;
    if (rnyx_validate(img, len, &lay) != RNYX_OK) return SYS_MALFORMED;
    for (slot = 0; slot < PROC_MAX; ++slot)
        if (slots[slot].state == PL_FREE) break;
    if (slot >= PROC_MAX) return SYS_BUSY;
    slots[slot].state = PL_LOADING;
    if (!user_create_loaded(&c, (const char *)(img + lay.code_off), lay.code_len,
                            (const char *)(img + lay.data_off), lay.data_filesz,
                            lay.data_memsz)) {
        slots[slot].state = PL_FREE;
        return SYS_NOMEM;
    }
    slots[slot].ctx = c;
    /* Argv block is built unconditionally (even for nargs==0, which
       yields the minimal [0][NULL] block): _start loads [RSP], so RSP
       must always address mapped stack memory. */
    if (!user_prepare_argv(c, nargs, arg_ptrs, arg_lens)) {
        require(user_destroy(c), "spawn_argv_destroy");
        slots[slot].ctx = 0;
        slots[slot].state = PL_FREE;
        return SYS_NOMEM;
    }
    if (!user_check()) panic("spawn_precheck_user");
    if (!proc_check()) panic("spawn_precheck_proc");
    /* IF=0 entry image (the 18a worker pattern): the entry trampoline
       restores saved IF before calling us, so a default 0x202 image
       would start this function with interrupts enabled (no foreground).
       CPL3 execution still runs IF=1 via the recorded user frame. */
    if (!thread_create_with_flags(&worker, proc_worker, (void *)(cpu_u64)slot, 0x002)) {
        require(user_destroy(c), "spawn_thread_destroy");
        slots[slot].ctx = 0;
        slots[slot].state = PL_FREE;
        return SYS_NOMEM;
    }
    /* The new kstack changed the high half: re-sync every live snapshot
       before any comparison (admission checks below compare values). */
    if (!user_sync_spaces()) panic("spawn_sync");
    slots[slot].ctx = c;
    slots[slot].worker = worker;
    slots[slot].worker_valid = 1;
    slots[slot].owner = owner;
    slots[slot].kill_requested = 0;
    slots[slot].stdin_sel = stdin_sel;
    slots[slot].stdout_sel = STDOUT_SERIAL;
    slots[slot].exit_code = 0;
    slots[slot].fault_vector = 0;
    slots[slot].fault_error = 0;
    slots[slot].state = PL_ACTIVE;
    if (!user_check() || !proc_check()) panic("spawn_admit");
    for (i = 0; i < PROC_MAX; ++i)
        if (i != slot && slots[i].state != PL_FREE && slots[i].ctx == c)
            panic("spawn_alias");
    *handle_out = (cpu_u64)slot | (slots[slot].gen << 32);
    return SYS_OK;
}

int proc_wait(cpu_u64 handle, struct proc_status *status, int owner,
              int self_slot)
{
    unsigned int slot;
    struct proc_slot *s;
    if (!foreground() || !proc_ready || !status) return SYS_INVAL;
    if (!decode(handle, &slot)) return SYS_BADHANDLE;
    s = &slots[slot];
    /* Live self-handle: report RUNNING without consuming and without
       ownership checks (a running caller is trivially alive). */
    if (self_slot >= 0 && slot == (unsigned int)self_slot) {
        if (s->state == PL_ACTIVE || s->state == PL_LOADING) {
            *status = (struct proc_status){PROC_RUNNING, 0, 0, 0};
            return SYS_OK;
        }
        return SYS_BADHANDLE;
    }
    if (s->owner != owner) return SYS_BADHANDLE;
    if (s->state == PL_ACTIVE || s->state == PL_LOADING) {
        *status = (struct proc_status){PROC_RUNNING, 0, 0, 0};
        return SYS_OK;
    }
    /* Reap only fully exited workers: publishing terminal while the
       worker thread is still parked would let join yield on a gate
       exit stack (in-guest waits), parking a scheduler-invalid frame.
       RUNNING here means "recorded but not yet reaped"; the next poll
       consumes once the worker breaks and exits (one quantum away, and
       every waiter yields). Join therefore never yields in practice;
       its loop stays as a backstop. */
    {
        enum thread_state tst = THREAD_FREE;
        if (!thread_state(s->worker, &tst)) panic("wait_tstate");
        if (tst != THREAD_EXITED) {
            *status = (struct proc_status){PROC_RUNNING, 0, 0, 0};
            return SYS_OK;
        }
    }
    if (s->state == PL_EXITED) {
        *status = (struct proc_status){PROC_EXITED, (cpu_u32)s->exit_code, 0, 0};
    } else if (s->state == PL_FAULTED) {
        *status = (struct proc_status){PROC_FAULTED, (cpu_u32)s->fault_vector,
                                       (cpu_u32)s->fault_error, 0};
    } else if (s->state == PL_ABORTED) {
        *status = (struct proc_status){PROC_ABORTED, 0, 0, 0};
    } else {
        return SYS_BADHANDLE;
    }
    /* Consume exactly once: join the worker, destroy the context, then
       free (owned cleanup first, generation last). */
    if (!join_worker(s)) panic("wait_join");
    if (!free_joined(slot)) panic("wait_free");
    if (!user_check() || !proc_check()) panic("wait_check");
    return SYS_OK;
}

int proc_terminate(cpu_u64 handle, int owner, int self_slot)
{
    unsigned int slot;
    struct proc_slot *s;
    if (!foreground() || !proc_ready) return SYS_INVAL;
    if (!decode(handle, &slot)) return SYS_BADHANDLE;
    s = &slots[slot];
    /* Own live handle: defined INVAL rejection (never self-destruction),
       checked before ownership so the rule is caller-relative. */
    if (self_slot >= 0 && slot == (unsigned int)self_slot) return SYS_INVAL;
    if (s->owner != owner) return SYS_BADHANDLE;
    if (s->state != PL_ACTIVE && s->state != PL_LOADING) return SYS_ALREADY_GONE;
    s->kill_requested = 1;
    if (!proc_check()) panic("terminate_check");
    return SYS_OK;
}

int proc_initialize(void)
{
    if (!foreground() || proc_ready) return proc_ready;
    ensure_init();
    return proc_ready && proc_check();
}

/* Syscall staging (single CPU, IF=0 handler/driver context only; never
   shared, never retained across calls — same discipline as the load.c
   write/read stages). */
static cpu_u8 spec_stage[sizeof(struct spawn_spec)];
static cpu_u8 args_stage[UAPI_MAX_ARGC * sizeof(struct user_arg)];
static cpu_u8 path_stage[33];
static cpu_u8 resolved_path[33];
static cpu_u8 resolved_path_b[33];
static cpu_u8 argv_stage[UAPI_MAX_ARGV_BYTES];
/* Slice D: second argv vector for spawn_pipe (both sides stay resident
   through dual validation; the descriptor stage is reused). */
static cpu_u8 argv_stage_b[UAPI_MAX_ARGV_BYTES];
static cpu_u64 argv_ptrs_b[UAPI_MAX_ARGC];
static cpu_u64 argv_lens_b[UAPI_MAX_ARGC];
static cpu_u64 argv_ptrs[UAPI_MAX_ARGC];
static cpu_u64 argv_lens[UAPI_MAX_ARGC];
/* Image staging uses the kernel heap, not .bss: the largest v2 image
   (28 + 32768 + 16384 = 49180 bytes) would overflow the linker's bounded
   BSS window. Allocated and freed within one spawn (balanced). */
#define IMG_STAGE_MAX (28u + 32768u + 16384u)

int sys_spawn(struct user_context *caller, cpu_u64 spec_ptr, cpu_u64 handle_out)
{
    struct spawn_spec spec;
    cpu_u64 cursor = 0;
    cpu_u64 handle = 0;
    cpu_u64 want, got;
    cpu_u32 h = 0;
    struct fs_stat st;
    int rc;
    if (!foreground() || !caller) return SYS_INVAL;
    ensure_init();
    /* Whole-spec copy first; every later check runs on the staged copy
       (C-M5 mutant: rereading userspace after validation breaks
       aliasing safety). */
    if (spec_ptr + sizeof(spec_stage) < spec_ptr) return SYS_BADARG;
    if (copy_from_user(caller, spec_stage, spec_ptr, sizeof(spec_stage)) !=
        sizeof(spec_stage))
        return SYS_BADARG;
    {
        const struct spawn_spec *sp = (const struct spawn_spec *)spec_stage;
        spec = *sp;
    }
    if (spec.path_len < 1 || spec.path_len > 32) return SYS_BADARG;
    if (spec.nargs > UAPI_MAX_ARGC) return SYS_BADARG;
    if (spec.nargs * (cpu_u64)sizeof(struct user_arg) < spec.nargs) return SYS_BADARG;
    if (spec.stdin_sel != STDIN_CLOSED && spec.stdin_sel != STDIN_KBD) return SYS_BADARG;
    if (spec.stdout_sel != STDOUT_SERIAL) return SYS_BADARG;
    if (spec.stderr_sel != 0 || spec.file_in != 0 || spec.file_out != 0 ||
        spec.file_err != 0)
        return SYS_BADARG;
    for (unsigned int i = 0; i < 4; ++i)
        if (spec.reserved[i] != 0) return SYS_BADARG;
    /* Argument descriptors: single copy, then staged-only validation. */
    want = (cpu_u64)spec.nargs * sizeof(struct user_arg);
    if (spec.args_ptr + want < spec.args_ptr) return SYS_BADARG;
    if (spec.nargs > 0) {
        if (copy_from_user(caller, args_stage, spec.args_ptr, want) != want)
            return SYS_BADARG;
    }
    for (cpu_u32 i = 0; i < spec.nargs; ++i) {
        cpu_u64 ptr, len;
        cpu_u8 *dst;
        {
            const struct user_arg *vec = (const struct user_arg *)args_stage;
            ptr = vec[i].ptr;
            len = vec[i].len;
        }
        if (len > UAPI_MAX_ARGV_BYTES) return SYS_BADARG;
        if (ptr + len < ptr) return SYS_BADARG;
        if (len + 1 > UAPI_MAX_ARGV_BYTES - cursor) return SYS_BADARG;
        dst = argv_stage + cursor;
        if (len > 0 && copy_from_user(caller, dst, ptr, len) != len) return SYS_BADARG;
        for (cpu_u64 k = 0; k < len; ++k)
            if (dst[k] == 0) return SYS_BADARG;
        argv_ptrs[i] = (cpu_u64)dst;
        argv_lens[i] = len;
        cursor += len + 1;
    }
    /* Path: bounded copy, NUL-terminate, then final discovery (Slice D:
       absolute paths pass the audited rule; bare names resolve under
       /bin/ with the frozen bound, never truncated). Every later step
       uses the resolved copy only. */
    if (spec.path_ptr + (cpu_u64)spec.path_len < spec.path_ptr) return SYS_BADARG;
    if (copy_from_user(caller, path_stage, spec.path_ptr, spec.path_len) != spec.path_len)
        return SYS_BADARG;
    path_stage[spec.path_len] = 0;
    {
        cpu_u64 eff = 0;
        while (eff < spec.path_len && path_stage[eff]) ++eff;
        rc = resolve_exec_path((const char *)path_stage, eff,
                               (char *)resolved_path);
        if (rc != SYS_OK) return rc;
    }
    /* Output capability BEFORE any admission (A3 discipline): a hostile
       handle_out must fail here, never after resources move. */
    if (handle_out + sizeof(cpu_u64) < handle_out) return SYS_BADARG;
    if (copy_dest_ok(caller, handle_out, sizeof(cpu_u64)) != sizeof(cpu_u64))
        return SYS_BADARG;
    /* Filesystem image fetch (resolved path: absolute or /bin/). */
    if (fs_stat((const char *)resolved_path, &st) != FS_OK) return SYS_NOTFOUND;
    if (st.type != FS_TYPE_FILE) return SYS_MALFORMED;
    if (st.size < RNYX_HEADER_LEN || st.size > IMG_STAGE_MAX) return SYS_MALFORMED;
    if (fs_open((const char *)resolved_path, &h) != FS_OK) return SYS_NOTFOUND;
    {
        cpu_u8 *img = 0;
        cpu_u64 size = st.size;
        if (heap_alloc(size, 8, (void **)&img) != HEAP_OK) {
            (void)fs_close(h);
            return SYS_NOMEM;
        }
        got = 0;
        while (got < size) {
            cpu_u64 chunk = size - got > FS_MAX_READ_BYTES ? FS_MAX_READ_BYTES : size - got;
            cpu_u64 n = 0;
            /* Heap alignment (8) satisfies the 2-byte block-layer rule;
               every chunk offset here is a multiple of 16384 (even). */
            if (fs_read(h, got, img + got, chunk, &n) != FS_OK || n != chunk) {
                (void)fs_close(h);
                if (heap_free(img) != HEAP_OK) panic("spawn_heap");
                return SYS_IOERR;
            }
            got += n;
        }
        if (fs_close(h) != FS_OK) {
            if (heap_free(img) != HEAP_OK) panic("spawn_heap");
            return SYS_IOERR;
        }
        rc = proc_spawn_image(img, size, argv_ptrs, argv_lens, spec.nargs,
                              spec.stdin_sel, proc_owner_of(caller), &handle);
        if (heap_free(img) != HEAP_OK) panic("spawn_heap");
    }
    if (rc != SYS_OK) return rc;
    /* Publish LAST (pre-validated above; no mutator can intervene on a
       single CPU with IF=0, so failure here is corruption, not input). */
    if (copy_to_user(caller, handle_out, (const cpu_u8 *)&handle,
                     sizeof(handle)) != sizeof(handle))
        panic("spawn_publish");
    return SYS_OK;
}

/* Stage one side's argv descriptors+strings into kernel buffers.
 * Descriptors are copied once into the shared args_stage; strings are
 * copied once into dst with interior-NUL rejection; ptrs/lens point at
 * the staged strings. Returns SYS_OK or SYS_BADARG. */
static int stage_argv(struct user_context *caller, cpu_u64 args_ptr,
                      cpu_u32 nargs, cpu_u8 *dst, cpu_u64 *ptrs, cpu_u64 *lens)
{
    cpu_u64 want, cursor = 0;
    if (nargs > UAPI_MAX_ARGC) return SYS_BADARG;
    want = (cpu_u64)nargs * sizeof(struct user_arg);
    if (args_ptr + want < args_ptr) return SYS_BADARG;
    if (nargs > 0) {
        if (copy_from_user(caller, args_stage, args_ptr, want) != want)
            return SYS_BADARG;
    }
    for (cpu_u32 i = 0; i < nargs; ++i) {
        cpu_u64 ptr, len;
        cpu_u8 *d;
        {
            const struct user_arg *vec = (const struct user_arg *)args_stage;
            ptr = vec[i].ptr;
            len = vec[i].len;
        }
        if (len > UAPI_MAX_ARGV_BYTES) return SYS_BADARG;
        if (ptr + len < ptr) return SYS_BADARG;
        if (len + 1 > UAPI_MAX_ARGV_BYTES - cursor) return SYS_BADARG;
        d = dst + cursor;
        if (len > 0 && copy_from_user(caller, d, ptr, len) != len) return SYS_BADARG;
        for (cpu_u64 k = 0; k < len; ++k)
            if (d[k] == 0) return SYS_BADARG;
        ptrs[i] = (cpu_u64)d;
        lens[i] = len;
        cursor += len + 1;
    }
    return SYS_OK;
}

/* Roll back one reserved slot that never published a handle: destroy a
 * pristine context if present, return the slot to FREE with its
 * generation UNCHANGED (no observer could see the transient LOADING). */
static void rollback_slot(unsigned int slot)
{
    struct proc_slot *s = &slots[slot];
    if (s->ctx) {
        require(user_destroy(s->ctx), "pipe_rollback_destroy");
        s->ctx = 0;
    }
    s->state = PL_FREE;
    s->worker_valid = 0;
    s->kill_requested = 0;
    s->owner = PROC_OWNER_KERNEL;
    s->exit_code = 0;
    s->fault_vector = 0;
    s->fault_error = 0;
}

/* Fetch + validate an executable image by resolved path into heap
 * staging (freed by the caller). Maps stat/open/read/close outcomes to
 * the frozen spawn classes. */
static int fetch_image(const char *path, cpu_u8 **img_out, cpu_u64 *len_out)
{
    struct fs_stat st;
    cpu_u32 h = 0;
    cpu_u8 *img = 0;
    cpu_u64 size, got = 0;
    struct rnyx_layout lay;
    if (!path || !img_out || !len_out) return SYS_INVAL;
    if (fs_stat(path, &st) != FS_OK) return SYS_NOTFOUND;
    if (st.type != FS_TYPE_FILE) return SYS_MALFORMED;
    if (st.size < RNYX_HEADER_LEN || st.size > IMG_STAGE_MAX) return SYS_MALFORMED;
    if (fs_open(path, &h) != FS_OK) return SYS_NOTFOUND;
    size = st.size;
    if (heap_alloc(size, 8, (void **)&img) != HEAP_OK) {
        (void)fs_close(h);
        return SYS_NOMEM;
    }
    while (got < size) {
        cpu_u64 chunk = size - got > FS_MAX_READ_BYTES ? FS_MAX_READ_BYTES : size - got;
        cpu_u64 n = 0;
        if (fs_read(h, got, img + got, chunk, &n) != FS_OK || n != chunk) {
            (void)fs_close(h);
            if (heap_free(img) != HEAP_OK) panic("pipe_fetch_heap");
            return SYS_IOERR;
        }
        got += n;
    }
    if (fs_close(h) != FS_OK) {
        if (heap_free(img) != HEAP_OK) panic("pipe_fetch_heap");
        return SYS_IOERR;
    }
    if (rnyx_validate(img, size, &lay) != RNYX_OK) {
        if (heap_free(img) != HEAP_OK) panic("pipe_fetch_heap");
        return SYS_MALFORMED;
    }
    *img_out = img;
    *len_out = size;
    return SYS_OK;
}

/* Build a pristine context + argv block on a reserved LOADING slot.
 * The slot stays invisible (no handle) until admission. */
static int prepare_side(unsigned int slot, const cpu_u8 *img, cpu_u64 len,
                        const cpu_u64 *ptrs, const cpu_u64 *lens, cpu_u64 nargs)
{
    struct rnyx_layout lay;
    struct user_context *c = 0;
    if (rnyx_validate(img, len, &lay) != RNYX_OK) return SYS_MALFORMED;
    if (!user_create_loaded(&c, (const char *)(img + lay.code_off), lay.code_len,
                            (const char *)(img + lay.data_off), lay.data_filesz,
                            lay.data_memsz))
        return SYS_NOMEM;
    slots[slot].ctx = c;
    if (!user_prepare_argv(c, nargs, ptrs, lens)) {
        require(user_destroy(c), "pipe_argv_destroy");
        slots[slot].ctx = 0;
        return SYS_NOMEM;
    }
    return SYS_OK;
}

int proc_spawn_pipe(const char *path_a,
                    const cpu_u64 *arg_ptrs_a, const cpu_u64 *arg_lens_a,
                    cpu_u64 nargs_a, unsigned int stdin_a,
                    const char *path_b,
                    const cpu_u64 *arg_ptrs_b, const cpu_u64 *arg_lens_b,
                    cpu_u64 nargs_b,
                    int owner, cpu_u64 *handle_a_out, cpu_u64 *handle_b_out)
{
    unsigned int slot_a = PROC_MAX, slot_b = PROC_MAX;
    unsigned int i, nfree = 0;
    thread_id worker_a = 0, worker_b = 0;
    cpu_u8 *ring = 0, *img = 0;
    cpu_u64 img_len = 0;
    int rc;
    if (!foreground() || !proc_ready) return SYS_INVAL;
    if (!path_a || !path_b) return SYS_INVAL;
    if (!handle_a_out || !handle_b_out) return SYS_INVAL;
    if (nargs_a > UAPI_MAX_ARGC || nargs_b > UAPI_MAX_ARGC) return SYS_BADARG;
    if ((nargs_a && (!arg_ptrs_a || !arg_lens_a)) ||
        (nargs_b && (!arg_ptrs_b || !arg_lens_b)))
        return SYS_BADARG;
    if (stdin_a != STDIN_CLOSED && stdin_a != STDIN_KBD) return SYS_BADARG;
    if (owner != PROC_OWNER_KERNEL &&
        (owner < 0 || owner >= (int)PROC_MAX || slots[(unsigned int)owner].state == PL_FREE))
        return SYS_BADARG;
    /* Capacity before any state moves: two FREE slots, two FREE worker
       threads, and a free pipe. The thread count is exact (IF=0, single
       CPU): later creations cannot fail for capacity, so no created
       worker is ever stranded without a teardown path. */
    for (i = 0; i < PROC_MAX; ++i)
        if (slots[i].state == PL_FREE) ++nfree;
    if (nfree < 2) return SYS_BUSY;
    if (thread_free_count() < 2) return SYS_NOMEM;
    if (pipe_allocated()) return SYS_BUSY;
    for (i = 0; i < PROC_MAX; ++i)
        if (slots[i].state == PL_FREE) {
            if (slot_a >= PROC_MAX) slot_a = i;
            else {
                slot_b = i;
                break;
            }
        }
    if (slot_a >= PROC_MAX || slot_b >= PROC_MAX) return SYS_BUSY;
    /* Reserve invisibly (LOADING publishes no handle; generations
       untouched). Endpoint selectors are fixed now so pipe_check sees
       coherent roles from reservation through admission. */
    slots[slot_a].state = PL_LOADING;
    slots[slot_a].ctx = 0;
    slots[slot_a].owner = owner;
    slots[slot_a].worker_valid = 0;
    slots[slot_a].kill_requested = 0;
    slots[slot_a].stdin_sel = stdin_a;
    slots[slot_a].stdout_sel = STDOUT_PIPE;
    slots[slot_b].state = PL_LOADING;
    slots[slot_b].ctx = 0;
    slots[slot_b].owner = owner;
    slots[slot_b].worker_valid = 0;
    slots[slot_b].kill_requested = 0;
    slots[slot_b].stdin_sel = STDIN_PIPE;
    slots[slot_b].stdout_sel = STDOUT_SERIAL;
    /* Ring before any fetch/prepare: every later failure path frees it
       (D-M12: skipping that free leaks exactly one ring, which the
       resource-balance gates catch). */
    if (!pipe_ring_alloc(&ring)) {
        rollback_slot(slot_a);
        rollback_slot(slot_b);
        return SYS_NOMEM;
    }
    /* Fetch A, prepare A, release the image; then B the same way. At
       most one image is heap-resident at a time, so even two
       maximum-size images never exceed the heap arena. */
    rc = fetch_image(path_a, &img, &img_len);
    if (rc != SYS_OK) {
        (void)pipe_ring_free(ring);
        rollback_slot(slot_a);
        rollback_slot(slot_b);
        return rc;
    }
    rc = prepare_side(slot_a, img, img_len, arg_ptrs_a, arg_lens_a, nargs_a);
    if (heap_free(img) != HEAP_OK) panic("pipe_img");
    img = 0;
    if (rc != SYS_OK) {
        (void)pipe_ring_free(ring);
        rollback_slot(slot_a);
        rollback_slot(slot_b);
        return rc;
    }
    rc = fetch_image(path_b, &img, &img_len);
    if (rc != SYS_OK) {
        (void)pipe_ring_free(ring);
        rollback_slot(slot_a);
        rollback_slot(slot_b);
        return rc;
    }
    rc = prepare_side(slot_b, img, img_len, arg_ptrs_b, arg_lens_b, nargs_b);
    if (heap_free(img) != HEAP_OK) panic("pipe_img");
    img = 0;
    if (rc != SYS_OK) {
        (void)pipe_ring_free(ring);
        rollback_slot(slot_a);
        rollback_slot(slot_b);
        return rc;
    }
    if (!user_check()) panic("pipe_precheck_user");
    if (!proc_check()) panic("pipe_precheck_proc");
    /* Workers come last; their creation cannot fail for capacity
       (pre-checked above). */
    if (!thread_create_with_flags(&worker_a, proc_worker,
                                  (void *)(cpu_u64)slot_a, 0x002)) {
        (void)pipe_ring_free(ring);
        rollback_slot(slot_a);
        rollback_slot(slot_b);
        return SYS_NOMEM;
    }
    if (!thread_create_with_flags(&worker_b, proc_worker,
                                  (void *)(cpu_u64)slot_b, 0x002)) {
        /* Unreachable for capacity (pre-checked); a failure here would
           strand worker_a with no teardown path, so it is corruption
           class like the admission panics below. The ring is freed to
           keep heap balance on the halt path diagnostics. */
        (void)pipe_ring_free(ring);
        panic("pipe_worker_b");
    }
    if (!user_sync_spaces()) panic("pipe_sync");
    slots[slot_a].worker = worker_a;
    slots[slot_a].worker_valid = 1;
    slots[slot_b].worker = worker_b;
    slots[slot_b].worker_valid = 1;
    slots[slot_a].exit_code = 0;
    slots[slot_a].fault_vector = 0;
    slots[slot_a].fault_error = 0;
    slots[slot_b].exit_code = 0;
    slots[slot_b].fault_vector = 0;
    slots[slot_b].fault_error = 0;
    /* Atomic admission: two ACTIVE children plus one allocated pipe.
       pipe_attach cannot fail here (pre-checked free, valid slots). */
    if (!pipe_attach(ring, slot_a, slots[slot_a].gen,
                     slot_b, slots[slot_b].gen))
        panic("pipe_attach");
    ring = 0;
    slots[slot_a].state = PL_ACTIVE;
    slots[slot_b].state = PL_ACTIVE;
    if (!user_check() || !proc_check() || !pipe_check()) panic("pipe_admit");
    for (i = 0; i < PROC_MAX; ++i)
        if (i != slot_a && i != slot_b && slots[i].state != PL_FREE &&
            (slots[i].ctx == slots[slot_a].ctx || slots[i].ctx == slots[slot_b].ctx))
            panic("pipe_alias");
    *handle_a_out = (cpu_u64)slot_a | (slots[slot_a].gen << 32);
    *handle_b_out = (cpu_u64)slot_b | (slots[slot_b].gen << 32);
    return SYS_OK;
}

/* Common spawn-spec shape (path/argv bounds, frozen reserved words).
 * Endpoint selectors are side-specific (checked by the caller). */
static int check_spec_shape(const struct spawn_spec *s)
{
    if (!s) return SYS_BADARG;
    if (s->path_len < 1 || s->path_len > 32) return SYS_BADARG;
    if (s->nargs > UAPI_MAX_ARGC) return SYS_BADARG;
    if (s->stderr_sel != 0 || s->file_in != 0 || s->file_out != 0 ||
        s->file_err != 0)
        return SYS_BADARG;
    for (unsigned int i = 0; i < 4; ++i)
        if (s->reserved[i] != 0) return SYS_BADARG;
    return SYS_OK;
}

int sys_spawn_pipe(struct user_context *caller, cpu_u64 spec_a,
                   cpu_u64 spec_b, cpu_u64 handle_a_out, cpu_u64 handle_b_out)
{
    struct spawn_spec sa, sb;
    cpu_u64 ha = 0, hb = 0;
    int rc;
    if (!foreground() || !caller) return SYS_INVAL;
    ensure_init();
    /* Stage + validate spec A (producer: stdout forced to PIPE_W). */
    if (spec_a + sizeof(spec_stage) < spec_a) return SYS_BADARG;
    if (copy_from_user(caller, spec_stage, spec_a, sizeof(spec_stage)) !=
        sizeof(spec_stage))
        return SYS_BADARG;
    {
        const struct spawn_spec *sp = (const struct spawn_spec *)spec_stage;
        sa = *sp;
    }
    rc = check_spec_shape(&sa);
    if (rc != SYS_OK) return rc;
    if (sa.stdin_sel != STDIN_CLOSED && sa.stdin_sel != STDIN_KBD) return SYS_BADARG;
    if (sa.stdout_sel != STDOUT_PIPE) return SYS_BADARG;
    /* Stage + validate spec B (consumer: stdin forced to PIPE_R). */
    if (spec_b + sizeof(spec_stage) < spec_b) return SYS_BADARG;
    if (copy_from_user(caller, spec_stage, spec_b, sizeof(spec_stage)) !=
        sizeof(spec_stage))
        return SYS_BADARG;
    {
        const struct spawn_spec *sp = (const struct spawn_spec *)spec_stage;
        sb = *sp;
    }
    rc = check_spec_shape(&sb);
    if (rc != SYS_OK) return rc;
    if (sb.stdin_sel != STDIN_PIPE) return SYS_BADARG;
    if (sb.stdout_sel != STDOUT_SERIAL) return SYS_BADARG;
    /* Stage both argv vectors (single copies; staged-only afterwards). */
    rc = stage_argv(caller, sa.args_ptr, sa.nargs, argv_stage, argv_ptrs, argv_lens);
    if (rc != SYS_OK) return rc;
    rc = stage_argv(caller, sb.args_ptr, sb.nargs, argv_stage_b, argv_ptrs_b, argv_lens_b);
    if (rc != SYS_OK) return rc;
    /* Output capability before any admission or fetch (both outputs
       last on success; both untouched on every failure). */
    if (handle_a_out + sizeof(cpu_u64) < handle_a_out) return SYS_BADARG;
    if (copy_dest_ok(caller, handle_a_out, sizeof(cpu_u64)) != sizeof(cpu_u64))
        return SYS_BADARG;
    if (handle_b_out + sizeof(cpu_u64) < handle_b_out) return SYS_BADARG;
    if (copy_dest_ok(caller, handle_b_out, sizeof(cpu_u64)) != sizeof(cpu_u64))
        return SYS_BADARG;
    /* Resolve both paths (separate buffers: both stay resident for
       the atomic call below). Image fetch happens inside
       proc_spawn_pipe, one image at a time. */
    if (sa.path_ptr + (cpu_u64)sa.path_len < sa.path_ptr) return SYS_BADARG;
    if (copy_from_user(caller, path_stage, sa.path_ptr, sa.path_len) != sa.path_len)
        return SYS_BADARG;
    path_stage[sa.path_len] = 0;
    {
        cpu_u64 eff = 0;
        while (eff < sa.path_len && path_stage[eff]) ++eff;
        rc = resolve_exec_path((const char *)path_stage, eff, (char *)resolved_path);
        if (rc != SYS_OK) return rc;
    }
    if (sb.path_ptr + (cpu_u64)sb.path_len < sb.path_ptr) return SYS_BADARG;
    if (copy_from_user(caller, path_stage, sb.path_ptr, sb.path_len) != sb.path_len)
        return SYS_BADARG;
    path_stage[sb.path_len] = 0;
    {
        cpu_u64 eff = 0;
        while (eff < sb.path_len && path_stage[eff]) ++eff;
        rc = resolve_exec_path((const char *)path_stage, eff, (char *)resolved_path_b);
        if (rc != SYS_OK) return rc;
    }
    rc = proc_spawn_pipe((const char *)resolved_path,
                         argv_ptrs, argv_lens, sa.nargs, sa.stdin_sel,
                         (const char *)resolved_path_b,
                         argv_ptrs_b, argv_lens_b, sb.nargs,
                         proc_owner_of(caller), &ha, &hb);
    if (rc != SYS_OK) return rc;
    /* Publish LAST (pre-validated; single CPU, no mutator can
       intervene, so failure here is corruption, not input). */
    if (copy_to_user(caller, handle_a_out, (const cpu_u8 *)&ha,
                     sizeof(ha)) != sizeof(ha))
        panic("pipe_publish_a");
    if (copy_to_user(caller, handle_b_out, (const cpu_u8 *)&hb,
                     sizeof(hb)) != sizeof(hb))
        panic("pipe_publish_b");
    return SYS_OK;
}

int sys_wait(struct user_context *caller, cpu_u64 handle, cpu_u64 status_out)
{
    struct proc_status staged;
    int owner;
    int rc;
    if (!foreground() || !caller) return SYS_INVAL;
    ensure_init();
    /* Pre-validate the output before anything consumes (C-M2 mutant:
       consuming first loses terminal status on hostile pointers). */
    if (status_out + sizeof(staged) < status_out) return SYS_BADARG;
    if (copy_dest_ok(caller, status_out, sizeof(staged)) != sizeof(staged))
        return SYS_BADARG;
    owner = proc_owner_of(caller);
    rc = proc_wait(handle, &staged, owner, owner);
    if (rc != SYS_OK) return rc;
    if (copy_to_user(caller, status_out, (const cpu_u8 *)&staged,
                     sizeof(staged)) != sizeof(staged))
        panic("wait_publish");
    return SYS_OK;
}

int sys_terminate(struct user_context *caller, cpu_u64 handle)
{
    int owner, self;
    if (!foreground() || !caller) return SYS_INVAL;
    ensure_init();
    owner = proc_owner_of(caller);
    self = owner;
    return proc_terminate(handle, owner, self);
}
