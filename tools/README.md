# Host foundation tools

## Purpose

Provide one small cross-platform command entry point for repository validation,
available host-source compilation, and repository tests.

## Public interfaces

`python tools/build/build.py {validate,build,test,check}`; exit code 0 means the
requested foundation check passed, nonzero means failure. Invalid commands use
argparse's nonzero error exit. `host/repository.py` owns the Stage 0 path contract,
version-1 metadata contract, and `.rl` recognition. Metadata intentionally has
exact fields/values for this foundation; later stages must revise the contract
and its tests together. It is not a general component/plugin manifest.

## Invariants

No downloaded dependencies, network access, host setting changes, or OS-image
claims. Resolve paths relative to the entry point, not the current directory.
Syntax compilation uses disposable temporary output; tests must not mutate the
real repository. Build failure stops the combined check immediately.

## Implementation status

Implemented: `validate` checks required directories/files, canonical metadata,
and recognition of the proposed `.rl` sample; `build` additionally compiles host
Python tooling and tests to temporary bytecode; `test` discovers repository
unittests; `check` combines build and tests. No OS targets are registered.

## Tests

`../tests/repository/` covers real and temporary fixture validation, missing
paths, malformed/type-invalid metadata, and CLI success/failure behavior.

## Known limitations

No native compilation, language parsing, emulator integration, image creation,
dependency installation, caching, or packaging. Presence checks do not establish
document accuracy or runtime behavior; those require review and later tests.
Python 3.10+ is the sole required execution dependency.
