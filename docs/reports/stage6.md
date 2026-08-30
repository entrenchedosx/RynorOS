# Bounded kernel heap — audited implementation

The prior agent left this work uncommitted on top of
`be1da0a39941bf726c6d961f3d1502c38d294127`. The incoming report's completion
claims were not sufficient evidence. The takeover audit reproduced heap
corruption despite 56 repository and 27 integration tests passing.

The old implementation lost unsplittable tails, accepted forged interior frees,
read out-of-range footer addresses, accepted a free-list cycle, and released
backing frames before removing mappings during initialization rollback.
Its report also mixed contradictory payload sizes, described nonexistent
72-bit paging/nesting checks, and implied a teardown that did not exist.

The allocator has been rewritten as a bounded address-ordered boundary-tag
partition with no separate free list. Its 64 KiB arena is real PMM-backed RW/NX
memory. Validation precedes mutation; short tails are absorbed; free accepts
only exact block starts; initialization failure restores mappings and PMM
accounting. Tests are separate in `kernel/mm/heap-test.c`.

[Heap design](../design/heap.md) describes the actual API, invariants and limits.
[Codebase audit](codebase-audit.md) is the authoritative current verification
record, including findings, tests, measured statistics and artifact hashes.
Earlier Stage 4/5 reports remain historical snapshots.

No scheduler, input driver, process, user mode, graphics, filesystem or language
compiler was added by this repair. The arena cannot grow/shrink and is retained
for kernel lifetime; there is no teardown. The next roadmap stage was not begun.
