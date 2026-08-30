<h1><img src="assets/branding/icon.png" width="56" height="56" alt="RynorOS icon"> RynorOS</h1>

An original operating-system project: **Rynorkernel**, with **RynorLang** (`.rl`)
planned as its native language. Inspired by the simplicity of TempleOS, not based
on its implementation, Linux, BSD, or an existing userspace.

## Current state — audited Stage 7 kernel execution

This is a single-CPU kernel development platform, **not a usable or
production-ready OS**. The independent [Stage 7 audit](docs/reports/stage7-audit.md)
found unsafe ownership and scheduler transitions despite prior completion claims.
The repaired implementation and limits are documented in the
[scheduler design](docs/design/scheduler.md). The [earlier audit](docs/reports/codebase-audit.md)
records the Stage 6 baseline, not current scheduler verification.

Implemented and exercised in QEMU:

- Original BIOS/SeaBIOS boot, x86-64 entry, COM1 serial, kernel GDT/IDT and real
  exception diagnostics.
- PIC/PIT IRQs, real E820 memory discovery, 4096-byte physical-frame allocation.
- PMM-owned four-level paging, mapping/unmapping/translation, RO/NX enforcement,
  real page faults and TLB invalidation.
- A fixed 64 KiB kernel heap.
- Seven worker slots plus bootstrap; four-page RW/NX stacks with an **unmapped,
  unbacked** guard; owned stack teardown and non-reused thread IDs.
- Round-robin timer preemption, cooperative yield, exit and nonblocking join/reap.
  Interrupt-state preservation and single-CPU lock contracts.

There is no user mode, process address-space switching, scheduler sleep/wake,
filesystem, shell, input driver, graphics, networking or RynorLang compiler.
The language examples cannot execute. No SMP, SIMD thread context, COW, swap,
demand paging or new large-page support exists.

## What the image actually does

Boot preserves its original regression prefix:

```text
Rynorkernel booted.
RynorOS 0.1.0 | x86_64 | stage1
```

It verifies CPU descriptors and breakpoint return, discovers/tests physical RAM,
replaces boot paging, tests permissions/faults, and tests the heap. After three
PIT heartbeat IRQs, it identifies the current execution subsystem:

```text
[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage7 kernel execution
```

The scheduler tests exercise resource failure/rollback, lifecycle and locks,
then non-yielding workers under real timer interrupts. Four-, two- and
one-runnable-context cases are checked. Final PMM/VM/heap/scheduler integrity
must pass before the bootstrap context halts with interrupts masked.
This is an explicit bounded test boot, not an interactive session or uptime service.

PIT configuration is 1193182/11932 Hz (about 99.99849 Hz); serviced IRQs are not
a wall-clock guarantee. Final retained memory is ten page-table frames plus
sixteen heap frames: 106496 allocated bytes. Worker stack frames and their
temporary table branch are reclaimed after join.

## Build and verify

Host requirements: Python 3.10+ standard library, NASM, Clang, LLD, and QEMU
with SeaBIOS. No WSL, host libc, third-party kernel/loader, Make or ISO utility.
See [dependency setup](docs/design/bootstrap-dependencies.md) for paths and versions.

```text
python tools/build/build.py validate
python tools/build/build.py build
python tools/build/build.py boot-test
python tools/build/build.py test
python tools/build/build.py integration-test
python tools/build/build.py check
```

`validate` checks metadata/assets/structure, not CPU execution. `build` compiles
and links original guest code, creates the 1 MiB raw boot image and packages
resources separately. `test` runs repository/parser/build-failure checks.
`boot-test` builds and captures actual serial output with a default 10-second
deadline (`--timeout` can override it). `integration-test` includes normal and
deliberately broken images, hardware faults, RAM-layout variations, ELF state
comparisons and byte-identical rebuilds. `check` runs build and both suites.

Artifacts under ignored `build/`: `boot.bin`, `rynorkernel.bin`,
`rynorkernel.elf`, `rynoros.img`, `rynoros-resources.zip` and
`build-manifest.json`. Logs include serial transcripts and owned-QEMU cleanup
records. Exact current counts, commands and evidence are in the
[Stage 7 audit](docs/reports/stage7-audit.md); test counts alone are not correctness.

## Identity and layout

The header uses the established [official icon](assets/branding/icon.png).
Its original PNG is preserved in the [asset hierarchy](assets/README.md) and
packaged deterministically, never embedded in the boot image. The serial runtime
uses the same OS/kernel names and reports its real stage. **No guest renders the
icon**; graphics and PNG decoding remain future work.

| Path | Responsibility |
| --- | --- |
| `boot/` | Original BIOS loader, E820 handoff, long-mode transition |
| `kernel/` | CPU/IRQs, PMM/VM/heap, stacks and kernel execution |
| `assets/` | Canonical identity resource, packaged separately |
| `tools/`, `tests/` | Host builds and explicit repository/hardware verification |
| `rynorlang/`, `user/` | Clearly labeled future language/userspace work |
| `docs/design/`, `docs/reports/` | Contracts, limitations and audit evidence |

Start with [architecture](ARCHITECTURE.md), [roadmap](ROADMAP.md),
[contributing](CONTRIBUTING.md), and [project metadata](project.json)
(Stage 7/schema 8). Implemented means present and verified under stated
conditions; planned/experimental does not mean executable.
