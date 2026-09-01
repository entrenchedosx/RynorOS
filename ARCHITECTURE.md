# Intended architecture

**Implemented:** foundation, boot/serial, CPU exceptions, PIC/PIT IRQs, physical frames, Stage 5 virtual memory, Stage 6 kernel heap, Stage 7 kernel execution infrastructure (per-thread stacks, context switching, timer-preemptive round-robin scheduler), Stage 8 PS/2 keyboard input (i8042/IRQ1, bounded drop-newest event queue), Stage 9 Bochs VBE linear frame buffer (1024x768x32 BGRX from PCI BAR0, mapped at VM_MMIO_BASE, host pmemsave pixel evidence), and Stage 10 basic kernel runtime (bounded strings, bounded byte rings, and ring-0 runtime services — FNV-1a digest, uppercase, digit count — driven from worker threads, host-recomputed evidence) under QEMU.
Implemented details are explicitly labeled below; **planned** sections are future
work and **experimental** items are unresolved proposals.

## 1. Boot process

Implemented: SeaBIOS starts the original 512-byte `boot/sector.asm` from an IDE
raw image. BIOS extended reads load the fixed payload from LBA 1 at physical
0x8000. Original `boot/transition.asm` collects the real BIOS E820 map, establishes minimal GDT/PAE paging/long
mode and jumps to `rynorkernel_entry`, which owns the stack, clears BSS, and
calls `kernel_main`. It prints the preserved boot prefix, initializes/verifies
the kernel GDT/IDT, performs one controlled breakpoint self-test, initializes/tests
the physical frame allocator from the handoff map, creates/activates/tests the
new kernel page tables, initializes/tests the bounded kernel heap, then initializes
the PIC/PIT and verifies three real IRQ0 ticks. Stage 7 then verifies stack
ownership, lifecycle and timer-driven execution with four, two and one runnable
contexts and verifying preemption, then, in Stage 8, explicitly configures the
i8042/keyboard and consumes eight host-selected keys while IRQ0 schedules a worker,
then, in Stage 9, consumes the boot-collected PCI/BGA handoff and paints/verifies
a pattern with interrupts disabled, then, in Stage 10, runs bounded string/buffer
and runtime-service self-tests on seven worker threads under re-enabled IRQ0,
rechecks memory/execution state and halts. The optional preserved Stage 11 shell
build appends a host-driven interactive session after the integrity gate.
The image contains no third-party loader or OS. SeaBIOS is external emulator
firmware, not RynorOS code; host tools never execute as guest OS services.

The small BIOS approach avoids UEFI packaging, ISO tools, and a separate loader
dependency, at the cost of a fixed legacy-BIOS low-memory layout tested only on
the pinned QEMU PC and unverified on physical hardware. Stage 5 extends
the loader to bounded one-sector reads into 0x8000..0x70000; no BIOS transfer
crosses a 64 KiB boundary. BSS is zeroed separately below the fixed stack, not read from
disk. See `boot/README.md` for the exact contract. Stage 4 adds a bounded/versioned
E820 handoff at linker-owned 0x4000..0x5000, with actual returned entry lengths
and completion status. Planned: reclamation and more capable loading when needed.
Stage 9 adds a version-2, 64-byte display handoff in the reserved page
0x5000..0x6000, collected by the real-mode transition and mapped read-only/NX.
No general boot-argument protocol exists yet.

## 2. CPU architecture

Implemented: x86-64, little-endian, one `qemu64` CPU under TCG on the pinned
`pc-i440fx-10.0` machine. Bootstrap GDT selectors are 0x08 (32-bit code), 0x10
(data), and 0x18 (64-bit code). CR4.PAE, EFER.LME, and CR0.PG/WP are set; CPU
long-mode support is checked. Kernel C uses the SysV x86-64 calling convention,
16-byte pre-call stack alignment, no red zone, no SIMD, and no host runtime.
Stage 2 replaces the temporary boot GDT with null, ring-0 long-code (0x08), and
ring-0 data/stack (0x10) descriptors. `LGDT`, far return/segment reloads, `SGDT`,
and selector checks verify the switch. It then builds a 256-slot IDT, installs
32 exception gates and 16 PIC IRQ gates, performs `LIDT`/`SIDT`, and checks gate
addresses/attributes. The remaining 208 entries are non-present. No TSS/IST or user descriptors exist.

Implemented tests cover #DE/#DB/#BP/#UD/#GP/#PF; only an armed self-test breakpoint
can resume through `IRETQ` in the Stage 2 path. Stage 5 also permits exact,
one-shot armed #PF tests to resume at designated assembly labels; unexpected
exceptions halt after best-effort diagnostics.
Only IRQ0 is enabled during the timer test; NMI and other devices remain masked.
Synchronous exceptions do not require IF.
See `docs/design/cpu.md` for frame and recovery contracts. Broader CPU hardening,
emergency stacks, and unsupported feature-specific exceptions remain future work.

Plan: retain the single-CPU scope until explicit synchronization is implemented.
Architecture-specific startup, registers, page tables, interrupt entry, and
context switching belong under `kernel/arch/`. Portable policy belongs elsewhere.
SMP, other architectures, floating-point task state, and broad hardware support
are out of initial scope. Experimental: future public ABI and physical-hardware
baseline. Changes to compiler flags and CPU assumptions require boot-test evidence.

## 3. Kernel responsibilities

Implemented: original freestanding C/assembly boot, serial, kernel descriptors,
shared exception diagnostics, PIC IRQ dispatch, bounded PIT self-test and physical
frame allocation, virtual-memory, bounded kernel heap and kernel scheduling APIs.
It has no privilege transitions or userspace OS service layer.

Plan: an original, small monolithic Rynorkernel owns CPU state, memory,
interrupts, scheduling, devices, and filesystem services. Early milestones run
a single kernel task. Clear internal interfaces should precede abstraction
layers. No POSIX compatibility or existing OS userspace is assumed. A small
freestanding C/assembly bootstrap is used until RynorLang can replace it;
this is a language/toolchain dependency, not an imported OS implementation.

## 4. Memory management

Unchanged required boot state: three zeroed page-table pages at
0x1000–0x3fff identity-map the first 2 MiB using one writable/executable 2 MiB
page; the fixed kernel stack spans 0x7c000–0x7ffff. Stage 4 inspects/reserves these
tables but adds no mappings. Stage 5 replaces them completely with PMM-owned
four-level tables and 4 KiB leaves; no user-space isolation is claimed.

Implemented PMM: real INT 15h/E820 records are collected before long mode into a
versioned 4 KiB handoff page (64 entries maximum). The kernel checks storage/header,
20/24-byte record sizes, enabled attributes, zero lengths, overflow and CPUID
physical-address limits. Usable intervals round inward; partial pages/nonusable
intervals reserve touched pages. A bounded sweep resolves overlaps with restrictive
types winning, merges adjacent equal intervals and never allocates holes.
ACPI reclaim/NVS, persistent and bad memory stay classified and unavailable;
no reclamation protocol exists yet. Unknown types/attributes are reserved.

All current kernel, boot, handoff, page-table and stack ranges come from linker
symbols and must be covered by firmware usable RAM. A conservative first-MiB
policy retains these and legacy BIOS/ROM/device regions. One allocation bit per
remaining usable 4096-byte frame is stored in a page-rounded bitmap placed and
reserved inside a discovered usable interval within the existing 1..2 MiB mapping.
Compact indices cover usable intervals, not high-address holes/reservations.
If metadata cannot fit mapped usable RAM, initialization fails rather than
limiting RAM silently or implementing new paging.

The API allocates/releases individual physical addresses, queries state and totals,
and detects invalid/double frees and OOM. It requires one CPU and IF=0. A search
cursor and allocation bits provide deterministic uniqueness/reuse; counters and
an independent recount check accounting. Full-pool OOM testing uses the real
allocator and releases every frame afterward. Physical addresses are not mappings,
zeroed storage or isolated userspace memory. `reserved_bytes` includes explicit
firmware address-space windows, not only RAM; holes are excluded from described
totals and unavailable. See `docs/design/physical-memory.md` for all invariants.

Implemented Stage 5: linked low kernel addresses are retained with page-aligned
RX code, R/NX rodata and RW/NX data/stack/bitmap; unused boot mappings disappear.
Seven PMM frames hold the kernel PML4 and low/window PDPT/PD/PT branches. The
high window at 0xffffff0000000000 accesses arbitrary allocated frames without a
whole-RAM direct map. CR3 replacement is verified; NX and CR0.WP are enforced.
Canonical/range/physical validation, map/unmap/range rollback, permission changes,
translation, INVLPG, inactive hierarchy creation/destruction and table accounting
are implemented. Only table frames are VM-owned; data mappings borrow caller-owned
PMM frames. All calls require one CPU and IF=0. See
`docs/design/virtual-memory.md` for layout, formats, APIs, errors and ownership.
No generic address-space switching, user mode/processes, COW, demand paging,
swap or new large pages exist. Stage 9 adds a narrowly scoped MMIO mapping API
with firmware-type/device-ownership checks and validated UC cache encoding.

### Kernel heap (Stage 6)

Implemented: a small, bounded, boundary-tag first-fit heap over a fixed 65536-byte
arena (`HEAP_BASE`, 16 PMM frames) mapped RW/NX through the kernel space's
dedicated high virtual arena at 0xffffc00000000000 (not the transient frame
window). Allocation/free honour power-of-two alignment (8..4096),
coalesce neighbouring blocks, detect boundary corruption and return distinct
error codes for alignment, overflow, invalid, corruption, out-of-memory and
context and mapping conflicts. `heap_check` walks the validated boundary-tag
partition and accounting; there is no separate free list. Unsplittable tails
belong to allocations, and only exact block payload pointers may be freed.
It runs only in single-CPU IF=0 contexts and is an internal kernel allocator,
not a libc `malloc` or a user allocator. See `docs/design/heap.md`.

### Kernel execution infrastructure (Stage 7)

Implemented: bounded single-CPU kernel threads with real per-thread kernel
stacks. Each stack lives in a dedicated virtual slot (PML4 index 448) as one
unmapped, unbacked guard page below four RW/NX PMM-backed payload pages.
Private registry identity and generation enforce stack ownership. Genuine context
switching (`sched_resume`/`thread_switch`) redirects IRETQ to a different
thread's saved frame. The PIT IRQ0 drives a deterministic round-robin scheduler
that preempts threads; the bootstrap context is a first-class thread and can be
preempted and resumed like any worker. Non-reused value IDs reject stale thread
references. Synchronization is irq-save/strict single-CPU; yielding with locks
is rejected. The bounded guest self-test uses non-yielding assembly workers,
real saved IRQ RIP/RSP, lifecycle/ownership negatives and exact PMM restoration;
`scheduler_check` verifies live state invariants. See
`docs/design/scheduler.md`. No user mode, processes, SMP or address-space
switching exists; the self-test masks IRQ0 at its bounded budgets.

## 5. Interrupts

Implemented: CLI/STI-controlled IRQ0 delivery, legacy PIC remapping/masks/EOI,
NMI masking, UART interrupt-disable, and a kernel exception/IRQ IDT. Per-vector stubs normalize hardware/synthetic error slots
and save 15 GPRs before a shared C diagnostic path. It prints vector/name/error,
CPU-saved RIP/CS/RFLAGS/RSP/SS, GPRs, and CR2 for page faults. Stack alignment and
DF are corrected for the C ABI; the armed breakpoint restores the original frame.
Vectors 0..31 retain the CPU diagnostic path; hardware IRQ0..15 use vectors
32..47 and a separate C dispatcher, with a shared full-register entry and IRETQ
return. Static handler registration rejects duplicate/invalid/cascade/null
requests; enabled lines require registered handlers and configuration requires IF=0.
The 8259 PIC uses manual EOI (slave before master); ISR readback distinguishes
spurious IRQ7/15. Only IRQ0 is enabled during the timer test; Stage 8 enables
IRQ1 (keyboard) and Stage 10 re-enables IRQ0 (scheduler/runtime) for their
bounded test phases; every line is masked again before the next phase and on the
final halt. No APIC/SMP complexity is introduced.
PIT channel 0 mode 2 uses divisor 11932 with QEMU's 1193182 Hz clock, giving
1193182/11932 Hz (about 99.9984914516 Hz). The non-blocking IRQ handler alone
increments a static 64-bit tick counter and records three samples. Foreground
`STI; HLT; CLI` waits avoid lost wakeups and print actual sample values; the third
IRQ masks the timer line. Completion checks ISR=0, all lines masked, IF=0 and
three ticks. PIT edges can coalesce, so this is not a wall-clock API. See
`docs/design/irq-timer.md` for exact controller/IDT/EOI contracts and tests.
BIOS temporarily permits interrupts for disk and E820 services before kernel entry.
The PMM self-test runs with IF=0; PIT testing follows it and a final PMM integrity
check follows the IRQs. Interrupt handlers do not allocate frames.
Stack failure, faults before IDT loading, or faults during diagnostics may still
triple-fault: there is no TSS/IST/emergency stack or general fault recovery.

Plan: introduce input interrupts only at their milestone. Handlers must remain
bounded and non-blocking; deferred work belongs outside interrupt context.
APIC/multicore choices remain future work. Preemption requires safe context
switching; the separately audited Stage 7 scheduler supplies that mechanism.

## 6. Device drivers

Implemented: bounded polled 16550-compatible COM1 transmit at 0x3f8, 115200 baud,
8N1, FIFO enabled, UART interrupts disabled; also architecture-specific PIC/PIT
configuration and IRQ0 handling, plus the Stage 8 PS/2 keyboard on the i8042
controller at port 0x60/0x64 on IRQ1. The keyboard ISR pushes raw scan codes into
a bounded drop-newest ring (no allocation/serial/blocking); a set-1 decoder maps
the tested key table to press/release events. See `docs/design/keyboard.md`.

Stage 9 adds a Bochs VBE linear frame buffer: real-mode handoff programs the BGA
registers (1024x768x32, ENABLE 0x41) and reads the LFB base from PCI BAR0, then
the kernel driver validates the 64-byte version-2 handoff and matches actual
PCI/BGA state, maps the whole frame uncached supervisor RW/NX at
`VM_MMIO_BASE` (PML4 slot 509) with `vm_map_device`, and exposes a clipped,
bounds-checked rect/pixel/text API over BGRX. Host pixel evidence is captured
independently via full QEMU HMP `pmemsave` and actual `screendump` scanout,
including every supported glyph. See `docs/design/framebuffer.md`.
No general driver framework exists beyond that layout.

The official icon at `assets/branding/icon.png` is an original-byte-preserving
project resource. Host builds package it and a manifest in a separate deterministic
`rynoros-resources.zip`; it is not in the kernel/boot image. No guest reads or
renders it. A GUI, PNG decoder, resource loading and conversion are future work.

Plan: keyboard/display coverage beyond the documented subsets, and block-device
support for a documented emulator configuration. Drivers validate device inputs
and expose narrow internal interfaces. Polling can precede interrupts where it
simplifies bring-up, with limitations documented. DMA requires reserved buffers
and address/lifetime rules before use. Real hardware, USB, networking, and broad
driver coverage are deferred. Future device models and register contracts remain
open; the current keyboard and framebuffer contracts are documented above.

## 7. Filesystem

Plan: bootstrap programs can initially come from a loader-supplied, read-only
bundle with explicit bounds and format checks; this is not the native filesystem.
Develop an original, small on-disk filesystem after a tested block interface.
Specify versioning, allocation, directories, file lengths, and corruption checks
before enabling writes. Begin read-only; add writable images with recovery tests
on disposable disks. Experimental: disk format and recovery mechanism. Do not
promise crash consistency until its guarantees are specified and tested.

## 8. Process/task model

Implemented: bounded cooperative and preemptive kernel threads in one shared
address space. Planned: user processes with separate address spaces and validated
system-call boundaries.
An initial in-kernel monitor and trusted test programs are not protected userspace.
Task lifecycle, stacks, resource ownership, and cancellation/exit behavior must
be explicit. Preemptive scheduling follows tested save/restore and synchronization.
Experimental: syscall ABI, executable format, scheduling policy, and handle model.
No multicore execution, binary compatibility, or multi-user security is promised.

## 9. Shell

Experimental and opt-in, not a completed milestone: a small ring-0 kernel monitor in
`kernel/shell/` reads real keyboard input, builds bounded command lines, and
exposes only the services that actually exist (the Stage 10 runtime services
DIGEST/UPPER/COUNT_DIGITS) plus the honest built-ins help/version/echo/clear.
It rejects unsupported commands honestly and is excluded from the certified
Stage 10 image (`RYNOR_SHELL_INTERACTIVE`). Its dedicated QEMU probe exercises
the current input path, but Stage 11 remains planned. The eventual shell in
`user/shell/` launches native programs and accesses files via documented OS
interfaces. It can migrate out of the kernel once userspace exists.
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

Implemented: repository/layout/parser/CLI/resource checks, host Python syntax compilation,
and real QEMU integration tests. Native code is assembled, compiled, and linked;
independent output directories yield byte-identical artifacts. QEMU captures
the original boot prefix plus ordered CPU initialization, real state diagnostics,
real E820/PMM initialization/full-pool tests, VM mapping/permission/fault/OOM tests,
then heap, timer setup/three real ticks, Stage 7 execution tests, Stage 8
keyboard `sendkey` handshake, Stage 9 framebuffer pixel evidence, and the
Stage 10 runtime worker-fold evidence, then
post-IRQ accounting within 30 seconds. Six required exception vectors are
actually triggered in separate images; saved RIP is compared with the linked ELF
symbol and register/error/flag values are checked. Default breakpoint return also
verifies GPR/RSP/RFLAGS restoration. Blank/wrong-version/unarmed images must fail.
Two real mutated kernel builds prove masked IRQ0 yields no ticks and missing
master EOI stops after one. Both must time out without the timer completion marker.
The icon package is compared byte-for-byte alongside the executable artifacts.
PMM runs with 16/64/128/256 MiB use the same kernel image and actual firmware maps;
host checks independently reconstruct normalization/reservations/totals, compare
returned frames with usable intervals, and verify linked-object reservations.
Corrupted real-map handoffs must fail before initialization. Guest map fixtures
are explicitly synthetic validation tests, never the source of PMM allocations.
Every launched emulator is stopped/reaped, normally via monitor `quit`. The five
Stage 1 regression tests remain, with their prefix assertion extended to require
the appended Stage 2–7 output. VM tests compare fault RIPs to linked symbols,
test actual RX/RO/NX accesses, and reject broken CR3/TLB/zeroing/fault-arm builds.
Scheduler probes compare hardware IRQ RIPs with a non-yielding assembly loop,
test register/flags/stack restoration, and reject broken handoffs and ownership.
These checks prove neither general hardware support nor language execution
or user-mode isolation. Current evidence is in `docs/reports/stage10-audit.md`;
the Stage 7, 8 and 9 audits retain their historical findings.

Stage 10 services are allocation-free foreground calls (IF preserved, IRQ
context rejected), not syscalls. Strings/rings rely on trusted live extents and
caller synchronization; arithmetic checks are not process isolation. Runtime
tests explicitly unmask IRQ0 and require CPU-traced preemption inside service
code on worker-owned stacks, then mask IRQ0 and reap all workers. Fixed serial
folds alone are insufficient evidence. See `docs/design/runtime.md`.

Plan: host unit tests for pure algorithms and language passes; emulator tests
for faults, allocation, interrupts, and native application execution; disposable
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
