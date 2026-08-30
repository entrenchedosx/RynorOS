"""Real canonical asset/package validation, without a PNG renderer dependency."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from resources import ICON_PATH, ICON_SHA256, package_resources, read_icon


class ResourceTests(unittest.TestCase):
    def test_original_png_is_preserved(self):
        data, info = read_icon(ROOT)
        self.assertEqual(hashlib.sha256(data).hexdigest(), ICON_SHA256)
        self.assertEqual((info["width"], info["height"]), (1254, 1254))
        self.assertFalse((ROOT / "icon.png").exists())

    def test_package_contents_and_byte_reproducibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary) / "a.zip", Path(temporary) / "b.zip"
            package_resources(ROOT, first)
            package_resources(ROOT, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), [ICON_PATH, "manifest.json"])
                self.assertEqual(archive.read(ICON_PATH), (ROOT / ICON_PATH).read_bytes())
                self.assertEqual(json.loads(archive.read("manifest.json")),
                                 {"os": "RynorOS", "assets": [read_icon(ROOT)[1]]})
                self.assertIsNone(archive.testzip())
                for entry in archive.infolist():
                    self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(entry.external_attr >> 16, 0o100644)

    def test_missing_and_corrupt_assets_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(FileNotFoundError):
                read_icon(root)
            target = root / ICON_PATH
            target.parent.mkdir(parents=True)
            for content in (b"", b"not a PNG", (ROOT / ICON_PATH).read_bytes()[:-1]):
                target.write_bytes(content)
                with self.assertRaises(ValueError):
                    read_icon(root)
