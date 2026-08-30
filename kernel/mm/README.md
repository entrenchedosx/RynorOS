# Physical memory subsystem

Purpose: safely allocate/release real 4096-byte physical frames discovered via
BIOS E820. Implemented Stage 4, not a heap or virtual-memory manager.

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
existing 2 MiB identity map. High returned physical addresses are not yet mapped.
Reserved totals include explicit firmware address-space windows, not just RAM.
See [full design](../../docs/design/physical-memory.md) for layout, API error
codes, normalization, reservations and tests. Stage 5 remains planned.
