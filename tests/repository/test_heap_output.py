"""Synthetic parser fixtures: not evidence of CPU execution."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from heap_output import parse_heap_output, validate_heap_output


def fixture(stress=227, oom=1):
    lines = ["[HEAP] initialize arena=65536 mapped=65536", "[HEAP] free_blocks=1",
             "[TEST] HEAP initialization rollback verified",
             "[TEST] HEAP adversarial boundaries and corruption verified",
             "[HEAP] small=0xffffc00000000010 mid=0xffffc00000000050 align4096=0xffffc00000001000",
             "[TEST] HEAP boundary and alignment verified",
             "[HEAP] coalesced free_blocks=1 used=0", "[TEST] HEAP coalescing verified",
             "[TEST] HEAP invalid calls rejected",
             f"[HEAP] stress blocks={stress} oom={oom}", "[TEST] HEAP stress and OOM verified",
             "[HEAP] PMM allocated_bytes=106496 free_bytes=937984 table_pages=10",
             "[HEAP] final used=0 mapped=65536", "[TEST] HEAP self-test passed"]
    return ("\r\n".join(lines) + "\r\n").encode()


class HeapOutputTests(unittest.TestCase):
    def test_valid_parser_fixture(self):
        self.assertEqual(validate_heap_output(fixture()), [])
        parsed = parse_heap_output(fixture())
        self.assertEqual(parsed["stress"], 227)
        self.assertEqual(parsed["small"], 0xFFFFC00000000010)

    def test_every_line_required(self):
        for line in fixture().splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_heap_output(fixture().replace(line, b"", 1)))

    def test_marker_order_required(self):
        for swaps in ((b"coalescing verified", b"invalid calls rejected"),
                      (b"self-test passed", b"final used=0")):
            swapped = fixture().replace(swaps[0], b"SWAP_SENTINEL").replace(swaps[1], swaps[0]).replace(b"SWAP_SENTINEL", swaps[1])
            with self.subTest(swaps=swaps):
                self.assertTrue(validate_heap_output(swapped))

    def test_alignment_and_accounting_required(self):
        for old, new in ((b"small=0xffffc00000000010", b"small=0xffffc00000000011"),
                         (b"mid=0xffffc00000000050", b"mid=0xffffc00000000054"),
                         (b"align4096=0xffffc00000001000", b"align4096=0xffffc00000001001"),
                         (b"coalesced free_blocks=1", b"coalesced free_blocks=2"),
                         (b"coalesced free_blocks=1 used=0", b"coalesced free_blocks=1 used=4096"),
                         (b"free_blocks=1", b"free_blocks=0"),
                         (b"oom=1", b"oom=0"),
                         (b"stress blocks=227", b"stress blocks=0"),
                         (b"mapped=65536", b"mapped=65535")):
            with self.subTest(old=old):
                self.assertTrue(validate_heap_output(fixture().replace(old, new)))

    def test_bounded_unbounded_duplicate_and_bad_encoding(self):
        for output in (fixture() * 10, fixture() + b"extra\r\n", fixture() + b"\xff",
                       fixture().replace(b"\r\n", b"\n")):
            with self.subTest(output=type(output)):
                self.assertTrue(validate_heap_output(output))
        self.assertTrue(validate_heap_output(fixture(stress=65536 // 256)))
        self.assertTrue(validate_heap_output(fixture(stress=1)))

    def test_cross_subsystem_accounting(self):
        vm = {"allocated": 28672, "free": 1015808}
        self.assertEqual(validate_heap_output(fixture(), vm), [])
        self.assertTrue(validate_heap_output(fixture(), {**vm, "free": vm["free"] + 4096}))
        for old, new in ((b"table_pages=10", b"table_pages=7"),
                         (b"allocated_bytes=106496", b"allocated_bytes=28672")):
            self.assertTrue(validate_heap_output(fixture().replace(old, new), vm))
