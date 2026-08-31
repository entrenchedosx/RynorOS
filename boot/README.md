# Original BIOS boot path

## Purpose and implementation status

Implemented Stage 1: original `sector.asm` BIOS bootstrap and `transition.asm`
CPU-mode transition load and enter original Rynorkernel under QEMU/SeaBIOS.
Stage 4 extends the real-mode transition with standard BIOS E820 collection.
Stage 5 extends disk loading with bounded single-sector requests and replaces
the temporary boot mappings later in the kernel.
BIOS fixed-sector loading is chosen to avoid third-party loaders, filesystem
parsers, ISO utilities, and UEFI packaging for this tiny milestone. It trades
portability/expandability for a small, auditable path; it is not a general loader.

## Public interfaces

The generated 1 MiB raw IDE image contains a 512-byte sector with signature
55 aa, then a flat linked payload beginning at LBA 1, then zero padding. There
is no partition table or filesystem. NASM receives `PAYLOAD_SECTORS` from the
actual linked payload length; counts outside 1..832 fail the build.

SeaBIOS enters the boot sector at physical 0x7c00 with the drive in DL. The
sector normalizes CS/data segments, initializes a temporary stack, checks BIOS
extended disk support, and uses INT 13h AH=42h to load into 0800:0000 (physical
0x8000), one sector per call. Each successful read increments LBA and destination
segment by 0x20, with offset zero, so no request crosses a 64 KiB boundary.
Disk errors print `Rynor boot: BIOS disk read failed.` to COM1 and halt.
On success it disables interrupts and jumps to `boot_transition` at 0x8000.

The transition first enumerates E820 into the linker-owned 0x4000..0x5000 page,
with 64 bounded slots, a versioned header and completion status. It preserves
actual 20/24-byte lengths and rejects incomplete/oversized enumeration. Kernel
validation/normalization is specified in `../docs/design/physical-memory.md`.
It then enables the fast A20 gate, masks legacy IRQs/NMI, installs a
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
| 0x4000–0x4fff | Versioned E820 handoff, retained after boot |
| 0x5000–0x5fff | Version-2 PCI/BGA display handoff, retained read-only/NX |
| 0x7000–0x7bff | Temporary boot stack area, top 0x7c00 |
| 0x7c00–0x7dff | BIOS sector |
| 0x8000–`__payload_end` | Loaded payload, linker-bounded below 0x70000 |
| `__bss_start`–`__bss_end` | Kernel-zeroed BSS, linker-bounded below 0x70000 |
| 0x7c000–0x7ffff | Fixed kernel stack, top 0x80000 |

These initial placements are the documented QEMU PC bootstrap contract, not
inferred RAM capacity. PMM validates their actual linker ranges against E820
before publishing an allocator and conservatively reserves the first MiB.
The physical bitmap is placed in discovered usable mapped RAM, never at a
guessed free address. Page tables are zeroed before use. No boot/firmware memory is
reclaimed. The temporary first 2 MiB is supervisor writable/executable until
Stage 5 replaces CR3 with seven PMM-backed table pages, removes unused boot
mappings and applies real RX/R/NX/RW permissions. See `../docs/design/virtual-memory.md`.

## Tests

`python tools/build/build.py boot-test` builds from source and verifies both
kernel serial lines within a bounded timeout. `integration-test` also exercises
blank disks and wrong-version payloads; source/compiler/link failure tests live
in the repository suite. Successful QEMU runs exit normally through monitor quit.
Stage 2 preserves this boot path and appends GDT/IDT/exception diagnostics after
the two legacy boot lines. See `../docs/design/cpu.md` for kernel-owned tables.
Stages 4/5 test physical and virtual memory before the existing Stage 3 PIT IRQ0
test, then checks allocator integrity again. The sector uses the Stage 5 bounded
read loop; the transition owns E820 acquisition. Stage 9 also validates the
QEMU standard-VGA PCI BAR/aperture and BGA mode before publishing display
metadata; see `../docs/design/framebuffer.md`. The icon resource package is a separate
host-side artifact, never loaded by BIOS or inserted into the raw image.

## Known limitations

BIOS/extended-LBA hard-disk boot only, no UEFI or real-hardware guarantee, no
disk retry policy, no executable signature/security scheme,
no boot arguments or general handoff ABI. Unsupported long-mode CPUs halt
silently; failed E820 enumeration is instead rejected by the kernel with a
diagnostic. The fixed handoff rejects more than 64 records without truncation.
The bounded host runner detects early halts; pre-CPUID machines are outside the contract.
BIOS disk-error and UART hardware-failure branches are not fault-injected yet.
The boot transition has no exception handlers. The kernel installs its exception
IDT later; faults before that point or with an unusable stack can triple-fault.
The temporary stack has no guard and BIOS stack usage is firmware-dependent.
Firmware stays an explicitly external bootstrap dependency, never renamed OS code.
