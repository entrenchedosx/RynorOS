# Host tools

## Purpose

One Python entry point extends the foundation with a minimal native image build
and real QEMU boot verification. Host tools are not RynorOS functionality.

## Public interfaces

`python tools/build/build.py COMMAND`:

- `validate`: required paths, nonempty files, exact Stage 1/schema-2 metadata,
  and `.rl` source recognition. Python only; not proof that code executes.
- `build`: validate, compile all host/test Python to temporary bytecode, assemble
  NASM sources, compile freestanding C with Clang, link ELF and flat payload with
  LLD, assemble the BIOS sector, and construct a zero-padded 1 MiB raw image.
- `test`: 26 repository/layout/CLI tests, including real build failures; requires
  native build tools but not QEMU.
- `boot-test`: build then run the serial smoke test; `--timeout SECONDS` defaults
  to 10, must be finite and greater than zero and at most 60.
- `integration-test`: build, QEMU positive/negative tests, ELF checks, independent
  byte-for-byte rebuild; requires all bootstrap dependencies.
- `check`: build, test, integration-test; short-circuits on failure.

Exit 0 means success; nonzero reports failure. Tool subprocesses have 60-second
limits. Use PATH or executable overrides `RYNOR_CLANG`, `RYNOR_LLD`, `RYNOR_NASM`,
`RYNOR_QEMU`. See `../docs/design/bootstrap-dependencies.md`.

## Invariants

No network/install steps, shell interpolation, physical disk access, or persistent
host setting changes. Paths derive from the entry point. Native compilation
starts by invalidating only named old deliverables and the manifest; artifacts
are prepared in a temporary directory and published only after all tools pass.
Preliminary validation/Python syntax failures occur before native build begins;
older outputs may remain in that case, but the command fails and no boot follows.
Do not run simultaneous builds into the same destination.

The image has no wall-clock values or host paths. Reproducibility means identical
inputs and tool versions yield identical boot sector, ELF, flat binary, and disk
image; it is tested across different temporary output directories. No identical
code generation across compiler versions is promised. The build manifest records
tool versions and artifact hashes; it is not an OS runtime component.

QEMU has no display, VGA, network, or parallel port, uses a snapshot disk, and
has serial output separate from monitor stdio. Each run truncates old logs,
requires both exact serial lines, and sends monitor `quit` in `finally`, with
3-second normal shutdown then 2-second terminate/kill fallbacks. The owned PID is
always waited for; forced cleanup fails a successful-boot claim. Interrupting the
test normally also executes cleanup; forcibly killing the host runner cannot be
guaranteed to do so. `run.json` records PID, command, exit, and cleanup.

## Implementation status and tests

Implemented Stage 1. `host/repository.py` owns the schema; `host/image.py` the
fixed-layout image build; `host/qemu.py` the execution harness. Tests cover path
and metadata failures, actual compile/link failures, image bounds, output
reproducibility, blank disk timeout, wrong version output, and stale-log rejection.

## Known limitations

No RynorLang compiler, incremental cache, package manager, filesystem builder,
or general-purpose image format. Python 3.10+ is declared; verification used
Python 3.14.3 on Windows only. Tested native tool versions and firmware are pinned
in the report; missing tools fail instead of skipping boot checks.
