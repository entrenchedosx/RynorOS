"""Validate actual PMM serial records against the firmware map they report."""

import re

PAGE = 4096
PMM_END = b"[TEST] PMM self-test passed\r\n"


def firmware_regions(raw, bits):
    """Independent bounded interval model; never supplies guest RAM values."""
    if not 32 <= bits <= 52:
        raise ValueError("PMM physical address width invalid")
    spans = []
    for base, length, kind, attributes, size in raw:
        if size not in (20, 24):
            raise ValueError("PMM firmware record size invalid")
        if not length or (size == 24 and not attributes & 1):
            continue
        end = base + length
        if base >= 1 << bits or end > 1 << bits:
            raise ValueError("PMM firmware range outside physical limits")
        low, high = base // PAGE * PAGE, (end + PAGE - 1) // PAGE * PAGE
        if kind not in (1, 3, 4, 5, 7) or (size == 24 and attributes & ~1):
            kind = 2
        if kind != 1:
            spans.append((low, high, kind))
            continue
        start, finish = (base + PAGE - 1) // PAGE * PAGE, end // PAGE * PAGE
        if start >= finish:
            spans.append((low, high, 2))
        else:
            spans.extend((a, b, k) for a, b, k in
                         ((low, start, 2), (start, finish, 1), (finish, high, 2)) if a < b)
    points = sorted({value for a, b, _ in spans for value in (a, b)})
    priority = {1: 1, 3: 2, 7: 3, 4: 4, 2: 5, 5: 6}
    result = []
    for start, end in zip(points, points[1:]):
        kinds = [kind for a, b, kind in spans if a <= start and end <= b]
        if not kinds:
            continue
        kind = max(kinds, key=priority.__getitem__)
        if result and result[-1][1] == start and result[-1][2] == kind:
            result[-1] = (result[-1][0], end, kind)
        else:
            result.append((start, end, kind))
    return result


def reserve(regions, start, end, kind):
    result = []
    for a, b, k in regions:
        if k != 1 or b <= start or a >= end:
            result.append((a, b, k))
        else:
            low, high = max(a, start), min(b, end)
            result.extend((x, y, z) for x, y, z in
                          ((a, low, 1), (low, high, kind), (high, b, 1)) if x < y)
    return result


def parse_pmm_output(output: bytes) -> dict:
    if len(output) > 100000:
        raise ValueError("PMM output exceeds bounded record budget")
    try:
        text = output.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("PMM output is not ASCII") from error
    if not text.endswith("\r\n"):
        raise ValueError("PMM output incomplete")
    lines = iter(text[:-2].split("\r\n"))

    def record(pattern):
        line = next(lines, "")
        match = re.fullmatch(pattern, line)
        if not match:
            raise ValueError(f"PMM missing/malformed record: expected {pattern!r}, got {line!r}")
        values = tuple(int(v) for v in match.groups())
        if any(v >= 1 << 64 for v in values):
            raise ValueError("PMM integer overflow")
        return values

    def literal(value):
        record(re.escape(value))

    def scalar(name):
        return record(re.escape(name) + r"(0|[1-9][0-9]{0,19})")[0]

    n = r"(0|[1-9][0-9]{0,19})"
    literal("[MM] firmware memory map acquired")
    entries = scalar("[MM] entries=")
    bits = scalar("[MM] physical_bits=")
    if not 1 <= entries <= 64:
        raise ValueError("PMM entry count invalid")
    raw = [record(r"\[MM\] raw base=" + n + " length=" + n + " type=" + n +
                  " attributes=" + n + " size=" + n) for _ in range(entries)]
    count = scalar("[MM] regions=")
    if not 1 <= count <= 392:
        raise ValueError("PMM region count invalid")
    regions = [record(r"\[MM\] region base=" + n + " end=" + n + " kind=" + n) for _ in range(count)]
    fields = ("firmware_usable_bytes", "described_bytes", "usable_bytes", "reserved_bytes",
              "free_bytes", "allocated_bytes")
    stats = {name: scalar("[MM] " + name + "=") for name in fields}
    metadata, metadata_bytes = record(r"\[MM\] metadata base=" + n + " bytes=" + n)
    literal("[MM] allocator initialized")
    literal("[TEST] PMM self-test started")
    literal("[TEST] PMM map validation passed")
    literal("[TEST] PMM reservations verified")
    frames = [scalar("[TEST] PMM allocated frame=") for _ in range(8)]
    literal("[TEST] PMM physical RAM write verified")
    reused = scalar("[TEST] PMM reused frame=")
    exhausted, last = record(r"\[TEST\] PMM exhausted frames=" + n + " last=" + n)
    final_free, final_allocated = record(r"\[MM\] final free_bytes=" + n + " allocated_bytes=" + n)
    literal("[TEST] PMM self-test passed")
    if next(lines, None) is not None:
        raise ValueError("PMM unexpected extra records")
    firmware = firmware_regions(raw, bits)
    firmware_usable = sum(b - a for a, b, kind in firmware if kind == 1)
    boot_reserved = reserve(firmware, 0, 0x100000, 8)
    candidates = sum((b - a) // PAGE for a, b, kind in boot_reserved if kind == 1)
    expected_bytes = ((candidates + 7) // 8 + PAGE - 1) // PAGE * PAGE
    locations = [a for a, b, kind in boot_reserved
                 if kind == 1 and a + expected_bytes <= min(b, 0x200000)]
    if not locations or metadata != locations[0] or metadata_bytes != expected_bytes or not expected_bytes:
        raise ValueError("PMM bitmap is not correctly placed/sized in discovered mapped RAM")
    expected = reserve(boot_reserved, metadata, metadata + metadata_bytes, 9)
    if regions != expected:
        raise ValueError("PMM normalized/reserved regions disagree with firmware map")
    usable = sum(b - a for a, b, kind in expected if kind == 1)
    described = sum(b - a for a, b, _ in expected)
    expected_stats = dict(firmware_usable_bytes=firmware_usable, usable_bytes=usable,
                          described_bytes=described, reserved_bytes=described - usable,
                          free_bytes=usable, allocated_bytes=0)
    if stats != expected_stats or (final_free, final_allocated) != (usable, 0):
        raise ValueError("PMM accounting inconsistent with discovered regions")
    if frames != sorted(set(frames)) or reused != frames[0]:
        raise ValueError("PMM allocation uniqueness/reuse failed")
    for frame in frames:
        if frame % PAGE or not any(kind == 1 and a <= frame and frame + PAGE <= b for a, b, kind in expected):
            raise ValueError("PMM allocated an unavailable/reserved/unaligned frame")
    if exhausted != usable // PAGE or last != max(b - PAGE for a, b, kind in expected if kind == 1):
        raise ValueError("PMM did not exhaust the actual discovered pool")
    return {**stats, "metadata_base": metadata, "metadata_bytes": metadata_bytes,
            "raw": raw, "regions": regions, "frames": frames, "exhausted_frames": exhausted,
            "last_frame": last, "physical_bits": bits}


def validate_pmm_output(output: bytes) -> list[str]:
    try:
        parse_pmm_output(output)
    except ValueError as error:
        return [str(error)]
    return []
