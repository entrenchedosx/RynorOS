# Roadmap

Stages 0–11 are implemented. Stage 12 onward remains **planned**; language and ABI
details are **experimental** until specified and tested. Each row is a small
delivery target with an observable exit condition, not a claim of functionality.
Stages may be split further. Numbering is a working sequence, not a schedule.

The incoming Stage 6 heap required correctness repairs despite passing its
original suite, and the Stage 7 scheduling milestone was rebuilt from that
baseline. The independent Stage 7 review found further ownership and scheduling
defects and repaired them. Current evidence is in `docs/reports/stage7-audit.md`;
`codebase-audit.md` is the earlier baseline. Stage labels do not imply production readiness.

| Stage | Milestone | Exit condition |
| --- | --- | --- |
| 0 | Repository foundation — implemented | Required paths, metadata, documents, host checks, and repository tests pass; no OS binaries claimed. |
| 1 | Bootable kernel — implemented | Original BIOS loader and freestanding x86-64 kernel; two real serial lines; deterministic raw image; 26 repository and five integration tests pass. See `docs/reports/stage1.md`. |
| 2 | CPU initialization and exception diagnostics — implemented | Kernel GDT/IDT loaded and checked; #DE/#DB/#BP/#UD/#GP/#PF diagnostics execute in QEMU; controlled breakpoint restores registers/flags/stack; 33 repository and 11 integration tests pass. See `docs/reports/stage2.md`. |
| 3 | External interrupt controller and hardware timer — implemented | PIC remap/masks/manual EOI, separate IRQ dispatch, PIT mode 2, three actual IRQ0 ticks and IRETQ returns; QEMU negative/regression tests; canonical icon packaged separately and reproducibly. No scheduler. See `docs/reports/stage3.md`. |
| 4 | Physical memory manager — implemented | Real BIOS E820 handoff; conservative map normalization; linker/firmware/metadata reservations; 4096-byte frame allocation, release, reuse, accounting and full real-pool OOM tests; multiple-RAM QEMU and corrupted-map tests; timer works after PMM. See `docs/reports/stage4.md`. No virtual-memory manager or isolation. |
| 5 | Virtual memory — implemented | PMM-owned four-level 4 KiB tables replace boot CR3; kernel RX/R/NX/RW layout, transactional map/unmap, permissions, translation, INVLPG, controlled real #PF, table ownership/OOM rollback, and full regressions. See `docs/reports/stage5.md`. No user mode, processes, COW, swap or heap. |
| 6 | Kernel heap — implemented | Bounded allocation/free with alignment, corruption, OOM/failure, and stress tests against real frames via the Stage 5 VM map; two-sided coalescing and strict host transcript validation. See `docs/reports/stage6.md`. No user heap, realloc or grow/shrink. |
| 7 | Kernel execution infrastructure — implemented and audited | PMM-backed RW/NX worker stacks with unbacked guards and checked ownership; real context switching and PIT-driven round-robin preemption; non-reused thread IDs and safe reap; tested rollback, hardware guard/NX faults, invalid handoffs and non-yielding worker execution. See `docs/reports/stage7-audit.md` for evidence and limits. No user mode, processes or SMP. |
| 8 | Hardware input — implemented, bounded subset | Explicit i8042/keyboard scan setup; IRQ1 status/data handling; 31-sample FIFO/drop-newest queue with loss notification; physical Set-1 subset; host-selected input checked against QEMU device/PIC/I/O traces while IRQ0 schedules a worker. See `docs/reports/stage8-audit.md` for independent evidence and limitations. No full text input, USB or physical-hardware certification. |
| 9 | Framebuffer/text output — implemented, audit evidence in report | Validated PCI/BGA device handoff; uncached RW/NX framebuffer; bounds-safe pixels/rectangles and bounded text subset; serial retained. Guarded algorithm tests, MMIO/OOM rollback and complete QEMU physical-byte/scanout evidence. See `docs/reports/stage9-audit.md`. No GUI, console, resizing or general GPU driver. |
| 10 | Basic kernel runtime — implemented | Bounded strings/buffers and runtime services built on the tested kernel threads; digest/format/wrap evidence independently recomputed on the host; mutation and regression gates. See `docs/reports/stage10-audit.md` (the superseded `stage10.md` is the milestone record). Protected user tasks remain Stage 18; no userspace or syscalls. |
| 11 | Shell/monitor — implemented | Ring-0 kernel monitor with real IRQ1 keyboard input, bounded 64-byte line buffer and 12-token tokenizer, strict argument validation, `help`/`version`/`echo`/`clear` + `upper`/`count`/`digest` via Stage 10 services; synthetic and 39-key real QEMU interactive sessions with per-key `scan`/`ascii`/`line` and per-command `exec`/`result` evidence; 110 repository + 155 integration test methods (147 non-shell + 8 shell), QEMU 9-configuration matrix, reproducible. See `docs/reports/stage11.md`. |
| 12 | RynorLang lexer | Freeze lexical subset; tokenize `.rl` input with spans and invalid-token tests; no execution claim. |
| 13 | RynorLang parser | Parse selected syntax into a documented temporary syntax tree; reject malformed input with locations. |
| 14 | AST and semantics | Stable AST, name resolution, type checks, and negative fixtures; replace temporary tree as needed. |
| 15 | Compiler | Document ABI/format; emit native arithmetic/control-flow/function code; test linking and real execution in a disclosed harness. |
| 16 | Native `.rl` programs | Compile and load bounded trusted programs from a boot bundle; actual runtime I/O; kernel-mode execution labeled unprotected. |
| 17a | Block storage | One tested block driver on disposable images, with bounds and I/O failure tests. |
| 17b | Native filesystem read | Specify original versioned format and image tool; verify directory/file reads and corrupted-image rejection. |
| 17c | Native filesystem write | Allocation/writes and explicitly tested recovery guarantees; no premature durability claim. |
| 18a | Protected userspace | User-mode processes, loader validation, syscalls, address-space isolation, and clean exit tests. |
| 18b | Native shell and library | Move shell into userspace and run file-backed `.rl` applications; test denied/invalid operations. |
| 19a | Language readiness for self-hosting | Specify and implement needed aggregate types, modules, memory and file APIs; conformance tests. |
| 19b | Self-hosting RynorLang | RynorLang compiler written in `.rl` runs on RynorOS and rebuilds itself; deterministic comparison and seed instructions. |
| 20a | Native system tools | Native build/link/image tools replace documented host dependencies; audited reproducible outputs. |
| 20b | Self-hosting RynorOS | Rebuild kernel, libraries, shell, applications, and boot artifacts inside RynorOS from source, with reproducibility evidence. |
| 21a | Windows executable format foundation | PE/COFF parsing, header/section validation, relocations, imports/exports, TLS, exception/unwind, x64 (0x8664) only; malformed rejection; no execution. |
| 21b | Windows user-mode ABI foundation | Handles, virtual memory reserve/commit, synchronization primitives, timers, environment, DLL load model, TLS, Structured Exception Handling substrate; small explicit contracts. |
| 21c | Win32 API compatibility | kernel32-like file/memory/thread/sync/timer/console/process semantics; NT object semantics; tested against real binaries (no proprietary assets shipped). |
| 21d | Windows GUI compatibility | Window creation, input, message loop, GDI-compatible basics, clipboard, system UI primitives; separate from native RynorOS GUI. |
| 21e | Windows graphics compatibility | Direct3D/DXGI boundary, shaders, GPU resources, command submission, presentation, fences; implementation technology decided later from hardware/licensing. |
| 21f | Windows audio/input/device compatibility | XInput/keyboard/mouse/audio/gamepad, device notifications; native drivers remain separate. |
| 21g | PE loader + DLL ecosystem | In-image loader, imports/exports/TLS/relocations/unwind, API-sets, versioned compatibility libraries; tested where legally permitted. |
| 21h | Advanced Windows runtime | SEH/VEH, fibers, TLS, overlapped I/O, named objects, registry/services, high-resolution timers; no speculative APIs. |
| 21i | Windows game compatibility foundation | Harness for startup, DLL load, graphics init, shaders, input/audio/filesystem/threads/timers/sockets/controllers; reproducible fixtures. |
| 21j | Multiplayer/network compatibility | Winsock/DNS/UDP/TCP/timers/threading; separate from game-specific behavior. |
| 21k | Windows driver compatibility environment | Contained Windows kernel/driver semantics under Rynorkernel; explicit security boundary; no uncontrolled driver access to Rynorkernel. |
| 21l | Advanced game compatibility | Progressive matrix: simple app → 3D → offline → online → high-performance multiplayer; per-subsystem failure tracking. |
| 21m | Compatibility certification framework | Standard test format (binary/environment/API/driver/graphics/input/network/security/observed/limitations); supported = passes declared criteria. |

## Windows Compatibility Program — dependencies and placement

The Windows Compatibility Program (21a–21m) is intentionally placed **after** the native RynorOS foundation (Stages 0–20). It is not inserted after Stage 10. Each 21x milestone explicitly depends on protected userspace, process/address-space management, executable loading, graphics, storage, networking, audio/input, and Windows API abstractions. Meaningful game compatibility (21i–21l) additionally requires the graphics stack, driver containment (21k), and the certification framework (21m). Stages labeled 21a–21m are a working sequence; they may be split further without renumbering Stages 0–20.

Security classification for every Windows workload (see `docs/design/windows-compatibility.md`): **A** no kernel components, **B** supported runtime deps, **C** requires kernel-driver semantics, **D** requires vendor approval, **E** unsupported (hide/spoof/bypass would be required). No blanket claim is made for category D/E. Anti-cheat/design choices must not rely on spoofing, hiding virtualization, or defeating code integrity — see the containment model.

## Dependencies and scope gates

Stages 12–14 can proceed on a host independently of hardware bring-up. Stage 15
needs a real execution harness and does not by itself provide OS integration.
Stage 16 needs runtime/loader work in addition to a compiler, but can use a
read-only boot bundle before disk storage. Stage 17a/b need block-device and
filesystem work. Stage 18 depends on memory isolation, tasks, executable
loading, and storage. Stages 19–20 need more language facilities than the
initial syntax draft; Stage 20 needs original native system tooling and a
deliberate plan for any bootstrap loader still in use. **Stage 21a requires
18a (protected userspace) and 17a/b (storage) for file-backed images; 21b/c
require 21a plus blocking scheduler waits and syscall/address-space switching;
21d/e require native or paravirt display/GPU (Stage 9 framebuffer alone is
insufficient); 21k requires the isolation/virtualization boundary and VT-x/IOMMU
readiness; 21i–21m require the full chain and must be labeled research-only
until prerequisites are verified.**

Networking, GUI composition, SMP, package management, multi-user security,
POSIX compatibility, and broad hardware support are not initial milestones.
Windows game/graphics/driver compatibility is explicitly **not** an initial
milestone and depends on the chain above. Progress requires passing acceptance
checks and updated status documentation, not just directories, stubs,
screenshots, or hardcoded success messages.
