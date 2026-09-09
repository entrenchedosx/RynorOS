"""RYNX v1/v2: RynorOS userspace executable envelope (host side).

Stage 18b loads native RynorLang programs linked at fixed user virtual
addresses (.text at USER_CODE_BASE, rodata/data/bss in the data window).
The kernel never parses ELF: this module converts a linked ELF64 into a
minimal envelope the kernel validates with checked arithmetic, and
rejects anything outside the documented subset. See
docs/design/executable-format.md.

Stage 18d Slice C adds version 2 (same 28-byte layout, version-gated
size classes): code tiles up to 32 KiB, data up to 16 KiB. v1 behavior
is byte-identical (default conversion path unchanged).

Envelope layout (all little-endian, 28 bytes, no trailing bytes):
  u8[4]  magic "RYNX"
  u16    version (1 or 2)
  u16    arch (1 = x86-64)
  u16    header_len (28)
  u16    reserved (0)
  u32    entry_off (0: entry is defined as USER_CODE_BASE)
  u32    code_size (v1: 1..4096; v2: 1..32768)
  u32    data_filesz (v1: 0..4096; v2: 0..16384)
  u32    data_memsz (filesz..same class max)
followed by code_size code bytes then data_filesz data bytes.
"""

import struct

MAGIC = b"RYNX"
VERSION = 1
VERSION2 = 2
ARCH_X86_64 = 1
HEADER_LEN = 28

CODE_BASE = 0x400000
DATA_BASE = 0x600000
PAGE = 4096
CODE_MAX2 = 8 * PAGE
DATA_MAX2 = 4 * PAGE

PT_NULL = 0
PT_LOAD = 1
PT_GNU_STACK = 0x6474E551

PF_X = 1
PF_W = 2
PF_R = 4

ET_EXEC = 2
EM_X86_64 = 62


def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


def build_envelope(code: bytes, data_filesz: int, data_memsz: int, data: bytes,
                   version: int = VERSION) -> bytes:
    """Assemble a validated envelope (used by tests and the converter)."""
    if version not in (VERSION, VERSION2):
        raise ValueError(f"unknown RYNX version {version}")
    code_max = PAGE if version == VERSION else CODE_MAX2
    data_max = PAGE if version == VERSION else DATA_MAX2
    if not 1 <= len(code) <= code_max:
        raise ValueError("bad code size for version %d" % version)
    if not 0 <= data_filesz <= data_memsz <= data_max:
        raise ValueError("bad data sizes for version %d" % version)
    if len(bytes(data)) != data_filesz:
        raise ValueError("data payload length mismatch")
    header = (MAGIC + struct.pack("<HHHH", version, ARCH_X86_64, HEADER_LEN, 0)
              + struct.pack("<IIII", 0, len(code), data_filesz, data_memsz))
    return header + bytes(code) + bytes(data)


def elf_to_rnyx(data: bytes, version: int = VERSION) -> bytes:
    """Convert a linked RynorOS ELF to RYNX bytes. Raises ValueError.

    version selects the size class (1: single-page windows, the default
    byte-identical path; 2: bounded multi-page windows). The ELF itself
    must already observe the chosen windows (enforced by the link
    script); this function never widens v1 images.
    """
    if version not in (VERSION, VERSION2):
        raise ValueError(f"unknown RYNX version {version}")
    code_max = PAGE if version == VERSION else CODE_MAX2
    data_max = PAGE if version == VERSION else DATA_MAX2
    data = bytes(data)
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not a 64-bit little-endian ELF")
    e_type = _u16(data, 16)
    e_machine = _u16(data, 18)
    e_entry = _u64(data, 24)
    e_phoff = _u64(data, 32)
    e_phentsize = _u16(data, 54)
    e_phnum = _u16(data, 56)
    if e_type != ET_EXEC:
        raise ValueError(f"not ET_EXEC (type {e_type})")
    if e_machine != EM_X86_64:
        raise ValueError(f"not x86-64 (machine {e_machine})")
    if e_entry != CODE_BASE:
        raise ValueError(f"entry {e_entry:#x} is not the code base")
    if e_phentsize < 56 or e_phoff + e_phnum * e_phentsize > len(data):
        raise ValueError("program header table out of file")
    code = None
    code_filesz = 0
    data_loads = []
    for index in range(e_phnum):
        off = e_phoff + index * e_phentsize
        p_type, p_flags = _u32(data, off), _u32(data, off + 4)
        p_offset, p_vaddr = _u64(data, off + 8), _u64(data, off + 16)
        p_filesz, p_memsz = _u64(data, off + 32), _u64(data, off + 40)
        if p_type == PT_NULL or p_type == PT_GNU_STACK:
            continue
        if p_type != PT_LOAD:
            raise ValueError(f"unsupported segment type {p_type:#x}")
        if p_offset + p_filesz < p_offset or p_offset + p_filesz > len(data):
            raise ValueError("segment file range out of file")
        if p_filesz > p_memsz:
            raise ValueError("filesz exceeds memsz")
        if p_flags & ~(PF_X | PF_W | PF_R):
            raise ValueError(f"bad segment flags {p_flags:#x}")
        if p_memsz == 0:
            continue
        if p_flags & PF_X:
            if p_flags & PF_W:
                raise ValueError("W+X segment")
            if p_memsz != p_filesz:
                raise ValueError("BSS in executable segment")
            if p_vaddr != CODE_BASE or p_memsz > code_max:
                raise ValueError("code outside the code window")
            if code is not None:
                raise ValueError("duplicate code segment")
            code = data[p_offset:p_offset + p_filesz]
            code_filesz = p_filesz
        else:
            # Data-window load, writable or read-only (the linker may
            # split .rodata R-only from .data/.bss RW; the kernel maps
            # the whole page U-RW, so both tile one file image; string
            # immutability inside it is a language property).
            if p_vaddr < DATA_BASE or p_vaddr + p_memsz < p_vaddr \
                    or p_vaddr + p_memsz > DATA_BASE + data_max:
                raise ValueError("data outside the data window")
            data_loads.append((p_vaddr, p_offset, p_filesz, p_memsz))
    if code is None:
        raise ValueError("no code segment")
    if not 0 < code_filesz <= code_max:
        raise ValueError("bad code size")
    data_loads.sort()
    data_blob = b""
    data_memsz = 0
    cursor = DATA_BASE
    for number, (vaddr, offset, filesz, memsz) in enumerate(data_loads):
        if vaddr != cursor:
            raise ValueError("data loads must tile contiguously from the data base")
        if number != len(data_loads) - 1 and filesz != memsz:
            raise ValueError("BSS only allowed on the last data load")
        data_blob += data[offset:offset + filesz]
        cursor += memsz
        data_memsz += memsz
    data_filesz = len(data_blob)
    if not 0 <= data_filesz <= data_memsz <= data_max:
        raise ValueError("bad data sizes")
    return build_envelope(code, data_filesz, data_memsz, data_blob,
                          version=version)
