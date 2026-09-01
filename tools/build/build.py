#!/usr/bin/env python3
"""Repository checks, original kernel image build, and bounded QEMU CPU tests."""

import argparse
from pathlib import Path
import py_compile
import sys
import tempfile
import unittest
import subprocess


sys.dont_write_bytecode = True
if sys.version_info < (3, 10):
    print("ERROR: Python 3.10 or newer is required.", file=sys.stderr)
    raise SystemExit(1)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from repository import validate_repository  # noqa: E402
from image import build_image  # noqa: E402
from qemu import boot_image  # noqa: E402


def validate() -> bool:
    errors = validate_repository(ROOT)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print(f"Validation failed: {len(errors)} error(s).", file=sys.stderr)
        return False
    print("Repository validation passed (structure, icon, and project metadata).", flush=True)
    return True


def build() -> bool:
    if not validate():
        return False
    sources = sorted(
        path for directory in (ROOT / "tools", ROOT / "tests")
        for path in directory.rglob("*.py")
    )
    with tempfile.TemporaryDirectory(prefix="rynoros-build-") as temporary:
        for index, source in enumerate(sources):
            try:
                py_compile.compile(
                    str(source), cfile=str(Path(temporary) / f"{index}.pyc"),
                    dfile=source.relative_to(ROOT).as_posix(), doraise=True,
                )
            except py_compile.PyCompileError as error:
                print(f"Host compilation failed: {error}", file=sys.stderr)
                return False
    print(f"Host build check passed: {len(sources)} Python sources compiled; temporary bytecode removed.", flush=True)
    build_image(ROOT)
    return True


def test() -> bool:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests/repository"), pattern="test_*.py",
    )
    if suite.countTestCases() == 0:
        print("ERROR: No repository tests discovered.", file=sys.stderr)
        return False
    if loader_errors():
        return False
    return unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


def loader_errors() -> bool:
    """Discovery silently drops modules that raise at import time; a green
    suite must never be produced by a test file that failed to load."""
    import importlib.util
    import sys as _sys
    failed = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        directory = str(path.parent)
        if directory not in _sys.path:
            _sys.path.insert(0, directory)
        name = path.stem
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as error:  # noqa: BLE001 - report every import failure
            failed.append(f"{path.relative_to(ROOT)}: {type(error).__name__}: {error}")
    if failed:
        print("ERROR: Test modules failed to import:", file=sys.stderr)
        for message in failed:
            print(f"  {message}", file=sys.stderr)
        return True
    return False


def boot_test(timeout: float) -> bool:
    if not build():
        return False
    boot_image(ROOT / "build/rynoros.img", ROOT / "build/boot-test", timeout)
    return True


def integration_test() -> bool:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests/integration"), pattern="test_*.py",
    )
    if suite.countTestCases() == 0:
        print("ERROR: No integration tests discovered.", file=sys.stderr)
        return False
    if loader_errors():
        return False
    return unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "build", "test", "boot-test", "integration-test", "check"))
    parser.add_argument("--timeout", type=float, default=10, help="boot-test timeout, seconds (0, 60]")
    args = parser.parse_args()
    try:
        if args.command == "check":
            success = build() and test() and integration_test()
        elif args.command == "boot-test":
            success = boot_test(args.timeout)
        else:
            success = {"validate": validate, "build": build, "test": test,
                       "integration-test": integration_test}[args.command]()
    except (OSError, ValueError, ImportError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
