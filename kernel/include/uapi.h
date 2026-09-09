#ifndef RYNOR_UAPI_H
#define RYNOR_UAPI_H
#include "cpu.h"

/* Stage 18d frozen userspace ABI (docs/design/stage18d-abi.md §D).
 *
 * Values are explicit integers, never compiler-ordered enums. Layouts use
 * fixed-width kernel types with compile-time offset/size assertions below.
 * Struct shapes for later slices (spawn/wait/pipe) are frozen here so the
 * layouts can never drift before their behavior lands; no behavior for
 * syscalls 4..8 exists yet (their numbers die as invalid_call).
 */

/* System-call error domain. Append-only; never renumber. Distinct from
 * rt_err, fs_result, and rnyx_error (mapping layers translate). */
enum sys_err {
    SYS_OK = 0,
    SYS_AGAIN = 1,
    SYS_INVAL = 2,
    SYS_NOTFOUND = 3,
    SYS_MALFORMED = 4,
    SYS_BADHANDLE = 5,
    SYS_BUSY = 6,
    SYS_NOMEM = 7,
    SYS_BADARG = 8,
    SYS_ALREADY_GONE = 9,
    SYS_IOERR = 10
};

/* Process lifecycle states for wait(). Separate domain; never compare
 * against sys_err values. */
enum proc_state {
    PROC_RUNNING = 0,
    PROC_EXITED = 1,
    PROC_FAULTED = 2,
    PROC_ABORTED = 3
};

/* Standard-stream selectors. Append-only; FILE values are reserved and
 * must be passed as 0 (meaning serial/keyboard default) until Slice C. */
enum stdin_sel {
    STDIN_CLOSED = 0,
    STDIN_KBD = 1,
    STDIN_PIPE = 2,
    STDIN_FILE = 3
};
enum stdout_sel {
    STDOUT_SERIAL = 0,
    STDOUT_PIPE = 1,
    STDOUT_FILE = 2
};

/* Bounds (frozen ABI §§8-9, 19). */
#define UAPI_MAX_ARGC 8u
#define UAPI_MAX_ARGV_BYTES 256u
#define UAPI_STACK_ARGV_BUDGET 512u
#define UAPI_MAX_CMDLINE 256u
#define UAPI_MAX_BIN_NAME 27u
#define UAPI_MAX_PROCS 3u
#define UAPI_PIPE_BUF 4096u

/* Argument descriptor: 16 bytes, alignment 8. Caller strings carry no
 * NUL (the kernel appends exactly one per argument); interior NUL bytes
 * are rejected. */
struct user_arg {
    cpu_u64 ptr;
    cpu_u64 len;
};

/* Spawn descriptor: 96 bytes, alignment 8. Reserved words must be zero
 * (fail closed otherwise); they enable FILE redirection and future
 * fields without resizing. */
struct spawn_spec {
    cpu_u64 path_ptr;    /* 0 */
    cpu_u64 path_len;    /* 8, 1..32 */
    cpu_u64 args_ptr;    /* 16, user_arg[nargs] in caller memory */
    cpu_u32 nargs;       /* 24, <= UAPI_MAX_ARGC */
    cpu_u32 stdin_sel;   /* 28 */
    cpu_u32 stdout_sel;  /* 32 */
    cpu_u32 stderr_sel;  /* 36, must be 0 (serial) in base */
    cpu_u64 file_in;     /* 40, must be 0 in base */
    cpu_u64 file_out;    /* 48, must be 0 in base */
    cpu_u64 file_err;    /* 56, must be 0 in base */
    cpu_u64 reserved[4]; /* 64..96, must be 0 */
};

/* Terminal-status payload for wait(): 16 bytes, alignment 4. */
struct proc_status {
    cpu_u32 state;    /* proc_state */
    cpu_u32 code;     /* EXITED: low 32 of exit code; FAULTED: vector */
    cpu_u32 detail;   /* FAULTED: low 32 of fault error; else 0 */
    cpu_u32 reserved; /* 0 */
};

_Static_assert(sizeof(struct user_arg) == 16, "user_arg layout");
_Static_assert(__builtin_offsetof(struct user_arg, ptr) == 0, "user_arg.ptr");
_Static_assert(__builtin_offsetof(struct user_arg, len) == 8, "user_arg.len");
_Static_assert(sizeof(struct spawn_spec) == 96, "spawn_spec layout");
_Static_assert(__builtin_offsetof(struct spawn_spec, path_ptr) == 0, "spec.path_ptr");
_Static_assert(__builtin_offsetof(struct spawn_spec, path_len) == 8, "spec.path_len");
_Static_assert(__builtin_offsetof(struct spawn_spec, args_ptr) == 16, "spec.args_ptr");
_Static_assert(__builtin_offsetof(struct spawn_spec, nargs) == 24, "spec.nargs");
_Static_assert(__builtin_offsetof(struct spawn_spec, stdin_sel) == 28, "spec.stdin_sel");
_Static_assert(__builtin_offsetof(struct spawn_spec, stdout_sel) == 32, "spec.stdout_sel");
_Static_assert(__builtin_offsetof(struct spawn_spec, stderr_sel) == 36, "spec.stderr_sel");
_Static_assert(__builtin_offsetof(struct spawn_spec, file_in) == 40, "spec.file_in");
_Static_assert(__builtin_offsetof(struct spawn_spec, file_out) == 48, "spec.file_out");
_Static_assert(__builtin_offsetof(struct spawn_spec, file_err) == 56, "spec.file_err");
_Static_assert(__builtin_offsetof(struct spawn_spec, reserved) == 64, "spec.reserved");
_Static_assert(sizeof(struct proc_status) == 16, "proc_status layout");
_Static_assert(__builtin_offsetof(struct proc_status, state) == 0, "status.state");
_Static_assert(__builtin_offsetof(struct proc_status, code) == 4, "status.code");
_Static_assert(__builtin_offsetof(struct proc_status, detail) == 8, "status.detail");
_Static_assert(__builtin_offsetof(struct proc_status, reserved) == 12, "status.reserved");

#endif
