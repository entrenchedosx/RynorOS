# RynorLang native program model (Stage 16)

Status: **implemented for Stage 16 (host-native only)**. This document
defines what a `.rl` program is, how it enters execution, what it can
observe (exit status, stdout), how it is linked, and where the host
bootstrap ends and the future RynorOS ABI begins. See `rynorlang-rir.md`
(IR), `rynorlang-abi.md` (calling convention), `rynorlang-runtime.md`
(value model and execution ladder), and `ROADMAP.md` Stage 16.

## 1. What a native program is (and is not)

A Stage 16 program is a **host-native Linux x86-64 ELF executable** built
from one `.rl` source file for testing. It is NOT a RynorOS userspace
program: no RynorOS syscall interface exists yet (Stage 18a), so startup,
output, and exit go through Linux syscalls in the labeled host runtime
(`tools/rynorlang/runtime/rt_linux.asm`). Nothing here ships in any RynorOS
image. The honest name is always "host-native RynorLang program".

```
program.rl
  -> RIR (build_rir + verify_module)
  -> program.asm (rynorlangc NASM backend, freestanding subset)
  -> program.o (nasm -f elf64)
  -> program (ld.lld --build-id=none, + rt_linux.o)
```

Deterministic: same source yields byte-identical RIR, assembly, objects,
and executables across workdirs (no timestamps, no absolute paths, no
random symbols; NASM is invoked with basenames so its FILE symbol is
constant). `tools/rynorlang/program.py` owns the pipeline.

## 2. Entry point

Exactly one entry: `fn main(): int` or `fn main(): unit`, no parameters
(`COMP_NO_ENTRY` otherwise). No new syntax. Program arguments
(argc/argv) are **explicitly deferred**: there is no argv representation in
the `{int,bool,str,unit}` type system yet, and no unsafe string-pointer API
is introduced to fake one. `fn main(args ...)` of any shape is rejected.

## 3. Exit status

`fn main(): int` exits with the low 8 bits of its return value
(`status = value & 0xFF`, the host process contract). This is documented
truncation, not a language change: values 0..127 are meaningful directly;
`return 300` exits 44 and `return 0 - 1` exits 255 (both pinned by tests).
Full-width integers remain provable through `print` (exact decimal bytes).
`fn main()` (unit) exits 0: the emitter zeroes `EAX` on the bare-return
path of `main` only (per `rynorlang-abi.md`).

## 4. Output: print

`print(x: int|bool|str): unit` is a compiler-known builtin (a user function
named `print` is `SEM_DUPLICATE`, reserved). Exactly one argument; any other
arity is `SEM_ARITY_MISMATCH`; non int/bool/str is `SEM_TYPE_MISMATCH`; the
unit result makes it `ExprStmt`-natural through the shared unit rules
(using it as a value is the ordinary unit-misuse diagnostic).

Lowering: the analyzer emits `Call{callee: "print"}`; the RIR builder maps
it to `call rt_print_int|bool|str` (checked against the `RT_HELPERS` table,
never trusted blindly). The verifier accepts only the three tabled helpers;
user functions may not use the `rt_` prefix (builder and verifier both
refuse it). The emitter references helpers as `extern` only when a print
exists, so non-print modules assemble byte-identically to Stage 15a.

Semantics (exact bytes, no implicit newline):
- int: signed decimal (`-9223372036854775808` prints in full).
- bool: `true` / `false`.
- str: raw `(ptr, len)` bytes, `0 <= len <= 4096`; empty prints nothing.
- Writes are single syscalls of at most 4096 bytes, hence atomic on pipes
  (never partial, never truncated).

## 5. Linking

- Assembler: `nasm -f elf64` (program object + runtime object).
- Linker: `ld.lld --build-id=none -o prog prog.o rt_linux.o`.
- Entry symbol: `_start`, provided by the runtime object only. Emitted
  code defines `rl_<len>_<name>` globals and never defines `_start` or
  emits `syscall` (checked by `check_asm`; the runtime object is the only
  syscall site, which is the documented bootstrap boundary).
- Library dependencies: none (freestanding; no libc, no dynamic linker).

## 6. Strings and memory

Stage 15a's contract carries over unchanged: borrowed `(ptr, len)`,
immutable literals in `.rodata`, deduplicated, bounded at 4096 bytes, no
dangling pointers (every pointer originates from a literal whose lifetime
is the whole process). Runtime-created strings remain **unsupported** (the
helpers render directly; nothing is allocated). Memory in use: static
`.rodata`/`.bss` (one 32-byte conversion buffer) plus compiler-known stack
frames. No heap, no allocator, no GC; the runtime source contains no
`mmap`/`brk`/`sbrk`/`malloc` (pinned by test).

## 7. Failure behavior (all deterministic, classified, non-crashing)

- Bad source: `PAR_*`/`SEM_*`/`COMP_*` diagnostics with file:line:col
  spans through every entry point (library and CLI); never a traceback.
- Missing entry / bad signature: `COMP_NO_ENTRY`.
- Shell-edition kinds: `COMP_V2_UNSUPPORTED` (no shell codegen yet).
- Missing toolchain: `COMP_TOOLCHAIN_MISSING` (capability-gated skips in
  tests with the reason pinned).
- Assembler/linker failures: `COMP_ASSEMBLE_FAILED` / `COMP_LINK_FAILED`.
- Runtime traps: div-zero `ud2` -> SIGILL; `INT_MIN / -1` hardware `#DE` ->
  SIGFPE; non-`unit` fall-off `int3` -> SIGTRAP. Traps are never values.

## 8. Tooling

`rynorlangc` (`tools/rynorlang/compile.py`) keeps `--rir`/`--asm` and gains:
- `--build DIR`: full pipeline into `DIR/prog.{asm,o,exe}` (+ `rt_linux.o`).
- `--run`: build in a temp dir, forward the program's stdout verbatim;
  the CLI exit code is the program's (compiler diagnostics stay on stderr
  with codes 1/2; the library API `build_program`/`run_program` returns
  `(value, None)` / `(None, {code, message})` with no such collision).
- `--edition` is unchanged (v1 default); `print` is a core feature in both
  editions.

## 9. Boundary to the future

```
Stage 16 host-native (this document: Linux ELF test programs)
  -> future RynorOS ABI (syscalls, loader, bundle validation)
  -> 18a userspace foundation, 18b loader+syscalls, 18c runtime,
     18d shell+REPL, 19a values, 19b-19d language, 19e self-hosting
     compiler, 20a-20b tools/self-host
  -> 20c graphics, 20d networking, 20e devices/audio
  -> 21 Windows compatibility
```

The kernel trusted-batch loader from the earlier Stage 16 sketch (boot
bundle manifest, opcode-scanning loader, serial-bound `print`, UNPROTECTED
guest execution) is **explicitly deferred**, not deleted: its requirements
(bundle `< 64 KiB` manifest validation, direct-call/static-jump-only scans,
known-slot memory checks, IRQ0-preemptible guest contract) are preserved in
`ROADMAP.md` as the follow-on native-execution milestone. Stage 16 proves
the language-to-executable chain on the host first, so that later loader
work inherits a verified compiler instead of co-designing one.
