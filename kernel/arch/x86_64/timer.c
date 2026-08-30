#include "irq.h"
#include "io.h"
#include "serial.h"

#define PIT_DIVISOR 11932u
#define TEST_TICKS 3u

static volatile cpu_u64 ticks;
static volatile cpu_u64 samples[TEST_TICKS];

static void timer_interrupt(void)
{
    /* Only this actual IRQ0 callback writes the counter/samples. The bounded
       sample buffer tolerates delayed foreground execution without inventing ticks. */
    cpu_u64 current = ++ticks;
    if (current <= TEST_TICKS) samples[current - 1] = current;
    if (current >= TEST_TICKS && !irq_set_enabled(0, 0)) cpu_halt();
}

static int write_tick(cpu_u64 value)
{
    char text[21];
    unsigned int at = sizeof(text) - 1;
    text[at] = 0;
    do {
        text[--at] = (char)('0' + value % 10);
        value /= 10;
    } while (value);
    return serial_write("[TIMER] tick=") && serial_write(text + at) && serial_write("\r\n");
}

void timer_self_test(void)
{
    if (!cpu_interrupts_disabled() || !irq_initialize() ||
        !irq_register(0, timer_interrupt)) cpu_halt();
    /* Reject invalid, cascade, null, duplicate, and unregistered-enable requests
       without changing live dispatch state. Exercise the registration contract. */
    if (irq_register(IRQ_COUNT, timer_interrupt) || irq_register(2, timer_interrupt) ||
        irq_register(1, (irq_handler)0) || irq_register(0, timer_interrupt) ||
        irq_set_enabled(1, 1)) cpu_halt();
    io_out8(0x43, 0x34); /* Channel 0, low/high count, mode 2, binary. */
    io_out8(0x40, (cpu_u8)PIT_DIVISOR);
    io_out8(0x40, (cpu_u8)(PIT_DIVISOR >> 8));
    io_out8(0x43, 0xe2); /* Read-back: latch channel 0 status only. */
    if ((io_in8(0x40) & 0x3f) != 0x34) cpu_halt();
    if (!serial_write("[TIMER] initialized\r\n"
                      "[TIMER] clock_hz=1193182 divisor=11932 mode=2\r\n"
                      "[TEST] waiting for timer interrupts\r\n") ||
        !irq_set_enabled(0, 1)) cpu_halt();
    for (unsigned int reported = 0; reported < TEST_TICKS; ++reported) {
        /* Check with IF=0, then atomically enable-and-sleep. STI's interrupt
           shadow prevents a lost wakeup between the condition and HLT. */
        while (ticks <= reported)
            __asm__ volatile ("sti; hlt; cli" : : : "memory");
        if (samples[reported] != reported + 1 || !write_tick(samples[reported])) cpu_halt();
    }
    /* Foreground execution proves IRETQ returned. ISR readback proves EOI
       cleared service; all three deliveries are needed to pass. Leave IF=0. */
    if (ticks != TEST_TICKS || pic_in_service() != 0 || io_in8(0x21) != 0xff ||
        io_in8(0xa1) != 0xff || !cpu_interrupts_disabled()) cpu_halt();
    (void)serial_write("[TEST] timer interrupt handling verified\r\n");
    (void)serial_flush();
}
