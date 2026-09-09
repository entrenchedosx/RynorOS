#ifndef RYNOR_USER_H
#define RYNOR_USER_H
#include "cpu.h"
#include "vm.h"

/* Stage 18a protected-userspace foundation. Static model: fixed user
   layout, two contexts max, int $0x80 gate with exit/yield reasons.
   See docs/design/userspace.md. All operations require IF=0; create,
   destroy, enter and resume additionally require foreground (never IRQ)
   context. */

/* Stage 18d Slice C: three static contexts (shell + two pipeline
   children); exit stacks scale with the same macro. A process table is
   still deferred beyond 18c in shape, but capacity is exactly three. */
#define USER_MAX_CONTEXTS 3u
#define USER_CODE_BASE 0x400000ULL
#define USER_DATA_BASE 0x600000ULL
#define USER_STACK_TOP 0x800000ULL
#define USER_STACK_PAGE (USER_STACK_TOP - VM_PAGE_SIZE)
#define USER_GUARD_PAGE (USER_STACK_PAGE - VM_PAGE_SIZE)
#define USER_EXIT_STACK_PAGES 1u
#define USER_EXIT_STACK_BYTES (USER_EXIT_STACK_PAGES * VM_PAGE_SIZE)

/* Deterministic preemption-test length. The spin tasks cannot exit
   before the flag ticks (timing-independent: flags are tick-driven, so
   any host speed yields the same counts). Tick 25 flags the current
   task, tick 26 flags the other; both then exit: 26 ticks, 28 switches,
   13/13 preemptions, 14/14 dispatches. */
#define USER_PREEMPT_TICKS 26u
#define USER_FLAG_TICKS 25u
/* Anti-hang backstop for the spin loop (iterations, never reached when
   flags work: 26 ticks land in ~0.26 s, far below this bound on any
   host). Tripping it fails the exact-count asserts loudly. */
#define USER_SPIN_BACKSTOP 1000000000ULL

/* Data-page offsets shared by guest blobs and the kernel. */
#define USER_DATA_STOP 0x00ULL
#define USER_DATA_COUNT 0x08ULL
#define USER_DATA_GPRS 0x10ULL
#define USER_DATA_GPR_COUNT 15u
/* Attack-blob parameters (kernel text/data VAs, prefilled by
   user_create past the spill area). */
#define USER_DATA_KTEXT 0x90ULL
#define USER_DATA_KDATA 0x98ULL

/* Gate reasons live in the stable syscall namespace (syscall.h):
   SYS_EXIT/SYS_YIELD/SYS_WRITE. Unknown reasons die as invalid_call. */

/* Return codes from user_enter/user_resume in RAX. Details (exit code,
   fault vector/error/CR2, yield count) live in the context record. */
#define USER_RUN_EXITED 1u
#define USER_RUN_YIELDED 2u
#define USER_RUN_PREEMPTED 3u
#define USER_RUN_FAULTED 4u
#define USER_RUN_WRITTEN 5u
/* Stage 18d Slice A: read() resumes the process like write (terminal for
 * the call, not for the process). Kernel-internal run code, not UAPI. */
#define USER_RUN_READ 6u
/* Stage 18d Slice C: spawn/wait/terminate resume codes (terminal for the
 * call, not for the process). Kernel-internal, not UAPI. */
#define USER_RUN_SPAWNED 7u
#define USER_RUN_WAITED 8u
#define USER_RUN_TERMINATED 9u

/* USER_ABORTED (Slice C): worker-observed kill flag converted to a
 * terminal state on the owning thread only. Never resumed, never
 * re-entered; destroyed like EXITED/FAULTED. */
enum user_state { USER_FREE, USER_ACTIVE, USER_EXITED, USER_FAULTED, USER_ABORTED };

/* Stage 18d Slice C (RYNX v2): bounded multi-page code/data windows.
 * v1 images use exactly 1 code + 1 data page (behavior identical). */
#define USER_MAX_CODE_PAGES 8u
#define USER_MAX_DATA_PAGES 4u
#define USER_V2_CODE_MAX (8u * 4096u)
#define USER_V2_DATA_MAX (4u * 4096u)

/* Scheduler-visible link. kern_save is frame_valid-compatible by
   construction (resume label RIP, entry RSP in own stack, kernel
   selectors, masked RFLAGS, vector/error zero) so preempted-user
   threads validate with zero scheduler changes. */
struct user_link {
    struct user_context *context;
    struct exception_frame kern_save;
    int bound;
};

struct user_context {
    enum user_state state;
    unsigned int slot;
    struct vm_space space;
    /* Physical frames backing the fixed windows. v1 uses index 0 only;
       v2 code/data tile pages 0..count-1 contiguously in VA. */
    cpu_u64 code_frame[USER_MAX_CODE_PAGES];
    cpu_u64 data_frame[USER_MAX_DATA_PAGES];
    cpu_u64 stack_frame;
    unsigned int code_pages, data_pages;
    cpu_u64 exit_base, exit_top;
    cpu_u64 table_pages_at_create;
    /* Recorded user state (full GPRs for faithful preemption resume). */
    cpu_u64 gprs[15];
    cpu_u64 rip, rsp, rflags;
    /* Termination record. */
    cpu_u64 exit_code;
    cpu_u64 fault_vector, fault_error, fault_cr2;
    int fault_class; /* 0 none, 1 trap, 2 invalid_call */
    /* Set for loader-created programs: gate evidence prints as [LOAD]
       rows by the load driver instead of [USER] rows here, so the 18a
       section grammar stays exact. */
    int loaded;
    /* Statistics. */
    cpu_u64 entries, resumes, gate_exits, yields, preemptions, faults;
    cpu_u64 sys_writes, sys_result;
    cpu_u64 code_size;
    struct user_link link;
};

/* Which blob image user_create copies into the code page. Extension
   blobs are CPL3 attack payloads: each must fault in a pinned way
   (see user_self_test fault rows); none may ever complete. */
enum user_blob {
    USER_BLOB_EXIT, USER_BLOB_SPIN, USER_BLOB_YIELD,
    USER_BLOB_UD2, USER_BLOB_READKERN_LO, USER_BLOB_READKERN_HI,
    USER_BLOB_WRITE_RX, USER_BLOB_EXEC_DATA, USER_BLOB_CLI,
    USER_BLOB_NULL, USER_BLOB_BADCALL,
    USER_BLOB_READKERN_TEXT, USER_BLOB_WRITEKERN_DATA,
    USER_BLOB_EXECKERN_TEXT, USER_BLOB_EXECSTACK,
    USER_BLOB_READCR3, USER_BLOB_KERNSEL, USER_BLOB_BADSEL,
    USER_BLOB_TIBIT, USER_BLOB_FARJMP_KCS, USER_BLOB_FARJMP_UDATA,
    USER_BLOB_MOVSS, USER_BLOB_DIVZERO, USER_BLOB_SYSCALL,
    USER_BLOB_SS_RSP, USER_BLOB_KERN_RSP,
    USER_BLOB_IRETQ_KCS, USER_BLOB_RETFQ_KCS, USER_BLOB_RDMSR,
};

int user_initialize(void); /* probe + static checks, once, foreground */
int user_check(void);      /* structural invariants, IF=0 */
int user_fault_managed(cpu_u64 vector); /* CPL3 kill-path whitelist, pure */
int user_create(struct user_context **out, enum user_blob blob);
/* Loaded-program sibling: fixed layout filled from file bytes (data tail
   zeroed, covering BSS), no attack-parameter prefill. Entry is defined
   as USER_CODE_BASE. v1 sizes (code<=4K, data<=4K) map one page each;
   v2 tiles up to USER_MAX_CODE_PAGES/USER_MAX_DATA_PAGES. */
int user_create_loaded(struct user_context **out, const char *code, cpu_u64 code_len,
                       const char *data, cpu_u64 data_len, cpu_u64 data_memsz);
/* Destroy an EXITED/FAULTED/ABORTED context, or a pristine ACTIVE one
   (never entered, unbound). Anything live is rejected fail-closed. */
int user_destroy(struct user_context *context);
/* ABORTED transition for the owning worker thread only: the context must
   be ACTIVE and bound to the calling thread (which is therefore not
   executing inside it). After this call the context is terminal and never resumed. */
int user_mark_aborted(struct user_context *context);
/* Refresh kernel-half snapshots of all live spaces after high-half
   table changes (thread create/join). See user.c. */
int user_sync_spaces(void);
cpu_u64 user_enter(struct user_link *link);  /* initial entry, returns run code */
/* Image entry for spawned programs: like user_enter but preserves the
   recorded RSP (argv startup block built pre-entry) instead of resetting
   it to the stack top. Entry RIP/RFLAGS are still established here. */
cpu_u64 user_enter_image(struct user_link *link);
cpu_u64 user_resume(struct user_link *link); /* re-entry from recorded state */
/* Build the argv startup block on a pristine (never entered) context's
   stack page: [argc][argv[]][NULL][NUL-terminated strings], RSP set to
   the 16-aligned block base (TOP-16 minimum even for argc==0, so
   _start's [RSP] load always lands mapped). args/lens live in kernel
   memory (already staged + validated by the caller). Requires kernel
   CR3 (asserted). */
int user_prepare_argv(struct user_context *context, cpu_u64 nargs,
                      const cpu_u64 *ptrs, const cpu_u64 *lens);
/* Record + validate a CPL3 frame on the IRQ path. All pure checks run
   pre-switch (still on the entry CR3); the switch happens only on
   success, so failure returns with the entry stack still addressable
   for a clean diagnostic halt. Returns 0 without touching state on
   any violation. */
int user_save_state(struct user_context *context, struct exception_frame *frame);
/* Pure origin validation (no switch, no state change) for pre-switch use. */
int user_origin_ok(struct user_context *context, struct exception_frame *frame);
/* Stop-flag store for the flag tick; call only on the user CR3 in IRQ
   context right after user_origin_ok passed. */
int user_publish_stop(struct user_context *context, struct exception_frame *frame);
void user_handle_exit(struct exception_frame *frame) __attribute__((noreturn));
void user_handle_fault(struct exception_frame *frame, cpu_u64 cr2) __attribute__((noreturn));
/* CPL3 tick counting for the deterministic preemption test. */
int user_note_cpl3_tick(void);
cpu_u64 user_cpl3_ticks(void);
void user_self_test(void);

/* CPL3 gate stub address (defined in exceptions.asm) for IDT checks. */
extern const char user_exit_stub[];

/* Kernel text/data addresses backing the attack-blob parameters
   (prefilled into each data page; also the exact fault-CR2 oracles). */
extern cpu_u64 user_kernel_cr3;

#endif
