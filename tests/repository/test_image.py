"""Pure image-layout tests, not substitutes for emulated execution."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from image import IMAGE_SIZE, MAX_PAYLOAD, make_image, build_image
from qemu import boot_image


class ImageTests(unittest.TestCase):
    def invalid_boot_arguments(self):
        """A per-test throwaway directory; failed-validation runs must not
        drop empty evidence logs into the repository working tree."""
        directory = tempfile.TemporaryDirectory(prefix="unused-logs-")
        self.addCleanup(directory.cleanup)
        return Path("unused.img"), Path(directory.name)

    def test_invalid_exception_variant_is_rejected_before_build(self):
        for vector in (True, -1, 2, 32, "3"):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                build_image(Path("unused-root"), test_vector=vector)

    def test_layout_and_zero_padding(self):
        sector = bytes(510) + b"\x55\xaa"
        result = make_image(sector, b"payload fixture")
        self.assertEqual(len(result), IMAGE_SIZE)
        self.assertEqual(result[:512], sector)
        self.assertEqual(result[512:527], b"payload fixture")
        self.assertEqual(result[527:], bytes(IMAGE_SIZE - 527))

    def test_invalid_signature_or_size_is_rejected(self):
        for sector in (bytes(512), b"\x55\xaa", bytes(1024)):
            with self.subTest(size=len(sector)), self.assertRaises(ValueError):
                make_image(sector, b"payload")

    def test_payload_bounds(self):
        sector = bytes(510) + b"\x55\xaa"
        for payload in (b"", bytes(MAX_PAYLOAD + 1)):
            with self.assertRaises(ValueError):
                make_image(sector, payload)
        self.assertEqual(len(make_image(sector, bytes(MAX_PAYLOAD))), IMAGE_SIZE)

    def test_invalid_boot_timeouts_are_rejected_before_launch(self):
        for timeout in (0, -1, 61, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                boot_image(*self.invalid_boot_arguments(), timeout)
