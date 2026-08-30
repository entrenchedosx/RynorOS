# Roadmap

Stages 0–7 are implemented. Stage 8 onward remains **planned**; language and ABI
details are **experimental** until specified and tested. Each row is a small
delivery target with an observable exit condition, not a claim of functionality.
Stages may be split further. Numbering is a working sequence, not a schedule.

The incoming Stage 6 heap required correctness repairs despite passing its
original suite, and the Stage 7 scheduling milestone was rebuilt from that
baseline. Current evidence and limitations are recorded in
`docs/reports/codebase-audit.md`; stage labels do not imply production readiness.

| Stage | Milestone | Exit condition |
| --- | --- | --- |
| 0 | Repository foundation — implemented | Required paths, metadata, documents, host checks, and repository tests pass; no OS binaries claimed. |
| 1 | Bootable kernel — implemented | Original BIOS loader and freestanding x86-64 kernel; two real serial lines; deterministic raw image; 26 repository and five integration tests pass. See `docs/reports/stage1.md`. |
| 2 | CPU initialization and exception diagnostics — implemented | Kernel GDT/IDT loaded and checked; #DE/#DB/#BP/#UD/#GP/#PF diagnostics execute in QEMU; controlled breakpoint restores registers/flags/stack; 33 repository and 11 integration tests pass. See `docs/reports/stage2.md`. |
| 3 | External interrupt controller and hardware timer — implemented | PIC remap/masks/manual EOI, separate IRQ dispatch, PIT mode 2, three actual IRQ0 ticks and IRETQ returns; QEMU negative/regression tests; canonical icon packaged separately and reproducibly. No scheduler. See `docs/reports/stage3.md`. |
| 4 | Physical memory manager — implemented | Real BIOS E820 handoff; conservative map normalization; linker/firmware/metadata reservations; 4096-byte frame allocation, release, reuse, accounting and full real-pool OOM tests; multiple-RAM QEMU and corrupted-map tests; timer works after PMM. See `docs/reports/stage4.md`. No virtual-memory manager or isolation. |
| 5 | Virtual memory — implemented | PMM-owned four-level 4 KiB tables replace boot CR3; kernel RX/R/NX/RW layout, transactional map/unmap, permissions, translation, INVLPG, controlled real #PF, table ownership/OOM rollback, and full regressions. See `docs/reports/stage5.md`. No user mode, processes, COW, swap or heap. |
| 6 | Kernel heap — implemented | Bounded allocation/free with alignment, corruption, OOM/failure, and stress tests against real frames via the Stage 5 VM map; two-sided coalescing and strict host transcript validation. See `docs/reports/stage6.md`. No user heap, realloc or grow/shrink. |
| 7 | Kernel execution infrastructure — implemented | Real per-thread kernel stacks (each with a faulting guard page) backed by PMM frames in a dedicated virtual slot; genuine context switching (`sched_resume`/`thread_switch`); a deterministic round-robin scheduler preempted by the PIT IRQ0; bounded guest self-test proving real preemption, distinct worker stacks, join/reaping and exact PMM balance; strict host transcript validation; broken-variant failures halt with `[SCHED] failure=`. See `docs/reports/stage7.md`. No user mode, processes or SMP. |
| 8 | Hardware input | One documented keyboard device feeds a tested event queue; overflow behavior defined. |
| 9 | Framebuffer/text output | Validated display information and bounds-safe drawing/text; serial diagnostics retained. |
| 10 | Cooperative user task lifecycle | User-level tasks with tested context save/restore and resource ownership, distinct from the Stage 7 kernel thread scheduler. |
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
