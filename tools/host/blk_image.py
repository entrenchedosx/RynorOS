#!/usr/bin/env python3
"""Deterministic disposable block-image tool for Stage 17a tests.

Creates raw test images with recognizable per-block patterns (never random)
so a test can prove which block was actually read. Not part of any RynorOS
image; host test infrastructure only.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

MAGIC = b"RLBLK1\x00\x00"
BLOCK = 512


def pattern(block_no: int, size: int = BLOCK, tag: int = 0xA5) -> bytes:
    """Deliberate per-block pattern: block N is uniquely identifiable."""
    return bytes(((block_no * 131 + i * 17 + tag) & 0xFF) for i in range(size))


def writeback_pattern(block_no: int, size: int = BLOCK) -> bytes:
    """Pattern the guest writes in write/readback tests (tag differs)."""
    return pattern(block_no, size, tag=0x5A)


def block_sum(data: bytes) -> int:
    return sum(data)


def block_wsum(data: bytes) -> int:
    """Position-weighted sum; distinguishes rotated patterns with equal sums."""
    return sum(i * byte for i, byte in enumerate(data))


def create(path: str | Path, size_mib: int) -> Path:
    """Write a patterned image: header block 0 + patterned data blocks."""
    if type(size_mib) is not int or size_mib < 1:
        raise ValueError("size_mib must be a positive int")
    path = Path(path)
    nblocks = size_mib * 1048576 // BLOCK
    header = MAGIC + struct.pack("<I", BLOCK) + struct.pack("<Q", nblocks)
    first = header + pattern(0)[len(header):]
    with open(path, "wb") as handle:
        handle.write(first)
        for block_no in range(1, nblocks):
            handle.write(pattern(block_no))
    return path


def create_zeroed(path: str | Path, size_mib: int) -> Path:
    """Write an all-zero image (no magic): discovery must ignore it."""
    if type(size_mib) is not int or size_mib < 1:
        raise ValueError("size_mib must be a positive int")
    path = Path(path)
    with open(path, "wb") as handle:
        handle.write(b"\x00" * (size_mib * 1048576))
    return path


def read_block(path: str | Path, block_no: int) -> bytes:
    with open(path, "rb") as handle:
        handle.seek(block_no * BLOCK)
        data = handle.read(BLOCK)
    if len(data) != BLOCK:
        raise ValueError(f"block {block_no} outside image {path}")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create", help="patterned image with RLBLK1 header")
    make.add_argument("path", type=Path)
    make.add_argument("mib", type=int)
    zero = sub.add_parser("zero", help="all-zero image without magic")
    zero.add_argument("path", type=Path)
    zero.add_argument("mib", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create(args.path, args.mib)
        else:
            create_zero(args.path, args.mib)
    except (OSError, ValueError) as error:
        print(f"blk_image: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
