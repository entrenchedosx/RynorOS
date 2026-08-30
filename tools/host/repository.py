"""Stage 0 repository contract; no kernel or language implementation."""

import json
from pathlib import Path


SOURCE_EXTENSION = ".rl"
REQUIRED_DIRECTORIES = (
    "kernel", "kernel/arch", "kernel/core", "kernel/mm", "kernel/interrupts",
    "kernel/drivers", "kernel/include", "boot", "rynorlang", "rynorlang/lexer",
    "rynorlang/parser", "rynorlang/ast", "rynorlang/compiler", "rynorlang/runtime",
    "rynorlang/tests", "rynorlang/examples", "user", "user/shell", "user/lib",
    "user/apps", "tools", "tools/build", "tools/host", "tests", "tests/repository",
    "tests/kernel", "tests/rynorlang", "tests/integration", "docs", "docs/design",
    "docs/reports", "build",
)
RESERVED_DIRECTORIES = (
    "kernel/arch", "kernel/core", "kernel/mm", "kernel/interrupts", "kernel/drivers",
    "kernel/include", "rynorlang/lexer", "rynorlang/parser", "rynorlang/ast",
    "rynorlang/compiler", "rynorlang/runtime", "rynorlang/tests", "user/shell",
    "user/lib", "user/apps", "tests/kernel", "tests/rynorlang", "tests/integration",
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
    "docs/reports/foundation.md",
) + tuple(f"{directory}/.gitkeep" for directory in RESERVED_DIRECTORIES)

# Version 1 is deliberately the exact Stage 0 contract, not a build-target DSL.
EXPECTED_METADATA = {
    "schema_version": 1,
    "os": "RynorOS",
    "kernel": "Rynorkernel",
    "language": {
        "name": "RynorLang",
        "source_extension": SOURCE_EXTENSION,
        "status": "experimental-design",
    },
    "license": "MIT",
    "stage": 0,
    "status": "foundation-only",
    "target": {"architecture": "x86_64", "status": "planned"},
    "bootstrap": {
        "python_minimum": "3.10",
        "python_packages": [],
        "git": "optional-version-control",
    },
    "implemented_components": [
        "repository-validator", "host-build-check", "repository-tests",
    ],
    "os_build_targets": [],
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
    # Exact types matter: JSON false must not pass as integer stage 0.
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
    if not any(path.is_file() and is_rynorlang_source(path) for path in examples.glob("*")):
        errors.append("rynorlang/examples: no .rl syntax sample found (not an execution test)")
    return sorted(errors)
