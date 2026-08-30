# Tests

## Purpose

Keep foundation checks separate from future system behavior verification.

## Public interfaces

Run `python tools/build/build.py test`, or directly run
`python -B -m unittest discover -s tests/repository -p test_*.py -v` from the root.

## Invariants

Assert real behavior. Fixtures use temporary directories. Missing checks, empty
suites, and reserved test directories must never be reported as kernel coverage.

## Implementation status

Implemented: `repository/` path/metadata and host-command tests.
Planned: `kernel/` kernel behavior, `rynorlang/` cross-pass language conformance,
and `integration/` emulator/runtime/filesystem checks. Subcomponent language
fixtures can live under `../rynorlang/tests/` without duplicating conformance tests.

## Tests

The foundation suite includes invalid metadata and missing-path cases as well
as valid repository checks; command tests exercise actual subprocess exit codes.

## Known limitations

No kernel, compiler, runtime, boot, filesystem, or hardware behavior is tested.
