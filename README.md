# RynorOS

RynorOS is a new hobby operating-system project. Its original kernel is
**Rynorkernel**, and its intended native programming language is **RynorLang**
(source extension **`.rl`**).

The long-term direction is a small, self-contained, integrated system inspired
by TempleOS's simplicity and immediacy, not by reusing its implementation.
RynorOS will not be based on Linux, BSD, another kernel, or an existing OS userspace.

## Current status: Stage 3 — hardware timer interrupts

Implemented and tested in QEMU: an original BIOS disk loader, minimal x86-64
entry, a freestanding C kernel, polled COM1 output, deterministic image build,
and bounded boot tests. The original two-line boot prefix remains unchanged:

```text
Rynorkernel booted.
RynorOS 0.1.0 | x86_64 | stage1
```

After that prefix, the kernel now explicitly loads/verifies its own GDT and IDT,
triggers exactly one intentional breakpoint, prints the real CPU frame and all
15 general-purpose registers, returns through `IRETQ`, verifies restoration,
then initializes the PIC/PIT, receives three real IRQ0 ticks, reports their actual
counter values and halts with IRQs masked. The historical `stage1` banner identifies
the preserved boot-path contract; metadata describes the current Stage 3 scope.
See [CPU design](docs/design/cpu.md), [IRQ/timer design](docs/design/irq-timer.md),
and [exact observed output and verification](docs/reports/stage3.md).

There is **no memory manager, heap, scheduler, filesystem, shell, graphics,
userspace, networking, or RynorLang compiler**. `.rl` examples still cannot run.
Static page tables and a fixed stack exist only to enter 64-bit mode safely
on the supported emulator configuration. No new paging/memory-management facility,
privilege transition or process isolation exists. The PIT is configured at
1193182/11932 Hz (about 99.9984914516 Hz); ticks count actual serviced interrupts,
not a scheduler or an uptime service.

The [official RynorOS icon](assets/branding/icon.png) is preserved in the
[OS asset hierarchy](assets/README.md) and packaged separately as
`build/rynoros-resources.zip`. It is not embedded in the kernel or disk image,
loaded by the guest, or rendered: graphical support is not implemented.

Status labels throughout the project:

- **Implemented**: present and verified by the stated checks.
- **Planned**: intended work, not executable functionality.
- **Experimental**: unresolved design proposals, not compatibility promises.

## Host commands

Requires Python 3.10+ (standard library), NASM, Clang, LLD, and QEMU with its
SeaBIOS firmware for boot tests. See [dependency setup](docs/design/bootstrap-dependencies.md)
for verified versions, PATH/override configuration, and this Windows host's paths.
No Linux/WSL environment, host C library, third-party bootloader, Make, or ISO
utility is used. After configuring tools, run:

```text
python tools/build/build.py validate
python tools/build/build.py build
python tools/build/build.py test
python tools/build/build.py boot-test
python tools/build/build.py integration-test
python tools/build/build.py check
```

`validate` checks structure/metadata using Python alone. `build` also checks host
Python syntax, assembles/compiles/links Rynorkernel, creates `build/rynoros.img`,
and packages the original icon with a deterministic resource manifest.
`test` runs 40 repository, asset/package, image-layout, diagnostic-parser, and CLI tests (requires build tools).
`boot-test` builds and boots with a default 10-second timeout (`--timeout 15`
overrides it). `integration-test` builds and runs 13 execution/reproducibility
checks, including real #DE/#DB/#BP/#UD/#GP/#PF, timer IRQs and masked-IRQ/missing-EOI
negative cases. `check` runs build, repository tests, and
integration tests, stopping on failure. Commands return nonzero on failure and
resolve source paths relative to the script, not the caller's working directory.

Artifacts: `build/boot.bin`, `build/rynorkernel.elf`, `build/rynorkernel.bin`,
`build/rynoros.img`, `build/rynoros-resources.zip`, and `build/build-manifest.json`
(versions, sizes, SHA-256). The 1 MiB boot image includes no icon data.
Default QEMU logs and process cleanup evidence are in `build/boot-test/`; separate
CPU test images/logs are under `build/cpu-tests/`; negative timer logs are under
`build/timer-tests/`. Generated files are ignored by
Git. [Stage 1 verification](docs/reports/stage1.md) is a historical snapshot.

## Layout

| Path | Responsibility | Status |
| --- | --- | --- |
| `boot/` | Original BIOS sector loader and long-mode transition | Implemented Stage 1 |
| `kernel/` | Entry, COM1, GDT/IDT, CPU diagnostics, PIC/PIT IRQs | Implemented through Stage 3 |
| `assets/` | Canonical official icon, separately packaged, not rendered | Implemented Stage 3 |
| `rynorlang/` | Language design and future toolchain | Experimental design |
| `user/` | Future shell, libraries, applications | Planned |
| `tools/` | Host validation, image build, and QEMU runner | Implemented |
| `tests/repository/` | Repository validation tests | Implemented |
| `tests/integration/` | Boot/exception/IRQ execution, failure/cleanup, ELF, reproducibility | Implemented through Stage 3 |
| `tests/kernel/`, `tests/rynorlang/` | Future subsystem/conformance tests | Planned |
| `docs/design/`, `docs/reports/` | Decisions, subsystem template, verification reports | Foundation docs |
| `build/` | Generated outputs/logs; `.gitkeep` retained | Implemented output area |

Start with [architecture](ARCHITECTURE.md), [roadmap](ROADMAP.md),
[RynorLang design](rynorlang/README.md), and
[bootstrap dependencies](docs/design/bootstrap-dependencies.md).
[project.json](project.json) is machine-readable Stage 3 metadata (schema 4).

## License and contributions

The initial repository uses the [MIT license](LICENSE).
See [CONTRIBUTING.md](CONTRIBUTING.md) for scope, status, test, and commit rules.
