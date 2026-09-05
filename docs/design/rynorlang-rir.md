# RynorLang IR (RIR) v1

Status: **implemented for Stage 15a**. This document specifies the typed
intermediate representation between the frozen Stage-14 stable AST and native
code generation. RIR is the compiler's only honest input: the backend never
reads the AST directly. See `rynorlang-ast.md` (frozen source contract),
`rynorlang-abi.md` (machine-level value and calling rules),
`rynorlang-runtime.md` (value model and execution ladder), and `ROADMAP.md`
Stage 15a. Implementation: `tools/rynorlang/rir.py` (build, verify,
serialize). Changing any rule below requires an explicit later-stage design
change.

## 1. Shape

A module is a dictionary with exactly these fields:

* `rir_version`: must equal `1`. Any other major version fails verification
  (`envelope: rir_version must be 1`); this is how Stage 15 pins its input
  contract and how later editions are refused instead of miscompiled.
* `source`: originating filename (string, informational only).
* `strtab`: deduplicated string table, first-use order. Each entry
  `{id, len, bytes}` with dense `id` values `0..n-1`, `bytes` ASCII,
  `len == len(bytes)`, `0 <= len <= 4096`, no duplicate byte strings.
* `funcs`: function list in source order.

A function has `name` (unique, non-empty string), `symbol` (deterministic
global index, informational), `params` (`[{name, symbol, type}]`),
`ret` (`"int"`, `"bool"`, `"str"`, or `null` for `unit`), `blocks`
(non-empty list), and `frameslots` (exact recomputed home-slot count, see Sec.5).

A block has `id` (`bb0..bbN-1`, dense, in layout order), `instrs` (list), and
`term` (exactly one terminator object, never an instruction).

## 2. Instructions and terminators

Operands are always virtual registers (`%N`); callees are always names
(direct calls only -- there are no function pointers, no indirect calls).

| Form | Meaning | Type rule |
|---|---|---|
| `%d = const int <decimal>` | 64-bit magnitude literal, `0..2^63-1` | `int` |
| `%d = const bool true\|false` | canonical boolean | `bool` |
| `%d = const str #<id>` | `strtab` reference `[0,n)` | `str` |
| `%d = copy <type> %s` | same-type rebinding (params, lets) | operand type preserved |
| `%d = binop <type> <op> %l %r` | `+ - * / %` on `int`; `== !=` on same `T in {int,bool,str}`; `< > <= >=` on `int`; `&& \|\|` on `bool` | result per frozen table |
| `%d = unop <type> <op> %v` | `-` on `int`; `!` on `bool` | result per frozen table |
| `%d = call <type> <name>(%a, ...)` | checked call, result kept | arity + arg types match callee signature |
| `call <name>(%a, ...)` | checked call, result discarded | callee returns `unit` |
| `jmp <bb>` | unconditional transfer | target exists |
| `br %c <bbT> <bbF>` | conditional transfer | `%c: bool` |
| `ret %v` \| `ret` | return value / bare return | value type matches signature (`unit` iff bare) |
| `unreachable` | fall-off trap marker | no fields |

Terms appear only as a block's `term`, never inside `instrs`. No other
opcodes exist; the reserved names `make_record/get_field/set_field`,
`make_list/list_idx/list_len/list_push`, `make_status/match_br`,
`spawn_pipe/exec_cmd/open_handle`, and any `rsv_*` opcode fail verification,
as does the reserved `value` type and any AST kind beyond the frozen 16
(`Pipeline`, `Cmd`, `Member`, ... fail the build with `COMP_V2_UNSUPPORTED`).

## 3. CFG invariants (all verifier-checked, fixed order)

1. Exactly one terminator per block, in last position; no terminator elsewhere.
2. Block ids dense `bb0..bbN-1`, layout order = array order; `bb0` is entry.
3. No dangling `tgt/then/else` references.
4. No undefined vreg use; every use is dominated by its definition (real
   dominance over the CFG; same-block uses follow their definition).
5. Single static definition per vreg; params pre-defined as `%0..%k-1`.
6. Terminator/return-type agreement (`ret v` type matches signature).
7. `br` conditions are `bool`; `jmp` carries no arguments (no phis -- sound
   because the language has no assignment and no shadowing, so every name
   resolves to one dominating definition and loop-carried state is impossible).
8. `frameslots` equals the shared allocator's recomputation exactly.
9. Dead tails (blocks ending `unreachable`) are allowed anywhere; they still
   verify fully.

## 4. Home-slot assignment (shared, single implementation)

`assign_slots(blocks, vreg_types, nparams)` in `rir.py` is the one and only
slot allocator. It computes block live-in/live-out sets to a fixed point over
the actual CFG, builds definition-vs-live interference (plus conservative
layout intervals), and greedily places each definition in the lowest
non-interfering slot (`str` takes two consecutive slots). Thus backedges and
non-layout edges cannot clobber a still-live home. The builder calls it to fill
`frameslots`; the verifier calls it to check `frameslots`; the emitter calls
it to locate homes. The three can never disagree. A 5000-term operator chain
(10001 temporaries) fits in 3 slots; frames beyond 1024 slots (8 KiB, the
L1 kernel-stack-shaped bound) are rejected. Parameter slots alone are checked
against the cap before graph construction.

## 5. Lowering map (16 frozen kinds -> RIR)

`Program -> Module` (source order, `strtab` built first-use); unknown kinds ->
`COMP_V2_UNSUPPORTED`. `Function -> Func` (params `%0..%k-1` + named `copy`,
body in `bb0`, `unreachable` appended to open non-`unit` tails, bare `ret` to
open `unit` tails). `Param` -> pre-defined vreg (no instruction). `Block` ->
inline emission into the current block, pushing one lexical scope whose
declarations die with it (a use outside its block fails closed even though
symbols are globally unique). `Let` -> initializer value bound to the declared
symbol (type re-checked). `Var` -> operand reference (no instruction).
`IntLit`/`BoolLit`/`StrLit` -> `const` (strings interned). `BinOp`/`UnOp` ->
single instruction after left-to-right operand lowering. `Call` -> argument
lowering left-to-right, then `call` (arity and arg types re-checked against
the callee signature collected in a first pass). `If` -> `br` + then/else/join
blocks (`else if` chains nest without an extra join; missing `else` jumps
straight to join). `While` -> header/body/exit with the condition living only
in the header (recomputed per iteration; never duplicated in the pre-header).
`Return` -> `ret`; statements after it lower into a fresh dead block.
`ExprStmt` -> value lowered and discarded (`unit` calls emit bare `call`).

Expression lowering is iterative (explicit work stack, right-pushed-first for
left-to-right order), so arbitrarily deep left-associated chains never touch
the Python recursion limit. Control nesting is bounded by the analyzer's own
depth limit and lowers with plain recursion.

## 6. Serialization (canonical golden format)

```
; rir_version=1 source="main.rl" funcs=1
.strtab #0 len=2 bytes="hi"
.function main : () -> int ; symbol=1 frameslots=1
.block bb0
  %0 = const int 42
  ret %0
```

Rules: header comment first; `.strtab` lines in id order (`\"`, `\\`, `\n`,
`\t`, `\xNN` escapes); one `.function` line per function
(`name : (params) -> ret|unit ; symbol=N frameslots=M`); one `.block` line per
block; two-space instruction indent. Byte-deterministic by construction
(lists only, no sets/dicts iterated, monotonic counters).

## 7. Diagnostics

Builder failures (never raise, always `(None, {"code","message"})`):
`COMP_V2_UNSUPPORTED` (unknown kind/operator/reserved type), `COMP_STR_TOO_LONG`
(`> 4096` bytes), `COMP_BAD_AST` (malformed shape, unknown symbol, type
inconsistency, duplicate definition), `COMP_FRAME_TOO_BIG` (`> 1024` slots).
Emitter-level: `COMP_NO_ENTRY` (no `fn main()` with `()->int`/`()->unit`),
`COMP_EMIT_FAILED`, `COMP_BAD_RIR` (verifier output). CLI exit codes mirror
the analyzer: `0` payload, `1` diagnostic, `2` usage.

## 8. Extension points (all rejected in v1)

Type `"value"`; opcodes listed in Sec.2; AST kinds listed in Sec.2; `rsv_*` block
prefixes; `rt_*` intrinsic names (arena/handle calls land here later). Each
has a dedicated verifier rejection and a mutation test proving it fires.

## 9. What RIR deliberately omits

Optimizer, register allocator (spill-everything homes instead), GC/aliasing
(static sizes + wholesale arenas suffice), exceptions/`break`/`continue`
(lowerable later via `jmp`), phi/normalization passes (unsound cost without
assignment), dynamic dispatch/indirect calls/`syscall` emission (the L1 loader
must opcode-scan static targets), string operations beyond `==`/`!=`+copy
(bounds and concatenation are runtime-library calls, Stage 16), modules,
pipelines, records, lists, match.
