# RynorOS native runtime library (Stage 18c, CPL3)

## Purpose

Freestanding C library in `user/lib/rt/` over the frozen 18b syscalls.
No new syscall numbers, no blocking, no host OS APIs: every conformance
program executes in QEMU CPL3 through the 18b loader, and the host only
compiles/links RYNX envelopes plus pins expected serial bytes.
Non-goals: blocking/read/open/wall-clock/cross-context sync/growth/
threads/heap reuse. Depends on the frozen 18b loader, syscall ABI
(`docs/design/syscall-abi.md`), and executable format
(`docs/design/executable-format.md`).

The boot transcript gains one disclosed additive section: after the
loader terminator the kernel runs `rt_self_test()` (`kernel/core/main.c`,
`kernel/core/rt-test.c`), which always ends the transcript with either
`[RT] rt verified` (plus `[SYSTEM] ... stage18c runtime library` and
`[TEST] rt self-test passed`) or the single line `[RT] no image, skipped`.
All pre-18c rows are byte-identical; validators split the trailing `[RT]`
run first (`tools/host/rt_output.py`, `tools/host/boot_output.py`).

## Public interfaces

Frozen surface: 16 functions (13 functional + 2 read-only evidence
channels `rt_live_count`/`rt_ptr_off` + the Stage 18d Slice A
`rt_fd_read`; the 18c `rt_open`/`rt_read` stubs keep their names and
`RT_NOSYS` behavior) plus frozen `rt_err`
(`RT_OK 0`, `RT_INVAL 1`, `RT_RANGE 2`, `RT_NOSYS 3`, `RT_AGAIN 4`,
`RT_NOMEM 5`). The library contains no intentional trap/panic path
(no `UD2`/`DIV`-by-zero/signed-overflow; all arithmetic unsigned);
wild caller pointers still fault as `USER_FAULTED`, which is a caller
bug. Fatal conformance mismatches exit via `rt_exit` with `64 + class`
(1=fmt -> 65, 2=alloc -> 66, 3=write -> 67, 4=nap -> 68, 5=wait -> 69,
6=nosys -> 70; 0=success). Caller obligations: check every `rt_err`;
ignoring a nonzero return and continuing is a caller bug the
conformance negatives pin down.

| Function | Syscalls used | Returns |
|---|---|---|
| `rt_exit(code)` noreturn | exit | never returns |
| `rt_write(fd, buf, n)` | write | `RT_OK`, or `RT_INVAL`/`RT_RANGE` |
| `rt_print(s)` / `rt_print_bytes(s, n)` | write | `RT_OK`, or `RT_INVAL`/`RT_RANGE` |
| `rt_fmt(buf, cap, fmt, ...)` | none (pure) | bytes emitted (`>= 0`), or negative `-RT_INVAL`/`-RT_RANGE` (never colliding with a count) |
| `rt_alloc(align, size, &out)` | none | `RT_OK`, or `RT_INVAL`/`RT_RANGE`/`RT_NOMEM` |
| `rt_free(ptr)` | none | `RT_OK` or `RT_INVAL` |
| `rt_arena_watermark()` / `rt_live_count()` / `rt_ptr_off(ptr)` | none | bytes / live blocks / offset or `(u64)-1` sentinel (evidence only, never writes the ledger, never authorizes frees) |
| `rt_nap(yields)` | yield | `RT_OK` or `RT_RANGE` |
| `rt_set_flag(p)` | none | `RT_OK` or `RT_INVAL` |
| `rt_wait_flag(p, max_yields)` | yield | `RT_OK`, `RT_AGAIN`, or `RT_INVAL` |
| `rt_open()` / `rt_read()` | none | always `RT_NOSYS` (honest stub, unchanged by 18d) |
| `rt_fd_read(fd, buf, n, &nread, flags)` | read (3, via six-register gate) | `RT_OK` (bytes in `*nread`), `RT_AGAIN` (empty, outputs untouched), or `RT_INVAL`/`RT_RANGE` |

### Syscall mapping table

| Call | Number | EBX | ECX | EDX | Kernel return mapped as |
|---|---|---|---|---|---|
| exit | 0 | status | — | — | terminal |
| yield | 1 | — | — | — | resume (`RT_OK`) |
| write | 2 | fd (`1`) | buf | len | bytes → `RT_OK`; `(u64)-1` → `RT_INVAL` |

`fd` is validated in the library first (`1` only, else `RT_INVAL`);
`n` is bounded by `RT_WRITE_MAX` (`4096`, mirroring the kernel cap),
else `RT_RANGE`. Kernel `-1` therefore always means `RT_INVAL` at this
layer, which keeps the two rejection classes distinguishable.

### Memory model (bounded arena, no growth)

The arena is a fixed `RT_ARENA_SIZE` (`2048`) byte array in the
library `.bss`, so the linker places it inside the single 4 KiB data
window after program sections; any overflow fails closed at converter
time (`data` over `4096` is rejected), never at runtime. Allocation is
bump-pointer with overflow-checked align-up; a fixed 16-entry ledger
records live blocks. `rt_free` accepts only exact live block pointers
(bogus or double free → `RT_INVAL`); freed space is not reused, so the
watermark is monotonic within a run and every number the conformance
programs print is deterministic. Alignment must be a nonzero power of
two `<= 16`, else `RT_INVAL`; `size 0` or `NULL out` → `RT_INVAL`;
`size` over the arena → `RT_RANGE`; fitting but exhausted (ledger full
or bump past end) → `RT_NOMEM`.

### Ownership conventions

The arena is per-program (one static context, no threads): ownership
means live-pointer identity within the program's ledger, not module
identity. `rt_free` checks pointer identity only, so a cross-module
free within the same program image would succeed; cross-module frees
are forbidden by convention (enforced by review, not by code) because
conformance programs are single-module. Allocation and free of a block
belong to the same module by convention.

### Formatting (transactional two-pass)

`s` in `rt_print` is scanned for `NUL` up to `RT_PRINT_MAX` (`4096`);
missing `NUL` → `RT_RANGE` with nothing written. `rt_fmt` supports
`%s %u %x %c` only; `fmt` itself must be `NUL`-terminated within
`RT_PRINT_MAX` bytes (overlong/unterminated → `RT_INVAL`); pass one
measures with overflow-checked bounds, pass two emits. `cap` must be
`<= INT64_MAX` so the count never collides with negative errors.
`rt_fmt` never `NUL`-terminates: use the returned count with
`rt_print_bytes`; scanning the buffer would read stale bytes (the
conformance `emit_n` helpers do exactly this). Unknown specs, `NULL`
args, or over-cap output return `RT_INVAL`/`RT_RANGE` with the
destination provably untouched (the negatives print the destination
to prove it). Emit-pass `at < cap` guards keep a mid-call `%s`
mutation from becoming memory corruption (bounded output instead).

### Sync scope (single context)

`rt_nap` issues at most `RT_NAP_MAX` (`64`) yields, else `RT_RANGE`.
`rt_wait_flag` polls at most `max_yields` times, yielding between
polls; set-then-wait returns `RT_OK`, exhausted waits return
`RT_AGAIN`. `max_yields` is caller-bounded: a huge value (including
`U64_MAX`) intentionally yields that many times. Cross-context handoff
is 18d scope: documented here, not implemented. There is deliberately
no wall-clock, no sleep, and no blocking wait anywhere in this library.

### Print rebind (toolchain)

In-OS RYNX targets (`runtime="rtlib"`) bind the RIR `rt_print_*`
helpers to `rt_write(1, ...)` through this library; host-native
targets keep the Linux `rt_linux.asm` helpers byte-identical. The two
runtimes share no objects; the default (`runtime="rynor"`) links the
direct-gate `rt_rynor.asm` exactly as Stage 18b did, so all 18b
artifacts rebuild byte-identically.

## Invariants

- Gate numbers stay `0/1/2` (`exit/yield/write`), pinned equal to
  `kernel/include/syscall.h` and this doc by `test_rtlib.py`; only
  `int $0x80` appears (`rt_gate.asm` `_start` + `rt.c` gate).
- `fd == 1` only; `n <= 4096`; `cap <= INT64_MAX`; `fmt` bounded.
- Watermark monotonic within a run; frees never reclaim.
- `rt_live_count`/`rt_ptr_off` never write the ledger and never
  influence `rt_free` (identity check only).
- Guest evidence: `alloc`/`fmt`/`write` programs print
  watermark/live/offset lines; `nap`/`wait`/`nosys` pin return codes
  only (no arena use, so no watermark lines by design).
- Kernel-observed `[LOAD] write` rows equal the golden payloads in
  order (18 class lines + bare `hello` + 3 `.rl` prints); extra/missing
  rows fail.
- Violations are detected by `tools/host/rt_output.py` golden compare
  (guest numbers + kernel bytes), non-zero conformance exits
  (65..70), and the five-mutant gate.

## Implementation status

Implemented (with evidence): `user/lib/rt/{rt.h,rt.c,rt_gate.asm,
rt_rl.c}`, `tools/rynorlang/runtime/rynoros_rt.ld` (merged RW data
`LOAD` for clang `.rodata`; kernel maps the page `U-RW` either way, so
placement only, never new permissions), `kernel/core/rt-test.c` +
`kernel/include/rttest.h` (sequential per-program create/run/destroy
with balanced accounting, 7 programs), `tools/host/rt_output.py`,
`tests/repository/test_rtlib.py` (4 pins), `tests/integration/
test_rt.py` (12 methods: 7 evidence + 5 mutants). Planned: 18d
cross-context handoff. Experimental: none.

## Tests

- `python tools/build/build.py test` → repository suite (561 methods,
  incl. `test_rtlib` 4: both runtimes build + distinct blobs, default
  is `rynor`, rebind goes through library with one `int 0x80`, frozen
  numbers incl. `RT_FD_STDOUT` equal kernel + doc rows + `rt_err`
  order + 15-function surface + host `rt_linux.asm` still
  `syscall`/`60`).
- `python -m unittest discover -s tests/integration -p test_rt.py`
  → 12 methods: shared 60 s boot pins full evidence/exits/markers/
  terminator/skip-path/rebind-distinct, plus 5 mutants (arena-bound,
  length-check, fmt-measure, wait-bound intended-timeout, fd-check
  write-row pin). Fail-fast negatives assert `guest failure` quickly
  (`[RT]/[LOAD] failure=` in `qemu.py`); the wait mutant asserts
  `timed out` with no verified marker and no failures; the fd mutant
  asserts complete + verified + exits `0*7` with write-row mismatch.
- Coverage limits: single QEMU config for `VERIFIED`; the 9-config
  matrix covers the `SKIPPED` path only for default images.

## Known limitations

No blocking, no `read`, no `open`, no wall-clock, no cross-context
sync, no growth/`sbrk`, no threads, no heap reuse across frees. The
write sink is still the 18b serial-hex evidence ABI. `rt_free` does
not compact; long-running allocation patterns exhaust the fixed
arena by design (use `RT_NOMEM` as the signal). Ownership is
per-program, not per-module (same-image cross-module free would
succeed; forbidden by review). Arena `base + size` cannot wrap U64 in
practice (`base` derives from the 0..2048 watermark, `size` <= 2048);
unsigned wrap is defined and fails closed to `RT_NOMEM`. Conformance
numbers observed in `build/rt-tests/` runs, not a trap-exhaustion proof.
