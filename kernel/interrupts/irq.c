#include "irq.h"
#include "io.h"
#include "serial.h"
#include "ksched.h"

static irq_handler handlers[IRQ_COUNT];
static int initialized;
static int dispatching;
int irq_in_context(void) { return dispatching; }

int irq_initialize(void)
{
    if (initialized || !pic_initialize()) return 0;
    initialized = 1;
    return serial_write("[IRQ] controller initialized\r\n");
}

int irq_register(unsigned int irq, irq_handler handler)
{
    if (!initialized || !cpu_interrupts_disabled() || irq >= IRQ_COUNT || irq == 2 ||
        !handler || handlers[irq]) return 0;
    handlers[irq] = handler;
    return 1;
}

int irq_set_enabled(unsigned int irq, int enabled)
{
    if (!initialized || irq >= IRQ_COUNT || (enabled && !handlers[irq])) return 0;
    return pic_set_enabled(irq, enabled);
}

int irq_set_handler(unsigned int irq, irq_handler handler)
{
    if (!initialized || !cpu_interrupts_disabled() || irq >= IRQ_COUNT || irq == 2 ||
        !handler || !handlers[irq]) return 0;
    handlers[irq] = handler;
    return 1;
}

struct exception_frame *irq_dispatch(struct exception_frame *frame)
{
    if (frame->vector < IRQ_BASE || frame->vector >= IRQ_BASE + IRQ_COUNT)
        cpu_halt();
    /* Dispatch context starts here (not after the spurious checks): the
       early returns below can park a CPL3 frame, and parking requires IRQ
       context like the tick path. No consumer distinguishes the flag on
       the spurious path (no handler runs there). */
    if (dispatching) cpu_halt();
    dispatching = 1;
    unsigned int irq = (unsigned int)frame->vector - IRQ_BASE;
    cpu_u16 active = pic_in_service();
    /* A spurious IRQ7 has no ISR bit and needs no EOI. For spurious IRQ15,
       only the master's genuine cascade service must be acknowledged. */
    if (irq == 7 && !(active & (1u << 7))) {
        struct exception_frame *resume = sched_park_cpl3(frame);
        dispatching = 0;
        return resume;
    }
    if (irq == 15 && !(active & (1u << 15))) {
        if (active & 4) pic_eoi(2);
        struct exception_frame *resume = sched_park_cpl3(frame);
        dispatching = 0;
        return resume;
    }
    /* Enforce a real controller acknowledgment, not software INT injection. */
    if (!initialized || !handlers[irq] || !(active & (1u << irq)) ||
        !cpu_interrupts_disabled() || frame->error != 0 || !(frame->rflags & 0x200))
        cpu_halt();
    handlers[irq](); /* No allocation, blocking, serial, or enabling IF here. */
    pic_eoi(irq);
    /* The IRQ0 handler is the scheduler drive; it may redirect the resume frame.
       Any other IRQ that interrupted CPL3 parks through the audited user-save
       path (Slice A): returning a raw CPL3 frame would die in sched_handoff,
       which only accepts kernel frames. All other handlers resume on the
       same frame. */
    struct exception_frame *resume = irq == 0 ? sched_tick(frame) : sched_park_cpl3(frame);
    dispatching = 0;
    return resume;
}
