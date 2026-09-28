"""BOOT-A1 boot-image layout pins: header, split binary, and overlap checks.

These are pure host-contract tests for the replacement BIOS loading
path. They never execute guest code; QEMU suites prove execution.
"""

import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
import boot_layout
from boot_layout import (
    BOOT_BASE,
    BOOT_FILE_SIZE,
    BOOT_PART_SECTORS,
    FILE_MAX_SECTORS,
    HEADER_LBA,
    HEADER_MAGIC,
    HEADER_SIZE,
    HEADER_VERSION,
    KERNEL_FILE_MAX,
    KERNEL_LBA,
    KERNEL_MEM_MAX,
    KERNEL_PHYS_BASE,
    MEM_MAX_PAGES,
    assemble_boot_payload,
    check_boot_layout,
    expected_boot_tables,
    fnv1a_32,
    make_boot_header,
    parse_boot_header,
    split_linked_binary,
)


def base_symbols(**overrides):
    symbols = {
        "boot_transition": 0x8000,
        "__boot_start": 0x8000,
        "__boot_end": 0x9000,
        "__kernel_start": KERNEL_PHYS_BASE,
        "__payload_end": KERNEL_PHYS_BASE + 0x12345,
        "__kernel_end": KERNEL_PHYS_BASE + 0x13000,
        "rynorkernel_entry": KERNEL_PHYS_BASE,
        "__kernel_phys_base": KERNEL_PHYS_BASE,
        "__boot_file_size": BOOT_FILE_SIZE,
        "__kernel_file_max": KERNEL_FILE_MAX,
        "__kernel_mem_max": KERNEL_MEM_MAX,
        "__page_tables_start": 0x1000,
        "__page_tables_end": 0x4000,
        "__boot_map_start": 0x4000,
        "__boot_map_end": 0x5000,
        "__fb_info_start": 0x5000,
        "__fb_info_end": 0x6000,
        "__boot_stack_start": 0x7000,
        "__boot_stack_end": 0x7C00,
        "__boot_sector_start": 0x7C00,
        "__boot_sector_end": 0x7E00,
        "__kernel_stack_start": 0x7C000,
        "__kernel_stack_end": 0x80000,
    }
    symbols.update(overrides)
    return symbols


class BootLayoutTests(unittest.TestCase):
    def test_header_round_trip_and_offsets(self):
        header = make_boot_header(
            file_sectors=3,
            mem_pages=4,
            checksum=0x12345678,
        )
        self.assertEqual(len(header), HEADER_SIZE)
        self.assertEqual(header[0:4], b"RBHN")
        self.assertEqual(struct.unpack_from("<IHHIII", header, 0),
                         (HEADER_MAGIC, HEADER_VERSION, 0, 3, 4, 0x12345678))
        self.assertEqual(struct.unpack_from("<III", header, 20),
                         (KERNEL_PHYS_BASE, KERNEL_PHYS_BASE, 0))
        self.assertEqual(header[32:], bytes(HEADER_SIZE - 32))
        parsed = parse_boot_header(header)
        self.assertEqual(parsed, {
            "magic": HEADER_MAGIC,
            "version": HEADER_VERSION,
            "flags": 0,
            "file_sectors": 3,
            "mem_pages": 4,
            "checksum": 0x12345678,
            "load_base": KERNEL_PHYS_BASE,
            "entry": KERNEL_PHYS_BASE,
            "reserved": 0,
        })

    def test_header_rejects_bad_fields(self):
        good = make_boot_header(file_sectors=8, mem_pages=8, checksum=1)
        cases = [
            ("bad-magic", good[:0] + b"XXXX" + good[4:]),
            ("bad-version", good[:4] + struct.pack("<H", 2) + good[6:]),
            ("bad-flags", good[:6] + struct.pack("<H", 1) + good[8:]),
            ("zero-file", good[:8] + struct.pack("<I", 0) + good[12:]),
            ("large-file", good[:8] + struct.pack("<I", FILE_MAX_SECTORS + 1) + good[12:]),
            ("small-mem", good[:12] + struct.pack("<I", 0) + good[16:]),
            ("large-mem", good[:12] + struct.pack("<I", MEM_MAX_PAGES + 1) + good[16:]),
            ("bad-base", good[:20] + struct.pack("<I", 0x100000) + good[24:]),
            ("bad-entry", good[:24] + struct.pack("<I", 0x100000) + good[28:]),
            ("bad-reserved", good[:28] + struct.pack("<I", 1) + good[32:]),
            ("trailing-data", good[:511] + b"\x01"),
            ("short", good[:-1]),
            ("long", good + b"\x00"),
        ]
        for name, header in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    parse_boot_header(header)
        with self.assertRaises(ValueError):
            make_boot_header(file_sectors=9, mem_pages=1, checksum=0)

    def test_split_linked_binary_drops_verified_gap(self):
        boot = bytes(index % 251 for index in range(BOOT_FILE_SIZE))
        kernel = bytes((index * 7) % 251 for index in range(1025))
        gap = bytes(KERNEL_PHYS_BASE - BOOT_BASE - BOOT_FILE_SIZE)
        linked = boot + gap + kernel
        got_boot, got_kernel = split_linked_binary(
            linked, KERNEL_PHYS_BASE, KERNEL_PHYS_BASE + len(kernel))
        self.assertEqual(got_boot, boot)
        self.assertEqual(got_kernel, kernel)

        corrupted = bytearray(linked)
        corrupted[BOOT_FILE_SIZE] = 1
        with self.assertRaisesRegex(ValueError, "zero gap"):
            split_linked_binary(bytes(corrupted), KERNEL_PHYS_BASE,
                                KERNEL_PHYS_BASE + len(kernel))
        with self.assertRaises(ValueError):
            split_linked_binary(linked[:-1], KERNEL_PHYS_BASE,
                                KERNEL_PHYS_BASE + len(kernel))
        with self.assertRaises(ValueError):
            split_linked_binary(linked, 0x100000, 0x100000 + len(kernel))

    def test_assemble_boot_payload_layout_and_checksum(self):
        boot = bytes(index % 251 for index in range(BOOT_FILE_SIZE))
        kernel = bytes((index * 7) % 251 for index in range(1025))
        payload, metadata = assemble_boot_payload(boot, kernel, 0x2000)
        self.assertEqual(metadata["file_sectors"], 3)
        self.assertEqual(metadata["mem_pages"], 2)
        self.assertEqual(metadata["kernel_file_bytes"], len(kernel))
        self.assertEqual(metadata["kernel_mem_bytes"], 0x2000)
        padded = kernel + bytes(3 * 512 - len(kernel))
        self.assertEqual(metadata["checksum"], fnv1a_32(padded))
        self.assertEqual(payload[:BOOT_FILE_SIZE], boot)
        self.assertEqual(payload[BOOT_FILE_SIZE:BOOT_FILE_SIZE + HEADER_SIZE],
                         metadata["header"])
        self.assertEqual(payload[BOOT_FILE_SIZE + HEADER_SIZE:], padded)
        self.assertEqual(parse_boot_header(metadata["header"])["checksum"],
                         metadata["checksum"])

    def test_assemble_boot_payload_rejects_bounds(self):
        boot = bytes(BOOT_FILE_SIZE)
        with self.assertRaises(ValueError):
            assemble_boot_payload(boot[:-1], b"x", 0x1000)
        with self.assertRaises(ValueError):
            assemble_boot_payload(boot, b"", 0x1000)
        with self.assertRaises(ValueError):
            assemble_boot_payload(boot, bytes(KERNEL_FILE_MAX + 1), KERNEL_MEM_MAX)
        # Linker BSS ends are not page-aligned; the loader rounds them up.
        _payload, metadata = assemble_boot_payload(boot, b"x", 0x1001)
        self.assertEqual(metadata["mem_pages"], 2)
        with self.assertRaises(ValueError):
            assemble_boot_payload(boot, bytes(9 * 512), 0x1000)
        with self.assertRaises(ValueError):
            assemble_boot_payload(boot, b"x", KERNEL_MEM_MAX + 0x1000)

    def test_expected_boot_tables_follow_kernel_extent(self):
        self.assertEqual(expected_boot_tables(KERNEL_PHYS_BASE + 0x1000), 8)
        self.assertEqual(expected_boot_tables(KERNEL_PHYS_BASE + 1), 8)
        self.assertEqual(expected_boot_tables(0xA00000), 8)
        self.assertEqual(expected_boot_tables(0xA00000 + 0x1000), 9)
        self.assertEqual(expected_boot_tables(KERNEL_PHYS_BASE + KERNEL_MEM_MAX), 15)
        for end in (KERNEL_PHYS_BASE,
                    KERNEL_PHYS_BASE + KERNEL_MEM_MAX + 0x1000, True):
            with self.subTest(end=end):
                with self.assertRaises(ValueError):
                    expected_boot_tables(end)

    def test_boot_overlap_checker(self):
        self.assertEqual(check_boot_layout(base_symbols()), [])
        mutated = base_symbols(__boot_end=0x7100)
        self.assertTrue(check_boot_layout(mutated))
        mutated = base_symbols(__kernel_start=0x70000, __payload_end=0x71000,
                               __kernel_end=0x72000,
                               rynorkernel_entry=0x70000)
        self.assertTrue(check_boot_layout(mutated))
        mutated = base_symbols(__kernel_end=KERNEL_PHYS_BASE + KERNEL_MEM_MAX + 0x1000)
        self.assertTrue(check_boot_layout(mutated))
        mutated = base_symbols(__payload_end=KERNEL_PHYS_BASE + KERNEL_MEM_MAX + 1)
        self.assertTrue(check_boot_layout(mutated))
        incomplete = base_symbols()
        del incomplete["__kernel_end"]
        self.assertTrue(check_boot_layout(incomplete))

    def test_layout_constants_match_sources(self):
        transition = (ROOT / "boot/transition.asm").read_text(encoding="utf-8")
        linker = (ROOT / "kernel/arch/x86_64/linker.ld").read_text(encoding="utf-8")
        sector = (ROOT / "boot/sector.asm").read_text(encoding="utf-8")

        def equ(name):
            match = re.search(r"^%s equ (0x[0-9a-fA-F]+|\d+)\b" % name,
                              transition, re.MULTILINE)
            self.assertIsNotNone(match, name)
            return int(match.group(1), 0)

        self.assertEqual(equ("HDR_LBA"), HEADER_LBA)
        self.assertEqual(equ("KERN_LBA"), KERNEL_LBA)
        self.assertEqual(equ("HDR_MAGIC"), HEADER_MAGIC)
        self.assertEqual(equ("HDR_VERSION"), HEADER_VERSION)
        self.assertEqual(equ("KERN_BASE"), KERNEL_PHYS_BASE)
        self.assertEqual(equ("FILE_MAX_SECTORS"), FILE_MAX_SECTORS)
        self.assertEqual(equ("MEM_MAX_PAGES"), MEM_MAX_PAGES)
        self.assertEqual(equ("HDR_BASE"), boot_layout.HEADER_SCRATCH_BASE)
        self.assertEqual(equ("STAGE_BASE"), boot_layout.STAGE_BASE)
        self.assertEqual(equ("STAGE_LEN"), boot_layout.STAGE_SIZE)
        self.assertEqual(equ("CALL_SECTORS"), boot_layout.BIOS_CALL_SECTORS)
        self.assertEqual(equ("PD_MAX"), boot_layout.BOOT_PD_MAX)
        self.assertIn("BOOT_SECTORS equ %d" % BOOT_PART_SECTORS, sector)
        for name, value in (
            ("__kernel_phys_base", KERNEL_PHYS_BASE),
            ("__boot_file_size", BOOT_FILE_SIZE),
            ("__kernel_file_max", KERNEL_FILE_MAX),
            ("__kernel_mem_max", KERNEL_MEM_MAX),
        ):
            match = re.search(r"^%s = (0x[0-9a-fA-F]+|\d+);$" % name,
                              linker, re.MULTILINE)
            self.assertIsNotNone(match, name)
            self.assertEqual(int(match.group(1), 0), value, name)


if __name__ == "__main__":
    unittest.main()
