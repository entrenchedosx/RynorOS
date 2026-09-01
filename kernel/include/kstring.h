#ifndef RYNOR_KSTRING_H
#define RYNOR_KSTRING_H
#include "cpu.h"

/* Bounded, freestanding string and memory helpers for the ring-0 kernel.
   No libc dependency. Every function reads only within the caller-declared
   source extent and writes only within the caller-declared destination
   capacity; a failure result never leaves a truncated or uninitialized
   destination visible as success. All lengths/capacities are cpu_u64 and none
   of the bounded primitives may read or write past their declared regions.

   Strings are conventionally NUL-terminated for callers that request them,
   but the primitives only ever rely on the explicit bounds handed in; they
   never scan for a NUL past the given maximum. All objects must be live and
   mapped. Inputs must remain immutable for the call. These helpers allocate
   nothing, never yield, and preserve IF; callers synchronize shared objects.
   Compare functions require valid pointers for nonzero lengths; their integer
   ordering result is not an argument-validation status. */
#define KSTR_MAX_CAP (1ULL << 40)

enum kstr_result {
    KSTR_OK = 0,
    KSTR_INVALID,      /* null pointer, zero capacity, or impossible bounds */
    KSTR_OVERFLOW,     /* result would exceed the destination capacity */
    KSTR_TERMINATION,  /* required source/destination NUL missing within bounds */
};

/* Bounded string length: returns the number of bytes before the first NUL in
   s[0..max-1], or max if no NUL is found there. Never dereferences past max. */
cpu_u64 kstr_nlen(const char *s, cpu_u64 max);

/* Bounded copy: writes src up to cap-1 bytes plus a NUL into dst when the
   source fits within cap-1. KSTR_OVERFLOW when src does not fit; dst is left
   unchanged on failure (bounded helper never overruns). dst must be non-null
   with cap>=1; src must be non-null. */
int kstr_copy(char *dst, cpu_u64 cap, const char *src);
/* Explicit source extent, including space for NUL. Prefer these for byte
   buffers. Legacy copy/cat require src readable through NUL or cap bytes.
   Copy and append permit overlap (snapshot/memmove semantics). Source-bound
   exhaustion reports TERMINATION; a destination overflow detected first takes
   precedence. Both leave dst untouched. */
int kstr_copy_n(char *dst, cpu_u64 cap, const char *src, cpu_u64 src_max);
int kstr_cat_n(char *dst, cpu_u64 cap, const char *src, cpu_u64 src_max);

/* Bounded append: copies src onto the end of the existing dst string so the
   combined length < cap and is NUL-terminated. KSTR_OVERFLOW if it would not
   fit; KSTR_TERMINATION for an unterminated destination; dst unchanged on failure. */
int kstr_cat(char *dst, cpu_u64 cap, const char *src);

/* Bounded compare of up to max bytes, or until a NUL in either. Returns 0 if
   equal, <0 if a<b, >0 if a>b, over the first min(byte length, max) bytes. */
int kstr_cmp(const char *a, const char *b, cpu_u64 max);

/* Bounded byte-region compare: exactly n bytes, no NUL interpretation. */
int kstr_cmpmem(const void *a, const void *b, cpu_u64 n);

/* Bounded byte search: returns 1 and sets *pos to the first index in
   s[0..max-1] equal to c, or 0 when not found within max (pos unchanged). */
int kstr_chr(const char *s, cpu_u64 max, char c, cpu_u64 *pos);

/* Overlap-safe bounded copy of exactly n bytes (memmove semantics). */
int kstr_move(void *dst, const void *src, cpu_u64 n);

/* Bounded formatted output into dst with capacity cap. Supports %u (decimal
   cpu_u64), %x/%X (lower/upper hex cpu_u64), %s (bounded NUL string), %c, and
   %%. Result is always NUL-terminated within cap on KSTR_OK; KSTR_OVERFLOW is
   returned (dst left unchanged) when the formatted text would exceed cap-1 or
   a %s argument exceeds available space. Formats and %s must be readable
   through NUL or KSTR_NLEN_MAX bytes; missing NUL returns KSTR_TERMINATION.
   Destination overlap with format/%s is rejected. All arguments must remain
   unchanged during both passes. No output is committed on validation failure. */
enum kstr_result kstr_format(char *dst, cpu_u64 cap, const char *fmt, ...);

/* Internal variadic entry point, plus the bounded max accepted for %s. */
#define KSTR_NLEN_MAX 4096u
typedef __builtin_va_list kstr_va_list;
enum kstr_result kstr_vformat(char *dst, cpu_u64 cap, const char *fmt, kstr_va_list ap);

/* Convenience decimal/hex formatting used by tests without variadic args. */
int kstr_utoa(char *dst, cpu_u64 cap, cpu_u64 value);
int kstr_utoa_hex(char *dst, cpu_u64 cap, cpu_u64 value, int upper);

#endif /* RYNOR_KSTRING_H */
