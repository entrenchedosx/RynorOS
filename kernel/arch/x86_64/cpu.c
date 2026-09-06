#include "cpu.h"
#include "serial.h"

struct table_pointer {
    cpu_u16 limit;
    cpu_u64 base;
} __attribute__((packed));

struct idt_gate {
    cpu_u16 offset_low, selector;
    cpu_u8 ist, attributes;
    cpu_u16 offset_middle;
    cpu_u32 offset_high, reserved;
} __attribute__((packed));

_Static_assert(sizeof(struct table_pointer) == 10, "GDTR/IDTR format");
_Static_assert(sizeof(struct idt_gate) == 16, "64-bit interrupt gate format");

/* Accessed bits are preset in code/data descriptors; the TSS descriptor
   below carries the runtime TSS address, so the table is built at boot. */
static cpu_u64 kernel_gdt[7] __attribute__((aligned(16)));
/* Single-CPU 64-bit TSS: RSP0 only, no IST, no I/O bitmap. */
static struct x86_tss {
    cpu_u32 reserved0;
    cpu_u64 rsp0, rsp1, rsp2, reserved1;
    cpu_u64 ist[7];
    cpu_u64 reserved2;
    cpu_u16 reserved3, iomap;
} __attribute__((packed)) cpu_tss __attribute__((aligned(16)));
_Static_assert(sizeof(struct x86_tss) == 104, "64-bit TSS size");
_Static_assert(__builtin_offsetof(struct x86_tss, rsp0) == 4, "TSS RSP0 offset");
_Static_assert(__builtin_offsetof(struct x86_tss, ist) == 36, "TSS IST offset");
_Static_assert(__builtin_offsetof(struct x86_tss, iomap) == 102, "TSS bitmap offset");
static struct idt_gate kernel_idt[256] __attribute__((aligned(16)));
extern const cpu_u64 exception_stub_table[32];
extern const cpu_u64 irq_stub_table[16];
extern const char user_exit_stub[];
extern void cpu_load_gdt(const struct table_pointer *pointer);
extern void cpu_load_task(cpu_u16 selector);

void cpu_set_rsp0(cpu_u64 top)
{
    /* IF=0 callers only; selects the CPL3 exit stack for one context. */
    cpu_tss.rsp0 = top;
}

cpu_u64 cpu_get_rsp0(void)
{
    return cpu_tss.rsp0;
}

/* Runtime descriptor checks compare live GDTR/GDT bytes against these code
   immediates, never against another mutable table. */
cpu_u64 cpu_tss_base(void)
{
    return (cpu_u64)&cpu_tss;
}

cpu_u64 cpu_gdt_base(void)
{
    return (cpu_u64)kernel_gdt;
}

cpu_u64 cpu_idt_base(void)
{
    return (cpu_u64)kernel_idt;
}

__attribute__((noreturn)) void cpu_halt(void)
{
    for (;;) __asm__ volatile ("cli; hlt");
}

static int initialize_gdt(void)
{
    /* Null, kernel code/data, user data/code (DPL3), 64-bit TSS pair. */
    cpu_tss = (struct x86_tss){0};
    cpu_tss.iomap = sizeof(cpu_tss);
    kernel_gdt[0] = 0;
    kernel_gdt[1] = 0x00af9b000000ffffULL;
    kernel_gdt[2] = 0x00cf93000000ffffULL;
    kernel_gdt[3] = 0x00cff3000000ffffULL;
    kernel_gdt[4] = 0x00affb000000ffffULL;
    cpu_u64 base = (cpu_u64)&cpu_tss;
    /* Available 64-bit TSS (type 0x9): LTR faults on a busy type. The CPU
       sets the Accessed bit on load and writes 0xB back; verifiers that
       run after LTR must expect the accessed form. */
    kernel_gdt[5] = (103 & 0xffff) | ((base & 0xffff) << 16) |
        (((base >> 16) & 0xff) << 32) | (0x89ULL << 40) | ((base >> 24 & 0xff) << 56);
    kernel_gdt[6] = base >> 32;
    const struct table_pointer desired = {sizeof(kernel_gdt) - 1, (cpu_u64)kernel_gdt};
    struct table_pointer actual;
    cpu_u16 cs, ss, ds, es, fs, gs, tr;
    cpu_load_gdt(&desired);
    cpu_load_task(CPU_TSS_SELECTOR);
    __asm__ volatile ("sgdt %0" : "=m"(actual));
    __asm__ volatile ("str %0" : "=r"(tr));
    __asm__ volatile ("mov %%cs, %0" : "=r"(cs));
    __asm__ volatile ("mov %%ss, %0" : "=r"(ss));
    __asm__ volatile ("mov %%ds, %0" : "=r"(ds));
    __asm__ volatile ("mov %%es, %0" : "=r"(es));
    __asm__ volatile ("mov %%fs, %0" : "=r"(fs));
    __asm__ volatile ("mov %%gs, %0" : "=r"(gs));
    return actual.base == desired.base && actual.limit == desired.limit &&
           cs == CPU_CODE_SELECTOR && ss == CPU_DATA_SELECTOR &&
           ds == CPU_DATA_SELECTOR && es == CPU_DATA_SELECTOR && fs == 0 && gs == 0 &&
           tr == CPU_TSS_SELECTOR && cpu_tss.rsp0 == 0 &&
           cpu_tss.iomap == sizeof(cpu_tss) &&
           kernel_gdt[3] == 0x00cff3000000ffffULL &&
           kernel_gdt[4] == 0x00affb000000ffffULL;
}

static int initialize_idt(void)
{
    /* Exceptions 0..31 and PIC IRQs 32..47 stay DPL0; vector 0x80 is the
       DPL3 userspace gate. 48..255 remain non-present except 128. */
    for (unsigned int vector = 0; vector < 48; ++vector) {
        cpu_u64 address = vector < 32 ? exception_stub_table[vector] : irq_stub_table[vector - 32];
        kernel_idt[vector] = (struct idt_gate){
            (cpu_u16)address, CPU_CODE_SELECTOR, 0, 0x8e,
            (cpu_u16)(address >> 16), (cpu_u32)(address >> 32), 0
        };
    }
    cpu_u64 gate = (cpu_u64)user_exit_stub;
    kernel_idt[128] = (struct idt_gate){
        (cpu_u16)gate, CPU_CODE_SELECTOR, 0, 0xee,
        (cpu_u16)(gate >> 16), (cpu_u32)(gate >> 32), 0
    };
    const struct table_pointer desired = {sizeof(kernel_idt) - 1, (cpu_u64)kernel_idt};
    struct table_pointer actual;
    __asm__ volatile ("lidt %0" : : "m"(desired) : "memory");
    __asm__ volatile ("sidt %0" : "=m"(actual));
    if (actual.base != desired.base || actual.limit != desired.limit)
        return 0;
    for (unsigned int vector = 0; vector < 256; ++vector) {
        const struct idt_gate *gate = &kernel_idt[vector];
        if (vector == 128) {
            cpu_u64 address = gate->offset_low | ((cpu_u64)gate->offset_middle << 16) |
                              ((cpu_u64)gate->offset_high << 32);
            if (address != (cpu_u64)user_exit_stub || gate->selector != CPU_CODE_SELECTOR ||
                gate->ist != 0 || gate->attributes != 0xee || gate->reserved != 0)
                return 0;
            continue;
        }
        if (vector >= 48) {
            if (gate->attributes != 0) return 0;
            continue;
        }
        cpu_u64 address = gate->offset_low | ((cpu_u64)gate->offset_middle << 16) |
                          ((cpu_u64)gate->offset_high << 32);
        cpu_u64 expected = vector < 32 ? exception_stub_table[vector] : irq_stub_table[vector - 32];
        if (address != expected || gate->selector != CPU_CODE_SELECTOR ||
            gate->ist != 0 || gate->attributes != 0x8e || gate->reserved != 0)
            return 0;
    }
    return 1;
}

int cpu_initialize(void)
{
    __asm__ volatile ("cli");
    if (!initialize_gdt()) {
        serial_write("[CPU] GDT verification failed\r\n");
        return 0;
    }
    if (!serial_write("[CPU] GDT initialized\r\n")) return 0;
    if (!initialize_idt()) {
        serial_write("[CPU] IDT verification failed\r\n");
        return 0;
    }
    return serial_write("[CPU] IDT initialized\r\n");
}
