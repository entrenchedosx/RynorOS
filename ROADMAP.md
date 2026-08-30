# Roadmap

Stages 0–2 are implemented. Stage 3 onward remains **planned**; language and ABI
details are **experimental** until specified and tested. Each row is a small
delivery target with an observable exit condition, not a claim of functionality.
Stages may be split further. Numbering is a working sequence, not a schedule.

| Stage | Milestone | Exit condition |
| --- | --- | --- |
| 0 | Repository foundation — implemented | Required paths, metadata, documents, host checks, and repository tests pass; no OS binaries claimed. |
| 1 | Bootable kernel — implemented | Original BIOS loader and freestanding x86-64 kernel; two real serial lines; deterministic raw image; 26 repository and five integration tests pass. See `docs/reports/stage1.md`. |
| 2 | CPU initialization and exception diagnostics — implemented | Kernel GDT/IDT loaded and checked; #DE/#DB/#BP/#UD/#GP/#PF diagnostics execute in QEMU; controlled breakpoint restores registers/flags/stack; 33 repository and 11 integration tests pass. See `docs/reports/stage2.md`. |
| 3 | Device interrupt system — planned | Extend entry conventions for external IRQs; select/remap controller, implement acknowledgment, and verify a timer interrupt while retaining Stage 2 exception tests. |
| 4 | Physical memory manager | Parse real boot memory map; reserve live regions; test allocation, exhaustion, and invalid/double frees. |
| 5 | Virtual memory | Owned page tables with permission tests, map/unmap checks, and expected page faults. |
| 6 | Kernel heap | Bounded allocation/free with alignment, failure, and stress tests. |
| 7 | Hardware input | One documented keyboard device feeds a tested event queue; overflow behavior defined. |
| 8 | Framebuffer/text output | Validated display information and bounds-safe drawing/text; serial diagnostics retained. |
| 9 | Basic kernel runtime | Minimal strings/buffers and cooperative task lifecycle with tested context save/restore and resource ownership. |
| 10 | Shell/monitor | Kernel monitor accepts real input and exposes only implemented services; parsing/error tests. |
| 11 | RynorLang lexer | Freeze lexical subset; tokenize `.rl` input with spans and invalid-token tests; no execution claim. |
| 12 | RynorLang parser | Parse selected syntax into a documented temporary syntax tree; reject malformed input with locations. |
| 13 | AST and semantics | Stable AST, name resolution, type checks, and negative fixtures; replace temporary tree as needed. |
| 14 | Compiler | Document ABI/format; emit native arithmetic/control-flow/function code; test linking and real execution in a disclosed harness. |
| 15 | Native `.rl` programs | Compile and load bounded trusted programs from a boot bundle; actual runtime I/O; kernel-mode execution labeled unprotected. |
| 16a | Block storage | One tested block driver on disposable images, with bounds and I/O failure tests. |
| 16b | Native filesystem read | Specify original versioned format and image tool; verify directory/file reads and corrupted-image rejection. |
| 16c | Native filesystem write | Allocation/writes and explicitly tested recovery guarantees; no premature durability claim. |
| 17a | Protected userspace | User-mode processes, loader validation, syscalls, address-space isolation, and clean exit tests. |
| 17b | Native shell and library | Move shell into userspace and run file-backed `.rl` applications; test denied/invalid operations. |
| 18a | Language readiness for self-hosting | Specify and implement needed aggregate types, modules, memory and file APIs; conformance tests. |
| 18b | Self-hosting RynorLang | RynorLang compiler written in `.rl` runs on RynorOS and rebuilds itself; deterministic comparison and seed instructions. |
| 19a | Native system tools | Native build/link/image tools replace documented host dependencies; audited reproducible outputs. |
| 19b | Self-hosting RynorOS | Rebuild kernel, libraries, shell, applications, and boot artifacts inside RynorOS from source, with reproducibility evidence. |

## Dependencies and scope gates

Stages 11–13 can proceed on a host independently of hardware bring-up. Stage 14
needs a real execution harness and does not by itself provide OS integration.
Stage 15 needs runtime/loader work in addition to a compiler, but can use a
read-only boot bundle before disk storage. Stage 17 depends on memory isolation,
tasks, executable loading, and storage. Stage 18 needs more language facilities
than the initial syntax draft; Stage 19 needs original native system tooling
and a deliberate plan for any bootstrap loader still in use.

Networking, GUI composition, SMP, package management, multi-user security,
POSIX compatibility, and broad hardware support are not initial milestones.
Progress requires passing acceptance checks and updated status documentation,
not just directories, stubs, screenshots, or hardcoded success messages.
