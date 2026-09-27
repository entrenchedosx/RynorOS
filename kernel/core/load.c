#include "load.h"
#include "syscall.h"
#include "ksched.h"
#include "irq.h"
#include "io.h"
#include "vm.h"
#include "kbd.h"
#include "serial.h"
#include "proc.h"
#include "pipe.h"
#include "fs.h"

/* Stage 18b executable loading + syscalls (int $0x80 gate extension).
   All entry points require IF=0 foreground unless noted; hostile bytes
   are validated with checked arithmetic before anything is mapped. */

static int foreground(void) { return cpu_interrupts_disabled() && !irq_in_context(); }

static cpu_u16 rd16le(const cpu_u8 *p)
{ return (cpu_u16)p[0] | ((cpu_u16)p[1] << 8); }

static cpu_u32 rd32le(const cpu_u8 *p)
{
    return (cpu_u32)p[0] | ((cpu_u32)p[1] << 8) |
           ((cpu_u32)p[2] << 16) | ((cpu_u32)p[3] << 24);
}

int rnyx_validate(const cpu_u8 *img, cpu_u64 len, struct rnyx_layout *out)
{
    cpu_u64 version;
    cpu_u64 code_max, data_max;
    if (!img || !out) return RNYX_ERR_TRUNCATED;
    if (len < RNYX_HEADER_LEN) return RNYX_ERR_TRUNCATED;
    if (rd32le(img) != RNYX_MAGIC) return RNYX_ERR_MAGIC;
    version = rd16le(img + 4);
    /* Version-gated size classes: v1 keeps the exact 18b single-page
       caps (byte-compatible); v2 tiles the bounded multi-page windows.
       Unknown versions fail closed; fields are never reinterpreted. */
    if (version == RNYX_VERSION) {
        code_max = VM_PAGE_SIZE;
        data_max = VM_PAGE_SIZE;
    } else if (version == RNYX_VERSION2) {
        code_max = RNYX_V2_CODE_MAX;
        data_max = RNYX_V2_DATA_MAX;
    } else {
        return RNYX_ERR_VERSION;
    }
    if (rd16le(img + 6) != RNYX_ARCH_X86_64) return RNYX_ERR_ARCH;
    if (rd16le(img + 8) != RNYX_HEADER_LEN) return RNYX_ERR_HEADER;
    if (rd16le(img + 10) != 0) return RNYX_ERR_HEADER;
    cpu_u64 entry = rd32le(img + 12);
    cpu_u64 code = rd32le(img + 16);
    cpu_u64 fsz = rd32le(img + 20);
    cpu_u64 msz = rd32le(img + 24);
    /* Entry is defined as the code base: a variable entry would need an
       audited enter-at-offset path first (fixed user_enter resets RIP). */
    if (entry != 0) return RNYX_ERR_ENTRY;
    if (code == 0 || code > code_max) return RNYX_ERR_CODE_SIZE;
    if (fsz > data_max || msz < fsz || msz > data_max) return RNYX_ERR_DATA_SIZE;
    /* Exact file shape: header + code + file-backed data, no truncation,
       no trailing bytes. Every addition is overflow-checked first. */
    if (code > ~0ULL - RNYX_HEADER_LEN) return RNYX_ERR_SHAPE;
    cpu_u64 base = RNYX_HEADER_LEN + code;
    if (fsz > ~0ULL - base) return RNYX_ERR_SHAPE;
    if (base + fsz != len) return RNYX_ERR_SHAPE;
    out->code_off = RNYX_HEADER_LEN;
    out->code_len = code;
    out->data_off = base;
    out->data_filesz = fsz;
    out->data_memsz = msz;
    return RNYX_OK;
}

int load_program(struct user_context **out, const cpu_u8 *img, cpu_u64 len)
{
    if (!foreground() || !out) return 0;
    struct rnyx_layout lay;
    if (rnyx_validate(img, len, &lay) != RNYX_OK) return 0;
    /* Fixed mappings (code U-RX, data U-RW, stack, guard) and
       table-count pinning; the data tail (BSS) is zeroed by the page
       copies. No new VA windows, no new permissions. */
    return user_create_loaded(out, (const char *)(img + lay.code_off), lay.code_len,
                             (const char *)(img + lay.data_off), lay.data_filesz,
                             lay.data_memsz);
}

/* Staging for validated user bytes (single CPU, IF=0 handler context
   only; never shared, never retained across calls). */
static cpu_u8 write_stage[SYSCALL_WRITE_MAX];

/* Copy exactly len bytes from the user range into dst. Two passes: first
   every page must query OK with the USER bit (supervisor leaves, holes,
   and noncanonical addresses fail here with nothing touched), then the
   bytes move page by page (window pointers never survive a VM call).
   Returns len or (cpu_u64)-1. Mappings cannot change mid-call (single
   CPU, IF=0, no other actor maps this space). Exported for the Slice C
   spawn staging (argument descriptors/strings copy once, then validate
   staged copies, never reread userspace). */
cpu_u64 copy_from_user(struct user_context *c, cpu_u8 *dst,
                       cpu_u64 uaddr, cpu_u64 len)
{
    if (!c || !dst) return (cpu_u64)-1;
    for (unsigned int pass = 0; pass < 2; ++pass) {
        cpu_u64 off = 0;
        while (off < len) {
            cpu_u64 addr = uaddr + off;
            cpu_u64 page = addr & ~(VM_PAGE_SIZE - 1);
            cpu_u64 chunk = VM_PAGE_SIZE - (addr & (VM_PAGE_SIZE - 1));
            if (chunk > len - off) chunk = len - off;
            struct vm_mapping m;
            if (vm_query(&c->space, page, &m) != VM_OK ||
                !(m.permissions & VM_USER))
                return (cpu_u64)-1;
            if (pass == 1) {
                cpu_u64 frame = m.physical & ~(VM_PAGE_SIZE - 1);
                volatile cpu_u8 *w = vm_frame_access(frame);
                if (!w) return (cpu_u64)-1;
                cpu_u64 start = addr & (VM_PAGE_SIZE - 1);
                for (cpu_u64 i = 0; i < chunk; ++i)
                    dst[off + i] = w[start + i];
            }
            off += chunk;
        }
    }
    return len;
}

cpu_u64 sys_write(struct user_context *c, cpu_u64 fd, cpu_u64 buf, cpu_u64 len)
{
    if (!c) return (cpu_u64)-1;
    /* Argument validation precedes any memory touch. */
    if (fd != SYS_STDOUT) return (cpu_u64)-1;
    /* Stage 18d Slice D: fd1 pipe-writer routing. Ordinary children
       (STDOUT_SERIAL) take the byte-identical path below; two-stage
       producers (STDOUT_PIPE) enqueue into the kernel-owned pipe. */
    {
        int owner = proc_owner_of(c);
        if (owner != PROC_OWNER_KERNEL && owner >= 0) {
            cpu_u64 gen = 0;
            unsigned int stdin_sel = STDIN_KBD, stdout_sel = STDOUT_SERIAL;
            int live = 0;
            if (proc_slot_info((unsigned int)owner, &gen, &stdin_sel,
                               &stdout_sel, &live) && live &&
                stdout_sel == STDOUT_PIPE) {
                cpu_u64 w;
                if (len > SYSCALL_WRITE_MAX) return (cpu_u64)-1;
                if (len == 0) return 0;
                if (buf + len < buf) return (cpu_u64)-1;
                if (copy_from_user(c, write_stage, buf, len) != len)
                    return (cpu_u64)-1;
                w = pipe_write((unsigned int)owner, gen, write_stage, len);
                /* pipe_write: count/short-0 pass through; broken (-1)
                   passes through; invalid (-2) maps to the existing
                   invalid class (-1). */
                if (w == (cpu_u64)-2) return (cpu_u64)-1;
                return w;
            }
        }
    }
    if (len > SYSCALL_WRITE_MAX) return (cpu_u64)-1;
    if (len == 0) return 0;
    if (buf + len < buf) return (cpu_u64)-1;
    if (copy_from_user(c, write_stage, buf, len) != len) return (cpu_u64)-1;
    return len;
}

/* Destination-range check shared by copy_to_user: every page covering
   [uaddr, uaddr+len) must query OK with USER *and* WRITE (supervisor
   leaves, RX code/data, holes, and noncanonical addresses fail here).
   Pure validation: touches no user memory. Exported for pre-validating
   syscall outputs before irreversible admission steps. */
cpu_u64 copy_dest_ok(struct user_context *c, cpu_u64 uaddr, cpu_u64 len)
{
    cpu_u64 off = 0;
    if (!c) return (cpu_u64)-1;
    if (len == 0) return 0;
    if (uaddr + len < uaddr) return (cpu_u64)-1;
    while (off < len) {
        cpu_u64 addr = uaddr + off;
        cpu_u64 page = addr & ~(VM_PAGE_SIZE - 1);
        cpu_u64 chunk = VM_PAGE_SIZE - (addr & (VM_PAGE_SIZE - 1));
        if (chunk > len - off) chunk = len - off;
        struct vm_mapping m;
        if (vm_query(&c->space, page, &m) != VM_OK ||
            (m.permissions & (VM_USER | VM_WRITE)) != (VM_USER | VM_WRITE))
            return (cpu_u64)-1;
        off += chunk;
    }
    return len;
}

cpu_u64 copy_to_user(struct user_context *c, cpu_u64 uaddr,
                     const cpu_u8 *src, cpu_u64 len)
{
    cpu_u64 off = 0;
    if (!c || (len && !src)) return (cpu_u64)-1;
    if (len == 0) return 0;
    /* Pass one: validate the whole range before any byte moves (B2
       mutant: validating only the first page lets a cross-page attack
       through; pass two below would then write past the checked page). */
    if (copy_dest_ok(c, uaddr, len) != len) return (cpu_u64)-1;
    while (off < len) {
        cpu_u64 addr = uaddr + off;
        cpu_u64 page = addr & ~(VM_PAGE_SIZE - 1);
        cpu_u64 chunk = VM_PAGE_SIZE - (addr & (VM_PAGE_SIZE - 1));
        if (chunk > len - off) chunk = len - off;
        /* Re-query per chunk (never trust pass-one across a VM call):
           mappings cannot change mid-call (single CPU, IF=0, no other
           actor maps this space), so this re-check always agrees. */
        struct vm_mapping m;
        if (vm_query(&c->space, page, &m) != VM_OK ||
            (m.permissions & (VM_USER | VM_WRITE)) != (VM_USER | VM_WRITE))
            return (cpu_u64)-1;
        cpu_u64 frame = m.physical & ~(VM_PAGE_SIZE - 1);
        volatile cpu_u8 *w = vm_frame_access(frame);
        if (!w) return (cpu_u64)-1;
        cpu_u64 start = addr & (VM_PAGE_SIZE - 1);
        for (cpu_u64 i = 0; i < chunk; ++i)
            w[start + i] = src[off + i];
        off += chunk;
    }
    return len;
}

/* Staging for validated keyboard/file bytes (single CPU, IF=0 handler
   context only; never shared, never retained across calls). Slice D:
   2-byte aligned so the same buffer stages fs_read chunks (the block
   layer rejects odd buffers); reuse across read/fread is sound because
   handlers never interleave (IF=0, single CPU). */
static _Alignas(2) cpu_u8 read_stage[SYSCALL_READ_MAX];
/* Slice D fread pathname staging (33 = 32-byte cap + NUL). P1-A2
   reuses the same buffer for fcreate/fwrite pathnames: handlers never
   interleave (IF=0, single CPU), and every syscall stages its path
   before any other staging moves. */
static cpu_u8 fread_path[33];
/* P1-A2: sys_fwrite stages payload chunks in read_stage (the link
   budget has no room for a second 4 KiB stage; handler
   non-interleaving makes the sharing sound). write_stage stays
   syscall-2-only: it lacks the 2-byte alignment fs_write needs. */

int sys_read(struct user_context *c, cpu_u64 fd, cpu_u64 buf, cpu_u64 len,
             cpu_u64 nread_out, cpu_u64 flags)
{
    cpu_u64 staged = 0;
    if (!c) return SYS_INVAL;
    /* Scalar validation before any memory touch. fd is full-64-bit
       compared (B3 mutant: truncating to 32 bits would accept
       0x1_00000000 as stdin). */
    if (fd != SYS_STDIN) return SYS_INVAL;
    if (flags != 0) return SYS_INVAL;
    if (len > SYSCALL_READ_MAX) return SYS_INVAL;
    /* Destination capability before any dequeue (A3 mutant: dequeuing
       first would let a hostile nread_out discard user input). */
    if (copy_dest_ok(c, nread_out, sizeof(cpu_u64)) != sizeof(cpu_u64))
        return SYS_INVAL;
    /* Stage 18d Slice D: fd0 pipe-reader routing. Two-stage consumers
       (STDIN_PIPE) read from the kernel-owned pipe; every other caller
       takes the unchanged keyboard/CLOSED path below. */
    {
        int owner = proc_owner_of(c);
        if (owner != PROC_OWNER_KERNEL && owner >= 0) {
            cpu_u64 gen = 0;
            unsigned int stdin_sel = STDIN_KBD, stdout_sel = STDOUT_SERIAL;
            int live = 0;
            if (proc_slot_info((unsigned int)owner, &gen, &stdin_sel,
                               &stdout_sel, &live) && live &&
                stdin_sel == STDIN_PIPE) {
                cpu_u64 n = 0;
                int rc;
                if (len == 0) {
                    cpu_u64 zero = 0;
                    if (copy_to_user(c, nread_out, (const cpu_u8 *)&zero,
                                     sizeof(zero)) != sizeof(zero))
                        return SYS_INVAL;
                    return SYS_OK;
                }
                if (buf + len < buf) return SYS_INVAL;
                if (copy_dest_ok(c, buf, len) != len) return SYS_INVAL;
                /* Stage without dequeuing; dequeue only after both
                   user copies succeed (D-M9: early dequeue loses bytes
                   on a failed copyout). */
                rc = pipe_read_stage((unsigned int)owner, gen, read_stage,
                                     len, &n);
                if (rc == SYS_AGAIN) return SYS_AGAIN;
                if (rc != SYS_OK) return SYS_INVAL;
                if (n > 0) {
                    if (copy_to_user(c, buf, read_stage, n) != n)
                        return SYS_INVAL;
                }
                if (copy_to_user(c, nread_out, (const cpu_u8 *)&n,
                                 sizeof(n)) != sizeof(n))
                    return SYS_INVAL;
                if (n > 0 && !pipe_read_commit(n)) return SYS_INVAL;
                return SYS_OK;
            }
        }
    }
    /* Per-process CLOSED stdin (Slice C spawn selectors) reads EOF
       without touching the shared keyboard stream. Contexts outside any
       process (test probes, bootstrap) always see the keyboard. */
    if (proc_stdin_eof(c)) {
        cpu_u64 zero = 0;
        if (copy_to_user(c, nread_out, (const cpu_u8 *)&zero,
                         sizeof(zero)) != sizeof(zero))
            return SYS_INVAL;
        return SYS_OK;
    }
    if (len == 0) {
        cpu_u64 zero = 0;
        if (copy_to_user(c, nread_out, (const cpu_u8 *)&zero,
                         sizeof(zero)) != sizeof(zero))
            return SYS_INVAL;
        return SYS_OK;
    }
    if (buf + len < buf) return SYS_INVAL;
    if (copy_dest_ok(c, buf, len) != len) return SYS_INVAL;
    /* Stage from the keyboard ring. EMPTY stops (AGAIN); LOST stages one
       0x00 marker byte (never queued by the ISR, so unambiguous) and the
       loop continues with post-gap bytes. */
    while (staged < len) {
        cpu_u8 byte = 0;
        enum kbd_result take = kbd_take(&byte);
        if (take == KBD_EMPTY) break;
        if (take == KBD_LOST) {
            read_stage[staged++] = 0x00;
            continue;
        }
        if (take != KBD_EVENT) return SYS_INVAL;
        read_stage[staged++] = byte;
    }
    if (!staged) return SYS_AGAIN;
    if (copy_to_user(c, buf, read_stage, staged) != staged) return SYS_INVAL;
    /* Publish the count LAST (output-publication rule). */
    if (copy_to_user(c, nread_out, (const cpu_u8 *)&staged,
                     sizeof(staged)) != sizeof(staged))
        return SYS_INVAL;
    return SYS_OK;
}

/* Stage 18d Slice D: stateless file read over a kernel-memory path.
 *
 * kpath is NUL-terminated kernel memory (never a user pointer); kbuf is
 * a kernel buffer of at least len bytes, 2-byte aligned when len > 0;
 * len is bounded by UAPI_FREAD_MAX. Each call opens internally, reads
 * at most len bytes at offset, and closes: no handle escapes. Returns
 * a frozen sys_err code with *nread_out published only on SYS_OK
 * (short at EOF, zero exactly at EOF); every other outcome leaves it
 * untouched. The driver exercises this core directly; sys_fread adds
 * the userspace staging/publication shell. */
int kern_fread(const char *kpath, cpu_u64 offset, cpu_u8 *kbuf,
               cpu_u64 len, cpu_u64 *nread_out)
{
    struct fs_stat st;
    cpu_u32 h = 0;
    cpu_u64 n = 0;
    int rc;
    if (!foreground() || !kpath || !nread_out) return SYS_INVAL;
    if (len > UAPI_FREAD_MAX) return SYS_INVAL;
    if (len > 0 && (!kbuf || ((cpu_u64)kbuf & 1u))) return SYS_INVAL;
    /* Staged-pathname rule: the caller staged the path; semantic
       validation runs on the kernel copy only (D-M2 mutant). */
    if (!fs_path_ok(kpath)) return SYS_BADARG;
    rc = fs_stat(kpath, &st);
    if (rc != FS_OK) return SYS_NOTFOUND;
    if (st.type != FS_TYPE_FILE) return SYS_MALFORMED;
    if (offset > st.size) return SYS_BADARG;
    {
        cpu_u64 avail = st.size - offset;
        n = len < avail ? len : avail;
    }
    if (n == 0) {
        *nread_out = 0;
        return SYS_OK;
    }
    if (fs_open(kpath, &h) != FS_OK) return SYS_NOTFOUND;
    {
        cpu_u64 got = 0;
        while (got < n) {
            cpu_u64 chunk = n - got > SYSCALL_READ_MAX ? SYSCALL_READ_MAX : n - got;
            cpu_u64 m = 0;
            /* Chunk offsets advance monotonically (no wrap: off <= size,
               chunk <= size - off), so every fs_read range is in-bounds
               by construction. */
            if (fs_read(h, offset + got, kbuf + got, chunk, &m) != FS_OK ||
                m != chunk) {
                (void)fs_close(h);
                return SYS_IOERR;
            }
            got += m;
        }
    }
    if (fs_close(h) != FS_OK) return SYS_IOERR;
    *nread_out = n;
    return SYS_OK;
}

/* Stage 18d Slice D, syscall 7: stateless fread.
 * Frozen register file: EBX path_ptr, ECX path_len (1..32), EDX offset,
 * ESI buf, EDI len (<=16384), EBP nread_out. No flags word exists.
 * Validation order (Slice B discipline): scalars -> wrap checks ->
 * stage pathname once -> validate staged path -> validate outputs ->
 * filesystem operation into kernel staging -> copy payload out ->
 * publish count LAST. */
int sys_fread(struct user_context *c, cpu_u64 path_ptr, cpu_u64 path_len,
              cpu_u64 offset, cpu_u64 buf, cpu_u64 len, cpu_u64 nread_out)
{
    cpu_u64 done = 0;
    if (!c) return SYS_INVAL;
    if (!foreground()) return SYS_INVAL;
    if (path_len < 1 || path_len > FS_MAX_PATH) return SYS_BADARG;
    if (len > UAPI_FREAD_MAX) return SYS_INVAL;
    if (path_ptr + path_len < path_ptr) return SYS_BADARG;
    /* Stage the pathname once; every later check runs on the copy. */
    if (copy_from_user(c, fread_path, path_ptr, path_len) != path_len)
        return SYS_BADARG;
    fread_path[path_len] = 0;
    if (!fs_path_ok((const char *)fread_path)) return SYS_BADARG;
    /* Output capability before any filesystem operation (A3: a hostile
       destination must fail here, never after state moves). The data
       buffer is untouched on zero-length calls. */
    if (len > 0) {
        if (buf + len < buf) return SYS_BADARG;
        if (copy_dest_ok(c, buf, len) != len) return SYS_BADARG;
    }
    if (nread_out + sizeof(cpu_u64) < nread_out) return SYS_BADARG;
    if (copy_dest_ok(c, nread_out, sizeof(cpu_u64)) != sizeof(cpu_u64))
        return SYS_BADARG;
    /* Zero length still names a file: validate path, existence, type,
       and offset (never touching buf), then publish zero. */
    if (len == 0) {
        cpu_u64 m = 0;
        cpu_u64 zero = 0;
        int rc = kern_fread((const char *)fread_path, offset, read_stage,
                            0, &m);
        if (rc != SYS_OK) return rc;
        if (copy_to_user(c, nread_out, (const cpu_u8 *)&zero,
                         sizeof(zero)) != sizeof(zero))
            return SYS_INVAL;
        return SYS_OK;
    }
    /* Bounded chunk loop over the 4 KiB kernel stage (no 16 KiB static
       staging against the link budget; each chunk is an independent
       stateless read, so multi-chunk transfers are byte-identical to a
       single call). */
    while (done < len) {
        cpu_u64 chunk = len - done > SYSCALL_READ_MAX ? SYSCALL_READ_MAX : len - done;
        cpu_u64 m = 0;
        int rc = kern_fread((const char *)fread_path, offset + done,
                            read_stage, chunk, &m);
        if (rc != SYS_OK) {
            /* Short reads surface as OK inside kern_fread; any error
               here (NOTFOUND/MALFORMED/BADARG/IOERR) leaves both user
               outputs untouched. BADARG arises only for offset past
               end, which the first chunk reports deterministically. */
            return rc;
        }
        if (m > 0) {
            if (copy_to_user(c, buf + done, read_stage, m) != m)
                return SYS_INVAL;
            done += m;
        }
        if (m < chunk) break; /* EOF: no further bytes exist. */
        if (done >= len) break;
        /* offset + done cannot wrap: done <= len <= 16K and the file
           offset advanced monotonically inside kern_fread. */
    }
    if (copy_to_user(c, nread_out, (const cpu_u8 *)&done,
                     sizeof(done)) != sizeof(done))
        return SYS_INVAL;
    return SYS_OK;
}

/* P1-A2 Slice P1-A: stateless file create over a kernel-memory path.
 *
 * kpath is NUL-terminated kernel memory (never a user pointer). The
 * call creates a zero-length file and persists its directory entry
 * before returning; on any failure no entry is published (the P1-A1
 * fs_create contract). Returns a frozen sys_err code. Mapping table
 * (fs_result -> sys_err; every fs_create outcome is classified):
 *   FS_OK       -> SYS_OK
 *   FS_EXISTS   -> SYS_EXISTS (target present, any type, incl. root)
 *   FS_NOSPC    -> SYS_NOSPC (directory full; create takes no data)
 *   FS_NOTFOUND -> SYS_NOTFOUND (missing parent names nothing)
 *   FS_NOTDIR   -> SYS_MALFORMED (a file on the parent chain: a type
 *                  mismatch, mirroring kern_fread's MALFORMED use)
 *   FS_INVALID  -> SYS_IOERR (the staged path already passed
 *                  fs_path_ok, so INVALID here means no filesystem is
 *                  mounted: storage unavailable, not a bad argument)
 *   FS_IOERR / FS_CORRUPT / anything else -> SYS_IOERR (device or
 *                  image failure; never a user-argument class).
 * The driver exercises this core directly; sys_fcreate adds the
 * userspace staging shell. */
int kern_fcreate(const char *kpath)
{
    int rc;
    if (!foreground() || !kpath) return SYS_INVAL;
    /* Staged-pathname rule: the caller staged the path; semantic
       validation runs on the kernel copy only (D-M2 mutant). */
    if (!fs_path_ok(kpath)) return SYS_BADARG;
    rc = fs_create(kpath);
    if (rc == FS_OK) return SYS_OK;
    if (rc == FS_EXISTS) return SYS_EXISTS;
    if (rc == FS_NOSPC) return SYS_NOSPC;
    if (rc == FS_NOTFOUND) return SYS_NOTFOUND;
    if (rc == FS_NOTDIR) return SYS_MALFORMED;
    if (rc == FS_INVALID) return SYS_IOERR;
    return SYS_IOERR;
}

/* P1-A2 Slice P1-A, syscall 9: stateless fcreate.
 * Frozen register file: EBX path_ptr, ECX path_len (1..32); RDX, RSI,
 * RDI, RBP reserved (the dispatcher rejects nonzero words with INVAL
 * per G2, mirroring the spawn/wait/terminate shape). Validation order
 * mirrors sys_fread: scalars -> wrap check -> stage pathname once ->
 * call the kernel-memory core on the staged copy. */
int sys_fcreate(struct user_context *c, cpu_u64 path_ptr, cpu_u64 path_len)
{
    if (!c) return SYS_INVAL;
    if (!foreground()) return SYS_INVAL;
    if (path_len < 1 || path_len > FS_MAX_PATH) return SYS_BADARG;
    if (path_ptr + path_len < path_ptr) return SYS_BADARG;
    /* Stage the pathname once; every later check runs on the copy. */
    if (copy_from_user(c, fread_path, path_ptr, path_len) != path_len)
        return SYS_BADARG;
    fread_path[path_len] = 0;
    return kern_fcreate((const char *)fread_path);
}

/* P1-A2 Slice P1-A: stateless file write over a kernel-memory path.
 *
 * kpath is NUL-terminated kernel memory (never a user pointer); kbuf
 * is a kernel buffer of at least len bytes, 2-byte aligned when
 * len > 0; len is bounded by UAPI_FWRITE_MAX. Each call stats,
 * opens internally, performs exactly one fs_write, and closes: no
 * handle escapes. Returns a frozen sys_err code with *nwritten_out
 * published only on SYS_OK (always == len: OK means every byte
 * landed); every other outcome leaves it untouched, mirroring
 * kern_fread's output discipline.
 * Differences from kern_fread (all required by write semantics):
 * - No internal chunk loop: fs_write already accepts the full
 *   <=16384 batch in one call (FS_MAX_WRITE_BYTES); sys_fwrite
 *   chunks at the 4 KiB staging bound instead.
 * - offset > size is BADARG for every len including 0: the P1-A1
 *   hole rejection reaches the syscall unchanged (no sparse files).
 * - Growth failures surface as SYS_NOSPC (no free extent); a failed
 *   relocation reports *nwritten == 0 with the old entry intact
 *   (data-before-directory), so SYS_NOSPC/SYS_IOERR from a growing
 *   write never publish a count.
 * fs_write outcome mapping: FS_OK -> SYS_OK; FS_NOSPC -> SYS_NOSPC;
 * FS_RANGE -> SYS_BADARG (defensive: the offset pre-check below
 * already rejects past-end writes; size cannot move mid-call);
 * FS_INVALID -> SYS_BADARG (length/cap/buffer pre-checked, so only
 * the offset+len end-wrap on an absurd on-disk size remains);
 * FS_NOTFILE -> SYS_MALFORMED and FS_BADHANDLE -> SYS_BADHANDLE
 * (both defensive: type and handle were just established);
 * FS_IOERR / FS_CORRUPT / anything else -> SYS_IOERR.
 * The driver exercises this core directly (including fault
 * injection); sys_fwrite adds the userspace staging shell. */
int kern_fwrite(const char *kpath, cpu_u64 offset, const cpu_u8 *kbuf,
                cpu_u64 len, cpu_u64 *nwritten_out)
{
    struct fs_stat st;
    cpu_u32 h = 0;
    cpu_u64 m = 0;
    int rc;
    if (!foreground() || !kpath || !nwritten_out) return SYS_INVAL;
    if (len > UAPI_FWRITE_MAX) return SYS_INVAL;
    if (len > 0 && (!kbuf || ((cpu_u64)kbuf & 1u))) return SYS_INVAL;
    /* Staged-pathname rule: the caller staged the path; semantic
       validation runs on the kernel copy only (D-M2 mutant). */
    if (!fs_path_ok(kpath)) return SYS_BADARG;
    rc = fs_stat(kpath, &st);
    if (rc != FS_OK) return SYS_NOTFOUND;
    if (st.type != FS_TYPE_FILE) return SYS_MALFORMED;
    /* Hole rejection at the syscall boundary: offset past end fails
       even for zero-length writes (P1-A1 contract, unchanged). */
    if (offset > st.size) return SYS_BADARG;
    if (len == 0) {
        *nwritten_out = 0;
        return SYS_OK;
    }
    if (fs_open(kpath, &h) != FS_OK) return SYS_NOTFOUND;
    rc = fs_write(h, offset, kbuf, len, &m);
    if (rc != FS_OK || m != len) {
        (void)fs_close(h);
        if (rc == FS_NOSPC) return SYS_NOSPC;
        if (rc == FS_RANGE || rc == FS_INVALID) return SYS_BADARG;
        if (rc == FS_NOTFILE) return SYS_MALFORMED;
        if (rc == FS_BADHANDLE) return SYS_BADHANDLE;
        return SYS_IOERR;
    }
    if (fs_close(h) != FS_OK) return SYS_IOERR;
    *nwritten_out = m;
    return SYS_OK;
}

/* P1-A2 Slice P1-A, syscall 10: stateless fwrite.
 * Frozen register file: EBX path_ptr, ECX path_len (1..32), EDX
 * offset, ESI buf, EDI len (<=16384), EBP nwritten_out. No flags
 * word exists (all six argument registers used, like fread).
 * Validation order (Slice B discipline, mirrored from sys_fread):
 * scalars -> wrap checks -> stage pathname once -> validate staged
 * path -> validate the count output before any filesystem operation
 * -> per chunk: copy payload in, then write -> publish count LAST.
 * Each chunk is copied from userspace before its disk write, so no
 * disk byte moves on unvalidated user memory; a hostile later chunk
 * fails the call with the count untouched (earlier landed chunks are
 * real file bytes: the file stays coherent, exactly like a short
 * POSIX write, and OK still means every byte landed). */
int sys_fwrite(struct user_context *c, cpu_u64 path_ptr, cpu_u64 path_len,
               cpu_u64 offset, cpu_u64 buf, cpu_u64 len,
               cpu_u64 nwritten_out)
{
    cpu_u64 done = 0;
    if (!c) return SYS_INVAL;
    if (!foreground()) return SYS_INVAL;
    if (path_len < 1 || path_len > FS_MAX_PATH) return SYS_BADARG;
    if (len > UAPI_FWRITE_MAX) return SYS_INVAL;
    if (path_ptr + path_len < path_ptr) return SYS_BADARG;
    /* Stage the pathname once; every later check runs on the copy. */
    if (copy_from_user(c, fread_path, path_ptr, path_len) != path_len)
        return SYS_BADARG;
    fread_path[path_len] = 0;
    if (!fs_path_ok((const char *)fread_path)) return SYS_BADARG;
    /* Output capability before any filesystem operation (A3: a hostile
       destination must fail here, never after state moves). */
    if (nwritten_out + sizeof(cpu_u64) < nwritten_out) return SYS_BADARG;
    if (copy_dest_ok(c, nwritten_out, sizeof(cpu_u64)) != sizeof(cpu_u64))
        return SYS_BADARG;
    /* Zero length still names a file: validate path, existence, type,
       and offset (never touching buf), then publish zero. */
    if (len == 0) {
        cpu_u64 m = 0;
        cpu_u64 zero = 0;
        int rc = kern_fwrite((const char *)fread_path, offset, read_stage,
                             0, &m);
        if (rc != SYS_OK) return rc;
        if (copy_to_user(c, nwritten_out, (const cpu_u8 *)&zero,
                         sizeof(zero)) != sizeof(zero))
            return SYS_INVAL;
        return SYS_OK;
    }
    if (buf + len < buf) return SYS_BADARG;
    /* Bounded chunk loop over the 4 KiB kernel stage (no 16 KiB static
       staging against the link budget; each chunk is an independent
       stateless write, so multi-chunk transfers are byte-identical to
       sequential single-chunk calls). */
    while (done < len) {
        cpu_u64 chunk = len - done > SYSCALL_READ_MAX ? SYSCALL_READ_MAX : len - done;
        cpu_u64 m = 0;
        int rc;
        /* offset + done cannot wrap on a reached iteration: chunk 1
           starts at done == 0, and every later iteration follows a
           successful chunk whose end (offset + done) fs_write proved
           un-wrapped; the explicit check below still guards the
           arithmetic before the offset is trusted. */
        if (offset + done < offset || offset + done < done) return SYS_BADARG;
        if (copy_from_user(c, read_stage, buf + done, chunk) != chunk)
            return SYS_BADARG;
        rc = kern_fwrite((const char *)fread_path, offset + done,
                         read_stage, chunk, &m);
        if (rc != SYS_OK) {
            /* Any error here (NOTFOUND/MALFORMED/BADARG/NOSPC/IOERR)
               leaves both user outputs untouched; earlier landed
               chunks are coherent file bytes (documented short-write
               behavior), and only SYS_OK publishes a count. */
            return rc;
        }
        if (m != chunk) return SYS_IOERR;
        done += m;
        if (done >= len) break;
    }
    if (copy_to_user(c, nwritten_out, (const cpu_u8 *)&done,
                     sizeof(done)) != sizeof(done))
        return SYS_INVAL;
    return SYS_OK;
}

static void text(const char *s)
{
    /* Evidence printing; the caller guarantees a working serial. */
    (void)serial_write(s);
}

static void number(cpu_u64 n)
{
    char b[21];
    unsigned int i = 20;
    b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    text(b + i);
}

void sys_write_evidence(struct user_context *c, cpu_u64 fd, cpu_u64 len,
                         cpu_u64 nwritten)
{
    static const char digits[] = "0123456789abcdef";
    /* Stage 18d Slice D: pipe writes carry no per-call evidence row
       (streaming throughput would flood the transcript); driver
       evidence comes from pipe snapshots. Serial rows are unchanged. */
    {
        int owner = proc_owner_of(c);
        if (owner != PROC_OWNER_KERNEL && owner >= 0) {
            cpu_u64 gen = 0;
            unsigned int stdin_sel = STDIN_KBD, stdout_sel = STDOUT_SERIAL;
            int live = 0;
            if (proc_slot_info((unsigned int)owner, &gen, &stdin_sel,
                               &stdout_sel, &live) && live &&
                stdout_sel == STDOUT_PIPE)
                return;
        }
    }
    text("[LOAD] write slot=");
    number(c->slot);
    text(" fd=");
    number(fd);
    text(" len=");
    number(len);
    text(" nwritten=");
    number(nwritten);
    text(" hex=");
    for (cpu_u64 i = 0; i < nwritten; ++i) {
        char pair[3];
        pair[0] = digits[(write_stage[i] >> 4) & 15u];
        pair[1] = digits[write_stage[i] & 15u];
        pair[2] = 0;
        text(pair);
    }
    text("\r\n");
}
