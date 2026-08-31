#ifndef RYNOR_KEYBOARD_INTERNAL_H
#define RYNOR_KEYBOARD_INTERNAL_H
#include "kbd.h"
#define KBD_STORAGE (KBD_RING_CAPACITY + 1u)
#define KBD_MASK (KBD_STORAGE - 1u)
_Static_assert((KBD_STORAGE & KBD_MASK) == 0, "ring storage must be power of two");
struct kbd_sample { cpu_u64 epoch; cpu_u8 scan; };
struct kbd_ring {
    struct kbd_sample data[KBD_STORAGE];
    cpu_u32 head, tail;
    cpu_u64 received, dropped, epoch;
};
struct kbd_decoder { unsigned int extended, pause; };
/* Pure bounded algorithms. Tests use LOCAL instances; never inject the IRQ ring.
   Production calls these only while IF=0. -1 means corrupt/exhausted counters. */
int kbd_ring_put(struct kbd_ring *, cpu_u8);
int kbd_ring_get(struct kbd_ring *, struct kbd_sample *);
int kbd_decode(struct kbd_decoder *, cpu_u8, struct kbd_event *);
enum kbd_result kbd_stream_next(struct kbd_ring *, struct kbd_decoder *,
                                cpu_u64 *epoch, struct kbd_event *);
#endif
