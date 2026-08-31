#ifndef RYNOR_IO_H
#define RYNOR_IO_H
#include "cpu.h"

static inline void io_out16(cpu_u16 port, cpu_u16 value)
{ __asm__ volatile ("outw %0, %1" : : "a"(value), "Nd"(port) : "memory"); }
static inline cpu_u16 io_in16(cpu_u16 port)
{ cpu_u16 v; __asm__ volatile ("inw %1, %0" : "=a"(v) : "Nd"(port) : "memory"); return v; }
static inline void io_out32(cpu_u16 port, cpu_u32 value)
{ __asm__ volatile ("outl %0, %1" : : "a"(value), "Nd"(port) : "memory"); }
static inline cpu_u32 io_in32(cpu_u16 port)
{ cpu_u32 v; __asm__ volatile ("inl %1, %0" : "=a"(v) : "Nd"(port) : "memory"); return v; }

static inline void io_out8(cpu_u16 port, cpu_u8 value)
{
    __asm__ volatile ("outb %0, %1" : : "a"(value), "Nd"(port) : "memory");
}

static inline cpu_u8 io_in8(cpu_u16 port)
{
    cpu_u8 value;
    __asm__ volatile ("inb %1, %0" : "=a"(value) : "Nd"(port) : "memory");
    return value;
}

static inline int cpu_interrupts_disabled(void)
{
    cpu_u64 flags;
    __asm__ volatile ("pushfq; pop %0" : "=r"(flags) : : "memory");
    return !(flags & 0x200);
}
#endif
