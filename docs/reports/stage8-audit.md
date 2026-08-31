# Independent Stage 8 audit

## Scope and repository

Baseline HEAD: `061238e48d9594f75aeb5a53deb91a660025e269`, main.
The incoming Stage 8 work was uncommitted: 18 modified tracked files and seven
untracked files. It was not a clean checkout. No Git commit, amend, reset, push,
branch change or configuration change is part of this audit.

Reviewed the entire incoming diff and all keyboard source/tests, initialization,
PIC/IRQ/frame contracts, Stage 7 scheduling, PMM/VM/heap contracts, build wiring,
parser fixtures, image creation, monitor injection and documentation.
No Stage 7 scheduler/stack/context-switch implementation changes were needed.
No Stage 9 implementation was added.

## Baseline evidence, not assumed claims

The unmodified incoming `check` passed **71 repository and 58 integration tests**.
Log: `build/stage8-audit-baseline.log`. The source snapshot is retained in ignored
`build/stage8-incoming.zip`.

An independent temporary-image mutation replaced every stored byte in the local
overflow test with zero, leaving subsequent real ISR bytes unchanged. The
original full keyboard boot still passed. This proves that the advertised
drop-newest test did not verify retained data/order. Evidence:
`build/stage8-original-probe.log`, `build/stage8-original-ring-corruption/`.
The actual single-producer FIFO algorithm was not found to overwrite data on its
normal path; the defect here was missing proof, not an invented ring off-by-one.

## Findings and repairs

No confirmed critical memory-corruption defect was found in the exercised boot.
The following issues were significant:

| Severity | Location | Problem / why it matters | Evidence | Repair |
| --- | --- | --- | --- | --- |
| High | original keyboard.c i8042_initialize | `(value & 0xc4) | 0x3b` inherits translation, enables auxiliary IRQ and sets unrelated bits; never selects device scan set/scanning. Works with the tested BIOS state, not a guaranteed protocol setup. | Command-byte bit review and QEMU model; cold-translation test. | Explicit bounded 74h/65h config/readback, controller/interface tests, F5/F0/02/F4 with ACK/RESEND rules. |
| High | original kbd_isr | Unconditionally reads 60h without OBF/AUX/parity/timeout checks. Stale/error/auxiliary bytes can become keyboard input. | Direct source path; new real-IRQ status/no-read/discard mutations. | Read status first, conditional read, separate empty/aux/error counters, loss notification. |
| High | original kbd_decode | Stateless byte decoding recognizes the 1c tail of E0 keypad Enter as ordinary Enter. Unsupported does not mean safe to reinterpret. | Prefix sequence analysis and exact decoder tests. | Stateful E0 suppression and bounded E1/Pause validation with documented malformed reset. |
| High | original ring_synthetic_test | Counts accepted/dropped/drained bytes but never checks values or FIFO order. | Corrupted retained contents passed original QEMU verification. | Local-instance tests for every occupancy, exact retained content, wraps/reuse, loss boundaries, invalid indexes and counter exhaustion. |
| Medium | original kbd_pop_raw / API | No interrupt exclusion or explicit consumer ownership. A preempted read/advance can race another caller; volatile does not serialize whole operations. Current single-consumer test did not expose it. | Source/API review; IF and IRQ-context tests. | Serialize complete poll/decode and statistics with existing irq_save/restore; reject IRQ polling. |
| Medium | original flush / init failure | Flush can loop forever; registration precedes setup and poisons an undocumented retry. Failed hardware state is not explicit. | Unbounded while loop and duplicate-registration path. | Bounded waits/flush, terminal FAILED state, phase diagnostic, PIC-masked failure, best-effort bounded device disable. |
| Medium | original raw loss handling | Dropped bytes have no event-level discontinuity contract; future stateful decoding/modifier clients can silently continue across a gap. | Original API exposes only cumulative dropped count. | Per-sample epoch, explicit LOST at the correct stream boundary, decoder reset; clients must clear their own pressed state. |
| High | original test_keyboard.py | The mandatory-count mutant just changes the assertion threshold; the alleged press/release swap removes make recognition and fails the synthetic test. Neither proves its claimed real-input property. | Actual mutation bodies. | Omitted live read accounting, post-decoder runtime swap, queue/decoder bypass and replay variants; distinguish host versus guest rejection. |
| Medium | original kbd_output / boot_output | Keyboard free-memory count is page-aligned but never compared with prior-stage accounting despite documentation claiming cross-checks. | A different aligned value passes original parser. | Compare all three retained resource totals with parsed scheduler state; parser negative test. |
| Medium | original qemu.py | Unvalidated key strings reach HMP; absent stdin causes an infinite continue loop; no independent I/O evidence. | Helper/control-flow review. | Allowlisted input, bounded one-command handshake, missing-monitor error, input ledger and QEMU device/PIC/data-read trace gate. |
| Documentation-only | README/design/report | Says ISR leaves interrupts enabled, calls emulated keys physical, overclaims overflow proof and timing-free operation; current counts link Stage 7. | Contradicts interrupt-gate IF=0 and actual test code. | Corrected support, concurrency, timing, evidence and limitations; preserved historical audit context. |

No empty keyboard stub or mock-backed production allocator was found. Synthetic
tests were labeled, but mixed into the live ring/counters. They now use local
objects and cannot substitute for the private IRQ queue.

## Invariants and failure behavior

- Queue: exactly 31 usable samples; full put cannot overwrite older samples.
  Tests verify byte values/order at every occupancy and through repeated wraps.
- Ownership: only the private ISR produces real input. Public poll serializes
  pop/decode with IF=0 and preserves caller IF. IRQ callers are rejected.
- Loss: retained pre-gap bytes drain unchanged; LOST precedes post-gap bytes.
  Missing bytes and modifier state are not reconstructed or silently invented.
- Initialization: explicit scan contract established before READY; failed state
  never unmasks IRQ1 or silently retries. Broken hardware cleanup is best effort;
  software PIC masking is mandatory.
- Hardware proof: host-selected event order, real scan values, decoded identities,
  IRQ/read/error/drop counters, and independent emulator event -> IRQ1 ack -> I/O
  read ordering must all agree. A guest success marker alone is insufficient.
- Resources: temporary concurrent worker is reaped; exact PMM/VM/heap totals and
  live scheduler invariants must return to their pre-keyboard baseline.

## Test methodology and honesty boundaries

The same image boots with the default sequence, a host-side random permutation
chosen after compilation (recorded in run.json), and shifts/repeated/UNKNOWN input.
The default eight keys produce sixteen bytes; the guest contains no expected key
order. Trace checks come from QEMU, not guest printf. Three trace events are
required, with missing evidence treated as failure.

IRQ0 concurrently preempts one busy worker while IRQ1 wakes/delivers input.
No Stage 7 code was redesigned to obtain this result.

Mutations affect real implementation logic or explicitly documented fault inputs.
They are built only in temporary copied repositories, not compiled as production
feature switches. Host-detected semantic failures may include a guest success
marker: the test must prove that the host rejects it, not falsely require a guest
panic. State/queue/controller failures require the relevant keyboard-stage marker
and intended failure, not an earlier CPU/PMM/scheduler crash.

Status-error/AUX/OBF mutations inject status faults in a real IRQ handler; they
verify rejection logic, not electrical parity-error generation on hardware.
Synthetic prefix/FIFO tests execute the real algorithms with local data; they
are not claimed as physical key delivery or hardware ring-exhaustion evidence.
All verification has limits: arbitrary coordinated rewrites of kernel, emulator
and verifier are not ruled out by software tests. This is not attestation.

## Final verification

Run on 2026-08-31 against the final source/tests, not cached transcripts:

| Command | Observed result |
| --- | --- |
| `python tools/build/build.py build` | PASS, compile/link/image/resource package |
| `python tools/build/build.py boot-test` | PASS, actual QEMU input and trace gate |
| `python tools/build/build.py test` | PASS, 74 repository tests |
| `python tools/build/build.py integration-test` | PASS, 78 integration tests; 83 QEMU launch commands recorded |
| `python tools/build/build.py validate` | PASS, structure/metadata/icon |
| `python tools/build/build.py check` | PASS, rebuild plus 74 repository and 78 integration tests again |
| `python -B -m unittest discover -s tests/repository -p 'test_*.py' -v` | PASS, 74 tests after the matrix |
| `python -B -m unittest discover -s tests/repository -p test_kbd_output.py -v` | PASS, 10 keyboard parser/harness tests |
| `python -B -m unittest discover -s tests/integration -p test_keyboard.py -v` | PASS, 25 keyboard integration tests |

74 + 78 = **152 distinct discovered tests**, not a count inflated by repeated
runs. The keyboard-specific cases are included in those totals. Final matrix
logs are `build/stage8-final-{build,boot-test,test,integration-test,validate,check}.log`
and `build/stage8-final-direct.log`; targeted results are in
`build/stage8-keyboard-targeted.log`. The separate direct repository run is also
preserved in `build/stage8-repository-direct.log`.

Earlier-stage cases passed, including actual exception/ELF state comparisons,
RO/NX/page-fault/TLB failures, PMM/VM/heap accounting and OOM, and Stage 7 stack,
handoff, lifecycle and non-yielding preemption mutations. Normal boots cover
8/16/64/128/256/512 MiB RAM, the additional `max` CPU and firmware RAM above
4 GiB with the 32 MiB below-4G configuration. The NX-disabled CPU fails closed
as expected. Independent-directory byte-identical rebuild comparison passed.

Keyboard negative cases now include real unexpected controller/interface replies,
an actual keyboard ECHO reply instead of ACK, and an unsupported device command
that produces **exactly three FE RESEND replies**, followed by controlled failure.
The latter count is independently checked in QEMU's data-read trace. Masked IRQ1,
discard/no-read, runtime press/release swap, decoder/queue bypass, FIFO corruption,
overwrite/capacity/wrap errors, loss omission, status errors, no host input and
synthetic/canned success substitutions are rejected. The translation-disabled
firmware-state variant passes because initialization establishes its own contract.

After the final matrix, all **81 retained run.json records updated during that
matrix** recorded `monitor-quit`, return code 0 and `reaped=true`; this is not the
launch count (some temporary logs are removed and shared paths are overwritten).
An independent `Get-Process -Name 'qemu-system-*'` check found no QEMU process.

Dependencies were unchanged: Python 3.14.3, NASM 3.02, Clang/LLD 23.1.0
(LLVM ea7d852a70e8bdfaf601d6626a760f9771b2c4b4), QEMU 11.1.0
(v11.1.0-12130-ge470268ff4), bundled SeaBIOS. The documented `RYNOR_CLANG`,
`RYNOR_LLD`, `RYNOR_QEMU` overrides selected the installed executables.
QEMU uses `pc-i440fx-10.0`, TCG, `qemu64`, one CPU, snapshot IDE raw image,
serial file, HMP monitor, no display/network, `-no-reboot`, and the three
documented trace events. Input requests have a 10-second default overall bound;
keyboard negative variants use three seconds. No dependency is guest OS code.

Final artifact SHA-256 values (also in `build/build-manifest.json`):

```text
rynoros.img (1048576 bytes)
9bda00d2263a693301a06cd3ecf5d077e8e5a3ee606db9b7607b7faf9f356762
rynorkernel.bin (81952 bytes)
140504eb3bc2baaf70b00422f6730d1c20b5216691d73e21bf869f09be11c5ff
rynoros-resources.zip (575058 bytes; canonical icon unchanged)
8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30
```

### Observed default keyboard transcript

From the final `build/boot-test/serial.log`, after the Stage 1–7 transcript:

```text
[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage8 hardware input
[KBD] self-test started
[KBD] queue FIFO, capacity, wrap and loss verified (synthetic)
[KBD] Set-1 subset and prefix isolation verified (synthetic)
[KBD] i8042 configured, Set-2 translated to Set-1, irq1 enabled
[KBD] waiting for input=0
[KBD] event=0 scan=30 key=30 type=1
[KBD] event=1 scan=158 key=30 type=2
[KBD] waiting for input=1
[KBD] event=2 scan=48 key=48 type=1
[KBD] event=3 scan=176 key=48 type=2
[KBD] waiting for input=2
[KBD] event=4 scan=46 key=46 type=1
[KBD] event=5 scan=174 key=46 type=2
[KBD] waiting for input=3
[KBD] event=6 scan=32 key=32 type=1
[KBD] event=7 scan=160 key=32 type=2
[KBD] waiting for input=4
[KBD] event=8 scan=57 key=57 type=1
[KBD] event=9 scan=185 key=57 type=2
[KBD] waiting for input=5
[KBD] event=10 scan=28 key=28 type=1
[KBD] event=11 scan=156 key=28 type=2
[KBD] waiting for input=6
[KBD] event=12 scan=30 key=30 type=1
[KBD] event=13 scan=158 key=30 type=2
[KBD] waiting for input=7
[KBD] event=14 scan=32 key=32 type=1
[KBD] event=15 scan=160 key=32 type=2
[KBD] irqs=17 reads=16 received=16 dropped=0 errors=0 auxiliary=0 empty=1
[KBD] concurrent timer_ticks=40 worker_runs=895800
[KBD] final allocated_bytes=106496 free_bytes=65818624 table_pages=10
[TEST] keyboard input verified
[TEST] PMM post-IRQ accounting verified
```

Worker/timer counts are observations, not fixed expected timing values. The
seventeenth IRQ is the separately counted startup empty IRQ, not an invented
keyboard byte. The trace requires sixteen device/IRQ1/data-read chains after
input begins. Alternate input also verified left/right Shift, repeated keys and
UNKNOWN x (`scan=45/173 key=0 type=0`) without recompiling the normal image.

### Final repository / verdict

**Verified with limitations**: the repaired bounded QEMU keyboard contract is
supported by reviewed implementation, behavioral tests and independent trace
evidence. The incoming report was not independently defensible as written.
This does not certify arbitrary hardware, complete text input or a production OS.

HEAD remains `061238e48d9594f75aeb5a53deb91a660025e269` on main. Final review
covers 20 modified tracked files and 10 untracked additions, all intentional and
unstaged. `git diff --check` passes. No commit, push, amend or history rewrite
was performed; the tree is intentionally **not clean**, as requested.

Final changed-file inventory, relative to `D:\RynorOS` (includes incoming
OpenCode work as well as audit repairs):

```text
Modified tracked files:
ARCHITECTURE.md
README.md
ROADMAP.md
docs/design/irq-timer.md
docs/design/virtual-memory.md
kernel/README.md
kernel/core/main.c
project.json
tests/README.md
tests/integration/test_boot.py
tests/integration/test_heap.py
tests/integration/test_pmm.py
tests/integration/test_scheduler.py
tests/integration/test_vm.py
tests/repository/test_pmm_output.py
tests/repository/test_timer_output.py
tools/host/boot_output.py
tools/host/image.py
tools/host/qemu.py
tools/host/repository.py

Untracked additions:
docs/design/keyboard.md
docs/reports/stage8-audit.md
docs/reports/stage8.md
kernel/drivers/keyboard-internal.h
kernel/drivers/keyboard-test.c
kernel/drivers/keyboard.c
kernel/include/kbd.h
tests/integration/test_keyboard.py
tests/repository/test_kbd_output.py
tools/host/kbd_output.py
```

Final documentation was completed after the frozen-source runtime matrix;
repository validation, direct keyboard parser tests and diff checks were rerun.
No kernel, host-tool or test code changed after that matrix.

## Remaining limitations

- QEMU PC/SeaBIOS, single CPU, legacy i8042/PIC; no physical-machine validation,
  USB, APIC keyboard routing, mouse support or SMP.
- Eight ordinary physical key identities, not a full Set-1 repertoire. E0/E1
  sequences are isolated/suppressed, not implemented as full extended keys.
- No character layout/case conversion, pressed-key/modifier state, LED commands,
  key-repeat synthesis/policy, runtime command arbitration, hotplug/reconnect.
- Fixed 31-sample drop-newest capacity. Clients must react to LOST; lost prefixes
  and releases cannot be recovered. The first post-gap byte can be ambiguous.
- Initialization is one attempt, with explicit terminal failure; poll budgets are
  not calibrated real-hardware timeouts. Guest input waiting relies on the bounded
  external QEMU watchdog.
- Unchanged Stage 7 limits include ring-0 shared address space and no emergency
  exception stack. No shell, display subsystem or usable interactive OS exists.

See [keyboard design](../design/keyboard.md) for API and protocol references.
