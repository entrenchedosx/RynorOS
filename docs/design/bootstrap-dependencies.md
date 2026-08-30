# Bootstrap dependencies

## Actual requirements (unchanged from Stage 1 through Stage 5)

All tools were already present on the Windows verification host. Nothing was
downloaded, installed, or copied from another operating-system project for this
milestone sequence. Kernel and loader sources are original RynorOS code under MIT.
Stages 2–4 add only original freestanding C/NASM code and standard-library Python
tests; no toolchain, emulator, firmware, library, or package dependency changed.
Stage 3 uses Python's `zipfile`/`struct` to package the user-supplied original icon,
without a PNG conversion library or runtime graphics dependency. The separately
packaged asset is not part of the executable boot image or firmware.
Stage 4 consumes SeaBIOS's standard E820 service while still in real mode. It
uses no external memory-management library or new firmware/bootloader. GitHub
synchronization uses Git and available host credentials, not guest OS code.

| Dependency | Verified version | Purpose / boundary | Upstream license |
| --- | --- | --- | --- |
| Python | 3.14.3; supported policy 3.10+ | Host build/tests, standard library only | PSF |
| NASM | 3.02, build June 28 2026 | Host assembler for original boot/entry sources | BSD-2-Clause |
| Clang | 23.1.0, LLVM commit ea7d852a70e8bdfaf601d6626a760f9771b2c4b4 | Host C cross-compiler, target x86_64-none-elf | Apache-2.0 with LLVM exceptions |
| LLD | 23.1.0, same LLVM commit | Host ELF/flat-binary linking, no target runtime libraries | Apache-2.0 with LLVM exceptions |
| QEMU | 11.1.0, v11.1.0-12130-ge470268ff4 | Host emulator, TCG, pc-i440fx-10.0, qemu64 | GPL-2.0; components have their own notices |
| SeaBIOS | 1.17.0-0-gb52ca86e094d-prebuilt.qemu.org | External guest firmware, QEMU's bios-256k.bin; not embedded in the disk image | GPL-3.0; see upstream COPYING/source notices |
| Git | 2.53.0.windows.2 | Optional host version control | GPL-2.0 |

No Linux/WSL, Make, GCC, objcopy, ISO builder, GRUB/Limine, target libc,
compiler runtime, or Python packages are used. LLD emits both ELF and binary
from the same original object files. QEMU needs its normal packaged DLLs/data
and SeaBIOS; no VGA option ROM is needed because VGA is disabled. None of the
emulator or firmware code becomes Rynorkernel.

## Setup and provenance

Use an existing trusted installation or obtain tools from their upstreams:

- [Python downloads](https://www.python.org/downloads/).
- [LLVM project releases/source](https://github.com/llvm/llvm-project) and
  [license](https://llvm.org/LICENSE.txt). The verified host uses a local
  installation at D:/llvm-install; version output identifies the commit above.
  Its original archive/build provenance was not independently authenticated.
- [NASM release builds](https://www.nasm.us/pub/nasm/releasebuilds/3.02/).
  The host's existing Scoop manifest points to
  [nasm-3.02-win64.zip](https://www.nasm.us/pub/nasm/releasebuilds/3.02/win64/nasm-3.02-win64.zip)
  with SHA-256 `161d0bfaff53c2f9e9f3e69fd0672323ebabafd1268976a5cec11be92a19aee7`.
- [QEMU downloads](https://www.qemu.org/download/) links to Windows builds.
  The existing host manifest identifies
  [qemu-w64-setup-20260811.exe](https://qemu.weilnetz.de/w64/2026/qemu-w64-setup-20260811.exe)
  and the matching upstream SHA-512 file. Use the complete distribution, not
  just the executable. These package-manifest references were inspected, not
  re-downloaded or independently signature-verified in this task.
- [SeaBIOS source](https://github.com/coreboot/seabios) and
  [COPYING](https://github.com/coreboot/seabios/blob/master/COPYING).
  The tested ROM comes from the above QEMU package; its embedded version string
  and SHA-256 are recorded, not inferred from a system firmware installation.

Put `clang`, `ld.lld`, `nasm`, and `qemu-system-x86_64` on PATH, or set
`RYNOR_CLANG`, `RYNOR_LLD`, `RYNOR_NASM`, and `RYNOR_QEMU` to executable paths.
Overrides contain a path only, never shell arguments. In a fresh PowerShell
session on this particular host the exact setup used is:

```powershell
$env:RYNOR_CLANG = 'D:\llvm-install\bin\clang.exe'
$env:RYNOR_LLD = 'D:\llvm-install\bin\ld.lld.exe'
$env:RYNOR_QEMU = 'C:\Users\aawad\scoop\apps\qemu\current\qemu-system-x86_64.exe'
# NASM is already on PATH through Scoop.
python tools/build/build.py check
```

These are session-only settings, not persistent machine configuration. The
repository does not hardcode these host-specific discovery paths. Other hosts
must supply their own paths. Run each tool's version command before reproducing;
QEMU must offer the pc-i440fx-10.0 machine and locate bios-256k.bin in its data
directory. Missing tools fail with a named dependency and override instruction.

## Verified executable/firmware SHA-256

Hashes identify the installed verification inputs; they are not signatures or
a claim of independent supply-chain authentication.

| Input | SHA-256 |
| --- | --- |
| clang.exe | `4d6ba0ae3d9064b53006220a99b32f77bdbba7bccd81b8dce8232985bff85526` |
| ld.lld.exe | `569474cd171c3f38cb808a165ae516564e6dc705aa7b95e85ad15a743e2cdaea` |
| nasm.exe (actual binary, not Scoop shim) | `04ec2385879f7e1c45dbe76c4020970555de48eeb97c23f59620ede061328f51` |
| qemu-system-x86_64.exe | `47d57a6072e0bb3bd98f87926eb129eb1736dfe818c67b3b81ef7ce4edd0b3cd` |
| share/bios-256k.bin | `ae6f6aa973aaccc143f57aa960fb035fd9de4daee4ad0cd713322f8c259e7650` |

Compare with `Get-FileHash -Algorithm SHA256 PATH` on Windows or `sha256sum PATH`
where available. The build manifest records actual compiler/assembler versions
and generated artifact hashes. The code does not force these exact tools on all
hosts; byte identity is claimed only for the same inputs and tool versions.
No different OS/Python/tool version matrix has been tested.

## Technical references and replacement strategy

The [Intel architecture manuals](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
describe the protected/long-mode transition contract.
[NASM output formats](https://www.nasm.us/doc/nasm09.html),
[Clang cross-compilation](https://clang.llvm.org/docs/CrossCompilation.html), and
[LLD linker scripts](https://lld.llvm.org/ELF/linker_script.html) document host
interfaces. [QEMU invocation](https://www.qemu.org/docs/master/system/invocation.html)
documents machine selection, serial capture, monitor, and snapshot drives.
These are interface references, not copied kernel implementations.

RynorLang's eventual native compiler/linker/build tools must replace the host
bootstrap pipeline. The BIOS loader is already original, but firmware/emulator
dependencies remain disclosed. Native rebuilding is not self-hosting until it
runs on RynorOS without delegating work back to host OS tools.
