# RynorLang Stage 19e — self-hosting compiler (frozen specification)

Status: **frozen.** Executable spec for Stage 19e: the self-host
definition (Level 4 byte fixed-point), the compiler-core dialect,
the bootstrap chain with declared seed, the guest machine model,
bounds, the direct-to-machine-code backend strategy, RYNX
emission, the serial-transport proof protocol, and the test plan
with 14 named mutants. Nothing here executes by itself.

Precedence: below all earlier frozen contracts; above the implementation.

## 1. Definition of self-hosting (completion = Level 4)

- L0 host compilation (exists; NOT self-hosting).
- L1: the compiler binary runs in CPL3, reads source via guest
  `fread`, exits with honest statuses. Necessary, not sufficient.
- L2: the guest compiler emits legitimate RYNX (header + code +
  data) for corpus programs; collected bytes pass the frozen
  RYNX validator; baked into a follow-up image through the normal
  shell-spawn path the output executes with correct behavior.
- L3: the guest compiler compiles its own compiler source
  (multi-file, manifest-pinned); the resulting binary is collected.
- L4 (ROADMAP completion): the collected self-built compiler
  binary, baked and booted, compiles the same compiler source
  again yielding **byte-identical** output (B == C), and the
  self-built binary demonstrably executes (compiles corpus
  programs whose outputs behave correctly). Byte identity is
  required (not merely semantic): the backend is deterministic
  by construction (§14) and embeds no generation metadata, so
  any divergence is a bug, investigated, never normalized away.

## 2. Bootstrap generations and trusted seed

- Seed: the existing host pipeline (`compile_source` family) is
  the declared trusted seed. It is NEVER described as
  self-hosted; every later artifact records its provenance.
- Gen-A: host builds the baby compiler source
  (`rynorlang/selfhost/`) to a v2 RYNX via the existing
  `build_rynor_program(..., runtime="rynor")` + `elf_to_rnyx`
  path. Baked at `/bin/selfc`.
- Gen-B: Gen-A, booted, compiles corpus programs (L2). Output
  bytes leave the guest ONLY as hex text over serial (§12).
- Gen-C: Gen-A, booted, compiles the baby source itself (L3).
- Gen-D: Gen-C output, baked and booted, compiles the baby
  source (B == C required) and corpus programs (execution proof
  of the self-built compiler). L4 complete.
- The baby source is ALSO built host-native (Linux ELF) from
  the identical files for development velocity; host-native
  runs are tests, never proof. Proof artifacts (hashes,
  transcripts) come exclusively from QEMU boots.

## 3. Compiler-core dialect (bootstrap constraint, not a fork)

Core = the baby's implemented subset; host enforces it with a
new `--profile core` value (19d-handoff mechanism). Under `core`
these are `SEM_PROFILE_EXCLUDED` (same code in baby):
map types anywhere; result types anywhere; `ok`/`err`
constructors; `unwrap_ok`/`unwrap_err`; `insert`/`get`
builtins; `print` of map/result/record/list values
(status/int/bool/str prints stay); `match` on a result
scrutinee. Everything else in frozen v1 stays (incl. records,
lists, status + `is_ok`/`unwrap_or`, `match` on
status/int/bool/str, `break`/`continue`, bitops, `use` imports,
`fread`/`fjoin`/`argv` from §6). Core programs additionally
observe one discipline (baby-enforced, host-accepted):
definition before use (functions and records; enables
single-pass codegen with no backpatching).
Rationale: each exclusion removes backend machinery (map
probing, result layouts, print_agg, match-on-result) while the
required corpus stays expressible via projection (lengths,
elements, fields, `is_ok`/`unwrap_or`, exit codes). `result`
cells are the only corpus class deferred to post-19e;
aggregate prints run as projections (same layouts verified).
Host-core tracks baby exactly: any cut below amends this
section and the §18 classification together.

## 4. Source/module model

Single-pass, streaming within one buffered source (≤16 KiB).
No token table: a 2-token lookahead window (current + peek —
`use` disambiguation needs exactly STRING-after-`use`) with
kinds/starts/lens in scalar variables; symbols are parallel
fixed lists (≤256 entries: kind, alias-span, name-span,
value ≈ 10 KiB). No string synthesis anywhere
(RynorLang has no concat): mangled identities stay
(alias-span, name-span) pairs compared piecewise; diagnostics
print pieces across successive `print` calls (print emits exact
bytes, no newlines). `use` resolution obeys frozen 19c
semantics exactly (relative resolution against the entry dir
via `fjoin`, dup/cycle/mangle/pins/edition); `std/` maps to
guest-baked `/std/` (byte copies of the host std sources,
hash-pinned by the baking test). Manifests are REQUIRED for
programs with project imports (R2-hardcoded, §14 of the 19d
report): presence + edition + sha256 (implemented in baby,
~50 lines) all enforced with frozen codes. std-only and
single-file programs ignore stray manifests (host-identical).

## 5. Compiler execution environment (CPL3, v2 binary)

v2 RYNX: code ≤64 KiB, data ≤32 KiB, 4 KiB stack, no heap, no
growth (frozen map). argv[1] carries the entry source path
(`MAX_ARGC` 8 / 256 bytes suffice; missing argv → diagnostic +
nonzero exit). Input via `fread` chunks (≤16384 per call);
output via `print` (≤4096 per call) to serial. No kernel, shell,
or host changes: no new syscalls (fread/spawn exist), no new
user C code (existing shell spawns `/bin/selfc` with argv;
keyboard drives it). Placement guard test pins no compiler
symbols in `kernel/` and no new `user/` binaries except the
baked RYNX artifacts.

## 6. New builtins (additive, collision-checked)

`fread(path: str, offset: int, len: int) -> status<str>`
(exact file bytes; err codes reuse the frozen family:
ERR_NOTFOUND for missing, ERR_OORANGE for bad offset/len,
ERR_NOMEM for arena overflow, new additive ERR_IO for other
failures — guest maps kernel `sys_err` onto the same table)
and `fjoin(dir: str, rel: str) -> status<str>`
(`dir + "/" + rel`, empty dir yields rel; empty or absolute
rel yields ERR_OORANGE, documented as invalid-argument;
deeper rules like `..` belong to the module layer, which
applies them identically on both sides). Reserved via
`AGG_BUILTINS` (a `fn fread`/`fjoin` becomes `SEM_DUPLICATE`
like `len`; zero collisions across all fixtures, pinned by
test). New RIR ops `str_fread`/`str_fjoin` with verifier,
oracle (host-fs backed), and Linux backend (open/read/close)
support. Guest backend emits the `fread` gate inline; `fjoin`
is pure arithmetic over bytes (inline compare/copy loop over a
fixed 64-byte out-scratch in the frame — paths ≤32 by FS cap,
never overflows).

## 7. Memory model and bounds (all enforced with diagnostics)

Guest data budget 32 KiB: source ≤16 KiB (oversize source is
`PAR_FILE_TOO_LARGE`; table exhaustion is `SEM_LIMIT_EXCEEDED`;
output over v2 limits is `COMP_EMIT_FAILED`; all pinned ±1);
2-token lookahead window (no token table); symbols ≤256
(parallel fixed lists); modules ≤16 (tighter than host 64:
guest buffer reality; host accepts up to 64 — corpus uses ≤4,
divergence class DOCUMENTED for 17+); import depth ≤16
(host-identical); manifest ≤64 KiB (host-identical rule);
frames ≤128 slots per function (guest stack reality; host
allows 1024 — corpus uses ≤16, DOCUMENTED beyond); output
code ≤65536 / data ≤32768 (v2 maxima, counted during the
sizing pass). Baby SOURCE discipline (checked host-side by
test): ≤12 KiB source, per-function frameslots ≤96, call depth
≤8 by audit, no unbounded recursion (iterative lexer/parser,
worklists; one bounded type-size recursion ≤ `MAX_TYPE_NESTING`
8, audited at 8 × small frames).
No recursion in baby-executed paths: parser is precedence
climbing with an explicit loop; type/shape walks are bounded
by `MAX_TYPE_NESTING` 8.

## 8. Intermediate representation: none in guest (Option A)

Backend strategy is direct AST→machine-code (task §12 Option
A): the guest builds no RIR (memory!), lowering the checked
AST straight to bytes in two passes over the same deterministic
code paths — pass 1 counts code/data sizes (and aborts cleanly
past v2 limits before printing anything), pass 2 emits header
+ code + data. RIR remains the HOST-side differential oracle
(host compiles the corpus to RIR text; guest behavior must
match it). Rationale: smallest guest footprint (no IR tables),
no assembler/linker needed in guest, single code path to audit;
the frozen ABI (frames, slots, sret-at-slot-0, tag layouts,
FNV+probe rule, eager `&&`/`||`, `idiv` traps) is reimplemented
from the spec, with differentials proving equality.

## 9. RYNX emission

Baby emits the 28-byte v2 header itself (magic/version/arch/
sizes from the sizing pass), then code, then data — all as hex
text (§12). No host envelope construction (the host only
hex-decodes opaque bytes). `_start` is emitted by the baby
(ignores argv, calls `rl_main`, exits with its value). Print
is inlined (int decimal template, bool/str direct `write`);
`exit` is inlined. Output programs are self-contained: no
runtime object, no linker, no relocations (absolute addressing
only, fixed VA). v1-size outputs are still wrapped as v2
(superset envelope; loader accepts).

## 10. Filesystem/output publication

No guest file write exists (RYNORFS v1: no create/extend; no
new syscalls in 19e). Publication = the hex protocol over
serial: `S191-BEGIN <nbytes>` line, hex chunks (≤4096
print each), `S191-END <fnv1a64-hex-of-binary>` line, then
exit status. Host collects bytes ONLY from the boot serial
transcript (provenance: input-image hash → transcript →
output hash, all recorded). Missing END marker or checksum
mismatch = discarded (transactional by framing; nothing
partial is ever baked). Follow-up boots bake collected bytes
at `/out/prog.rnx` (image provision, explicitly allowed
orchestration) and the existing shell spawns the absolute
path for execution proof.

## 11. Diagnostics

Frozen codes reused exactly (`PAR_*`/`SEM_*`/`MOD_*`/`COMP_*`
+ `SEM_PROFILE_EXCLUDED` for core exclusions). Messages need
not match host text (documented); codes must match on the
required corpus. Failed compiles print `S191-ERROR <code>`
and exit nonzero with no output block. Baby never traps on
specified failure classes (caps/cycles/pins/malformed all
diagnose); a genuine baby bug faults only its own process
(kernel integrity never at risk; subsequent boots unaffected).

## 12. Deterministic-build rule

Same (source bytes, module graph incl. manifest bytes,
options) → same bytes, always. No timestamps/counters/ASLR
influence (no heap addresses leak: fixed VA; no iteration
over unordered structures: all tables append-ordered).
Verified by compiling the corpus 3× in-guest and comparing
hashes, plus the L4 B==C gate. Any divergence is a bug.

## 13. Compatibility

19e adds: two builtins, one profile value, no syntax, no
changed semantics (default profile byte-identical; all 456
repo + 84 guest tests must stay green). New files:
`rynorlang/selfhost/*.rl` + manifest (baby source),
`tests/fixtures/rynorlang/selfhost/` (guest corpus),
`tests/repository/test_rynorlang_selfhost.py` (host-side:
builtins/profile/pins/mutants), `tests/integration/test_selfc.py`
(guest proof chain). Inventory + repository pins updated.

## 14. Self-host proof procedure (per level)

L1: boot `/bin/selfc` with no argv → usage diagnostic,
nonzero exit; with missing file → `S191-ERROR` class,
nonzero exit (no trap).
L2: for each required corpus program: boot, type
`selfc <path>`, collect `S191` block, verify checksum,
validate RYNX via frozen `rnyx.validate`, bake at
`/out/prog.rnx`, boot, shell-spawn, assert exit/stdout
equals host-oracle values.
L3: bake baby source + manifest; boot; `selfc
/self/main.rl`; collect Gen-C bytes; validate RYNX.
L4: bake Gen-C as `/bin/selfc` with identical source
files; boot; `selfc /self/main.rl`; collect Gen-D bytes;
assert Gen-D == Gen-C (sha256); bake Gen-D output of a
corpus program; boot; execute; assert behavior.
Anti-cheat throughout (§17).

## 15. Security

Compiler input is hostile userspace data: all caps diagnosed
(§7), no kernel surface added, CPL3 faults contained.
`fread`/`fjoin` backends validate pointers/lengths through
the existing two-pass `USER`-bit checks (host: bounded reads;
guest: kernel `fread` already validates; baby's inline gate
passes caller buffers). New filesystem surface: none (read
only). Audit new `user-pointer` uses in changed host code by
existing test patterns.

## 16. Test plan (binding)

Host (`test_rynorlang_selfhost.py`): builtin accept/reject
± codes, profile-core accept/exclude matrix, baby-source
core-cleanliness (host analyzes every baby file with
`profile=core`), frameslots/line-count discipline pins,
placement-guard pins, RIR-op verifier pins, oracle fread
pins, ≥10 host mutants. Guest (`test_selfc.py`): L1
diagnostics, L2 corpus matrix (each: collect→checksum→
validate→bake→spawn→behavior), L3 self-compile, L4
fixed-point + Gen-D execution, source-variation
anti-canned set, rejection-variation set, repeat-compile
stability (spawn loop in one boot), resource-failure set
(oversize/cycle/pin/manifest/depth caps), 14 E-mutants
(§18), provenance chain (image→transcript→output hashes).

## 17. Anti-cheat design (E-M1/E-M2/E-M12 core)

Provenance: every proof artifact records
(input-image-sha256, transcript-sha256, output-sha256);
bytes reach the host ONLY through `collect_load_writes` of
the proving boot (a test helper asserts the byte source —
no fixture file, no substitute path). Variation: each L2
case runs against ≥3 source mutations (constants, branches,
field values, sizes); outputs must differ correspondingly
(a canned binary is byte-identical across mutations →
RED). Rejection variation: invalid mutations must produce
the frozen class (a canned acceptor fails). Self-shortcut:
Gen-C bytes must differ from Gen-A bytes (different
codegen... they SHOULD differ — independent backends) AND
Gen-D must equal Gen-C (same backend) — substituting
Gen-A for Gen-C breaks the equality RED.

## 18. Mutants (14, all RED-verified then restored)

E-M1 host-substitutes-output (provenance test RED);
E-M2 canned binary (variation set RED);
E-M3 wrong arg register (corpus runtime RED);
E-M4 record field offset +8 (record corpus RED);
E-M5 list capacity ignored (cap-boundary RED);
E-M6 mangling separator `_` vs `__` (nested project RED);
E-M7 cycle check replaced by accept (cycle corpus compiles
instead of rejecting RED);
E-M8 core-exclusion removed (negative corpus GREEN→RED);
E-M9 generation counter byte in output (3× hash RED);
E-M10 END marker emitted before validation (truncation
test RED); E-M11 fread of `/host/only` path (must cleanly
reject — isolation RED if accepted); E-M12 Gen-A bytes
replayed as Gen-C (equality-pattern RED); E-M13 corrupt
magic (loader rejects — validation RED if accepted);
E-M14 table-reset removed (repeat-compile drift RED).

## 19. Alternatives considered

- C1 guest assembler/linker (task Option C): rejected — no
  such tools exist in guest; building them dwarfs the
  compiler and is Stage 20 scope.
- C2 AST→RIR→code in guest (Option B): rejected — IR
  tables cost memory the v2 data window cannot spare;
  RIR stays the host oracle.
- C3 new fwrite syscall + in-guest files: rejected — needs
  create/extend semantics RYNORFS v1 forbids; the serial
  protocol + bake transport achieves every level with zero
  kernel surface. (A future stage may add file output; 19e
  proves compilation, not filesystem growth.)
- C4 C implementation of the guest compiler: rejected —
  ROADMAP mandates the core-dialect RynorLang compiler
  that rebuilds itself; C cannot bootstrap RynorLang.
- C5 full host profile in guest (maps/result/match): rejected
  — backend machinery exceeds the memory/code budget; the
  projection-expressible corpus + documented deferral of
  `result` cells keeps self-hosting meaningful.
- C6 single-file baby (no modules): rejected — Level 3
  multi-file self-compile exercises the module system;
  `fjoin` (~15 lines) unlocks full relative resolution.
- C7 semantic (non-byte) fixed-point: rejected — 19d
  determinism + no generation metadata make byte identity
  achievable; anything weaker hides bugs.

## 20. Non-goals

Stage 20 tools/network/graphics/audio/Windows; new syscalls;
kernel/shell/user-C changes; guest file writing; core-subset
expansion; performance optimization beyond boundedness;
changing any frozen semantic, code, or corpus expectation.

## 21. Critics (seven; BLOCKERs resolved before implementation)

1. Language: "core exclusions (no maps/result) make the
   dialect a fork." → Resolved: core ⊆ v1 verified by
   `--profile core` on both sides; excluded constructs
   diagnose with the SAME code in host and guest; the normal
   language is untouched. Locked.
2. Compiler/backend: "direct-to-code without IR cannot be
   trusted against the frozen ABI." → Resolved: every ABI
   rule reimplemented from spec has a differential (host
   RIR/oracle vs guest behavior) on the required corpus +
   E-M3/E-M4/E-M5 break exactly those rules RED. Locked.
3. Kernel/runtime: "a compiler in 32K data + 4K stack is
   fantasy." → Resolved: budgets measured (§7 caps; code
   ratio 172 B/line ⇒ ≤350-line baby; per-fn frameslots
   pinned host-side; streaming output; two-pass sizing).
   Slice B measures the ratio on baby-shaped code and
   stops the line if it diverges >20%. Locked.
4. Security: "guest compiler parsing hostile source in
   CPL3 with inline syscalls risks kernel integrity." →
   Resolved: no new syscalls; all bounds diagnosed; CPL3
   faults contained by design; placement-guard test;
   hostile corpus (caps/cycles/malformed) must diagnose,
   never fault the kernel. Locked.
5. Minimalism: "`fjoin` + `fread` + `core` profile is three
   host features for one stage." → Resolved: each is
   load-bearing (source input, path resolution, subset
   lock) with no smaller alternative found (single-file and
   absolute-only paths both fail the shared-corpus test).
   Locked.
6. Future compatibility: "byte fixed-point over-constrains
   20x (ASLR, timestamps). " → Resolved: the constraint
   binds 19e artifacts only (fixed VA, no metadata); later
   stages relax explicitly with their own RFCs. Locked.
7. Cold reviewer: "serial hex exfiltration is a toy, not a
   compiler output path." → Resolved: the artifact is a
   complete valid RYNX (validated + executed); transport
   is framing, proven opaque by variation + provenance +
   checksum tests. A file path would need filesystem
   growth (out of scope, C3). Locked.

## 22. Bounds inventory (additive)

Guest: source ≤16 KiB, 2-token window, symbols ≤256, modules
≤16, import depth ≤16, manifest ≤64 KiB, frames ≤128
slots/fn, output code ≤65536, data ≤32768, argv path ≤32,
fread chunk ≤16384, print chunk ≤4096, hex line discipline
per §10. Code-size ladder (measured at slice B on baby-shaped
code; stop the line past 55 KiB projected): L1 cut match
desugaring (match corpus deferred, host-core rejects all
match); L2 cut the `std/` prefix map (std corpus deferred);
L3 cut `push` (cap cells via literals). Cuts never threaten
Level 4 (self-compile uses only the intersection). Baby
source: ≤12 KiB, ≤350 lines target (kLOC
1.5k loose bound), per-fn frameslots ≤96, call depth ≤8,
no unbounded recursion. Host: `fread`/`fjoin` lengths mirror guest
caps (fread len ≤16384, paths ≤32); `--profile core`
rejections enumerated in §3. Each bound carries ±1 or
present/absent evidence.
