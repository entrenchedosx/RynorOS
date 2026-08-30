# Stage 3 — external interrupts and hardware timer

Historical snapshot of commit `726180c0559a64f46616e7944f73a9d542732b6a`.
Stage 4 supersedes current-status/next-milestone statements below; see `stage4.md`.

Implementation and verification report, 2026-08-30. Stage 3 builds on Stage 2
commit `c6a353e`; no Stage 4 work is included. The source and this report are
committed together as the Stage 3 milestone.

## Implemented scope

Original PIC initialization/remapping/masking/EOI, 16 hardware IRQ gates sharing
the proven register-save/IRETQ mechanism, separate C IRQ dispatch and controlled
registration, and PIT channel 0 mode 2 at divisor 11932. QEMU input clock
1193182 Hz gives exactly 1193182/11932 Hz (approximately 99.9984914516 Hz).
Three real IRQ0 deliveries supply counter samples printed by foreground code;
the handler masks IRQ0 after the third. It neither allocates nor prints serial.
Completion requires return to foreground, clear ISR and all PIC lines masked.

See [IRQ/timer design](../design/irq-timer.md) for ports, ICWs, numbering,
IDT/frame integration, IF assumptions, registration, tick accounting, spurious
IRQ7/15 behavior, EOI order and complete invariants. CPU exception mappings and
diagnostics remain in [CPU design](../design/cpu.md). No scheduler exists.

## Icon integration

The previously untracked root `icon.png` is now the canonical official icon at
`assets/branding/icon.png`, identified in `project.json` and `assets/README.md`.
The original 1254 x 1254 RGBA PNG is preserved byte-for-byte (574499 bytes;
SHA-256 `beac0bc23e59cdad3ddbbddcee9cb7d9444c90c15654a4f9c520d7ce61c6b353`).
`build/rynoros-resources.zip` carries it plus a content manifest with normalized
ZIP metadata. The kernel/boot image contains no raster bytes. This is a separately
packaged OS asset, **not rendered, loaded by the guest, or converted**. There is
no graphics subsystem, PNG decoder, archive reader or filesystem. A future
graphical stage can consume the original; small-icon variants remain future work.

## Dependencies and QEMU configuration

Unchanged host bootstrap dependencies: Python standard library (now also
`zipfile`/`struct` for resource packaging), NASM, Clang, LLD, QEMU and its SeaBIOS.
No packages installed or dependencies downloaded. These tools are not part of
the guest OS. Version/provenance details: [bootstrap dependencies](../design/bootstrap-dependencies.md).

Pinned emulator: `pc-i440fx-10.0`, `qemu64`, TCG, 64 MiB, one CPU, `bios-256k.bin`;
IDE raw disk in snapshot mode, no display/VGA/network/parallel port. The command
uses `-serial file:build/boot-test/serial.log -monitor stdio -no-reboot` plus
`-d guest_errors -D build/boot-test/guest-errors.log`. Full resolved command,
owned PID, exit code and cleanup are recorded in `build/boot-test/run.json`.
Default marker deadline is ten seconds; negative runs use two seconds. The
runner sends monitor `quit`, waits, and has bounded terminate/kill fallbacks;
fallback cleanup is not accepted as a successful run.

## Verification

Commands (from `D:\RynorOS`, using the documented host executable overrides):

```text
python tools/build/build.py validate
python tools/build/build.py build
python tools/build/build.py boot-test
python tools/build/build.py test
python tools/build/build.py integration-test
python tools/build/build.py check
python -B -m unittest discover -s tests/repository -p test_*.py -v
```

Final results: **all commands exited 0**. Both the individual integration command
and combined check passed after the monitor-input fix. Direct repository discovery
also passed. `git diff --check` reported no whitespace errors; a final host process
scan found **zero QEMU processes**. Retained current-run summaries show
`cleanup=monitor-quit`, exit 0 and `reaped=true` on normal and expected-failure runs.
Stage 3 implementation/verification gates are satisfied; the milestone commit
contains the intentional source/document/asset changes, not generated output.

The repository suite contains **40 tests**, retaining all 33 Stage 2 tests and
adding four strict timer-parser cases and three original-asset/package cases.
The integration suite contains **13 tests**, retaining all 11 Stage 2 cases and
adding two real timer-negative kernel builds. There are no skipped tests.

The normal boot test verifies the GDT/IDT markers, complete breakpoint state,
GPR/RFLAGS/RSP restoration, linked ELF RIP, controller/timer setup, three ordered
timer samples, and final completion. IRQ0's ISR bit is checked before dispatch;
the final foreground check requires ISR=0 and all IRQs masked. Missing master
EOI demonstrably stops after one real tick. Masked IRQ0 demonstrably yields no
ticks. Both negative images must time out rather than succeed, and leave no
child process behind. #DE/#DB/#UD/#GP/#PF and the unarmed breakpoint still use
real exception instructions; fatal variants intentionally stop before the timer.

All generated artifacts, including the original-byte resource package, are
compared across independent build directories. Canonical icon hash/dimensions,
ZIP members, CRCs, timestamp and Unix permissions are also checked. The package
does not affect the 1 MiB boot-image layout or insert PNG data into the payload.

### Exact observed default serial output

Line endings on COM1 are CRLF; shown here as text. RIP/RSP are real captured
values for this linked build, not values invented by the serial formatter.

```text
Rynorkernel booted.
RynorOS 0.1.0 | x86_64 | stage1
[CPU] GDT initialized
[CPU] IDT initialized
[TEST] triggering controlled exception
[EXCEPTION] vector=03 name=breakpoint error_source=synthetic error=0x0000000000000000
[STATE] rip=0x000000000000837b cs=0x0000000000000008 rflags=0x0000000000000402 rsp=0x000000000007ffa8 ss=0x0000000000000010
[GPR] rax=0x0000000000000101 rbx=0x0000000000000102 rcx=0x0000000000000103 rdx=0x0000000000000104
[GPR] rbp=0x0000000000000105 rsi=0x0000000000000106 rdi=0x0000000000000107 r8=0x0000000000000108
[GPR] r9=0x0000000000000109 r10=0x000000000000010a r11=0x000000000000010b r12=0x000000000000010c
[GPR] r13=0x000000000000010d r14=0x000000000000010e r15=0x000000000000010f
[EXCEPTION] action=resume
[TEST] exception handling verified
[IRQ] controller initialized
[TIMER] initialized
[TIMER] clock_hz=1193182 divisor=11932 mode=2
[TEST] waiting for timer interrupts
[TIMER] tick=1
[TIMER] tick=2
[TIMER] tick=3
[TEST] timer interrupt handling verified
```

### Artifact identity

The payload occupies 14 sectors and remains inside the 32 KiB fixed load/BSS
window. These hashes are for the normal returning-breakpoint/timer image, not
the intentionally broken negative fixtures:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `boot.bin` | 512 | `b708ad3be65f5ce0516efab68ecbe672208682d7415f30817af843a4c75af71e` |
| `rynorkernel.bin` | 7106 | `592675ef1541915716d9bf37df02f3b444a659215e3532458ccb71e27ad07b81` |
| `rynorkernel.elf` | 16448 | `8cb54b4525bef8abb70fc0fb6529bcde496c767cdf9d0d8de06466e9062a9006` |
| `rynoros.img` | 1048576 | `28b527a269da63523a1e9b0f47c15c336ce12e2330979d88611bc7b3bf98728e` |
| `rynoros-resources.zip` | 575058 | `8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30` |

### Failure investigated during verification

A repeated masked-IRQ negative test correctly waited without ticks, but QEMU
needed forced termination after the three-second monitor-quit deadline. Its
monitor log showed `quit` input without normal exit. The old harness used
`communicate(input=...)`, which immediately closes stdin. Keeping the monitor
pipe open while explicitly writing/flushing the command and waiting removes
that possible EOF/command-processing race. The fix did not increase deadlines
or relax cleanup assertions. Four targeted masked-IRQ repetitions subsequently
passed with normal monitor quit, followed by full-suite reruns. Forced cleanup
remains a test failure, never hidden as successful shutdown.

## Files changed

- New kernel code: `kernel/include/io.h`, `kernel/include/irq.h`,
  `kernel/interrupts/irq.c`, `kernel/arch/x86_64/pic.c`, `kernel/arch/x86_64/timer.c`.
- Extended entry/setup: `kernel/arch/x86_64/cpu.c`,
  `kernel/arch/x86_64/exceptions.asm`, `kernel/core/main.c`.
- New canonical resources: `assets/branding/icon.png`, `assets/README.md`.
- New host helpers/tests: `tools/host/resources.py`, `tools/host/timer_output.py`,
  `tests/repository/test_resources.py`, `tests/repository/test_timer_output.py`.
- Extended build/tests/metadata: `tools/build/build.py`, `tools/host/image.py`,
  `tools/host/qemu.py`, `tools/host/repository.py`, `project.json`,
  `tests/integration/test_boot.py`, `tests/repository/test_repository.py`.
- Documentation: `README.md`, `ROADMAP.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`,
  `boot/README.md`, `kernel/README.md`, `tools/README.md`, `tests/README.md`,
  `docs/design/cpu.md`, `docs/design/irq-timer.md`,
  `docs/design/bootstrap-dependencies.md`, `docs/reports/stage2.md` (historical
  annotation only), and this report. No generated artifacts are committed.

## Limitations and next milestone

Only IRQ0 is runtime-tested as an external device source. Slave and spurious
PIC handling is implemented but not independently injected. The PIC/PIT design
is tied to the documented single-CPU PC environment. Serviced ticks are not a
wall-clock or lossless edge count. No guest timeout independent of the timer,
emergency interrupt stack, nesting, dynamic registration lifecycle or uptime
service exists. No memory managers, heap, scheduler, graphics, filesystem,
userspace, networking or RynorLang implementation has been added. Stage 4 should
begin with a real firmware memory map and physical-memory accounting; it has
not begun here.
