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

/* Accessed bits are preset; no CPU writes into the constant descriptor table. */
static const cpu_u64 kernel_gdt[3] __attribute__((aligned(16))) = {
    0, 0x00af9b000000ffffULL, 0x00cf93000000ffffULL
};
static struct idt_gate kernel_idt[256] __attribute__((aligned(16)));
extern const cpu_u64 exception_stub_table[32];
extern void cpu_load_gdt(const struct table_pointer *pointer);

__attribute__((noreturn)) void cpu_halt(void)
{
    for (;;) __asm__ volatile ("cli; hlt");
}

static int initialize_gdt(void)
{
    const struct table_pointer desired = {sizeof(kernel_gdt) - 1, (cpu_u64)kernel_gdt};
    struct table_pointer actual;
    cpu_u16 cs, ss, ds, es, fs, gs;
    cpu_load_gdt(&desired);
    __asm__ volatile ("sgdt %0" : "=m"(actual));
    __asm__ volatile ("mov %%cs, %0" : "=r"(cs));
    __asm__ volatile ("mov %%ss, %0" : "=r"(ss));
    __asm__ volatile ("mov %%ds, %0" : "=r"(ds));
    __asm__ volatile ("mov %%es, %0" : "=r"(es));
    __asm__ volatile ("mov %%fs, %0" : "=r"(fs));
    __asm__ volatile ("mov %%gs, %0" : "=r"(gs));
    return actual.base == desired.base && actual.limit == desired.limit &&
           cs == CPU_CODE_SELECTOR && ss == CPU_DATA_SELECTOR &&
           ds == CPU_DATA_SELECTOR && es == CPU_DATA_SELECTOR && fs == 0 && gs == 0;
}

static int initialize_idt(void)
{
    /* BSS initialization leaves vectors 32..255 non-present. No device IRQs. */
    for (unsigned int vector = 0; vector < 32; ++vector) {
        cpu_u64 address = exception_stub_table[vector];
        kernel_idt[vector] = (struct idt_gate){
            (cpu_u16)address, CPU_CODE_SELECTOR, 0, 0x8e,
            (cpu_u16)(address >> 16), (cpu_u32)(address >> 32), 0
        };
    }
    const struct table_pointer desired = {sizeof(kernel_idt) - 1, (cpu_u64)kernel_idt};
    struct table_pointer actual;
    __asm__ volatile ("lidt %0" : : "m"(desired) : "memory");
    __asm__ volatile ("sidt %0" : "=m"(actual));
    if (actual.base != desired.base || actual.limit != desired.limit)
        return 0;
    for (unsigned int vector = 0; vector < 256; ++vector) {
        const struct idt_gate *gate = &kernel_idt[vector];
        if (vector >= 32) {
            if (gate->attributes != 0) return 0;
            continue;
        }
        cpu_u64 address = gate->offset_low | ((cpu_u64)gate->offset_middle << 16) |
                          ((cpu_u64)gate->offset_high << 32);
        if (address != exception_stub_table[vector] || gate->selector != CPU_CODE_SELECTOR ||
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
