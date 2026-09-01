# Virtual memory subsystem

## Purpose and status

Implemented Stage 5: original x86-64 four-level page tables, 4096-byte pages,
PMM-backed table allocation, a live kernel address space, mapping/unmapping,
translation, permissions, explicit invalidation, and real page-fault tests.
(A bounded kernel heap is implemented separately in Stage 6; see [heap.md](heap.md).)
User mode, process address spaces, address-space switching,
copy-on-write, demand paging, swap, shared-memory policy and file-backed mappings
are **not implemented**. Creating an empty paging hierarchy is not a process.

The architectural reference is [AMD64 Volume 2, Chapter 5](https://docs.amd.com/v/u/en-US/24593_3.44_APM_Vol2).
This is an interface reference, not imported OS implementation code.

## Paging mode and representation

Four levels are sufficient for the current single-CPU kernel. CR4.PAE must be
set; LA57, PCIDE and PGE must be clear. CR0.PG and CR0.WP are required. NX support
is checked through CPUID and EFER.NXE is enabled before loading NX-bearing tables.
Unsupported CPUs fail explicitly; permissions are never silently weakened.

`paging.h` defines `page_entry`, `page_table`, flags and geometry. Each table is
512 eight-byte entries occupying exactly one 4096-byte frame. Virtual indices
are bits 47..39 (PML4), 38..30 (PDPT), 29..21 (PD), and 20..12 (PT); bits 11..0
are the page offset. Physical addresses occupy bits 51..12 of entries, with the
actual supported width checked against CPUID MAXPHYADDR (32..52 supported).

Intermediate entries are exactly present/write/user plus hardware accessed;
they do not restrict leaf permissions. Leaf present means readable on x86; there
is no read-disable or execute-only API. Writable, user and NX control real
hardware access. Accessed and dirty are reported by query and preserved across
permission changes. PWT/PCD flag constants and the generic `pte_has` helper
identify those hardware bits, but **cache-policy mappings are not exposed for
ordinary RAM**: ordinary allocated RAM uses write-back caching. The Stage 9
device-only API uses a validated PAT-index-3 UC encoding; there is no generic
PAT-policy API or global-page support. Software changes to table memory outside this implementation violate
the API contract; detected unsupported or malformed encodings are rejected.

There are no new large pages. The Stage 1 2 MiB bootstrap leaf is replaced,
not inherited. A huge-page bit in a walked hierarchy is rejected; bit 7 in a
4 KiB leaf (PAT) is also rejected. Five-level paging is not enabled.

## Virtual layout and boot transition

| Virtual interval | Role after VM activation |
| --- | --- |
| Linked 0x8000..`__text_end` | Kernel/retained transition code, supervisor RX |
| `__text_end`..`__rodata_end` | Page-aligned rodata, supervisor R/NX |
| `__data_start`..rounded `__kernel_end` | Data/BSS, supervisor RW/NX, including GDT/IDT/PMM state |
| 0x7c000..0x80000 | Existing kernel stack, supervisor RW/NX |
| 0x4000..0x5000 | E820 handoff, supervisor R/NX |
| PMM-discovered bitmap extent | Identity-mapped supervisor RW/NX |
| 0xffffff0000000000 | Temporary one-frame access window, supervisor RW/NX |
| 0xffffff0000001000 | Window PT's own page, supervisor RW/NX |
| PML4 slot 509 | Exclusive Stage 9 foreign-device MMIO; supervisor RW/NX UC, ordinary APIs reject |
| PML4 slot 511 | Reserved/unmapped future kernel layout |
| 0x40000000 and nearby pages; 0xffff800000000000 | Temporary self-test mappings, removed on completion |

No whole-RAM direct map exists. Null, unused low-memory holes, the old bootstrap
tables at 0x1000..0x4000 and the old boot-sector/boot-stack addresses are **not
mapped** after activation. Unmapped pages adjacent to the kernel stack are not
a complete overflow-recovery solution: TSS/IST and emergency stacks are absent.
All first-MiB physical reservations remain retained by the PMM; unmapping old
boot structures does not reclaim them.

Before switching, seven frames are allocated through the real PMM and zeroed:
PML4, low PDPT/PD/PT and window PDPT/PD/PT. Each must fit the existing 2 MiB
bootstrap mapping for initial construction. A shortage or unmapped candidate
causes rollback; no hidden low-memory allocator is used. The root connects the
two branches. Only actual linked live objects, stack, handoff and bitmap are
mapped. Page-aligned linker boundaries separate code, rodata and writable data.
NX is enabled, CR3 is replaced with the new PMM root, CR3 is read back, and the
hierarchy is checked. Code and stack retain their linked addresses, so execution,
serial, exception return and IRQ return continue without relocation.

The old page-table hierarchy is **completely discarded**, not shared or reused.
Subsequent access to physical frames uses the new window, including frames above
2 MiB. Its PT has a permanent self-alias at window+4096, allowing entry zero to
be changed to any allocated physical frame followed by `INVLPG(window)`.
Only one temporary pointer may be live; every subsequent VM operation can change
its target. No function may retain that pointer across another VM call or IRQ
enable. All VM operations require one CPU and IF=0, and no IRQ handler calls VM.

The additional code and page-aligned sections exceeded the old 32 KiB load bound.
The existing BIOS loader now reads one sector at a time, bounded by the linked
payload size within 0x8000..0x70000 (832 sectors maximum). Each request uses a
zero offset and advances its segment by 0x20, avoiding 64 KiB boundary crossings.
This is the same raw image format and original loader, not a new boot protocol.

## Public interfaces and ownership

`vm.h` defines the API. `vm_space` is caller-owned, zero-initialized, immovable
storage. Its identity pointer rejects copied/live handles; callers must not edit
private fields or page-table bytes. Each space owns its root and all intermediate
table frames, tracked by `table_pages`. There is no static table pool or malloc.

- `vm_initialize`: creates and activates the kernel hierarchy once. No generic
  switch-to-empty-space function is exposed; an empty hierarchy cannot run C.
- `vm_create`: allocates/zeros a root for an inactive hierarchy.
- `vm_destroy`: validates an inactive hierarchy, then recursively releases its
  tables. Destroying the active kernel space is refused. Data leaves are borrowed
  and are **not freed** by destruction.
- `vm_map` / `vm_map_range`: map aligned, currently PMM-allocated data frames,
  without replacing mappings. All existing leaves and physical frames are
  preflighted. A failure rolls back every new mapping/table in the transaction.
- `vm_unmap` / `vm_unmap_range`: require all requested pages present before
  mutation, clear leaves, invalidate active translations and prune empty tables.
- `vm_query`: returns physical address including offset, permissions and A/D.
- `vm_translate`: returns the physical address; failure leaves output unchanged.
- `vm_protect`: changes an existing leaf's permissions and invalidates it.
- `vm_frame_access`: temporary access to a currently allocated physical frame,
  under the window lifetime restrictions above; not a permanent direct map.
- `vm_check`: verifies table encodings, PMM allocation state and table counts.

Data-frame lifetime is the caller's responsibility: keep frames allocated until
all mappings are removed, release only owned allocations, and do not map or
modify VM-owned table frames. PMM allocation-state checks are not ownership
tokens or security boundaries. Handles and pointers are trusted kernel objects,
not user input. Corruption detection is best effort, not arbitrary graph repair.

Supported leaf permissions are write, user, execute; all present pages read.
W+X is rejected. Kernel-space user mappings are rejected; inactive hierarchies
can encode U/S for future use, but no ring-3 execution or isolation is claimed.
Public mutations cannot touch the active low 2 MiB infrastructure or PML4 slots
509..511. No automatic replacement, copy, zeroing of data frames, reference count,
or sharing policy is provided.

## Validation and failure semantics

Canonical ranges are 0..0x00007fffffffffff and 0xffff800000000000..0xffffffffffffffff.
Bits 63..48 must sign-extend bit 47. Mapping addresses must be 4096-aligned;
queries accept offsets. A range must contain at least one page, have a
representable exclusive end, and stay inside one canonical half. The final
high page ending at 2^64 is deliberately rejected because its exclusive end
cannot be represented. Page counts, range ends and physical bounds are checked
with subtraction/division before multiplication/addition; no wrap is permitted.

Results: OK=0, NOT_READY=1, INVALID=2, ALIGNMENT=3, NONCANONICAL=4, OVERFLOW=5,
PHYSICAL=6, OOM=7, EXISTS=8, NOT_MAPPED=9, PERMISSION=10, UNSUPPORTED=11,
BUSY=12, CONTEXT=13, CORRUPT=14. PHYSICAL covers reserved, unallocated,
out-of-width or overflowing physical spans. Empty/destroyed/copied handles are
invalid. Initialization and range mapping failures never publish partial success.

Invariants: all table frames are allocated in PMM and zeroed before publication;
each normal tree owns each of its table nodes once; data leaves are borrowed;
new mappings cannot overwrite old ones; no page-table frees precede removal of
their last active translation; PMM free+allocated remains unchanged by VM except
for explicitly owned table/data allocations; only the seven persistent kernel
table frames remain allocated after the test.

## TLB management

CR3 reload performs the one-time boot-to-kernel replacement with PCID/global
pages disabled. Mapping, unmapping and permission changes use `INVLPG(va)` only
for the active kernel space. Inactive spaces cannot have cached translations
because this stage never activates them. Table-window changes invalidate the
window, not every kernel address. Unmap detaches empty tables before invalidation
and frees them only after invalidation. The audit made this order explicit for
both pruning and failed insertion: detach all affected parents, invalidate, then
release. Correctness must not depend on later window remaps incidentally clearing
paging-structure caches. Range rollback errors halt rather than being ignored.
No per-operation CR3 reload, SMP shootdown or ASID/PCID facility exists.

## Fault handling

The existing common exception entry captures CR2 before C work and preserves
the full Stage 2 frame. After VM activation, #PF prints CR2, hardware error code,
RIP, present/write/user/reserved/fetch bits and CS privilege level. Unexpected
faults print a fatal marker and continue into the existing full CPU diagnostic
and halt path. They never automatically allocate memory or retry arbitrary code.

Only an explicit one-shot test arm may resume. CR2, exact error, faulting RIP,
saved RSP, vector, kernel selectors and IF=0 must all match. Recovery changes only
saved RIP to a specific assembly return label; IRETQ restores the remaining frame.
The deliberate tests are supervisor write-to-RO (error 3), instruction fetch
from NX (error 17), and read from an explicitly unmapped address (error 0).
Stage 2's pre-VM #PF fixture remains unchanged and still halts as before.

## Tests and limitations

Guest tests include real mapped writes and physical reads, offset translation,
A/D bits, read-only and NX enforcement after warmed writable/executable TLB
entries, RX execution of RET, unmap fault and remap to a different frame,
three-page ranges crossing a PT boundary, high-half mappings, duplicate/alignment/
noncanonical/overflow/reserved-frame/W+X rejection, poisoned-table zeroing,
unsupported-huge-page rejection and inactive hierarchy destruction.

OOM tests exhaust the real PMM using an intrusive list in the allocated frames,
including high physical RAM. They prove root-allocation failure, intermediate
failure, rollback after two allocated tables, and range rollback after the first
leaf succeeds. All temporary frames/tables are restored and counted. IRQ0 runs
after VM; both PMM and VM integrity checks run again afterward.

Host tests validate the full transcript, cross-check PMM/VM totals and compare
fault RIPs with real ELF symbols. Negative kernel copies remove CR3 loading,
permission invalidation or table zeroing, or unarm/mismatch a real page fault;
none may print VM success. Same-image 16/64/128/256 MiB runs retain all earlier
tests. Reproducibility includes the unchanged separate icon package.

Known limitations: single CPU, IF=0, no NMI-safe window use, bootstrap seven-frame
availability below 2 MiB, existing PMM metadata-placement limit, no general
address-space activation/destruction of active spaces, no user mode/processes,
no general cache-policy/PAT programming API, no large pages/PCID/global mappings, no automatic
data ownership/refcounting, no demand paging, COW or swap. Dynamic kernel
allocation is provided by the separate Stage 6 kernel-heap subsystem
([heap.md](heap.md)), not by this VM layer. No physical
hardware validation or general recovery from corrupted stacks/tables is claimed.

Stage 9 adds `vm_map_device` / `vm_unmap_device`, kernel-space/IF=0 only, inside
slot 509. Non-RAM eligibility is checked against normalized PMM kinds, not
just a FREE bit. Driver must prove actual device ownership. Leaves use
PCD=PWT=1/PAT=0 (PAT3 checked UC when present); `vm_mapping.uncached` exposes
the actual entry state. Ordinary map/unmap/protect cannot alter this slot.
Tables still come from PMM, foreign device pages never do. Full preflight and
rollback reuse the existing implementation. See [framebuffer.md](framebuffer.md)
for ownership, validation and real OOM/partial-mapping tests.
