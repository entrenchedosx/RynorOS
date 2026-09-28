# DMA buffers (DMA-A1)

## Memory model (frozen)

A DMA buffer is **N physically contiguous whole PMM frames** owned by
the same physical-ownership bitmap as every other frame consumer.
There is no separate DMA pool and no independent DMA bitmap:
`pmm_alloc_contiguous` extends the PMM, so the PMM and the DMA layer
can never hand out the same frame twice.

One buffer carries three addresses that must never be conflated:

| Address | Meaning | Source of truth |
| --- | --- | --- |
| `virt` | kernel virtual address, DMA arena (PML4 slot 385) | `vm_map_range` on the kernel space |
| `phys` | first frame's physical address | `pmm_alloc_contiguous` |
| `bus` | device-visible address programmed into descriptors | `dma_phys_to_bus(phys)` |

DMA-A1 freezes `bus == phys` (no IOMMU, no translation) as the
implementation of `dma_phys_to_bus()` only. Drivers must never derive
the bus address themselves; future IOMMU support replaces that one
function and the `bus` field starts carrying an IOVA.

## Allocation contract

`dma_alloc(size, align, max_bus, &out)`:

- `size` is rounded up to whole pages with checked arithmetic:
  `frames = ceil(size / 4096)`, `alloc_size = frames * 4096`.
  `size == 0` fails with `DMA_INVALID`; overflow fails `DMA_OVERFLOW`.
- `align` must be a nonzero power of two (`DMA_ALIGNMENT` otherwise).
  Sub-page alignments are trivially satisfied because every frame
  starts 4 KiB-aligned; the guarantee is `phys % align == 0`.
- `max_bus` is the inclusive last-byte device address
  (`bus + alloc_size - 1 <= max_bus`). `DMA_ADDR_ANY` (`~0ULL`) means
  unconstrained; `DMA_ADDR_32BIT` (`0xFFFFFFFF`) constrains below 4 GiB.
- On any failure `*out` is left untouched (P1 convention). There are
  no partially initialized descriptors.
- At most `DMA_MAX_BUFFERS` (16) buffers are live at once
  (`DMA_BUSY` beyond that); the registry is also the double-free and
  overlap detector.
- New buffers are zeroed through the kernel mapping before return.
- Context: single CPU, IF=0, foreground (`DMA_CONTEXT` otherwise),
  same as PMM/VM/heap. No IRQ handler may call it.

## Contiguous PMM primitive

`pmm_alloc_contiguous(frames, align_bytes, limit_end, &physical)`
(`limit_end` exclusive, `0` = no limit) performs a deterministic
first-fit scan for N consecutive free frames inside one usable region
span (physical contiguity never spans a reserved span or hole).
The scan is shared with tests as the pure helper `pmm_scan_run()`,
which runs over caller-supplied regions+bitmap so synthetic fixtures
never touch the live allocator. Only a fully validated run is marked;
a failed search leaves the bitmap and statistics unchanged.
The PMM search cursor is deliberately **not** advanced (advancing it
past alignment-skipped free frames would break the
no-free-below-cursor invariant enforced by `pmm_check`).

## Virtual mapping

DMA RAM maps into a dedicated arena, **not** the heap and **not**
slot-509 MMIO space: `DMA_VA_BASE` (PML4 slot 385,
`0xFFFFC08000000000`), bump-allocated, bounded to 1 GiB
(`DMA_VA_MAX_PAGES`). Leaves are supervisor RW/NX **write-back**
system RAM; DMA RAM must never be marked uncacheable just because PCI
MMIO is. Each mapping is existence-checked by the VM layer, so a
conflict surfaces as `DMA_VM_ERROR`, never a silent overlap.
VA allocation is bump-only (no reuse); exhaustion fails closed with
the physical frames rolled back. A VA free-list is future work.

## Cache coherence and ordering

DMA-A1 freezes the **coherent x86-64 WB model**: devices observe RAM
through the coherent caches, so no cache maintenance exists.
`dma_sync_for_device()` / `dma_sync_for_cpu()` are documented no-op
boundaries for that model; non-coherent platforms are unsupported.
Ordering for future descriptor rings: x86-TSO store order plus
`dma_rmb()` / `dma_wmb()` compiler barriers (no fake fence
instructions). See "Memory barriers" below.

## Ownership and lifetime

Buffers are `CPU_OWNED` from `dma_alloc` until the driver programs a
device with the bus address (`DEVICE_VISIBLE`, a driver-side concept),
and must be quiesced by the driver before `dma_free` — the DMA layer
cannot stop hardware. `dma_free()` validates the descriptor against
the live registry (magic + slot + fields), unmaps, releases every
frame, then poisons the descriptor. A second free observes the
poisoned magic and fails `DMA_INVALID` without touching the bitmap.

## Memory barriers

`dma_rmb()` and `dma_wmb()` are compiler barriers. On x86-64 this is
sufficient for the frozen coherent model: TSO forbids store-store and
load-load reordering, the store buffer drains in FIFO order, and the
barrier forbids the compiler from moving accesses across the call.
Non-x86 or non-TSO ports must revisit these two functions; call sites
stay unchanged.

## Security

- Zero-on-alloc is mandatory: freed frames reused by a new buffer
  read back as zero before the caller writes, proven by the
  fill-free-realloc test. No stale process or kernel memory reaches a
  device.
- No zero-on-free claim is made (poisoning is diagnostic only).
- There is **no IOMMU and no DMA isolation**: any future device with
  bus mastering will be able to DMA anywhere in physical RAM. This is
  a documented major limitation, not a solved problem.
- No userspace DMA surface exists (no syscall); drivers are kernel-side.

## Failure behavior

- Fragmentation (free total suffices, no run fits): `DMA_OOM`,
  bitmap and statistics unchanged.
- Physical exhaustion: `DMA_OOM`; existing buffers stay valid.
- VA exhaustion after physical success: physical frames are released,
  output untouched, `DMA_OOM`.
- `vm_map_range` is single-call and internally transactional, so no
  partial PTE state can leak; DMA-A1 additionally never retries a
  failed mapping.
- PCI command registers are untouched: DMA-A1 never enables bus
  mastering anywhere. Memory existing does not authorize DMA.

## Diagnostics

`dma_check()` revalidates every live entry (arena bounds, PMM
allocated state, PTE match, pairwise non-overlap). Boot stays quiet;
the gated self-test prints the `[DMA]` evidence section.

## API

`kernel/include/dma.h`: `dma_initialize`, `dma_alloc`, `dma_free`,
`dma_phys_to_bus`, `dma_sync_for_device`, `dma_sync_for_cpu`,
`dma_rmb`, `dma_wmb`, `dma_check`, `dma_self_test` (gated),
plus the `dma_debug_va_limit()` rollback-test hook.

## Non-goals

xHCI/AHCI/NVMe/NIC/audio/GPU drivers, IOMMU, scatter-gather, bounce
buffers, MSI/MSI-X, IOAPIC, DMA isolation domains, userspace DMA,
sub-page physical allocation, VA reuse.

## Verification (DMA-A1)

- `tests/integration/test_dma.py`: 17/17 (3 QEMU + 14 mutants).
- `tests/repository/test_dma_abi.py`: 9/9 header pins.
- Synthetic scan: 17 fixtures, every row replayed by the independent
  Python first-fit model (`model_scan` in `dma_output.py`); the
  not-found rows print the `0xdead` sentinel, proving the output
  index is left untouched on failure.
- Live witness (default 64 MiB topology): 35 allocations pinned
  exactly, first-fit base `0x121000` (`0x120000` before INT-A1,
  `0x11f000` before BOOT-A1 — each slice spends one more page-table
  page below the DMA pool), VA cursor from
  `0xffffc08000000000`, every `bus == phys`, every `virt != phys`,
  reuse identities `0x121000`/`0x126000`, two-boot determinism.
- Mutants M1-M14 all RED for their designed tag and restored:
  M1/M4/M13 `s-found`, M2/M3 `s-start`, M5/M10 `pmm-rollback`,
  M6/M11/M12 `v-round`, M7 `v-refree`, M8 `v-unmapped`, M9 `r-zero`,
  M14 `rb-leak`. M8 covers the free-path mapping leak: a literal
  N+1-frame free is unkillable-cleanly in this live topology (the
  first freed run abuts free space, so the extra release halts
  instead of failing), and is documented as such in the test.
- M9 note: virgin-frame zero checks false-pass on QEMU-zeroed RAM;
  the 0xA5 fill/free/realloc proof is the true zeroing killer.
- Regressions: PMM 7/7, VM 8/8, heap 5/5, PCI 17/17, P1-A1 8/8,
  P1-A2 16/16, P1-A3 14/14 in isolation; full integration gate 453/457
  with 4 non-green triaged: `test_boot` unarmed-breakpoint
  (`fs-test.c` `-Werror` under `TEST_ARMED=0`), `test_pipe`
  user-path count, and `test_rleval` f8 all fail identically at
  pristine HEAD (files untouched by this slice; f8 reverified in a
  HEAD worktree), and the `test_pmm` 4 GiB subtest is an
  environmental flake (green in isolation with final code).

## Resource cost (measured)

- Static: 768 B BSS registry (16 entries) + ~24 B cursor/limit/flag.
- Code: `.text` +10811, `.rodata` +2312, `.data` +8, `.bss` +803 vs
  the PCI-A1 test image (production `dma.c`/`pmm.c` plus `-Os`
  `dma-test.c`); `__bss_end` was `0x6E19C`, leaving 7780 bytes of the
  old 0x70000 BIOS window (removed by BOOT-A1; kept as the measurement
  that motivated the high-load work).
- Runtime per mapping: one shared PDPT+PD+PT chain (3 PMM frames,
  first mapping only), one PTE per page, one registry entry; DMA-A1
  tests consume 64 VA pages of the 1 GiB arena. No fixed DMA pool.
