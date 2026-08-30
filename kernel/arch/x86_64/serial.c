/* Original minimal 16550-compatible COM1 output, with no host/runtime calls. */
#include "serial.h"

static void out8(unsigned short port, unsigned char value)
{
    __asm__ volatile ("outb %0, %1" : : "a"(value), "Nd"(port));
}

static unsigned char in8(unsigned short port)
{
    unsigned char value;
    __asm__ volatile ("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

void serial_init(void)
{
    out8(0x3fb, 0x00); /* Clear DLAB before accessing interrupt enable. */
    out8(0x3f9, 0x00); /* No UART interrupts. */
    out8(0x3fb, 0x80); /* Divisor latch access. */
    out8(0x3f8, 0x01); /* 115200 baud, divisor 1. */
    out8(0x3f9, 0x00);
    out8(0x3fb, 0x03); /* 8 data bits, no parity, one stop bit. */
    out8(0x3fa, 0x07); /* Enable and clear FIFOs. */
    out8(0x3fc, 0x03); /* DTR/RTS, no interrupt output gate. */
}

int serial_write(const char *text)
{
    while (*text) {
        unsigned int remaining = 1000000;
        while (!(in8(0x3fd) & 0x20)) {
            if (--remaining == 0)
                return 0;
        }
        out8(0x3f8, (unsigned char)*text++);
    }
    return 1;
}

int serial_flush(void)
{
    for (unsigned int remaining = 1000000; remaining; --remaining) {
        if (in8(0x3fd) & 0x40)
            return 1;
    }
    return 0;
}
