# BIOS boot architecture (BOOT-A1)

Historical ceiling removed: the kernel no longer shares the
`0x8000-0x70000` BIOS window. A fixed 4 KiB boot part loads the kernel
file from disk to its 8 MiB link base, verified end to end. File cap
8 MiB, memory cap 16 MiB (linker + builder + loader agree; the host
ABI suite pins all three against each other).

## Why the 0x70000 ceiling existed

Three independent ceilings coincided; the tightest bound:

1. `boot/sector.asm` loaded the whole payload to `0x8000` with at most
   832 sectors: `0x8000 + 832*512 = 0x70000`.
2. `linker.ld` asserted `__payload_end <= 0x70000` and
   `__bss_end <= 0x70000` (margin below the `0x7c000` kernel stack).
3. `transition.asm` mapped a single 2 MiB identity page and the kernel
   lived inside it below `0x70000`.

Low RAM above the old window is not free for growth anyway
(`0x80000-0x100000` is EBDA/video/ROM), so any multi-MiB kernel must
leave low memory. The measured headroom at DMA-A1 was 7,780 bytes.

## Designs compared (and rejected)

- **Direct high BIOS load**: impossible. The INT 13h AH=42h packet
  carries a segment:offset buffer (16-bit segment), addressing at most
  ~1 MiB + 64 KiB. It cannot target an 8 MiB load base.
- **Low link grown to 0x9F000**: caps the kernel at ~576 KiB absolute.
  Insufficient.
- **2 MiB base**: user code is linked at 4 MiB, capping kernel growth
  just under 2 MiB. Insufficient multi-MiB headroom.
- **1 MiB base**: 3 MiB capacity, but tangles the PMM metadata window
  and merely defers page-table growth. Rejected as fragile.
- **Protected-mode disk driver**: rejected; BOOT-A1 must not invent a
  disk driver for elegance.
- **Higher-half kernel**: correct long-term direction, far too large
  for this slice.

**Chosen: low bounce + high copy.** The BIOS sector loads a fixed
4 KiB boot part; the boot code chunk-reads the kernel file into low
staging and copies it to `0x800000` with 32-bit moves (unreal mode),
then enters long mode exactly as before and jumps to the high entry.
The kernel stays identity-mapped (`va == pa`), so no kernel source
changes for addressing; only placement moves.

## Layout

```text
disk image             size
-----------------------------------------
LBA 0   boot sector    512 B
LBA 1-8 boot part      4 KiB (transition, padded, asserted)
LBA 9   boot header    512 B (build-generated, checksummed)
LBA 10+ kernel file    N sectors (text/rodata/data; BSS is NOLOAD)
```

The image keeps its 1 MiB minimum (small kernels pad exactly as
before) and grows past it for large kernels. The `ld.lld`
`--oformat=binary` output follows VMA, not LMA, so the raw link
contains an 8 MiB zero gap; `image.py` drops it deterministically
(boot `[0,0x1000)` + kernel `[0x7F8000,EOF)`, gap verified all-zero)
instead of shipping it. The shipped `rynorkernel.bin` artifact is the
contiguous on-disk boot payload (boot part + header + sector-padded
kernel file), not the raw linker binary.

## Physical memory map at boot

```text
0x00000000-0x00000FFF  IVT/BDA (firmware-owned)
0x00001000-0x00003FFF  boot page tables (PML4/PDPT/PD)
0x00004000-0x00004FFF  E820 handoff (retained)
0x00005000-0x00005FFF  display handoff (retained)
0x00006000-0x00006FFF  boot header scratch (transient)
0x00007000-0x00007BFF  boot stack (transient)
0x00007C00-0x00007DFF  boot sector (retained, never reclaimed)
0x00008000-0x00008FFF  boot part (transition; retained, never reclaimed)
0x00010000-0x0006FFFF  disk staging (transient; ordinary RAM after boot)
0x0007C000-0x0007FFFF  kernel stack (retained)
0x00800000-...        kernel image (text/rodata/data/BSS; PMM-owned)
```

Staging is never reserved: it is ordinary usable RAM once the copy
finishes. The kernel range is PMM-reserved from linker symbols.

## Kernel placement

- Physical = virtual = `0x800000` (`KERNEL_PHYS_BASE`), identity.
  `USER_STACK_TOP` is exactly `0x800000`: userspace `[0, 8 MiB)` and
  the kernel `[8 MiB, ...)` form a clean low-half partition.
- Entry == load base, frozen by format and linker-asserted.
- Early boot tables map `ceil(mem_end / 2 MiB)` 2 MiB identity pages
  (runtime count from the header, capped to 12 entries: 24 MiB, the
  base plus the 16 MiB memory cap).
- `vm_initialize` maps the exact kernel range with 4 KiB pages
  (RX text, R rodata, RW data/BSS, NX), allocating one PT per touched
  2 MiB region; table count is `7 + kernel_pts`, verified against ELF
  by the host. The high image is immutable kernel footprint: map,
  unmap, and protect refuse it exactly like the low bootstrap range.
- PMM keeps its low `owned[]` rule (`< 1 MiB`) for the boot part and
  handoff/stack state, and validates the high kernel extent
  separately: frozen base, file/mem caps from linker symbols, and
  E820-usable coverage; then reserves `[start, page_rounded_end)`.
  User address spaces clone one replica PT per touched kernel region,
  so per-context tables are `6 + kernel_pts`, also ELF-checked.

## Size model

- `file_size`: kernel text/rodata/data bytes on disk (+ header).
- `mem_size`: `file_size` + BSS, page-rounded; maps and reserves this.
- BSS stays NOLOAD: never stored, zeroed by `entry.asm` (unchanged).
- Build caps (linker + `image.py`): file <= 8 MiB, mem <= 16 MiB;
  boot-time E820 check requires one usable entry spanning the whole
  kernel range (exactness stays the kernel's job, so corrupted-map
  tests still reach the kernel). Demonstrated bound: see Verification.

## Boot header (LBA 9, 512 B, build-generated)

```text
+0  u32 magic      0x4E484252 ("RBHN")
+4  u16 version    1
+6  u16 flags      0
+8  u32 file_sectors  (1..16384)
+12 u32 mem_pages     (>= ceil(file_sectors/8), <= 4096)
+16 u32 fnv1a         (over the sector-padded kernel file bytes)
+20 u32 load_base     0x800000
+24 u32 entry         0x800000
+28 u32 reserved      0
+32 480 bytes zero
```

The loader validates every field with checked arithmetic before any
copy; any failure prints a COM1 diagnostic and halts without entering
the kernel. The checksum covers exactly the bytes the loader copies
(padded file, not BSS).

## Loader notes (measured)

- Unreal mode returns through a 16-bit protected descriptor: a far
  jump executed with CS.D=1 decodes as ptr16:32 and would leave
  CS.D=1 in real mode (later CALL/RET pop 32 bits and go wild).
- Unreal-mode data blocks assemble in bits 16 with 32-bit operands:
  the CPU runs CS.D=0, so 32-bit defaults would misdecode; string
  ops, JECXZ, and LOOP are avoided (implicit 16-bit size).
- BIOS calls run with canonical DS=0: SeaBIOS forms flat pointers as
  seg<<4|off, so a flat DS misaddresses the EDD packet. Callers
  re-arm unreal state after every BIOS call; the failure path keeps
  a working DS so the diagnostic always prints.

## Failure behavior

Bad magic/version/size/checksum/range, disk errors, A20 failure, and
range/E820 mismatches all halt in the boot part with a distinct
`Rynor boot: ...` line. Control never reaches a partial kernel.

## Verification (BOOT-A1)

Oversized-kernel matrix (`tests/integration/test_boot_matrix.py`,
4/4): each case builds a bloated kernel and boots it end to end in
QEMU, validated against its own ELF layout (PMM reservation, VM
tables, userspace tables):

```text
case  kernel file          kernel mem            VM/user  proves
B1    2601 sectors (1.27M)  360 pages (1.41M)      8/7     multi-chunk staging copy, one region
B2     561 sectors          744 pages (2.90M)      9/8     crosses the 10 MiB line, two regions
B3     561 sectors         2152 pages (8.41M)     12/11    five regions
B4    12841 sectors (6.27M) 3943 pages (15.40M)   15/14    all eight regions, near capacity
```

Loader mutants (`tests/integration/test_boot_mutants.py`, M1-M12,
12/12): image mutants (bad magic/version/flags, file/mem bounds,
load base, entry, corrupt byte, bogus header LBA, tiny RAM) and
source mutants (inverted checksum branch, broken A20 expectation)
each halt in the loader with their exact `Rynor boot: ...`
diagnostic and never reach the kernel.

Regression inventory (all green): `test_boot`, `test_display`,
`test_keyboard`, `test_pmm`, `test_vm`, `test_audit`, `test_heap`,
`test_scheduler`, `test_runtime`, `test_dma`, `test_pci`,
`test_shell`, `test_userspace`, `test_input`, `test_proc`,
`test_pipe`, `test_load`, `test_rt`, `test_filesystem`,
`test_storage`, `test_cplshell`, `test_p1a`, `test_p1a2`,
`test_p1a3`, `test_rlen`, `test_rleval`, `test_native_backend`,
plus the repository ABI/output suites (rynorlang selfhost failures
pre-date BOOT-A1 and are unchanged by it).

## New capacity

Demonstrated maximum (B4, exact): **16,150,528-byte kernel memory
footprint** (3943 pages, 96.3% of the 16 MiB cap) with a
**6,574,592-byte kernel file** (12841 sectors, 78.4% of the 8 MiB
cap), booted end to end from high memory. The old ceiling was
384 KiB total with 7,780 bytes of headroom; the memory cap alone is
42x larger.

Remaining bounds (all enforced by linker + builder + loader, and
pinned against each other by `tests/repository/test_boot_layout.py`):

- Kernel file <= 8 MiB (16384 sectors), mem <= 16 MiB (4096 pages).
- Early boot tables map at most 12 2 MiB entries (24 MiB cap).
- One E820 usable entry must span the whole kernel range; the
  loader rejects short RAM (`Rynor boot: kernel exceeds usable
  RAM.`, pinned by the 8 MiB audit case). 16 MiB is the smallest
  proven boot.

## UEFI boundary

The kernel receives no BIOS-specific structures beyond the two
retained handoff pages (E820 map, display info) and the generic
rule "image loaded at its link addresses, BSS zeroed". A future UEFI
loader must provide the same two pages (or a superset handoff) and
jump to the link entry; no other coupling exists.
