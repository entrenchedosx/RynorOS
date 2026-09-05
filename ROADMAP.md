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
| 15a | Typed IR + native compiler (static core) — implemented | One Python 3.10+ stdlib implementation: `tools/rynorlang/rir.py` (typed 3-address CFG builder + verifier with real dominance, shared liveness slot allocator, canonical dumps), `tools/rynorlang/compile.py` (`rynorlangc`: NASM emitter for the SysV-subset ABI — unboxed `i64`/`u8`/`(ptr,len)`, `unit` erased, spill-everything homes, `ud2`/`int3` traps), and `tools/rynorlang/interp.py` (differential test oracle only, with honesty rules). The 48 RIR tests and 39 compiler tests use 17 good + 3 trap fixtures with JSON goldens, verifier units, determinism checks, native differential runs, and a 21-mutation matrix; bad fixtures rejected pre-emit; v2 AST kinds explicitly rejected (`COMP_V2_UNSUPPORTED`). See `docs/design/rynorlang-rir.md`, `docs/design/rynorlang-abi.md`, and `docs/reports/stage15a.md`. No dynamic values, no GC, no shell syntax. |
| 15b | Shell language surface (host-side, edition-gated) — implemented | One Python 3.10+ stdlib implementation: edition-gated `|>` token plus `Pipeline`/`Cmd` AST kinds behind `edition="shell"` (alias `"shell-preview"`; default v1 byte-identical); precedence-0 left-associative pipelines; `str`-only stages with the extended `unit` rule; zero-new-token commands (bare words, adjacent `-flags`, quoted `>`/`>>` targets); piped input fills a non-head command's first parameter; stub registry for host tests; 15a backend rejects v2 kinds; TEST-ONLY bounded evaluator plus REPL block accumulator. The 47 shell tests use 12 good + 11 bad `shell-edition/` fixtures. See `docs/design/rynorlang-shell-language.md` and `docs/reports/stage15b.md`. |
| 16 | Native `.rl` programs (host-native) — implemented | One Python 3.10+ stdlib program pipeline at `tools/rynorlang/program.py` (`.rl` → RIR → NASM → object → Linux x86-64 ELF via NASM + LLD, deterministic byte-identical artifacts) plus a labeled host program runtime (`tools/rynorlang/runtime/rt_linux.asm`: `_start`, exact-bytes `print(int/bool/str)`, static-only, no heap); entry stays `fn main(): int|unit` with no args (argc/argv explicitly deferred); exit is low-8-bit with documented truncation (300 → 44, -1 → 255; full width via `print`); `print` builtin with reserved name and `rt_*` helper table (`COMP_V2_UNSUPPORTED` for shell kinds still); TEST-ONLY oracle extended with print capture for stdout differentials. The 44 program tests use 22 good + 2 trap `programs/` fixtures with exit/stdout goldens, generated matrices, authenticity attacks, and a 5-mutation matrix. Host-native only: no RynorOS syscalls, no userspace, no self-hosting. Deferred (future native-execution milestone, requirements preserved): read-only boot-bundle loader (magic+version+count manifest, per-entry `{name[32],len,fnv1a}`, whole bundle `< 64 KiB`, validated before any byte executes; corrupt input halts, never executes); `print` bound to serial; UNPROTECTED kernel-mode batch with opcode-scanning loader, known-slot memory checks, and the IRQ0-preemptible guest contract. See `docs/design/rynorlang-program-model.md` and `docs/reports/stage16.md`. |
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
Stage 15b is host-side language surface only (no codegen, no in-OS execution;
a TEST-ONLY host evaluator proves semantics) and
likewise needs no hardware. Stage 16 is a host-native program toolchain
(.rl to Linux ELF test executables) needing no hardware; the later
kernel-batch loader can use a read-only boot bundle before disk storage. The shell
language (15b surface, 18b REPL) never executes in ring 0: the deferred
batch loader (not Stage 16) is the UNPROTECTED one; interactive evaluation waits for 18a userspace.
Stage 16 is implemented as host-native programs (see row 16); the kernel
batch loader remains future work. Stage 17a/b need block-device and
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
