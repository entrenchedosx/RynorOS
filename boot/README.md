# Original BIOS boot path

## Purpose and implementation status

Implemented Stage 1: original `sector.asm` BIOS bootstrap and `transition.asm`
CPU-mode transition load and enter original Rynorkernel under QEMU/SeaBIOS.
BIOS fixed-sector loading is chosen to avoid third-party loaders, filesystem
parsers, ISO utilities, and UEFI packaging for this tiny milestone. It trades
portability/expandability for a small, auditable path; it is not a general loader.

## Public interfaces

The generated 1 MiB raw IDE image contains a 512-byte sector with signature
55 aa, then a flat linked payload beginning at LBA 1, then zero padding. There
is no partition table or filesystem. NASM receives `PAYLOAD_SECTORS` from the
actual linked payload length; counts outside 1..64 fail the build.

SeaBIOS enters the boot sector at physical 0x7c00 with the drive in DL. The
sector normalizes CS/data segments, initializes a temporary stack, checks BIOS
extended disk support, and uses INT 13h AH=42h to load into 0800:0000 (physical
0x8000). Disk errors print `Rynor boot: BIOS disk read failed.` to COM1 and halt.
On success it disables interrupts and jumps to `boot_transition` at 0x8000.

The transition enables the fast A20 gate, masks legacy IRQs/NMI, installs a
minimal GDT, enters protected mode, checks CPUID long-mode support, initializes
three static page tables, sets CR4.PAE/EFER.LME/CR0.PG and WP, and far-jumps into
64-bit code. Then it jumps to `rynorkernel_entry`. See `../kernel/README.md`.
The ELF file is a symbol-bearing diagnostic artifact; BIOS loads the separate
flat binary, not ELF program headers. Entry is fixed by the linker, not a host shim.

## Invariants

| Physical range | Reserved use |
| --- | --- |
| 0x1000–0x1fff | PML4 |
| 0x2000–0x2fff | PDPT |
| 0x3000–0x3fff | Page directory, one identity-mapped 2 MiB page |
| 0x7000–0x7bff | Temporary boot stack area, top 0x7c00 |
| 0x7c00–0x7dff | BIOS sector |
| 0x8000–0xffff | Payload and BSS, linker-limited to 32 KiB |
| 0x7c000–0x7ffff | Fixed kernel stack, top 0x80000 |

These are assumptions about the tested QEMU/SeaBIOS RAM layout, not results of
a firmware memory-map parser. Page tables are zeroed before use. No memory is
reclaimed. No allocation API or virtual-memory manager exists. The first 2 MiB
is supervisor writable/executable with no guard pages; this is not isolation.

## Tests

`python tools/build/build.py boot-test` builds from source and verifies both
kernel serial lines within a bounded timeout. `integration-test` also exercises
blank disks and wrong-version payloads; source/compiler/link failure tests live
in the repository suite. Successful QEMU runs exit normally through monitor quit.

## Known limitations

BIOS/extended-LBA hard-disk boot only, no UEFI or real-hardware guarantee, no
disk retry policy, no executable signature/security scheme, no E820 parsing,
no boot arguments or general handoff ABI. Unsupported long-mode CPUs halt
silently (the runner times out); pre-CPUID machines are outside the contract.
BIOS disk-error and UART hardware-failure branches are not fault-injected yet.
There are no exception handlers; unexpected exceptions can triple-fault.
The temporary stack has no guard and BIOS stack usage is firmware-dependent.
Firmware stays an explicitly external bootstrap dependency, never renamed OS code.
