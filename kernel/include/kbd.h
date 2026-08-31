#ifndef RYNOR_KBD_H
#define RYNOR_KBD_H
#include "cpu.h"

/* UP, one shared input stream. Poll/statistics save and restore caller IF.
   Poll rejects IRQ context. No allocations; loss is reported, never hidden. */
#define KBD_RING_CAPACITY 31u
enum kbd_event_type { KBD_EVENT_UNKNOWN, KBD_EVENT_PRESS, KBD_EVENT_RELEASE };
struct kbd_event { cpu_u8 scan, key; enum kbd_event_type type; };
enum kbd_result { KBD_BAD_CONTEXT = -2, KBD_NOT_READY = -1,
                  KBD_EMPTY = 0, KBD_EVENT = 1, KBD_LOST = 2 };
struct kbd_statistics {
    cpu_u64 irqs, reads, received, dropped, errors, auxiliary, empty_irqs, queued;
};
/* One initialization attempt, foreground IF=0; failure is terminal and IRQ1
   remains PIC-masked. No implicit retry with a partly configured device. */
int kbd_initialize(void);
const char *kbd_init_error(void);
enum kbd_result kbd_poll(struct kbd_event *out);
int kbd_statistics(struct kbd_statistics *out);
void keyboard_self_test(void);
#endif
