# Stage 5 — Virtual memory management

Implementation and verification report, 2026-08-30. Base commit:
`83135e3064529eb1015248d647956aaf22da0d6d`. Stage 6 is not included.
(Historical snapshot; see `codebase-audit.md` for later VM hardening and the
audited heap. This report's hashes, transcript and counts belong to Stage 5.)

## Implemented mechanisms

Original four-level, 48-bit canonical x86-64 paging with 4096-byte leaves.
The new PMM-owned root completely replaces bootstrap CR3. Page-aligned linked
code is RX, rodata R/NX, data/BSS/stack/bitmap RW/NX; unused bootstrap addresses
are no longer mapped. A two-page high virtual window provides serialized frame
access and access to its own PT; it is not a whole-RAM direct map. There is no
second physical allocator. Seven persistent table frames consume 28672 bytes.

The API creates/destroys inactive hierarchies, maps/unmaps single pages and
ranges, translates/queries addresses and changes permissions. Table frames are
owned by VM, data frames borrowed from callers. Canonical/alignment/overflow/
physical/permission errors are distinct; duplicates cannot overwrite mappings.
Table and range OOM roll back allocations. Active changes use INVLPG; CR3 is
reloaded only for the controlled bootstrap replacement.

Real faults use the existing CPU exception frame and captured CR2. Unexpected
faults halt; only exact one-shot armed test faults may return at designated
assembly labels. Actual read-only, NX and unmapped accesses generated errors
3, 17 and 0 respectively. The prior Stage 2 #PF test still runs before VM.

See [complete VM design](../design/virtual-memory.md) for layout, PML4/PDPT/PD/PT
entry encoding, flags, ownership, API errors, transition, TLB strategy, rollback,
fault design, invariants and limitations.

## Bootstrap and artifacts

No new external dependency. Python standard library, NASM, Clang/LLD and
QEMU/SeaBIOS remain host bootstrap tools, not guest services. Verification uses
the previously documented Windows tool overrides and pinned versions:
Python 3.14.3, NASM 3.02, Clang/LLD 23.1.0, QEMU 11.1.0, SeaBIOS 1.17.0.

The additional VM code and aligned sections exceeded the original 32 KiB load
window. The original sector loader now performs bounded one-sector INT 13h
reads, advancing LBA and destination segment. Requests cannot cross 64 KiB
boundaries; the maximum payload is 0x8000..0x70000. The actual normal payload
is **36880 bytes / 73 sectors**. It is genuinely loaded and
executed, including code beyond the previous bound. Disk format remains a
zero-padded 1 MiB raw image. Icon packaging is unchanged and separate.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `boot.bin` | 512 | `becbf9bcf6e4a66f8d5a976a8fce1d109d9cace1e3963b6e5e910c9d25a11f37` |
| `rynorkernel.bin` | 36880 | `f403892962d8e8ae8cd610bd62fccfbcfe5b9fc66910dfc22bb2845b90dc3ff8` |
| `rynorkernel.elf` | 49728 | `daf44216d2e0dd4b3649035aeed931228155a527195ab07d2f0e0fc687e5dfd7` |
| `rynoros-resources.zip` | 575058 | `8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30` |
| `rynoros.img` | 1048576 | `9d1122324227daea952292a3e4be5532e32c0e7885aa99a9f2c2a551f3dc8067` |

## Verification methodology and results

The suite contains **51 repository tests and 24 integration tests**. All 18
Stage 4 integration cases are retained. Five new parser tests reject missing,
reordered, malformed, contradictory and unbounded VM records. Six VM integration
tests use actual QEMU execution: normal paging with fault RIP/ELF comparisons,
then missing CR3 load, missing permission invalidation, missing post-bootstrap
table zeroing, unarmed fault and wrong expected fault address. Every broken
image must fail without VM completion and without timer ticks.

The guest exercises:

- Live root creation/activation, linked code/data/stack permissions, absent null
  and boot-table mappings, and all table frames counted as allocated PMM memory.
- Real mapped writes, physical reads through the window, offset translation and
  CPU-maintained accessed/dirty bits.
- Warmed RW -> RO write fault; actual RX execution -> NX fetch fault; unmap ->
  nonpresent read fault; remap to different RAM without stale TLB data.
- Three contiguous pages crossing a PT boundary and a high canonical mapping.
- Duplicate/alignment/noncanonical/range-wrap/reserved-frame/W+X/user-on-kernel
  rejection; unsupported huge configurations; zeroing a poisoned reusable frame.
- Inactive hierarchy destruction without freeing borrowed data.
- Real PMM exhaustion, root/table OOM, two-table rollback and partial range
  rollback, followed by complete restoration.
- Stage 1 boot, GDT/IDT, seeded breakpoint registers/IRETQ, all required exception
  variants, PIC/PIT, real timer IRQs after VM, and final PMM plus VM integrity.

Identical binaries boot with **16/64/128/256 MiB** of actual QEMU RAM; no RAM size
is passed into the allocator. Observed final accounting:

| QEMU RAM | VM table pages | Allocated bytes | Free bytes |
| --- | ---: | ---: | ---: |
| 16 MiB | 7 | 28672 | 15564800 |
| 64 MiB | 7 | 28672 | 65896448 |
| 128 MiB | 7 | 28672 | 133005312 |
| 256 MiB | 7 | 28672 | 267218944 |

PMM's unchanged reserved totals include firmware MMIO address space, not only RAM.

Commands from `D:\RynorOS`:

```text
python tools/build/build.py build
python tools/build/build.py boot-test
python tools/build/build.py test
python tools/build/build.py integration-test
python tools/build/build.py validate
python tools/build/build.py check
python -B -m unittest discover -s tests/repository -p test_*.py -v
```

All seven commands completed with exit status 0. All 51 repository tests and
24 integration tests passed, without skips. Full command logs are retained in
ignored `build/stage5-final-*.log`. The complete serial transcript and all five
artifact hashes below were compared against the actual generated outputs.
No QEMU processes remained after the command matrix.

QEMU: `pc-i440fx-10.0`, TCG, `qemu64`, one CPU, default `-m 64M`,
`bios-256k.bin`, raw IDE snapshot disk, serial file, monitor stdio,
`-display none -vga none -nic none -parallel none -no-reboot`, guest-error log.
Normal completion deadline is ten seconds; negative variants use two seconds.
No fragile fixed boot delay: the runner validates explicit output continuously.
Each run requests monitor quit, waits/reaps its exact child, and records command,
PID, return code and cleanup in `run.json`. Forced cleanup is a failed gate.
The byte-identical rebuild test compares all five artifacts, including the icon ZIP.

## Exact observed default serial output

Copied from the real `build/boot-test/serial.log`; serial uses CRLF.
The legacy prefix and PMM/IRQ transcripts are preserved.

```text
Rynorkernel booted.
RynorOS 0.1.0 | x86_64 | stage1
[CPU] GDT initialized
[CPU] IDT initialized
[TEST] triggering controlled exception
[EXCEPTION] vector=03 name=breakpoint error_source=synthetic error=0x0000000000000000
[STATE] rip=0x000000000000843b cs=0x0000000000000008 rflags=0x0000000000000402 rsp=0x000000000007ffa8 ss=0x0000000000000010
[GPR] rax=0x0000000000000101 rbx=0x0000000000000102 rcx=0x0000000000000103 rdx=0x0000000000000104
[GPR] rbp=0x0000000000000105 rsi=0x0000000000000106 rdi=0x0000000000000107 r8=0x0000000000000108
[GPR] r9=0x0000000000000109 r10=0x000000000000010a r11=0x000000000000010b r12=0x000000000000010c
[GPR] r13=0x000000000000010d r14=0x000000000000010e r15=0x000000000000010f
[EXCEPTION] action=resume
[TEST] exception handling verified
[MM] firmware memory map acquired
[MM] entries=7
[MM] physical_bits=40
[MM] raw base=0 length=654336 type=1 attributes=1 size=20
[MM] raw base=654336 length=1024 type=2 attributes=1 size=20
[MM] raw base=983040 length=65536 type=2 attributes=1 size=20
[MM] raw base=1048576 length=65929216 type=1 attributes=1 size=20
[MM] raw base=66977792 length=131072 type=2 attributes=1 size=20
[MM] raw base=4294705152 length=262144 type=2 attributes=1 size=20
[MM] raw base=1086626725888 length=12884901888 type=2 attributes=1 size=20
[MM] regions=8
[MM] region base=0 end=651264 kind=8
[MM] region base=651264 end=655360 kind=2
[MM] region base=983040 end=1048576 kind=2
[MM] region base=1048576 end=1052672 kind=9
[MM] region base=1052672 end=66977792 kind=1
[MM] region base=66977792 end=67108864 kind=2
[MM] region base=4294705152 end=4294967296 kind=2
[MM] region base=1086626725888 end=1099511627776 kind=2
[MM] firmware_usable_bytes=66580480
[MM] described_bytes=12951945216
[MM] usable_bytes=65925120
[MM] reserved_bytes=12886020096
[MM] free_bytes=65925120
[MM] allocated_bytes=0
[MM] metadata base=1048576 bytes=4096
[MM] allocator initialized
[TEST] PMM self-test started
[TEST] PMM map validation passed
[TEST] PMM reservations verified
[TEST] PMM allocated frame=1052672
[TEST] PMM allocated frame=1056768
[TEST] PMM allocated frame=1060864
[TEST] PMM allocated frame=1064960
[TEST] PMM allocated frame=1069056
[TEST] PMM allocated frame=1073152
[TEST] PMM allocated frame=1077248
[TEST] PMM allocated frame=1081344
[TEST] PMM physical RAM write verified
[TEST] PMM reused frame=1052672
[TEST] PMM exhausted frames=16095 last=66973696
[MM] final free_bytes=65925120 allocated_bytes=0
[TEST] PMM self-test passed
[VM] paging subsystem initialized
[VM] kernel address space created
[VM] CR3 loaded
[VM] root=1052672 table_pages=7
[VM] kernel mappings verified
[TEST] VM self-test started
[VM] mapping va=1073741824 physical=1081344 offset_physical=1085432
[TEST] VM mapping verified
[TEST] VM invalid mappings rejected
[TEST] triggering read-only page fault
[VM] page fault
[VM] fault_address=0x0000000040000000 error=0x0000000000000003 rip=0x0000000000008512
[VM] present=1 write=1 user=0 reserved=0 fetch=0 cpl=0
[VM] page fault action=resume_test
[TEST] triggering non-executable page fault
[VM] page fault
[VM] fault_address=0x0000000040000000 error=0x0000000000000011 rip=0x0000000040000000
[VM] present=1 write=0 user=0 reserved=0 fetch=1 cpl=0
[VM] page fault action=resume_test
[TEST] VM permissions verified
[TEST] VM unmapping verified
[TEST] triggering controlled page fault
[VM] page fault
[VM] fault_address=0x0000000040000000 error=0x0000000000000000 rip=0x0000000000008507
[VM] present=0 write=0 user=0 reserved=0 fetch=0 cpl=0
[VM] page fault action=resume_test
[TEST] controlled page fault verified
[TEST] page fault diagnostics verified
[TEST] VM TLB invalidation verified
[TEST] VM ranges and high addresses verified
[TEST] VM address-space destruction verified
[TEST] VM real OOM rollback verified
[VM] final table_pages=7 allocated_bytes=28672 free_bytes=65896448
[TEST] VM self-test passed
[IRQ] controller initialized
[TIMER] initialized
[TIMER] clock_hz=1193182 divisor=11932 mode=2
[TEST] waiting for timer interrupts
[TIMER] tick=1
[TIMER] tick=2
[TIMER] tick=3
[TEST] timer interrupt handling verified
[TEST] PMM post-IRQ accounting verified
```

## Failures found and corrected

The initial link exceeded the old 32 KiB bound; bounded sector loading and a
matching linker/build bound resolved the real limitation. The first guest VM
run passed but the Stage 4 host parser rejected the additional output; the new
strict VM parser validates it rather than ignoring it.

The first missing-zeroing test correctly failed during initialization (Stage 4
had left real data in reused RAM), earlier than its expected poisoned-root
assertion. The negative mutation now omits only post-bootstrap zeroing, proving
the specific recycled-table test while leaving bootstrap construction intact.
Missing permission INVLPG prevented the expected hardware write fault; the
negative test detects that absence. No canned fault or allocator output is used.

## Files changed

Core additions: `kernel/include/vm.h`, `kernel/include/paging.h`,
`kernel/mm/vm.c`, `kernel/mm/vm-test.c`,
`kernel/arch/x86_64/vm-test.asm`.

Integration: `boot/sector.asm`, `kernel/arch/x86_64/linker.ld`,
`kernel/core/main.c`, `kernel/include/cpu.h`,
`kernel/interrupts/exceptions.c`, `tools/host/image.py`,
`tools/host/boot_output.py`, new `tools/host/vm_output.py`,
`tools/host/qemu.py`, `tools/host/repository.py`,
`tools/build/build.py`, `project.json`.

Tests: new `tests/repository/test_vm_output.py` and
`tests/integration/test_vm.py`; updated PMM transcript fixture and metadata
negative test. Existing kernel exception and timer implementation is preserved.

Documentation: root README/ROADMAP/ARCHITECTURE, boot/kernel/mm/tools/tests
READMEs, CPU/IRQ/PMM/bootstrap design documents, new VM design and this report,
plus a historical annotation on the Stage 4 report. No unrelated assets or
generated binaries enter the milestone commit.

## Limitations and next milestone

No user mode, process address spaces, generic context switching, heap, COW,
demand paging, swap, large-page API, MMIO mapping API, PAT/PCID/global pages,
SMP or hardware validation. Kernel handles are trusted objects; borrowed data
lifetimes are caller-managed, not reference counted. The one-frame window
requires serialized IF=0 calls. Initial table construction requires seven PMM
frames inside the existing 2 MiB boot mapping. PMM's existing placement limits
remain. Unexpected or nested faults and invalid stacks have no general recovery.

Next at that snapshot: **Stage 6 — Kernel heap** (subsequently audited and repaired; see
[`stage6.md`](stage6.md)). The established workflow remains
implement, test, verify, commit, clean tree, push, report SHA. The task handoff
records the actual commit and GitHub result only after Git confirms them.
