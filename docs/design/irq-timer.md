# External IRQs and hardware timer

## Purpose and implementation status

Implemented Stage 3: original dual-8259 PIC programming, IRQ dispatch, and PIT
channel 0 interrupts on the existing single-CPU QEMU PC/SeaBIOS path. This is
a bounded hardware timer self-test, **not a scheduler** or a timekeeping service.
The CPU exception mechanisms and six execution-tested vectors remain intact.

## Controller and timer choice

The pinned `pc-i440fx-10.0` machine supplies a legacy PIC and PIT. They need no
ACPI parser, APIC discovery, MMIO mapping, or SMP support, so they fit this boot
milestone. No additional host tool or imported kernel implementation is needed.

`pic.c` programs both PICs with IF=0: mask all lines; ICW1=0x11 (edge-triggered,
cascaded, ICW4 required); ICW2=32/40; ICW3=4/2 (slave on master IRQ2);
ICW4=1 (8086 mode, manual EOI, normal nested priority); mask all again and verify
mask/ISR readback. Port 0x80 writes supply legacy I/O recovery delays during
configuration. Master command/data ports are 0x20/0x21; slave 0xa0/0xa1.

| CPU vector | Source | Path |
| --- | --- | --- |
| 0..31 | Architectural CPU exceptions | Existing `exception_dispatch`, no PIC EOI |
| 32 | Master IRQ0, PIT channel 0 | `irq_dispatch`, timer callback, master EOI |
| 33..39 | Master IRQ1..7 | Installed gates; masked (IRQ2 reserved cascade) |
| 40..47 | Slave IRQ8..15 | Installed gates; masked; slave then master EOI |
| 48..255 | Unassigned | Non-present gates |

Mask updates maintain the cascade: IRQ2 is unmasked only when at least one slave
line is enabled. Stage 3 enables only IRQ0. UART interrupt generation and NMI
remain disabled. No APIC or SMP initialization is implemented.

PIT ports 0x43/0x40 receive control 0x34 (channel 0, low byte then high byte,
binary mode 2) and divisor **11932** (0x2e9c). Status read-back command 0xe2
checks the programmed access/mode/BCD bits. QEMU's input clock is **1193182 Hz**:
the exact configured rate is **1193182 / 11932 Hz**, approximately
**99.9984914516 Hz**, not exactly 100 Hz. This is a hardware configuration, not
a host-wall-clock timing guarantee; PIT edges may coalesce if servicing is slow.
Ticks count serviced IRQs, not elapsed time. Only channel 0 is reprogrammed.

Hardware references (protocol descriptions, not copied implementations):
[Intel 8259A datasheet, interrupt sequence](https://www.alldatasheet.com/html-pdf/66107/INTEL/8259A/898/7/8259A.html),
[Intel 8259A, spurious requests](https://www.alldatasheet.com/html-pdf/66107/INTEL/8259A/2317/18/8259A.html),
[Intel 8254-compatible timer documentation](https://cdrdv2-public.intel.com/332995/332995-skl-io-platform-datasheet-vol1_rev004.pdf),
[QEMU PIT clock definition](https://github.com/qemu/qemu/blob/master/include/hw/timer/i8254.h).

## Public interfaces and invariants

These are kernel-internal declarations in `kernel/include/irq.h`, not a public
userspace ABI or general driver framework:

- `irq_initialize()` initializes the controller once; returns zero on failure.
- `irq_register(irq, handler)` installs one non-null callback per line. Requires
  IF=0 and initialized controller; rejects duplicates, IRQ2 and out-of-range IDs.
  There is deliberately no dynamic unregister/replacement operation yet.
- `irq_set_enabled(irq, enabled)` changes masks with IF=0; enabling requires a
  registered handler. PIC register readback is checked. Cascade is automatic.
- `irq_dispatch(frame)` receives the saved frame from assembly. Valid real IRQs
  invoke a callback, then acknowledge the PIC. It never enters CPU diagnostics.
- `pic_initialize`, `pic_set_enabled`, `pic_in_service`, `pic_eoi` are low-level
  architecture internals; callers must obey the same single-CPU/IF=0 contract.
- `timer_self_test()` owns channel 0 and its static state for this one-shot test.

The additional 16 stubs push synthetic error=0 and vectors 32..47. They share
the existing full GPR save, DF clearing, stack alignment and `IRETQ` restore
mechanism. The vector at frame offset 120 chooses a separate C IRQ dispatcher;
the exception frame layout and CR2 diagnostic contract do not change. GDT and
IDT readback now verifies all 48 gates before the existing breakpoint test runs.
Both paths use interrupt gates (0x8e), CPL0, selector 0x08, IST0. No new stack,
privilege transition, scheduler context or paging mechanism is introduced.

The dispatcher verifies IF=0, saved IF=1, normalized error=0 and the matching
PIC ISR bit before calling the registered handler. A software `INT 32` cannot
satisfy that hardware in-service check. Unexpected non-spurious IRQ state halts
fail-closed without a success marker; it does not block in serial output.

EOI is manual, after the handler: slave first for IRQ8..15, then master; only
master for IRQ0..7. OCW3=0x0b reads ISR. Spurious IRQ7 with ISR7 clear needs no
EOI. Spurious IRQ15 with slave ISR7 clear acknowledges only the master cascade
if in service. These spurious/slave paths are implemented but not independently
hardware-injected in the Stage 3 suite. Interrupt nesting is disabled.

## Tick counter and controlled test

The callback alone increments a static volatile 64-bit counter. It saves the
first three actual counter values in a three-entry static sample buffer, then
masks IRQ0 on tick three. The bounded buffer tolerates delayed foreground work.
There is no serial output, allocation, general loop, or waiting in the handler.
Mask operations use fixed I/O and readbacks; the dispatcher handles EOI.

The foreground first verifies rejection of invalid/cascade/null/duplicate
registration and unregistered enable requests. It programs the timer, prints
the setup/wait markers, unmasks IRQ0 and waits for samples. Conditions are
checked with IF=0, followed by adjacent `STI; HLT; CLI`. STI's interrupt shadow
prevents an interrupt-before-HLT lost-wakeup race. Spurious wakeups simply
recheck the counter. There is no arbitrary delay or busy-wait for a tick.

Each `[TIMER] tick=N` formats a recorded counter value in decimal; the numbers
are not canned serial strings. The completion marker requires three samples,
exactly three serviced ticks, a cleared PIC ISR, both PIC masks=0xff and IF=0.
Foreground progress after each wake demonstrates return from the interrupt
entry/handler/EOI/IRETQ path. It then flushes serial and returns to CLI/HLT.
The PIT continues oscillating, but all device lines remain masked. Timer service
is intentionally stopped after the test rather than silently promising uptime.

## Tests and known limitations

The default QEMU boot must contain the unchanged boot prefix, complete Stage 2
breakpoint diagnostics, and exact ordered timer transcript. The harness waits
on explicit serial markers with a 10-second deadline, normally quits QEMU via
its monitor and always reaps its owned process. Each run records cleanup/PID.
Repository tests reject missing, changed, extra and reordered timer records.

Real negative kernel copies leave IRQ0 masked (zero ticks) or omit master EOI
(one tick then no further delivery). Both must time out at two seconds and
never print timer success. No fault switches are compiled into the normal
kernel. All six CPU exception cases and original Stage 1 tests still run.

Unsupported: scheduler, preemption, uptime API, variable-frequency timer API,
APIC/SMP, device drivers other than boot/serial/PIC/PIT, TSS/IST/emergency stacks,
memory allocation or protection policy, physical hardware validation, nested
interrupts and recovery from a failed stack. Guest waits have no independent
clock-based watchdog; the host timeout detects a missing timer. No claim of
exact host timing or lossless timer-edge accounting is made. Stage 4 is planned.
