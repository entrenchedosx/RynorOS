#ifndef RYNOR_KBD_H
#define RYNOR_KBD_H
#include "cpu.h"

/* UP, one shared input stream. Poll/statistics save and restore caller IF.
   Poll rejects IRQ context. No allocations; loss is reported, never hidden. */
#define KBD_RING_CAPACITY 31u
#define KBD_KEY_CTRL 0x1du
enum kbd_event_type { KBD_EVENT_UNKNOWN, KBD_EVENT_PRESS, KBD_EVENT_RELEASE };
struct kbd_event { cpu_u8 scan, key; enum kbd_event_type type; cpu_u8 extended; };
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
/* Stage 18d Slice A: consume one raw scan byte for CPL3 delivery.
 * Returns KBD_EMPTY (nothing; output untouched), KBD_EVENT with the raw
 * scan byte in out->scan (including E0/E1 prefix bytes; 0x00/0xFF never
 * appear — the ISR consumes them as errors with an epoch advance), or
 * KBD_LOST (epoch advanced: exactly one loss notification per gap; the
 * first post-gap byte stays queued for the next call). Foreground IF=0
 * only; returns KBD_BAD_CONTEXT otherwise. Never touches the decoder:
 * layout/policy belongs to CPL3. */
enum kbd_result kbd_take(cpu_u8 *out);
void keyboard_self_test(void);
#endif
