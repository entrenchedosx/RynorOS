# RynorOS codebase audit

Historical audit of the Stage 6 baseline. Current execution-infrastructure
findings and verification are in [stage7-audit.md](stage7-audit.md).

Audit date: 2026-08-30. Starting committed baseline:
`be1da0a39941bf726c6d961f3d1502c38d294127` (Stage 5), matching GitHub main.
The working tree also contained 23 modified files and seven untracked files
from an uncommitted Stage 6 heap. After the user paused that agent, the audit
covered and repaired that work in place. No later roadmap stage was started.

## Overall assessment

**Stage 1–5: mostly sound within a narrow, documented bootstrap environment.**
The PMM and VM are real, not simulated allocators. The incoming heap, however,
**required significant repair**. Passing its tests did not establish safe block
management. The repaired kernel is a useful early single-CPU development
foundation, not a production-grade or usable general operating system.

The unmodified incoming checkout passed **56 repository tests and 27 integration
tests**, reproduced in `build/audit-baseline-check.log`. Four extra real-QEMU
probes then exposed defects that suite missed. This is direct evidence against
using milestone labels or serial success alone as a correctness argument.
The incoming heap source is retained locally (LF-normalized) in ignored
`build/audit-original/heap-before.c`, SHA-256
`c50604dde908f4482eefe2d786f0769f8f0b1df68f1f2ea2c7c77c90bd1fb5d0`.
Original fault transcripts are under `build/audit-original/`; they are not
compiled into the repaired kernel or committed as implementation.

## Scope and method

Inspected Git history/status/Stage 5 diff, the full source tree and incoming
changes; boot sector/transition/linker/entry; GDT/IDT and all exception/IRQ/test
assembly; serial/PIC/PIT; E820 normalization, PMM, VM and heap; host image/resource
builders, validators, QEMU lifecycle and repository/integration tests; architecture,
subsystem designs and historical reports; asset metadata and language samples.
Searches included TODO/FIXME/placeholder/stub/fake/mock/unreachable and misleading
status text. Findings below distinguish actual bugs, contract limitations and
future work, rather than treating a keyword match as a defect.

Architectural review used the [Intel SDM Volume 3 paging chapter](https://cdrdv2-public.intel.com/782157/325384-sdm-vol-3abcd.pdf)
and [Intel invalidation guidance](https://cdrdv2-public.intel.com/835748/252046-sdm-change-document.pdf).
Processors may retain translation information independently of page-table memory,
including information produced speculatively; invalidation ordering must therefore
be explicit. Tests under TCG are not a proof of every physical CPU cache behavior.
The firmware interface remains the documented ACPI E820 interface; no OS/kernel
implementation was imported as a repair.

## Confirmed working mechanisms

- **Boot:** original BIOS EDD sector loader, bounded one-sector transfers to the
  linked low payload, real E820 collection before leaving BIOS, A20/PAE/long-mode
  transition, BSS clear and a linker-owned 16 KiB stack. Original boot tables are
  three reserved pages mapping 2 MiB, not a competing dynamic physical allocator.
- **CPU:** kernel GDT load/readback, correct long-mode code/data descriptors with
  accessed bits preset; IDT gates; exception/error normalization; all fifteen
  non-RSP GPRs plus the CPU frame saved; DF cleared for C; aligned calls; IRETQ.
  Six exception vectors are actually exercised. CR2 is sampled before C work.
  No ring-3, TSS or IST mechanism is implied by these descriptors.
- **IRQ/timer:** PIC remapping/masking, ISR-based real-IRQ dispatch, manual EOI,
  PIT mode 2/divisor 11932, three hardware-derived samples and foreground return.
  No software timer counter or scheduler masquerades as hardware delivery.
- **PMM:** actual 20-byte SeaBIOS E820 records (24-byte format also supported),
  bounded handoff validation, conservative overlap/edge normalization, real
  reservations, bitmap in discovered RAM, frame allocation/release/reuse and
  accounting. No fixed RAM amount, malloc, hidden allocator or fabricated map
  supplies the normal allocator. Types/holes not explicitly usable remain excluded.
- **VM:** original four-level 4 KiB tables allocated/zeroed via PMM; physical
  address width from CPUID; 48-bit canonical validation; page-index/flag encoding;
  NX enable and CR0.WP validation; a genuine CR3 replacement; RX/R/NX/RW kernel
  mappings; serialized frame window; translation/map/unmap/protect; real RO/NX/
  nonpresent faults; table destruction and OOM rollback. Kernel execution, stack,
  GDT/IDT and bitmap remain mapped across the switch. Old boot tables are unmapped.
- **Build/assets:** freestanding NASM/Clang/LLD with no guest host-OS services,
  deterministic raw image and separate deterministic icon ZIP. The canonical
  icon is packaged, not rendered or loaded into the guest. `.rl` is only an
  intended source extension and syntax sample; no compiler exists.

## Significant defects and repairs

### 1. Heap lost short trailing fragments — confirmed corruption

Both allocation branches published the requested used size even when the tail
was too small to become a free block. A 16/32-byte remainder then belonged to no
block. A request of 65488 bytes in the fresh 65536-byte arena returned success
but failed complete-coverage checking. QEMU reproduction emitted
`[HEAP] failure=audit_tail_coverage`.

**Repair:** any remainder smaller than the minimum block is absorbed into the
allocated footprint. Plain and page-aligned near-full requests test 0/16/32-byte
tails, full payload writes, statistics, free and restored whole-arena coverage.
A deliberately restored tail-loss bug must fail the new guest tests.

### 2. Heap accepted a forged interior free — confirmed ownership violation

Free derived a header directly from the supplied pointer and trusted matching
magic/footer bytes. Caller payload containing plausible tags could therefore be
freed as though it were a real allocation. The original QEMU probe failed
`audit_interior_free`: the call was incorrectly accepted.

**Repair:** walk the validated arena partition from its true beginning and require
an exact payload-start match. Valid-looking tags inside payload do not establish
ownership. Regression tests forge those tags, require INVALID and unchanged
integrity, then free the real allocation. A mutation removing this check fails.

### 3. Heap metadata bounds checked too late — confirmed unexpected page fault

Free calculated/read a footer using unchecked block size. A size of 0x100000 in
a valid block caused a real #PF at `CR2=0xffffc000000ffff0` instead of a controlled
CORRUPT result. Addition-based bounds in other paths also permitted overflow;
free-list traversal could follow unvalidated payload pointers or loop indefinitely.

**Repair:** every public mutation/statistics path validates the complete bounded
partition first, using `size <= end - cursor` before footer arithmetic. Tests
cover zero, too short, misaligned, oversized and overflowing sizes, corrupt
footer tags, unchanged allocation output, and restoration after rejection.
No neighbouring block or accounting is modified before validation succeeds.

### 4. Heap integrity checker accepted a cycle — confirmed false assurance

A one-node free list pointing to itself was accepted: the checker broke when
the next node was the head, without proving termination/coverage. It also did
not prove every list node was a real partition boundary or that all free blocks
were reachable. The QEMU cycle probe failed `audit_cycle_rejection`.

**Repair:** removed the redundant free list entirely. Address-order first fit
over a bounded boundary-tag partition is sufficient for 64 KiB and eliminates
payload-link traversal/cycles/orphans. The checker now also rejects adjacent
free blocks and reconciles the exact partition with counters.

### 5. Heap initialization rollback violated frame lifetime and hid failures

Rollback released PMM backing frames before unmapping their virtual addresses,
ignored query/unmap/release errors, and translated every VM error into OOM.
This violated the documented borrowed-frame lifetime even if IF=0 and the lack
of concurrent allocation made the original happy-path test appear safe.

**Repair:** whole-range conflict preflight; explicit NOT_READY/VM_ERROR/
MAPPING_CONFLICT results; unmap and invalidate before physical release; checked
rollback and fail-stop on internal corruption. Real PMM exhaustion tests leave
0/1/4/18 free frames to exercise root/intermediate/partial-arena failure, then
verify no mappings, unchanged table counts/free bytes, restore the pool and retry.
A pre-existing last-arena-page mapping and its data must survive conflict rejection.

### 6. Aligned heap allocation unnecessarily required a leading fragment

The old aligned path always created a leading free block, even when the payload
was already aligned, and could reject a fitting request. The single allocation
path now permits zero leading gap and splits only representable nonzero gaps.
It handles every supported alignment with patterned, interleaved allocations.
A dedicated exact-fit test leaves an already page-aligned free payload and
consumes it without sacrificing an unnecessary leading fragment.

### 7. VM table retirement relied on implicit later cache effects

Unmap invalidated the leaf address **before** pruning parent entries; failed
insertion detached/freed new intermediate tables without a final explicit
invalidation. Frequent frame-window INVLPG operations clear paging-structure
caches incidentally, so a hardware failure was **not reproduced** and is not
claimed. Nevertheless, the local table-retirement invariant was not established
at the point frames became free and was fragile under future changes.

**Repair:** detach all affected entries, invalidate after the last unlink, then
release frames. Inactive spaces remain uncached because this kernel cannot
activate them. Range rollback errors no longer silently disappear. Tests cover
repeated mappings with a live sibling and active-space OOM rollback through heap
initialization. This repair does not add SMP shootdowns, PCID or address switching.

The audit also initially observed that a missing-unmap-INVLPG mutant **passed**:
software translation queries before the hardware probe disturbed cached state.
The test now warms the mapping with a live sibling, unmaps, performs the actual
faulting read before further VM queries, and only then runs those retained
query/double-unmap checks. The same mutant now fails the required hardware-fault
assertion. The original virtual address remains unchanged. This is stronger
coverage on the tested QEMU, not a claim that every CPU retains identical TLB state.

### 8. Tests and documentation overstated coverage

The heap stress used one uniform size that happened not to hit the lost-tail
case. It did not test true interior pointers, corrupted lengths, init rollback
or final PMM frame ownership. A payload-size-growth assertion rewarded binary
bloat, not behavior. The parser accepted implausible stress counts; its supposed
marker swap actually replaced both occurrences with the same marker.

**Repair:** adversarial guest tests, new broken-image tests, all sixteen arena
frame/permission/uniqueness checks, cross-PMM/VM/heap totals, exact stress
footprint validation, a real marker swap, and a payload-sector consistency check.
Zero root addresses are also rejected without relying on optional PMM context.
Unchecked PMM statistics calls in VM tests now require success.

Documentation falsely described 72-bit-aware paging, nesting checks, demand
mapping and arena teardown; the Stage 6 report contained contradictory payload
sizes; CONTRIBUTING still claimed VM absent; README retained seven final tables
after heap initialization. Those claims are corrected, and the uncommitted
Stage 6 report is replaced with a pointer to this verified audit record.
Heap tests are separated from allocator implementation in `heap-test.c`.
The BIOS presence-check call now preserves DS and the disk-error path clears DF;
this is defensive firmware-boundary hardening, not a reproduced SeaBIOS failure.

## Invariants, enforcement, evidence and failure behavior

| Subsystem | Invariant / enforcement | Evidence | Failure |
| --- | --- | --- | --- |
| E820/PMM | Only validated usable whole frames; linker/bitmap reservations; allocation bit and cursor | Real maps, corrupt handoffs, complete pool exhaustion/release, multiple RAM layouts | Explicit PMM status; initialization stops |
| VM | PMM-owned zeroed tables; borrowed leaves remain allocated; canonical/aligned/range-safe APIs | Real mapped writes and physical reads, poison reuse, OOM rollback and final counts | Distinct VM statuses; corruption during rollback halts |
| Table lifetime | Detach → invalidate → release | Source/order audit, active rollback and mapping regressions | Never publish a normal rollback result after failed cleanup |
| Permissions | Leaf W/U/NX plus permissive nonleaf flags, CR0.WP and EFER.NXE | Warmed write-to-RO error 3; NX fetch error 17; no-NX rejection | Real hardware #PF / unsupported initialization |
| Fault return | Exact one-shot arm, CR2/error/RIP/RSP/selectors/IF | ELF RIP comparisons, unarmed/wrong-address negative images | Unexpected faults diagnose/halt, never allocate or retry arbitrarily |
| Heap | Complete nonoverlapping partition, exact payload owner, bounded tags, coalesced frees | Original-bug probes plus adversarial repaired guest tests | INVALID/CORRUPT/OOM without mutation |
| IRQ | Real ISR bit, registered callback, EOI, complete IRETQ return | Three ticks; mask mutant zero ticks; EOI mutant one tick | Bounded host timeout; no success marker |
| Host | Fresh image/logs and owned-child cleanup | Build failure tests, stale logs, positive/negative QEMU and reproducibility | Nonzero exit; monitor quit and reap |

## Test quality classification

- **Strong:** real CPU faults with ELF-linked RIPs and register/IRETQ checks;
  warmed RO/NX enforcement; physical alias write/read; real PMM exhaustion;
  omission mutations for CR3/TLB/zeroing/EOI; new corruption, rollback and high-RAM
  execution. These observe behavior beyond a source string or file existing.
- **Useful:** strict transcripts, cross-subsystem totals, independent E820
  reconstruction, file/schema/asset checks, compile/link failures, reproducibility.
  They prove their own contracts, not general OS correctness.
- **Weak alone:** counts of tests, initialization markers, raw table existence,
  query-only permission checks for inactive USER mappings. No ring-3 isolation
  is claimed. Repeated normal boots are regressions, not independent subsystem proofs.
- **Misleading and corrected:** heap-size-growth test; implausible accepted OOM
  counts; ineffective marker-swap fixture; blanket heap corruption/lifetime claims.
  The synthetic parser/normalizer fixtures are explicitly labeled and were not
  mistaken for actual firmware or physical backing.

## Remaining architectural limitations / technical debt

1. One CPU and IF=0 are API preconditions, not locks, NMI safety or IRQ-context
   detection. No concurrency, shootdown, shared table or reference-count protocol.
2. VM handles/roots/table bytes are trusted kernel objects. Identity/count/PMM
   checks do not authenticate ownership or repair arbitrary aliased/cyclic table
   graphs. PMM allocation bits do not encode owner/generation. Borrowed data must
   outlive all mappings; stale pointers after reuse remain caller bugs.
3. Kernel stays low-linked; the frame window is transient and expires on the next
   VM call. No whole-RAM direct map. Public mappings cannot alter boot infrastructure
   or reserved top slots. There is no generic address-space switch, shared-kernel
   process layout or MMIO cache-policy mapping API. These require deliberate design.
4. Boot assumes a legacy PC/SeaBIOS layout, BIOS EDD, fast A20 and fixed low stack.
   PMM metadata and seven initial VM frames must fit accessible low RAM. E820 is
   capped at 64 entries, low 1 MiB retained, firmware/ACPI memory never reclaimed.
   VM range self-tests currently require a three-frame contiguous test span.
5. No TSS/IST emergency stack or reliable double-fault/stack-overflow recovery.
   NMI remains masked; slave PIC/spurious paths are not independently hardware
   exercised. No real-machine, KVM/hardware-assisted, SMP or interrupt-storm validation.
6. W+X rejection is per mapping, not a global physical-alias security policy.
   USER bits can be encoded in inactive hierarchies, but ring-3, user/kernel
   isolation, processes, COW, demand paging, swap and native executable loading
   do not exist. No new large-page implementation is present.
7. Default boot still runs exhaustive self-tests and halts after three IRQs.
   This is not a production boot profile or uptime service. Fault diagnostics and
   one-shot test recovery still share the VM test module; separate them before
   adding a non-self-test boot configuration. Tiny formatting routines are duplicated.
8. Heap is fixed 64 KiB with linear validation/search, no reclaim/growth/realloc,
   stale-pointer protection, allocation ownership tags or zero-fill guarantee.
9. Host tooling pins a machine contract and records versions, but this is not a
   cross-toolchain or supply-chain audit. Icon hashing proves identity, not graphics.

No TODO/FIXME or empty function was found masquerading as implemented kernel
functionality. Assembly “stubs” are real entry code, not placeholders. The
RynorLang, user, general-driver and future-test directories deliberately reserve
future work; implementing them would have expanded this audit into new stages.

## Final verification

All commands below completed with exit status 0 on the repaired source:

```powershell
$env:RYNOR_CLANG='D:\llvm-install\bin\clang.exe'
$env:RYNOR_LLD='D:\llvm-install\bin\ld.lld.exe'
$env:RYNOR_QEMU='C:\Users\aawad\scoop\apps\qemu\current\qemu-system-x86_64.exe'
python tools/build/build.py build
python tools/build/build.py boot-test
python tools/build/build.py test
python tools/build/build.py integration-test
python tools/build/build.py validate
python tools/build/build.py check
python -B -m unittest discover -s tests/repository -p 'test_*.py' -v
```

**57 repository tests and 34 integration tests passed, with no skips.** The
direct discovery independently passed the same 57 repository cases; it is not
another 57 distinct tests. Final command logs are locally retained under
`build/audit-final-*.log`. Repeated-build comparisons in the integration suite
passed for kernel, image and resource artifacts. No tool dependencies were added.
Verification used Python 3.14.3, NASM 3.02, Clang/LLD 23.1.0 at LLVM revision
`ea7d852a70e8bdfaf601d6626a760f9771b2c4b4`, and QEMU 11.1.0
(`v11.1.0-12130-ge470268ff4`).

QEMU used TCG, `pc-i440fx-10.0`, SeaBIOS `bios-256k.bin`, one CPU, an IDE raw
snapshot drive, serial-to-file and monitor-based shutdown. Normal runs use a
10-second deadline; deliberately broken images have shorter bounded deadlines.
Positive boots passed at **8, 16, 64, 128, 256 and 512 MiB**, with both the default
`qemu64` and additional `max` CPU tested. A 64 MiB configuration with
`max-ram-below-4g=32M` produced the real E820 record below and exhausted frames
through physical address 4328517632, proving the map/PMM did not truncate at 4 GiB:

```text
[MM] raw base=4294967296 length=33554432 type=1 attributes=1 size=20
[TEST] PMM exhausted frames=16095 last=4328517632
```

The feature-negative `qemu64,-nx` run stopped with `[VM] init_error=11` and no
VM/heap success marker. Other negative images intentionally broke CPU recovery,
E820 data, IRQ unmasking/EOI, CR3 activation, table zeroing, permission/unmap
invalidation, and heap boundaries/ownership. They were rejected rather than
accepted as successful boots. In particular, omitting unmap invalidation produced
`[VM] failure=expected_hardware_page_fault_missing` after the repaired test.
Owned QEMU processes were reaped; audit runtime records show `monitor-quit`,
return code 0, and `reaped: true`. A final host process inspection found no
`qemu-system-*` process remaining.

Selected exact serial output from the default 64 MiB run (full transcript:
`build/boot-test/serial.log`):

```text
[MM] firmware_usable_bytes=66580480
[MM] described_bytes=12951945216
[MM] usable_bytes=65925120
[MM] reserved_bytes=12886020096
[MM] metadata base=1048576 bytes=4096
[TEST] PMM exhausted frames=16095 last=66973696
[MM] final free_bytes=65925120 allocated_bytes=0
[TEST] PMM self-test passed
[VM] root=1052672 table_pages=7
[VM] fault_address=0x0000000040000000 error=0x0000000000000003 rip=0x0000000000008512
[VM] fault_address=0x0000000040000000 error=0x0000000000000011 rip=0x0000000040000000
[VM] fault_address=0x0000000040000000 error=0x0000000000000000 rip=0x0000000000008507
[TEST] controlled page fault verified
[VM] final table_pages=7 allocated_bytes=28672 free_bytes=65896448
[TEST] VM self-test passed
[HEAP] initialize arena=65536 mapped=65536
[HEAP] free_blocks=1
[TEST] HEAP initialization rollback verified
[TEST] HEAP adversarial boundaries and corruption verified
[HEAP] stress blocks=227 oom=1
[HEAP] PMM allocated_bytes=106496 free_bytes=65818624 table_pages=10
[HEAP] final used=0 mapped=65536
[TEST] HEAP self-test passed
[TIMER] tick=1
[TIMER] tick=2
[TIMER] tick=3
[TEST] timer interrupt handling verified
[TEST] PMM post-IRQ accounting verified
```

Reserved bytes include firmware-described MMIO windows, not just installed RAM.
Final PMM allocation is exactly 26 frames: ten tables and sixteen heap pages.
The heap has no live allocations, but its lifetime backing remains allocated.

Final artifact SHA-256 values (same toolchain, deterministic rebuilds):

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `boot.bin` | 512 | `fdbf7163a4f2975976ccf50c67efb3bece66e78d1e7e733efef0da0d2769de00` |
| `rynorkernel.bin` | 49168 | `26be97992f0283beec6d703c62a218266d6f71b1ee9516f9dff355b61da03146` |
| `rynorkernel.elf` | 62640 | `ba72d30ba41c5cb2611d48d027bf0c077a8b33bf4460dd5251d56666ce8fd3ac` |
| `rynoros.img` | 1048576 | `e68b10887986710f1bc22a630020802125173c076f9a0b7df2d445291936237c` |
| `rynoros-resources.zip` | 575058 | `8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30` |

The final handoff records the enclosing commit SHA and GitHub synchronization
only after those Git operations are verified. Generated logs/images and original
defect probes remain ignored build evidence, not kernel sources.

**Readiness:** suitable for continued bounded, single-CPU kernel development under
the stated contracts. It is not a production safety certification, a process
memory-isolation foundation without further design, or a usable desktop OS.
No hardware-input or later roadmap stage was implemented during this audit.
