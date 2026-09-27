"""Host-side parser/validator for the kernel [PCI] self-test section."""
import re
from dataclasses import dataclass, field

_PCI_TRANSPORT_RE = re.compile(rb"^\[PCI\] transport ok$")
_PCI_SYNTHETIC_RE = re.compile(rb"^\[PCI\] synthetic ok devices=(\d+) buses=(\d+)$")
_PCI_RESCAN_RE = re.compile(rb"^\[PCI\] rescan ok$")
_PCI_DEV_RE = re.compile(
    rb"^\[PCI\] dev bus=(\d+) dev=(\d+) fn=(\d+) vendor=([0-9a-f]+) "
    rb"device=([0-9a-f]+) class=([0-9a-f]+) sub=([0-9a-f]+) prog=([0-9a-f]+) "
    rb"rev=([0-9a-f]+) hdr=([0-9a-f]+) mf=(\d+) cmd=([0-9a-f]+) "
    rb"status=([0-9a-f]+) irql=([0-9a-f]+) irqp=([0-9a-f]+) nbar=(\d+)$")
_PCI_BAR_RE = re.compile(
    rb"^\[PCI\] bar bus=(\d+) dev=(\d+) fn=(\d+) index=(\d+) "
    rb"kind=(io|mmio32|mmio64) base=([0-9a-f]+) size=([0-9a-f]+) "
    rb"prefetch=(\d+) rawhi=([0-9a-f]+)$")
_PCI_MAP_RE = re.compile(
    rb"^\[PCI\] map bus=(\d+) dev=(\d+) fn=(\d+) entry=(\d+) "
    rb"va=([0-9a-f]+) pages=(\d+) xcheck=(\d+)$")
_PCI_RESTORE_RE = re.compile(rb"^\[PCI\] restore bus=(\d+) dev=(\d+) fn=(\d+) ok=1$")
_PCI_LIVE_RE = re.compile(rb"^\[PCI\] live ok devices=(\d+)$")
_PCI_STATS_RE = re.compile(
    rb"^\[PCI\] stats reads=(\d+) writes=(\d+) buses=(\d+) functions=(\d+) "
    rb"bars=(\d+) registry=(\d+)$")
_PCI_VERIFIED_RE = re.compile(rb"^\[PCI\] pci verified$")
_PCI_FAILURE_RE = re.compile(rb"^\[PCI\] failure=(.+)$")

_PCI_ALLOWED = (_PCI_TRANSPORT_RE, _PCI_SYNTHETIC_RE, _PCI_RESCAN_RE,
                _PCI_DEV_RE, _PCI_BAR_RE, _PCI_MAP_RE, _PCI_RESTORE_RE,
                _PCI_LIVE_RE, _PCI_STATS_RE, _PCI_VERIFIED_RE)


@dataclass
class PciDevice:
    bus: int
    dev: int
    fn: int
    vendor: int
    device: int
    class_code: int
    subclass: int
    prog_if: int
    revision: int
    header: int
    multifunction: int
    command: int
    status: int
    irq_line: int
    irq_pin: int
    nbar: int


@dataclass
class PciBar:
    bus: int
    dev: int
    fn: int
    index: int
    kind: str
    base: int
    size: int
    prefetch: int
    rawhi: int


@dataclass
class PciMap:
    bus: int
    dev: int
    fn: int
    entry: int
    va: int
    pages: int
    xcheck: int


@dataclass
class PciEvidence:
    transport_ok: bool = False
    synthetic: tuple | None = None
    rescan_ok: bool = False
    devs: list = field(default_factory=list)
    bars: list = field(default_factory=list)
    maps: list = field(default_factory=list)
    restores: list = field(default_factory=list)
    live_devices: int | None = None
    stats: dict = field(default_factory=dict)
    verified: bool = False
    failures: list = field(default_factory=list)


def _int(value: bytes) -> int:
    return int(value.decode("ascii"))


def _hex(value: bytes) -> int:
    return int(value.decode("ascii"), 16)


def parse_pci_section(out: bytes) -> PciEvidence:
    evidence = PciEvidence()
    for line in out.split(b"\r\n"):
        text = line.strip()
        if not text.startswith(b"[PCI]"):
            continue
        match = _PCI_FAILURE_RE.match(text)
        if match:
            evidence.failures.append(match.group(1).decode("ascii", "replace"))
            continue
        if _PCI_TRANSPORT_RE.match(text):
            evidence.transport_ok = True
            continue
        match = _PCI_SYNTHETIC_RE.match(text)
        if match:
            evidence.synthetic = (_int(match.group(1)), _int(match.group(2)))
            continue
        if _PCI_RESCAN_RE.match(text):
            evidence.rescan_ok = True
            continue
        match = _PCI_DEV_RE.match(text)
        if match:
            fields = match.groups()
            evidence.devs.append(PciDevice(
                _int(fields[0]), _int(fields[1]), _int(fields[2]),
                _hex(fields[3]), _hex(fields[4]), _hex(fields[5]),
                _hex(fields[6]), _hex(fields[7]), _hex(fields[8]),
                _hex(fields[9]), _int(fields[10]), _hex(fields[11]),
                _hex(fields[12]), _hex(fields[13]), _hex(fields[14]),
                _int(fields[15])))
            continue
        match = _PCI_BAR_RE.match(text)
        if match:
            fields = match.groups()
            evidence.bars.append(PciBar(
                _int(fields[0]), _int(fields[1]), _int(fields[2]),
                _int(fields[3]), fields[4].decode("ascii"),
                _hex(fields[5]), _hex(fields[6]), _int(fields[7]),
                _hex(fields[8])))
            continue
        match = _PCI_MAP_RE.match(text)
        if match:
            fields = match.groups()
            evidence.maps.append(PciMap(
                _int(fields[0]), _int(fields[1]), _int(fields[2]),
                _int(fields[3]), _hex(fields[4]), _int(fields[5]),
                _int(fields[6])))
            continue
        match = _PCI_RESTORE_RE.match(text)
        if match:
            evidence.restores.append(tuple(_int(g) for g in match.groups()))
            continue
        match = _PCI_LIVE_RE.match(text)
        if match:
            evidence.live_devices = _int(match.group(1))
            continue
        match = _PCI_STATS_RE.match(text)
        if match:
            keys = ("reads", "writes", "buses", "functions", "bars",
                    "registry")
            evidence.stats = {key: _int(group)
                              for key, group in zip(keys, match.groups())}
            continue
        if _PCI_VERIFIED_RE.match(text):
            evidence.verified = True
    return evidence


def validate_pci_section(out: bytes) -> list:
    lines = [line.strip() for line in out.split(b"\r\n")
             if line.strip().startswith(b"[PCI]")]
    if not lines:
        return ["PCI section missing"]
    for text in lines:
        if _PCI_FAILURE_RE.match(text):
            return [f"guest failure: {text.decode('ascii', 'replace')}"]
        if any(rx.match(text) for rx in _PCI_ALLOWED):
            continue
        return [f"unexpected PCI output: {text[:80]!r}"]
    if lines[-1] != b"[PCI] pci verified":
        return ["PCI section missing pci verified"]
    return []
