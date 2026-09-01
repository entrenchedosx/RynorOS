"""Synthetic parser fixtures for Stage 10, never emulator data."""
from pathlib import Path
import sys
import unittest
import struct
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from runtime_output import (RUNTIME_START, RUNTIME_END, RUNTIME_GOOD,
                            W_INPUT, WORKERS, ROUNDS, DISPLAY_ACCOUNTING,
                            fnv1a, worker_acc, total_fold, validate_runtime_output,
                            parse_runtime_output, verify_runtime_memory, verify_runtime_trace)


def good_worker_token():
    return ("worker=0 acc=0x%X rounds=%d" % (worker_acc(W_INPUT[0]), ROUNDS)).encode()


class RuntimeOutputTests(unittest.TestCase):
    def evidence(self):
        records = []
        for i, inp in enumerate(W_INPUT):
            stack = 0xffffe00000000000 + i * 5 * 4096
            records.append([worker_acc(inp), 40, i + 30, stack, 2, 0x8100,
                            stack + 0x2000,
                            fnv1a(bytes((j + i) & 255 for j in range(4096))), 300])
        return records

    def pack(self, rows):
        return b''.join(struct.pack('<9Q', *r) for r in rows)

    def test_physical_evidence_all_fields_required(self):
        verify_runtime_memory(self.pack(self.evidence()), 0x8000, 0x8200)
        for slot in range(WORKERS):
            for field in range(9):
                rows = self.evidence()
                rows[slot][field] = 0
                with self.subTest(slot=slot, field=field), self.assertRaises(ValueError):
                    verify_runtime_memory(self.pack(rows), 0x8000, 0x8200)

    def test_evidence_identity_stack_and_extents(self):
        for field in (2, 3):
            rows = self.evidence()
            rows[1][field] = rows[0][field]
            with self.assertRaises(ValueError):
                verify_runtime_memory(self.pack(rows), 0x8000, 0x8200)
        for data in (b'', bytes(7*9*8), self.pack(self.evidence())[:-1], self.pack(self.evidence()) + b'\0'):
            with self.assertRaises(ValueError):
                verify_runtime_memory(data, 0x8000, 0x8200)

    def test_bad_elf_rejected(self):
        from kernel_elf import read_symbols
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.elf'
            for data in (b'', b'\x7fELF', bytes(64)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    read_symbols(path, ('runtime_evidence',))

    def test_cpu_trace_corroborates_each_worker(self):
        data = self.pack(self.evidence())
        lines = []
        for i, row in enumerate(self.evidence()):
            for j in range(2):
                lines.append(f'{i*2+j}: v=20 e=0000 i=0 cpl=0 IP=0008:{row[5]:016x} '
                             f'pc={row[5]:016x} SP=0010:{row[6]:016x}\n')
        trace = ''.join(lines)
        verify_runtime_trace(data, trace, 0x8000, 0x8200)
        for bad in ('', trace.replace('i=0', 'i=1'), trace.replace('v=20', 'v=21'),
                    trace.replace('cpl=0', 'cpl=3'), trace + lines[0], ''.join(lines[1:])):
            with self.assertRaises(ValueError):
                verify_runtime_trace(data, bad, 0x8000, 0x8200)

    def test_valid_fixture(self):
        self.assertEqual(validate_runtime_output(RUNTIME_GOOD), [])
        parsed = parse_runtime_output(RUNTIME_GOOD)
        self.assertEqual(parsed["total"], total_fold())
        self.assertEqual(parsed["allocated"], DISPLAY_ACCOUNTING["allocated"])
        self.assertEqual(parsed["free"], DISPLAY_ACCOUNTING["free"])

    def test_reference_folds(self):
        # Independent regression values, computed from the FNV/fold constants.
        self.assertEqual(fnv1a(b"w0:data0123"), 0x31be90fd0b4db280)
        self.assertEqual(worker_acc("w0:data0123"), 0x96b2b2353f662800)
        self.assertEqual(total_fold(), 0x7c209a0c59d000a0)
        self.assertEqual(tuple(worker_acc(inp) for inp in W_INPUT), (
            0x96B2B2353F662800, 0xD69325FB76935220, 0x341E12E7365A67D0,
            0x5AE70CEE0E562640, 0x780567CB064E63F0, 0x7C09AC01C41EC7B0,
            0x8BC68E3994B8CCD0))

    def test_accounting_against_display_baseline(self):
        self.assertEqual(validate_runtime_output(RUNTIME_GOOD, DISPLAY_ACCOUNTING), [])
        for key, bad in (("allocated", DISPLAY_ACCOUNTING["allocated"] + 4096),
                         ("free", DISPLAY_ACCOUNTING["free"] - 4096),
                         ("tables", DISPLAY_ACCOUNTING["tables"] + 1)):
            previous = dict(DISPLAY_ACCOUNTING)
            previous[key] = bad
            with self.subTest(key=key):
                self.assertTrue(validate_runtime_output(RUNTIME_GOOD, previous))

    def test_every_line_required(self):
        for line in RUNTIME_GOOD.splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_runtime_output(RUNTIME_GOOD.replace(line, b"", 1)))

    def test_worker_digest_fold_mismatch(self):
        good = good_worker_token()
        tampered = ("worker=0 acc=0x%X rounds=%d" % (worker_acc(W_INPUT[0]) + 1, ROUNDS)).encode()
        self.assertTrue(validate_runtime_output(RUNTIME_GOOD.replace(good, tampered, 1)))

    def test_worker_rounds_mismatch(self):
        self.assertTrue(validate_runtime_output(RUNTIME_GOOD.replace(b"rounds=40", b"rounds=39", 1)))

    def test_total_mismatch(self):
        token = ("[RUNTIME] total=%d\r\n" % total_fold()).encode()
        self.assertTrue(validate_runtime_output(RUNTIME_GOOD.replace(token,
                      ("[RUNTIME] total=%d\r\n" % (total_fold() + 1)).encode(), 1)))

    def test_formats_and_wrap_are_checked(self):
        for old, new in ((b'fmt0="rynor 42 2a"', b'fmt0="rynor 42 2b"'),
                         (b'fmt1="334"', b'fmt1="335"'),
                         (b'fmt2="FF"', b'fmt2="FE"'),
                         (b'wrap="cdef"', b'wrap="cdee"')):
            with self.subTest(old=old):
                self.assertTrue(validate_runtime_output(RUNTIME_GOOD.replace(old, new, 1)))

    def test_unbounded_duplicate_and_bad_encoding(self):
        for output in (RUNTIME_GOOD * 2, RUNTIME_GOOD + b"\xff", RUNTIME_GOOD * 100,
                       b"\x00" + RUNTIME_END):
            with self.subTest(output=type(output).__name__):
                self.assertTrue(validate_runtime_output(output))

    def test_complete_boot_ordering(self):
        from kbd_output import KBD_GOOD
        from display_output import DISPLAY_GOOD
        from boot_output import validate_boot_output, POST_IRQ
        from test_exception_output import parser_fixture
        from test_pmm_output import fixture as pmm_fixture
        from test_vm_output import fixture as vm_fixture
        from test_heap_output import fixture as heap_fixture
        from timer_output import TIMER_OUTPUT
        from sched_output import SCHED_GOOD
        before = parser_fixture() + pmm_fixture() + vm_fixture() + heap_fixture() + TIMER_OUTPUT
        compose = before + SCHED_GOOD + KBD_GOOD + DISPLAY_GOOD + RUNTIME_GOOD + POST_IRQ
        self.assertEqual(validate_boot_output(compose), [])
        for broken in (before + SCHED_GOOD + KBD_GOOD + DISPLAY_GOOD + POST_IRQ,
                       compose.replace(RUNTIME_GOOD, b""),
                       compose.replace(RUNTIME_GOOD, RUNTIME_GOOD * 2),
                       compose + RUNTIME_GOOD):
            self.assertTrue(validate_boot_output(broken))
