# RynorLang native ABI v0

Status: **implemented for Stage 15a**. This document freezes the machine-level
value representation and calling convention used by the Stage 15a backend
(`tools/rynorlang/compile.py`) and the host execution harness. It covers the
frozen `{int,bool,str,unit}` universe only; later types arrive with their own
explicit ABI amendments. See `rynorlang-rir.md` (IR), `rynorlang-runtime.md`
(value model, execution ladder), and `ROADMAP.md` Stage 15a. Changing any rule
below requires an explicit later-stage design change.

## 1. Machine representation (freestanding x86-64, NASM `elf64`)

| Type | Size/Align | Register form | Memory form |
|---|---|---|---|
| `int` (signed i64) | 8/8, two's complement `-2^63..2^63-1` | full `r64` | `dq`, little-endian |
| `bool` | 1/1, unsigned `0`/`1` only | `r64` zero-extended (`RAX` is `0` or `1`, bits 63:1 are `0`); `AL` is the value | 8-byte home slot, only the low byte significant |
| `str` | 16/8 = `{ptr:u64, len:u64}`, `0 <= len <= 4096` | two slots: `ptr` then `len` | two consecutive 8-byte slots, pointer at the lower address |
| `unit` | 0/1, erased | no register, never read | no slot |

`bool` canonicity (`0`/`1`, high bits zero) is established by construction,
not by masking: the only producers are `const false/true`, comparisons
(`setcc` + `movzx`), and `&&`/`||`/`!` over canonical inputs (`and`/`or`/`xor`
preserve canonicity). The verifier rejects any `bool` vreg defined by another
operation. Masking (`and reg,1`) is deliberately absent: it would coerce `2`
to `0` and hide a backend bug, while a mismatch against the differential
oracle exposes it instead. No `int->bool` cast exists anywhere (no implicit
conversions, at any level).

## 2. Strings: `(ptr, len)` borrows

* Literals live in `.rodata` (one label per distinct byte-string,
  `_rlstr_<N>` in first-use order, `db` bytes; the empty string is a valid
  address with `len 0`, and callees must not dereference when `len == 0`).
* Literals are immutable and caller-borrowed: nobody frees them, and the
  callee must not write through `ptr` nor depend on any shorter lifetime.
  The backend emits no `free` and no reuse of literal storage.
* Compile-time cap: literals longer than 4096 bytes are rejected pre-emit
  (`COMP_STR_TOO_LONG`). Version 0 has no runtime string creation, so no
  runtime length check exists; future service results check `len <= 4096`
  and report `err`, never truncate or trap (see `rynorlang-runtime.md` Sec.1).
* Content comparison (`==`/`!=`) compares bytes, never pointers: equal
  contents compare equal even for distinct literals. The emitter uses one
  bounded `repe cmpsb` loop (`rcx <= 4096` from the length check); lengths
  compared first, so zero-length sides never dereference. This single
  instruction sequence is explicitly allowlisted in the assembler's
  independent post-check.

## 3. Calls (SysV subset)

A slot is 8 bytes. `int`/`bool` occupy one slot; `str` occupies two
consecutive slots **atomically** (if only one register slot remains, the
whole string moves to the stack -- never split across the boundary).

* First six slots travel in `RDI, RSI, RDX, RCX, R8, R9`, left to right.
* Slot 7+ travels on the stack: the caller emits `sub rsp, N`,
  stores at `[rsp+0]`, `[rsp+8]`, ..., calls, then `add rsp, N`, with
  `N = align16(8 * nstack)` so `rsp % 16 == 0` holds at every `call`.
* Returns: `int` in `RAX`; `bool` in `RAX` as `0`/`1` (upper bits zero);
  `str` in `RAX` (pointer) + `RDX` (length); `unit` leaves `RAX`/`RDX`
  undefined. A `unit` `main` is normalized to exit status 0 (emitter zeroes
  `EAX` on the bare-return path of `main` only).
* Caller cleans the argument stack. Caller-saved registers
  (`RAX, RCX, RDX, RSI, RDI, R8, R9, R10, R11`) are clobberable across calls;
  the emitter never uses callee-saved registers except the `RBP` frame.
  **Spill-everything:** every vreg has a home slot; registers are scratch
  within a single instruction and nothing lives in a register across a `call`.

## 4. Stack frames

```text
[rbp+16+8k]  stack argument k (k = 0 first) | [rbp+8] ret-addr | [rbp] saved-rbp
[rbp-8]      home slot 0                  | [rbp-16]    home slot 1 | ...
[rsp]        frame bottom (rsp % 16 == 0)
```

* Prologue: `push rbp; mov rbp, rsp; sub rsp, FRAME` (`sub` omitted when the
  frame is empty). Epilogue: `leave; ret`.
* `FRAME = align16(8 * frameslots)`; the verifier rejects `frameslots > 1024`
  (`FRAME > 8192` equivalent bound). Home slot `s` of vreg `%N` comes from
  the shared liveness allocator (`rir.assign_slots`); a `str` vreg's length
  half is always the immediately following slot.
* Alignment proof: the caller guarantees `rsp % 16 == 0` at `call`; on entry
  `rsp % 16 == 8`; `push rbp` restores `0`; `FRAME % 16 == 0` preserves it;
  every nested call's `N % 16 == 0` preserves it again.
* **No red zone:** `[rsp-128, rsp)` is never touched; all locals address via
  `[rbp-x]`. No SSE/XMM/x87, no PIC/PIE, no stack protector, no unwind
  tables, no varargs. Emission header is `bits 64; default rel` plus
  `section .note.GNU-stack noalloc noexec`; links use `--build-id=none`.

## 5. Traps (never silent)

* Division/modulo by zero: `test rcx, rcx; jz <trap>; cqo; idiv rcx` with the
  trap body `ud2` (each site gets unique labels; fall-through jumps over it).
  The host harness observes `SIGILL`.
* `INT_MIN / -1` (and `%`): deliberately **not** pre-checked, matching
  Clang/GCC practice -- the hardware `#DE` from `idiv` is the trap (observed
  as `SIGFPE`). The kernel already diagnoses `#DE` (Stage 2), so the L1 story
  stays uniform: the loader never has to distinguish the two, and the oracle
  reports both under the single `div0` category.
* Fall-off of a non-`unit` function without a terminal `return` (legal per
  the frozen "no all-paths check" rule): the emitter ends the body with
  `int3`. The host harness observes `SIGTRAP`, distinct from `SIGILL`, so
  test failures attribute the right cause. Nothing wraps, nothing returns
  zero, nothing falls into adjacent code.
* A trap is never a value: all three abort the process/halt the guest, so a
  trapped computation can never be consumed as data (extends the staged abort
  semantics in `rynorlang-runtime.md` Sec.4).

## 6. Absence proof (independent post-check)

`compile.check_asm()` re-scans every emitted text for indirect calls/jumps
(`call`/`jmp` to a register or memory operand), privileged and port
instructions (`syscall`, `iret`, `cli`, `sti`, `hlt`, `in`, `out`), string
instructions other than the single allowlisted `repe cmpsb`, and any SSE/AVX
register or unwind/`eh_frame` residue. Direct `call rl_*` / `jmp` / `jcc` to
local labels are the only transfers; the test suite asserts the check is
clean on the whole fixture corpus and flags injected abuse line by line. The
same allowlist is what lets a future L1 loader opcode-scan emitted code.

## 7. Entry and exit (host harness)

Emitted code defines length-prefixed `rl_<decimal-length>_<name>` globals
(for example `rl_4_main`). The prefix and length prevent a source function
from colliding with a generated block or trap suffix. It never defines
`_start` and never emits `syscall`.
The reviewed harness (`tools/rynorlang/harness_start.asm`) provides `_start`
only: `call rl_4_main; mov rdi, rax; mov rax, 60; syscall`. Linux exit codes
are 8 bits, so native exit observations are meaningful only below 128 (the
runner reports 128+N fatalities as signals); value fixtures therefore stay in
`0..127` and full-width arithmetic is proven through boolean ballots. Unit
`main` exits `0`.
