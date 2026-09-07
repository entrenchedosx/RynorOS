# Stage 18b Hardening — Residual Ledger

Bounded residuals accepted by architect ruling across gauntlet rounds 1–2.
Each entry is a known limitation with its containment argument and the hook
milestone where a redesign belongs. None blocks the 18b exit condition
(loader + syscall boundary with validated isolation); none is silent — every
one has either a fail-closed behavior, a pinning test, or both.

## R1/R2 rulings applied (not residuals)

- R1 halt-on-rollback-failure: every rollback return in fs.c and the user.c
  load path is checked and halts with a `rollback_<step>` marker
  (`[FS] failure=rollback_*` via rollback_fail, `[USER]` via panic).
  A repository pin (`test_rollback_discards`) fails on any new `(void)`
  rollback discard, bare-statement rollback call, or removed checked form.
  Detection leg, stated explicitly: for provably-unreachable halts the
  guarantee is form-level, not behavioral — e.g. the entry-revoke R-B
  mutant boots green with a full image (completed, `fs verified`) because
  the halt cannot fire on static devices, and it trips exactly 1 void-hit
  in the pin test (executed). An earlier stripped-image QEMU failure
  (missing files tripping `evict-stat`) was artifact, not mutant
  detection; no claim is made that R-B is detected by guest behavior.
- R2 blk authority confinement: the sole non-FS path to blk_set_writable
  is blk-test.c's test_writability entry (single outstanding grant,
  test-device only, with negatives); fs_mount/fs_unmount own the FS path.
  Call-site confinement is pinned by the same test module.

## Bounded residuals

1. IST/emergency stacks — BOUNDED RESIDUAL, hook: hardening milestone.
   All gates use IST=0 with a single 4K RSP0 exit stack per context; a
   nested fault/NMI inside delivery corrupts the live frame and can
   triple-fault instead of halting with a diagnostic. Containment: single
   CPU, no NMI source armed in the test matrix, recursion guard halts on
   detected nesting. Full IST design (DF/NMI/MC stacks + guards) belongs
   to a hardening milestone, not 18b.
2. SMEP/SMAP — BOUNDED RESIDUAL, hook: hardening milestone.
   Both features are probed but required OFF; the kernel half stays mapped
   on user CR3 and user_publish_stop relies on SMAP-off stores. Containment:
   under single-delivery conditions no kernel RIP-hijack primitive is
   reachable from CPL3 (all returns are validated IRETQ/frame paths), and
   high-half leaves are audited supervisor-only by high_ok. Nested
   delivery is item 1's residual, not this item's claim. Enforcement
   belongs to 18d-era hardening.
3. vm_protect USER posture — BOUNDED RESIDUAL, hook: hardening milestone.
   vm_protect enforces the USER bit on the old mapping only, so the API
   would accept a USER→supervisor flip if ever called that way. Containment:
   no in-tree caller does so (all user mappings are created with fixed
   permissions and never reprotected); replica_ok would fail closed on the
   result. API-level enforcement belongs to hardening.
4. Fault-whitelist halt — BOUNDED RESIDUAL, hook: 18d.
   CPL3 faults outside the managed whitelist (NMI/#DF/#MC/CET) halt instead
   of recording a kill. Containment: none of these vectors is reachable
   from unprivileged code in the current matrix (DPL0 IDT gates force #GP;
   no CET/NMI source exists); reachable hostile RFLAGS/vectors now kill
   for loaded programs (gate + fault paths). Full-vector kill coverage
   belongs to 18d.
5. Cooperative no-watchdog liveness — BOUNDED RESIDUAL, hook: 18c/18d.
   A loaded program that never traps spins forever under preemption with
   no tick budget or kill (18a spin blobs terminate via the stop flag,
   which loaded programs deliberately never receive). Containment: the
   in-tree program set always exits; a hostile spinner denies only its own
   slot's progress on a single CPU and is observable via tickspin counters.
   Quanta/watchdog belong to the 18c runtime / 18d shell milestones.
6. Tick-time hostile-RSP halt — BOUNDED RESIDUAL, hook: hardening milestone.
   sched_tick halts (frame_failure) on a failed user_origin_ok instead of
   recording a fault for loaded programs, unlike the gate/fault paths.
   Containment: no privilege escape and no corruption (halt is total, via
   cli+halt); 18a exact-count preemption depends on the current path.
   IRQ-context kill-and-schedule needs new scheduler machinery and
   belongs to hardening, not 18b.
7. blk capability remainder — BOUNDED RESIDUAL, hook: hardening milestone.
   blk_set_writable remains callable by any kernel caller (presence check
   only); confinement is by call-site pin, not by capability. Containment:
   the only in-tree callers are fs_mount (post-validation) and the single
   test entry (test-device only, revoked at teardown); CPL3 has no path to
   it; boot-disk writes stay DENIED at rest (pinned by blk, fs, and
   authority tests). A capability/owner model belongs to hardening.
8. Partial PA/code-hash corroboration — BOUNDED RESIDUAL, hook: hardening.
   load_output.validate pins geometry, program multiset, exits, maps
   (count + per-slot shape + isolation disjointness), writes, rejects,
   tickspin, and accounting — but not full code bytes or physical-frame
   provenance. Containment: exit codes, hello bytes, probe payload, and
   exact exit rows bind the executed programs; a substitution preserving
   every observable stays contained behind the same boundary. Full
   file→executed hashing belongs to hardening.
9. QEMU-only evidence — BOUNDED RESIDUAL, hook: physical-hardware milestone.
   All execution evidence is QEMU TCG (pinned emulator + firmware hashes);
   no silicon runs are claimed. Bare-metal porting (drivers, timing,
   microcode behavior) is explicitly out of scope until the
   physical-hardware milestone.

## 18c handoff

The 18c runtime library inherits: frozen syscall numbers, the copyin chunk
pattern, fixed user windows (growth is 18c's first design decision), the
`[LOAD]` evidence grammar, R1's rollback markers, and R2's single test
authority entry. The ledger above is the hardening backlog it must not
reopen silently.
