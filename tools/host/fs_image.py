#!/usr/bin/env python3
"""Deterministic RYNORFS v1 image builder for Stage 17b tests.

Builds exact-fit filesystem images from an explicit entry list (plus
targeted corruptions for negative tests). Sorted canonical order, no
timestamps, no host paths, no randomness. Host test infrastructure only.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

MAGIC = b"RYNORFS\x00"
VERSION = 1
BLOCK = 512
DIR_SLOTS_PER_BLOCK = 8
MAX_DIR_BLOCKS = 64
TYPE_FILE = 1
TYPE_DIR = 2


def split_path(path: str) -> list:
    if not path.startswith("/") or len(path) < 2 or len(path) > 32:
        raise ValueError(f"bad path {path!r}")
    if path.endswith("/") or "//" in path:
        raise ValueError(f"bad path {path!r}")
    parts = path[1:].split("/")
    for part in parts:
        if not part or len(part) > 31 or part in (".", ".."):
            raise ValueError(f"bad component {part!r} in {path!r}")
        if any(not 0x20 <= ord(c) <= 0x7E for c in part):
            raise ValueError(f"non-printable component {part!r}")
    return parts


def encode_entry(name: str, ftype: int, first: int, count: int, length: int) -> bytes:
    if not 1 <= len(name) <= 31:
        raise ValueError(f"bad name {name!r}")
    raw = name.encode("ascii")
    if any(not 0x20 <= c <= 0x7E for c in raw):
        raise ValueError(f"bad name bytes {name!r}")
    record = bytearray(64)
    record[0:len(raw)] = raw
    record[32] = ftype
    struct.pack_into("<Q", record, 40, first)
    struct.pack_into("<Q", record, 48, count)
    struct.pack_into("<Q", record, 56, length)
    return bytes(record)


def build(entries: list, pad_blocks: int = 0) -> bytes:
    """entries: [(path, bytes|None)] with None marking a directory.

    Intermediate directories are auto-created; exact duplicates and
    file/dir conflicts raise ValueError; output is sorted canonically.
    pad_blocks appends trailing zeroed device blocks beyond the fs.
    """
    files: dict = {}
    dirs: set = set()
    for path, content in entries:
        parts = split_path(path)
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in files:
                raise ValueError(f"conflict: {parent!r} is a file")
            dirs.add(parent)
        key = "/".join(parts)
        if key in files or key in dirs:
            raise ValueError(f"duplicate entry {path!r}")
        if content is None:
            if key in files:
                raise ValueError(f"conflict: {key!r}")
            dirs.add(key)
        else:
            if not isinstance(content, (bytes, bytearray)):
                raise ValueError(f"bad content for {path!r}")
            files[key] = bytes(content)
    records = []
    data_blobs = []
    for name in sorted(dirs):
        records.append((name, encode_entry(name, TYPE_DIR, 0, 0, 0)))
    data_block = 0
    data_map = {}
    for name in sorted(files):
        blob = files[name]
        nblocks = (len(blob) + BLOCK - 1) // BLOCK if blob else 0
        # Canonical empty file: (0,0,0), matching the kernel/host rule that
        # a zero-count extent carries no first-block.
        data_map[name] = (data_block if nblocks else 0, nblocks)
        data_block += nblocks
        records.append((name, None))
    records.sort(key=lambda item: item[0])
    nentries = len(records)
    dir_blocks = max(1, (nentries + DIR_SLOTS_PER_BLOCK - 1) // DIR_SLOTS_PER_BLOCK)
    if dir_blocks > MAX_DIR_BLOCKS:
        raise ValueError("directory too large for v1")
    # Data region is never degenerate (even an empty filesystem carries one
    # zeroed block); the kernel requires data_blocks >= 1.
    data_blocks = max(1, data_block)
    data_start = 1 + dir_blocks
    total = 1 + dir_blocks + data_blocks + pad_blocks
    body = []
    for name, record in records:
        if record is not None:
            body.append(record)
            continue
        first, count = data_map[name]
        length = len(files[name])
        # Absolute first block; canonical empty file is (0,0,0).
        body.append(encode_entry(name, TYPE_FILE, data_start + first if count else 0,
                                 count, length))
    while len(body) < dir_blocks * DIR_SLOTS_PER_BLOCK:
        body.append(bytes(64))
    assert len(body) == dir_blocks * DIR_SLOTS_PER_BLOCK
    header = bytearray(BLOCK)
    header[0:8] = MAGIC
    struct.pack_into("<I", header, 8, VERSION)
    struct.pack_into("<I", header, 12, BLOCK)
    struct.pack_into("<Q", header, 16, total)
    struct.pack_into("<Q", header, 24, 1)
    struct.pack_into("<Q", header, 32, dir_blocks)
    struct.pack_into("<Q", header, 40, data_start)
    struct.pack_into("<Q", header, 48, data_blocks)
    out = bytearray()
    out += header
    for chunk in body:
        out += chunk
    for name in sorted(files):
        blob = files[name]
        out += blob
        padding = (-len(blob)) % BLOCK if blob else 0
        out += b"\x00" * padding
    out += b"\x00" * ((data_blocks - data_block) * BLOCK)
    out += b"\x00" * (pad_blocks * BLOCK)
    assert len(out) == total * BLOCK, (len(out), total)
    return bytes(out)


def corrupt(image: bytes, kind: str) -> bytes:
    """Produce a deterministically damaged image for negative tests."""
    data = bytearray(image)
    total = struct.unpack("<Q", data[16:24])[0]
    dir_start = struct.unpack("<Q", data[24:32])[0]
    if kind == "magic":
        data[0:8] = b"BADMAGIC"
    elif kind == "version":
        struct.pack_into("<I", data, 8, 2)
    elif kind == "blksize":
        struct.pack_into("<I", data, 12, 1024)
    elif kind == "reserved":
        struct.pack_into("<Q", data, 56, 1)
    elif kind == "total0":
        struct.pack_into("<Q", data, 16, 0)
    elif kind == "dir_oob":
        struct.pack_into("<Q", data, 24, total + 10)
    elif kind == "dir_big":
        struct.pack_into("<Q", data, 32, 65)
    elif kind == "data_overlap":
        struct.pack_into("<Q", data, 40, dir_start)
    elif kind == "trunc":
        del data[-BLOCK:]
    else:
        raise ValueError(f"unknown corruption {kind!r}")
    return bytes(data)


def corrupt_entry(image: bytes, index: int, field: str, value: int) -> bytes:
    """Damage directory entry <index> (0-based over dir slots)."""
    data = bytearray(image)
    dir_start = struct.unpack("<Q", data[24:32])[0]
    base = dir_start * BLOCK + index * 64
    offsets = {"type": 32, "first": 40, "count": 48, "length": 56}
    if field == "dup":
        data[base:base + 64] = data[dir_start * BLOCK:dir_start * BLOCK + 64]
    elif field == "name_garbage":
        data[base] = 0x01
    elif field in offsets:
        width = 1 if field == "type" else 8
        data[base + offsets[field]:base + offsets[field] + width] = value.to_bytes(
            width, "little")
    else:
        raise ValueError(f"unknown entry field {field!r}")
    return bytes(data)


def write_image(path: str | Path, entries: list, pad_blocks: int = 0) -> Path:
    path = Path(path)
    path.write_bytes(build(entries, pad_blocks))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create", help="build an image from a JSON manifest")
    make.add_argument("manifest", type=Path)
    make.add_argument("output", type=Path)
    make.add_argument("--pad-blocks", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.manifest.read_text(encoding="utf-8"))
        entries = []
        for item in spec.get("files", []):
            content = item.get("content")
            entries.append((item["path"], content.encode("utf-8") if content is not None else None))
        write_image(args.output, entries, args.pad_blocks)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"fs_image: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
