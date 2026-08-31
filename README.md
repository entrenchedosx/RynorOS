<h1><img src="assets/branding/icon.png" width="56" height="56" alt="RynorOS icon"> RynorOS</h1>

An original operating-system project: **Rynorkernel**, with **RynorLang** (`.rl`)
planned as its native language. Inspired by the simplicity of TempleOS, not based
on its implementation, Linux, BSD, or an existing userspace.

## Current state — Stage 9 framebuffer/text output

This is a single-CPU kernel development platform, **not a usable or
production-ready OS**. The independently [audited Stage 7 scheduler](docs/reports/stage7-audit.md)
(repair, ownership and limits) and the [Stage 8 keyboard](docs/reports/stage8.md)
are the prior verified milestones. Stage 9 adds a narrow QEMU display path;
the [independent audit](docs/reports/stage9-audit.md) records corrections and
verification evidence. The [earlier audit](docs/reports/codebase-audit.md)
records the Stage 6 baseline, not current verification.

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
- PS/2 keyboard on i8042/IRQ1: a 31-sample drop-newest queue, explicit loss
  reporting, and a documented Set-1 subset. Host-selected QEMU keys are checked
  against actual events and independent device/IRQ/data-port traces.
- Validated QEMU standard-VGA PCI/BGA handoff, uncached RW/NX framebuffer,
  bounds-safe pixels/rectangles and a bounded uppercase/digit text subset.
  Complete framebuffer bytes and actual QEMU scanout are independently checked.

There is no user mode, process address-space switching, scheduler sleep/wake,
filesystem, shell, GUI/desktop, networking or RynorLang compiler.
The language examples cannot execute. No SMP, SIMD thread context, COW, swap,
demand paging or new large-page support exists.

## What the image actually does

Boot preserves its original regression prefix:

```text
Rynorkernel booted.
RynorOS 0.1.0 | x86_64 | stage1
```

It verifies CPU descriptors and breakpoint return, discovers/tests physical RAM,
replaces boot paging, tests permissions/faults, and tests the heap. Three PIT
heartbeat IRQs and the scheduler tests precede the Stage 8 banner:

```text
[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage8 hardware input
```

The scheduler tests exercise resource failure/rollback, lifecycle and locks,
then non-yielding workers under real timer interrupts. Four-, two- and
one-runnable-context cases are checked. Stage 8 configures the i8042 and keyboard,
reports eight host-selected keys (16 raw bytes, zero drops), and exercises IRQ1
while IRQ0 schedules another worker. Stage 9 validates its boot-time display
handoff, exercises MMIO rollback and guarded drawing/text tests, then paints a
1024x768 pattern/font atlas. Final PMM/VM/heap/scheduler/keyboard integrity
must pass before the bootstrap context halts with interrupts masked.
This is an explicit bounded test boot, not an interactive session or uptime service.

PIT configuration is 1193182/11932 Hz (about 99.99849 Hz); serviced IRQs are not
a wall-clock guarantee. Final retained memory in the normal display configuration
is fourteen page-table frames plus sixteen heap frames: 122880 allocated bytes.
The extra four table pages map foreign device VRAM, not PMM RAM. Worker stack frames and their
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
[Stage 9 audit](docs/reports/stage9-audit.md); test counts alone are not correctness.
Display evidence is retained as `display.pmem` and `display.ppm` beside each
successful normal boot's serial log; this is emulator, not physical-hardware evidence.

## Identity and layout

The header uses the established [official icon](assets/branding/icon.png).
Its original PNG is preserved in the [asset hierarchy](assets/README.md) and
packaged deterministically, never embedded in the boot image. The serial runtime
uses the same OS/kernel names and reports its real stage. **No guest renders the
icon**; PNG decoding and graphical UI remain future work. The framebuffer test
uses the same OS identity in text, not an invented icon conversion.

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
(Stage 9/schema 9). Implemented means present and verified under stated
conditions; planned/experimental does not mean executable.
