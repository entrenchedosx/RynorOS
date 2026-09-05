# Stage 15a report -- RynorLang typed IR and native backend (static core)

## Outcome

Stage 15a is a host-side Python 3.10+ compiler bootstrap. It lowers the
frozen Stage-14 stable AST to a typed three-address CFG (RIR), verifies it
with real CFG dominance, assigns spill-everything home slots with one shared
liveness allocator, and emits freestanding x86-64 NASM for a written SysV-subset
ABI. It changes no boot or kernel source. Execution exists only as
disclosed host harness runs plus a differential test oracle; no interpreter
ships in any image.

## Implementation

- `tools/rynorlang/rir.py` provides `build_rir`, `verify_module`, `dumps`,
  and `assign_slots` (`RIR_VERSION=1`, `MAX_FRAMESLOTS=1024`,
  `MAX_STR_LEN=4096`). The builder is iterative over expressions, tracks one
  lexical scope per `Block`, and fills `frameslots` from the shared allocator.
  The verifier checks envelope, dense `bb0..bbN-1` ids, one terminator per
  block, real dominance for every vreg use, single static definition,
  terminator/return agreement, `bool` branch conditions, exact `frameslots`
  recomputation, and the 1024-slot bound. Reserved `value` types, reserved
  opcodes, `rsv_*` ops, and non-v1 AST kinds are rejected.
- `tools/rynorlang/compile.py` (`rynorlangc`) provides `emit_asm`,
  `compile_source`, `check_asm`, and `main` (`--rir`/`--asm`, exit 0/1/2).
  The emitter reads verified RIR only, never the AST. Values follow
  `docs/design/rynorlang-abi.md`: `int` as i64, `bool` as zero-extended u8
  0/1, `str` as `(ptr,len)` in two atomic slots, `unit` erased. Calls use
  `RDI,RSI,RDX,RCX,R8,R9` plus `align16` stack slots; frames are
  `align16(8*frameslots)` with no red zone, no SIMD, no `syscall`, direct
  `rl_*` calls and static jumps only. Traps are `ud2` (div-zero),
  hardware `#DE` (`INT_MIN/-1`, observed as `SIGFPE`), and `int3` (fall-off).
  `check_asm` re-scans the text for indirect transfers, privileged/port
  instructions, non-allowlisted string ops, and SSE/AVX residue.
- `tools/rynorlang/interp.py` is a TEST-ONLY oracle (`run_rir`, step and call
  budgets, `OracleRefused` on unsupported input). Honesty rules: separate code
  from the emitter, same first-error order, mismatch means backend bug, oracle
  alone never closes a stage.
- `tools/rynorlang/harness_start.asm` provides `_start` only
  (`call rl_4_main; mov rdi, rax; mov rax, 60; syscall`). Function symbols
  are length-prefixed so source names cannot collide with generated labels.

Design contracts: `docs/design/rynorlang-rir.md` (IR, CFG, slots, lowering,
goldens, diagnostics) and `docs/design/rynorlang-abi.md` (representation,
calls, frames, traps, absence proof, harness). Value model and ladder remain
in `docs/design/rynorlang-runtime.md` (L0 implemented; L1/L2 still design).

## Fixtures and tests

Fixtures: 17 good plus 3 trap programs under
`tests/fixtures/rynorlang/compiler/good/` and `trap/` (`main42`, `fib10`,
`sevenargs`, `stackmix`, `strboundary`, `strreturn`, `boolops`, `strcmpeq`,
`unitmain`, `wrapballot`, arithmetic/loop/nested shapes; traps `divzero`,
`falloff`, `intmin`).

`tests/repository/test_rynorlang_rir.py` contains 48 test methods: golden RIR
text for the corpus, verifier unit negatives (envelope, strtab, terminators,
redefinition, mistyped binop, arity, reserved opcode/type, frameslots exact),
a live slot-overlap soundness probe over fixtures plus deep/loop/str stress
shapes, a branch-dominance test proving a one-branch definition cannot
authorize a join use, a same-block forward-use test locking two-pass
"before its definition" diagnostics, a CFG-liveness backedge test, malformed
diagnostics without exceptions, and mutation checks proving builder operand
checks exist.

`tests/repository/test_rynorlang_compiler.py` contains 39 test methods:
golden ASM presence, determinism (3x byte-identical), negative pre-emit
rejections, `check_asm` clean on the corpus plus per-line abuse flagging,
native differential runs (compiled exit/signal equals oracle prediction;
`fib10` exits 55), and a 21-mutation matrix where each mutant is DETECTED
(opcode flips, alignment breaks, return-register breaks, arg-order swaps,
hardcoded returns, canned assembly, ignored RIR, branch swaps, string-length
and atomic-offset breaks, callee-saved scratch, unsigned-comparison flips).

Deep stress: a 5000-term operator chain (10001 temporaries) fits in 3 home
slots. Slot-overlap checker reports ALL SOUND.

## Verification

Targeted Stage 15a commands (reference host: Python 3.14, NASM 3.02,
Clang/LLD 23.1.0, QEMU 11.1.0, `RYNOR_CLANG`/`RYNOR_LLD` set, NASM/QEMU on
PATH):

```text
python -m pytest tests/repository/test_rynorlang_rir.py tests/repository/test_rynorlang_compiler.py -q
python tools/build/build.py test
python tools/build/build.py check
```

Reference-host result: 48 + 39 Stage 15a tests pass (87 methods, 164
subtests); full `test` gate passes 386/386 repository tests; `check` passes
build, repository, and integration gates. Inventory sum
`REPOSITORY_TEST_INVENTORY` equals collected count (386). Docs counts in
`README.md`, `ARCHITECTURE.md`, and `docs/design/shell.md` match the
inventory; `test_docs_counts_current` derives the total instead of
hard-coding it.

## Limitations

- Static `{int,bool,str,unit}` only; `value` reserved-but-rejected until
  Stage 19a. No records, lists, status, match, modules, pipelines, commands,
  member access, strings beyond `==`/`!=`/copy, or runtime string creation.
- No optimizer or register allocator (spill-everything homes); no GC,
  aliasing, exceptions, `break`/`continue`, indirect calls, or `syscall`
  emission. Native exits are meaningful below 128 only (Linux 8-bit codes;
  signals surface as 128+N); full-width arithmetic is proven via ballots.
- Host-only L0: kernel, loader, bundle, userspace, REPL, and self-hosting
  remain later stages. QEMU kernel tests are regression-only for this stage.
