"""Stage 5 repository/asset contract; this does not check kernel execution."""

import json
from pathlib import Path
from resources import read_icon


SOURCE_EXTENSION = ".rl"
REQUIRED_DIRECTORIES = (
    "kernel", "kernel/arch", "kernel/core", "kernel/mm", "kernel/interrupts",
    "kernel/drivers", "kernel/include", "boot", "rynorlang", "rynorlang/lexer",
    "rynorlang/parser", "rynorlang/ast", "rynorlang/compiler", "rynorlang/runtime",
    "rynorlang/tests", "rynorlang/examples", "user", "user/shell", "user/lib",
    "user/apps", "tools", "tools/build", "tools/host", "tests", "tests/repository",
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
) + tuple(f"{directory}/.gitkeep" for directory in RESERVED_DIRECTORIES)

# Version 6 is the exact Stage 5 contract, not a build-target DSL.
EXPECTED_METADATA = {
    "schema_version": 6,
    "version": "0.1.0",
    "os": "RynorOS",
    "kernel": "Rynorkernel",
    "language": {
        "name": "RynorLang",
        "source_extension": SOURCE_EXTENSION,
        "status": "experimental-design",
    },
    "license": "MIT",
    "stage": 5,
    "status": "virtual-memory-management",
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
