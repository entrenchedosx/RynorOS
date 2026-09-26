# Stage 19e close-out — M8 str-return, compiler freeze, RynorOS resume

M8 COMPLETE — STRING RETURN VERIFIED.
STAGE 19e FEATURE-CLOSURE COMPLETE — FULL SELF-HOST SCALING DEFERRED.
RYNORLANG FEATURE-DRIVEN DEVELOPMENT HALTED.
RYNOROS DEVELOPMENT RESUMED — P1 filesystem mutation (create + extend + userspace wiring).

## M8 proof (commit 773c597)

String returns lower through the frozen aggregate/sret ABI: a `str` is a
2-word descriptor (ptr, len); the callee writes it through the hidden sret
pointer at `[rbp+16]` into caller-owned storage (home pair, outer sret
forward, or temp pair). No second return ABI, no new machine forms.

- Accept corpus: 25 cases with host-oracle differentials (exit + stdout),
  all executed in the MI emulator with RSP-balance asserted per run.
- Reject set: 5 cases stay backend-25 (`len`/`byte_at` direct on calls,
  `status<str>` materialization, non-str print-calls, `fn main(): str`).
- Mutants: reachable M8 mutants go RED and are restored (suite green).
- Native QEMU: 6/6 MATCH (lit/nest/hexch/hexchoob/callarg/wide).
- Exact image checks: RYNX extent equality, csz/fsz bounds, no-RBX audit,
  fail-closed sweep proving every branch/call target lands on a swept
  instruction boundary (E8 on 0x55 prologues).
- Suite: 21/21 `test_rynorlang_selfhost_emit_strret`.
- Regressions (all green, this backend): cond 14, byteat 14, strbyte 21,
  unwrap 19, bool 13, match 13, branch 24, emit 17, str 19, fjoin 16,
  print 13, data 20, stackargs 21.
- Pin moves: the str-ret / print-strcall 25-pins moved out of the
  str/data/stackargs suites into M8_PROMOTED_PINS compile-gates; the M8
  suite owns string returns end-to-end. Sweep table extended (test-only,
  additive) for four pre-existing forms M8 babies first sweep: `89 F1`,
  `48 29 C6`, `48 87 04 24`, `4C 8B/89` r8/r9 loads and spills.

## Whole-util coverage: 11/12 -> 12/12

Fresh per-fn census of `rynorlang/selfhost/util.rl` (12 fns, M8 backend):
all 12 return code 0. The M7 blocker (`hexch`, str-return) compiles.
No other util row changed verdict.

## Post-M8 compiler architecture audit

Bundle scale (measured): util/lex/check/prog/emit = 1501 fns,
11,183 lines, 604 KB of source; emit.rl alone is 1027 fns / 407 KB.

Frozen caps (backend rejects beyond these):

| Cap | Value | Enforced by |
|---|---|---|
| Image code | 64 KiB | derr 26 |
| Image data | 32 KiB | derr 26 |
| Arg words / call | 12 | gate 25 (+stackargs bounds) |
| Lets / fn | 128 | derr 26 |
| Temp depth | 128 | derr 26 |
| Entry | exactly one `fn main(): int` | gate 25 |

Fresh compilability (M8 backend):

| Subsystem | Fns | Verdict |
|---|---|---|
| util | 12 | 12/12 code 0 (fresh full census) |
| lex | 6 | 0/6 measurable — census driver exceeds the 500M host-oracle step budget (infra-blocked, not a backend verdict) |
| check/prog/emit | 149/307/1027 | 21/30 in a fresh stratified sample (all 9 rejects are code 25; 8 fail in harness/stubs, i.e. target unjudged) |

Stale reference (9/17, partial 1219-row pass, pre-M5..M8): check 31/149,
prog 70/307, emit 332/745 rows code 0, code-25-dominated. Understates the
current backend; kept only as a lower bound.

Static signature blockers over all 1501 fns (live predictor except
bool-ret/str-ret, which M1/M8 closed): 316 fns exceed 12 arg words
(check 15, prog 87, emit 214) and 65 exceed 12 params. These need
calling-convention work (deeper stack args or record packing), a project
in its own right, before full self-host is even expressible.

Scaling blockers, ranked:

1. Image ceiling. 604 KB of source cannot become a 64+32 KiB baby at any
   plausible expansion ratio (~100x short). Full self-host needs a new
   image/profile story, not another lowering slice.
2. Calling convention. 316 fns over the 12-word cap.
3. Census/harness budget. The whole-backend-under-oracle driver already
   overflows 500M steps on lex-shaped probes; per-fn verification itself
   needs harness work (budget splits, slimmer drivers).
4. Long-tail body constructs. The sample's target-side rejects are
   heterogeneous (gates, recursion, record/list corners) — no single
   next slice clears them; each is M-series-sized work.
5. Suite runtime. Backend-under-oracle verification now costs
   4–44 min per suite (stackargs 21 tests / 44 min); iteration is
   throttled by verification, not by ideas.

## Compiler freeze decision

Feature-driven RynorLang backend work is HALTED after M8. Rationale: the
remaining gap to full self-host is structural (image caps, convention,
harness budget), and every further lowering slice pays the full
verification tax while moving none of those ceilings. Deferred, with
reason: full self-host (blocked by 1–3 above); `len`/`byte_at` directly
on call results, `status<str>` materialization, `fn main(): str`
(small, but each re-opens the verification bill for zero OS value).

The backend stays untouched except to serve OS needs (e.g. a syscall or
driver shape the OS slice genuinely requires, proven by a failing OS
test first).

## RynorOS capability audit

| Subsystem | Maturity | Evidence |
|---|---|---|
| Boot (sector + transition) | Working | every QEMU test boots to CPL3 shell |
| Proc/spawn/wait/terminate, scheduler, pipes | Working | test_proc/scheduler/pipe/load/userspace |
| Block PIO + fs mount/open/stat/read/close | Working | test_storage/filesystem, corrupt-image mutants |
| In-kernel fs overwrite (in-extent only) | Working, capped | fs_write + fault-injection + write mutants; extension/creation/truncation are explicit FS_RANGE |
| Syscalls 0–8 (exit/yield/write/read/spawn/wait/terminate/fread/spawn_pipe) | Working | CPL3 shell + native-backend tests |
| CPL3 shell (spawn, pipes, scripts, resident RL eval) | Working | test_shell/cplshell transcript pins |
| File creation / extension / userspace file write | MISSING | no allocator, no syscall, no shell verb |
| Drivers/input/display/mm/heap | Mixed | per-subsystem tests exist; audit per slice |

Product readiness: no repo R-scale exists, so stated plainly — the OS is
a read-only-software appliance: it boots, runs baked-in programs, and
evaluates code in memory, but no CPL3 principal can persist a byte. The
single top blocker is file creation + extension wired to userspace.

## First OS slice: P1 filesystem mutation

Exact target: a CPL3 program creates a file, writes bytes, closes it,
reopens it, and reads the bytes back — all through new frozen ABI,
proven by QEMU integration tests with corrupt-image and fault mutants.

Architecture (three layers, in order):

1. Kernel allocator + create/extend (`kernel/storage/fs.c`, `fs.h`):
   free-data-block tracking over the existing dir+data layout,
   `fs_create` (dir slot + first extent), `fs_write` extension within
   a grown-or-allocated extent. Caps mirror existing style (PATH 32,
   OPEN 8, per-op 16 KiB). Overwrite path untouched.
2. Syscall: `SYS_FWRITE` (+ create/open flags on the existing path
   form, following the `SYS_FREAD` six-register pattern in
   `kernel/core/user.c`). Number 9; frozen ranges documented in
   `kernel/include/syscall.h`.
3. Shell + tests: a shell write verb (`echo TEXT > /path` redirect or
   `write` builtin) in `user/shell/sh.c`, and
   `tests/integration/test_filesystem.py` additions:
   create→write→readback evidence rows, host-file corroboration,
   partial-write/fault mutants, following the existing mutant patterns.

Non-goals for P1: directories-as-fds, unlink/rename, truncation,
permissions, journaling. Those are later slices once create/write/close
is native-proven.

## Git state

- M8: 773c597 on main (8 files: emit.rl, strret suite, probe_m8native,
  str/data/stackargs/fjoin suites). No push (policy).
- Scratch preserved: probe_*.py, census*.jsonl, assets/rynoros/ untouched.
- This report: docs/reports/stage19e.md (new, uncommitted with the
  report itself — commit separately or with the P1 slice kickoff).
