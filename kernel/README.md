# Rynorkernel

## Purpose

Original x86-64 kernel for RynorOS. Stage 1 only boots, prints serial output,
and halts; it is not built on another kernel or existing OS userspace.

## Public interfaces

Internal Stage 1 entry `rynorkernel_entry` in `arch/x86_64/entry.asm` requires
ring-0 long mode, selectors and identity mapping from `../boot/README.md`, and
disabled interrupts/NMI. It sets RSP=0x80000, clears linker-defined BSS, calls
`core/main.c:kernel_main` using the SysV x86-64 ABI, then enters CLI/HLT forever.
There is no public syscall or executable ABI.

`include/serial.h` declares `serial_init`, `serial_write`, and `serial_flush`.
Implementation is `arch/x86_64/serial.c`: COM1 at 0x3f8, divisor 1 (115200), 8N1,
FIFOs enabled/cleared, UART interrupts disabled. Write/flush return zero after
1,000,000 polls if the required transmitter state does not appear. Callers
provide a valid NUL-terminated string; there is no memory-safety boundary.
The kernel returns to its halt loop on output failure; the host test then fails
if both complete lines were not delivered. No receiving or interrupt driver exists.

## Invariants

No host libc, OS APIs, dynamic loader, compiler runtime library, floating-point,
SIMD, stack protector runtime, or red zone. Stack alignment is 16 bytes before
CALL. Interrupts are never enabled by the kernel. The linker enforces that
payload and BSS end at or below 0x10000. Memory is statically reserved, not allocated.

## Implementation status

Implemented: 64-bit entry, BSS initialization, fixed stack, C main, serial output,
and fixed-layout linking. `mm/`, `interrupts/`, and `drivers/` remain reserved;
the only device code is the small architecture-specific serial implementation.
No later subsystem or RynorLang implementation has been added.

## Tests

`python tools/build/build.py boot-test` builds and executes the kernel.
`integration-test` also checks byte-identical rebuilds, ELF identity, negative
boot output, bounded failure, and QEMU cleanup. `test` covers build/link errors.

## Known limitations

Only the documented QEMU PC configuration is verified. No IDT/fault handlers,
allocator, heap, scheduler, filesystem, graphics, userspace, or hardware support
beyond boot/serial. No guard page, stack-overflow detection, memory-map validation,
or writable/executable separation. Unhandled exceptions can reset the CPU;
`-no-reboot` lets the harness detect that as failure. Stage 2 remains future work.
