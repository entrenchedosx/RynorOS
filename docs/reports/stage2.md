# Stage 2 — CPU Initialization and Controlled Exception Diagnostics

Date: 2026-08-30. **Implementation and runtime verification passed** on the
documented QEMU configuration. The final Git-cleanliness gate also requires a
decision about an unrelated `icon.png` added during this task; it is not part of
Stage 2 and is being preserved without modification. No Stage 3 work was done.

## Mechanisms implemented

- Kernel-owned GDT: null, DPL-0 long-code selector 0x08, DPL-0 writable
  data/stack selector 0x10. LGDT, far return to reload CS, data-segment reloads,
  SGDT, and selector checks execute before the GDT initialized marker.
- Kernel-owned IDT: 256 slots; 32 DPL-0 interrupt gates (0x8e, IST=0) for CPU
  exceptions, with 32..255 non-present. LIDT/SIDT and gate checks execute before
  the IDT initialized marker. No TSS, IST, privilege transition, or IRQ enabling.
- Small per-vector assembly stubs normalize hardware error/synthetic zero slots.
  One shared path saves all 15 non-RSP GPRs, receives actual CPU-saved
  RIP/CS/RFLAGS/RSP/SS, captures CR2 separately, clears DF, aligns the C call,
  and invokes shared serial diagnostics. Compile-time frame layout assertions
  accompany the 176-byte structure.
- A one-shot armed breakpoint self-test uses explicit INT3, validates actual
  captured state, resumes through IRETQ, and checks restored GPRs/RSP/RFLAGS
  before reporting completion. Other controlled test images halt after verified
  diagnostics; unexpected/unarmed exceptions diagnose and halt without success.

See [CPU design](../design/cpu.md) for exact descriptor values, every vector/error
mapping, frame offsets, public/internal interfaces, invariants, and limitations.
Boot code and the existing serial interface were not replaced. The initial
two-line `0.1.0 | x86_64 | stage1` banner is deliberately preserved as the Stage 1
boot-path compatibility prefix; current metadata is Stage 2/schema 3.

## Exact default serial output observed

Both legacy lines and every diagnostic line use CRLF. The following is the full
guest serial transcript, not the QEMU monitor output:

```text
Rynorkernel booted.
RynorOS 0.1.0 | x86_64 | stage1
[CPU] GDT initialized
[CPU] IDT initialized
[TEST] triggering controlled exception
[EXCEPTION] vector=03 name=breakpoint error_source=synthetic error=0x0000000000000000
[STATE] rip=0x00000000000082cb cs=0x0000000000000008 rflags=0x0000000000000402 rsp=0x000000000007ffb8 ss=0x0000000000000010
[GPR] rax=0x0000000000000101 rbx=0x0000000000000102 rcx=0x0000000000000103 rdx=0x0000000000000104
[GPR] rbp=0x0000000000000105 rsi=0x0000000000000106 rdi=0x0000000000000107 r8=0x0000000000000108
[GPR] r9=0x0000000000000109 r10=0x000000000000010a r11=0x000000000000010b r12=0x000000000000010c
[GPR] r13=0x000000000000010d r14=0x000000000000010e r15=0x000000000000010f
[EXCEPTION] action=resume
[TEST] exception handling verified
```

The GPR values are seeded test inputs and printed from actual saved registers,
not substituted constants. RSP comes from the CPU frame and is compared with
the assembly's pre-exception RSP. RIP is compared against the actual ELF
`cpu_test_after` symbol, not a hardcoded expected address. DF is deliberately
set in the interrupted flags; C runs with DF cleared, IRETQ restores it, and
the test clears it again before returning to C. This verifies the return path.

## Required-vector execution coverage

Each image triggers exactly one exception. The default image resumes from #BP;
the other images terminate the test in a controlled CLI/HLT loop after validation.
All rows were actually executed in QEMU:

| Vector | Instruction / trigger | Saved RIP | Error source/value | Saved flags | Terminal action |
| --- | --- | --- | --- | --- | --- |
| 0 (#DE) | `DIV RCX`, RCX=0 | 0x82d0 (fault label) | synthetic / 0 | 0x10002 | halt, verified |
| 1 (#DB) | TF=1 then `NOP` | 0x82cb (after label) | synthetic / 0 | 0x102 | halt, verified |
| 3 (#BP) | `INT3` | 0x82cb (after label) | synthetic / 0 | 0x402 | IRETQ resume, verified |
| 6 (#UD) | `UD2` | 0x82c7 (fault label) | synthetic / 0 | 0x10002 | halt, verified |
| 13 (#GP) | `MOV DS,AX`, AX=0x18 beyond GDT limit | 0x82cc (fault label) | CPU / 0x18 | 0x10002 | halt, verified |
| 14 (#PF) | Read at unmapped 0x200000 | 0x82c7 (fault label) | CPU / 0 | 0x10002 | halt, verified |

#PF additionally printed `[PAGE] cr2=0x0000000000200000`. Its error=0 denotes a
supervisor read of a non-present page, using the unchanged Stage 1 boot mapping.
No page tables or protection policy were added to create this test. #DE's
RAX/RCX/RDX and #GP's RAX appropriately differ from the default seeded values.

An additional **unarmed** INT3 image printed the real #BP state followed by
`[EXCEPTION] action=halt reason=unexpected`. It did not print a verified marker;
the host runner correctly timed out and reaped QEMU. No fault was simulated by
an INT instruction with an invented CPU error code, and no C undefined behavior
was used to trigger a hardware fault.

## Build, QEMU, and tests

Dependencies did not change: Python 3.14.3, NASM 3.02, Clang/LLD 23.1.0
(LLVM `ea7d852a70e8bdfaf601d6626a760f9771b2c4b4`), QEMU 11.1.0
(`v11.1.0-12130-ge470268ff4`), SeaBIOS 1.17.0, and Git 2.53.0.windows.2.
No downloads, packages, target runtime, or host OS services were added.
Use the existing session-only tool setup in [bootstrap dependencies](../design/bootstrap-dependencies.md).

QEMU configuration is unchanged: `pc-i440fx-10.0`, TCG, `qemu64`, 64 MiB,
one CPU, `bios-256k.bin`, raw IDE snapshot disk, no display/VGA/network/parallel,
serial to a file, monitor on separate stdio, and `-no-reboot`. The runner waits
for complete validated output within 10 seconds; it does not assume a fixed
boot duration. Missing/altered markers, state fields, ordering, duplicate records,
or incorrect actions fail. It checks the transcript again after QEMU shutdown.

Commands run after tool setup:

```text
python tools/build/build.py validate
python tools/build/build.py build
python tools/build/build.py boot-test
python tools/build/build.py test
python tools/build/build.py integration-test
python tools/build/build.py check
git diff --check
```

Results: all passed; **33 repository tests and 11 integration tests**, no skips.
The Stage 1 baseline passed before edits (26 repository, five integration).
Those cases remain: metadata expectations advanced to Stage 2 and the boot
comparison now requires the original prefix plus all new diagnostics, rather
than rejecting appended output. Rebuild byte identity, ELF checks, blank-disk
timeout, wrong-banner rejection, compile/link failures, and stale-log rejection
still pass. Seven new repository cases and six integration cases were added.

`build/boot-test/` holds default serial/QEMU logs and `run.json`; per-vector
images/logs are under `build/cpu-tests/`. Every emulator run records its owned
PID and normal monitor-quit cleanup, exit 0, and `reaped: true`, including
negative timeout cases. No QEMU process remained after verification. There is
no broad process-kill operation; only children created by the runner are stopped.

## Reproducibility and artifacts

With the unchanged tools, the default image was rebuilt in independent output
directories; all four native artifacts were byte-identical:

| Artifact | Bytes | SHA-256 |
| --- | --- | --- |
| boot.bin | 512 | `a49a9c86f58c05fb2831959249e22ea05fcb7798d8f7f03527e40f4b49fcc0dc` |
| rynorkernel.bin | 5265 | `904e2433db0ab3ce04fe956a2e94eb90f5b39842d797233d8b815e69398b5f91` |
| rynorkernel.elf | 13200 | `f19bb3f8f93913c8758ad59212d567e77d383d327dd6faec810fbbbfc7a607a7` |
| rynoros.img | 1048576 | `ceadb8d8b70a0d57850f4c57a5e89d788437dfb661f39325cbd8766f0978a211` |

The default payload occupies 11 sectors and stays inside the original 32 KiB
payload/BSS budget. Variant selection is recorded as `cpu_self_test` in each
build manifest. Source comments do not supply timestamp/build-path data.

## Failures observed and resolved

The first Stage 2 guest run correctly diagnosed and returned from #BP, but the
old host harness timed out because it expected exactly two serial lines.
The harness was extended to validate the complete structured diagnostic stream;
the prefix was preserved, not weakened or removed. No kernel exception was
silenced to pass a test. Later six-vector execution and regression checks passed.

## Scope and remaining limitations

Implemented: kernel GDT verification, exception IDT, normalized entry, real
register diagnostics, one-shot controlled test and verified IRETQ return,
required-vector QEMU tests. Added CPU sources/header, shared exception C source,
output parser/tests, and updated build/metadata/design/report documentation.
The obsolete `kernel/interrupts/.gitkeep` was removed because source now retains
the directory; it remains recoverable in Git history.

Unsupported: user mode/privilege transitions, TSS/IST/emergency stacks, external
IRQs/timer/controller acknowledgment, scheduler, new memory management or memory
protection policy, heap, process isolation, filesystem, shell, graphics,
networking, RynorLang, SIMD/debug-register context capture, and real-hardware
validation. Other wired vectors and nested faults are unexercised/best-effort.
Invalid stacks and faults before IDT loading or during diagnostics can still
double/triple-fault. This is not general recovery from arbitrary kernel faults.

Next milestone: **Stage 3 — external/device interrupt system**, with explicit
controller setup/acknowledgment and a timer test, preserving these regressions.
No Stage 3 functionality was implemented. The unrelated `icon.png` is not used
by the build, tests, kernel, boot image, or this milestone.
