"""Repository tests only; samples are not parsed, compiled, or executed."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from repository import (  # noqa: E402
    REQUIRED_DIRECTORIES, REQUIRED_FILES, is_rynorlang_source, validate_repository,
)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rynoros-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Path(self.temporary.name)
        for directory in REQUIRED_DIRECTORIES:
            (self.fixture / directory).mkdir(parents=True, exist_ok=True)
        for filename in REQUIRED_FILES:
            shutil.copy2(ROOT / filename, self.fixture / filename)

    def write_metadata(self, metadata):
        (self.fixture / "project.json").write_text(json.dumps(metadata), encoding="utf-8")

    def read_metadata(self):
        return json.loads((self.fixture / "project.json").read_text(encoding="utf-8"))

    def test_real_repository(self):
        self.assertEqual(validate_repository(ROOT), [])

    def test_valid_fixture(self):
        self.assertEqual(validate_repository(self.fixture), [])

    def test_every_required_file_is_checked(self):
        for filename in REQUIRED_FILES:
            with self.subTest(filename=filename):
                target = self.fixture / filename
                target.unlink()
                self.assertIn(f"missing file: {filename}", validate_repository(self.fixture))
                shutil.copy2(ROOT / filename, target)

    def test_every_required_directory_is_checked(self):
        # Temporarily rename each fixture directory; never modify the real root.
        for directory in REQUIRED_DIRECTORIES:
            with self.subTest(directory=directory):
                target = self.fixture / directory
                moved = self.fixture / "reserved-for-test"
                target.rename(moved)
                try:
                    self.assertIn(f"missing directory: {directory}", validate_repository(self.fixture))
                finally:
                    moved.rename(target)

    def test_empty_required_document_is_rejected(self):
        (self.fixture / "README.md").write_text("", encoding="utf-8")
        self.assertIn("empty required file: README.md", validate_repository(self.fixture))

    def test_malformed_metadata_is_rejected(self):
        for content in ("{", '{"os":"RynorOS","os":"Other"}', "null", "[]"):
            with self.subTest(content=content):
                (self.fixture / "project.json").write_text(content, encoding="utf-8")
                self.assertTrue(validate_repository(self.fixture))

    def test_invalid_utf8_is_rejected(self):
        (self.fixture / "project.json").write_bytes(b"\xff")
        self.assertTrue(any("cannot read valid metadata" in e for e in validate_repository(self.fixture)))

    def test_metadata_identity_and_status_are_enforced(self):
        baseline = self.read_metadata()
        for field, value in (
            ("os", "OtherOS"), ("kernel", "Linux"), ("license", "unknown"),
            ("stage", True), ("schema_version", True), ("stage", 3),
            ("status", "bootable"), ("os_build_targets", ["kernel"]),
            ("implemented_components", ["compiler"]), ("extra", "unknown"),
        ):
            with self.subTest(field=field, value=value):
                metadata = dict(baseline)
                metadata[field] = value
                self.write_metadata(metadata)
                self.assertTrue(any(f"project.json.{field}:" in e for e in validate_repository(self.fixture)))

    def test_missing_metadata_field_is_rejected(self):
        metadata = self.read_metadata()
        del metadata["language"]["source_extension"]
        self.write_metadata(metadata)
        self.assertIn("project.json.language.source_extension: missing field", validate_repository(self.fixture))

    def test_wrong_extension_metadata_is_rejected(self):
        for extension in ("rl", ".c", ".RL", ""):
            with self.subTest(extension=extension):
                metadata = self.read_metadata()
                metadata["language"]["source_extension"] = extension
                self.write_metadata(metadata)
                self.assertTrue(any("source_extension" in e for e in validate_repository(self.fixture)))

    def test_rl_extension_recognition(self):
        self.assertTrue(is_rynorlang_source(Path("hello.rl")))
        for name in ("hello.c", "hello.RL", "hello.rl.txt", "hello", ".rl"):
            with self.subTest(name=name):
                self.assertFalse(is_rynorlang_source(Path(name)))

    def test_missing_rl_sample_is_rejected(self):
        sample = self.fixture / "rynorlang/examples/hello.rl"
        sample.rename(sample.with_suffix(".txt"))
        self.assertTrue(any("no .rl syntax sample" in e for e in validate_repository(self.fixture)))


if __name__ == "__main__":
    unittest.main()
