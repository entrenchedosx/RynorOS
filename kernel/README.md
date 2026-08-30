# Rynorkernel

## Purpose

An original kernel for RynorOS, initially targeting a single x86-64 CPU.

## Public interfaces

None implemented. Future boot handoff, architecture entry, allocator, interrupt,
driver, and syscall interfaces require written contracts before callers use them.

## Invariants

The kernel must not depend on another OS kernel/userspace. Validate external
inputs; explicitly track memory/resource ownership; never enable interrupts
before handlers and controller state are ready. See `../ARCHITECTURE.md`.

## Implementation status

Planned only. Reserved areas: `arch/` CPU-specific code; `core/` startup/tasks;
`mm/` memory management; `interrupts/` dispatch; `drivers/` devices; `include/`
shared kernel declarations. These directories contain no implementations.

## Tests

Only directory-presence checks exist. Future behavior tests belong in
`../tests/kernel/` and emulator-level checks in `../tests/integration/`.

## Known limitations

No entry point, build target, memory manager, drivers, scheduler, or executable
image. Exact CPU features and ABI remain undecided.
