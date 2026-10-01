"""The repository test package must resolve from this checkout."""

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PackageResolutionTests(unittest.TestCase):
    def test_tests_package_and_helpers_resolve_from_checkout(self):
        expected_tests = (ROOT / "tests").resolve()
        tests_spec = importlib.util.find_spec("tests")
        self.assertIsNotNone(tests_spec)
        self.assertEqual(Path(tests_spec.origin).resolve(), expected_tests / "__init__.py")

        helpers_spec = importlib.util.find_spec("tests.repository")
        self.assertIsNotNone(helpers_spec)
        helper_locations = {
            Path(location).resolve()
            for location in (helpers_spec.submodule_search_locations or ())
        }
        self.assertEqual(helper_locations, {(expected_tests / "repository").resolve()})

        helper_spec = importlib.util.find_spec(
            "tests.repository.test_rynorlang_selfhost_emit"
        )
        self.assertIsNotNone(helper_spec)
        self.assertEqual(
            Path(helper_spec.origin).resolve(),
            expected_tests / "repository" / "test_rynorlang_selfhost_emit.py",
        )

        backend_spec = importlib.util.find_spec(
            "tests.integration.delayed_nbd"
        )
        self.assertIsNotNone(backend_spec)
        self.assertEqual(
            Path(backend_spec.origin).resolve(),
            expected_tests / "integration" / "delayed_nbd.py",
        )

        integration_dir = str((expected_tests / "integration").resolve())
        if integration_dir not in sys.path:
            sys.path.insert(0, integration_dir)
        top_level_backend_spec = importlib.util.find_spec("delayed_nbd")
        self.assertIsNotNone(top_level_backend_spec)
        self.assertEqual(
            Path(top_level_backend_spec.origin).resolve(),
            expected_tests / "integration" / "delayed_nbd.py",
        )


if __name__ == "__main__":
    unittest.main()
