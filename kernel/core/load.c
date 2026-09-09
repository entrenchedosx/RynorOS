#include "load.h"
#include "syscall.h"
#include "ksched.h"
#include "irq.h"
#include "io.h"
#include "vm.h"
#include "kbd.h"
#include "serial.h"
#include "proc.h"

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

/* Staging for validated keyboard bytes (single CPU, IF=0 handler context
   only; never shared, never retained across calls). */
static cpu_u8 read_stage[SYSCALL_READ_MAX];

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
