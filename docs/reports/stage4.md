# Stage 4 — Physical memory management

Historical Stage 4 snapshot. Stage 5 adds the VM subsystem and extends the BIOS
payload limit; current behavior and verification are in `stage5.md`. (Stage 6
adds the kernel heap; see `stage6.md`.)

Implementation and verification report, 2026-08-30. Base commit:
`726180c0559a64f46616e7944f73a9d542732b6a`. No Stage 5 work is included.

## Implemented design

Original INT 15h/E820 collection before long mode, a bounded/versioned handoff,
defensive normalization, linker-derived bootstrap reservations, a physical
allocation bitmap placed in discovered mapped RAM, 4096-byte frame allocation/
release/state/statistics, and real-pool exhaustion/reuse tests. The existing
static boot paging is unchanged; no virtual-memory manager or isolation exists.

See [physical memory design](../design/physical-memory.md) for entry format,
ownership, validation/normalization rules, exact reservations, metadata placement,
API invariants and failure codes. Bootstrap dependencies remain Python standard
library, NASM, Clang/LLD and QEMU/SeaBIOS. No host tools execute as guest services.

## Verification

The complete suite passes **46 repository tests and 18 integration tests**, with
no skips. The integration suite retains all 13 Stage 3 tests. The five new PMM
tests include one same-binary run at four RAM sizes and four corrupt-handoff
negative cases. The same image responds to actual firmware map differences;
neither host RAM values nor parser fixtures are fed into the allocator.

Commands used from `D:\RynorOS` with the existing documented tool overrides:

```text
python tools/build/build.py build
python tools/build/build.py boot-test
python tools/build/build.py test
python tools/build/build.py integration-test
python tools/build/build.py validate
python tools/build/build.py check
python -B -m unittest discover -s tests/repository -p test_*.py -v
```

Every command above completed with exit status 0. The report's complete serial
transcript was compared exactly against the captured normal-boot log, and the
final diff was checked for whitespace errors and unrelated changes before the
last full verification run and commit.

QEMU configuration is unchanged except tests also vary `-m`: pinned
`pc-i440fx-10.0`, TCG, `qemu64`, one CPU, SeaBIOS `bios-256k.bin`, snapshot IDE
raw image, serial file capture, monitor stdio, no display/VGA/network/parallel
port, `-no-reboot`, guest-error log. Default completion deadline is ten seconds;
corrupted images must fail within two seconds. The runner keeps monitor input
open until `quit` is consumed, waits for exit, and uses bounded cleanup fallbacks.
Only normal monitor quit/exit 0/reaping passes. Exact commands and PIDs are in
each generated `run.json`; successful/expected-timeout tests leave no owned
QEMU process running.

Verified behavior:

- Genuine E820 20-byte records, signature/size/completion checks in boot, bounded
  header/record/range checks in C, and CPUID physical address width (40 here).
- GDT/IDT/breakpoint frame and register restoration; all other Stage 2 required
  CPU exception types still execute in their separate images.
- Correct normalization and reservations reconstructed independently by the host
  from captured raw records, with every linked live region checked unavailable.
- Single/eight-frame unique allocations, actual RAM writes, release, rejected
  double free, exact reuse, real full-pool OOM, unchanged failed-allocation output,
  release of the whole pool and independent accounting recount.
- PIT IRQ0 delivers three ticks after PMM; another PMM integrity check follows.
  Masked-IRQ and missing-EOI failures still behave as expected.
- Corrupted actual handoffs (incomplete, 21-byte record, overflowing length,
  firmware-reserved kernel RAM) fail before allocator initialization, never
  report PMM success and never start the timer test.
- Original icon resource package unchanged; all five generated artifacts compare
  byte-identically across independent output directories.

### Exact statistics from the actual QEMU runs

All four runs returned seven entries and restored allocated bytes to zero.
Values are bytes except the final column; initial free bytes equal usable bytes.

| QEMU RAM | Firmware usable | PMM usable/free | Reserved address-space bytes | Bitmap bytes | Frames exhausted/released |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16 MiB | 16248832 | 15593472 | 12886020096 | 4096 | 3807 |
| 64 MiB | 66580480 | 65925120 | 12886020096 | 4096 | 16095 |
| 128 MiB | 133689344 | 133033984 | 12886020096 | 4096 | 32479 |
| 256 MiB | 267907072 | 267247616 | 12886024192 | 8192 | 65246 |

The bitmap begins at actual discovered usable address **1048576 (0x100000)** in
these maps. It is not hardcoded there by the allocator. With 256 MiB its second
page is also reserved and the first allocated frame changes accordingly.
Large reserved totals come from real high-address firmware reservations,
including a 12 GiB window ending at 2^40; these are **not installed RAM**.

### Exact observed default 64 MiB serial output

Serial uses CRLF. The retained current-run log is `build/boot-test/serial.log`.

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

### Generated artifacts

The normal payload is 18544 bytes, 37 sectors; BSS is zeroed separately and
reserved using linker symbols. No large physical-frame backing array is in the
kernel image. The raw boot disk remains exactly 1 MiB.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `boot.bin` | 512 | `80834c568b52e3673beddfd00a3d2b9180eebe016026d8ac476f71309b9726c3` |
| `rynorkernel.bin` | 18544 | `8e5154b6415ba1a557d7760d071fa4c7eb7ab682595a8f565561a37bf340a4e2` |
| `rynorkernel.elf` | 29688 | `272376eb11ebf9b1236ea3a51c0e909de096f8d8f3170610bdc87d8db5f422af` |
| `rynoros.img` | 1048576 | `52d370a67463e57828c2f914df95a7c42395fc563bd3fb71b7a6dd302df95da0` |
| `rynoros-resources.zip` | 575058 | `8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30` |

### Development failures corrected

An aggregate test-fixture initializer caused Clang to emit an unavailable
`memcpy`; assigning the small explicit fields removed the hidden runtime
dependency without adding a library. The first real PMM boot passed in the
guest but the old Stage 3 host transcript parser rejected its additional lines;
the new parser validates those records instead of ignoring them. A metadata-
growth assertion incorrectly expected 128 MiB to require two bitmap pages;
one page correctly covers it, so the test now includes 256 MiB and verifies
the observed two-page bitmap there. No allocator failure was hidden or replaced
with canned output.

## Files changed and Git workflow

New sources: `kernel/include/boot_memory.h`, `kernel/include/pmm.h`,
`kernel/mm/map.c`, `kernel/mm/pmm.c`, `kernel/mm/selftest.c`; the obsolete
`kernel/mm/.gitkeep` is removed (recoverable in Git history).

Boot integration: `boot/transition.asm`, `kernel/arch/x86_64/linker.ld`,
`kernel/arch/x86_64/entry.asm`, `kernel/core/main.c`.

Host/test integration: new `tools/host/pmm_output.py`, `tools/host/boot_output.py`,
`tests/repository/test_pmm_output.py`, `tests/integration/test_pmm.py`; updated
`tools/host/image.py`, `tools/host/qemu.py`, `tools/host/repository.py`,
`tools/build/build.py`, `tests/integration/test_boot.py`,
`tests/repository/test_repository.py`, `project.json`.

Documentation: `README.md`, `ROADMAP.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`,
`boot/README.md`, `kernel/README.md`, `kernel/mm/README.md`, `tools/README.md`,
`tests/README.md`, `docs/design/cpu.md`, `docs/design/irq-timer.md`,
`docs/design/physical-memory.md`, `docs/design/bootstrap-dependencies.md`,
`docs/reports/stage3.md` (historical annotation), and this report.

The repository initially had branch `main` and no remote. The requested GitHub
URL was reachable and had no refs. After final diff review and verification,
the milestone is committed locally, the tree checked clean, and that URL is
configured as `origin` for a normal push (no force). Exact commit/push outcome
is reported in the task handoff; no push is claimed before Git confirms it.
`CONTRIBUTING.md` records the requested commit/clean-tree/push rule for future
completed milestones. No generated images or unrelated assets are committed.

## Limitations

No virtual-memory manager, heap, scheduler, userspace isolation, frame zeroing,
ACPI reclaim, SMP, filesystem or RynorLang. The first MiB remains conservatively
reserved, map capacity is 64 records, and bitmap placement must fit existing
mapped usable RAM. Reserved-address-space totals include firmware MMIO windows,
not merely installed RAM. Next milestone is Stage 5, not started in this change.
(Stage 6 now implements a bounded kernel heap; see `stage6.md`.)
