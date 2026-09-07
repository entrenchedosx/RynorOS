"""R1/R2 static pins: no silent rollback discards; confined blk authority.

Host-side grep/static guards (no QEMU needed):

- R1: every rollback return in kernel/storage/fs.c and the
  kernel/core/user.c load path is checked; a failure halts with a
  rollback_<step> marker ([FS] failure=rollback_* via rollback_fail,
  [USER] failure=rollback_* via panic). Any new `(void)<callee>`
  discard, any bare-statement rollback call, or any removed checked
  form fails this module.
- R2: blk_set_writable/blk_clear_writable call sites are confined to
  blk.c (definition), fs.c (mount/unmount), and blk-test.c's single
  test_writability entry. Any re-exposed setter fails this module.

Serial writes, unused-variable suppressions, (void)arg, and pure
helpers (record_state, user_to_kernel) are not rollback discards and
are excluded by callee-name matching.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FS = ROOT / "kernel/storage/fs.c"
USER = ROOT / "kernel/core/user.c"
KERNEL = ROOT / "kernel"

# Rollback-relevant callees whose return must never be silently
# discarded in the two audited files.
CALLEES = (
    "pmm_release", "pmm_allocate", "vm_destroy", "vm_create",
    "vm_clone_low", "vm_release_low", "vm_map", "vm_unmap",
    "teardown_space", "release_frames", "blk_clear_writable",
    "blk_set_writable", "thread_detach_user", "thread_attach_user",
    "copy_page", "sync_high", "clone_high",
)

CHECK_TOKENS = ("if", "return", "require", "!=", "==", "&&", "||")


def _void_discard_lines(text):
    """Lines of the form `(void)callee(` for a rollback callee."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for callee in CALLEES:
            if re.search(r"\(void\)" + callee + r"\s*\(", line):
                hits.append((number, line.strip()))
    return hits


def _bare_call_lines(text):
    """Statement-position rollback calls with no check token on the line.

    Continuation lines of multi-line `if (... || ...)` conditions carry
    `!=`/`||` and pass; definition lines (`static int callee(...`) never
    match because the callee is not first after whitespace.
    """
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"\s*(" + "|".join(CALLEES) + r")\s*\(", line)
        if match and not any(token in line for token in CHECK_TOKENS):
            hits.append((number, line.strip()))
    return hits


class RollbackDiscardTests(unittest.TestCase):
    def test_no_void_rollback_discards(self):
        for path in (FS, USER):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                _void_discard_lines(text), [],
                f"silent (void) rollback discard in {path.relative_to(ROOT)}",
            )
            self.assertEqual(
                _bare_call_lines(text), [],
                f"unchecked bare rollback call in {path.relative_to(ROOT)}",
            )

    def test_rollback_checked_forms_present(self):
        fs = FS.read_text(encoding="utf-8")
        self.assertIn("rollback_fail(\"revoke\")", fs)
        self.assertIn("rollback_fail(\"unmount\")", fs)
        self.assertIn("static void rollback_fail", fs)
        self.assertGreaterEqual(fs.count("if (blk_clear_writable(fs_dev) != BLK_OK)"), 2)
        user = USER.read_text(encoding="utf-8")
        self.assertGreaterEqual(user.count('panic("rollback_release")'), 6)
        self.assertGreaterEqual(user.count('panic("rollback_destroy")'), 2)
        self.assertGreaterEqual(user.count('panic("rollback_teardown")'), 1)
        self.assertGreaterEqual(user.count('panic("rollback_detach")'), 1)
        self.assertIn("static int release_frames", user)

    def _helper_span(self, text):
        start = text.find("static int test_writability(cpu_u32 id, int grant)")
        self.assertNotEqual(start, -1, "test_writability entry missing")
        end = text.find("\n}", start)
        self.assertNotEqual(end, -1, "test_writability end missing")
        return start, end

    def _call_sites(self, name):
        sites = {}
        for path in sorted((KERNEL).rglob("*.c")):
            text = path.read_text(encoding="utf-8")
            lines = [n for n, line in enumerate(text.splitlines(), 1)
                     if re.search(r"\b" + name + r"\s*\(", line)]
            if lines:
                sites[path.relative_to(ROOT).as_posix()] = (text, lines)
        return sites

    def test_blk_set_writable_confined(self):
        sites = self._call_sites("blk_set_writable")
        self.assertEqual(
            set(sites), {
                "kernel/storage/blk.c",
                "kernel/storage/fs.c",
                "kernel/storage/blk-test.c",
            }, f"blk_set_writable escapes confinement: {sorted(sites)}",
        )
        text, lines = sites["kernel/storage/blk.c"]
        self.assertEqual(len(lines), 1)
        self.assertRegex(text.splitlines()[lines[0] - 1],
                         r"^int blk_set_writable\(cpu_u32 id\)")
        text, lines = sites["kernel/storage/fs.c"]
        self.assertEqual(len(lines), 1)
        self.assertIn("if (blk_set_writable(dev))", text.splitlines()[lines[0] - 1])
        text, lines = sites["kernel/storage/blk-test.c"]
        self.assertEqual(len(lines), 1)
        start, end = self._helper_span(text)
        offset = sum(len(line) + 1 for line in text.splitlines()[:lines[0] - 1])
        self.assertTrue(start < offset < end, "setter call outside test_writability")
        span = text[start:end]
        self.assertIn("!dev->test_device", span)
        self.assertIn("test_writable_id != -1", span)

    def test_blk_clear_revocation_points(self):
        sites = self._call_sites("blk_clear_writable")
        self.assertEqual(
            set(sites), {
                "kernel/storage/blk.c",
                "kernel/storage/fs.c",
                "kernel/storage/blk-test.c",
            }, f"blk_clear_writable escapes revocation points: {sorted(sites)}",
        )
        text, lines = sites["kernel/storage/blk.c"]
        self.assertEqual(len(lines), 1)
        self.assertRegex(text.splitlines()[lines[0] - 1],
                         r"^int blk_clear_writable\(cpu_u32 id\)")
        text, lines = sites["kernel/storage/fs.c"]
        self.assertEqual(len(lines), 2)
        for number in lines:
            self.assertIn("if (blk_clear_writable(fs_dev) != BLK_OK)",
                          text.splitlines()[number - 1])
        text, lines = sites["kernel/storage/blk-test.c"]
        self.assertEqual(len(lines), 1)
        start, end = self._helper_span(text)
        offset = sum(len(line) + 1 for line in text.splitlines()[:lines[0] - 1])
        self.assertTrue(start < offset < end, "clear call outside test_writability")


if __name__ == "__main__":
    unittest.main()
