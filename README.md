# RynorOS

RynorOS is a new hobby operating-system project. Its original kernel is
**Rynorkernel**, and its intended native programming language is **RynorLang**
(source extension **`.rl`**).

The long-term direction is a small, self-contained, integrated system inspired
by TempleOS's simplicity and immediacy, not by reusing its implementation.
RynorOS will not be based on Linux, BSD, another kernel, or an existing OS userspace.

## Current status: audited early kernel with bounded heap

The codebase audit found real defects in the incoming heap despite its passing
tests. See [findings, repairs and verification](docs/reports/codebase-audit.md).
This is kernel bring-up infrastructure, not a usable or production-ready OS.

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
then initializes and tests a physical frame allocator from the real BIOS E820
map, exhausts/releases its discovered pool, replaces the boot page tables with
a PMM-backed kernel address space, tests real mapping, permissions, page faults,
unmapping and OOM rollback, runs the Stage 6 kernel-heap self-test, then initializes the PIC/PIT and receives three real
IRQ0 ticks. PMM, VM and heap integrity are checked again before halting with IRQs
masked. The historical `stage1` banner identifies the preserved boot-path
contract; metadata describes the current Stage 6 scope.
See [CPU design](docs/design/cpu.md), [IRQ/timer design](docs/design/irq-timer.md),
the [physical-memory design](docs/design/physical-memory.md),
the [virtual-memory design](docs/design/virtual-memory.md),
and [exact observed output and verification](docs/reports/stage5.md)
and [stage6.md](docs/reports/stage6.md).
The PMM manages real **4096-byte physical frames** using a bitmap placed in
discovered usable RAM. It protects firmware/boot/kernel/stack/map/bitmap extents,
rejects invalid/double frees and reports explicit exhaustion. Its physical
addresses are not automatically virtual mappings or zero-filled buffers.

The Stage 6 kernel heap is a small, bounded, boundary-tag first-fit allocator
over a 65536-byte arena of real PMM frames mapped RW/NX through the kernel
virtual space; see [the heap design](docs/design/heap.md). It is an internal
kernel facility, not a libc `malloc` or a user allocator.

There are **no scheduler, filesystem, shell, graphics,
userspace, networking, or RynorLang compiler**. `.rl` examples still cannot run.
Four-level paging uses 4096-byte pages, read-only kernel code/rodata, NX data,
and a temporary physical-frame window. Seven PMM-owned tables remain after the VM
test; heap initialization adds three tables and sixteen arena frames (106496
allocated bytes in total). The fixed 64 KiB heap does not grow or release its arena.
No user mode, process address spaces, COW, demand paging, swap, or new large-page
support exists. The PIT is configured at
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
`test` runs repository, asset/package, image-layout, diagnostic/PMM/VM/heap-parser,
and CLI tests (requires build tools); current counts are in the audit report.
`boot-test` builds and boots with a default 10-second timeout (`--timeout 15`
overrides it). `integration-test` builds and runs execution/reproducibility
checks, including real #DE/#DB/#BP/#UD/#GP/#PF, timer IRQs and masked-IRQ/missing-EOI
negative cases, real PMM/VM runs with 16/64/128/256 MiB, corrupted E820 handoffs,
broken CR3/TLB/table-zeroing/fault-arm variants, heap corruption/boundary/rollback
tests, 8/512 MiB runs, real RAM above 4 GiB, another CPU model and missing-NX rejection.
`check` runs build, repository tests, and
integration tests, stopping on failure. Commands return nonzero on failure and
resolve source paths relative to the script, not the caller's working directory.

Artifacts: `build/boot.bin`, `build/rynorkernel.elf`, `build/rynorkernel.bin`,
`build/rynoros.img`, `build/rynoros-resources.zip`, and `build/build-manifest.json`
(versions, sizes, SHA-256). The 1 MiB boot image includes no icon data.
Default QEMU logs and process cleanup evidence are in `build/boot-test/`; separate
CPU test images/logs are under `build/cpu-tests/`; negative timer logs are under
`build/timer-tests/`; PMM logs are under `build/pmm-tests/` and VM logs under
`build/vm-tests/`. Generated files are ignored by
Git. [Stage 1 verification](docs/reports/stage1.md) is a historical snapshot.

## Layout

| Path | Responsibility | Status |
| --- | --- | --- |
| `boot/` | Original bounded BIOS loader, E820 handoff and long-mode transition | Implemented through Stage 6 |
| `kernel/` | Entry, COM1, CPU diagnostics, PIC/PIT, PMM, virtual memory and kernel heap | Implemented through Stage 6 |
| `assets/` | Canonical official icon, separately packaged, not rendered | Implemented Stage 3 |
| `rynorlang/` | Language design and future toolchain | Experimental design |
| `user/` | Future shell, libraries, applications | Planned |
| `tools/` | Host validation, image build, and QEMU runner | Implemented |
| `tests/repository/` | Repository validation tests | Implemented |
| `tests/integration/` | Boot/exception/IRQ/PMM/VM/heap execution, failure/cleanup, ELF, reproducibility | Implemented through Stage 6 |
| `tests/kernel/`, `tests/rynorlang/` | Future subsystem/conformance tests | Planned |
| `docs/design/`, `docs/reports/` | Decisions, subsystem template, verification reports | Foundation docs |
| `build/` | Generated outputs/logs; `.gitkeep` retained | Implemented output area |

Start with [architecture](ARCHITECTURE.md), [roadmap](ROADMAP.md),
[RynorLang design](rynorlang/README.md), and
[bootstrap dependencies](docs/design/bootstrap-dependencies.md).
[project.json](project.json) is machine-readable Stage 6 metadata (schema 7).

PMM `reserved_bytes` includes explicitly reported firmware address-space windows
(including high MMIO), not merely RAM consumption. `usable_bytes` is the actual
allocatable pool after reservations; `free_bytes + allocated_bytes` equals it.
Holes remain unavailable. No memory size is supplied by the host to the allocator.

## License and contributions

The initial repository uses the [MIT license](LICENSE).
See [CONTRIBUTING.md](CONTRIBUTING.md) for scope, status, test, and commit rules.
