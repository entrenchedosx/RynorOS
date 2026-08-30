# Independent Stage 7 audit and repair

Starting commit: `3187675c7651f8256c1c511ac661e11487cbad60`.
Trusted predecessor: `4c537f037924dc21f7ae7e48aa88c8a4329bb836`.
The initial checkout was clean on main; a live `git ls-remote` showed GitHub main
matched HEAD. Both commits were authored/committed as Codex. This audit does not
rewrite them. All new audit commits use author and committer **r1ra**.

## Verdict and baseline reproduction

**Stage 7 required significant repair.** Its context layout and basic PIC/IRQ
route were usable, but lifecycle, ownership, transition serialization and tests
were not trustworthy as delivered. No next milestone was implemented.

The clean unmodified baseline produced **62 repository passes and 36/37
integration passes**. `test_no_genuine_preemption_cannot_pass` timed out after
the timer transcript without its expected scheduler diagnostic. Log:
`build/stage7-audit-baseline.log`. This independently contradicts an unconditional
all-green completion claim. The timeout alone does not prove a specific race;
the unsafe transition windows were separately identified in source.

Additional QEMU probes extracted the exact original commit into temporary
fixtures. They produced:

```text
[SCHED] failure=audit_foreign_free_accepted
[SCHED] failure=audit_copied_owner_accepted
[SCHED] failure=audit_yield_enabled_interrupts
```

These are real guest assertion failures, not mocked allocator results.
The probe changed an allocated stack's guard frame to an unrelated allocated
frame; free accepted it. A copied stack handle was also accepted as an owner.
Yield called with IF=0 returned with IF=1. Original transcripts remain locally
under ignored `build/stage7-original/`; the source is recoverable from Git.

## Scope

Reviewed the complete trusted-baseline-to-Stage-7 diff; current and previous
commit metadata; architecture, README, roadmap, earlier audit and Stage 7
design/report; all stack/thread/switch code; CPU frames, exception and IRQ entry,
PIC/PIT and return paths; PMM/VM/heap ownership contracts; linker/image build;
all Stage 7 tests and changed regression parsers; canonical icon/package.
The architectural basis is linked in `docs/design/scheduler.md`.

## Findings and fixes

1. **Caller-controlled physical ownership.** `kstack_valid` checked magic, slot
   geometry and one alignment, not live slot ownership, PMM state or mapping
   identity. Free trusted caller physical addresses and skipped unsuccessful
   payload queries. Copies/stale handles could unmap a live stack or release
   another allocation. Replaced with a private frame registry bound to an
   immovable handle address and generation. Teardown preflights every guard,
   payload mapping, permission and PMM state before mutation, and rejects its
   own active stack. Handle corruption can no longer nominate physical frames.

2. **Duplicate stack attachment and stale thread pointers.** Creation copied a
   caller-owned stack without transfer/invalidation, so multiple threads could
   share the same stack ownership. Join dereferenced arbitrary thread pointers,
   accepted magic instead of table membership, and recycled pointers without a
   generation. Creation now allocates its own internal stack; public thread IDs
   are non-reused values. Live/self/bootstrap/reaped/stale joins are rejected.
   Stack ownership stays in the thread record until non-current EXITED reap.

3. **Interrupt-enabled state transitions.** Yield and exit modified current,
   READY/RUNNING/EXITED and saved frames with IF=1. An interrupt could attribute
   a real old-stack frame to the newly published current thread. Join did not
   enforce its advertised context either. All transitions now run with IF=0;
   yield restores its caller's IF and verifies, rather than overwrites, current
   on resumption. Lifecycle calls reject IRQ context; bootstrap cannot exit.

4. **Wrong voluntary context flags and error offset.** `thread_switch` forced
   RFLAGS=0x202 (including IF=1), stored a qword at offset 124 instead of 128,
   duplicated a saved-register store and duplicated the restore tail. It now
   saves real IF=0 flags, writes the correct normalized error slot and shares
   one IRETQ restore tail. C restores the yield caller's IF after RET. The
   function-call versus instruction-interruption contracts are explicit.

5. **Unvalidated context handoff.** IRQ return directly assigned RSP from an
   unchecked C return pointer. Saved RIP/RSP/selectors/flags and scheduler
   ownership were not validated. A final C gate now rejects unknown pointers
   before reading them, checks selected/current provenance and frame bounds,
   validates linked-text RIP, ring-0 selectors, flags and normalized slots before
   assembly changes RSP. Invalid current/state is fail-closed. This is bounded
   corruption detection, not authentication against malicious ring-0 writes.

6. **Scheduler state and initialization coupled to tests.** Initialization only
   existed inside a long self-test. `scheduler_check` checked idle test counters,
   not live state; a supposed readiness helper merely returned an active flag.
   Initialization and live invariant checks are now real subsystem APIs. Slot
   selection uses internal array position, not an unchecked ID. Self-tests and
   busy-loop assembly are separate. Empty/READY/RUNNING/EXITED lifecycle is
   checked, bootstrap is permanent, and unknown address-space arguments were
   removed rather than pretending CR3 switching exists.

7. **Unnecessary guard-frame allocation.** Mapping then unmapping and retaining
   a fifth physical frame provided no additional guard protection. The guard is
   now absent from the outset and unbacked; four real RW/NX payload frames are
   zeroed before publication. Mapping conflict and partial allocation rollback
   are tested. No giant pool, alternative PMM or heap-backed fake stack exists.

8. **Synchronization was unproven and deadlock-prone.** Locks had no IF/ownership
   enforcement or tests, used a byte operation on an int flag, and could spin
   forever on a single CPU. Restore did nothing for a saved IF=0. The UP lock
   API now returns failure on contention/recursion, enforces IF=0, binds handle
   identity and thread/IRQ owner, checks unlock and blocks yield/exit while held.
   Enabling interrupts while holding a lock halts. Nested restore handles both
   states. This is not claimed to be an SMP atomic lock implementation.

9. **Undefined/fragile struct manipulation.** Structs containing enums/pointers
   were type-punned through `cpu_u64 *` for copying/zeroing. Replaced with typed
   assignment and compiler-required original freestanding byte memcpy/memset.
   No host libc is linked. ISR-accessed test controls are now explicit volatile
   objects; general state is protected by IF=0 and compiler barriers.

10. **Tests did not establish their claims.** Every old worker yielded; a
    computed marker address was never actually written or checked against RSP.
    The preemption counter counted a selection, not observed worker execution.
    The two 'broken' variants only inverted accounting or raised an assertion
    threshold. Replaced with non-yielding busy workers, actual saved IRQ RIP/RSP,
    register/flag preservation, repeated dispatch and genuinely broken code.
    The host now cross-checks final PMM/VM/heap statistics, bounds numbers,
    requires the actual completion delimiter and ordered per-worker evidence.

11. **Misleading documentation and deferred identity.** README still described
    Stage 6 and no scheduler. Architecture and IRQ docs simultaneously claimed
    and denied scheduling/replacement. The report overstated stack writes and
    preemption proof. These are corrected; the original report is labeled
    historical/untrusted. Roadmap Stage 10 no longer promises user tasks before
    the protected-userspace milestone. Identity uses the unchanged canonical
    icon at a small README-title size and a real Stage 7 serial system line.

No empty scheduler stub or literal fake PMM backing was found. The principal
"slop" was unsafe ownership, incomplete error/context contracts, redundant guard
work, an unused misleading helper, assertion-only negative tests, and claims
not supported by what the tests executed.

## What proves the repaired behavior

| Invariant | Enforcement | Execution evidence | Failure behavior |
| --- | --- | --- | --- |
| Stack belongs to exactly one handle | Private frame registry, handle address, generation, full map/PMM preflight | Copies, stale generations, slot substitutions, foreign mapping and zeroed reuse | Reject before mutation |
| No frame release while mapped | VM unmap/invalidation before PMM release | Actual pool depletion with 0..7 frames, map conflicts, leak mutations | Rollback balances; internal cleanup failure halts |
| No stale thread slot identity | Non-reused ID lookup, internal slot selection | Three full seven-worker create/yield/exit/reap rounds, exhaustion/live/self/reaped checks | API failure, unchanged output/accounting |
| Correct current/context at transfer | IF=0 state transition, live checker, final handoff provenance/frame gate | Bad current, RSP, CS and IRQ return-pointer images | `[SCHED] failure=...`, no arbitrary return |
| Hardware preemption | IRQ0 frame save, EOI and selected IRETQ | No-yield/no-HLT worker loop; actual IRQ RIP inside ELF loop; real distinct RSP; GPR/DF/CF checks | Missing switching or corrupt register image fails |
| UP lock/IF discipline | IF=0, exact owner and handle, held-lock count | Nested IF=1/IF=0, recursive/copied unlock, yield/IRQ restrictions | Failure or controlled fatal misuse |

The measured main phase has bootstrap and three workers for 24 serviced IRQs:
each worker is preempted/dispatched six times. A second phase has bootstrap plus
one worker for 24 IRQs (12 worker preemptions), and a final bootstrap-only phase
has 24 IRQs with zero switches. Total scheduler switches: 48; scheduler IRQs: 72.
No elapsed-time fairness is claimed. Workers can starve each other by holding IF=0.

Guard-read and stack-execute images use existing real assembly load/jump probes.
The host checks CR2, error=0 for the absent guard or error=17 for NX, RIP against
the linked fault symbol (or target), and unexpected-fault halt. No general
recovery is armed. A failure earlier in boot/PMM/VM cannot satisfy a Stage 7 test:
the Stage 7 start marker and exact intended failure are required.

An early repair's flag validator rejected a real QEMU IRQ frame with RF=1
(`RFLAGS=0x10246`). Architectural review confirmed RF is valid saved state;
the validator preserves RF instead of weakening selector/privilege checks.
The wrong-version negative test was narrowed to the exact legacy boot line
because the legitimate new system identity also contains the version string.

Final review also caught a defect in the audit's own initial handoff validator:
it accepted only IRQ0, incorrectly rejecting other PIC vectors, including the
existing spurious-IRQ paths. A QEMU INT 39 probe reproduced `handoff_frame`.
Validation now accepts vectors 32..47, while only IRQ0 schedules. The ordinary
self-test executes INT 39/47 with clear PIC ISR and verifies IF/state on return;
a timer-only-validator mutation must fail. This proves CPU frame/IRETQ handling
for software-triggered spurious paths, not external IRQ7/15 device delivery.

## Branding and scope

The original 1254x1254 RGBA PNG at `assets/branding/icon.png` is unchanged.
README renders it as a 56-pixel icon within the title, not a giant pasted image.
The canonical asset/package contract is documented in `assets/README.md`.
The guest adds `[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage7 kernel execution`
when entering the actual execution self-test, keeping the legacy Stage 1 prefix.
It does not display, decode or embed the PNG. Resource ZIP contents are unchanged.

## Remaining limitations

- One CPU, ring 0, shared kernel CR3; no processes, TLS/segment switching,
  SIMD/FPU state, user isolation, priorities, sleep/wake or blocking join.
- Seven worker slots, eight available stack slots, four payload pages per stack.
  Handles/output pointers are trusted kernel storage. Generation exhaustion fails
  rather than wraps; arbitrary malicious corruption of private registries is not
  a supported recovery/security boundary.
- No TSS/IST emergency stack. Guard access from a healthy stack is diagnosed;
  actual exhausted-stack exception delivery can still double/triple-fault, and
  a sufficiently large stack jump can skip the guard.
- NMI remains masked; no nested scheduling or SMP synchronization. UP lock
  operations are checked nonblocking acquisition, not multiprocessor spin locks.
- Boot is an exhaustive bounded test then halt, not a production run loop.
  QEMU TCG evidence does not establish physical-hardware/KVM behavior or wall-clock
  fairness. Delayed PIT edges may coalesce.
- Existing PMM low-metadata placement and initial VM low-frame availability
  restrictions remain. The heap is fixed 64 KiB; the icon is packaged, not rendered.
- No input driver, shell, filesystem, language compiler or later milestone added.

## Final verification

All commands below exited zero against the frozen repaired implementation:

| Command | Result |
| --- | --- |
| `python tools/build/build.py build` | PASS |
| `python tools/build/build.py boot-test` | PASS, real serial execution |
| `python tools/build/build.py test` | 64 repository tests PASS |
| `python tools/build/build.py integration-test` | 53 integration tests PASS |
| `python tools/build/build.py validate` | PASS |
| `python tools/build/build.py check` | 64 repository + 53 integration tests PASS |
| `python -B -m unittest discover -s tests/repository -p 'test_*.py' -v` | 64 tests PASS |

There are 117 distinct host test cases, not 117 independent hardware proofs.
The 53 integration cases include 19 scheduler cases; the normal scheduler case
boots the same image three times. Negative cases deliberately break switching,
frame release, rollback, owner binding, ID/state checks, context registers,
guard permissions and IF preservation, or inject invalid current/RSP/CS/handoff
values. They must reach the Stage 7 test and fail for the intended reason, without
the success/post-accounting markers. Real guard/NX faults are checked separately.

QEMU runs passed at 8, 16, 64, 128, 256 and 512 MiB, with the existing high-memory
layout and maximum-CPU variants. The no-NX variant correctly rejects VM setup
before Stage 7; it is not counted as a successful scheduler boot. Final allocated
PMM bytes were 106496 and VM table pages were 10 in the successful RAM variants.
Free bytes respectively were 7098368, 15486976, 65818624, 132927488, 267141120,
535568384. The split 64-MiB layout discovered RAM at physical 4294967296 and
allocated a test frame at 4328517632; its final free count was 65818624.

One observed 64-MiB transcript (loop iteration counts are genuinely variable):

```text
[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage7 kernel execution
[SCHED] self-test started
[SCHED] stacks ownership, guard mappings and reuse verified
[SCHED] real OOM and mapping rollback verified
[SCHED] IRQ nesting and lock contracts verified
[SCHED] lifecycle exhaustion, stale IDs and repeated reap verified
[SCHED] non-yielding timer probe started
[SCHED] worker=1 preemptions=6 dispatches=6 rsp=18446708889337483128 irq_rsp=18446708889337483128 irq_rip=34481
[SCHED] worker=2 preemptions=6 dispatches=6 rsp=18446708889337503608 irq_rsp=18446708889337503608 irq_rip=34481
[SCHED] worker=3 preemptions=6 dispatches=6 rsp=18446708889337524088 irq_rsp=18446708889337524088 irq_rip=34481
[SCHED] two-runnable ticks=24 switches=24
[SCHED] single-runnable timer return verified
[SCHED] final allocated_bytes=106496 free_bytes=65818624 table_pages=10
[TEST] scheduler self-test passed
[TEST] preemptions=48 runs=548101
[TEST] PMM post-IRQ accounting verified
```

Reproducibility tests passed. SHA-256 of the repaired artifacts:

| Artifact | SHA-256 |
| --- | --- |
| `boot.bin` | `049ffd049b25c134020a339e3ff21fb6155e00c2cb0779c34cacb00efcd2ef1d` |
| `rynorkernel.bin` | `7f952a123f564327bf01b0963bb9650f2be174e548c44ddcc16280014f475a41` |
| `rynorkernel.elf` | `b91e0379b935ec34a5d72c04a522a5a70d7ec1201419dcbdbd928674f82864ec` |
| `rynoros.img` | `382807e46b17f6b66925cbb6f21a2c2e23dbe130c337600cfc85f86a88d8f646` |
| `rynoros-resources.zip` | `8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30` |

Host versions: Python 3.14.3, NASM 3.02, Clang/LLD 23.1.0 (LLVM commit
`ea7d852a70e8bdfaf601d6626a760f9771b2c4b4`), QEMU 11.1.0
(`v11.1.0-12130-ge470268ff4`). GNU objdump 2.46.0.20260210 was used for
additional disassembly review, not added as a build dependency. Disassembly
confirmed the corrected frame offsets, real flags capture, shared IRETQ tail
and freestanding byte copy/zero loops. There was no QEMU process after the matrix.
Local full logs are `build/stage7-final-*.log`; fixtures/artifacts remain ignored.

Commit metadata and live remote equality are verified separately after committing;
passing tests alone does not establish a successful push. The final handoff gives
the resulting commit SHA rather than embedding a self-referential hash here.
