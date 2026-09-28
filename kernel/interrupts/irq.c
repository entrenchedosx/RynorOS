#include "apic.h"
#include "cpu.h"
#include "io.h"
#include "irq.h"
#include "ksched.h"
#include "serial.h"

/* Unified dispatch: legacy ISA vectors 32-47 on either backend (PIC ISR
   proof + PIC EOI, or LAPIC ISR proof + LAPIC EOI), dynamic GSI vectors
   48-127 on the APIC backend only. Drivers register handlers with an
   opaque cookie; EOI is owned here and never called by drivers. */
static int initialized;
static volatile int dispatching;

int irq_initialize(void)
{
    /* No in-handler guard here or below: the timer masks IRQ0 from inside
       its own handler (HEAD contract), and the userspace phase quiesces
       never-registered lines. IF=0 is enforced where it matters. */
    if (initialized) return 0;
    apic_vector_init();
    if (!pic_initialize()) return 0;
    initialized = 1;
    return serial_write("[IRQ] controller initialized\r\n");
}

int irq_register(unsigned int irq, irq_handler handler, void *opaque)
{
    if (!initialized || !cpu_interrupts_disabled()) return 0;
    return apic_route_register(irq, handler, opaque) == APIC_OK;
}

int irq_set_enabled(unsigned int irq, int enabled)
{
    if (!initialized || irq >= IRQ_COUNT || irq == 2) return 0;
    /* Unmask requires a registered handler (route and handler are bound
       atomically at register, so route presence is the same test). Masking
       is unconditional: quiesce of untouched lines must succeed. */
    if (enabled && !apic_route_for_irq(irq)) return 0;
    if (apic_active()) {
        /* Masking an unrouted line is a no-op success: ioapic_init parks
           every entry masked and only used routes are ever unmasked, so an
           unrouted line is already quiet (matches the PIC row, where the
           userspace phase quiesces never-registered lines). Unmasking
           without a route still fails above. */
        if (!apic_route_for_irq(irq)) return 1;
        return apic_route_set_masked(irq, !enabled) == APIC_OK;
    }
    /* PIC row: keep the route shadow in step when a route exists, then
       drive the PIC (which enforces IF=0 itself, as at HEAD). */
    (void)apic_route_set_masked(irq, !enabled);
    return pic_set_enabled(irq, enabled);
}

int irq_set_handler(unsigned int irq, irq_handler handler, void *opaque)
{
    if (!initialized || !cpu_interrupts_disabled()) return 0;
    return apic_route_set_handler(irq, handler, opaque) == APIC_OK;
}

int irq_in_context(void) { return dispatching != 0; }

static void write_vector(unsigned int vector)
{
    char buffer[4];
    unsigned int at = sizeof(buffer);
    do {
        buffer[--at] = (char)('0' + vector % 10);
        vector /= 10;
    } while (vector);
    (void)serial_write("[IRQ] unexpected vector=");
    for (unsigned int i = at; i < sizeof(buffer); ++i) {
        char digit[2] = {buffer[i], 0};
        (void)serial_write(digit);
    }
    (void)serial_write("\r\n");
}

struct exception_frame *irq_dispatch(struct exception_frame *frame)
{
    cpu_u64 vector = frame->vector;
    if (vector < APIC_VECTOR_IRQ_BASE || vector > APIC_VECTOR_DYNAMIC_END) cpu_halt();
    if (dispatching) cpu_halt();
    dispatching = 1;
    struct exception_frame *resume = 0;
    /* Spurious PIC vectors park silently BEFORE route lookup (HEAD order):
       IRQ7/15 noise has no route and must not print. A spurious IRQ15
       still acknowledges the master's genuine cascade service. */
    if (!apic_active() &&
        (vector == IRQ_BASE + 7 || vector == IRQ_BASE + 15)) {
        cpu_u16 noise = pic_in_service();
        unsigned int sirq = (unsigned int)vector - IRQ_BASE;
        if (!(noise & (cpu_u16)(1u << sirq))) {
            if (vector == IRQ_BASE + 15 && (noise & 4)) pic_eoi(2);
            resume = sched_park_cpl3(frame);
            dispatching = 0;
            return resume;
        }
    }
    struct apic_route *route = apic_route_for_vector((unsigned int)vector);
    /* Dynamic routes cannot exist on the PIC backend; treat any such
       delivery as unexpected rather than misdecoding it as legacy. */
    if (!route || !route->handler || (!route->legacy && !apic_active())) {
        /* §57: unexpected vector. Diagnostic for the host (which asserts
           absence on good boots), mask what exists, park, continue. A
           missing-hardware-proof delivery below still halts: that is an
           injection attack, not a surprise device. */
        write_vector((unsigned int)vector);
        if (route) {
            if (apic_active())
                (void)apic_set_route_mask(route->ioapic, route->pin, 1);
            else if (route->legacy)
                (void)pic_set_enabled(route->irq, 0);
        }
        resume = sched_park_cpl3(frame);
        dispatching = 0;
        return resume;
    }
    if (apic_active()) {
        /* LAPIC ISR proves hardware delivery: software INT never sets it. */
        if (!apic_lapic_isr_set((unsigned int)vector) || !cpu_interrupts_disabled() ||
            frame->error != 0 || !(frame->rflags & 0x200))
            cpu_halt();
    } else {
        /* Spurious 7/15 filtered above; any ISR-clear delivery here is an
           injection, not noise. */
        unsigned int irq = (unsigned int)vector - IRQ_BASE;
        cpu_u16 active = pic_in_service();
        if (!(active & (cpu_u16)(1u << irq))) cpu_halt();
        if (!cpu_interrupts_disabled() || frame->error != 0 || !(frame->rflags & 0x200))
            cpu_halt();
    }
    route->handler((cpu_u32)vector, route->opaque);
    apic_note_vector((unsigned int)vector);
    if (apic_active()) {
        apic_lapic_eoi();
        if (apic_lapic_isr_set((unsigned int)vector)) cpu_halt();
    } else {
        pic_eoi((unsigned int)vector - IRQ_BASE);
    }
    if (vector == IRQ_BASE)
        resume = sched_tick(frame);
    else
        resume = sched_park_cpl3(frame);
    if (!resume) cpu_halt();
    dispatching = 0;
    return resume;
}
