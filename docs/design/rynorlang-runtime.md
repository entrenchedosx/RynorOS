# RynorLang runtime model

Status: **L0 implemented for Stage 15a; 15b host shell surface implemented
(analysis only, no execution); L1/L2 remain design.** This document defines the
value model, the runtime-library boundary, the execution ladder, and the staged
error model that carry RynorLang from its frozen static core (Stages 12-14)
toward a native shell and scripting language. It creates no syntax and changes
no frozen contract. See `rynorlang-ast.md` (frozen core), `rynorlang-shell-language.md`
(shell surface), and `ROADMAP.md` (staging).

## 1. Value model

RynorLang stays **fully statically typed; there is no `any`, no `dynamic`, and
no gradual typing**, at any stage. Heterogeneity is expressed with explicit,
closed, statically-checked unions -- never with erased types. Rationale: the
Stage 15 backend is a single-pass freestanding x86-64 emitter over a bounded
heap with no GC; the 63-test mutation gate and byte-identical JSON discipline
depend on every expression having one static type; the self-hosted compiler
(Stage 19e, ~1.5 kLOC budget) must remain writable in the frozen subset. A
dynamic fallback would move today's host-caught `bad/` fixture failures into
unobservable QEMU faults.

The value universe grows only by staged, frozen additions:

| Stage | Values | Representation (no GC) |
|---|---|---|
| 14 (frozen) | `int` (i64), `bool`, `str` (ASCII, bounded), `unit` (calls only, `ExprStmt` only) | registers/stack slots; `str` = `(ptr,len)` borrow; literals in `.rodata`, nobody frees |
| 15-16 | same set; `str` gains runtime bounds (`<= 4 KiB`, checked) | unchanged + explicit `rt_alloc` arena for service results, freed wholesale per call |
| 18b | `handle` (opaque syscall capability: file, process) | `u64` index into a kernel-owned table; never dereferenced by language code |
| 19a | `record` (nominal static shape), `list<T,N>` (fixed-cap array + len), `status` (`ok(T)`/`err(Error)`), `map<K,V,N>` | static sizes/offsets known at analyze time; inline or per-function/per-stage arenas freed wholesale on `return`/stage-end; copy-on-assign, no aliasing, no cycles, no inheritance, no null |

`Object` = a `record` instance. `Property` = a statically declared typed field.
`Collection` = `list<T,N>` (later `map<K,V,N>`), always with static element
types and capacities. Equality stays same-`T`-only (extended per new type);
ordering stays `int`-only; `&&`/`!`/`-` unchanged. There are no implicit
conversions at any stage.

Caps are part of the type: `str <= 4 KiB`, `list` length `N` is a constant,
arenas have fixed caps. Exceeding a cap is a compile-time `SEM_*` error when
statically known, otherwise a runtime `err(Error)` value -- never a trap, never
silent truncation.

## 2. Language vs runtime library vs OS

The split is strict and permanent:

* **Language** (frozen keywords `fn/let/if/else/while/return/true/false/int/bool/str`
  only): pure computation over typed values. No I/O, no syscalls, no intrinsics,
  no inline assembly, no syscall expressions. No OS feature ever becomes a
  keyword or operator. `print`/`use`/`open` stay plain identifiers
  (`SEM_UNKNOWN_FUNCTION` until their declaring module exists).
* **Runtime library** (native code first, `.rl` later): owns buffers,
  formatting, and all effects. Every effect is a typed function call resolved
  through modules (Stage 19a `use "std/io";`). Example -- `digest("ab")`
  end to end: keystrokes fill the userspace line buffer -> the REPL block
  `write_str(digest_hex("ab"));` (already compiled) calls
  `rt_digest(ptr, len, out8)` (validates `len`, stack 8-byte out) ->
  `syscall(SYS_DIGEST, ptr, len, out)` with `U/S` + length validation ->
  kernel FNV-1a -> runtime hex-formats to a 16-char `str` (session-arena
  allocated) -> `rt_print` -> serial/VGA. The language never sees a pointer,
  a syscall number, or a capability.
* **OS** (kernel): isolation, scheduling, memory, devices. Adding a service =
  a new syscall number plus a thin library wrapper; zero grammar change.

## 3. Execution ladder (trust labels)

* **L0 -- host-only, untrusted for the kernel.** `tools/rynorlang/` plus the
  Stage 15 backend compile `.rl` -> native deterministically on the host.
  A tree-walking evaluator may exist ONLY as a differential test oracle with
  honesty rules (separate code from the emitter, same first-error order,
  mismatch = backend bug, oracle alone never closes a stage). No interpreter
  ships in any image.
* **L1 -- kernel-mode, trusted, UNPROTECTED (Stage 16, batch only).** Only
  build-time boot-bundle programs compiled by L0, embedded read-only: a
  magic+version+count manifest with per-entry `{name[32], len, fnv1a}` and
  bounded payloads (whole bundle `< 64 KiB`). The loader validates
  magic/count/overflow/checksum and halts (never executes) on any failure. No
  interactive evaluation, no paste-to-execute, no ring-0 REPL. Safety is
  specified, not assumed: emitted L1 code is verifier-checked (direct calls
  and static jumps only -- no computed/indirect branches, no `syscall`
  instruction, loader opcode-scans and refuses; all accesses to
  compiler-known slots with explicit string length checks; statically bounded
  frames), cap exhaustion halts with a diagnostic, and guest code runs under
  the normal preemptible contract (bounded, non-blocking, no locks across
   service calls). Faults halt. Banner labels the mode `UNPROTECTED`. Deleted
   or kernel-gated once 18b–18d land (loader, runtime, and shell in userspace).
* **L2 -- userspace, untrusted (Stage 18a+).** `CPL3`, per-process address
  spaces, validated buffers, clean exit. Only here do the interactive REPL,
  file-backed scripts, and true streaming pipelines exist. The existing
  `kernel/shell/shell.c` monitor stays a frozen trusted monitor and MUST NOT
  gain evaluation.

## 4. Error model (staged)

* **Compile time, all stages:** single first `SEM_*` diagnostic, no partial
  tree, no recovery -- kept through self-hosting (keeps the mutation gate
  small, outputs deterministic, and the self-hosted compiler writable).
* **Runtime, Stages 15-18a:** abort semantics. A failing pipeline stage stops
  the pipeline; downstream never runs; outputs already produced are unchanged
  (extends the current `krst_call` transactional rule). One `error:` line plus
  an out-of-band status code; error text is never consumable as data.
* **Runtime, Stage 19a+:** `status`/`Result<T,E>` becomes the first composite
  type (`Error{code:int, message:str(fixed-cap), span?, trace:int[8]}`), threaded
  explicitly through `match`/`if`. The shell/syscall boundary stays abort-style.
* **Mapping (frozen):** `KRST_*` codes propagate verbatim (never remapped to
  0); `SHELL_UNKNOWN/ARGS/TOO_MANY/INVALID` become caller bugs reported at the
  call-site span; `SHELL_SERVICE` as a squash-code disappears, replaced by
  transparent `Error` propagation. `SHELL_OK = 0` stays the sole success.
* Multi-error recovery, if ever, arrives post-20 as IDE-only batch mode only.

## 5. Memory discipline (no GC)

Static shapes give static sizes. `int`/`bool`/`unit` live in registers and
stack slots. `str`/`list`/`record` live inline or in scoped arenas: one arena
per function activation plus one per pipeline stage plus (REPL only) one
session arena for persisted `let`/`fn` values. Arenas free wholesale; no heap
pointer survives its arena; a `heap_check`-style walk asserts zero leak per
prompt and per stage. No reference counting until Stage 18+ at the earliest,
and only if measurements demand it.

## 6. Testing requirements (binding on later stages)

* Value semantics: good/bad fixtures per new type rule; empty/max-str/max-list;
  arena-exhaustion -> `err`, never trap; mutation removes each new check.
* Boundary: every cap (string length, list `N`, arena size, module count,
  bundle size) has an exact-accept/exact-reject pair.
* Runtime: host-recomputed digests/folds (Stage 10 precedent), balanced
  `pmm/vm/heap` accounting around every guest run, canned-output and
  dispatch-bypass mutants, PIT-preemption proof.
* Determinism: 3x byte-identical outputs on host and guest.

## 7. Open questions

Max `str`/arena sizes; `map` key-type set; `handle` close/exhaustion semantics;
`status` propagation syntax (`?` operator vs explicit `match` -- decide in the
shell RFC, not here); REPL continue-vs-error heuristic for partial blocks.
