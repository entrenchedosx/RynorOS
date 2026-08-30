# Bounded kernel heap

## Purpose and status

Implemented: a single-CPU, fixed-capacity kernel allocator backed by real PMM
frames and VM mappings. The incoming implementation was not reliable despite
passing its tests; the codebase audit rewrote its block management.
This is not a growable heap, libc, user allocator or production-readiness claim.
See [audit findings and evidence](../reports/codebase-audit.md).

## Arena, mappings and lifetime

The arena is 65536 bytes (16 pages) at `0xffffc00000000000`, PML4 slot 384.
This is a dedicated high canonical kernel range, separate from the transient
frame window in slot 510. Paging remains four-level/48-bit, **not 72-bit**.
No huge pages are used.

Initialization requires the active VM subsystem and IF=0. It preflights all
sixteen virtual pages for conflicts, allocates frames through PMM, then maps
each supervisor RW/NX. Three additional VM-owned table frames are needed by
this layout. A mapping conflict preserves the existing mapping and data.
If allocation/mapping fails, rollback unmaps/invalidate first, then releases
each backing frame. All rollback results are checked; an internal rollback
failure halts rather than reporting successful cleanup. Initialization can be
retried after ordinary OOM. Readiness is published only after complete success.

All pages are mapped at initialization, **not on demand**. The heap retains its
arena for kernel lifetime. There is no heap teardown or grow/shrink API. After
self-tests, PMM holds 26 frames: seven original VM tables, three heap-branch
tables, and sixteen arena frames, for 106496 bytes. These counts describe this
bootstrap layout, not a second physical allocator.

## Block representation and invariants

Blocks partition the entire arena without holes or overlap. Each has a
16-byte header and 16-byte footer, both containing 64-bit size and tag.
Size includes metadata/padding, is a multiple of 16, and is at least 48 bytes.
Used tag is `0x524e484541504f42`; free tag is that value OR 1. A compile-time
assertion rejects indistinguishable encodings.

There is **no auxiliary free list**. Address-order scans find blocks using
validated sizes. This removes payload-pointer traversal, cycles, orphan nodes
and duplicated list accounting from a small bounded allocator.

Before allocation, release, or statistics, `heap_check` verifies the complete
partition: enough bytes for a block; size/minimum/alignment; size no larger
than the remaining arena; known tag; matching footer; no adjacent free blocks;
and exact used/free-block accounting. It checks size with subtraction before
calculating or reading the footer. A scan visits at most floor(65536/48) blocks.
Corruption is rejected before any allocator mutation.

## Allocation and release

`heap_alloc(size, alignment, &out)` accepts nonzero sizes and power-of-two
alignment 8..4096. Checked rounding reserves header/footer plus a 16-aligned
payload. First-fit uses address order, not insertion order. An aligned block
needs no leading gap when its payload is already aligned; any nonzero gap
must be large enough for a free block. A remainder smaller than 48 bytes is
**absorbed into the allocation**, never dropped. Larger tails become free blocks.
All returned pointers are at least 16-aligned. Payload is not promised zeroed.
Failure leaves `out` unchanged.

`heap_free(pointer)` accepts only an exact payload start found by walking from
the arena base. A plausible header forged inside caller data cannot authorize
a free. Whole-arena validation precedes changing any neighbour or accounting.
Valid free coalesces both adjacent free blocks; the prior no-adjacent-free
invariant means one check per side suffices. Double free at a surviving free
block start returns CORRUPT; pointers inside coalesced blocks return INVALID.

Statistics count whole block footprints, including metadata and alignment
padding, not requested payload lengths. `free_bytes + used_bytes = arena_bytes`;
free bytes are not a guarantee that an equally sized payload can be allocated.

## API errors and context

`heap.h`: initialize, alloc, free, statistics and check.
Results are OK, NOT_READY, INVALID, ALIGNMENT, OVERFLOW, OUT_OF_MEMORY,
CORRUPT, BUSY, CONTEXT, VM_ERROR and MAPPING_CONFLICT. Zero size is OVERFLOW
under the retained API. A full arena is OUT_OF_MEMORY, not a fake null success.
The separate VM_ERROR avoids misreporting every VM failure as physical OOM.

IF=0 is checked. Single CPU, no IRQ/exception/NMI allocator calls, and valid
output pointers are caller obligations; IF=0 alone does not detect interrupt
context or make this SMP-safe. There are no locks or hidden interrupt toggles.
Pointers carry no generation or owner token: stale use after address reuse
cannot be distinguished from the current owner's pointer. Corruption rejection
is bounded diagnostics, not protection against arbitrary malicious kernel writes.

## Tests

`heap-test.c` is separate from allocator code; boot explicitly invokes it.
It tests real PMM exhaustion with 0/1/4/18 frames available during initialization,
partial mapping rollback and retry, an occupied arena page, all sixteen backing
frames/permissions/uniqueness, near-full allocations with 0/16/32-byte tails in
both plain and 4096-aligned paths, writes across the arena, forged interior
headers, zero/short/misaligned/out-of-range/overflow sizes, corrupt footers,
unchanged output on error, exact reuse, eight mixed allocation/free cycles over
32 live patterned allocations, all supported alignments, and full-arena OOM.

The normal 256-byte-payload stress fits 227 blocks (288-byte footprints).
Host validation checks that count and cross-checks final PMM totals against the
earlier VM transcript. Mutated builds prove lost-tail/interior-pointer and
alignment bugs fail; indistinguishable tags fail compilation. Same-image runs
cover multiple RAM sizes and real physical RAM above 4 GiB.

## Limitations

Fixed 64 KiB capacity; linear validation/search per operation; no realloc,
per-object ownership, security isolation, concurrent callers or reclamation.
Invalid stacks, unmapped arena pages, and arbitrary kernel corruption can still
fault. This is intentionally a small bounded facility, not a general allocator
for processes, DMA, drivers or production workloads.
