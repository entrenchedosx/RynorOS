"""Stage 17a integration: real IDE I/O in QEMU against disposable images.

The guest discovers the PIIX3 controller, selects the RLBLK1 test device,
proves reads with host-recomputed digests, and proves writes by readback.
Mutation variants prove the evidence is causal. The boot disk is never
written: writes are refused off the test device, and all test drives ride
snapshot overlays.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from blk_image import create, create_zeroed
from blk_output import parse_serial, validate


def _mutate_copy(pairs, source="kernel/storage/blk.c"):
    tmp = tempfile.TemporaryDirectory(prefix="blk-fault-", dir=ROOT / "build")
    root = Path(tmp.name)
    from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
    for d in REQUIRED_DIRECTORIES:
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_FILES:
        shutil.copyfile(ROOT / f, root / f)
    path = root / source
    contents = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents, encoding="utf-8")
    return tmp, root


class StorageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = ROOT / "build/storage-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.small = cls.work / "test-1mib.img"
        cls.large = cls.work / "test-8mib.img"
        cls.zeroed = cls.work / "zeroed-1mib.img"
        create(cls.small, 1)
        create(cls.large, 8)
        create_zeroed(cls.zeroed, 1)
        import hashlib
        cls.hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (cls.small, cls.large, cls.zeroed)}
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination)

    def _boot_with(self, method, image, timeout=60):
        logs = self.work / method
        output = boot_image(self.destination / "rynoros.img", logs, timeout=timeout,
                            extra_drives=(image,) if image is not None else ())
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["reaped"])
        return output

    def test_storage_1mib_full_evidence(self):
        output = self._boot_with("evidence-1mib", self.small)
        self.assertEqual(validate(parse_serial(output), self.small), [])

    def test_storage_8mib_full_evidence(self):
        output = self._boot_with("evidence-8mib", self.large, timeout=60)
        self.assertEqual(validate(parse_serial(output), self.large), [])

    def test_zeroed_image_not_selected(self):
        output = self._boot_with("zeroed", self.zeroed)
        evidence = parse_serial(output)
        self.assertEqual(evidence.failures, [])
        self.assertNotIn(b"[BLK] devices=", output)
        self.assertIn(b"[TEST] shell monitor verified\r\n", output)

    def test_corrupt_magic_not_selected(self):
        bad = self.work / "corrupt.img"
        shutil.copyfile(self.small, bad)
        with open(bad, "r+b") as handle:
            handle.seek(3)
            handle.write(b"\xFF")
        output = self._boot_with("corrupt", bad)
        evidence = parse_serial(output)
        self.assertEqual(evidence.failures, [])
        self.assertNotIn(b"[BLK] devices=", output)
        self.assertIn(b"[TEST] shell monitor verified\r\n", output)

    def test_missing_drive_boots_cleanly(self):
        output = self._boot_with("nodrive", None)
        evidence = parse_serial(output)
        self.assertEqual(evidence.failures, [])
        self.assertNotIn(b"[BLK] devices=", output)

    def test_snapshot_leaves_host_images_pristine(self):
        import hashlib
        for path, digest in sorted(self.hashes.items(), key=lambda kv: kv[0].name):
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def _run_storage_failure(self, expected_reason, pairs):
        tmp, root = _mutate_copy(pairs)
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / self._testMethodName
        try:
            with self.assertRaises(RuntimeError) as err:
                boot_image(root / "build" / "img" / "rynoros.img", logs, timeout=60,
                           extra_drives=(self.small,))
        finally:
            summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["reaped"])
        msg = str(err.exception) + (logs / "serial.log").read_bytes().decode("ascii", errors="replace")
        self.assertIn(expected_reason, msg)
        self.assertIn(expected_reason, summary.get("failure", ""))

    def test_range_check_inversion_fails(self):
        # Past-end reads must be rejected pre-hardware; inverted, the
        # out-of-range sector is issued and the bounds require() fires.
        self._run_storage_failure("past-end", [
            ("if (count > dev->block_count - start) return BLK_RANGE;",
             "if (count > dev->block_count - start) return BLK_OK;"),
        ])

    def test_completion_error_inversion_fails(self):
        # Treating DRQ as error breaks every transfer starting at discovery.
        self._run_storage_failure("discovery", [
            ("        if (s & IDE_SR_ERR) return -1;",
             "        if (s & IDE_SR_DRQ) return -1;"),
        ])

    def test_offset_plus_one_detected(self):
        tmp, root = _mutate_copy([
            ("    io_out8(slot->cmd + IDE_REG_LBA0, (cpu_u8)lba);",
             "    io_out8(slot->cmd + IDE_REG_LBA0, (cpu_u8)(lba + 1));"),
        ])
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / "mut-offbyone"
        output = boot_image(root / "build" / "img" / "rynoros.img", logs, timeout=60,
                            extra_drives=(self.small,))
        self.assertTrue(validate(parse_serial(output), self.small))

    def test_fake_capacity_detected(self):
        tmp, root = _mutate_copy([
            ("        devices[i].block_count = count;",
             "        devices[i].block_count = count + 1000000;"),
        ])
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / "mut-capacity"
        # The inflated last-block read fails in-guest; the serial that
        # survives still carries the lying capacity line for the host.
        with self.assertRaises(RuntimeError):
            boot_image(root / "build" / "img" / "rynoros.img", logs, timeout=60,
                       extra_drives=(self.small,))
        serial = (logs / "serial.log").read_bytes()
        evidence = parse_serial(serial)
        self.assertNotEqual(evidence.blocks, 2048)
        self.assertTrue(validate(evidence, self.small))


if __name__ == "__main__":
    unittest.main()
