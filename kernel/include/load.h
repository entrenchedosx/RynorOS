#ifndef RYNOR_LOAD_H
#define RYNOR_LOAD_H
#include "cpu.h"
#include "user.h"

/* Stage 18b executable loading (RYNX v1 envelope over fixed user windows).
 *
 * The kernel never parses ELF: a host converter links native RynorLang
 * programs at fixed virtual addresses and packs this envelope, which the
 * kernel validates with checked arithmetic before mapping anything.
 * See docs/design/executable-format.md.
 *
 * Layout (little-endian, 28 bytes, no trailing bytes):
 *   u8[4] magic "RYNX", u16 version (1), u16 arch (1 = x86-64),
 *   u16 header_len (28), u16 reserved (0), u32 entry_off (0: entry is
 *   defined as USER_CODE_BASE; a variable entry needs an audited
 *   enter-at-offset path first), u32 code_size (1..4096),
 *   u32 data_filesz (0..4096), u32 data_memsz (filesz..4096),
 * then code_size code bytes, then data_filesz data bytes.
 */
#define RNYX_MAGIC 0x584e5952u
#define RNYX_VERSION 1u
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
