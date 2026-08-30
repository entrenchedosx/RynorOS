# Memory subsystems

Purpose: safely allocate/release real 4096-byte physical frames discovered via
BIOS E820 (Stage 4), and map those frames using owned page tables (Stage 5).
Neither subsystem is a heap or a user-mode isolation facility.

Public kernel interfaces are in `../include/pmm.h`: initialize, allocate, release,
query frame state, statistics, const normalized-region inspection and integrity
checking. All calls require one CPU and IF=0. `map.c` validates/normalizes the
versioned handoff, `pmm.c` owns the bitmap/regions/counters, and `selftest.c`
contains boot diagnostics and deterministic real-pool tests.

Invariants: only full usable firmware frames are allocatable; all low-memory
boot infrastructure and the dynamically placed physical bitmap are reserved;
allocated frames are unique; invalid/double releases cannot alter accounting;
free+allocated=usable and usable+reserved=described. The caller owns each returned
physical frame until release. No mapping, zeroing, generation tag or isolation
is implied by allocation.

Tests: real firmware maps at multiple QEMU RAM sizes, reserved-range checks
against linker symbols, eight unique allocations, physical writes, release/reuse,
full-pool exhaustion/release, adversarial normalizer cases, malformed handoff
rejection, and timer interrupts after PMM with a final integrity check.

Known limitations: BIOS/one CPU only; 64 raw map records; conservative first-MiB
retention; no firmware reclamation; bitmap must fit discovered RAM inside the
initial 2 MiB identity map. High returned addresses are not implicitly mapped;
Stage 5 provides explicit mappings and a serialized temporary frame window.
Reserved totals include explicit firmware address-space windows, not just RAM.
See [full design](../../docs/design/physical-memory.md) for layout, API error
codes, normalization, reservations and tests.

Stage 5 interfaces are in `vm.h`/`paging.h`: create/destroy inactive hierarchies,
map/unmap page/range, query/translate, protect, frame access and integrity checks.
`vm.c` owns page-table state and CR3/TLB operations. `vm-test.c` adds diagnostics,
exact armed page-fault recovery and real hardware self-tests. Tables come only
from PMM and are zeroed; data leaves borrow caller-owned frames. Calls require
IF=0, and temporary pointers expire on the next VM operation. The active kernel
space cannot be destroyed. No user processes, COW, swap, demand paging or large
pages are implemented. See [VM design](../../docs/design/virtual-memory.md) for
API errors, layout, permissions, invariants, rollback and verification.
