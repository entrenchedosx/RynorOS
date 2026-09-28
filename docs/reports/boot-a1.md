# RYNOROS BOOT-A1 close-out — 384 KiB kernel-load ceiling removed

RYNOROS BOOT-A1 COMPLETE — HISTORICAL 384 KiB KERNEL CEILING REMOVED
DEMONSTRATED KERNEL CAPACITY — 16,150,528-byte memory footprint (3943 pages) + 6,574,592-byte file
NEXT HARDWARE BLOCKER — APIC/ACPI enumeration (LAPIC + IOAPIC + MSI/MSI-X); PIC/PIT frozen

## Archaeology

The historical loader put the kernel at `0x8000` and grew it toward
`0x70000`: 384 KiB total, shared with the boot part, the PMM metadata,
and every page table the boot created. The DMA-A1 measurement showed
7,780 bytes of headroom left — one APIC/ACPI subsystem, let alone
xHCI/NVMe/graphics, would not fit. Any fix that kept the kernel under
1 MiB only moved the wall; BOOT-A1 moved the kernel above it.

## New architecture

- Kernel links at `0x800000` (8 MiB). File <= 8 MiB (16384 sectors),
  mem <= 16 MiB (4096 pages); linker, builder, and loader enforce the
  same bounds, pinned against each other by
  `tests/repository/test_boot_layout.py`.
- Image layout: sector 0 boot signature, LBA 9 checksummed header
  (magic, version, file/mem sizes, load base, entry, FNV-1a over the
  sector-padded kernel file), kernel file at LBA 10+.
- Loader: reads the header first, validates every field, requires one
  E820 usable entry spanning the whole kernel range, stages the file
  through a 64-sector low bounce buffer (B1 proves 41 chunks), copies
  to 8 MiB, re-verifies the checksum over the loaded bytes, checks A20,
  and jumps. Any mismatch halts with a distinct `Rynor boot: ...`
  line; control never reaches a partial kernel.
- Boot page tables map `[0, N*2 MiB)` with N from the header, capped
  at 12 entries (24 MiB); the PMM then owns everything the tables and
  the kernel image do not reserve.

## Kernel side

- Placement and page tables come from the linked symbols
  (`__kernel_start`, `__kernel_end`), never from constants: the old
  `0x8000`/`0x70000` assumptions are gone from `pmm.c`, `vm.c`,
  `user.c`, and the self-tests.
- PMM reserves the full high image (kind 8) from the firmware map;
  the host replays the same reservation from the ELF, so accounting
  is layout-driven at every kernel size.
- The #PF self-test probe moved from `0x200000` to `0x2000000`: the
  old probe address is inside the new boot identity map, so it no
  longer faults. 32 MiB sits above the 24 MiB boot-map cap, so it is
  unmapped at probe time for every kernel size.

## Oversized-kernel matrix (4/4)

`tests/integration/test_boot_matrix.py`, each a full QEMU boot:

```text
B1  2601 sectors / 360 pages    8/7 tables   multi-chunk copy, one region
B2   561 sectors / 744 pages    9/8 tables   crosses 10 MiB, two regions
B3   561 sectors / 2152 pages  12/11 tables  five regions
B4  12841 sectors / 3943 pages 15/14 tables  all eight regions, near capacity
```

B4 fills 96.3% of the memory cap and 78.4% of the file cap.

## Loader mutants (12/12)

`tests/integration/test_boot_mutants.py`: bad magic/version/flags,
file/mem bounds, load base, entry, corrupt byte, bogus header LBA,
tiny RAM, inverted checksum branch, broken A20 expectation — each
halts in the loader with its exact diagnostic and never enters the
kernel; each restores to green.

## DMA and PCI frozen

No DMA/PCI behavior changed. Two pins moved with the layout: the DMA
first-fit base `0x11f000` -> `0x120000` (one more page-table page
below the pool; `bus == phys` and every structural rule re-verified),
and the PCI suite passes untouched. xHCI note (correction): xHCI
supports PCI pin/INTx mode for Interrupter 0, so initial
single-interrupter bring-up does not require MSI/MSI-X; MSI/MSI-X
remains the preferred modern path for a multi-vector design.

## Docs

`docs/design/boot.md` (architecture, verification, capacity),
`docs/design/cpu.md` (probe address), `docs/design/dma.md`
(first-fit base), README/ARCHITECTURE/`docs/design/shell.md`
(inventory 1100 repository + 474 integration; 8 MiB fail-closed).

## UEFI boundary

The loader is BIOS-only (INT 0x13, E820, A20, VGA handoff). UEFI boot
services, GOP, ACPI-table parsing from firmware, and multiprocessor
startup remain future work; none was started here.

## Fresh interrupt audit

Measured state: dual-8259 PIC, vectors 32-47, manual EOI with cascade
and spurious-line discipline; 16-line handler table; IRQ0 = PIT
heartbeat/scheduler drive, IRQ1 = keyboard; IRQ2 cascade reserved;
all other lines masked. The kernel contains no LAPIC/IOAPIC/MSI/
MSI-X/ACPI-table code (only E820-kind constants and "no ACPI/MCFG
here yet" comments). PIC/PIT behavior is frozen by BOOT-A1 and
re-verified by the boot/keyboard/scheduler suites, including the
missing-EOI and masked-line mutants.

Next dependency: APIC/ACPI enumeration is the measured next hardware
blocker — RSDP/MADT parsing, IOAPIC routing, LAPIC timer/IPI — with
MSI/MSI-X on top. Room now exists to build it properly.

## Regression inventory

Green: matrix, mutants, boot, display, keyboard, pmm, vm, audit,
heap, scheduler, runtime, dma, pci, shell, userspace, input, proc,
pipe, load, rt, filesystem, storage, cplshell, p1a, p1a2, p1a3,
rlen, rleval, native_backend; all non-rynorlang repository suites;
`build.py validate`, `build`, `boot-test`. Rynorlang selfhost
failures pre-date BOOT-A1 and are unchanged by it.

RYNOROS BOOT-A1 COMPLETE — HISTORICAL 384 KiB KERNEL CEILING REMOVED
DEMONSTRATED KERNEL CAPACITY — 16,150,528-byte memory footprint (3943 pages) + 6,574,592-byte file
NEXT HARDWARE BLOCKER — APIC/ACPI enumeration (LAPIC + IOAPIC + MSI/MSI-X); PIC/PIT frozen
