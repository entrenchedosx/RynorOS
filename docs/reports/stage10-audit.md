# Independent Stage 10 audit and repair

## State inspected

HEAD/main is `6306d2f2804a432f275a3e891ba120ec8222701a`. The incoming
working tree has 15 modified tracked files and 17 untracked files (directory
entries expanded), including Stage 11 shell implementation and harness changes.
No files were staged. No commit, push, amend or history rewrite was performed.
The initial review paused on the baseline mismatch below. The user subsequently
confirmed the other agent was paused; this audit resumed, preserving its work.

## Baseline mismatch

The requested scope is Stage 10. However, `kernel/core/main.c` includes
`shell.h` and calls `shell_self_test`; `tools/host/image.py` compiles both
shell sources; `boot_output.py` imports `shell_output.py`; and `qemu.py` adds
interactive shell input. The shell sources and host module are untracked and
absent from the required-file inventory used to construct test fixtures.
These are material Stage 11 changes, not isolated unused files. They have been
preserved. The user was asked whether another editor is active and whether to
isolate Stage 10 or preserve shell integration during this audit.

## Incoming verification (before repairs)

`python tools/build/build.py test` exited 1: 96 tests ran in 7.731 seconds;
unittest reported nine failures and one error (failure reports include
subtests). Copied command fixtures fail with
`ModuleNotFoundError: No module named 'shell_output'`, preventing intended
compile/link/error-path assertions from running. Evidence:
`build/stage10-audit-incoming-test.log`. The reported 96/96 result is not a
result for the incoming working tree. At that point no independent full
integration, QEMU matrix or reproducibility result had been established;
completed final results follow below.

## Initial source findings (subsequently repaired)

- **HIGH — kstring.c:** `kstr_cat` evaluates `dst[len]` before checking
  `len < cap`. When bounded length returns cap for an unterminated destination,
  the source expression accesses outside the declared extent. Optimization
  may remove this impossible-condition branch; that does not make the C
  expression a valid bounds contract. A guard-boundary probe is needed.
- **MEDIUM — kstring.c:** the formatter's `%s` measure uses
  `used + n + 1 >= cap`, rejecting exact fit, unlike its other specifiers.
  Empty `%s` into capacity one is also incorrectly rejected.
- **HIGH — krst.c:** overlap validation ignores the `out_len` object and
  computes range ends without overflow checks. A result length stored inside
  the output can overwrite a successfully produced result. Input/output
  lifetime, overlap and representable-range rules need explicit enforcement.
- **MEDIUM — kbuf.c / kbuf.h:** only some operations check count; cap/head
  invariants are inconsistent and zero cap can reach modulo. Header describes
  head as `0..count`, but it is a physical ring index `0..cap-1`.
- **TEST-QUALITY — runtime-test.c:** the alleged wrap test writes offsets
  zero through five in an eight-byte buffer; it never wraps. Recycle checking
  examines only seven of eight returned bytes. The wrap transcript is a
  literal, not emitted from the observed read buffer.
- **TEST-QUALITY — test_runtime.py / runtime_output.py:** the canned-output
  negative deliberately corrupts a digest. This proves rejection of a wrong
  digest, not rejection of an accurate canned transcript. Fixed known folds
  cannot by themselves establish worker execution. Claims that canned output
  cannot reproduce them are unsupported.
- **TEST-QUALITY — runtime-test.c:** each worker stores the constant ROUNDS
  after its loop, rather than a measured completed-call count; services use
  very short inputs and cooperative yields. Evidence of timer preemption
  inside runtime service execution has not been established by these tests.

The reported append-test correction is present: seven characters plus NUL fit
capacity eight; eight characters plus NUL do not. The dispatcher does reject
null output even with zero capacity, but the negative guest test exercises only
UPPER, not all three services. The worker-loop mutation now uniquely matches
the braced worker loop. These observations are source inspection, not new
runtime verification.

## Repairs and additional findings

The incoming snapshot is preserved in ignored `build/stage10-incoming-snapshot.zip`.
Stage 11 sources were retained, not redesigned: their self-test and objects are
now opt-in rather than part of the normal Stage 10 boot. Copied fixtures include
the preserved shell dependencies. The existing keyboard injection helper retains
its four-argument interface; the shell change had also broken that repository
negative test with a TypeError. No Stage 11 certification is claimed.

Independent copied-image probes produced `failure=audit_format_exact` and
`failure=audit_length_alias`. A fully accurate canned runtime transcript passed
the original QEMU verifier: `test_truthful_canned_runtime_is_rejected` failed
with `RuntimeError not raised`. These are actual emulator results in
`build/stage10-original-probes.log`, not merely source suspicions.

Further tracing found IRQ0 was masked by Stage 8 and never re-enabled in the
original runtime test. Its preemption claim was false: only cooperative yields
ran. Stage 10 now installs its bounded IRQ test handler, unmasks IRQ0, requires
two real in-service preemptions per worker, masks IRQ0 afterward, and verifies
IRQ-context rejection without output changes. Services themselves never change
interrupt state. The host independently checks physical worker records and
QEMU CPU IRQ trace against actual ELF code/stack extents. Neither fixed serial
text nor a physical record alone constitutes proof.

String repairs include one-past-end ordering, exact/empty `%s`, explicit-source
copy/append variants, overlap-safe moves instead of restrict memcpy, integer
pointer ordering, format/source bounds and alias preflight, and va_copy cleanup.
Formatter aliasing was a memory-safety defect: writing into its own format or
`%s` input could change the second walk after validation, while the old write
pass omitted capacity checks. Rejecting those aliases restores the two-pass
invariant; this is not merely a cosmetic API restriction.
Two-pass transactional formatting requires immutable valid caller objects.
Byte rings validate cap/head/count, reject backing-storage/metadata aliases and
wrapping extents, and define zero-byte/clear behavior. Dead unused overflow
statuses were removed. Runtime dispatch validates separate length storage,
pointer overflow/alignment, all overlaps, null output, 64-KiB request limits and
IRQ context. COUNT checks output capacity before scanning input.

Guest tests now place strings against a real unmapped guard, exercise capacity
1..9 rings through genuine wraps and inspect all bytes/sentinels. The original
wrap output was hardcoded; it is now emitted from the actual read buffer.
All services exercise invalid/empty/exact/undersized/overlapping requests;
repeated workers and actual PMM exhaustion test allocation independence,
creation failure and cleanup. Final statistics are queried after cleanup rather
than printed from the saved pre-test structure. Partial worker-group creation
also reaps its owned threads before reporting failure.

The larger naturally aligned evidence object exposed the linker's fixed 32-byte
BSS start alignment. BSS is now page-aligned, consistent with existing VM page
granularity. The first repaired link failed explicitly, not a tolerated warning.
Kernel data remain within the existing reserved low-memory linker region; no
new permanent allocated PMM frames are required. There is no hidden allocator.

## Verification methodology and boundaries

Production algorithms are original freestanding ring-0 code. Tests comprise
synthetic guest algorithms, actual guard mappings/faults, real resource
exhaustion, worker execution, independent physical-memory inspection and CPU
interrupt traces. Repository parser fixtures alone are not hardware evidence.
The additional QEMU `-d int` stream is host verification, not a device dependency
inside runtime services. No new compiler, library or host package is required.

The Stage 9 canned-display test can legitimately fail at the runtime accounting
gate: skipping display mapping leaves four fewer table frames than the forged
display record. Accepting that precise failure or the independent pixel failure
does not turn a pre-display crash into success. Other display no-op mutations
still require actual physical pixels/scanout; both gates remain in normal boots.
The first full regression also exposed a timing-dependent Stage 8 canned-output
test: the longer Stage 10 boot allowed all eight host keys to be sent, so the
forgery was rejected by missing data-port reads instead of the earlier host-input
count gate. The test now allows those two precise rejection reasons and always
requires the independent keyboard trace to reject the forgery, plus completion
of subsequent runtime/accounting stages. No generic early crash is accepted.

No physical-hardware certification, userspace, isolation, syscalls, ELF userspace,
filesystem or RynorLang execution is claimed. Caller-provided pointers must be
live/mapped and objects correctly sized; arithmetic checks are not protection
against malicious ring-0 callers. Shared mutable objects need caller locking.
No SMP contract exists. Bounded self-test attempt limits are not real-time or
physical-hardware performance guarantees. The preserved opt-in shell is outside
this Stage 10 audit.

## Final independently observed verification

All commands used Python 3.14.3, Clang/LLD 23.1.0, NASM 3.02 and QEMU 11.1.0.
Host overrides were `RYNOR_CLANG=D:\llvm-install\bin\clang.exe`,
`RYNOR_LLD=D:\llvm-install\bin\ld.lld.exe` and
`RYNOR_QEMU=C:\Users\aawad\scoop\apps\qemu\current\qemu-system-x86_64.exe`.
Direct integration imports used `PYTHONPATH=tests/integration`.

| Command | Final observed result | Evidence under `build/` |
| --- | --- | --- |
| `python tools/build/build.py build` | PASS, 1,048,576-byte image, 257 payload sectors | `stage10-repro-build1.log`, `stage10-repro-build2.log` |
| `python tools/build/build.py boot-test --timeout 30` | PASS; 4.75 seconds, monitor quit, exit 0, reaped | `stage10-observed-final-boot.log`, `boot-test/run.json` |
| `python tools/build/build.py test` | 100 tests, OK | `stage10-final-test.log` |
| `python tools/build/build.py integration-test` | 142 tests, OK, 740.230 seconds | `stage10-verified-integration.log` |
| `python tools/build/build.py validate` | PASS | `stage10-final-validate.log` |
| `python tools/build/build.py check` | PASS: 100 repository + 142 integration tests | `stage10-verified-check.log` |
| `python -B -m unittest discover -s tests/repository -p 'test_*.py' -v` | 100 tests, OK, 20.190 seconds | `stage10-direct-repository.log` |
| `python -B -m unittest test_runtime test_keyboard.KeyboardTests.test_canned_success_output_cannot_prove_hardware -v` | 35 tests, OK: 34 Stage 10 + 1 cross-stage forgery test | `stage10-verified-targeted.log` |

The full check integration run independently took 782.141 seconds. Counts are
unittest methods, not counts of every guest assertion, subtest or distinct boot.
The Stage 10 suite has five positive and 29 negative/mutation methods. The first
full 139-method integration attempt had two failures from the overly specific
keyboard rejection reason and the now-earlier binary-UPPER failure assertion.
Those tests were corrected to require the actual targeted failure, then the
expanded suite passed twice. Earlier failures are retained in the audit logs,
not presented as successful verification.

The regression matrix includes 8, 16, 64, 128, 256 and 512 MiB, `qemu64` and
`max` CPUs, actual firmware RAM above 4 GiB with a low-RAM hole, and the existing
missing-NX fail-closed configuration. Stages 1–9 positive and negative tests
passed. Successful and intentional-failure QEMU runs require monitor-quit,
exit 0 and reaping. The final process inspection found zero QEMU processes.
Physical hardware was not tested.

## Mutation results

Every following negative was detected in the final Stage 10 suite; each copied
image is modified with an exact single-occurrence matcher, leaving production
sources unmutated. Negative tests require reaching Stage 10 and the relevant
failure, not an arbitrary early boot crash.

| Broken behavior | Detection |
| --- | --- |
| Wrong FNV constant; wrong worker fold; no-op UPPER; no-op COUNT | Independently recomputed result or specific guest result assertion |
| Destination copy bounds removed; partial ring write allowed | Overflow/no-partial assertions |
| Worker rounds bypassed | Measured round count/result mismatch |
| Inaccurate canned output; accurate canned output | Host fold check; missing physical execution records |
| IRQ0 disabled during services | Bounded `service_preemption_missing` failure |
| Physical evidence cleared | Mandatory physical record validation |
| IRQ-context rejection removed | Actual IRQ service-call check |
| Source terminator bound removed; explicit source extent ignored | Source/termination boundary assertions |
| Exact-fit formatter rejection reintroduced | Exact-capacity success assertion |
| Ring head validation removed; wrong stride; capacity incremented | Invalid metadata, wrap and sentinel checks |
| Length-output alias allowed; NULL/zero output accepted | Dispatcher failure/unchanged-output assertions |
| Partial worker creation forced after three successes | Required rollback and balanced accounting before named failure |
| Worker reap skipped; guard frame leaked | Real PMM/VM/heap accounting checks |
| Worker bypasses dispatcher | Missing in-service hardware preemption evidence |
| CPU IRQ trace omitted | Mandatory independent trace correlation |
| Explicit one-past-end guard read | Actual page fault at CR2 `0x40001000` |
| Formatter destination checks removed; format alias check removed | Overflow/alias unchanged-buffer checks |
| Both accurate serial and plausible physical records forged | Missing actual in-service CPU IRQ deliveries |

These are regression sensitivity tests, not proof against a malicious kernel
that deliberately executes fake workload to forge evidence, or a compromised
emulator/verifier. The physical record is not an authentication mechanism.

## Concrete execution and accounting evidence

The final normal boot reported the following exact runtime block:

```text
[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage10 basic kernel runtime
[RUNTIME] self-test started
[STR] fmt0="rynor 42 2a" fmt1="334" fmt2="FF"
[STR] strings, bounds, overlap and formatting verified (synthetic)
[BUF] wrap="cdef"
[BUF] buffers, wrap, capacity and bounds verified (synthetic)
[SVC] digest, uppercase and count services verified (synthetic)
[RUNTIME] dispatch rejects invalid, overlapping and undersized requests
[RUNTIME] worker=0 acc=0x96B2B2353F662800 rounds=40
[RUNTIME] worker=1 acc=0xD69325FB76935220 rounds=40
[RUNTIME] worker=2 acc=0x341E12E7365A67D0 rounds=40
[RUNTIME] worker=3 acc=0x5AE70CEE0E562640 rounds=40
[RUNTIME] worker=4 acc=0x780567CB064E63F0 rounds=40
[RUNTIME] worker=5 acc=0x7C09AC01C41EC7B0 rounds=40
[RUNTIME] worker=6 acc=0x8BC68E3994B8CCD0 rounds=40
[RUNTIME] total=8944318237794893984
[RUNTIME] worker digests and round counts verified under preemption
[RUNTIME] final allocated_bytes=122880 free_bytes=65802240 table_pages=14
[TEST] runtime api verified
[TEST] PMM post-IRQ accounting verified
```

The 504-byte physical record contains seven workers, IDs 50–56, each with two
observed in-service preemptions. Worker zero: stack `0xffffe00000000000`, saved
RIP `0x8793`, RSP `0xffffe00000003ed0`, independently recomputed 4096-byte probe
digest `0x88d6ce5789d35325`, 2952 attempts. QEMU CPU records 145 and 159 both
independently report `v=20 e=0000 i=0 cpl=0 IP=0008:0000000000008793`, matching
PC and `SP=0010:ffffe00000003ed0`. Other workers have distinct validated stacks
and matching CPU events. Attempt counts/event IDs vary with timing and are not
hardcoded success criteria.

The displayed totals are the same as the immediately preceding Stage 9 display
baseline, not a new fixed RAM assumption. Temporary worker/guard/table frames
are reclaimed. Services allocate no heap or physical memory. Guest checks also
compare heap state and PMM/VM consistency before and after repeated lifecycle,
OOM and partial-creation paths. No tested lifecycle leak remains.

## Reproducibility and final repository state

Two consecutive builds produced identical SHA-256 values for all six artifacts:

```text
boot.bin             8e4c996c8cd71cccdb0555649800e97e99a2594175b3ffb556db388dfd083be2
rynorkernel.bin       8c88c01f7d65367056939d78023da264a3c1e9c9cb6606404cfaeaa0e96b154c
rynorkernel.elf       1c5c56b20bde4141ffa53f295b9a7e403fb03fdfa0e86d19d6100be8f165e8a3
rynoros.img           3c916030012d9273af7cd34eb4b31e885cfba6a411066e0028f10266c3f9220a
rynoros-resources.zip 8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30
build-manifest.json   8126b7eaa716582730420130c63972bb63a1c2ddb64ee121395f2dc56ca06a7b
```

HEAD remains `6306d2f2804a432f275a3e891ba120ec8222701a`, branch `main`.
Final working tree: 19 modified tracked files, 21 untracked individual files,
zero staged files. This includes preserved incoming work, not only audit edits.
`git diff --check` passes. Commit created: NO. Push performed: NO. History
rewritten: NO. Ignored build logs/evidence are local artifacts, not committed
test fixtures. No GitHub synchronization claim is made for these changes.

## Final verdict

**VERIFIED WITH LIMITATIONS.** The repaired Stage 10 contract is defensible
under the tested single-CPU QEMU environment. The incoming completion report
was not: source boundary defects, false preemption claims, broken fixtures and
an accurate canned-output bypass were independently reproduced and repaired.
No unresolved material Stage 10 defect is known from this audit. This is not a
production-readiness certificate or proof of all possible inputs/interleavings.
Trusted pointer extents, immutable format inputs and caller synchronization are
explicit API preconditions. Physical hardware and SMP are unverified; protected
tasks, syscalls, filesystems and RynorLang are absent. Preserved Stage 11 shell
work is neither included in normal Stage 10 execution nor independently approved.
