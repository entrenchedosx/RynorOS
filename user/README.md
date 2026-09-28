# Native environment

## Purpose

Reserve `shell/` for the native shell, `lib/` for shared native APIs/runtime
bindings, and `apps/` for RynorOS applications.

## Public interfaces

`lib/rt/` (Stage 18c base, extended through P1): process/exit/write/print,
bounded arena alloc/free with read-only evidence, nap/flag yields, fd
read/write, filesystem calls (`fcreate`/`fread`/`fstat`/`fwrite`/`readdir`/
`unlink`), and pipe calls (`pipe_read_once`/`pipe_write_all`/`spawn_pipe`).
`shell/` holds the native CPL3 shell (REPL, scripts, pipelines, resident
evaluator). Shell commands are real CPL3 programs, not host API aliases.

## Invariants

Expose only real OS services. Clearly label trusted kernel-mode programs versus
protected user processes. Use `.rl` for RynorLang source.

## Implementation status

`lib/rt/` implemented and verified (freestanding C + one asm stub over the
frozen syscalls; CPL3 conformance programs). The native shell shipped in
Stage 18d; `apps/` remains reserved for future RynorOS applications.

## Tests

`lib/rt` conformance runs in QEMU CPL3 (`tests/integration/test_rt.py`);
host-side rebind pins live in `tests/repository/test_rtlib.py`.

## Known limitations

No heap growth (bounded arena only), no blocking calls, no argv. File reads
and writes exist via the P1 syscalls; the write sink for evidence remains
the serial-hex ABI where tests require it.
