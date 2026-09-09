/* Stage 18c native runtime library (CPL3, freestanding).
 *
 * Thin validated wrappers over the frozen 18b syscalls (exit/write/yield
 * only: exact numbers in kernel/include/syscall.h, never duplicated
 * here as literals except through the gate enum below). No host OS API,
 * no blocking, no growth. Ownership and limits: docs/design/native-runtime.md.
 */
#ifndef RYNOR_RT_H
#define RYNOR_RT_H

/* Frozen gate numbers (Stage 18b set, `int $0x80` only). Deliberately
   restated here instead of including kernel/include/syscall.h so user
   builds never depend on kernel headers; the values mirror the kernel
   header and a repository test pins them (and the doc table) equal.
   fd 1 likewise mirrors SYS_STDOUT (pinned by the same test). */
#define RT_SYS_EXIT 0u
#define RT_SYS_YIELD 1u
#define RT_SYS_WRITE 2u

/* Frozen error set. The library contains no intentional trap/panic
   path (no UD2/DIV-by-zero/signed-overflow); wild caller pointers still
   fault as USER_FAULTED, which is a caller bug, not a library trap.
   Fatal conformance mismatches exit via rt_exit with 64+class
   (1=fmt->65, 2=alloc->66, 3=write->67, 4=nap->68, 5=wait->69,
   6=nosys->70; 0=success). */
enum rt_err {
    RT_OK = 0,
    RT_INVAL = 1,
    RT_RANGE = 2,
    RT_NOSYS = 3,
    RT_AGAIN = 4,
    RT_NOMEM = 5
};

/* Frozen bounds (mirror the kernel/user contracts they sit on). */
#define RT_FD_STDOUT 1u
#define RT_WRITE_MAX 4096u
#define RT_PRINT_MAX 4096u
#define RT_ARENA_SIZE 2048u
#define RT_ARENA_ALIGN_MAX 16u
#define RT_ARENA_SLOTS 16u
#define RT_NAP_MAX 64u

/* Frozen surface: 15 functions (13 functional + 2 read-only evidence
   channels rt_live_count/rt_ptr_off). Evidence channels never write the
   ledger and never authorize frees; rt_free consults live-pointer
   identity only. */
/* Process control. */
void rt_exit(int code) __attribute__((noreturn));

/* I/O over fd 1 only. rt_fmt returns bytes emitted (0..need, need <=
   INT64_MAX required; cap must be <= INT64_MAX), or -RT_INVAL /
   -RT_RANGE (negative, so counts never collide with error codes).
   rt_fmt never NUL-terminates: use the returned count with
   rt_print_bytes; scanning the buffer would read stale bytes.
   fmt must be a NUL-terminated string (bounded to RT_PRINT_MAX bytes
   scanned); args must be stable for the call duration. */
enum rt_err rt_write(unsigned int fd, const void *buf, unsigned long long n);
enum rt_err rt_print(const char *s);
enum rt_err rt_print_bytes(const char *s, unsigned long long n);
/* rt_fmt supports %s %u %x %c only; %u/%x consume unsigned int (use
   rt_rl.c helpers for full 64-bit ints). See rt.c for the contract. */
long long rt_fmt(char *buf, unsigned long long cap, const char *fmt, ...);

/* Bounded arena (no growth). Pointers are arena offsets only in the
   sense that callers must treat them as opaque; for deterministic
   evidence the tests print offsets via rt_ptr_off().
   rt_ptr_off maps any in-arena address to its offset; NULL or
   out-of-arena input returns (unsigned long long)-1 and never traps.
   Liveness is not checked (evidence only). */
enum rt_err rt_alloc(unsigned long long align, unsigned long long size, void **out);
enum rt_err rt_free(void *ptr);
unsigned long long rt_arena_watermark(void);
unsigned long long rt_live_count(void);
unsigned long long rt_ptr_off(const void *ptr);

/* Cooperative sync (single context only; never blocks; no wall-clock).
   max_yields is caller-bounded: huge values intentionally yield that
   many times. Cross-context handoff is 18d scope, not implemented. */
enum rt_err rt_nap(unsigned long long yields);
enum rt_err rt_set_flag(unsigned long long *p);
enum rt_err rt_wait_flag(unsigned long long *p, unsigned long long max_yields);

/* Honest stubs: always RT_NOSYS, documented. */
enum rt_err rt_open(const char *path);
enum rt_err rt_read(int fd, void *buf, unsigned long long n);

#endif
