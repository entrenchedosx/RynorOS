"""Host validator for Stage 17a block-storage serial evidence.

Every numeric claim from the guest is recomputed from the test image file
itself (never trusted blindly); written-block expectations come from the
deterministic writeback formula shared with blk_image.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from blk_image import BLOCK, MAGIC, block_sum, block_wsum, read_block, writeback_pattern
import struct


@dataclass
class BlkEvidence:
    devices: int = 0
    test_id: int = -1
    blocks: int = 0
    reads: dict = field(default_factory=dict)
    bootsec: bool = False
    writeback: tuple | None = None
    neighbors: dict = field(default_factory=dict)
    verified: bool = False
    failures: list = field(default_factory=list)


_READ_RE = re.compile(rb"^\[BLK\] read blk=(\d+) sum=(\d+) wsum=(\d+)$")
_DISC_RE = re.compile(rb"^\[BLK\] devices=(\d+) test=(\d+) blocks=(\d+)$")
_WB_RE = re.compile(rb"^\[BLK\] writeback blk=(\d+) sum=(\d+) wsum=(\d+)$")
_NB_RE = re.compile(rb"^\[BLK\] neighbor blk=(\d+) sum=(\d+) wsum=(\d+)$")


def parse_serial(observed: bytes) -> BlkEvidence:
    evidence = BlkEvidence()
    for raw in observed.split(b"\r\n"):
        line = raw.strip()
        if line.startswith(b"[BLK] failure="):
            evidence.failures.append(line.decode("ascii", "replace"))
            continue
        match = _DISC_RE.match(line)
        if match:
            evidence.devices = int(match.group(1))
            evidence.test_id = int(match.group(2))
            evidence.blocks = int(match.group(3))
            continue
        match = _READ_RE.match(line)
        if match:
            evidence.reads[int(match.group(1))] = (int(match.group(2)), int(match.group(3)))
            continue
        if line == b"[BLK] bootsec aa55=1":
            evidence.bootsec = True
            continue
        match = _WB_RE.match(line)
        if match:
            evidence.writeback = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            continue
        match = _NB_RE.match(line)
        if match:
            evidence.neighbors[int(match.group(1))] = (int(match.group(2)), int(match.group(3)))
            continue
        if line == b"[BLK] storage verified":
            evidence.verified = True
    return evidence


def validate_blk_section(tail: bytes) -> list:
    """Structural check for the optional trailing block-evidence section.

    Empty (absent section, i.e. normal boots) is valid. Otherwise every
    line must be a well-formed [BLK] record run ending at `storage
    verified`, with no guest failure lines. Numeric truth against the
    image file is checked separately by validate(), not here.
    """
    if tail == b"":
        return []
    lines = tail.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines or not _DISC_RE.match(lines[0].strip()):
        return [f"unexpected output after shell section: {lines[0][:60]!r}" if lines
                else "unexpected output after shell section"]
    allowed = (_DISC_RE, _READ_RE, _WB_RE, _NB_RE)
    for line in lines[1:]:
        text = line.strip()
        if text == b"[BLK] bootsec aa55=1":
            continue
        if text == b"[BLK] storage verified":
            continue
        if text.startswith(b"[BLK] failure="):
            return [f"guest failure: {text.decode('ascii', 'replace')}"]
        if text.startswith(b"[BLK] ") and any(rx.match(text) for rx in allowed):
            continue
        return [f"unexpected output after shell section: {text[:60]!r}"]
    if lines[-1].strip() != b"[BLK] storage verified":
        return ["block-evidence section missing storage verified"]
    return []


def file_block_sums(image: Path, block_no: int) -> tuple:
    data = read_block(image, block_no)
    return block_sum(data), block_wsum(data)


def validate(evidence: BlkEvidence, image: Path) -> list:
    """Recompute every guest claim from the image file. [] means valid."""
    errors = []
    if evidence.failures:
        errors.append(f"guest failures: {evidence.failures}")
    if not evidence.verified:
        errors.append("missing [BLK] storage verified")
    if evidence.devices < 2:
        errors.append(f"expected boot + test devices, got {evidence.devices}")
    try:
        first = read_block(image, 0)
    except (OSError, ValueError) as error:
        return errors + [f"unreadable image: {error}"]
    if first[:8] != MAGIC:
        errors.append("test image lacks RLBLK1 magic")
        return errors
    (blksz,) = struct.unpack("<I", first[8:12])
    (nblocks,) = struct.unpack("<Q", first[12:20])
    if blksz != BLOCK:
        errors.append(f"header block size {blksz}")
    if evidence.blocks != nblocks:
        errors.append(f"capacity {evidence.blocks} != header {nblocks}")
    for blk in (0, 1, 2, nblocks - 1):
        want = file_block_sums(image, blk)
        got = evidence.reads.get(blk)
        if got is None:
            errors.append(f"missing read evidence for block {blk}")
        elif tuple(got) != want:
            errors.append(f"block {blk} sums {got} != file {want}")
    if not evidence.bootsec:
        errors.append("missing boot-sector cross-device evidence")
    if evidence.writeback is None:
        errors.append("missing writeback evidence")
    else:
        wblk, wsum, wwsum = evidence.writeback
        want_wb = (block_sum(writeback_pattern(wblk)), block_wsum(writeback_pattern(wblk)))
        if (wsum, wwsum) != want_wb:
            errors.append(f"writeback sums {(wsum, wwsum)} != pattern {want_wb}")
        for neighbor in (wblk - 1, wblk + 1):
            got = evidence.neighbors.get(neighbor)
            if got is None:
                errors.append(f"missing neighbor evidence for block {neighbor}")
            elif tuple(got) != file_block_sums(image, neighbor):
                errors.append(f"neighbor {neighbor} sums {got} changed")
    return errors
