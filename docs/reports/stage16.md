# Stage 16 report -- native .rl host programs

## Outcome

Stage 16 is a host-side Python 3.10+ program toolchain. It turns verified
`.rl` sources into real Linux x86-64 ELF executables (RIR -> NASM ->
object -> linked executable) with a minimal labeled host runtime for
startup/exit and exact-bytes `print`, and runs them. It adds no userspace,
no RynorOS syscalls, no heap, no argv, and no shell codegen. The default v1
grammar is unchanged for all pre-existing fixtures; `print` is the one
designed v1 addition (announced as "until Stage 16" since Stage 14).

## Implementation

- Analyzer (`tools/rynorlang/analyze.py`): `print(x: int|bool|str): unit`
  builtin (arity/type checked, `ExprStmt`-natural via shared unit rules);
  user `fn print` is `SEM_DUPLICATE` (reserved). The one intentional
  evolution: `tests/.../semantics/bad/unknown_fn.rl` now calls
  `frobnicate` (same `SEM_UNKNOWN_FUNCTION` coverage), and
  `test_19_unknown_fn` pins `print(1)`-as-value as `SEM_TYPE_MISMATCH`.
- RIR (`tools/rynorlang/rir.py`): `RT_HELPERS`
  (`rt_print_int/bool/str`, unit, one typed param); the builder maps
  `print` calls onto helpers (re-checking the operand type instead of
  trusting the frontend); the verifier accepts exactly the tabled helpers
  and refuses `rt_`-prefixed definitions; shell kinds still fail with
  `COMP_V2_UNSUPPORTED`.
- Oracle (`tools/rynorlang/interp.py`): opt-in `out=` capture list renders
  helpers exactly as native does (decimal / true-false / raw); return
  shape unchanged, default discards.
- Backend (`tools/rynorlang/compile.py`): emits `extern` for referenced
  helpers only (print-free modules assemble byte-identically to Stage 15a);
  CLI gains `--build DIR` and `--run` (stdout forwarded verbatim, exit
  code is the program's; diagnostics stay on stderr).
- Host runtime (`tools/rynorlang/runtime/rt_linux.asm`, HOST BOOTSTRAP,
  not a RynorOS ABI): `_start` (call `rl_4_main`, exit with `rax`), exact
  decimal/bool/raw-str writers over one 32-byte static buffer, single
  bounded syscalls (atomic, never truncated), caller-saved registers only.
  The 15a test harness is untouched.
- Pipeline (`tools/rynorlang/program.py`): capability probing (Windows
  NASM + WSL archlinux link/run, or POSIX direct), `build_program`
  (writes `prog.{asm,o}`, `rt_linux.o`, `prog`; basenames + fixed flags
  for byte-identical artifacts), `run_program` (`{exit, signal, stdout}`
  with sidecar capture so WSL markers never pollute bytes). Expected
  failures return `{code, message}` (`COMP_TOOLCHAIN_MISSING`,
  `COMP_ASSEMBLE_FAILED`, `COMP_LINK_FAILED`, plus pass-through
  `PAR_*/SEM_*/COMP_*`); never a traceback.

Design contract: `docs/design/rynorlang-program-model.md` (program model,
entry, low-8-bit exit with documented truncation, exact-bytes print,
linking, static-only memory, failure taxonomy, host-vs-future boundary).

## Fixtures and tests

Fixtures: 22 good + 2 trap under `tests/fixtures/rynorlang/programs/`
(hello/return/arithmetic/comparisons/boolean/ifs/loops/calls/fib/strings/
edges/wrap-around/traps) with exact `(exit, stdout)` goldens.

`tests/repository/test_rynorlang_programs.py` contains 42 test methods:
exact good/trap inventories; artifact shape (bits 64 / ELF / RIR header);
bad-source/entry/kind gates without raises; exit values incl. documented
300 -> 44 and -1 -> 255 truncation; trap signals (SIGILL div-zero, SIGTRAP
falloff); exact-stdout print goldens (incl. full INT64_MIN digits, empty,
100-char, dedup, no-newline proof); exit+stdout differentials vs the oracle
for every fixture; generated arithmetic/comparison/control/call matrices;
authenticity (41/42/43 exits, distinct outputs/bytes, fixture-branch
mutant); 5 targeted mutations (hardcoded exit, canned assembly, skipped
link, wrong entry symbol, biased digit helper); callee-preservation ABI
spot checks; integer matrix incl. wrap and `INT_MIN / -1` SIGFPE; string
matrix incl. content equality, single-storage dedup, and a no-heap proof
(no mmap/brk/sbrk/malloc in the runtime); CLI `--build`/`--run`/error
behavior without tracebacks; 3x byte-identical rir/asm/obj/exe with no
workdir leaks.

## Verification

```text
python -m pytest tests/repository/test_rynorlang_programs.py -q
python tools/build/build.py test
python tools/build/build.py check
```

Reference-host result: 44 program tests pass; full `test` gate passes
477/477 repository tests; `check` passes build, repository, and integration
gates. Inventory sum `REPOSITORY_TEST_INVENTORY` equals collected count
(475). Docs counts in `README.md`, `ARCHITECTURE.md`, and
`docs/design/shell.md` match; `test_docs_counts_current` derives the total.

## Forensic notes (found and repaired during implementation)

- NASM records its input path in the object FILE symbol, so executables
  differed across workdirs: the pipeline now invokes every tool with
  basenames (`cwd=workdir`, WSL `--cd`), proven byte-identical with zero
  path leakage.
- Missing `extern` for helpers failed assembly: the emitter now declares
  exactly the referenced `rt_*` symbols (print-free modules unchanged).
- WSL wait-status probes mix child stdout with marker lines: the runner
  redirects child stdout to a sidecar file read back verbatim.
- `trap/falloff.rl` first drafted with `if true` never fell off (oracle
  exit 1, not a trap): rewritten with `if false` (oracle `falloff`,
  native SIGTRAP).
- One equivalent mutant caught and replaced (a post-write store with no
  observable effect): digit-bias mutant instead.

## Limitations

- Host-native Linux ELF test programs only; no RynorOS ABI/userspace.
- No argv (deferred, no unsafe pointer API); no runtime-created strings;
  no heap/GC; print writes exact bytes with no newline.
- Exit statuses are low-8-bit by host contract (documented truncation).
- The kernel trusted-batch loader (bundle manifest, opcode-scanning
  loader, serial print, UNPROTECTED guest) is deferred with requirements
  preserved in `ROADMAP.md`, not deleted.
