#include "user.h"
#include "ksched.h"
#include "irq.h"
#include "io.h"
#include "serial.h"
#include "paging.h"
#include "syscall.h"
#include "load.h"

/* Stage 18a protected userspace. Static model: fixed layout, two
   contexts, shared-half address spaces, int $0x80 exit/yield gate.
   Design contract: docs/design/userspace.md. */

cpu_u64 user_kernel_cr3 = 0;
static int initialized;
static int smep_present, smap_present;
static cpu_u64 cpl3_ticks;

static struct user_context contexts[USER_MAX_CONTEXTS];
static cpu_u8 exit_stacks[USER_MAX_CONTEXTS][USER_EXIT_STACK_BYTES]
    __attribute__((aligned(16)));

extern const char user_blob_exit[], user_blob_exit_end[];
extern const char user_blob_spin[], user_blob_spin_end[];
extern const char user_blob_yield[], user_blob_yield_end[];
extern const char user_blob_ud2[], user_blob_ud2_end[];
extern const char user_blob_readkern_lo[], user_blob_readkern_lo_end[];
extern const char user_blob_readkern_hi[], user_blob_readkern_hi_end[];
extern const char user_blob_write_rx[], user_blob_write_rx_end[];
extern const char user_blob_exec_data[], user_blob_exec_data_end[];
extern const char user_blob_cli[], user_blob_cli_end[];
extern const char user_blob_null[], user_blob_null_end[];
extern const char user_blob_badcall[], user_blob_badcall_end[];
extern const char user_blob_readkern_text[], user_blob_readkern_text_end[];
extern const char user_blob_writekern_data[], user_blob_writekern_data_end[];
extern const char user_blob_execkern_text[], user_blob_execkern_text_end[];
extern const char user_blob_execstack[], user_blob_execstack_end[];
extern const char user_blob_readcr3[], user_blob_readcr3_end[];
extern const char user_blob_kernsel[], user_blob_kernsel_end[];
extern const char user_blob_badsel[], user_blob_badsel_end[];
extern const char user_blob_tibit[], user_blob_tibit_end[];
extern const char user_blob_farjmp_kcs[], user_blob_farjmp_kcs_end[];
extern const char user_blob_farjmp_udata[], user_blob_farjmp_udata_end[];
extern const char user_blob_movss[], user_blob_movss_end[];
extern const char user_blob_divzero[], user_blob_divzero_end[];
extern const char user_blob_syscall[], user_blob_syscall_end[];
extern const char user_blob_ss_rsp[], user_blob_ss_rsp_end[];
extern const char user_blob_kern_rsp[], user_blob_kern_rsp_end[];
extern const char user_blob_iretq_kcs[], user_blob_iretq_kcs_end[];
extern const char user_blob_retfq_kcs[], user_blob_retfq_kcs_end[];
extern const char user_blob_rdmsr[], user_blob_rdmsr_end[];
extern cpu_u64 user_enter_asm(struct exception_frame *, struct exception_frame *, cpu_u64);

static void panic(const char *why) __attribute__((noreturn));
static void panic(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[USER] failure="); (void)serial_write(why);
    (void)serial_write("\r\n"); (void)serial_flush(); cpu_halt();
}
static void require(int ok, const char *why) { if (!ok) panic(why); }
static void text(const char *s) { require(serial_write(s), "serial"); }
static void number(cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    text(b + i);
}
static void field(const char *s, cpu_u64 n) { text(s); number(n); }
static void hex(cpu_u64 v)
{
    char b[19] = "0x0000000000000000";
    for (unsigned int n = 0; n < 16; ++n) b[17 - n] = "0123456789abcdef"[(v >> (n * 4)) & 15];
    text(b);
}
static int foreground(void) { return cpu_interrupts_disabled() && !irq_in_context(); }
static cpu_u64 read_cr3(void)
{
    cpu_u64 v;
    __asm__ volatile ("mov %%cr3,%0" : "=r"(v));
    return v;
}
static void write_cr3(cpu_u64 v) { __asm__ volatile ("mov %0,%%cr3" : : "r"(v) : "memory"); }
/* Switch to the kernel address space once entry validation passed.
   The frame window (slot 511) and the MMIO slot exist only in the
   kernel half, so no VM helper may run on a user CR3. Pure validation
   (origin, vector/error, RSP0) always precedes this switch. */
static void user_to_kernel(void) { write_cr3(user_kernel_cr3); }

static void cpuid(cpu_u32 leaf, cpu_u32 sub, cpu_u32 *a, cpu_u32 *b, cpu_u32 *c, cpu_u32 *d)
{
    __asm__ volatile ("cpuid" : "=a"(*a), "=b"(*b), "=c"(*c), "=d"(*d) : "a"(leaf), "c"(sub));
}

static void rdmsr(cpu_u32 msr, cpu_u32 *lo, cpu_u32 *hi)
{
    __asm__ volatile ("rdmsr" : "=a"(*lo), "=d"(*hi) : "c"(msr));
}

/* No fast-syscall backdoor, checked at init and re-checked wherever
   the address-space shape is revalidated: a set SCE or SYSENTER_CS
   after boot fails closed here instead of exposing an unvetted
   CPL3->CPL0 entry. */
static int msr_mediation_ok(void)
{
    cpu_u32 a, b, c, d, lo, hi;
    rdmsr(0xc0000080u, &lo, &hi);
    if (lo & 1u) return 0;
    cpuid(1, 0, &a, &b, &c, &d);
    if (d & (1u << 11)) {
        rdmsr(0x174u, &lo, &hi);
        if (lo != 0 || hi != 0) return 0;
    }
    return 1;
}

/* Packed 10-byte GDTR/IDTR image for SGDT/SIDT verification reads. */
struct table_register {
    cpu_u16 limit;
    cpu_u64 base;
} __attribute__((packed));

static void probe(void)
{
    cpu_u32 a, b, c, d;
    cpuid(7, 0, &a, &b, &c, &d);
    int smep = (b >> 7) & 1, smap = (b >> 20) & 1;
    cpuid(7, 0, &a, &b, &c, &d);
    require(((b >> 7) & 1) == smep && ((b >> 20) & 1) == smap, "cpuid_unstable");
    smep_present = smep; smap_present = smap;
    cpu_u64 cr4;
    __asm__ volatile ("mov %%cr4,%0" : "=r"(cr4));
    /* 18a never enables either feature; enforcement is an 18d concern. */
    require(!(cr4 & ((1ULL << 20) | (1ULL << 21))), "cr4_smep_smap");
    /* No alternate CPL3->CPL0 entry may exist: SYSCALL/SYSRET needs
       EFER.SCE (bit 0), SYSENTER needs a nonzero SYSENTER_CS. Neither is
       ever programmed here; firmware leaving them armed fails closed.
       (With SCE clear, SYSCALL raises #UD; with CS zero, SYSENTER faults
       instead of entering. SYSCALL is exercised as a CPL3 attack blob.) */
    require(msr_mediation_ok(), "msr_mediation");
}

static const struct { const char *start, *end; } blobs[] = {
    {user_blob_exit, user_blob_exit_end}, {user_blob_spin, user_blob_spin_end},
    {user_blob_yield, user_blob_yield_end}, {user_blob_ud2, user_blob_ud2_end},
    {user_blob_readkern_lo, user_blob_readkern_lo_end},
    {user_blob_readkern_hi, user_blob_readkern_hi_end},
    {user_blob_write_rx, user_blob_write_rx_end},
    {user_blob_exec_data, user_blob_exec_data_end},
    {user_blob_cli, user_blob_cli_end}, {user_blob_null, user_blob_null_end},
    {user_blob_badcall, user_blob_badcall_end},
    {user_blob_readkern_text, user_blob_readkern_text_end},
    {user_blob_writekern_data, user_blob_writekern_data_end},
    {user_blob_execkern_text, user_blob_execkern_text_end},
    {user_blob_execstack, user_blob_execstack_end},
    {user_blob_readcr3, user_blob_readcr3_end},
    {user_blob_kernsel, user_blob_kernsel_end},
    {user_blob_badsel, user_blob_badsel_end},
    {user_blob_tibit, user_blob_tibit_end},
    {user_blob_farjmp_kcs, user_blob_farjmp_kcs_end},
    {user_blob_farjmp_udata, user_blob_farjmp_udata_end},
    {user_blob_movss, user_blob_movss_end},
    {user_blob_divzero, user_blob_divzero_end},
    {user_blob_syscall, user_blob_syscall_end},
    {user_blob_ss_rsp, user_blob_ss_rsp_end},
    {user_blob_kern_rsp, user_blob_kern_rsp_end},
    {user_blob_iretq_kcs, user_blob_iretq_kcs_end},
    {user_blob_retfq_kcs, user_blob_retfq_kcs_end},
    {user_blob_rdmsr, user_blob_rdmsr_end},
};
#define USER_BLOB_COUNT ((unsigned int)(sizeof(blobs) / sizeof(blobs[0])))

static int frame_within_exit(const struct user_context *c, const struct exception_frame *f)
{
    cpu_u64 a = (cpu_u64)f;
    return a >= c->exit_base && a <= c->exit_top - sizeof(*f);
}

/* Hardware-built origin checks shared by all CPL3 paths. Vector/error
   expectations differ per path (gate/fault/IRQ). RFLAGS allows RF: fault
   delivery preserves it (observed set on #UD), and it is harmless with
   no debug active. TF/AC/ID/IOPL/NT/VM/VIF/VIP stay forbidden. */
static int origin_valid(const struct exception_frame *f)
{
    return f->cs == CPU_USER_CODE_SELECTOR && f->ss == CPU_USER_DATA_SELECTOR &&
           vm_canonical(f->rip) && vm_canonical(f->rsp) &&
           f->rip >= USER_CODE_BASE && f->rip < USER_CODE_BASE + VM_PAGE_SIZE &&
           f->rsp > USER_STACK_PAGE && f->rsp <= USER_STACK_TOP && !(f->rsp & 7) &&
           (f->rflags & 0x202) == 0x202 && !(f->rflags & ~0x10ED7ULL);
}

/* Fault-path origin: CS/SS/RIP/RFLAGS only. An instruction-fetch fault
   reports the target as RIP, legitimately outside the code page; the
   per-case test asserts the exact expected RIP instead. RSP is
   deliberately UNCHECKED here (not even canonical): a faulted context
   is never resumed (user_resume refuses non-ACTIVE), its recorded RSP
   is never trusted for any stack switch (exit stacks are static), and
   it is never printed. A corrupted user stack therefore records a kill
   instead of halting the kernel. The gate and IRQ paths keep the
   strict RSP range check because their frames' RSP is trusted for
   resume/yield accounting. */
static int origin_valid_fault(const struct exception_frame *f)
{
    return f->cs == CPU_USER_CODE_SELECTOR && f->ss == CPU_USER_DATA_SELECTOR &&
           vm_canonical(f->rip) &&
           (f->rflags & 0x202) == 0x202 && !(f->rflags & ~0x10ED7ULL);
}

int user_fault_managed(cpu_u64 vector)
{    return vector == 0 || vector == 4 || vector == 5 || vector == 6 ||
           vector == 7 || (vector >= 10 && vector <= 14) || vector == 16 ||
           vector == 17 || vector == 19;
}

static int has_hw_error(cpu_u64 vector)
{
    return vector == 10 || vector == 11 || vector == 12 || vector == 13 ||
           vector == 14 || vector == 17;
}

static void record_state(struct user_context *c, const struct exception_frame *f)
{
    c->gprs[0] = f->rax; c->gprs[1] = f->rbx; c->gprs[2] = f->rcx;
    c->gprs[3] = f->rdx; c->gprs[4] = f->rsi; c->gprs[5] = f->rdi;
    c->gprs[6] = f->rbp; c->gprs[7] = f->r8; c->gprs[8] = f->r9;
    c->gprs[9] = f->r10; c->gprs[10] = f->r11; c->gprs[11] = f->r12;
    c->gprs[12] = f->r13; c->gprs[13] = f->r14; c->gprs[14] = f->r15;
    c->rip = f->rip; c->rsp = f->rsp; c->rflags = f->rflags;
}

int user_origin_ok(struct user_context *c, struct exception_frame *f)
{
    if (!c || c->state != USER_ACTIVE || !c->space.identity ||
        c->space.identity != &c->space || !c->space.root) return 0;
    return frame_within_exit(c, f) && origin_valid(f);
}

int user_save_state(struct user_context *c, struct exception_frame *f)
{
    /* Validate BEFORE switching CR3: with a desynced RSP0 the frame may
       sit on a stack that is only mapped in the current (user) address
       space, so every pure check runs first and failure returns with
       the entry stack still valid for a clean diagnostic halt. */
    if (!user_origin_ok(c, f)) return 0;
    if (f->vector < IRQ_BASE || f->vector >= IRQ_BASE + IRQ_COUNT || f->error != 0)
        return 0;
    /* The CPU loaded RSP0 on entry; it must be this context's exit top.
       Anything else means a desynced TSS and an untrusted stack. */
    if (cpu_get_rsp0() != c->exit_top) return 0;
    if (!user_kernel_cr3) return 0;
    write_cr3(user_kernel_cr3);
    record_state(c, f);
    ++c->preemptions;
    return 1;
}

/* Publish the stop flag into the current user data page. Call ONLY on
   the user CR3 in IRQ context right after user_origin_ok passed: the
   data page is unconditionally mapped for every ACTIVE context, and the
   constant VA cannot be steered by frame contents. Supervisor store to
   a user page is legal with SMAP off. */
int user_publish_stop(struct user_context *c, struct exception_frame *f)
{
    /* Loaded programs own their data page: offset 0 is program data,
       never USER_DATA_STOP. Refuse fail-closed so a missed caller gate
       halts loudly instead of corrupting the image. */
    if (c && c->loaded) return 0;
    if (!user_origin_ok(c, f)) return 0;
    /* The fixed-VA store is only valid on this context's user address
       space. The sole caller runs pre-switch, but never trust call order:
       on the kernel CR3 this VA is identity-mapped kernel memory. */
    if (read_cr3() != c->space.root) return 0;
    *(volatile cpu_u64 *)USER_DATA_BASE = 1;
    return 1;
}

/* CPL3 tick accounting for the deterministic preemption test. True from
   the flag tick on: the caller then publishes the stop flag. */
int user_note_cpl3_tick(void)
{
    ++cpl3_ticks;
    return cpl3_ticks >= USER_FLAG_TICKS;
}
cpu_u64 user_cpl3_ticks(void) { return cpl3_ticks; }

static int replica_ok(const struct user_context *c);

static int clone_ok(const struct user_context *c)
{
    struct vm_space *k = vm_kernel_space();
    if (!k) return 0;
    volatile cpu_u64 *kw = vm_frame_access(k->root);
    if (!kw) return 0;
    cpu_u64 hi[253], k0 = kw[0];
    for (unsigned int i = 0; i < 253; ++i) hi[i] = kw[256 + i];
    volatile cpu_u64 *uw = vm_frame_access(c->space.root);
    if (!uw) return 0;
    /* Slot 0 is a private replica (same leaves, different table). */
    if (!uw[0] || uw[0] == k0) return 0;
    /* No other low-half slot may exist: user mappings live under
       PML4[0] only, and future low-half maps have no business in a
       user root. */
    for (unsigned int i = 1; i < 256; ++i)
        if (uw[i]) return 0;
    for (unsigned int i = 0; i < 253; ++i)
        if (uw[256 + i] != hi[i]) return 0;
    for (unsigned int i = 253; i < 256; ++i)
        if (uw[256 + i]) return 0;
    return replica_ok(c);
}

/* Window staging for the replica walk below. Foreground-only (IF=0);
   values are copied out before any vm_query switches the window. */
static cpu_u64 rep_pd[512], rep_pt[512], rep_pdpt[512];

/* No user-accessible leaf may exist anywhere under kernel PML4
   256..508: every such leaf is copied verbatim into all user address
   spaces, so a single U leaf would be a persistent CPL3 window into
   shared kernel tables. Intermediates legitimately carry U (table
   walks need it); only leaves grant access, so only leaves are
   checked. Slots 509..511 (frame window, MMIO) are never copied and
   out of scope here. */
static int high_ok(void)
{
    struct vm_space *k = vm_kernel_space();
    if (!k) return 0;
    volatile cpu_u64 *w = vm_frame_access(k->root);
    if (!w) return 0;
    for (unsigned int i = 0; i < 512; ++i) rep_pd[i] = w[i];
    for (unsigned int i = 256; i < 509; ++i) {
        cpu_u64 e = rep_pd[i];
        if (!e) continue;
        if (e & PTE_HUGE) {
            if (e & PTE_USER) return 0;
            continue;
        }
        if ((e & ~(PTE_ADDRESS | PTE_ACCESS)) != PTE_TABLE_FLAGS) return 0;
        w = vm_frame_access(e & PTE_ADDRESS);
        if (!w) return 0;
        for (unsigned int j = 0; j < 512; ++j) rep_pdpt[j] = w[j];
        for (unsigned int j = 0; j < 512; ++j) {
            e = rep_pdpt[j];
            if (!e) continue;
            if (e & PTE_HUGE) {
                if (e & PTE_USER) return 0;
                continue;
            }
            if ((e & ~(PTE_ADDRESS | PTE_ACCESS)) != PTE_TABLE_FLAGS) return 0;
            w = vm_frame_access(e & PTE_ADDRESS);
            if (!w) return 0;
            for (unsigned int t = 0; t < 512; ++t) rep_pt[t] = w[t];
            for (unsigned int t = 0; t < 512; ++t) {
                e = rep_pt[t];
                if (!e) continue;
                if (e & PTE_HUGE) {
                    if (e & PTE_USER) return 0;
                    continue;
                }
                if ((e & ~(PTE_ADDRESS | PTE_ACCESS)) != PTE_TABLE_FLAGS) return 0;
                w = vm_frame_access(e & PTE_ADDRESS);
                if (!w) return 0;
                for (unsigned int m = 0; m < 512; ++m)
                    if (w[m] & PTE_USER) return 0;
            }
        }
    }
    return 1;
}

/* Audit every leaf of the private low replica against the kernel: each
   present replica leaf must equal the kernel's leaf at the same VA
   (same frame, same W/X, supervisor-only, A/D ignored), except the
   three user pages, which must carry exactly their user permissions.
   Missing replica leaves fail safe (unmapped faults); extra or altered
   leaves fail here. Intermediate form matches vm.c table checks. */
static int replica_ok(const struct user_context *c)
{
    struct vm_space *k = vm_kernel_space();
    if (!k) return 0;
    volatile cpu_u64 *w = vm_frame_access(c->space.root);
    if (!w) return 0;
    if ((w[0] & ~(PTE_ADDRESS | PTE_ACCESS)) != PTE_TABLE_FLAGS) return 0;
    cpu_u64 pdpt = w[0] & PTE_ADDRESS;
    w = vm_frame_access(pdpt);
    if (!w) return 0;
    for (unsigned int j = 0; j < 512; ++j)
        if (j && w[j]) return 0; /* Only PDPT[0] exists (see vm_clone_low). */
    if ((w[0] & ~(PTE_ADDRESS | PTE_ACCESS)) != PTE_TABLE_FLAGS) return 0;
    cpu_u64 pd = w[0] & PTE_ADDRESS;
    w = vm_frame_access(pd);
    if (!w) return 0;
    for (unsigned int j = 0; j < 512; ++j) rep_pd[j] = w[j];
    for (unsigned int j = 0; j < 512; ++j) {
        cpu_u64 pde = rep_pd[j];
        if (!pde) continue;
        if ((pde & ~(PTE_ADDRESS | PTE_ACCESS)) != PTE_TABLE_FLAGS) return 0;
        w = vm_frame_access(pde & PTE_ADDRESS);
        if (!w) return 0;
        for (unsigned int i = 0; i < 512; ++i) rep_pt[i] = w[i];
        for (unsigned int i = 0; i < 512; ++i) {
            cpu_u64 le = rep_pt[i];
            if (!le) continue;
            cpu_u64 va = ((cpu_u64)j << 21) | ((cpu_u64)i << 12);
            cpu_u64 frame = 0;
            unsigned int uperm = 0;
            if (va == USER_CODE_BASE) {
                frame = c->code_frame; uperm = VM_USER | VM_EXECUTE;
            } else if (va == USER_DATA_BASE) {
                frame = c->data_frame; uperm = VM_USER | VM_WRITE;
            } else if (va == USER_STACK_PAGE) {
                frame = c->stack_frame; uperm = VM_USER | VM_WRITE;
            }
            if (uperm) {
                cpu_u64 want = frame | PTE_PRESENT | PTE_USER;
                if (uperm & VM_WRITE) want |= PTE_WRITE;
                if (!(uperm & VM_EXECUTE)) want |= PTE_NX;
                if ((le & ~(PTE_ACCESS | PTE_DIRTY)) != want) return 0;
                continue;
            }
            /* Kernel leaf: supervisor-only and value-equal to the
               kernel's own mapping (huge/foreign/permission drift
               fails here, never silently). */
            if ((le & (PTE_USER | PTE_HUGE))) return 0;
            struct vm_mapping m;
            if (vm_query(k, va, &m) != VM_OK) return 0;
            cpu_u64 want = (m.physical & PTE_ADDRESS) | PTE_PRESENT;
            if (m.permissions & VM_WRITE) want |= PTE_WRITE;
            if (!(m.permissions & VM_EXECUTE)) want |= PTE_NX;
            if ((le & ~(PTE_ACCESS | PTE_DIRTY)) != want) return 0;
        }
    }
    return 1;
}

static int query_exact(struct user_context *c, cpu_u64 va, unsigned int perm)
{
    struct vm_mapping m;
    if (vm_query(&c->space, va, &m) != VM_OK) return 0;
    if (m.permissions != perm) return 0;
    enum pmm_state state;
    cpu_u64 base = m.physical & ~(VM_PAGE_SIZE - 1);
    if (pmm_query(base, &state) != PMM_OK || state != PMM_STATE_ALLOCATED) return 0;
    return (int)(base + (va & (VM_PAGE_SIZE - 1)) == m.physical);
}

int user_check(void)
{
    if (!cpu_interrupts_disabled() || !initialized) return 0;
    struct table_register gdtr;
    __asm__ volatile ("sgdt %0" : "=m"(gdtr));
    if (gdtr.limit != 7 * 8 - 1) return 0;
    /* The table the CPU loads must be ours, not an alias with the same
       checked words: compare the GDTR base against the code immediate. */
    if (gdtr.base != cpu_gdt_base()) return 0;
    volatile cpu_u64 *gdt = (volatile cpu_u64 *)gdtr.base;
    /* Strict allowlist, not a spot check: kernel descriptors pin CPL0
       (a DPL flip would hand CPL3 kernel selectors), user descriptors
       pin DPL3. */
    if (gdt[0] != 0 || gdt[1] != 0x00af9b000000ffffULL ||
        gdt[2] != 0x00cf93000000ffffULL ||
        gdt[3] != 0x00cff3000000ffffULL || gdt[4] != 0x00affb000000ffffULL)
        return 0;
    /* TSS type carries the Accessed bit the CPU wrote back on LTR; the
       base must be the real TSS or CPL3->CPL0 transitions would load
       RSP0 from attacker memory. G/AVL/limit-high must stay zero. */
    cpu_u64 tss_base = ((gdt[5] >> 16) & 0xffff) | ((gdt[5] >> 32) & 0xff) << 16 |
                       ((gdt[5] >> 56) & 0xff) << 24 | gdt[6] << 32;
    if ((gdt[5] & 0xffff) != 103 || ((gdt[5] >> 40) & 0xff) != 0x8b ||
        ((gdt[5] >> 48) & 0xff) != 0 || tss_base != cpu_tss_base())
        return 0;
    cpu_u16 tr;
    __asm__ volatile ("str %0" : "=r"(tr));
    if (tr != CPU_TSS_SELECTOR) return 0;
    /* No LDT may exist (see cpu_load_gdt): TI-bit selectors must fault
       on the explicitly invalid LDTR, never resolve through a table. */
    cpu_u16 ldtr;
    __asm__ volatile ("sldt %0" : "=r"(ldtr));
    if (ldtr != 0) return 0;
    struct table_register idtr;
    __asm__ volatile ("sidt %0" : "=m"(idtr));
    if (idtr.limit != 256 * 16 - 1) return 0;
    /* The table the CPU consults must be ours (same alias reasoning
       as the GDTR base check above). */
    if (idtr.base != cpu_idt_base()) return 0;
    const struct { cpu_u16 lo, sel; cpu_u8 ist, attr; cpu_u16 mid; cpu_u32 hi, res; }
        *gates = (const void *)idtr.base;
    /* Every CPL3-callable gate must be intentional: 0..47 stay DPL0
       kernel gates, only 128 is DPL3, everything else non-present. A
       flipped DPL would let user code invoke a privileged handler
       (e.g. int $0x0E forging #PF) without #GP. */
    for (unsigned int v = 0; v < 256; ++v) {
        if (v < 48) {
            if (gates[v].attr != 0x8e || gates[v].sel != CPU_CODE_SELECTOR ||
                gates[v].ist != 0 || gates[v].res != 0)
                return 0;
        } else if (v != 128) {
            if (gates[v].attr != 0) return 0;
        }
    }
    const struct { cpu_u16 lo, sel; cpu_u8 ist, attr; cpu_u16 mid; cpu_u32 hi, res; }
        *gate = (const void *)(idtr.base + 128 * 16);
    if (gate->attr != 0xee || gate->sel != CPU_CODE_SELECTOR || gate->ist != 0 ||
        gate->res != 0) return 0;
    if ((((cpu_u64)gate->hi) << 32 | ((cpu_u64)gate->mid) << 16 | gate->lo) !=
        (cpu_u64)user_exit_stub) return 0;
    /* Every copied high leaf must be supervisor-only (see high_ok):
       this runs wherever the address-space shape is revalidated. */
    if (!high_ok()) return 0;
    /* Fast-syscall MSRs are re-checked here, not just at init. */
    if (!msr_mediation_ok()) return 0;
    if (read_cr3() != user_kernel_cr3 || !vm_check(vm_kernel_space())) return 0;
    for (unsigned int i = 0; i < USER_MAX_CONTEXTS; ++i) {
        const struct user_context *c = &contexts[i];
        if (c->slot != i || c->exit_base != (cpu_u64)&exit_stacks[i][0] ||
            c->exit_top != (cpu_u64)&exit_stacks[i][USER_EXIT_STACK_BYTES])
            return 0;
        if (c->link.context != c || (c->link.bound != 0 && c->link.bound != 1)) return 0;
        if (c->state == USER_FREE) {
            if (c->space.root || c->space.identity || c->space.table_pages ||
                c->code_frame || c->data_frame || c->stack_frame) return 0;
            continue;
        }
        if (c->state != USER_ACTIVE && c->state != USER_EXITED && c->state != USER_FAULTED)
            return 0;
        if (!c->space.root || c->space.identity != &c->space || !c->table_pages_at_create)
            return 0;
        if (c->space.table_pages != c->table_pages_at_create) return 0;
        if ((c->code_frame % VM_PAGE_SIZE != 0) || !c->code_frame ||
            (c->data_frame % VM_PAGE_SIZE != 0) || !c->data_frame ||
            (c->stack_frame % VM_PAGE_SIZE != 0) || !c->stack_frame)
            return 0;
        if (!query_exact((struct user_context *)c, USER_CODE_BASE, VM_USER | VM_EXECUTE) ||
            !query_exact((struct user_context *)c, USER_DATA_BASE, VM_USER | VM_WRITE) ||
            !query_exact((struct user_context *)c, USER_STACK_PAGE, VM_USER | VM_WRITE))
            return 0;
        struct vm_mapping m;
        /* Guard page and null must report unmapped; the low kernel image
           must be present supervisor-only (delivery reads IDT/GDT/TSS
           through the user CR3). */
        struct vm_space *space = &((struct user_context *)c)->space;
        if (vm_query(space, USER_GUARD_PAGE, &m) != VM_NOT_MAPPED) return 0;
        if (vm_query(space, 0, &m) != VM_NOT_MAPPED) return 0;
        if (vm_query(space, 0x8000, &m) != VM_OK || m.permissions != VM_EXECUTE)
            return 0;
        if (!clone_ok(c)) return 0;
    }
    return 1;
}

int user_initialize(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context() || initialized) return 0;
    probe();
    /* Quiesce device IRQs for the userspace phase: only IRQ0 (timer) has
       a CPL3 path (scheduler preemption). A device IRQ landing on a CPL3
       frame has no resume path (it is not a scheduler tick), so lines
       1..15 stay masked until the self-test ends (boot halts after).
       Earlier phases (keyboard stage 8) already consumed their input. */
    for (unsigned int irq = 1; irq < 16; ++irq) {
        if (irq == 2) continue; /* Cascade is auto-managed, never direct. */
        require(irq_set_enabled(irq, 0), "irq_quiesce");
    }
    for (unsigned int i = 0; i < USER_BLOB_COUNT; ++i) {
        cpu_u64 size = (cpu_u64)(blobs[i].end - blobs[i].start);
        if (!size || size > VM_PAGE_SIZE) return 0;
    }
    for (unsigned int i = 0; i < USER_MAX_CONTEXTS; ++i) {
        contexts[i].slot = i;
        contexts[i].exit_base = (cpu_u64)&exit_stacks[i][0];
        contexts[i].exit_top = (cpu_u64)&exit_stacks[i][USER_EXIT_STACK_BYTES];
        contexts[i].link.context = &contexts[i];
    }
    user_kernel_cr3 = read_cr3();
    if (user_kernel_cr3 != vm_kernel_space()->root || !vm_check(vm_kernel_space()))
        return 0;
    text("[USER] initialized\r\n[USER] smep=");
    number((cpu_u64)smep_present);
    text(" smap=");
    number((cpu_u64)smap_present);
    text("\r\n");
    initialized = 1;
    return user_check();
}

/* Release the three page frames, reporting success. Callers halt on
   failure (R1 halt-on-rollback-failure): a failed release means PMM
   corruption, since every nonzero frame here was just allocated for this
   context. Unreachable while PMM state is consistent. */
static int release_frames(struct user_context *c)
{
    int ok = 1;
    if (c->code_frame) { if (pmm_release(c->code_frame) != PMM_OK) ok = 0; c->code_frame = 0; }
    if (c->data_frame) { if (pmm_release(c->data_frame) != PMM_OK) ok = 0; c->data_frame = 0; }
    if (c->stack_frame) { if (pmm_release(c->stack_frame) != PMM_OK) ok = 0; c->stack_frame = 0; }
    return ok;
}
static int teardown_space(struct user_context *c, int strict);

static int copy_page(cpu_u64 frame, const char *src, cpu_u64 len)
{
    volatile cpu_u8 *w = vm_frame_access(frame);
    if (!w) return 0;
    for (cpu_u64 i = 0; i < VM_PAGE_SIZE; ++i)
        w[i] = i < len ? (cpu_u8)src[i] : 0;
    return 1;
}

/* Re-copy kernel PML4 slots 256..508 into a user space (values only,
   no allocation). Kernel high-half tables mutate during normal operation
   (kstack alloc/free, heap growth), so by-value snapshots go stale; they
   are refreshed at every user entry/resume (active-correctness: nothing
   mutates high tables while a user space is on-CPU, since CPL3-entry
   handlers never allocate) and for all live spaces at destroy time (so
   the closing check sees current snapshots). A missed sync fails closed
   in user_check, never silently corrupt. */
static int sync_high(struct user_context *c)
{
    if (!c || !c->space.root || c->space.identity != &c->space) return 0;
    struct vm_space *k = vm_kernel_space();
    if (!k) return 0;
    volatile cpu_u64 *kw = vm_frame_access(k->root);
    if (!kw) return 0;
    cpu_u64 hi[253];
    for (unsigned int i = 0; i < 253; ++i) hi[i] = kw[256 + i];
    volatile cpu_u64 *uw = vm_frame_access(c->space.root);
    if (!uw) return 0;
    if (!uw[0]) return 0;
    /* High entries are copied verbatim (intermediate U bits are normal
       and required for table walks; only LEAF U bits grant CPL3 access,
       and those are audited by high_ok, not here). */
    for (unsigned int i = 0; i < 253; ++i) uw[256 + i] = hi[i];
    for (unsigned int i = 253; i < 256; ++i)
        if (uw[256 + i]) return 0;
    return 1;
}

static int clone_high(struct user_context *c)
{
    volatile cpu_u64 *kw = vm_frame_access(vm_kernel_space()->root);
    if (!kw) return 0;
    cpu_u64 hi[253];
    for (unsigned int i = 0; i < 253; ++i) hi[i] = kw[256 + i];
    volatile cpu_u64 *uw = vm_frame_access(c->space.root);
    if (!uw) return 0;
    if (!uw[0]) return 0; /* Low chain linked by vm_clone_low, must be present. */
    for (unsigned int i = 0; i < 253; ++i) {
        if (uw[256 + i]) return 0;
        uw[256 + i] = hi[i];
    }
    return 1;
}

static int create_with_image(struct user_context **out, const char *code, cpu_u64 code_len,
                             const char *data, cpu_u64 data_len, int prefill);

int user_create(struct user_context **out, enum user_blob blob)
{
    if ((unsigned int)blob >= USER_BLOB_COUNT) return 0;
    cpu_u64 size = (cpu_u64)(blobs[blob].end - blobs[blob].start);
    /* Blobs carry no data segment: NULL with zero length (explicitly
       allowed; copy_page never dereferences it). */
    return create_with_image(out, blobs[blob].start, size, 0, 0, 1);
}

/* Loaded programs share the exact fixed layout (entry is defined as the
   code base, so no enter-path change exists): same three mappings, same
   table count, BSS tail zeroed by the page copy. Attack parameters are
   NOT prefilled for loaded programs (they would disclose kernel
   addresses to untrusted code). */
int user_create_loaded(struct user_context **out, const char *code, cpu_u64 code_len,
                       const char *data, cpu_u64 data_len)
{
    if (!code || !code_len || code_len > VM_PAGE_SIZE || data_len > VM_PAGE_SIZE)
        return 0;
    if (data_len && !data) return 0;
    if (!create_with_image(out, code, code_len, data ? data : "", data_len, 0))
        return 0;
    (*out)->loaded = 1;
    return 1;
}

static int create_with_image(struct user_context **out, const char *code, cpu_u64 code_len,
                             const char *data, cpu_u64 data_len, int prefill)
{
    if (!foreground() || !initialized || !out || !code || !code_len ||
        code_len > VM_PAGE_SIZE || data_len > VM_PAGE_SIZE ||
        (data_len && !data))
        return 0;
    struct user_context *c = 0;
    for (unsigned int i = 0; i < USER_MAX_CONTEXTS; ++i)
        if (contexts[i].state == USER_FREE) { c = &contexts[i]; break; }
    if (!c) return 0;
    c->code_frame = c->data_frame = c->stack_frame = 0;
    c->space = (struct vm_space){0};
    if (pmm_allocate(&c->code_frame) != PMM_OK) return 0;
    if (pmm_allocate(&c->data_frame) != PMM_OK) {
        if (!release_frames(c)) panic("rollback_release");
        return 0;
    }
    if (pmm_allocate(&c->stack_frame) != PMM_OK) {
        if (!release_frames(c)) panic("rollback_release");
        return 0;
    }
    if (vm_create(&c->space) != VM_OK) {
        if (!release_frames(c)) panic("rollback_release");
        return 0;
    }
    /* Private low chain first (delivery needs IDT/GDT/TSS/handlers on the
       user CR3); a failure here leaves only private tables, so plain
       vm_destroy unwinds soundly. High clone after: shared entries that
       vm_destroy must never see (teardown_space handles those). Every
       rollback step is checked (R1): a failed step halts with its marker
       instead of returning a clean OOM. */
    if (vm_clone_low(&c->space) != VM_OK) {
        if (vm_destroy(&c->space) != VM_OK) panic("rollback_destroy");
        if (!release_frames(c)) panic("rollback_release");
        c->space = (struct vm_space){0};
        return 0;
    }
    if (!clone_high(c)) {
        volatile cpu_u64 *uw = vm_frame_access(c->space.root);
        if (uw) for (unsigned int i = 0; i < 253; ++i) uw[256 + i] = 0;
        if (vm_destroy(&c->space) != VM_OK) panic("rollback_destroy");
        if (!release_frames(c)) panic("rollback_release");
        c->space = (struct vm_space){0};
        return 0;
    }
    if (vm_map(&c->space, USER_CODE_BASE, c->code_frame, VM_USER | VM_EXECUTE) != VM_OK ||
        vm_map(&c->space, USER_DATA_BASE, c->data_frame, VM_USER | VM_WRITE) != VM_OK ||
        vm_map(&c->space, USER_STACK_PAGE, c->stack_frame, VM_USER | VM_WRITE) != VM_OK)
        goto fail;
    cpu_u64 size = code_len;
    if (!copy_page(c->code_frame, code, size) ||
        !copy_page(c->data_frame, data, data_len) || !copy_page(c->stack_frame, 0, 0))
        goto fail;
    if (prefill) {
        /* Attack-blob parameters: kernel text/data addresses the fault
           blobs dereference. Fixed user-layout offsets past the GPR
           spill area; never prefilled for loaded programs. */
        volatile cpu_u64 *dw = vm_frame_access(c->data_frame);
        if (!dw) goto fail;
        dw[USER_DATA_KTEXT / 8] = (cpu_u64)&user_enter;
        dw[USER_DATA_KDATA / 8] = (cpu_u64)&user_kernel_cr3;
    }
    /* Fixed layout, fixed table count: root, PDPT, PD, kernel-replica
       PT, code PT, data/stack PT. Anything else trips fail-closed here
       for an explicit revisit, never silently. */
    if (c->space.table_pages != 6) goto fail;
    c->table_pages_at_create = c->space.table_pages;
    c->code_size = size;
    c->rip = USER_CODE_BASE; c->rsp = USER_STACK_TOP; c->rflags = 0x202;
    for (unsigned int i = 0; i < 15; ++i) c->gprs[i] = 0;
    c->state = USER_ACTIVE;
    /* Closing check must unwind on failure: returning 0 with *out set
       and mappings live would leak a half-created ACTIVE context. */
    if (!user_check()) goto fail;
    *out = c;
    return 1;
fail:
    /* R1 halt-on-rollback-failure: insert rolls back its own tables, so
       lenient teardown drains to the root; a failed step means VM/PMM
       corruption and halts with its marker instead of returning clean.
       Full slot reset (not just space/frames): the closing user_check runs
       after state==ACTIVE, so returning with ACTIVE + root==0 would wedge
       the 2-slot pool and poison global user_check (ACTIVE requires root).
       Early failures (still FREE) restore identically, harmlessly. */
    {
        unsigned int slot = c->slot;
        cpu_u64 base = c->exit_base, top = c->exit_top;
        if (!teardown_space(c, 0)) panic("rollback_teardown");
        if (!release_frames(c)) panic("rollback_release");
        *c = (struct user_context){0};
        c->slot = slot; c->exit_base = base; c->exit_top = top;
        c->link.context = c;
    }
    return 0;
}

/* Unmap the three user pages and release the private root. Shared
   kernel-half tables are never touched: vm_destroy/vm_check must not run
   on user spaces (shared tables break ownership accounting, and
   destroy_tree would free them). Strict mode requires all three unmaps;
   lenient mode tolerates already-unmapped pages on create-failure paths
   (insert rolls its own tables back, so pruning still drains to root). */
static int teardown_space(struct user_context *c, int strict)
{
    enum vm_result r1 = vm_unmap(&c->space, USER_CODE_BASE);
    enum vm_result r2 = vm_unmap(&c->space, USER_DATA_BASE);
    enum vm_result r3 = vm_unmap(&c->space, USER_STACK_PAGE);
    if (strict && (r1 != VM_OK || r2 != VM_OK || r3 != VM_OK)) return 0;
    /* Drop the private low chain (replica PT, PD, PDPT); shared high
       entries die with the root and are never walked as owned tables. */
    if (vm_release_low(&c->space) != VM_OK) return 0;
    if (!c->space.root || c->space.identity != &c->space || c->space.table_pages != 1)
        return 0;
    volatile cpu_u64 *uw = vm_frame_access(c->space.root);
    if (!uw) return 0;
    for (unsigned int i = 0; i < 256; ++i)
        if (uw[i]) return 0;
    cpu_u64 root = c->space.root;
    if (pmm_release(root) != PMM_OK) return 0;
    enum pmm_state root_state;
    if (pmm_query(root, &root_state) != PMM_OK || root_state != PMM_STATE_FREE)
        return 0;
    c->space = (struct vm_space){0};
    return 1;
}

int user_destroy(struct user_context *c)
{
    if (!foreground() || !initialized || !c) return 0;
    if (c < contexts || c >= contexts + USER_MAX_CONTEXTS) return 0;
    /* Terminal states destroy freely. A pristine ACTIVE context (never
       entered, unbound) is also safe: nothing references its space. */
    if (c->state != USER_EXITED && c->state != USER_FAULTED &&
        !(c->state == USER_ACTIVE && !c->link.bound && !c->entries && !c->resumes))
        return 0;
    if (c->link.bound || !c->space.root || c->space.identity != &c->space) return 0;
    /* Refresh every live snapshot first: kernel high tables may have
       mutated since (kstack free on thread join), and the closing check
       compares exact values. */
    for (unsigned int i = 0; i < USER_MAX_CONTEXTS; ++i)
        if (contexts[i].state != USER_FREE && !sync_high(&contexts[i])) return 0;
    if (!teardown_space(c, 1)) return 0;
    cpu_u64 code = c->code_frame, data = c->data_frame, stack = c->stack_frame;
    if (pmm_release(code) != PMM_OK || pmm_release(data) != PMM_OK ||
        pmm_release(stack) != PMM_OK)
        return 0;
    enum pmm_state state;
    if (pmm_query(code, &state) != PMM_OK || state != PMM_STATE_FREE ||
        pmm_query(data, &state) != PMM_OK || state != PMM_STATE_FREE ||
        pmm_query(stack, &state) != PMM_OK || state != PMM_STATE_FREE)
        return 0;
    unsigned int slot = c->slot;
    cpu_u64 base = c->exit_base, top = c->exit_top;
    *c = (struct user_context){0};
    c->slot = slot; c->exit_base = base; c->exit_top = top;
    c->link.context = c;
    return user_check();
}

static struct exception_frame *build_frame(struct user_context *c)
{
    struct exception_frame *f =
        (struct exception_frame *)(c->exit_top - sizeof(*f));
    *f = (struct exception_frame){0};
    f->r15 = c->gprs[14]; f->r14 = c->gprs[13]; f->r13 = c->gprs[12];
    f->r12 = c->gprs[11]; f->r11 = c->gprs[10]; f->r10 = c->gprs[9];
    f->r9 = c->gprs[8]; f->r8 = c->gprs[7]; f->rdi = c->gprs[5];
    f->rsi = c->gprs[4]; f->rbp = c->gprs[6]; f->rdx = c->gprs[3];
    f->rcx = c->gprs[2]; f->rbx = c->gprs[1]; f->rax = c->gprs[0];
    f->rip = c->rip; f->cs = CPU_USER_CODE_SELECTOR;
    f->rflags = c->rflags; f->rsp = c->rsp; f->ss = CPU_USER_DATA_SELECTOR;
    return f;
}

static int enter_valid(struct user_link *link)
{
    if (!foreground() || !initialized || !link || link->bound) return 0;
    struct user_context *c = link->context;
    if (!c || c < contexts || c >= contexts + USER_MAX_CONTEXTS) return 0;
    if (&c->link != link || c->state != USER_ACTIVE) return 0;
    if (!c->space.root || c->space.identity != &c->space) return 0;
    return 1;
}

cpu_u64 user_enter(struct user_link *link)
{
    if (!enter_valid(link)) return 0;
    struct user_context *c = link->context;
    if (!thread_attach_user(link)) return 0;
    for (unsigned int i = 0; i < 15; ++i) c->gprs[i] = 0;
    c->rip = USER_CODE_BASE; c->rsp = USER_STACK_TOP; c->rflags = 0x202;
    if (!sync_high(c)) {
        /* Unwind the attach: enter must be all-or-nothing. The detach is
           checked (R1): failure means scheduler corruption, so halt. */
        if (!thread_detach_user()) panic("rollback_detach");
        return 0;
    }
    cpu_set_rsp0(c->exit_top);
    ++c->entries;
    return user_enter_asm(build_frame(c), &link->kern_save, c->space.root);
}

cpu_u64 user_resume(struct user_link *link)
{
    if (!foreground() || !initialized || !link || !link->bound) return 0;
    struct user_context *c = link->context;
    if (!c || &c->link != link || c->state != USER_ACTIVE) return 0;
    if (!c->space.root || c->space.identity != &c->space) return 0;
    if (c->rip < USER_CODE_BASE || c->rip >= USER_CODE_BASE + VM_PAGE_SIZE ||
        c->rsp <= USER_STACK_PAGE || c->rsp > USER_STACK_TOP || (c->rsp & 7) ||
        !vm_canonical(c->rsp) ||
        (c->rflags & 0x202) != 0x202 || (c->rflags & ~0x10ED7ULL)) return 0;
    if (!sync_high(c)) return 0;
    cpu_set_rsp0(c->exit_top);
    ++c->resumes;
    return user_enter_asm(build_frame(c), &link->kern_save, c->space.root);
}

static void fault_evidence(const struct user_context *c)
{
    text("[USER] fault slot=");
    number(c->slot);
    field(" vector=", c->fault_vector);
    text(" error=");
    hex(c->fault_error);
    text(" rip=");
    hex(c->rip);
    text(" cr2=");
    hex(c->fault_cr2);
    text("\r\n");
}

void user_handle_exit(struct exception_frame *f)
{
    /* All gates below run pre-switch (still on the entry CR3): a
       desynced RSP0 may leave a frame on a stack that is unmapped
       after the switch, so validation — and any resulting panic —
       must happen while the entry stack is still addressable. */
    struct user_link *link = thread_user_link();
    if (!link || !link->bound || !link->context) panic("exit_no_link");
    struct user_context *c = link->context;
    /* Terminal transitions run exactly once, on the foreground entry
       path, against the stack the TSS selected. A second exit on a
       terminal context, a nested entry from IRQ context, or a desynced
       RSP0 (frame on the wrong stack) fails here before any record. */
    if (c->state != USER_ACTIVE) panic("exit_state");
    if (irq_in_context()) panic("exit_irq");
    if (cpu_get_rsp0() != c->exit_top) panic("exit_rsp0");
    if (!frame_within_exit(c, f)) panic("exit_bad_frame");
    /* Switch only after validation: recording and scheduling need the
       kernel half, and the entry stack has proven addressable. */
    user_to_kernel();
    record_state(c, f);
    /* A malformed gate frame from a LOADED program records a kill
       (invalid_call class) instead of halting: hostile executables are
       the 18b threat model, while 18a blobs are trusted vectors whose
       malformed frames still panic fail-closed. A frame off the exit
       stack can never reach here (unsafe to touch post-switch). The
       stub always pushes vector 128/error 0, so those checks are
       defense-in-depth; the guest-steerable parts are origin + RAX. */
    cpu_u32 reason = (cpu_u32)f->rax, code = (cpu_u32)f->rbx;
    int frame_ok = origin_valid(f) && f->vector == 128 && f->error == 0 &&
                   !(f->rax >> 32);
    if (!frame_ok) {
        if (!c->loaded) panic("exit_bad_frame");
    } else {
        /* Terminal transitions quiesce the timer drive: by the time any exit
           runs, every needed tick has been counted (exits are flag-gated),
           and no post-exit user window should take further ticks. Yields and
           writes keep the drive (write resumes the process like yield). */
        if (reason != SYS_YIELD && reason != SYS_WRITE && !irq_set_enabled(0, 0))
            panic("exit_mask");
        if (reason == SYS_EXIT) {
            c->state = USER_EXITED; c->exit_code = code; ++c->gate_exits;
            if (!c->loaded) {
                text("[USER] exit slot=");
                number(c->slot);
                field(" code=", code);
                text("\r\n");
            }
            sched_resume(user_schedule_next(link, USER_RUN_EXITED));
        }
        if (reason == SYS_YIELD) {
            ++c->yields;
            if (!c->loaded) {
                text("[USER] yield slot=");
                number(c->slot);
                field(" count=", c->yields);
                text("\r\n");
            }
            sched_resume(user_schedule_next(link, USER_RUN_YIELDED));
        }
        if (reason == SYS_WRITE) {
            /* Terminal-for-the-call (not for the process): copy the user
               bytes, record the count for the resume frame, and schedule
               on. The hardware frame already points past the 2-byte gate,
               so no RIP adjustment exists anywhere here. */
            cpu_u64 out = sys_write(c, f->rbx, f->rcx, f->rdx);
            c->sys_result = out;
            ++c->sys_writes;
            c->gprs[0] = out;
            sys_write_evidence(c, f->rbx, f->rdx, out == (cpu_u64)-1 ? 0 : out);
            sched_resume(user_schedule_next(link, USER_RUN_WRITTEN));
        }
        if (reason == SYS_READ) {
            /* Stage 18d Slice A: nonblocking stdin read. Same
               terminal-for-the-call shape as write: the sys_err code
               (never a byte count) lands in the resume RAX. The reserved
               register must be zero (frozen ABI); a nonzero RBP is an
               INVAL return, never a kill (kill is reserved for the
               EAX-high-32 rule checked above). */
            int rc;
            if (f->rbp != 0) {
                rc = SYS_INVAL;
            } else {
                rc = sys_read(c, f->rbx, f->rcx, f->rdx, f->rsi, f->rdi);
            }
            c->sys_result = (cpu_u64)rc;
            c->gprs[0] = (cpu_u64)rc;
            sched_resume(user_schedule_next(link, USER_RUN_READ));
        }
    }
    c->state = USER_FAULTED; c->fault_class = 2;
    c->fault_vector = 128; c->fault_error = reason; c->fault_cr2 = 0;
    ++c->faults;
    if (!c->loaded) fault_evidence(c);
    sched_resume(user_schedule_next(link, USER_RUN_FAULTED));
}

void user_handle_fault(struct exception_frame *f, cpu_u64 cr2)
{
    /* Pre-switch gates, same rationale as the exit path. */
    struct user_link *link = thread_user_link();
    if (!link || !link->bound || !link->context) panic("fault_no_link");
    struct user_context *c = link->context;
    /* Same single-terminal-entry discipline as the exit path. */
    if (c->state != USER_ACTIVE) panic("fault_state");
    if (irq_in_context()) panic("fault_irq");
    if (cpu_get_rsp0() != c->exit_top) panic("fault_rsp0");
    if (!frame_within_exit(c, f)) panic("fault_stack");
    /* Guest-steerable checks (RFLAGS via POPF, vector via faulting
       instruction, CR2 via fault address, error slot): a LOADED program
       records a kill (trap class) instead of halting, mirroring the gate
       path. Link/state/IRQ/RSP0/stack above stay panic-always: a failure
       there means kernel desync or an unaddressable frame, never just a
       hostile program. Trusted 18a blobs still panic fail-closed. */
    {
        int ok = origin_valid_fault(f) && user_fault_managed(f->vector);
        if (ok) {
            if (has_hw_error(f->vector)) {
                if (!vm_canonical(cr2) && f->vector == 14) ok = 0;
            } else if (f->error != 0) ok = 0;
        }
        if (!ok) {
            if (!c->loaded) {
                if (!origin_valid_fault(f)) panic("fault_origin");
                if (!user_fault_managed(f->vector)) panic("fault_vector");
                if (has_hw_error(f->vector)) panic("fault_bad_cr2");
                panic("fault_bad_error");
            }
            user_to_kernel();
            record_state(c, f);
            c->state = USER_FAULTED; c->fault_class = 1;
            c->fault_vector = f->vector; c->fault_error = f->error;
            c->fault_cr2 = cr2;
            ++c->faults;
            sched_resume(user_schedule_next(link, USER_RUN_FAULTED));
        }
    }
    user_to_kernel();
    record_state(c, f);
    c->state = USER_FAULTED; c->fault_class = 1;
    c->fault_vector = f->vector; c->fault_error = f->error; c->fault_cr2 = cr2;
    ++c->faults;
    if (!c->loaded) fault_evidence(c);
    sched_resume(user_schedule_next(link, USER_RUN_FAULTED));
}
