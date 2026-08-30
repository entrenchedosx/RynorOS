#ifndef RYNOR_IRQ_H
#define RYNOR_IRQ_H
#include "cpu.h"

#define IRQ_BASE 32
#define IRQ_COUNT 16
typedef void (*irq_handler)(void);

/* Single CPU only. Configuration requires IF=0; IRQ2 is reserved for cascade. */
int irq_initialize(void);
int irq_register(unsigned int irq, irq_handler handler);
int irq_set_enabled(unsigned int irq, int enabled);
/* Replace the handler installed for an already-registered IRQ (scheduler takes
   over IRQ0 from the one-shot heartbeat). Requires IF=0 and an initialized,
   non-cascade IRQ; never changes the enabled state. */
int irq_set_handler(unsigned int irq, irq_handler handler);
/* Dispatch a hardware IRQ and return the frame to resume from. Returns the same
   frame unless the scheduler preempted, in which case it returns a pointer to
   the next thread's saved context. Never returns NULL. */
struct exception_frame *irq_dispatch(struct exception_frame *frame);
cpu_u16 pic_in_service(void);
int pic_initialize(void);
int pic_set_enabled(unsigned int irq, int enabled);
void pic_eoi(unsigned int irq);
void timer_self_test(void);
#endif
