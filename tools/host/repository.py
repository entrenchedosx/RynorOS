"""Current repository/asset contract; this does not check kernel execution."""

import json
from pathlib import Path
from resources import read_icon


SOURCE_EXTENSION = ".rl"
REQUIRED_DIRECTORIES = (
    "kernel", "kernel/arch", "kernel/core", "kernel/mm", "kernel/interrupts",
    "kernel/drivers", "kernel/runtime", "kernel/shell", "kernel/include", "boot", "rynorlang", "rynorlang/lexer",
    "rynorlang/parser", "rynorlang/ast", "rynorlang/compiler", "rynorlang/runtime",
    "rynorlang/tests", "rynorlang/examples", "user", "user/shell", "user/lib",
    "user/apps", "tools", "tools/build", "tools/host", "tools/rynorlang",
    "tools/rynorlang/runtime",
    "tests", "tests/repository", "tests/fixtures/rynorlang/lexer/good",
    "tests/fixtures/rynorlang/lexer/bad", "tests/fixtures/rynorlang/parser/good",
    "tests/fixtures/rynorlang/parser/bad", "tests/fixtures/rynorlang/semantics/good",
    "tests/fixtures/rynorlang/semantics/bad",
    "tests/fixtures/rynorlang/compiler/good",
    "tests/fixtures/rynorlang/compiler/trap",
    "tests/fixtures/rynorlang/shell-edition/good",
    "tests/fixtures/rynorlang/shell-edition/bad",
    "tests/fixtures/rynorlang/programs/good",
    "tests/fixtures/rynorlang/programs/trap",
    "tests/kernel", "tests/rynorlang", "tests/integration", "docs", "docs/design",
    "docs/reports", "build", "kernel/arch/x86_64", "assets", "assets/branding",
)
RESERVED_DIRECTORIES = (
    "kernel/drivers",
    "rynorlang/lexer", "rynorlang/parser", "rynorlang/ast",
    "rynorlang/compiler", "rynorlang/runtime", "rynorlang/tests", "user/shell",
    "user/lib", "user/apps", "tests/kernel", "tests/rynorlang",
    "build",
)
REQUIRED_FILES = (
    "README.md", "LICENSE", "ROADMAP.md", "ARCHITECTURE.md", "CONTRIBUTING.md",
    ".gitignore", ".gitattributes", "project.json", "kernel/README.md",
    "boot/README.md", "rynorlang/README.md", "rynorlang/examples/hello.rl",
    "user/README.md", "tools/README.md", "tests/README.md",
    "tools/build/build.py", "tools/host/repository.py",
    "tests/repository/test_repository.py", "tests/repository/test_commands.py",
    "docs/design/subsystem-template.md", "docs/design/bootstrap-dependencies.md",
    "docs/reports/foundation.md", "docs/reports/stage1.md",
    "boot/sector.asm", "boot/transition.asm", "kernel/arch/x86_64/entry.asm",
    "kernel/arch/x86_64/serial.c", "kernel/arch/x86_64/linker.ld",
    "kernel/core/main.c", "kernel/include/serial.h", "tools/host/image.py",
    "tools/host/qemu.py", "tests/repository/test_image.py", "tests/integration/test_boot.py",
    "kernel/include/cpu.h", "kernel/arch/x86_64/cpu.c", "kernel/arch/x86_64/descriptors.asm",
    "kernel/arch/x86_64/exceptions.asm", "kernel/arch/x86_64/selftest.asm",
    "kernel/interrupts/exceptions.c", "tools/host/exception_output.py",
    "tests/repository/test_exception_output.py", "docs/design/cpu.md", "docs/reports/stage2.md",
    "kernel/include/io.h", "kernel/include/irq.h", "kernel/interrupts/irq.c",
    "kernel/arch/x86_64/pic.c", "kernel/arch/x86_64/timer.c",
    "tools/host/timer_output.py", "tools/host/resources.py",
    "assets/README.md", "assets/branding/icon.png", "docs/reports/stage3.md",
    "docs/design/irq-timer.md", "tests/repository/test_timer_output.py",
    "tests/repository/test_resources.py",
    "kernel/include/boot_memory.h", "kernel/include/pmm.h", "kernel/mm/map.c",
    "kernel/mm/pmm.c", "kernel/mm/selftest.c", "kernel/mm/README.md",
    "tools/host/boot_output.py", "tools/host/pmm_output.py",
    "tests/repository/test_pmm_output.py", "tests/integration/test_pmm.py",
    "docs/design/physical-memory.md", "docs/reports/stage4.md",
    "kernel/include/vm.h", "kernel/include/paging.h", "kernel/mm/vm.c", "kernel/mm/vm-test.c",
    "kernel/arch/x86_64/vm-test.asm", "tools/host/vm_output.py",
    "tests/repository/test_vm_output.py", "tests/integration/test_vm.py",
    "docs/design/virtual-memory.md", "docs/reports/stage5.md",
    "kernel/include/heap.h", "kernel/mm/heap.c", "tools/host/heap_output.py",
    "kernel/mm/heap-test.c",
    "tests/integration/test_audit.py",
    "tests/repository/test_heap_output.py", "tests/integration/test_heap.py",
    "docs/design/heap.md", "docs/reports/stage6.md",
    "kernel/include/ksched.h", "kernel/arch/x86_64/switch.asm", "kernel/core/thread.c",
    "kernel/mm/kstack.c", "tools/host/sched_output.py",
    "kernel/core/scheduler-test.c", "kernel/arch/x86_64/scheduler-test.asm",
    "kernel/core/memory.c",
    "tests/integration/test_scheduler.py",
    "tests/repository/test_sched_output.py",
    "docs/design/scheduler.md", "docs/reports/stage7.md", "docs/reports/stage7-audit.md",
    "docs/reports/stage8.md", "docs/reports/stage8-audit.md", "docs/reports/stage9-audit.md",
    "kernel/include/kbd.h", "kernel/drivers/keyboard.c",
    "kernel/drivers/keyboard-test.c", "kernel/drivers/keyboard-internal.h",
    "tools/host/kbd_output.py", "tests/integration/test_keyboard.py",
    "tests/repository/test_kbd_output.py", "docs/design/keyboard.md",
    "kernel/include/display.h", "kernel/drivers/display.c",
    "kernel/drivers/display-internal.h", "kernel/drivers/display-font.h",
    "kernel/drivers/display-surface.c", "kernel/drivers/display-surface-test.c",
    "kernel/drivers/display-test.c", "tools/host/display_output.py",
    "tests/integration/test_display.py",
    "tests/repository/test_fb_output.py", "docs/design/framebuffer.md",
    "docs/reports/stage9.md",
    "kernel/include/kstring.h", "kernel/include/kbuf.h", "kernel/include/krst.h",
    "kernel/runtime/kstring.c", "kernel/runtime/kbuf.c", "kernel/runtime/krst.c",
    "kernel/runtime/runtime-test.c", "kernel/runtime/README.md",
    "kernel/runtime/region.h", "kernel/runtime/boundary-test.c",
    "tools/host/runtime_output.py", "tests/integration/test_runtime.py",
    "tools/host/kernel_elf.py",
    "tests/repository/test_runtime_output.py", "docs/design/runtime.md",
    "docs/reports/stage10.md", "docs/reports/stage10-audit.md",
    # Stage 11 implementation, verifier, and linked design/report contracts.
    # Windows documents are research-only architecture, never executable code.
    "kernel/include/shell.h", "kernel/shell/shell.c", "kernel/shell/shell-test.c",
    "kernel/shell/shell-internal.h",
    "tools/host/shell_output.py", "docs/design/shell.md", "docs/reports/stage11.md",
    "docs/design/windows-compatibility.md", "docs/windows-compatibility-program.md",
    # Stage 12 is host-only. The implementation intentionally lives under tools/.
    "tools/__init__.py", "tools/rynorlang/lex.py", "tools/rynorlang/__init__.py",
    "tests/repository/test_rynorlang_lexer.py",
    "docs/design/rynorlang-lexer.md", "docs/reports/stage12.md",
    # Stage 13 parser is host-only; implementation lives under tools/rynorlang.
    "tools/rynorlang/parse.py", "tests/repository/test_rynorlang_parser.py",
    "docs/design/rynorlang-parser.md", "docs/reports/stage13.md",
    # Stage 14 semantics is host-only; implementation lives under tools/rynorlang.
    "tools/rynorlang/analyze.py", "tests/repository/test_rynorlang_semantics.py",
    "docs/design/rynorlang-ast.md", "docs/reports/stage14.md",
    # Stage 15a compiler is host-only; RIR, backend, oracle, harness, tests,
    # fixtures, and design docs live under tools/, tests/, and docs/.
    "tools/rynorlang/rir.py", "tools/rynorlang/compile.py",
    "tools/rynorlang/interp.py", "tools/rynorlang/harness_start.asm",
    "tests/repository/test_rynorlang_rir.py",
    "tests/repository/test_rynorlang_compiler.py",
    "docs/design/rynorlang-rir.md", "docs/design/rynorlang-abi.md",
    "docs/reports/stage15a.md",
    # Stage 15b shell surface is host-only; edition-gated lexer/parser/
    # analyzer deltas plus the shell host module, tests, and fixtures.
    "tools/rynorlang/shell.py",
    "tests/repository/test_rynorlang_shell.py",
    "docs/design/rynorlang-shell-language.md", "docs/reports/stage15b.md",
    # Stage 16 host-native programs: program pipeline, host runtime,
    # program fixtures, tests, and design docs.
    "tools/rynorlang/program.py",
    "tools/rynorlang/runtime/rt_linux.asm",
    "tests/repository/test_rynorlang_programs.py",
    "docs/design/rynorlang-program-model.md", "docs/reports/stage16.md",
) + tuple(f"{directory}/.gitkeep" for directory in RESERVED_DIRECTORIES)

# Version 14 is the exact Stage 14 repository contract, not a build-target DSL.
EXPECTED_METADATA = {
    "schema_version": 14,
    "version": "0.1.0",
    "os": "RynorOS",
    "kernel": "Rynorkernel",
    "language": {
        "name": "RynorLang",
        "source_extension": SOURCE_EXTENSION,
        "status": "semantic-subset-frozen",
    },
    "license": "Apache-2.0",
    "stage": 14,
    "status": "rynorlang-semantics",
    "assets": {"official_icon": "assets/branding/icon.png", "status": "packaged-not-rendered",
               "package": "rynoros-resources.zip"},
    "target": {"architecture": "x86_64", "status": "implemented-qemu",
               "boot_method": "original-bios-lba-loader"},
    "bootstrap": {
        "python_minimum": "3.10",
        "python_packages": [],
        "git": "optional-version-control",
        "build_tools": ["clang", "ld.lld", "nasm"],
        "test_tools": ["qemu-system-x86_64", "SeaBIOS"],
    },
    "implemented_components": [
        "repository-validator", "host-build-check", "repository-tests",
        "bios-boot", "x86_64-entry", "serial-output", "qemu-boot-test",
        "kernel-gdt", "exception-idt", "exception-diagnostics", "controlled-cpu-self-test",
        "pic-irq-dispatch", "pit-timer", "timer-irq-self-test", "os-resource-package",
        "bios-e820-map", "physical-frame-allocator", "pmm-self-test",
        "four-level-paging", "vm-map-unmap", "vm-permissions", "vm-page-fault-tests",
        "bounded-kernel-heap", "heap-boundary-alignment", "heap-coalescing",
        "heap-stress-oom", "heap-self-test",
        "per-thread-kernel-stacks", "kstack-guard-pages", "round-robin-scheduler",
        "timer-preemption", "thread-abstraction", "context-switching",
        "scheduler-self-test",
        "ps2-keyboard", "i8042-controller", "irq1-driver", "bounded-keyboard-event-queue",
        "keyboard-sendkey-injection", "keyboard-self-test",
        "bochs-vbe-framebuffer", "pci-bar0-lfb-discovery", "framebuffer-mapping",
        "bounds-safe-rect-text", "framebuffer-pixel-evidence", "framebuffer-self-test",
        "bounded-strings", "string-format-primitives", "bounded-byte-rings",
        "runtime-services", "fnv1a-64-digest", "runtime-service-dispatch",
        "runtime-on-worker-threads", "runtime-self-test",
        "kernel-shell", "shell-tokenizer", "shell-dispatch", "shell-line-buffer",
        "shell-interactive-session", "shell-self-test",
        "rynorlang-lexer", "lexer-tokenization", "lexer-spans", "lexer-diagnostics",
        "rynorlang-parser", "parser-temporary-tree", "parser-spans", "parser-diagnostics",
        "rynorlang-semantics", "semantics-stable-ast", "semantics-name-resolution", "semantics-type-checking",
    ],
    "os_build_targets": ["rynorkernel", "rynoros.img", "rynoros-resources.zip"],
}


def is_rynorlang_source(path: Path) -> bool:
    """Recognize the case-sensitive intended suffix, without parsing contents."""
    return path.suffix == SOURCE_EXTENSION


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate metadata key: {key}")
        result[key] = value
    return result


def _compare_metadata(actual: object, expected: object, field: str) -> list[str]:
    # Exact types matter: JSON booleans must never substitute for integer metadata.
    if type(actual) is not type(expected):
        return [f"{field}: expected {type(expected).__name__}"]
    errors = []
    if isinstance(expected, dict):
        for key in sorted(expected.keys() - actual.keys()):
            errors.append(f"{field}.{key}: missing field")
        for key in sorted(actual.keys() - expected.keys()):
            errors.append(f"{field}.{key}: unknown field")
        for key in expected.keys() & actual.keys():
            errors.extend(_compare_metadata(actual[key], expected[key], f"{field}.{key}"))
    elif actual != expected:
        errors.append(f"{field}: expected {expected!r}, got {actual!r}")
    return errors


def validate_repository(root: Path) -> list[str]:
    """Return all detected foundation errors; an empty list means valid."""
    errors = []
    for directory in REQUIRED_DIRECTORIES:
        if not (root / directory).is_dir():
            errors.append(f"missing directory: {directory}")
    for filename in REQUIRED_FILES:
        path = root / filename
        if not path.is_file():
            errors.append(f"missing file: {filename}")
        elif path.name != ".gitkeep" and path.stat().st_size == 0:
            errors.append(f"empty required file: {filename}")

    metadata_path = root / "project.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(
                metadata_path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_object,
            )
        except (OSError, UnicodeError, ValueError) as error:
            errors.append(f"project.json: cannot read valid metadata: {error}")
        else:
            errors.extend(_compare_metadata(metadata, EXPECTED_METADATA, "project.json"))

    examples = root / "rynorlang/examples"
    try:
        read_icon(root)
    except (OSError, ValueError) as error:
        errors.append(f"official icon: {error}")
    if not any(path.is_file() and is_rynorlang_source(path) for path in examples.glob("*")):
        errors.append("rynorlang/examples: no .rl syntax sample found (not an execution test)")
    return sorted(errors)
