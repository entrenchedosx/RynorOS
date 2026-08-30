# Physical memory management

## Purpose and implementation status

Stage 4 implements original physical frame allocation from the actual BIOS E820
map, not a static RAM-size assumption or an array pretending to be physical RAM.
The page size is **4096 bytes**. It allocates physical addresses, not C buffers.
The PMM provides no mappings, heap, userspace isolation, reclamation of firmware
memory, scheduler, DMA subsystem, NUMA or SMP. During PMM initialization, Stage 1's
three static tables identity-map only 0..2 MiB. Stage 5 subsequently replaces
them using the separate [VM subsystem](virtual-memory.md).

## Firmware acquisition and handoff

`boot/transition.asm` calls INT 15h with EAX=0xe820, EDX='SMAP', ECX=24 and
ES:DI pointing into the reserved handoff page while still in real mode. EBX=0
starts enumeration; returned EBX continues it. Each successful call must return
'SMAP' and exactly 20 or 24 bytes. EBX=0 terminates; CF after at least one valid
entry is also an allowed E820 end indication. Failure on the first call, wrong
signature/size or a nonterminating enumeration beyond 64 records leaves the map
incomplete. No fallback fabricates a map or silently truncates it. Interrupts are
enabled only around BIOS calls, then disabled before the mode transition.

Standard reference: [ACPI system address map interfaces, E820 and limitations](https://uefi.org/specs/ACPI/6.5/15_System_Address_Map_Interfaces.html).
The actual tested SeaBIOS returns **20-byte** records despite the 24-byte
request. For those records the bootstrap retains attributes=1; it does not
pretend they supplied extended attributes. The returned length is retained.

The linker defines `__boot_map_start=0x4000`, `__boot_map_end=0x5000`.
Boot code clears the entire page first. Header, offsets in bytes:

| Offset | uint32 field | Required value |
| --- | --- | --- |
| 0 | magic | 0x50414d52, little-endian `RMAP` |
| 4 | version | 1 |
| 8 | count | 1..64 |
| 12 | capacity | 64 |
| 16 | stride | 32 |
| 20 | status | 1 complete; 0 not complete; 2 acquisition failed |
| 24 | storage bytes | 4096 |
| 28 | reserved | 0 |

Each 32-byte slot at offset 32 contains uint64 base and length; uint32 firmware
type, extended attributes, actual returned size, and zero reserved word. C static
assertions bind the header/slot layout to assembly. The maximum used space is
2080 bytes. Boot owns/writes the page until long-mode handoff; the kernel then
retains it read-only by convention for diagnostics. It is never returned to PMM.

The kernel reads only the fixed linker-owned page, not a firmware-controlled
pointer. Before consumption it checks that the page is aligned, exactly 4 KiB,
large enough, inside the existing identity mapping, and checks CR3/PML4/PDPT/PDE
against the actual bootstrap mapping (allowing CPU accessed/dirty bits). Header
validation precedes indexed record access. The normalized E820 map must also
describe each live boot/kernel RAM range as usable. This validates ownership
against firmware without claiming that E820 authenticates a malicious firmware.
Initial safe execution still assumes the documented QEMU PC low-memory layout.

## Validation and normalization

MAXPHYADDR comes from CPUID 0x80000008 (supported widths 32..52); a missing leaf
is a diagnosed failure. Every nonempty enabled entry must fit wholly within
`[0, 2^MAXPHYADDR)`. Length is checked with subtraction before adding to base,
rejecting wraparound and out-of-range endpoints. Zero-length records are ignored.
For 24-byte entries, disabled records (attribute bit 0 clear) are ignored. Other
attribute bits force reservation, never ordinary allocation. Only firmware type 1
is allocatable. Types 3 (ACPI reclaim), 4 (ACPI NVS), 5 (bad) and 7 (persistent)
retain distinct classifications. Unknown types become reserved. ACPI reclaim
memory is identified but **not reclaimed**: no ACPI consumer/lifetime protocol exists.

Usable extents are rounded inward to whole frames; touched partial edge frames
become reserved. Nonusable extents round outward. A bounded boundary sweep sorts
and resolves overlaps conservatively: bad > reserved/unknown > NVS > persistent
> ACPI reclaim > usable. Duplicate and overlapping usable records do not count
frames twice. Adjacent equal classifications merge. Holes are absent from the
normalized table and always unavailable. Maximum 64 raw records produce at most
192 intermediate spans; 392 final-region slots bound normalization/reservation.
Scratch/region arrays are actual kernel metadata, not the source of allocated RAM.

## Reservations and storage

The complete first MiB is retained by an explicit conservative **legacy-PC
policy**, not guessed to be the available RAM size. This retains IVT/BDA/EBDA,
firmware/ROM/device areas and all current low-memory bootstrap infrastructure.
Only usable firmware intervals change to `kind=8`; other classifications and
holes remain unavailable. There is no low-memory reclamation API yet.

| Reservation source | Current range / meaning |
| --- | --- |
| Linker `__page_tables_start/end`, used by startup | 0x1000..0x4000, three live page-table pages |
| Linker `__boot_map_start/end`, used by E820 collector | 0x4000..0x5000, immutable handoff page |
| Linker `__boot_stack_start/end` | 0x7000..0x7c00, retained bootstrap stack |
| BIOS sector origin and linker `__boot_sector_start/end` | 0x7c00..0x7e00 |
| Linker `__kernel_start/end` | Original loaded payload plus actual BSS, including GDT/IDT, IRQ state and PMM static arrays |
| Linker `__kernel_stack_start/end`, entry uses end symbol | 0x7c000..0x80000 |
| PMM-discovered metadata extent | First suitable usable range below the existing identity-map limit, rounded to pages; `kind=9` |

The kernel checks that every linked live range is valid, below 1 MiB and covered
by firmware usable RAM before applying reservations. The linker still restricts
the loaded payload and BSS below 0x70000 without overlapping the stack. The
original Stage 4 load limit was 32 KiB; Stage 5 added bounded sector reads and
page-aligned section boundaries. BSS is still zeroed by entry, not loaded from
disk. Reservations derive from actual linker symbols, never a guessed BSS size.

Bitmap size derives from the count of discovered usable frames after low-memory
reservation: one allocation bit per frame, rounded up to bytes and then pages.
The bitmap is placed in a real usable contiguous interval within the already
mapped 1..2 MiB window, reserved before writing it, and excluded from frame
indexing. No fixed bitmap array backs the allocator. Compact indexing covers
only final usable spans, so a high reserved MMIO region does not demand a bitmap
for every physical address below it. Initial sizing may include a few excess bits
for the subsequently reserved bitmap pages; those bits are never indexed.
If the complete metadata cannot fit this mapped window, initialization fails
explicitly; it does not silently cap RAM or add page tables.

## API, algorithm, ownership and failure semantics

`kernel/include/pmm.h` is an internal interface. Single CPU and **IF=0** are
required for initialization, allocation, release, queries and statistics; no
interrupt handler calls PMM. Calls reject a wrong interrupt context. No implicit
STI/CLI, locks, hidden heap, or host service is used.

- `pmm_initialize(map, physical_bits)` validates/normalizes/reserves and initializes
  the physical bitmap. Reinitialization of a live allocator is rejected.
- `pmm_allocate(&physical)` returns `PMM_OK` and transfers ownership of one
  available frame to the caller. A next-search cursor scans allocation bits;
  the compact index maps back through usable regions to the physical address.
- `pmm_release(physical)` requires an aligned, currently allocated usable frame;
  it clears the bit and lowers the search cursor for deterministic reuse.
- `pmm_query(physical, &state)` distinguishes free, allocated, reserved and
  unavailable. `pmm_statistics` copies counters; `pmm_regions` exposes const
  normalized records. `pmm_check` independently recounts bits and region totals.

Results: OK=0, NOT_READY=1, INVALID=2, UNAVAILABLE=3, NOT_ALLOCATED=4,
OUT_OF_MEMORY=5, WRONG_CONTEXT=6, BAD_MAP=7, BAD_LAYOUT=8, NO_METADATA=9.
Allocation failure leaves the caller's output untouched; zero is not an OOM
sentinel. Physical zero is reserved by policy. An unaligned release is INVALID,
a reserved/hole release is UNAVAILABLE, and a double release is NOT_ALLOCATED.
Map failures retain a diagnostic reason. Nothing claims general fault recovery.

Allocation bits express the ownership boundary (free vs held by a caller), not
process IDs. The caller must release only its own live allocation. There is no
generation tag to detect a stale address after reuse, userspace isolation, frame
zeroing or virtual-address guarantee. In particular, physical addresses above
2 MiB must not be dereferenced without the Stage 5 VM API explicitly mapping them.

Invariants: only normalized usable frames enter indexing; reservations/holes
cannot be allocated; no allocated bit can be allocated again; double free cannot
change accounting; free+allocated=usable; usable+reserved=described. All sizes
are multiples of 4096. Initialization is fail-closed, never partially published.

## Statistics meaning

`firmware_usable_bytes` counts conservatively normalized type-1 frames before OS
reservations. `usable_bytes` is the allocatable pool after all reservations;
`free_bytes` and `allocated_bytes` partition it. `reserved_bytes` counts all
explicitly described nonusable frame extents, including firmware MMIO/ROM windows
and OS reservations. `described_bytes` is the union of described page extents,
excluding holes. These last two values are **physical address-space coverage,
not installed RAM or RAM consumed by the kernel**. QEMU's high reserved ranges
make reserved bytes much larger than its `-m` value; those are real E820 records.

## Tests and known limitations

The guest tests real initialization, all reserved-region boundaries, every low
frame, single/multiple allocation, unique addresses, invalid requests, double
free, exact reuse and accounting. It writes/reads the first and last qwords of
one genuinely allocated frame within the existing mapping. It then exhausts
the **entire real pool**, verifies explicit OOM and unchanged output, and releases
every frame through the public API. No fake quota or alternate allocator is used.
Separate explicitly synthetic map fixtures exercise the production normalizer's
alignment, overlapping/duplicate entries, unknown types, disabled/legacy entries,
zero lengths, bad record sizes, overflow, physical limits, ACPI/high addresses,
capacity and incomplete-map rejection. They never initialize the allocator or
provide the observed firmware diagnostics/totals.

QEMU tests run the same binary with 16, 64, 128 and 256 MiB, independently reconstruct
normalization/reservations from captured raw E820 records, compare all totals,
validate returned addresses and linked object reservations, and require real OOM,
reuse and final restoration. Corrupted copies of real firmware handoffs must
fail before allocator initialization. The timer runs **after** PMM, followed by
another allocator integrity check. All previous exception and timer tests remain.
The original icon package and boot artifact reproducibility guarantees remain.

Limitations: BIOS-only fixed handoff capacity, no physical hardware validation,
no ACPI/boot-memory reclamation, no concurrent callers, no ownership tags or
security boundary, and bitmap placement constrained by the existing mapping.
The exhaustive boot self-test scales with discovered frames; it is intended for
bounded bring-up runs, not a claim of a production boot-time performance budget.
Stage 5 virtual-memory management is implemented separately in `vm.c`; it does
not alter PMM allocation semantics or add user-space isolation.
