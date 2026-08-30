# Host tools

## Purpose

One Python entry point extends the foundation with a minimal native image build
and real QEMU boot/CPU-exception/timer verification plus deterministic icon packaging.
Host tools are not guest RynorOS functionality.

## Public interfaces

`python tools/build/build.py COMMAND`:

- `validate`: required paths, nonempty files, exact Stage 3/schema-4 metadata,
  canonical icon header/hash and `.rl` recognition. Python only; not execution proof.
- `build`: validate, compile all host/test Python to temporary bytecode, assemble
  NASM sources, compile freestanding C with Clang, link ELF and flat payload with
  LLD, assemble the BIOS sector, construct a zero-padded 1 MiB raw image, and
  publish a separate `rynoros-resources.zip` icon package.
- `test`: 40 repository/layout/parser/CLI/resource tests, including real build failures; requires
  native build tools but not QEMU.
- `boot-test`: build then verify boot prefix, full breakpoint diagnostics and three real timer ticks; `--timeout SECONDS` defaults
  to 10, must be finite and greater than zero and at most 60.
- `integration-test`: build, 13 QEMU/ELF/reproducibility tests including all six required exceptions, real IRQ0 and independent
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
image and resource ZIP; tested across different temporary output directories. No identical
code generation across compiler versions is promised. The build manifest records
tool versions and artifact hashes; it is not an OS runtime component.

QEMU has no display, VGA, network, or parallel port, uses a snapshot disk, and
has serial output separate from monitor stdio. Each run truncates old logs,
requires the exact legacy boot prefix, complete ordered Stage 2 diagnostics and
Stage 3 IRQ/timer markers (fatal CPU variants stop before the timer),
checks register/error/flag values and completion state, and sends monitor `quit` in `finally`, with
3-second normal shutdown then 2-second terminate/kill fallbacks. The owned PID is
always waited for; forced cleanup fails a successful-boot claim. Interrupting the
test normally also executes cleanup; forcibly killing the host runner cannot be
guaranteed to do so. `run.json` records PID, command, exit, and cleanup.

## Implementation status and tests

Implemented through Stage 3. `host/repository.py` owns the schema; `host/image.py` the
fixed-layout image build; `host/qemu.py` the execution harness and
`host/exception_output.py` and `host/timer_output.py` the captured-output validators.
`host/resources.py` checks the original icon and writes its fixed-metadata,
uncompressed ZIP package. Tests cover path
and metadata failures, actual compile/link failures, image bounds, output
reproducibility, blank disk timeout, wrong version output, stale-log rejection,
asset corruption, and actual masked-IRQ/missing-EOI failures.

The internal builder's keyword-only `test_vector` selects 0/1/3/6/13/14 and
defaults to breakpoint (3). `test_armed=False` is a negative integration fixture.
Only the test suite uses variant images; each triggers exactly one exception and
is retained under ignored `build/cpu-tests/` with logs. `cpu_self_test` in the
build manifest records the variant. The public build always uses the returning
breakpoint. `EXPECTED_OUTPUT` in the QEMU module is the Stage 1 prefix for
regression compatibility, not a complete Stage 3 success criterion.

Timer failure tests mutate temporary source copies, not the normal kernel or
tool flags: mask IRQ0, or omit master EOI. Logs persist in `build/timer-tests/`;
temporary sources/images are removed after assertions. Neither failure can
produce the expected three ticks and completion marker.

## Known limitations

No RynorLang compiler, incremental cache, package manager, filesystem builder,
or general-purpose image format. Python 3.10+ is declared; verification used
Python 3.14.3 on Windows only. Tested native tool versions and firmware are pinned
in the report; missing tools fail instead of skipping boot checks.
