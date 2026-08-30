# Bootstrap dependencies

## Stage 0: actual requirements

| Dependency | Purpose | Requirement / provenance | Target-system dependency? |
| --- | --- | --- | --- |
| Python | Run validator, bytecode compilation, and unittest checks | CPython-compatible Python 3.10+; standard library only; user-provided installation, Python Software Foundation license for CPython | No |
| Git | Optional repository history | User-provided Git; no minimum-version-specific feature needed; GPL-2.0 | No |

No package manager, downloaded libraries, third-party source, assembler,
cross-compiler, linker, emulator, firmware image, or bootloader is required for
Stage 0. A host OS and command environment are necessary to run these tools;
the scripts use portable Python APIs and do not require Bash, Make, or PowerShell
specifically. Host tools are development infrastructure, not RynorOS services.
The editor/assistant used to author files is not a build dependency.

The foundation has been exercised on the host versions recorded in
`../reports/foundation.md`; the minimum supported version is a declared policy,
not a claim that every supported interpreter/platform has been tested.

## Future dependencies: candidates, not installed or selected here

Stage 1 needs a freestanding compiler (for example GCC or Clang), assembler
(possibly compiler-integrated), linker, emulator (for example QEMU), and a
chosen firmware/bootloader/image path. A debugger may help. The bootstrap
compiler for RynorLang also needs an implementation-language decision.

Before adoption, record exact versions, upstream provenance, licenses, retrieval
and verification instructions, commands, target/host boundary, and replacement
strategy. Third-party loaders and host compilers may assist bootstrapping; they
must not provide a renamed kernel or imported OS userspace. Do not imply a
tool is required now just because it appears as a candidate here.

## Independence boundary

Eventually RynorOS must supply its own native compiler, runtime, shell,
userspace, filesystem, and build tools. Bootstrap seeds and their provenance
remain documented even after native rebuilds are possible. A native command
that delegates its functionality to the host is not self-hosting.
