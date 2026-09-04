"""Exercise the real CLI and isolated failure cases; no fake OS targets."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import os
import importlib.util


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES  # noqa: E402


class CommandTests(unittest.TestCase):
    def run_command(self, root, command, environment=None):
        return subprocess.run(
            [sys.executable, "-B", str(root / "tools/build/build.py"), command],
            cwd=root.parent, capture_output=True, text=True, timeout=60,
            env=environment,
        )

    def make_fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix="rynoros-cli-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for directory in REQUIRED_DIRECTORIES:
            (root / directory).mkdir(parents=True, exist_ok=True)
        for filename in REQUIRED_FILES:
            shutil.copy2(ROOT / filename, root / filename)
        return root

    def test_validate_from_another_working_directory(self):
        result = self.run_command(ROOT, "validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Repository validation passed", result.stdout)

    def test_build_reports_actual_scope(self):
        root = self.make_fixture()
        result = self.run_command(root, "build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Python sources compiled", result.stdout)
        self.assertIn("rynoros.img", result.stdout)
        self.assertTrue((root / "build/rynorkernel.elf").is_file())
        self.assertEqual((root / "build/rynoros.img").stat().st_size, 1024 * 1024)

    def test_missing_compiler_fails_without_stale_image(self):
        root = self.make_fixture()
        image = root / "build/rynoros.img"
        image.write_bytes(b"stale test fixture")
        environment = dict(os.environ, RYNOR_CLANG=str(root / "missing-clang"))
        result = self.run_command(root, "build", environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Required host tool", result.stderr)
        self.assertFalse(image.exists())

    def test_c_compilation_failure_stops_build(self):
        root = self.make_fixture()
        (root / "kernel/core/main.c").write_text("#error deliberate_compile_failure\n", encoding="utf-8")
        result = self.run_command(root, "build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deliberate_compile_failure", result.stderr)
        self.assertFalse((root / "build/rynoros.img").exists())

    def test_unresolved_symbol_stops_link(self):
        root = self.make_fixture()
        (root / "kernel/core/main.c").write_text(
            "extern void missing_symbol(void);\nvoid kernel_main(void) { missing_symbol(); }\n",
            encoding="utf-8",
        )
        result = self.run_command(root, "build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("undefined symbol: missing_symbol", result.stderr)
        self.assertFalse((root / "build/rynoros.img").exists())

    def test_invalid_command_fails(self):
        result = self.run_command(ROOT, "boot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)

    def test_missing_file_fails_validation_and_check(self):
        root = self.make_fixture()
        (root / "LICENSE").unlink()
        for command in ("validate", "check"):
            with self.subTest(command=command):
                result = self.run_command(root, command)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing file: LICENSE", result.stderr)

    def test_invalid_python_fails_build(self):
        root = self.make_fixture()
        (root / "tools/host/broken.py").write_text("def broken(:\n", encoding="utf-8")
        result = self.run_command(root, "build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Host compilation failed", result.stderr)

    def test_empty_test_suite_fails(self):
        root = self.make_fixture()
        for path in (root / "tests/repository").glob("test_*.py"):
            path.unlink()
        result = self.run_command(root, "test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No repository tests discovered", result.stderr)

    def test_failing_test_propagates_exit_status(self):
        root = self.make_fixture()
        spec = importlib.util.spec_from_file_location("inventory_build", ROOT / "tools/build/build.py")
        build_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_module)
        for path in (root / "tests/repository").glob("test_*.py"):
            path.unlink()
        template = '''import unittest
class Inventory(unittest.TestCase):
    pass
def make_test(index):
    def test(self):
        if FAIL and index == 0:
            self.fail("deliberate fixture failure")
    return test
for i in range(COUNT):
    setattr(Inventory, f"test_{i}", make_test(i))
'''
        for module, count in build_module.REPOSITORY_TEST_INVENTORY.items():
            source = f"COUNT={count}\nFAIL={module == 'test_repository'}\n" + template
            (root / f"tests/repository/{module}.py").write_text(source, encoding="utf-8")
        result = self.run_command(root, "test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deliberate fixture failure", result.stderr)

    def test_missing_subsystem_test_module_participation_fails(self):
        root = self.make_fixture()
        (root / "tests/integration/test_vm.py").write_text(
            '"""Deliberately emptied required subsystem suite."""\n', encoding="utf-8",
        )
        result = self.run_command(root, "integration-test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("integration test inventory mismatch", result.stderr)
        self.assertIn("test_vm: expected 8, observed 0", result.stderr)


if __name__ == "__main__":
    unittest.main()
