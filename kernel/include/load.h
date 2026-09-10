#ifndef RYNOR_LOAD_H
#define RYNOR_LOAD_H
#include "cpu.h"
#include "user.h"
#include "uapi.h"

/* Stage 18b executable loading (RYNX v1 envelope over fixed user windows).
 *
 * The kernel never parses ELF: a host converter links native RynorLang
 * programs at fixed virtual addresses and packs this envelope, which the
 * kernel validates with checked arithmetic before mapping anything.
 * See docs/design/executable-format.md.
 *
 * Layout (little-endian, 28 bytes, no trailing bytes):
 *   u8[4] magic "RYNX", u16 version (1 or 2), u16 arch (1 = x86-64),
 *   u16 header_len (28), u16 reserved (0), u32 entry_off (0: entry is
 *   defined as USER_CODE_BASE; a variable entry needs an audited
 *   enter-at-offset path first), u32 code_size (v1: 1..4096;
 *   v2: 1..32768), u32 data_filesz (v1: 0..4096; v2: 0..16384),
 *   u32 data_memsz (filesz..same class max),
 * then code_size code bytes, then data_filesz data bytes.
 */
#define RNYX_MAGIC 0x584e5952u
#define RNYX_VERSION 1u
/* Stage 18d Slice C: bounded multi-page envelopes (same 28-byte layout,
 * version-gated size classes; v1 caps byte-identical). */
#define RNYX_VERSION2 2u
#define RNYX_V2_CODE_MAX (8u * 4096u)
#define RNYX_V2_DATA_MAX (4u * 4096u)
#define RNYX_ARCH_X86_64 1u
#define RNYX_HEADER_LEN 28u

struct rnyx_layout {
    cpu_u64 code_off, code_len;
    cpu_u64 data_off, data_filesz, data_memsz;
};

/* Validation failures (driver maps these to evidence reason words). */
enum rnyx_error {
    RNYX_OK = 0,
    RNYX_ERR_TRUNCATED,
    RNYX_ERR_MAGIC,
    RNYX_ERR_VERSION,
    RNYX_ERR_ARCH,
    RNYX_ERR_HEADER,
    RNYX_ERR_ENTRY,
    RNYX_ERR_CODE_SIZE,
    RNYX_ERR_DATA_SIZE,
    RNYX_ERR_SHAPE
};

/* Validate hostile envelope bytes (IF=0, foreground, no state change).
   Returns RNYX_OK with *out filled, or the failing check. */
int rnyx_validate(const cpu_u8 *img, cpu_u64 len, struct rnyx_layout *out);

/* Load a validated image into a fresh context: fixed code/data/stack
   mappings (reusing the 18a address-space machinery, never new VA
   windows), BSS tail zeroed, entry at USER_CODE_BASE. Returns 1 with
   *out bound to nothing (caller enters), 0 with nothing created. */
int load_program(struct user_context **out, const cpu_u8 *img, cpu_u64 len);

/* Write syscall body: copyin [buf, buf+len) via validated page chunks
   to the serial sink. Returns bytes written or (cpu_u64)-1. Runs with
   IF=0 on kernel CR3; never holds the frame window across VM calls. */
cpu_u64 sys_write(struct user_context *c, cpu_u64 fd, cpu_u64 buf, cpu_u64 len);
/* Validated copyin for spawn staging (Slice C): same two-pass
   discipline as sys_write's internal helper. */
cpu_u64 copy_from_user(struct user_context *c, cpu_u8 *dst,
                       cpu_u64 uaddr, cpu_u64 len);
/* Validated copyout range check (Slice C): every byte of the range must
   be mapped USER+WRITE. Pure validation, no memory touched. */
cpu_u64 copy_dest_ok(struct user_context *c, cpu_u64 uaddr, cpu_u64 len);

/* Stage 18d Slice B: validated copy to userspace. Every byte of
   [uaddr, uaddr+len) must live on a USER+WRITE page (two passes: all
   pages checked first, then bytes move chunk by chunk; window pointers
   never survive a VM call). Returns len, or (cpu_u64)-1 with nothing
   written. len == 0 succeeds without touching the destination. Wrap,
   supervisor leaves, holes, and noncanonical addresses fail here. */
cpu_u64 copy_to_user(struct user_context *c, cpu_u64 uaddr,
                     const cpu_u8 *src, cpu_u64 len);

/* Stage 18d Slice A, syscall 3: nonblocking read from stdin endpoint 0
   (keyboard scan staging; endpoint multiplexing arrives with spawn
   selectors in Slice C). Returns a sys_err code (never a byte count):
   SYS_OK (bytes staged, *nread_out published last), SYS_AGAIN (empty;
   both outputs untouched), SYS_INVAL (bad arguments; outputs untouched).
   Runs with IF=0 on kernel CR3. Slice D routes STDIN_PIPE callers to
   the kernel-owned pipe (AGAIN when empty/live, OK+0 at terminal EOF);
   keyboard/CLOSED behavior is unchanged. */
int sys_read(struct user_context *c, cpu_u64 fd, cpu_u64 buf, cpu_u64 len,
             cpu_u64 nread_out, cpu_u64 flags);

/* Stage 18d Slice D, syscall 7: stateless fread over an absolute path
   (no discovery, no handles). Frozen register order: path_ptr,
   path_len (1..32), offset, buf, len (<=UAPI_FREAD_MAX), nread_out.
   Short reads at EOF are OK (never an error); offset past end is
   BADARG; missing/ non-file map to NOTFOUND/MALFORMED like spawn.
   Outputs are published last and left untouched on every error. */
int sys_fread(struct user_context *c, cpu_u64 path_ptr, cpu_u64 path_len,
              cpu_u64 offset, cpu_u64 buf, cpu_u64 len, cpu_u64 nread_out);
/* Kernel-memory fread core shared by sys_fread (chunked) and the test
   driver (whole reads): kpath is NUL-terminated kernel memory, kbuf is
   a kernel buffer of at least len bytes (2-byte aligned when len > 0).
   See load.c for the contract. */
int kern_fread(const char *kpath, cpu_u64 offset, cpu_u8 *kbuf,
               cpu_u64 len, cpu_u64 *nread_out);

/* Print the [LOAD] write evidence row (slot/fd/len/nwritten + hex of
   the staged bytes). Called by the gate handler, not the driver, so
   the row is atomic with the syscall under IF=0. */
void sys_write_evidence(struct user_context *c, cpu_u64 fd, cpu_u64 len,
                        cpu_u64 nwritten);

/* Boot self-test driver: scans block devices for RYNX programs, runs
   them, prints [LOAD] evidence. Always terminates the transcript with
   either [LOAD] load verified or [LOAD] no image, skipped. */
void load_self_test(void);

#endif
