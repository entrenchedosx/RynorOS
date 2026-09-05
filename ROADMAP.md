# Roadmap

Stages 0–14 are implemented. Stage 15 onward remains **planned**; language and ABI
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
| 12 | RynorLang lexer — implemented | One Python 3.10+ stdlib implementation at `tools/rynorlang/lex.py`; ASCII/1 MiB bounds; exact frozen keywords, identifiers, decimal signed-64-bit magnitude, four string escapes, maximal-munch operators including standalone `!`, source spans, and first-error diagnostics. The 49 strict lexer tests use 16 valid and 19 invalid fixtures. Lexical failure exits 1. No parsing, AST, compilation, or execution. See `docs/design/rynorlang-lexer.md` and `docs/reports/stage12.md`. |
| 13 | RynorLang parser — implemented | One Python 3.10+ stdlib implementation at `tools/rynorlang/parse.py`; parses the frozen grammar into a frozen temporary syntax tree with exact spans, colon return types, no trailing list commas, documented precedence/associativity, nearest-`if` else binding, and depth-bound diagnostics. The 52 strict parser tests use 14 valid and 21 invalid fixtures and five live mutations. No stable AST, name resolution, type checking, compilation, or execution. See `docs/design/rynorlang-parser.md` and `docs/reports/stage13.md`. |
| 14 | AST and semantics — implemented | One Python 3.10+ stdlib implementation at `tools/rynorlang/analyze.py`; lowers the Stage 13 temporary tree to a stable JSON-compatible AST with exact spans, deterministic symbols, and type checking (no implicit conversions, `unit` for missing return, forward refs allowed, no shadowing). The 63 strict semantics tests use 12 valid and 20 invalid fixtures and twenty-one behavioral mutations, plus an 8-test public-API gauntlet. No interpretation or codegen. See `docs/design/rynorlang-ast.md` and `docs/reports/stage14.md`. |
| 15a | Typed IR + native compiler (static core) | RIR spec (typed 3-address CFG: `Module/Func/Block`, instrs `const/copy/binop/unop/call/ret`, terms `jmp/br_cond/ret/unreachable`) with JSON goldens and a verifier; written SysV-subset ABI (unboxed `i64`/`u8`/`(ptr,len)`, `unit` erased, no red zone/SIMD); NASM emitter for arithmetic/comparison/branches/loops/calls; disclosed host harness links and runs fixtures; tree-walk evaluator exists only as a differential test oracle with honesty rules. Exit: compiled fixtures behave bit-identically to the oracle; bad fixtures rejected pre-emit; v2 AST kinds explicitly rejected (`COMP_V2_UNSUPPORTED`). See `docs/design/rynorlang-runtime.md` (values/ABI) and the Stage 15 report. No dynamic values, no GC, no shell syntax. |
| 15b | Shell language surface (host-side, edition-gated) | `Pipeline`/`Cmd` AST kinds plus `|>` behind an explicit shell edition flag; default v1 grammar byte-identical (all v1 fixtures pass unchanged); pipelines are a precedence-0 expression level (composable: `let x: str = a \|> b;`), each MVP stage statically `str`-typed with the `unit` rule extended (non-final stages non-`unit`; `unit` pipelines only as `ExprStmt`); command argv need no new lexer tokens (bare words = `IDENT`, flags = span-adjacent `-`+`IDENT`, redirects reuse `>`/`>>`); property access (`.`, `Member`) deferred to 19a with records; new `shell-edition/` good/bad fixtures with exact spans; edition-gate tests (v1 rejects with old codes; Stage-15a compiler rejects v2 kinds). Exit: shell-edition programs analyze deterministically; v1 contract untouched. No codegen for new kinds yet. See `docs/design/rynorlang-shell-language.md`. |
| 16 | Native `.rl` programs (trusted batch) | Read-only boot-bundle loader (magic+version+count manifest, per-entry `{name[32],len,fnv1a}`, whole bundle `< 64 KiB`, validated before any byte executes; corrupt input halts, never executes); `print(int/bool/str)` bound to serial; kernel-mode execution explicitly labeled UNPROTECTED in the banner. Emitted L1 code must satisfy verifier-checked bounds, not just fixture equivalence: direct calls and static jumps only (no computed/indirect branches, no `syscall` instruction — loader scans opcodes and refuses), all memory accesses to compiler-known stack slots/arena ranges with explicit length checks on string ops, statically bounded frames, cap exhaustion halting with a diagnostic (never trap, never silent wrap); guest runs under the normal IRQ0-preemptible contract (no locks held across service calls, bounded non-blocking). Exit: QEMU image runs bundled `.rl` printing actual computed values; mutated emitter fails equivalence; bundle mutations (bad magic/count/truncate/checksum/OOM) halt; a forbidden-opcode fixture is refused by the loader. No interactive evaluation, no paste-to-execute, no ring-0 REPL. |
| 17a | Block storage | One tested block driver on disposable images, with bounds and I/O failure tests. |
| 17b | Native filesystem read | Specify original versioned format and image tool; verify directory/file reads and corrupted-image rejection. |
| 17c | Native filesystem write | Allocation/writes and explicitly tested recovery guarantees; no premature durability claim. |
| 18a | Protected userspace | User-mode processes, loader validation, syscalls, address-space isolation, and clean exit tests. |
| 18b | Native shell, REPL, and library (userspace) | RynorLang REPL + file-backed scripts + standard library in CPL3 via syscalls; per-block evaluation with whole-buffer re-analysis (deterministic symbols); session + per-submission arenas with zero-leak walks; `Ctrl-C` abort preserves session; true streaming pipelines with scheduler backpressure; `$?`-style status query. Exit: denied OOB/mem and bad-syscall tests trap; shell runs from the filesystem, not the bundle; placement guard asserts no `kernel/shell/repl*` ever exists. The ring-0 monitor stays frozen and gains no evaluation. |
| 19a | Language readiness for self-hosting | Specify and implement needed aggregate types (`record`, `list<T,N>`, `status`/`Result`, `map`), `match` narrowing, string/byte ops, fixed-int/bitops, `break`/`continue`, modules (`use "path";`, file = module, hash-pinned manifests, cycle = error), memory and file APIs, error/diagnostic convention, determinism/canonical-encoding spec; conformance tests. |
| 19b | Self-hosting RynorLang | RynorLang compiler written in the frozen core subset (`dialect="core"`, enforced by `--profile=strict` + CI gate) runs on RynorOS and rebuilds itself; deterministic comparison and seed instructions. |
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

Stages 12–14 can proceed on a host independently of hardware bring-up. Stage 15a
needs a real execution harness and does not by itself provide OS integration.
Stage 15b is host-side language surface only (no codegen, no execution) and
likewise needs no hardware. Stage 16 needs runtime/loader work in addition to
a compiler, but can use a read-only boot bundle before disk storage. The shell
language (15b surface, 18b REPL) never executes in ring 0: 16 is batch-only
and labeled unprotected; interactive evaluation waits for 18a userspace.
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
