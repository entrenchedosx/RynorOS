"""Exercise the real CLI and isolated failure cases; no fake OS targets."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES  # noqa: E402


class CommandTests(unittest.TestCase):
    def run_command(self, root, command):
        return subprocess.run(
            [sys.executable, "-B", str(root / "tools/build/build.py"), command],
            cwd=root.parent, capture_output=True, text=True, timeout=30,
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
        result = self.run_command(ROOT, "build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Python sources compiled", result.stdout)
        self.assertIn("no kernel, compiler, or boot image was built", result.stdout)

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
        for path in (root / "tests/repository").glob("test_*.py"):
            path.unlink()
        (root / "tests/repository/test_failure.py").write_text(
            "import unittest\nclass Failure(unittest.TestCase):\n"
            "    def test_failure(self):\n        self.fail('deliberate fixture failure')\n",
            encoding="utf-8",
        )
        result = self.run_command(root, "test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deliberate fixture failure", result.stderr)


if __name__ == "__main__":
    unittest.main()
