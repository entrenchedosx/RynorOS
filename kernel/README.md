# Rynorkernel

## Purpose

Original x86-64 kernel for RynorOS. Stages 1–10 boot, print serial output, load
kernel descriptors, diagnose one controlled exception, initialize and test E820-
based physical frame allocation, replace/test kernel paging, run a bounded kernel-
heap self-test, verify three PIC/PIT
timer IRQs, verify kernel threads and preemption, receive/decode real PS/2
keyboard input on IRQ1, validate and paint the framebuffer, run bounded
strings/byte rings and ring-0 runtime services on worker threads, then run the
final integrity gate, mask interrupts and halt. It is not
built on another kernel or existing OS userspace.

## Public interfaces

Internal Stage 1 entry `rynorkernel_entry` in `arch/x86_64/entry.asm` requires
ring-0 long mode, selectors and identity mapping from `../boot/README.md`, and
disabled interrupts/NMI. It sets RSP=0x80000, clears linker-defined BSS, calls
`core/main.c:kernel_main` using the SysV x86-64 ABI, then enters CLI/HLT forever.
There is no public syscall or executable ABI.

Stage 2 `include/cpu.h` declares `cpu_initialize`, `cpu_exception_self_test`,
`exception_dispatch`, and `cpu_halt`, all kernel-internal. The kernel replaces
the boot GDT with code selector 0x08/data selector 0x10 and installs the exception
IDT; `docs/design/cpu.md` specifies the exact saved-frame and recovery contract.
The armed default breakpoint returns; Stage 5 also permits exact one-shot #PF
test recovery. Unexpected exceptions print and halt.

Stage 3 `include/irq.h` adds controller setup, static handler registration,
mask control, separate IRQ dispatch and `timer_self_test`. See
`../docs/design/irq-timer.md` for exact contracts, EOI/spurious behavior, frequency
and the bounded foreground test. All device IRQs are masked except IRQ0 during
that test. CPU exceptions remain independent of the PIC and never send EOI.

Stage 4 `include/boot_memory.h` and `include/pmm.h` define the validated firmware
handoff and physical frame API. `mm/README.md` and
`../docs/design/physical-memory.md` specify normalization, bitmap ownership,
linker/firmware reservations, explicit failure codes and statistics. Allocation
returns 4096-byte physical frames; it does not map or zero them. PMM testing
precedes VM and IRQ0 testing; accounting is rechecked after the interrupts.

Stage 5 `include/vm.h` and `include/paging.h` define four-level tables and a
PMM-backed mapping API. `mm/vm.c` owns table creation/destruction, range rollback,
permissions, queries, CR3 replacement and INVLPG; `mm/vm-test.c` handles real
page-fault diagnostics and narrowly controlled hardware tests. The canonical
layout, frame-window lifetime and ownership rules are in
`../docs/design/virtual-memory.md`. There are no process address spaces yet.

Stage 6 `include/heap.h` and `mm/heap.c` define a small, bounded, boundary-tag
first-fit kernel heap over a fixed 65536-byte arena of PMM frames mapped RW/NX
through the Stage 5 kernel space at `HEAP_BASE`. Allocation/free coalesce free
blocks and detect boundary corruption; `heap_check` walks the arena and validates
accounting. `heap_self_test` runs in `core/main.c` after the VM self-test and
before the PIC/PIT timer. It requires one CPU and IF=0; it is an internal kernel
allocator, not a libc `malloc`. See `../docs/design/heap.md`.

Stage 7 `include/ksched.h`, `core/thread.c`, `mm/kstack.c` and
`arch/x86_64/switch.asm` define bounded single-CPU kernel threads, real per-thread
kernel stacks (each a faulting guard page below RW/NX payload frames), genuine
context switching, and a PIT-IRQ0-driven round-robin scheduler. `scheduler_self_test`
runs in `core/main.c` after the timer and proves real preemption; `scheduler_check`
is part of the final integrity gate. See `../docs/design/scheduler.md`.

Stage 8 `include/kbd.h` and `drivers/keyboard.c` provide the PS/2 keyboard on the
i8042 controller (ports 0x60/0x64) on IRQ1: the ISR pushes raw scan codes into a
bounded drop-newest ring (no allocation/serial/blocking), and a set-1 decoder maps
the documented subset to physical press/release events with prefix isolation.
`drivers/keyboard-test.c` separately tests local queue instances, then eight
host-selected keys (16 bytes, zero drops) while IRQ0 preempts a worker.
The host checks independent QEMU device/PIC/data-read traces. Queue loss is
explicit and public consumption saves/restores IF. See `../docs/design/keyboard.md`.

Stage 9 `include/display.h` and `drivers/display.c` provide a validated Bochs VBE
linear frame buffer: the real-mode handoff programs BGA registers (1024x768x32,
ENABLE 0x41) and reads the LFB base from PCI BAR0, the driver maps the whole
frame via `vm_map_device` at `VM_MMIO_BASE` and exposes a clipped, bounds-checked
rect/pixel/text API over BGRX 32 BPP. `drivers/display-test.c` paints pattern 0,
reads it back through the driver, and re-verifies cross-stage accounting; the
host independently verifies every framebuffer byte via QEMU HMP `pmemsave` and
every scanout pixel via `screendump`. The 64-byte version-2 handoff includes a
verified PCI aperture; MMIO rejects PMM RAM and uses supervisor RW/NX UC pages.
The shared surface algorithms have guarded-buffer tests; real PMM exhaustion
tests table/mapping rollback. Supported text is A-Z, 0-9, space and .-:/?, with
newline/carriage return, clipped transparent 5x7 glyphs and a 128-character cap.
See `../docs/design/framebuffer.md`.

Stage 10 `include/kstring.h`, `include/kbuf.h`, `include/krst.h` and the
`runtime/` sources provide the basic kernel runtime: bounded strings and a
transactional two-pass `kstr_format`, bounded byte rings, and ring-0 services
(FNV-1a 64 digest, uppercase, digit count) dispatched through `krst_call` with
overlap/undersize/bad-op/bad-arg rejection. `runtime/runtime-test.c` first runs
synthetic bounds tests, then drives the services from seven worker threads via
the Stage 7 scheduler, folding 40 digests per worker and reporting the result.
The host independently recomputes every worker fold, the total, the format
outputs and the buffer wrap payload. Mandatory physical worker records also
prove saved hardware IRQ RIP/RSP within service code/owned stacks; fixed
serial values alone cannot reject canned output. `runtime_self_test` runs from `core/main.c` after display and before the
final integrity gate. This is ring-0 runtime infrastructure only; there is no
userspace or syscall (Stage 18). See `../docs/design/runtime.md`.

`include/serial.h` declares `serial_init`, `serial_write`, and `serial_flush`.
Implementation is `arch/x86_64/serial.c`: COM1 at 0x3f8, divisor 1 (115200), 8N1,
FIFOs enabled/cleared, UART interrupts disabled. Write/flush return zero after
1,000,000 polls if the required transmitter state does not appear. Callers
provide a valid NUL-terminated string; there is no memory-safety boundary.
The kernel returns to its halt loop on output failure; the host test then fails
if complete diagnostics were not delivered. No serial receiver or UART IRQ driver exists.

## Invariants

No host libc, OS APIs, dynamic loader, compiler runtime library, floating-point,
SIMD, stack protector runtime, or red zone. Stack alignment is 16 bytes before
CALL. IF is enabled in timer waits and running threads, never inside a handler.
PMM/VM/heap/stack mutation remains foreground IF=0. The linker enforces that
the loaded payload and BSS end at or below 0x70000. Bootstrap state
is statically reserved; only real E820-usable unreserved frames enter PMM.

## Implementation status

Implemented: 64-bit entry, BSS initialization, fixed stack, C main, serial output,
and fixed-layout linking; kernel GDT/IDT, 32 exception entry stubs, shared C
diagnostics in `interrupts/exceptions.c`, and an assembly-controlled self-test;
16 IRQ stubs, `interrupts/irq.c` dispatch, `arch/x86_64/pic.c` and `timer.c`.
`mm/` implements map validation, real frame allocation, virtual memory, the
bounded kernel heap and self-tests; `drivers/` holds the Stage 8 keyboard and
Stage 9 framebuffer drivers; `runtime/` holds the Stage 10 bounded
string/buffer/service sources;
device code covers architecture-specific serial, PIC, PIT and keyboard support.
No RynorLang implementation has been added.

## Tests

`python tools/build/build.py boot-test` builds and executes the kernel.
`integration-test` also checks byte-identical rebuilds, ELF identity, negative
boot output, bounded failure, and QEMU cleanup, plus real #DE/#DB/#BP/#UD/#GP/#PF
and unarmed-breakpoint tests. It also verifies three real IRQ0 returns, and
negative masked-IRQ/missing-EOI behavior. `test` covers build/link, diagnostic
parsing and the separate icon resource package, which no kernel code reads.
PMM integration covers 16/64/128/256 MiB, physical writes, full-pool exhaustion,
release/reuse, exact accounting and corrupted firmware-map rejection. All live
linked boot/kernel ranges are checked against the reported final map.
VM tests exercise real CR3 replacement, hardware writes/execution/faults, permission
changes, high frames, unmapping/TLB behavior, range rollback, table zeroing and
allocation failure, plus broken CR3/TLB/zeroing/fault-arm kernel variants.

## Known limitations

Only the documented QEMU PC configuration is verified. No
filesystem, GUI or general graphics stack, userspace, privilege transitions, TSS/IST,
or general external device support beyond IRQ0, the single PS/2 keyboard on
IRQ1. No reliable stack-overflow recovery,
process isolation or address-space switching. Invalid stacks, early boot faults,
or faults during diagnosis can still reset the CPU; `-no-reboot` makes the runner
fail. Other exception stubs are best-effort/unexercised, not feature-enablement
claims. See `../docs/design/cpu.md`, `../docs/design/irq-timer.md` and
`../docs/design/physical-memory.md`, `../docs/design/virtual-memory.md` and
`../docs/design/heap.md`.
