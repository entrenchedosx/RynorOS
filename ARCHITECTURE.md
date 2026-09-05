# Intended architecture

**Implemented:** foundation, boot/serial, CPU exceptions, PIC/PIT IRQs, physical frames, Stage 5 virtual memory, Stage 6 kernel heap, Stage 7 kernel execution infrastructure (per-thread stacks, context switching, timer-preemptive round-robin scheduler), Stage 8 PS/2 keyboard input (i8042/IRQ1, bounded drop-newest event queue), Stage 9 Bochs VBE linear frame buffer (1024x768x32 BGRX from PCI BAR0, mapped at VM_MMIO_BASE, host pmemsave pixel evidence), Stage 10 basic kernel runtime (bounded strings, bounded byte rings, and ring-0 runtime services — FNV-1a digest, uppercase, digit count — driven from worker threads, host-recomputed evidence), Stage 11 ring-0 shell monitor, and the Stage 12–14 host-side RynorLang lexer/parser/semantics (stable AST, name resolution, type checking). Kernel behavior is verified under QEMU; the language tools are separate Python bootstrap tooling and are not guest code.
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
rechecks memory/execution state, then runs the Stage 11 synthetic shell tests.
Dedicated interactive images additionally append a bounded host-driven session;
normal images print the explicit interactive-skip marker and halt.
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
and detects invalid/double frees and OOM. It requires one CPU and IF=0 and `!irq_in_context()` (IRQ handlers must not call PMM). A search
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
PMM frames. All calls require one CPU and IF=0 and `!irq_in_context()`. See
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
It runs only in single-CPU IF=0 and `!irq_in_context()` contexts and is an internal kernel allocator,
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

Implemented and verified — ring-0 kernel monitor (`kernel/shell/`): reads real `IRQ1` keyboard input via `kbd_poll` (Set-1 `0x00/0xff` overrun and `AUX`/`ERROR` counted as `epoch` loss, `E0`/`E1` prefix isolation preserved), translates `a–z`/`0–9`/`space` via bounded table plus `Enter` (`0x1c`) and `Backspace` (`0x0e`), accumulates a bounded `64`-byte `data[65]` line with `len`/`NUL` invariant and `line_insert` overflow rejection, tokenizes with `shell_tokenize` (`kstr_nlen` bounded, `SHELL_TOO_MANY=-3` distinct from valid counts `0..12`, `SHELL_INVALID=-1` for unterminated input), and dispatches with strict argument counts. It exposes the implemented `KRST_SVC_UPPER`/`COUNT_DIGITS`/`DIGEST` plus `help`/`version`/`echo` and an honest serial-only `clear` redraw-request stub. `upper` rejects arguments longer than the 40-byte service bound instead of truncating and checks the returned length before adding a NUL; `count` decodes the complete 64-bit little-endian result; `count` and `digest` require eight result bytes. `wait_key` sleeps with `sti;hlt;cli`, validates `E0`/`E1` tails with immediate malformed-sequence recovery, and drains matching break events. Interactive images consume exactly `39` keys. The default script is `upper hello | count a1b2 | digest ab | bogus`; a different host-selected 39-key script is independently passed to both injection and transcript validation so a fixed default transcript cannot satisfy both positive runs. Per-key `scan`/`ascii`/`line`, per-command `exec`/`result`, and `keys=39 received_scan_bytes=78` are checked. The reviewed inventory contains `477` repository and `162` integration test methods, plus a 9-configuration QEMU matrix and deterministic raw-artifact and manifest comparison. The eventual `user/shell/` will move into `CPL3` with files once `18a` exists.

## 10. RynorLang

Stage 12 implements one host-side lexer at `tools/rynorlang/lex.py`. Its frozen
subset is ASCII and 1 MiB bounded, uses exact keyword/operator tables, attaches
one-based line/column and zero-based byte spans, and stops at the first lexical
diagnostic. The implementation is Python 3.10+ standard library bootstrap
tooling and is not linked into Rynorkernel. See `rynorlang/README.md` and
`docs/design/rynorlang-lexer.md` for the exact contract. Stage 13 adds a host-side parser at `tools/rynorlang/parse.py`. It enforces colon return types, rejects trailing commas, implements the documented precedence including unary `!`, and produces a frozen temporary syntax tree with exact lexer spans and depth-bounded diagnostics. See `docs/design/rynorlang-parser.md`. Stage 14 adds a host-side semantic analyzer at `tools/rynorlang/analyze.py` that lowers the temporary tree to a stable, JSON-compatible AST schema (`Program, Function, Param, Block, Let, If, While, Return, ExprStmt, BinOp, UnOp, IntLit, BoolLit, StrLit, Var, Call`) with exact spans, deterministic symbol indices, and type checking (no implicit conversions, `unit` for missing return, forward function references allowed, no shadowing). Returned dictionaries/lists are caller-owned and mutable; “stable” describes the schema. See `docs/design/rynorlang-ast.md`. Stage 15a adds the typed IR, verifier, native emitter, and test-oracle execution described in §11 below. Stage 15b adds an edition-gated shell surface (`Pipeline`/`Cmd`/`Flag`/`Redirect` AST kinds behind `edition="shell"`, default v1 byte-identical): precedence-0 `|>` pipelines with `str`-only stages and the extended `unit` rule, zero-new-token commands with a stub registry, and a TEST-ONLY bounded host evaluator. No shell codegen, no userspace execution, no kernel evaluation. See `docs/design/rynorlang-shell-language.md`.

## 11. Compiler

Stage 15a implements RIR, a typed three-address CFG IR over the frozen
16-kind AST (`Module{funcs[]} Func{params,blocks[]} Block{id,instrs[],term}`,
every vreg typed `int|bool|str`), at `tools/rynorlang/rir.py`: a builder with
lexical scope tracking, a verifier with real CFG dominance plus a shared
liveness home-slot allocator (one implementation used by builder, verifier,
and emitter, so the three can never disagree), and canonical JSON/text dumps.
`tools/rynorlang/compile.py` (`rynorlangc`) lowers verified RIR to NASM for
the written SysV-subset ABI (`docs/design/rynorlang-abi.md`: unboxed
`i64`/`u8`/`(ptr,len)`, `unit` erased, spill-everything homes, no red zone,
no SIMD, `ud2` div-zero / hardware `#DE` overflow / `int3` fall-off).
`tools/rynorlang/harness_start.asm` (`_start` only) links and runs compiled
fixtures. A tree-walking evaluator exists ONLY as a differential test oracle
(`tools/rynorlang/interp.py`, honesty rules: separate code from the emitter,
same first-error order, mismatch = backend bug). The IR carries a
reserved-but-rejected `type:"value"` extension point for later dynamic-free
shell values — the verifier rejects it until Stage 19a, so the static fast
path never boxes. Separate diagnostics and deterministic outputs from host
I/O. No evaluation shortcuts masquerade as compilation. Foreign toolchains
may bootstrap code generation only when disclosed; eventual native output
must not depend on a host OS runtime. See `docs/design/rynorlang-rir.md`.
Stage 15b adds the edition-gated shell surface (see §10). Stage 16 turns the
chain into real host-native programs: `print(int/bool/str)` builtin with a
reserved name and an `rt_*` helper table in RIR, a labeled Linux x86-64 host
runtime (`tools/rynorlang/runtime/rt_linux.asm`: `_start`, exact-bytes
writers, static-only, no heap), and a deterministic program pipeline
(`tools/rynorlang/program.py`: `.rl` to NASM to object to ELF, low-8-bit
exit, no argv yet). Host-native test executables only: no RynorOS syscalls,
no userspace, no self-hosting. See `docs/design/rynorlang-program-model.md`.

## 12. Userspace

Plan: a minimal native library, shell, and `.rl` applications using RynorOS
services, not wrappers over host APIs. Initial trusted programs may execute in
kernel mode as an explicit intermediate milestone. Protected userspace requires
user-mode entry, validated memory access, syscalls, process exit, and loading.
Runtime I/O such as `print` will bind to real OS services only when available.
The API and ownership/error conventions are experimental. The native shell is a
RynorLang program (REPL + scripts, structured `|>` pipelines over typed values
per `docs/design/rynorlang-shell-language.md`), running only in CPL3; the
ring-0 monitor (`kernel/shell/`) stays a frozen trusted monitor and never gains
evaluation. RynorLang itself stays fully statically typed at every stage —
heterogeneity comes from explicit closed unions (`record`, `list<T,N>`,
`status`), never from dynamic typing; see `docs/design/rynorlang-runtime.md`.

## 13. Testing strategy

Implemented: repository/layout/transcript-parser/CLI/resource checks, host Python syntax compilation,
and real QEMU integration tests. Native code is assembled, compiled, and linked;
independent output directories yield byte-identical artifacts. QEMU captures
the original boot prefix plus ordered CPU initialization, real state diagnostics,
real E820/PMM initialization/full-pool tests, VM mapping/permission/fault/OOM tests,
then heap, timer setup/three real ticks, Stage 7 execution tests, Stage 8
keyboard `sendkey` handshake, Stage 9 framebuffer pixel evidence, the Stage 10
runtime worker-fold evidence, and Stage 11 shell evidence, then
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

## 14. Windows Compatibility Architecture

Planned — no implementation claimed. The design hosts a Windows-compatible execution environment under Rynorkernel; Rynorkernel remains the sole trusted computing base and owner of the machine. See `docs/design/windows-compatibility.md` for the full contract.

```
                         APPLICATIONS
                              │
             ┌────────────────┴────────────────┐
             │                                 │
       Native RynorOS                     Windows software
             │                                 │
             │                      ┌──────────┴──────────┐
             │                      │ Windows compatibility│
             │                      │ runtime / ABI / APIs │
             │                      │ drivers / devices   │
             │                      └──────────┬──────────┘
             │                                 │
             └────────────────┬────────────────┘
                              ▼
                        RYNORKERNEL
                              │
                    ┌─────────┼─────────┐
                    │         │         │
                   CPU       VM       devices
                    │         │         │
                    └─────────┴─────────┘
                              │
                           HARDWARE
```

**CPU privilege vs. architectural trust.** x86-64 `CPL0` is maximal CPU privilege; there is no `ring above ring 0`. RynorOS today runs only `CPL0` (`GDT 0x08/0x10`, no TSS/IST, no ring 3). The Windows environment is not “above” Rynorkernel in ring terms. The intended boundary is:

```text
Hardware
   │
   ▼
Rynorkernel privileged layer  (Ring 0, owns CR3/IDT/GDT/TSS/PMM/VM/IOMMU)
   │
   ├── isolation / virtualization boundary
   │
   ▼
Windows-compatible execution environment
   │
   ├── Windows user-mode compatibility (Ring 3, U/S=1, per-process PML4)
   ├── Windows kernel/driver compatibility (contained, not direct Rynorkernel access)
   └── virtual device model (virtio-GPU/virgl or Vulkan translation or passthrough)
```

Two honest implementations satisfy this without false ring claims:

* **Native isolated subsystem** (preferred, matches Stage 18a): Rynorkernel stays sole Ring 0; Windows code is deprivileged to Ring 3 behind a validated syscall gate + `U/S` paging (`vm_create` activation, `TSS.RSP0`, `syscall/sysret`). No VT-x required. This is OS-level isolation, not virtualization.
* **Type-1 hypervisor** (alternative): Rynorkernel in VMX Root Ring 0; an unmodified Windows kernel runs in VMX Non-Root Ring 0 with EPT/NPT, vAPIC/vPIC, vPCI and IOMMU isolation. Requires `VT-x + EPT + VPID + IOMMU` and a full device model. Both are described as *architectural trust boundary ≠ CPU privilege level*.

**Windows execution environment — what must be reproduced vs. virtualized.** User-mode: PE/COFF loader, `ntdll` syscall thunks, PEB/TEB, handle/object manager, `VirtualAlloc` (reserve/commit/`PAGE_GUARD`), dispatcher objects (`Event`/`Mutex`/`Semaphore`/`WaitableTimer` with blocking `THREAD_WAITING` queues), registry hives (fake or host-backed), TLS/FSBASE/GSBASE, SEH/VEH/`KiUserExceptionDispatcher` with x64 `pdata/xdata` unwind. Kernel/driver: WDM/WDF `DriverEntry`, `IRP`/`MDL`, DMA/scatter-gather via IOMMU, PnP, NDIS, `DxgKrnl`/`VidMm`/`VidSch`. Current RynorOS has none of this — only supervisor `PMM/VM/heap/kstack` and `FNV-1a` ring-0 services; all pointers are trusted.

**Isolation.** `PMM` frames and `VM` tables are per-address-space, never shared writable host↔guest; `IOMMU` isolates DMA; `SMEP/SMAP/PKE` (future) and `W^X` (`NX` already enforced) block `U→K` access; handle tables and `PML4 509/510/511` (MMIO/window) are kernel-private. No `vm_frame_access` to userspace, no `W+X` leaves.

**Graphics.** Stage 9 provides only a single uncached BGRX LFB at `VM_MMIO_BASE` (slot 509). Windows graphics needs a staged path: (1) `llvmpipe`/`WARP` software rasterizer into the LFB (proves DXGI without GPU), (2) `virtio-gpu` virgl/venus paravirt queue, (3) `DXVK`/`vkd3d-proton` → Vulkan (`vkQueueSubmit`) with shader compilation (`HLSL→DXIL→SPIR-V`, `DXC`), (4) VFIO passthrough / SR-IOV with native vendor KMD. Each step needs PCIe enumeration (`MSI-X`, `Resizable BAR`), display KMS atomic modeset, and a `TTM`/`GEM`-like video-memory manager — none of which Stage 9 provides.

**Security / anti-cheat — compatibility vs. bypass.** See `docs/windows-compatibility-program.md` for the mandatory classification:

```text
A — no kernel components          (pure Win32, achievable with user-mode env)
B — supported runtime deps         (VC++/DirectX redist, still user-mode)
C — requires kernel-driver semantics (needs contained NT driver env or Windows VM)
D — requires vendor approval       (Vanguard/EAC-HVCI+TPM attestation, whitelist)
E — unsupported (would require hide/spoof/disable CI/PatchGuard/VBS)
```

RynorOS will **not** spoof `CPUID` hypervisor bit, hide `MSR 0x40000000`, forge `TPM2_Quote` PCRs, patch `PatchGuard`, or disable `DSE`/`HVCI`. For C/D the legitimate architecture hosts a **genuine Windows kernel** under a hypervisor (`EPT`/`IOMMU`/`fTPM`) so the anti-cheat sees a real `ntoskrnl`/`CI.dll`; for D the vendor must explicitly support the `RynorOS-Hv` platform. Claims use `planned/research/prototype/QEMU-verified/bare-metal-verified/vendor-supported`, never `Fortnite works` before certification.

**Dependencies.** No Windows stage is placed before `18a Protected userspace` and `17a/b` storage; `21b/c` additionally need blocking scheduler waits and syscall/CR3 switching; `21e`/`21k` need PCIe/IOMMU/APIC/HPET and the graphics stack. Until then every Windows milestone is `research-only`.

## 15. Eventual self-hosting strategy

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

## 16. Documentation contract

Every subsystem document must state purpose, public interfaces, invariants,
implementation status, tests, and known limitations. Use
`docs/design/subsystem-template.md`. Empty reserved directories expose no API.
Decisions that change the above proposals require rationale, dependencies,
acceptance tests, and updates to the roadmap and metadata when applicable.
