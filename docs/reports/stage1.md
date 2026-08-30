# Stage 1 — Bootable Rynorkernel

Date: 2026-08-30. Status: **COMPLETE for the documented QEMU configuration**.
Stage 2 and all later subsystems remain unimplemented. This is execution evidence
for a boot-and-serial milestone, not a claim of a usable general-purpose OS.

## Boot method, entry, and CPU state

The original BIOS sector loads the original linked flat payload from LBA 1 at
0x8000. Original boot transition code establishes a GDT, A20, protected mode,
PAE page tables, and long mode. It jumps to `rynorkernel_entry` in
`kernel/arch/x86_64/entry.asm`; that sets a 16-byte-aligned stack at 0x80000,
zeros BSS, and calls original freestanding C `kernel/core/main.c:kernel_main`.
The kernel programs COM1, prints the messages below, and returns to CLI/HLT.
Only boot-required static mapping exists; there is no memory manager.

Architecture: x86-64, single CPU, little-endian, ring 0, SysV x86-64 C calling
convention, no red zone/SIMD/host runtime. IRQs/NMI stay disabled after BIOS disk
loading. SeaBIOS is external firmware provided by QEMU, not embedded OS code.
No Linux/BSD kernel, existing userspace, or third-party loader is included.

The BIOS choice minimizes dependencies for this milestone; it deliberately
limits loading to 32 KiB and assumes the documented QEMU low-memory layout.
See `../../boot/README.md` for the exact memory reservations and handoff.

## Bootstrap dependencies

Verified on Windows with Python 3.14.3, NASM 3.02, Clang/LLD 23.1.0
(LLVM commit `ea7d852a70e8bdfaf601d6626a760f9771b2c4b4`), QEMU 11.1.0
(`v11.1.0-12130-ge470268ff4`), and SeaBIOS
`1.17.0-0-gb52ca86e094d-prebuilt.qemu.org`. Git 2.53.0.windows.2 is used for history.
All were already installed; no packages were added. No WSL/Linux build is used.
Tool paths, package provenance limitations, licenses, executable/firmware hashes,
setup commands, and eventual replacement strategy are in
`../design/bootstrap-dependencies.md`.

## Exact verification commands

After the session-only tool setup documented above, from `D:\RynorOS`:

```text
python tools/build/build.py validate
python tools/build/build.py build
python tools/build/build.py test
python tools/build/build.py boot-test
python tools/build/build.py integration-test
python tools/build/build.py check
git diff --check
```

All passed. The combined check compiles eight Python tooling/test sources,
builds the original native kernel and image, passes 26 repository/layout/CLI
tests, and passes five integration tests. No tests were skipped.

QEMU configuration, expanded below for readability (one command; paths are
absolute in the automated runner):

```text
qemu-system-x86_64
  -machine pc-i440fx-10.0 -accel tcg -cpu qemu64 -m 64M -smp 1
  -bios bios-256k.bin -display none -vga none -nic none -parallel none
  -boot order=c,strict=on
  -drive file=D:\RynorOS\build\rynoros.img,format=raw,if=ide,snapshot=on
  -serial file:D:\RynorOS\build\boot-test\serial.log
  -monitor stdio -no-reboot
  -d guest_errors -D D:\RynorOS\build\boot-test\guest-errors.log
```

Serial is the emulated 16550-compatible COM1, I/O base 0x3f8, divisor 1,
115200 baud, 8 data bits, no parity, one stop bit; FIFO enabled and interrupts
disabled. Kernel output is polled I/O, not a host printing substitution.

Exact observed serial text (both lines terminated by CRLF):

```text
Rynorkernel booted.
RynorOS 0.1.0 | x86_64 | stage1
```

The runner requires these exact bytes, not just an image or first-line match.
Success runs completed within the 10-second bound and exited with code 0 via
monitor `quit`. `build/boot-test/run.json` records the actual command, owned PID,
return code, elapsed time, and `reaped: true`. Serial, monitor/stderr, and guest
error logs are in the same directory. No QEMU processes remained after checks.

## Artifact and reproducibility results

| Generated artifact | Bytes | SHA-256 with the verified tools |
| --- | --- | --- |
| `build/boot.bin` | 512 | `7508c3015e9a39e8a93a367e25c08084fc7ee1fcd42623d4642217b90a43c9a9` |
| `build/rynorkernel.bin` | 600 | `80a3c6a2894ee305dcbaa3fa243e7b3535b5fa985df78012399c30cd7a8f02ea` |
| `build/rynorkernel.elf` | 6008 | `1f4d82f40a2dd84a402076e654f4a354736afab1eed8483f6c333e95bbbb7779` |
| `build/rynoros.img` | 1048576 | `ae1933c8a21212a9a35123f7ee98a39e8d6235e8561b508e2132447cb5429a6e` |

The payload occupies two BIOS sectors; remaining image bytes are zero padding.
`build/build-manifest.json` records artifact sizes/hashes and tool versions.
ELF and flat binary are separately linked from the same objects/script; the ELF
contains symbols for inspection, and the BIOS image contains the flat payload.
The integration suite rebuilt all four artifacts in independent temporary output
directories and compared their complete bytes and SHA-256 values successfully.
There are no timestamp/build-path fields in the boot artifacts. Reproducibility
across different compiler versions is not claimed. Snapshot testing left the
image hash unchanged. No generated artifact is committed to Git.

## Negative tests and failures resolved

- Missing compiler, actual C `#error`, unresolved link symbol, malformed host
  Python, invalid command, invalid metadata/path, and empty/failing test-suite
  cases correctly fail. Native build failure does not publish a new image.
- A blank disk really ran in QEMU and timed out after two seconds; a pre-existing
  successful serial log did not pass. QEMU exited via monitor quit and was reaped.
- An intentionally modified version string still reached real kernel code and
  printed the first line, but failed the two-second test because the second line
  was wrong. This is a negative fixture, not a replacement implementation.
- Initial NASM `-Wall` enabled relocation-style diagnostics inappropriate for
  deliberate low-address mixed-mode code; default NASM warnings remain errors,
  while the linker checks relocation ranges and the explicit payload limit.
- The first QEMU configuration used a read-only IDE backend, which QEMU rejected
  with `Block node is read-only`. It was changed to snapshot mode, then rebuilt
  and repeatedly boot-tested. No compilation-only result was accepted as success.

## Files and scope

Added original BIOS sector/transition, x86-64 entry/linker/serial code, C kernel
main and serial declarations; extended the existing Python build entry point
with small image/QEMU modules; added image and real integration tests; updated
metadata, architecture, roadmap, subsystem/dependency docs, and this report.
Four obsolete `.gitkeep` files were removed where real source/tests now retain
their directories; history preserves them. Unrelated reserved subsystems remain.

Implemented: boot image, minimal required CPU state, serial, deterministic build,
and automated execution tests. Not implemented: general memory management, heap,
scheduler, interrupts/exception handlers, filesystem, shell, graphics, userspace,
RynorLang compiler, multitasking, networking, or self-hosting.

Known limitations: QEMU BIOS only, no real-hardware validation, no E820 parser,
no guard pages or permissions isolation, fixed stack/load range, no fault
diagnostics, no disk retry/security policy, and no UART/BIOS error fault injection.
Monitor cleanup is tested on success and timeout; forced-cleanup fallbacks and
forced termination of the host runner itself are not coverage claims.

Next recommended milestone: **Stage 2 — CPU initialization hardening and a
controlled exception diagnostic path**, preserving the serial boot regression.
No Stage 2 implementation was performed. Commit attribution uses the existing
command-scoped agent identity; no persistent Git settings were changed.
