"""BOOT-A1 BIOS boot-image layout: header, split binary, and overlap checks."""

import struct

from kernel_elf import read_symbols

SECTOR_SIZE = 512
PAGE_SIZE = 4096
BOOT_BASE = 0x8000
BOOT_FILE_SIZE = 0x1000
BOOT_PART_LBA = 1
BOOT_PART_SECTORS = 8
HEADER_LBA = 9
HEADER_SIZE = 512
KERNEL_LBA = 10
KERNEL_PHYS_BASE = 0x800000
KERNEL_FILE_MAX = 0x800000
KERNEL_MEM_MAX = 0x1000000
FILE_MAX_SECTORS = 16384
MEM_MAX_PAGES = 4096
BOOT_PAGE_TABLES = 7
BOOT_PD_MAX = 12
BIOS_CALL_SECTORS = 64
HEADER_SCRATCH_BASE = 0x6000
HEADER_SCRATCH_SIZE = 0x1000
STAGE_BASE = 0x10000
STAGE_SIZE = 0x60000
HEADER_MAGIC = 0x4E484252
HEADER_VERSION = 1
MAX_BOOT_PAYLOAD = BOOT_FILE_SIZE + HEADER_SIZE + KERNEL_FILE_MAX


def fnv1a_32(data: bytes) -> int:
    if type(data) is not bytes:
        raise ValueError("FNV input must be bytes")
    value = 0x811C9DC5
    for byte in data:
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


def make_boot_header(file_sectors: int, mem_pages: int, checksum: int) -> bytes:
    for name, value in (("file_sectors", file_sectors), ("mem_pages", mem_pages),
                        ("checksum", checksum)):
        if type(value) is not int or isinstance(value, bool):
            raise ValueError(f"Boot header {name} must be an integer")
    if not 0 < file_sectors <= FILE_MAX_SECTORS:
        raise ValueError("Boot header file extent out of range")
    file_pages = (file_sectors + 7) // 8
    if not file_pages <= mem_pages <= MEM_MAX_PAGES:
        raise ValueError("Boot header memory extent out of range")
    if not 0 <= checksum <= 0xFFFFFFFF:
        raise ValueError("Boot header checksum out of range")
    header = struct.pack("<IHHIII", HEADER_MAGIC, HEADER_VERSION, 0,
                         file_sectors, mem_pages, checksum)
    header += struct.pack("<III", KERNEL_PHYS_BASE, KERNEL_PHYS_BASE, 0)
    return header + bytes(HEADER_SIZE - len(header))


def parse_boot_header(header: bytes) -> dict:
    if type(header) is not bytes or len(header) != HEADER_SIZE:
        raise ValueError("Boot header must be exactly one sector")
    magic, version, flags, file_sectors, mem_pages, checksum = struct.unpack_from(
        "<IHHIII", header, 0)
    load_base, entry, reserved = struct.unpack_from("<III", header, 20)
    if header[32:] != bytes(HEADER_SIZE - 32):
        raise ValueError("Boot header trailing bytes must be zero")
    if (magic != HEADER_MAGIC or version != HEADER_VERSION or flags != 0 or
            load_base != KERNEL_PHYS_BASE or entry != KERNEL_PHYS_BASE or
            reserved != 0):
        raise ValueError("Boot header identity fields invalid")
    if not 0 < file_sectors <= FILE_MAX_SECTORS:
        raise ValueError("Boot header file extent out of range")
    file_pages = (file_sectors + 7) // 8
    if not file_pages <= mem_pages <= MEM_MAX_PAGES:
        raise ValueError("Boot header memory extent out of range")
    return {"magic": magic, "version": version, "flags": flags,
            "file_sectors": file_sectors, "mem_pages": mem_pages,
            "checksum": checksum, "load_base": load_base, "entry": entry,
            "reserved": reserved}


def split_linked_binary(linked: bytes, kernel_start: int, payload_end: int) -> tuple:
    if type(linked) is not bytes:
        raise ValueError("Linked binary must be bytes")
    for name, value in (("kernel_start", kernel_start), ("payload_end", payload_end)):
        if type(value) is not int or isinstance(value, bool):
            raise ValueError(f"Linked extent {name} must be an integer")
    if kernel_start != KERNEL_PHYS_BASE:
        raise ValueError("Linked kernel must start at the frozen load base")
    file_bytes = payload_end - kernel_start
    if not 0 < file_bytes <= KERNEL_FILE_MAX:
        raise ValueError("Linked kernel file extent out of range")
    expected = (KERNEL_PHYS_BASE - BOOT_BASE) + file_bytes
    if len(linked) != expected:
        raise ValueError("Linked binary length disagrees with ELF extents")
    boot_part = linked[:BOOT_FILE_SIZE]
    gap = linked[BOOT_FILE_SIZE:KERNEL_PHYS_BASE - BOOT_BASE]
    if gap.strip(b"\0"):
        raise ValueError("Linked binary VMA gap is not a zero gap")
    return boot_part, linked[KERNEL_PHYS_BASE - BOOT_BASE:]


def assemble_boot_payload(boot_part: bytes, kernel_file: bytes,
                          kernel_mem_bytes: int) -> tuple:
    if type(boot_part) is not bytes or type(kernel_file) is not bytes:
        raise ValueError("Boot payload inputs must be bytes")
    if type(kernel_mem_bytes) is not int or isinstance(kernel_mem_bytes, bool):
        raise ValueError("Kernel memory extent must be an integer")
    if len(boot_part) != BOOT_FILE_SIZE:
        raise ValueError("Boot part must be exactly 4 KiB")
    if not 0 < len(kernel_file) <= KERNEL_FILE_MAX:
        raise ValueError("Kernel file extent out of range")
    if not len(kernel_file) <= kernel_mem_bytes <= KERNEL_MEM_MAX:
        raise ValueError("Kernel memory extent out of range")
    file_sectors = (len(kernel_file) + SECTOR_SIZE - 1) // SECTOR_SIZE
    mem_pages = (kernel_mem_bytes + PAGE_SIZE - 1) // PAGE_SIZE
    if not (file_sectors + 7) // 8 <= mem_pages <= MEM_MAX_PAGES:
        raise ValueError("Kernel memory extent is smaller than its file")
    padded = kernel_file + bytes(file_sectors * SECTOR_SIZE - len(kernel_file))
    checksum = fnv1a_32(padded)
    header = make_boot_header(file_sectors, mem_pages, checksum)
    payload = boot_part + header + padded
    if len(payload) > MAX_BOOT_PAYLOAD:
        raise ValueError("Assembled boot payload exceeds its bound")
    metadata = {"header": header, "file_sectors": file_sectors,
                "mem_pages": mem_pages, "checksum": checksum,
                "kernel_file_bytes": len(kernel_file),
                "kernel_mem_bytes": kernel_mem_bytes}
    return payload, metadata


def elf_boot_layout(elf_path) -> dict:
    """Kernel extent and layout-driven table counts from the linked ELF.

    The host recomputes these independently of guest claims so serial
    accounting (VM tables, PMM reservation, user address spaces) is
    checked against the binary, not trusted from the transcript."""
    symbols = read_symbols(elf_path, ("__kernel_start", "__kernel_end"))
    start = symbols["__kernel_start"][0]
    end = (symbols["__kernel_end"][0] + PAGE_SIZE - 1) // PAGE_SIZE * PAGE_SIZE
    if start != KERNEL_PHYS_BASE or end <= start or end - start > KERNEL_MEM_MAX:
        raise ValueError("ELF kernel extent outside the frozen window")
    touched = (end - start + 0x1FFFFF) // 0x200000
    return {"kernel_start": start, "kernel_end": end,
            "vm_tables": expected_boot_tables(end),
            "user_tables": 6 + touched}


def expected_boot_tables(kernel_end: int) -> int:
    if type(kernel_end) is not int or isinstance(kernel_end, bool):
        raise ValueError("Kernel end must be an integer")
    if (kernel_end <= KERNEL_PHYS_BASE or
            kernel_end - KERNEL_PHYS_BASE > KERNEL_MEM_MAX):
        raise ValueError("Kernel memory extent out of range")
    touched = ((kernel_end + 0x1FFFFF) // 0x200000 -
               KERNEL_PHYS_BASE // 0x200000)
    if not 1 <= touched <= 8:
        raise ValueError("Kernel extent needs an impossible page-table count")
    return BOOT_PAGE_TABLES + touched


REQUIRED_BOOT_SYMBOLS = (
    "boot_transition", "__boot_start", "__boot_end", "__kernel_start",
    "__payload_end", "__kernel_end", "rynorkernel_entry",
    "__kernel_phys_base", "__boot_file_size", "__kernel_file_max",
    "__kernel_mem_max",
    "__page_tables_start", "__page_tables_end", "__boot_map_start",
    "__boot_map_end", "__fb_info_start", "__fb_info_end",
    "__boot_stack_start", "__boot_stack_end", "__boot_sector_start",
    "__boot_sector_end", "__kernel_stack_start", "__kernel_stack_end",
)
FIXED_BOOT_SYMBOLS = {
    "boot_transition": 0x8000,
    "__boot_start": 0x8000,
    "__boot_end": 0x9000,
    "__kernel_start": KERNEL_PHYS_BASE,
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


def check_boot_layout(symbols: dict) -> list:
    if type(symbols) is not dict:
        return ["boot symbols must be a mapping"]
    errors = []
    values = {}
    for name in REQUIRED_BOOT_SYMBOLS:
        if name not in symbols:
            errors.append(f"missing boot symbol: {name}")
            continue
        value = symbols[name]
        if type(value) is not int or isinstance(value, bool):
            errors.append(f"invalid boot symbol: {name}")
            continue
        values[name] = value
        if name in FIXED_BOOT_SYMBOLS and value != FIXED_BOOT_SYMBOLS[name]:
            errors.append(f"boot symbol moved: {name}={value:#x}")
    if {"__kernel_start", "__payload_end", "__kernel_end"} <= set(values):
        file_bytes = values["__payload_end"] - values["__kernel_start"]
        mem_bytes = values["__kernel_end"] - values["__kernel_start"]
        if not 0 < file_bytes <= KERNEL_FILE_MAX:
            errors.append("kernel file extent out of range")
        mem_pages = (mem_bytes + PAGE_SIZE - 1) // PAGE_SIZE if mem_bytes > 0 else 0
        if not 0 < mem_bytes <= KERNEL_MEM_MAX or mem_pages > MEM_MAX_PAGES:
            errors.append("kernel memory extent out of range")
        if values["__payload_end"] > values["__kernel_end"]:
            errors.append("kernel file extends past kernel memory")
    ranges = [
        ("page_tables", values.get("__page_tables_start"), values.get("__page_tables_end")),
        ("boot_map", values.get("__boot_map_start"), values.get("__boot_map_end")),
        ("fb_info", values.get("__fb_info_start"), values.get("__fb_info_end")),
        ("header_scratch", HEADER_SCRATCH_BASE,
         HEADER_SCRATCH_BASE + HEADER_SCRATCH_SIZE),
        ("boot_stack", values.get("__boot_stack_start"), values.get("__boot_stack_end")),
        ("boot_sector", values.get("__boot_sector_start"), values.get("__boot_sector_end")),
        ("boot_part", values.get("__boot_start"), values.get("__boot_end")),
        ("staging", STAGE_BASE, STAGE_BASE + STAGE_SIZE),
        ("kernel_stack", values.get("__kernel_stack_start"),
         values.get("__kernel_stack_end")),
        ("kernel_image", values.get("__kernel_start"), values.get("__kernel_end")),
    ]
    for name, start, end in ranges:
        if type(start) is not int or type(end) is not int:
            continue
        if start >= end:
            errors.append(f"boot range inverted: {name}")
    for left in range(len(ranges)):
        for right in range(left + 1, len(ranges)):
            first, second = ranges[left], ranges[right]
            if (type(first[1]) is not int or type(first[2]) is not int or
                    type(second[1]) is not int or type(second[2]) is not int):
                continue
            if max(first[1], second[1]) < min(first[2], second[2]):
                errors.append(f"boot range overlap: {first[0]}/{second[0]}")
    return sorted(errors)
