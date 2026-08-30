# Intended architecture

**Implemented:** Stage 0 repository tooling and documentation only.
All system behavior below is **planned**; explicitly marked **experimental**
items need decisions and prototypes. None of these interfaces exists yet.

## 1. Boot process

Plan: firmware starts an explicitly documented bootstrap loader, which loads
Rynorkernel and supplies a versioned handoff containing a memory map, kernel
location, and optional framebuffer. The kernel validates the handoff, reserves
all live boot resources, establishes its own stack and CPU state, and reports
startup through serial output. Loader memory is reclaimed only after copying
or releasing everything referencing it. Stage 1 must choose a boot protocol and
firmware mode; neither is selected or implemented here. A third-party loader
is acceptable as a disclosed bootstrap dependency, not as a kernel foundation.
An original native loader is a later independence milestone, not a prerequisite
for initial kernel work.

## 2. CPU architecture

Plan: x86-64, little-endian, one CPU initially, first tested under emulation.
Architecture-specific startup, registers, page tables, interrupt entry, and
context switching belong under `kernel/arch/`. Portable policy belongs elsewhere.
SMP, other architectures, floating-point task state, and broad hardware support
are out of initial scope. Experimental: exact baseline CPU features and ABI.
Future freestanding code must avoid implicit host runtime dependencies and
document compiler flags, stack alignment, red-zone policy, and calling convention.

## 3. Kernel responsibilities

Plan: an original, small monolithic Rynorkernel owns CPU state, memory,
interrupts, scheduling, devices, and filesystem services. Early milestones run
a single kernel task. Clear internal interfaces should precede abstraction
layers. No POSIX compatibility or existing OS userspace is assumed. A small
freestanding C/assembly bootstrap is proposed until RynorLang can replace it;
this is a language/toolchain dependency, not an imported OS implementation.

## 4. Memory management

Plan: first normalize and validate the boot memory map; exclude kernel,
firmware, device, page-table, and handoff regions. A physical page allocator
precedes virtual-memory policy and a kernel heap. Page accounting must prevent
double allocation, reject invalid frees, and handle exhaustion explicitly.
Later address spaces separate kernel and user mappings, with permissions set
deliberately (including non-executable data where supported). Experimental:
allocator data structures, virtual address layout, and heap strategy. Kernel
allocation failures must not silently continue with invalid pointers.

## 5. Interrupts

Plan: establish exception handlers and useful fault diagnostics before enabling
external interrupts. Architecture entry stubs preserve a documented register
frame. Configure and acknowledge the selected controller, then introduce timer
and input interrupts individually. Handlers must be bounded and non-blocking;
deferred work belongs outside interrupt context. Experimental: initial PIC/APIC
and timer choices. Preemption follows safe context switching, not merely a timer.

## 6. Device drivers

Plan: serial diagnostics first, then minimal keyboard, display, and block-device
support for a documented emulator configuration. Drivers validate device inputs
and expose narrow internal interfaces. Polling can precede interrupts where it
simplifies bring-up, with limitations documented. DMA requires reserved buffers
and address/lifetime rules before use. Real hardware, USB, networking, and broad
driver coverage are deferred. Device models and register contracts remain open.

## 7. Filesystem

Plan: bootstrap programs can initially come from a loader-supplied, read-only
bundle with explicit bounds and format checks; this is not the native filesystem.
Develop an original, small on-disk filesystem after a tested block interface.
Specify versioning, allocation, directories, file lengths, and corruption checks
before enabling writes. Begin read-only; add writable images with recovery tests
on disposable disks. Experimental: disk format and recovery mechanism. Do not
promise crash consistency until its guarantees are specified and tested.

## 8. Process/task model

Plan: single kernel execution context, then cooperative kernel tasks, then user
processes with separate address spaces and validated system-call boundaries.
An initial in-kernel monitor and trusted test programs are not protected userspace.
Task lifecycle, stacks, resource ownership, and cancellation/exit behavior must
be explicit. Preemptive scheduling follows tested save/restore and synchronization.
Experimental: syscall ABI, executable format, scheduling policy, and handle model.
No multicore execution, binary compatibility, or multi-user security is promised.

## 9. Shell

Plan: a small kernel monitor first provides input, help, and real diagnostics
for services that exist. It must reject unsupported commands honestly. The
eventual shell in `user/shell/` launches native programs and accesses files via
documented OS interfaces. It can migrate out of the kernel once userspace exists.
Language evaluation is unavailable until the compiler/runtime exists; the
shell must never simulate program execution with canned responses.

## 10. RynorLang

Experimental: a small statically typed language with `.rl` sources, explicit
control flow, predictable value semantics, and clear diagnostics. The initial
design uses signed integers, booleans, immutable strings, variables, functions,
conditionals, and loops. See `rynorlang/README.md` for the draft, including
deferred modules. No parser, runtime, or compatibility guarantee exists.
Pointers, low-level intrinsics, allocation, and layout controls require later
design before RynorLang can implement a kernel or compiler.

## 11. Compiler

Plan: build a host-side lexer, parser, explicit AST, name/type checking, then a
small x86-64 native backend with a written ABI and output format. Choose the
bootstrap implementation language before starting the lexer; Python is a
candidate, not an existing compiler. Separate diagnostics and deterministic
outputs from host I/O. No evaluation shortcuts should masquerade as compilation.
Linking, relocation, runtime calls, and executable loading need tested contracts
before native applications. Foreign toolchains may bootstrap code generation
only when disclosed; eventual native output must not depend on a host OS runtime.

## 12. Userspace

Plan: a minimal native library, shell, and `.rl` applications using RynorOS
services, not wrappers over host APIs. Initial trusted programs may execute in
kernel mode as an explicit intermediate milestone. Protected userspace requires
user-mode entry, validated memory access, syscalls, process exit, and loading.
Runtime I/O such as `print` will bind to real OS services only when available.
The API and ownership/error conventions are experimental.

## 13. Testing strategy

Implemented: repository structure/metadata tests, malformed-metadata rejection,
command failure checks, and host Python syntax compilation. These provide no
evidence about booting or language execution.

Plan: host unit tests for pure algorithms and language passes; emulator tests
for boot, faults, allocation, interrupts, and native program execution; disposable
image tests for filesystem corruption and recovery. Harnesses must assert
observable behavior, record versions/configuration, enforce timeouts, and fail
on crashes or missing results. Serial messages alone are not proof of a memory
manager or compiler. Hardware smoke tests supplement, not replace, emulator
coverage. Keep reports under `docs/reports/` with exact commands and limitations.

## 14. Eventual self-hosting strategy

Plan: (1) bootstrap enough OS and compiler support on a documented host;
(2) add language data structures, file I/O, memory facilities, and linking needed
to express the compiler; (3) port/rewrite that compiler in RynorLang and compile
it with the bootstrap compiler; (4) run the resulting compiler on RynorOS;
(5) compile it again there and compare deterministic outputs or explain remaining
nondeterminism; (6) rebuild the kernel, libraries, shell, tools, and boot artifacts
from source on RynorOS. Preserve trusted seed sources and reproducible bootstrap
instructions. Compiler self-hosting and whole-system self-hosting are separate
claims. Replacing host tools incrementally is essential; merely calling them
from a RynorOS shell does not meet the goal.

## Documentation contract

Every subsystem document must state purpose, public interfaces, invariants,
implementation status, tests, and known limitations. Use
`docs/design/subsystem-template.md`. Empty reserved directories expose no API.
Decisions that change the above proposals require rationale, dependencies,
acceptance tests, and updates to the roadmap and metadata when applicable.
