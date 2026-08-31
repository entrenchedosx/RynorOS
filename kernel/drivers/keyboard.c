#include "kbd.h"
#include "keyboard-internal.h"
#include "io.h"
#include "irq.h"
#include "ksched.h"

#define KBD_CMD 0x64u
#define KBD_DATA 0x60u
#define STATUS_OBF 0x01u
#define STATUS_IBF 0x02u
#define STATUS_AUX 0x20u
#define STATUS_ERROR 0xc0u
#define POLL_LIMIT 100000u
#define FLUSH_LIMIT 256u
#define CONFIG_QUIET 0x74u /* translation, both clocks disabled, SYS; no IRQs */
#define CONFIG_RUNNING 0x65u /* translation, mouse disabled, SYS, IRQ1 */
enum driver_state { OFF, STARTING, READY, FAILED };
static enum driver_state state;
static const char *init_error = "not_started";
static struct kbd_ring input;
static struct kbd_decoder decoder;
static struct kbd_statistics stats;
static cpu_u64 consumed_epoch;

int kbd_ring_put(struct kbd_ring *q, cpu_u8 scan)
{
    if (q->head >= KBD_STORAGE || q->tail >= KBD_STORAGE ||
        q->received == ~0ULL || q->dropped == ~0ULL || q->epoch == ~0ULL) return -1;
    cpu_u32 next = (q->head + 1u) & KBD_MASK;
    if (next == q->tail) { ++q->dropped; ++q->epoch; return 0; }
    q->data[q->head] = (struct kbd_sample){q->epoch, scan};
    q->head = next;
    ++q->received;
    return 1;
}
int kbd_ring_get(struct kbd_ring *q, struct kbd_sample *out)
{
    if (!out || q->head >= KBD_STORAGE || q->tail >= KBD_STORAGE) return -1;
    if (q->head == q->tail) return 0;
    *out = q->data[q->tail];
    q->tail = (q->tail + 1u) & KBD_MASK;
    return 1;
}
/* Physical key identities, NOT text/ASCII/modifier interpretation.
   Unsupported E0 packets are suppressed as a unit; E1 Pause is bounded.
   On malformed Pause, discard the mismatching byte and reset, never reinterpret
   its tail as an ordinary key. Repeated E0 continues suppression. */
int kbd_decode(struct kbd_decoder *d, cpu_u8 scan, struct kbd_event *out)
{
    static const cpu_u8 pause_tail[] = {0x1d, 0x45, 0xe1, 0x9d, 0xc5};
    *out = (struct kbd_event){scan, 0, KBD_EVENT_UNKNOWN};
    if (d->pause) {
        unsigned int n = d->pause - 1;
        d->pause = n < sizeof(pause_tail) && scan == pause_tail[n] && n+1 < sizeof(pause_tail) ? d->pause+1 : 0;
        return 0;
    }
    if (scan == 0xe1) { d->extended = 0; d->pause = 1; return 0; }
    if (scan == 0xe0) { d->extended = 1; return 0; }
    if (d->extended) { d->extended = 0; return 0; }
    cpu_u8 key = scan & 0x7f;
    switch (key) {
    case 0x1e: case 0x30: case 0x2e: case 0x20: /* a b c d */
    case 0x39: case 0x1c: case 0x2a: case 0x36: /* space enter left/right shift */
        out->key = key;
        out->type = scan & 0x80 ? KBD_EVENT_RELEASE : KBD_EVENT_PRESS;
        return 1;
    default: return 0;
    }
}
static void increment(cpu_u64 *n) { if (*n == ~0ULL) cpu_halt(); ++*n; }
static void kbd_isr(void)
{
    if (state != READY || !cpu_interrupts_disabled() || !irq_in_context()) cpu_halt();
    increment(&stats.irqs);
    cpu_u8 status = io_in8(KBD_CMD);
    if (!(status & STATUS_OBF)) { increment(&stats.empty_irqs); return; }
    cpu_u8 scan = io_in8(KBD_DATA);
    increment(&stats.reads);
    if (status & STATUS_AUX) { increment(&stats.auxiliary); return; }
    if (status & STATUS_ERROR) {
        increment(&stats.errors); increment(&input.epoch); return;
    }
    if (kbd_ring_put(&input, scan) < 0) cpu_halt();
    /* IRQ dispatcher owns EOI. No polling, allocation, serial or STI here. */
}
enum kbd_result kbd_stream_next(struct kbd_ring *q, struct kbd_decoder *d,
                                cpu_u64 *seen, struct kbd_event *out)
{
    if (q->head >= KBD_STORAGE || q->tail >= KBD_STORAGE) return KBD_BAD_CONTEXT;
    cpu_u64 epoch = q->head == q->tail ? q->epoch : q->data[q->tail].epoch;
    if (epoch != *seen) {
        *seen = epoch; *d = (struct kbd_decoder){0};
        return KBD_LOST; /* leave the first post-gap sample for next poll */
    }
    struct kbd_sample sample;
    int got = kbd_ring_get(q, &sample);
    if (got < 0) return KBD_BAD_CONTEXT;
    if (got) { (void)kbd_decode(d, sample.scan, out); return KBD_EVENT; }
    return KBD_EMPTY;
}
enum kbd_result kbd_poll(struct kbd_event *out)
{
    cpu_u64 flags = irq_save();
    enum kbd_result result = KBD_EMPTY;
    if (!out || irq_in_context()) result = KBD_BAD_CONTEXT;
    else if (state != READY) result = KBD_NOT_READY;
    else {
        result = kbd_stream_next(&input, &decoder, &consumed_epoch, out);
        if (result < 0) cpu_halt();
    }
    irq_restore(flags);
    return result;
}
int kbd_statistics(struct kbd_statistics *out)
{
    cpu_u64 flags = irq_save();
    int ok = out && state == READY;
    if (ok) {
        *out = stats; out->received = input.received; out->dropped = input.dropped;
        out->queued = (input.head - input.tail) & KBD_MASK;
    }
    irq_restore(flags); return ok;
}
static int input_ready(void)
{
    for (unsigned int i = 0; i < POLL_LIMIT; ++i) {
        if (!(io_in8(KBD_CMD) & STATUS_IBF)) return 1;
        io_out8(0x80, 0);
    }
    return 0;
}
static int command(cpu_u8 cmd)
{ if (!input_ready()) return 0; io_out8(KBD_CMD, cmd); return 1; }
static int data(cpu_u8 value)
{ if (!input_ready()) return 0; io_out8(KBD_DATA, value); return 1; }
static int read_reply(cpu_u8 *value)
{
    for (unsigned int i = 0; i < POLL_LIMIT; ++i) {
        cpu_u8 status = io_in8(KBD_CMD);
        if (status & STATUS_OBF) {
            *value = io_in8(KBD_DATA);
            return !(status & (STATUS_AUX | STATUS_ERROR));
        }
        io_out8(0x80, 0);
    }
    return 0;
}
static int flush_output(void)
{
    for (unsigned int i = 0; i < FLUSH_LIMIT; ++i) {
        if (!(io_in8(KBD_CMD) & STATUS_OBF)) return 1;
        (void)io_in8(KBD_DATA);
    }
    return !(io_in8(KBD_CMD) & STATUS_OBF);
}
static int set_config(cpu_u8 value)
{
    cpu_u8 actual = 0;
    return command(0x60) && data(value) && command(0x20) &&
           read_reply(&actual) && actual == value;
}
static int keyboard_command(cpu_u8 value)
{
    for (unsigned int attempt = 0; attempt < 3; ++attempt) {
        cpu_u8 reply = 0;
        if (!data(value) || !read_reply(&reply)) return 0;
        if (reply == 0xfa) return 1;
        if (reply != 0xfe) return 0; /* only RESEND is retryable */
    }
    return 0;
}
const char *kbd_init_error(void) { return init_error; }
int kbd_initialize(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context() || state != OFF) return 0;
    state = STARTING;
    cpu_u8 reply = 0;
    init_error = "quiesce";
    if (!pic_set_enabled(1, 0) || !command(0xad) || !command(0xa7) ||
        !flush_output() || !set_config(CONFIG_QUIET)) goto fail;
    init_error = "controller_test";
    if (!command(0xaa) || !read_reply(&reply) || reply != 0x55) goto fail;
    /* Some controllers reset configuration during self-test. Re-establish it. */
    if (!command(0xad) || !command(0xa7) || !flush_output() || !set_config(CONFIG_QUIET)) goto fail;
    init_error = "interface_test";
    if (!command(0xab) || !read_reply(&reply) || reply != 0) goto fail;
    init_error = "keyboard_commands";
    if (!command(0xae) || !keyboard_command(0xf5) ||
        !keyboard_command(0xf0) || !keyboard_command(2) ||
        !keyboard_command(0xf4)) goto fail;
    init_error = "config_readback";
    if (!set_config(CONFIG_RUNNING)) goto fail;
    init_error = "irq_registration";
    if (!irq_register(1, kbd_isr)) goto fail;
    state = READY; /* publish before unmask; IF still zero */
    if (!irq_set_enabled(1, 1)) goto fail;
    init_error = "none";
    return 1;
fail:
    state = FAILED;
    if (!pic_set_enabled(1, 0)) cpu_halt(); /* software delivery MUST be disabled */
    (void)command(0xad); (void)command(0xa7);
    /* Best effort hardware quiescence; a broken controller can reject commands.
       No allocation or retry promise; PIC remains masked in every failed state. */
    return 0;
}
