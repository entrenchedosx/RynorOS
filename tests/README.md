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

Implemented: 26 `repository/` tests for paths, metadata, extension, image layout,
command exits, and actual native compile/link failures. Five `integration/` tests
build the kernel/image, verify real serial execution, inspect ELF architecture,
compare independent rebuilds, and reject blank/wrong-version images even with
stale success logs. The positive run records its serial/logs/cleanup in
`build/boot-test/`; negative fixtures are discarded after assertions.

`kernel/` reserves future isolated subsystem tests; `rynorlang/` reserves
cross-pass language conformance. Local language fixtures can live in
`../rynorlang/tests/`; none are executable today.

## Known limitations

No hardware tests or coverage of memory allocation, interrupts, scheduling,
filesystem, graphics, userspace, or RynorLang execution. Serial success proves
only this milestone. BIOS error injection and forced-QEMU-cleanup fallbacks are
not separately exercised; normal monitor cleanup is asserted on success/timeout.
