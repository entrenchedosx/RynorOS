"""Host-side parser/validator for the kernel [DMA] self-test section.

Also carries the independent first-fit scan model: model_scan()
reimplements the pmm_scan_run contract in Python so the integration
suite corroborates every synthetic scase row instead of trusting the
guest's own verdict.
"""
import re
from dataclasses import dataclass, field

_DMA_SCASE_RE = re.compile(
    rb"^\[DMA\] scase i=(\d+) n=(\d+) align=([0-9a-f]+) lim=([0-9a-f]+) "
    rb"b0=([0-9a-f]+) p0=(\d+) b1=([0-9a-f]+) p1=(\d+) bits=([0-9a-f]+) "
    rb"found=(\d+) start=([0-9a-f]+)$")
_DMA_SCAN_OK_RE = re.compile(rb"^\[DMA\] scan ok cases=(\d+)$")
_DMA_VALIDATION_RE = re.compile(rb"^\[DMA\] validation ok$")
_DMA_ALLOC_RE = re.compile(
    rb"^\[DMA\] alloc id=(\d+) virt=([0-9a-f]+) phys=([0-9a-f]+) "
    rb"bus=([0-9a-f]+) size=([0-9a-f]+) alloc=([0-9a-f]+) "
    rb"align=([0-9a-f]+)$")
_DMA_FREE_RE = re.compile(rb"^\[DMA\] free id=(\d+) ok=1$")
_DMA_FREE_DOUBLE_RE = re.compile(rb"^\[DMA\] free ok=1 double=detected$")
_DMA_PATTERN_RE = re.compile(rb"^\[DMA\] pattern ok pages=(\d+)$")
_DMA_CROSSPAGE_RE = re.compile(rb"^\[DMA\] crosspage ok$")
_DMA_REUSE_RE = re.compile(rb"^\[DMA\] reuse ok phys=([0-9a-f]+)$")
_DMA_ROLLBACK_RE = re.compile(rb"^\[DMA\] rollback ok$")
_DMA_DMA32_RE = re.compile(rb"^\[DMA\] dma32 ok phys=([0-9a-f]+)$")
_DMA_OOM_RE = re.compile(rb"^\[DMA\] oom ok$")
_DMA_BUSY_RE = re.compile(rb"^\[DMA\] busy ok$")
_DMA_GUARD_RE = re.compile(rb"^\[DMA\] guard ok$")
_DMA_SYNC_RE = re.compile(rb"^\[DMA\] sync ok$")
_DMA_VERIFIED_RE = re.compile(rb"^\[DMA\] dma verified$")
_DMA_FAILURE_RE = re.compile(rb"^\[DMA\] failure=(.+)$")

_DMA_ALLOWED = (_DMA_SCASE_RE, _DMA_SCAN_OK_RE, _DMA_VALIDATION_RE,
                _DMA_ALLOC_RE, _DMA_FREE_RE, _DMA_FREE_DOUBLE_RE,
                _DMA_PATTERN_RE, _DMA_CROSSPAGE_RE, _DMA_REUSE_RE,
                _DMA_ROLLBACK_RE, _DMA_DMA32_RE, _DMA_OOM_RE,
                _DMA_BUSY_RE, _DMA_GUARD_RE, _DMA_SYNC_RE,
                _DMA_VERIFIED_RE)


@dataclass
class DmaScase:
    index: int
    n: int
    align: int
    limit: int
    b0: int
    p0: int
    b1: int
    p1: int
    bits: int
    found: int
    start: int


@dataclass
class DmaAlloc:
    id: int
    virt: int
    phys: int
    bus: int
    size: int
    alloc: int
    align: int


@dataclass
class DmaEvidence:
    scases: list = field(default_factory=list)
    scan_cases: int | None = None
    validation_ok: bool = False
    allocs: list = field(default_factory=list)
    frees: list = field(default_factory=list)
    double_free: bool = False
    pattern_pages: int | None = None
    crosspage_ok: bool = False
    reuses: list = field(default_factory=list)
    rollback_ok: bool = False
    dma32: int | None = None
    oom_ok: bool = False
    busy_ok: bool = False
    guard_ok: bool = False
    sync_ok: bool = False
    verified: bool = False
    failures: list = field(default_factory=list)


def _int(value: bytes) -> int:
    return int(value.decode("ascii"))


def _hex(value: bytes) -> int:
    return int(value.decode("ascii"), 16)


def parse_dma_section(out: bytes) -> DmaEvidence:
    evidence = DmaEvidence()
    for line in out.split(b"\r\n"):
        text = line.strip()
        if not text.startswith(b"[DMA]"):
            continue
        match = _DMA_FAILURE_RE.match(text)
        if match:
            evidence.failures.append(match.group(1).decode("ascii", "replace"))
            continue
        match = _DMA_SCASE_RE.match(text)
        if match:
            fields = match.groups()
            evidence.scases.append(DmaScase(
                _int(fields[0]), _int(fields[1]), _hex(fields[2]),
                _hex(fields[3]), _hex(fields[4]), _int(fields[5]),
                _hex(fields[6]), _int(fields[7]), _hex(fields[8]),
                _int(fields[9]), _hex(fields[10])))
            continue
        match = _DMA_SCAN_OK_RE.match(text)
        if match:
            evidence.scan_cases = _int(match.group(1))
            continue
        if _DMA_VALIDATION_RE.match(text):
            evidence.validation_ok = True
            continue
        match = _DMA_ALLOC_RE.match(text)
        if match:
            fields = match.groups()
            evidence.allocs.append(DmaAlloc(
                _int(fields[0]), _hex(fields[1]), _hex(fields[2]),
                _hex(fields[3]), _hex(fields[4]), _hex(fields[5]),
                _hex(fields[6])))
            continue
        match = _DMA_FREE_RE.match(text)
        if match:
            evidence.frees.append(_int(match.group(1)))
            continue
        if _DMA_FREE_DOUBLE_RE.match(text):
            evidence.double_free = True
            continue
        match = _DMA_PATTERN_RE.match(text)
        if match:
            evidence.pattern_pages = _int(match.group(1))
            continue
        if _DMA_CROSSPAGE_RE.match(text):
            evidence.crosspage_ok = True
            continue
        match = _DMA_REUSE_RE.match(text)
        if match:
            evidence.reuses.append(_hex(match.group(1)))
            continue
        if _DMA_ROLLBACK_RE.match(text):
            evidence.rollback_ok = True
            continue
        match = _DMA_DMA32_RE.match(text)
        if match:
            evidence.dma32 = _hex(match.group(1))
            continue
        if _DMA_OOM_RE.match(text):
            evidence.oom_ok = True
            continue
        if _DMA_BUSY_RE.match(text):
            evidence.busy_ok = True
            continue
        if _DMA_GUARD_RE.match(text):
            evidence.guard_ok = True
            continue
        if _DMA_SYNC_RE.match(text):
            evidence.sync_ok = True
            continue
        if _DMA_VERIFIED_RE.match(text):
            evidence.verified = True
    return evidence


def validate_dma_section(out: bytes) -> list:
    lines = [line.strip() for line in out.split(b"\r\n")
             if line.strip().startswith(b"[DMA]")]
    if not lines:
        return ["DMA section missing"]
    for text in lines:
        if _DMA_FAILURE_RE.match(text):
            return [f"guest failure: {text.decode('ascii', 'replace')}"]
        if any(rx.match(text) for rx in _DMA_ALLOWED):
            continue
        return [f"unexpected DMA output: {text[:80]!r}"]
    if lines[-1] != b"[DMA] dma verified":
        return ["DMA section missing dma verified"]
    return []


def model_scan(spans, bitmask: int, n: int, align: int, limit: int):
    """Independent first-fit model of pmm_scan_run.

    spans: [(base, pages)] usable regions in compact order.
    bitmask: allocated bits over the concatenated frames.
    Returns (found, start_index); start is None when not found.
    """
    if n <= 0 or align <= 0 or align & (align - 1):
        return False, None
    frames = sum(pages for _, pages in spans)
    if frames <= 0 or n > frames:
        return False, None
    step = 1 if align <= 0x1000 else align // 0x1000
    offset = 0
    for base, pages in spans:
        if pages <= 0 or base % 0x1000:
            return False, None
        span = pages
        if limit:
            if limit <= base:
                offset += pages
                continue
            span = (min(limit, base + pages * 0x1000) - base) // 0x1000
        if span >= n:
            first = 0
            if step > 1:
                miss = base & (align - 1)
                if miss:
                    first = (align - miss) // 0x1000
            for k in range(first, span - n + 1, step):
                if all(not (bitmask >> (offset + k + j)) & 1
                       for j in range(n)):
                    return True, offset + k
        offset += pages
    return False, None
