# Stage 8: PS/2 keyboard input

## Purpose and support contract

A single PS/2 keyboard on port 1 of the i8042-compatible controller of the
pinned QEMU PC/SeaBIOS environment. The original driver and tests required
repairs; see [independent audit](../reports/stage8-audit.md). This is not a
physical-hardware certification, USB driver, console, or full text-input system.

The driver owns controller configuration for kernel lifetime. The auxiliary
port remains disabled. Keyboard commands run only during boot, before host key
injection. No runtime command/scancode multiplexing or hotplug is supported.

## Controller initialization

Foreground IF=0, no IRQ context, exactly one attempt:

1. Mask PIC IRQ1; disable both ports (AD/A7), bounded flush, write/read back 74h.
   This explicitly enables translation, disables both clocks and both IRQs, and
   sets the system flag. Reserved bit 7, keylock override and auxiliary IRQ are clear.
2. Controller self-test AA must return 55h; disable/flush/reapply configuration
   because a real controller may reset configuration during its self-test.
3. Port-1 interface test AB must return zero.
4. Enable port 1 (AE); send keyboard F5 (disable scanning/defaults), F0/02
   (select Set 2), F4 (enable scanning). Each byte requires FA ACK; only FE RESEND
   is retried, at most three attempts.
5. Write/read back 65h: translation on, keyboard clock on, auxiliary clock off,
   IRQ1 enabled, auxiliary IRQ disabled. Register callback, publish READY with
   IF=0, then unmask PIC IRQ1.

The device produces Set 2; the controller translates it to Set 1 at port 60h.
This no longer inherits the BIOS translation or keyboard scan-set settings.
Reading back the IRQ-enabled command byte can latch one startup IRQ with no
remaining output byte. The ISR diagnoses/counts this without reading stale data.
On the pinned QEMU build this latch deterministically yields exactly one empty
IRQ, and the host validator requires `empty == 1` so the accounting cannot be
silently removed; the guest-side invariant remains the general `irqs == reads +
empty`.

Every input-buffer/output-buffer wait is limited to 100000 polls with port-80
recovery delays; flush is limited to 256 reads. These are bounded iteration
budgets, not a calibrated wall-clock hardware timeout. Replies reject AUX,
timeout and parity status. Initialization failure records its phase, latches
FAILED, masks PIC IRQ1 and attempts bounded port disable. A broken controller
may reject cleanup commands; no successful hardware cleanup is claimed then.
Retries are rejected explicitly. No frames or heap objects are allocated.

## IRQ path

Vector 33 enters the unchanged Stage 7 IRQ/frame path. The common dispatcher
checks real PIC in-service state and IF=0 before calling the private ISR.

The ISR counts the IRQ, reads status at 64h, and reads 60h only when OBF is set.
Empty IRQs are counted. AUX bytes are consumed/discarded, never keyboard input.
Timeout/parity bytes are consumed/discarded and mark a stream discontinuity.
Set-1 overrun/self-test response bytes (00h/FFh) are likewise consumed,
counted as errors and mark a stream discontinuity rather than appearing as
phantom UNKNOWN keys.
A valid byte goes through the same bounded ring primitive used in local tests.
The dispatcher issues master PIC EOI after the ISR; it never enables IF.
No driver IRQ path allocates, polls, prints, blocks or calls the scheduler.
IRETQ restores the interrupted context, including a worker if IRQ1 interrupted it.

## Ring, ownership and concurrency

32 storage slots, exactly **31 usable raw-byte samples**. Each sample also carries
a loss epoch. Empty is head==tail; full is ((head+1)&31)==tail. Head/tail always
stay in 0..31. A full put increments dropped/epoch without changing any retained
sample or either index: **drop newest**, not overwrite oldest. Received counts
accepted bytes, not attempted bytes. Invalid indexes or exhausted 64-bit counters
fail closed, rather than wrapping into a valid state.

The hardware queue is private. Synthetic tests create local queue objects and
cannot inflate hardware counters. The ISR is its only producer. Foreground
`kbd_poll` saves/disables/restores caller IF around the entire pop/decode operation;
it rejects IRQ context and null output. Multiple kernel callers are serialized
on one CPU, but there is one logical stream, not subscriber broadcast. There is
no SMP or lock-free claim. `kbd_statistics` snapshots counters under the same IF
discipline. Stage 7's compiler barriers and IF handling are reused unchanged.

## Event and loss API

`kbd_poll` returns EMPTY, EVENT, LOST, NOT_READY or BAD_CONTEXT. EMPTY/LOST/error
leave output untouched. EVENT includes actual scan byte, physical key identity
and PRESS/RELEASE/UNKNOWN; it is not ASCII text. A gap is reported before the
first post-gap sample (or when the old queue drains); the decoder resets there.
Retained pre-gap samples remain in FIFO order. Multiple lost bytes may coalesce
into one LOST notification; exact dropped count remains available.

Clients must reset their own pressed/modifier state on LOST. Lost bytes cannot
be reconstructed, and the first post-gap byte can be inherently ambiguous.
This driver does not pretend to recover a missing prefix or key release.

Supported ordinary Set-1 identities:

| Key | Make | Break |
| --- | --- | --- |
| a / b / c / d | 1e / 30 / 2e / 20 | 9e / b0 / ae / a0 |
| Space / Enter | 39 / 1c | b9 / 9c |
| Left / right Shift | 2a / 36 | aa / b6 |

Repeated make bytes are repeated PRESS events. Shift keys are physical events
only: no case conversion, modifier bitmap, LED management or repeat synthesis.
Other ordinary codes are UNKNOWN. E0 packets are suppressed as a unit, so keypad
Enter is not misreported as ordinary Enter. E1 Pause tails are bounded/checked.
Malformed Pause discards the mismatching byte and resets; repeated E0 keeps
prefix suppression. Unknown/prefix bytes still appear as UNKNOWN raw events,
not falsely recognized ordinary keys. There is no complete extended-key support.

## Tests and invariants

`keyboard-test.c` is separate from production logic. Local instances test empty,
1..31 samples, eight overflow drops, exact retained contents/order, 16 repeated
fill/drain cycles across wraps, post-overflow reuse, counter/index rejection,
loss-boundary placement, every supported make/break, repeated makes, unknown
bytes, E0/Pause suppression and malformed-prefix reset.

The real boot test has eight host-selected single-key requests. The guest has
no expected key table or key order: it prints each actual raw/decoded event.
Default input is a,b,c,d,Space,Enter,a,d. Other runs shuffle the host sequence
after compilation and exercise both shifts and UNKNOWN x. IRQ0 concurrently
preempts a busy worker while IRQ1 feeds the queue. Worker reap restores exact
PMM/VM/heap accounting; PIC ISR and masks are checked afterward.

Host validation checks all sixteen actual bytes/identities/types against sent
keys, byte/IRQ/error/drop counters and cross-stage memory statistics. Independent
QEMU trace events must show device make/break in Set 2 with translation, then
PIC acknowledgment of vector 33, then the matching port-60 read, for each byte.
These traces are in `guest-errors.log` (the existing QEMU -D log); `run.json`
records sent keys and owned-process cleanup. No test claims QEMU keys are
physical keys on a real machine.

Meaningful implementation mutations must fail. A serial transcript alone
cannot satisfy the trace gate. This is empirical verification of reviewed code,
not cryptographic attestation against an adversary rewriting both kernel/tests.

## Dependencies and limitations

No new executable dependency. The documented QEMU build must support
`ps2_keyboard_event`, `pic_interrupt` and `pckbd_kbd_read_data` trace events.
Missing/malformed trace evidence fails, never silently skips verification.
Readiness markers avoid fixed startup sleeps; QEMU still times key release,
and the harness polls with an overall bounded deadline. The guest has no
independent input-wait watchdog when no key arrives.

No USB, mouse support, real-hardware validation, SMP, runtime recovery/reconnect,
full scancode repertoire, text layout, shell, or graphics is implemented.

Protocol references (not copied driver implementations):
[QEMU i8042 model](https://github.com/qemu/qemu/blob/v11.0.0/hw/input/pckbd.c),
[QEMU PS/2 model](https://github.com/qemu/qemu/blob/v11.0.0/hw/input/ps2.c),
[QEMU monitor sendkey](https://www.qemu.org/docs/master/system/monitor.html).
