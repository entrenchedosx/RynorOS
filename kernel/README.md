# Rynorkernel

## Purpose

Original x86-64 kernel for RynorOS. Stages 1–2 boot, print serial output, load
kernel descriptors, diagnose one controlled exception, and halt. It is not
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
Only the armed default breakpoint returns; unexpected exceptions print and halt.

`include/serial.h` declares `serial_init`, `serial_write`, and `serial_flush`.
Implementation is `arch/x86_64/serial.c`: COM1 at 0x3f8, divisor 1 (115200), 8N1,
FIFOs enabled/cleared, UART interrupts disabled. Write/flush return zero after
1,000,000 polls if the required transmitter state does not appear. Callers
provide a valid NUL-terminated string; there is no memory-safety boundary.
The kernel returns to its halt loop on output failure; the host test then fails
if complete diagnostics were not delivered. No receiving or device IRQ driver exists.

## Invariants

No host libc, OS APIs, dynamic loader, compiler runtime library, floating-point,
SIMD, stack protector runtime, or red zone. Stack alignment is 16 bytes before
CALL. Interrupts are never enabled by the kernel. The linker enforces that
payload and BSS end at or below 0x10000. Memory is statically reserved, not allocated.

## Implementation status

Implemented: 64-bit entry, BSS initialization, fixed stack, C main, serial output,
and fixed-layout linking; kernel GDT/IDT, 32 exception entry stubs, shared C
diagnostics in `interrupts/exceptions.c`, and an assembly-controlled self-test.
`mm/` and `drivers/` remain reserved;
the only device code is the small architecture-specific serial implementation.
No later subsystem or RynorLang implementation has been added.

## Tests

`python tools/build/build.py boot-test` builds and executes the kernel.
`integration-test` also checks byte-identical rebuilds, ELF identity, negative
boot output, bounded failure, and QEMU cleanup, plus real #DE/#DB/#BP/#UD/#GP/#PF
and unarmed-breakpoint tests. `test` covers build/link and diagnostic-parser errors.

## Known limitations

Only the documented QEMU PC configuration is verified. No allocator, heap,
scheduler, filesystem, graphics, userspace, privilege transitions, TSS/IST,
or external IRQ handling. No guard page, stack-overflow detection, memory-map
validation, or writable/executable separation. Invalid stacks, early boot faults,
or faults during diagnosis can still reset the CPU; `-no-reboot` makes the runner
fail. Other exception stubs are best-effort/unexercised, not feature-enablement
claims. See `../docs/design/cpu.md`. Stage 3 remains future work.
