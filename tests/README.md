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

Implemented: 40 `repository/` tests for paths, metadata, extension, image layout,
command exits, native compile/link failures, strict diagnostic/timer parsing,
canonical PNG integrity and deterministic resource package contents.
Parser fixtures are explicitly synthetic test data, never kernel execution evidence.
The original 26 tests remain with the metadata assertion advanced to Stage 3.

Thirteen `integration/` tests retain the five Stage 1 regression cases and add
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

Normal logs are in `build/boot-test/`; variant images/logs persist in
`build/cpu-tests/`; timer-negative logs are in `build/timer-tests/`. Blank/wrong-version
and timer-negative temporary fixtures are discarded after
assertions. Every run asserts normal monitor quit/reaping; forced cleanup is failure.

`kernel/` reserves future isolated subsystem tests; `rynorlang/` reserves
cross-pass language conformance. Local language fixtures can live in
`../rynorlang/tests/`; none are executable today.

## Known limitations

No physical-hardware tests or coverage of memory allocation, device IRQs beyond IRQ0, scheduling,
filesystem, graphics, userspace, or RynorLang execution. Serial success proves
only this milestone. Other exception vectors, nested faults, TSS/IST, SIMD state,
privilege transitions, BIOS error injection and forced-QEMU-cleanup fallbacks are
not separately exercised; normal monitor cleanup is asserted on success/timeout.
Slave PIC and spurious IRQ7/15 branches are implemented but not independently
injected. Packaging the icon is not a test or implementation of graphical output.
