# Tests

## Purpose

Separate repository/tool checks from real emulated kernel execution.

## Public interfaces

```text
python tools/build/build.py test
python tools/build/build.py boot-test
python tools/build/build.py integration-test
python tools/build/build.py check
```

Direct repository discovery: `python -B -m unittest discover -s tests/repository -p test_*.py -v`.
Configure the tools listed in `../docs/design/bootstrap-dependencies.md` first.

## Invariants

Tests assert actual behavior. Missing dependencies or empty discovery fail;
they never silently skip an acceptance gate. Failure fixtures use temporary
directories. Only generated files under `build/` may change in the real repository.
Real QEMU runs are bounded and each owned process is stopped and waited for.

## Implementation status and tests

Implemented: repository tests for paths, metadata, extension, image layout,
command exits, native compile/link failures, strict diagnostic/timer/heap parsing,
canonical PNG integrity, deterministic resource package contents and PMM map/
accounting/ownership transcript validation.
Parser fixtures are explicitly synthetic test data, never kernel execution evidence.
The original 26 tests remain with the metadata assertion advanced to Stage 8.

The `integration/` suite retains the five Stage 1 regression cases and adds
real #DE/#DB/#UD/#GP/#PF execution and an unarmed-breakpoint negative case.
The normal boot covers #BP and verifies IRETQ restoration, then requires PIC/PIT
initialization and three actual IRQ0 counter samples/returns. All six saved RIPs
are compared with actual ELF symbols; complete diagnostics check seeded GPRs,
CPU flags/selectors, hardware versus synthetic errors, and CR2 for #PF. A
Stage 1 prefix alone cannot pass. Byte-identical rebuild, blank disk, wrong
version, and stale-log rejection coverage remains. No existing case was removed.
Temporary kernel copies prove masked IRQ0 produces zero ticks, and missing master
EOI stops after one tick. Both tests require a timeout and no timer success.
The IRQ registration rejection checks and final PIC ISR/mask readback are also
executed by the default kernel test before it reports timer success.

Stage 4 adds real E820/PMM boot tests at 16/64/128/256 MiB using the same image.
They check raw-to-normalized map transformations, every linked live region's
reservation, actual physical writes, allocation/release/reuse, whole-pool OOM,
metadata growth and full accounting restoration. Timer testing follows PMM,
then accounting is checked again. Four corrupt-handoff cases reject incomplete
maps, invalid entry sizes, overflowing lengths and firmware-reserved kernel RAM.

Normal logs are in `build/boot-test/`; variant images/logs persist in
`build/cpu-tests/`; timer-negative logs are in `build/timer-tests/`. Blank/wrong-version
and timer-negative temporary fixtures are discarded after
assertions. Every run asserts normal monitor quit/reaping; forced cleanup is failure.
PMM test logs are in `build/pmm-tests/`; corrupt-handoff fixture images/sources
are discarded. Guest synthetic map fixtures test the actual C normalizer but
never provide the map or backing storage used by the real allocator tests.

`kernel/` reserves future isolated subsystem tests; `rynorlang/` reserves
cross-pass language conformance. Local language fixtures can live in
`../rynorlang/tests/`; none are executable today.

The VM suite includes five strict parser tests and seven real integration tests. The
normal VM case compares hardware fault RIPs against ELF symbols and PMM totals;
six broken builds test skipped CR3, stale permission/unmap TLB entries, omitted
post-bootstrap table zeroing, unarmed faults and wrong expected fault addresses. Logs are under
`build/vm-tests/`. The same-image RAM-size tests now exercise VM as well as PMM.

Stage 6 adds a strict kernel-heap output parser/repository test and real
integration cases. The guest `heap_self_test` exercises allocation and free,
alignment and boundary-tag coalescing, corruption detection, statistics and a
bounded-arena OOM path, all backed by real PMM frames mapped through the kernel
space; the host validator cross-checks the printed arena/block transcript.

Stage 7's independent audit replaced assertion-only negative tests with actual
implementation mutations. Separate guest tests cover stack ownership, guard/map
conflicts, 0..7-frame OOM rollback, lifecycle exhaustion/reuse/stale IDs, IRQ and
lock restrictions, then non-yielding assembly workers. Actual IRQ RIP/RSP and
register/flag checks prove timer preemption; host comparisons use linked ELF
symbols. Four-, two- and one-runnable-context cases are exercised. Guard/NX faults,
bad pointers/selectors/handoffs, missing frame releases, stale IDs and broken
register/IF restoration must fail. Normal scheduler images boot repeatedly.
Logs are under `build/sched-tests/`; exact scope/counts are in `stage7-audit.md`.
The audit strengthened corruption/near-full/reuse tests, actual initialization
OOM rollback, all arena-frame ownership, and cross-subsystem accounting. A bad
tag encoding now fails compilation rather than reaching QEMU. Tail-loss and
forged-interior-free mutants must fail their guest assertions. The former
image-size-growth assertion was removed: binary bloat is not functionality.

The Stage 8 audit replaced count-only ring tests and assertion-inversion
mutations. Local queue instances check exact FIFO contents, capacity, wraps,
overflow retention/reuse and loss boundaries; decoder tests check supported keys
and prefix isolation. Real boots report eight host-selected keys, including
reordered/repeated keys, shifts and an explicitly UNKNOWN key. The same image is
used for different sequences; no expected key order is compiled into the guest.
Independent QEMU device, PIC IRQ1 and port-read traces must match every byte.
IRQ0 schedules a busy worker concurrently, and resource accounting must balance.
Actual masked/discard/no-read/counter/ring/decoder/loss/initialization and replay
mutations must fail. Some failures are intentionally detected by the HOST despite
a guest success marker; earlier-stage failure cannot substitute for that evidence.
Logs are under `build/kbd-tests/`; exact results are in the Stage 8 audit report.

Stage 9 adds framebuffer validation: the repository display output suite requires
the handoff/geometry/accounting records in order and gates the mapping VA and
table accounting against the keyboard baseline. The integration `test_display.py`
boots the real image and compares every framebuffer byte (`pmemsave`) and every
actual scanout pixel (`screendump`) against an independent host pattern/font
specification. Padded stride and paths with spaces are exercised. Corrupted boot
metadata and mutations breaking bounds/stride/clipping/glyphs/device ownership/
cache/mapping/text writes must fail in the display stage, not an earlier test.
Canned success cannot satisfy these evidence gates. Guarded local-buffer tests
are explicitly synthetic; device writes and PMM exhaustion/rollback execute in
QEMU. Logs are under `build/fb-tests/`; exact results are in the Stage 9 audit.

`test_audit.py` adds 8/512 MiB boots, the `max` CPU, NX-disabled rejection, and
real firmware RAM above 4 GiB using a 32 MiB below-4G limit. No guest memory
map is fabricated for that test. Current exact counts and command results are
in `../docs/reports/stage9-audit.md`; audit logs are under `build/audit-tests/`.

## Known limitations

No physical-hardware tests or coverage of user-mode isolation, device IRQs beyond
IRQ0 (timer), the single IRQ1 PS/2 keyboard, console, windowing, filesystem, userspace,
or RynorLang execution. Serial success proves
only this milestone. Other exception vectors, nested faults, TSS/IST, SIMD state,
privilege transitions, BIOS disk-read error injection and forced-QEMU-cleanup fallbacks are
not separately exercised; normal monitor cleanup is asserted on success/timeout.
Slave PIC delivery is not independently hardware-injected. Stage 7's software
INT probes exercise the spurious IRQ7/15 return paths, not external delivery.
Packaging the icon is not a test or implementation of graphical output.
