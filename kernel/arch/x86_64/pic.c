#include "io.h"
#include "irq.h"

static cpu_u16 mask = 0xffff;

static void pic_write(cpu_u16 port, cpu_u8 value)
{
    io_out8(port, value);
    io_out8(0x80, 0); /* Legacy PC I/O recovery delay; no memory access. */
}

int pic_initialize(void)
{
    if (!cpu_interrupts_disabled()) return 0;
    pic_write(0x21, 0xff);
    pic_write(0xa1, 0xff);
    pic_write(0x20, 0x11); /* ICW1: edge triggered, cascade, ICW4 follows. */
    pic_write(0xa0, 0x11);
    pic_write(0x21, IRQ_BASE);
    pic_write(0xa1, IRQ_BASE + 8);
    pic_write(0x21, 0x04); /* Slave connected to master IRQ2. */
    pic_write(0xa1, 0x02); /* Slave identity on cascade bus. */
    pic_write(0x21, 0x01); /* 8086 mode, manual EOI, normal nested priority. */
    pic_write(0xa1, 0x01);
    mask = 0xffff;
    pic_write(0x21, 0xff);
    pic_write(0xa1, 0xff);
    return io_in8(0x21) == 0xff && io_in8(0xa1) == 0xff && pic_in_service() == 0;
}

int pic_set_enabled(unsigned int irq, int enabled)
{
    if (!cpu_interrupts_disabled() || irq >= IRQ_COUNT || irq == 2) return 0;
    if (enabled) mask &= (cpu_u16)~(1u << irq);
    else mask |= (cpu_u16)(1u << irq);
    /* Never unmask the cascade unless a slave line is enabled. */
    if ((mask & 0xff00) != 0xff00) mask &= (cpu_u16)~4u;
    else mask |= 4;
    pic_write(0xa1, (cpu_u8)(mask >> 8));
    pic_write(0x21, (cpu_u8)mask);
    return io_in8(0x21) == (cpu_u8)mask && io_in8(0xa1) == (cpu_u8)(mask >> 8);
}

cpu_u16 pic_in_service(void)
{
    io_out8(0x20, 0x0b); /* OCW3: next command-port read is ISR, not IRR. */
    io_out8(0xa0, 0x0b);
    cpu_u16 master = io_in8(0x20);
    return master | ((cpu_u16)io_in8(0xa0) << 8);
}

void pic_eoi(unsigned int irq)
{
    if (irq >= 8) io_out8(0xa0, 0x20);
    io_out8(0x20, 0x20); /* Slave before master; non-specific EOI, no nesting. */
}
