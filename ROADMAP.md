# Roadmap

Stages 0–9 are implemented. Stage 10 onward remains **planned**; language and ABI
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
| 10 | Basic kernel runtime | Bounded strings/buffers and runtime services built on the tested kernel threads; protected user tasks remain Stage 18. |
| 11 | Shell/monitor | Kernel monitor accepts real input and exposes only implemented services; parsing/error tests. |
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

## Dependencies and scope gates

Stages 12–14 can proceed on a host independently of hardware bring-up. Stage 15
needs a real execution harness and does not by itself provide OS integration.
Stage 16 needs runtime/loader work in addition to a compiler, but can use a
read-only boot bundle before disk storage. Stage 18 depends on memory isolation,
tasks, executable loading, and storage. Stages 19–20 need more language facilities
than the initial syntax draft; Stage 20 needs original native system tooling
and a deliberate plan for any bootstrap loader still in use.

Networking, GUI composition, SMP, package management, multi-user security,
POSIX compatibility, and broad hardware support are not initial milestones.
Progress requires passing acceptance checks and updated status documentation,
not just directories, stubs, screenshots, or hardcoded success messages.
