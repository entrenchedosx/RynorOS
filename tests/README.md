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
The original 26 tests remain with the metadata assertion advanced to Stage 7.

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

Stage 7 adds a strict scheduler output parser/repository test and real integration
cases. The guest `scheduler_self_test` creates three workers with real per-thread
kernel stacks, runs them under full PIT preemption, proves at least one genuine
supervisor switch, distinct per-worker stack markers, join/reaping and exact PMM
restoration, then a final idle check. Host validators record `preemptions`/`runs`;
a normal boot plus two broken builds (no genuine preemption, PMM imbalance)
prove the guest asserts fire with `[SCHED] failure=` halts. Logs are under
`build/sched-tests/`.
The audit strengthened corruption/near-full/reuse tests, actual initialization
OOM rollback, all arena-frame ownership, and cross-subsystem accounting. A bad
tag encoding now fails compilation rather than reaching QEMU. Tail-loss and
forged-interior-free mutants must fail their guest assertions. The former
image-size-growth assertion was removed: binary bloat is not functionality.

`test_audit.py` adds 8/512 MiB boots, the `max` CPU, NX-disabled rejection, and
real firmware RAM above 4 GiB using a 32 MiB below-4G limit. No guest memory
map is fabricated for that test. Current exact counts and command results are
in `../docs/reports/codebase-audit.md`; audit logs are under `build/audit-tests/`.

## Known limitations

No physical-hardware tests or coverage of user-mode isolation, device IRQs beyond IRQ0,
filesystem, graphics, userspace, or RynorLang execution. Serial success proves
only this milestone. Other exception vectors, nested faults, TSS/IST, SIMD state,
privilege transitions, BIOS disk-read error injection and forced-QEMU-cleanup fallbacks are
not separately exercised; normal monitor cleanup is asserted on success/timeout.
Slave PIC and spurious IRQ7/15 branches are implemented but not independently
injected. Packaging the icon is not a test or implementation of graphical output.
