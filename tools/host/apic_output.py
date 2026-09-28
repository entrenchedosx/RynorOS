"""Strict ACPI/APIC/IRQ evidence parser for the INT-A1 self-test section.

The guest section runs from APIC_START to APIC_VERIFIED between the
post-IRQ accounting line and the Stage 11 shell banner. Values pinned
here are facts about the pinned test machine (QEMU pc-i440fx-10.0,
qemu64 CPU, hashed SeaBIOS): RSDP revision, RSDT entry count, MADT
topology, LAPIC version, IOAPIC pin count, route programming, and the
exact proof counts. A firmware or QEMU change that moves any of them
must update these pins deliberately, never silently.
"""
import re

APIC_START = b"[APIC] self-test started\r\n"
APIC_VERIFIED = b"[APIC] apic verified\r\n"

# Synthetic case counts are code facts: adding or removing a fixture
# case must update the pin in the same change.
SYNTHETIC_ACPI = 43
SYNTHETIC_APIC = 120

# Pinned test-machine firmware topology (SeaBIOS on pc-i440fx-10.0).
RSDP_REV = 0
RSDP_XSDT = 0
RSDT_ENTRIES = 4
MADT_LAPIC_BASE = 4276092928  # 0xFEE00000
MADT_FLAGS = 1
MADT_CPUS = 1
MADT_IOAPICS = 1
MADT_ISOS = 5
MADT_NMIS = 1
CPU_ROWS = ((0, 0, 1),)  # (uid, apic_id, flags)
IOAPIC_ROWS = ((0, 4273995776, 0),)  # (id, base, gsi_base); base is 0xFEC00000
ISO_ROWS = (  # (bus, irq, gsi, flags) in MADT order
    (0, 0, 2, 0),
    (0, 5, 5, 13),
    (0, 9, 9, 13),
    (0, 10, 10, 13),
    (0, 11, 11, 13),
)

# Pinned test-machine hardware measurements (qemu64 LAPIC, i440fx IOAPIC).
LAPIC_ID = 0
LAPIC_VERSION = 20  # 0x14
LAPIC_MAXLVT = 5
IOAPIC_MAXREDIR = 23  # 24 pins, GSI 0-23

# Pinned route programming: IRQ0 via the MADT override to GSI 2, IRQ1
# identity, both on legacy vectors, parked masked at print time.
ROUTE_ROWS = (  # (irq, gsi, vector, trigger, polarity, ioapic, masked)
    (0, 2, 32, "edge", "high", 0, 1),
    (1, 1, 33, "edge", "high", 0, 1),
)

PROVE_TICKS = 11  # 5 + masked-quiet + 6 more; exact, no duplicates
ECHO_BYTE = 0xEE
ECHO_VECTOR = 33
DYN_VECTOR = 48
DYN_GSI = 7
COST_FRAMES = 1
COST_TABLES = 1


def parse_apic_output(output: bytes) -> dict:
    if len(output) > 16384:
        raise ValueError("APIC output too large")
    if b"[APIC] unavailable" in output or b"[ACPI] failure=" in output or \
            b"[APIC] failure=" in output:
        reason = "unavailable"
        match = re.search(r"reason=([a-z0-9_]+)", output.decode("ascii", "replace"))
        if match:
            reason = match.group(1)
        raise ValueError("APIC unexpectedly unavailable on the test machine: " + reason)
    lines = iter(output.decode("ascii").splitlines(keepends=True))

    def exact(s):
        if next(lines, None) != s + "\r\n":
            raise ValueError("APIC missing/out-of-order line: " + s)

    def numbers(pattern):
        match = re.fullmatch(pattern + r"\r\n", next(lines, ""))
        if not match:
            raise ValueError("APIC invalid numeric record")
        values = tuple(int(n) for n in match.groups() if n is not None and n.isdigit())
        if any(n >= 1 << 64 for n in values):
            raise ValueError("APIC numeric overflow")
        return match

    exact("[APIC] self-test started")
    match = numbers(r"\[APIC\] synthetic acpi=(\d+) apic=(\d+)")
    if (int(match.group(1)), int(match.group(2))) != (SYNTHETIC_ACPI, SYNTHETIC_APIC):
        raise ValueError("APIC synthetic case count drifted")
    match = numbers(r"\[ACPI\] rsdp rev=(\d+) xsdt=(\d+) entries=(\d+)")
    if (int(match.group(1)), int(match.group(2)), int(match.group(3))) != \
            (RSDP_REV, RSDP_XSDT, RSDT_ENTRIES):
        raise ValueError("APIC RSDP/root facts disagree with pinned firmware")
    match = numbers(r"\[ACPI\] madt lapic=(\d+) flags=(\d+) cpus=(\d+) ioapics=(\d+) "
                    r"isos=(\d+) nmis=(\d+) skipped=(\d+) dups=(\d+)")
    got = tuple(int(match.group(i)) for i in range(1, 9))
    want = (MADT_LAPIC_BASE, MADT_FLAGS, MADT_CPUS, MADT_IOAPICS, MADT_ISOS,
            MADT_NMIS, 0, 0)
    if got != want:
        raise ValueError("APIC MADT summary disagrees with pinned firmware")
    for uid, apic_id, flags in CPU_ROWS:
        match = numbers(r"\[ACPI\] cpu uid=(\d+) apic=(\d+) flags=(\d+)")
        if (int(match.group(1)), int(match.group(2)), int(match.group(3))) != \
                (uid, apic_id, flags):
            raise ValueError("APIC CPU row disagrees with pinned firmware")
    for ident, base, gsi in IOAPIC_ROWS:
        match = numbers(r"\[ACPI\] ioapic id=(\d+) base=(\d+) gsi=(\d+)")
        if (int(match.group(1)), int(match.group(2)), int(match.group(3))) != \
                (ident, base, gsi):
            raise ValueError("APIC IOAPIC row disagrees with pinned firmware")
    for bus, irq, gsi, flags in ISO_ROWS:
        match = numbers(r"\[ACPI\] iso bus=(\d+) irq=(\d+) gsi=(\d+) flags=(\d+)")
        if (int(match.group(1)), int(match.group(2)), int(match.group(3)),
                int(match.group(4))) != (bus, irq, gsi, flags):
            raise ValueError("APIC override row disagrees with pinned firmware")
    match = numbers(r"\[APIC\] lapic id=(\d+) version=(\d+) maxlvt=(\d+)")
    if (int(match.group(1)), int(match.group(2)), int(match.group(3))) != \
            (LAPIC_ID, LAPIC_VERSION, LAPIC_MAXLVT):
        raise ValueError("APIC LAPIC identity disagrees with pinned CPU")
    for index in range(len(IOAPIC_ROWS)):
        match = numbers(r"\[APIC\] ioapic idx=(\d+) maxredir=(\d+)")
        if (int(match.group(1)), int(match.group(2))) != (index, IOAPIC_MAXREDIR):
            raise ValueError("APIC redirection maximum disagrees with pinned IOAPIC")
    for irq, gsi, vector, trigger, polarity, ioapic, masked in ROUTE_ROWS:
        match = numbers(r"\[IRQ\] route irq=(\d+) gsi=(\d+) vector=(\d+) "
                        r"trigger=(edge|level) polarity=(high|low) "
                        r"ioapic=(\d+) masked=(\d+)")
        got = (int(match.group(1)), int(match.group(2)), int(match.group(3)),
               match.group(4), match.group(5), int(match.group(6)),
               int(match.group(7)))
        if got != (irq, gsi, vector, trigger, polarity, ioapic, masked):
            raise ValueError("APIC route programming drifted")
    match = numbers(r"\[IRQ\] backend=apic imcr=(\d+) pic_masked=1")
    imcr = int(match.group(1))
    if imcr not in (0, 1):
        raise ValueError("APIC IMCR flag out of range")
    match = numbers(r"\[IRQ\] timer ticks=(\d+)")
    if int(match.group(1)) != PROVE_TICKS:
        raise ValueError("APIC timer proof count drifted")
    match = numbers(r"\[IRQ\] kbd base=(\d+) count=(\d+)")
    kbd_base, kbd_count = int(match.group(1)), int(match.group(2))
    # Silence is a delta (PIC-era IRQ1s are in the baseline), and the
    # baseline must be non-vacuous: the keyboard demonstrably fired
    # before the switch, so zero new deliveries is a real claim.
    if kbd_base != kbd_count or kbd_base < 1:
        raise ValueError("APIC keyboard quiet window violated")
    match = numbers(r"\[IRQ\] kbd echo byte=(\d+) vector=(\d+)")
    if (int(match.group(1)), int(match.group(2))) != (ECHO_BYTE, ECHO_VECTOR):
        raise ValueError("APIC keyboard echo proof drifted")
    counts = {}
    for _ in range(2):
        match = numbers(r"\[IRQ\] vector=(\d+) count=(\d+)")
        counts[int(match.group(1))] = int(match.group(2))
    if sorted(counts) != [32, 33]:
        raise ValueError("APIC vector census incomplete")
    if counts[33] != kbd_base + 1:
        raise ValueError("APIC keyboard vector census disagrees with echo proof")
    if counts[32] < PROVE_TICKS:
        raise ValueError("APIC timer vector census misses proof ticks")
    match = numbers(r"\[IRQ\] dyn vector=(\d+) gsi=(\d+) trigger=level "
                    r"polarity=low reuse=(\d+)")
    if (int(match.group(1)), int(match.group(2)), int(match.group(3))) != \
            (DYN_VECTOR, DYN_GSI, 1):
        raise ValueError("APIC dynamic-route exercise drifted")
    match = numbers(r"\[APIC\] cost frames=(\d+) tables=(\d+)")
    cost_frames, cost_tables = int(match.group(1)), int(match.group(2))
    if (cost_frames, cost_tables) != (COST_FRAMES, COST_TABLES):
        raise ValueError("APIC resource cost drifted")
    exact("[APIC] apic verified")
    if next(lines, None) is not None:
        raise ValueError("APIC unexpected trailing records")
    return dict(imcr=imcr, kbd_base=kbd_base, timer_counts=counts,
                frames=cost_frames, tables=cost_tables)


def validate_apic_output(output: bytes) -> list[str]:
    try:
        parse_apic_output(output)
    except (ValueError, UnicodeDecodeError) as error:
        return [str(error)]
    return []


def fixture() -> bytes:
    """Complete good section for transcript fixtures (mirrors the pinned
    test-machine boot; never presented as emulator execution)."""
    lines = [
        "[APIC] self-test started",
        f"[APIC] synthetic acpi={SYNTHETIC_ACPI} apic={SYNTHETIC_APIC}",
        f"[ACPI] rsdp rev={RSDP_REV} xsdt={RSDP_XSDT} entries={RSDT_ENTRIES}",
        f"[ACPI] madt lapic={MADT_LAPIC_BASE} flags={MADT_FLAGS} cpus={MADT_CPUS} "
        f"ioapics={MADT_IOAPICS} isos={MADT_ISOS} nmis={MADT_NMIS} skipped=0 dups=0",
    ]
    for uid, apic_id, flags in CPU_ROWS:
        lines.append(f"[ACPI] cpu uid={uid} apic={apic_id} flags={flags}")
    for ident, base, gsi in IOAPIC_ROWS:
        lines.append(f"[ACPI] ioapic id={ident} base={base} gsi={gsi}")
    for bus, irq, gsi, flags in ISO_ROWS:
        lines.append(f"[ACPI] iso bus={bus} irq={irq} gsi={gsi} flags={flags}")
    lines.append(f"[APIC] lapic id={LAPIC_ID} version={LAPIC_VERSION} maxlvt={LAPIC_MAXLVT}")
    for index in range(len(IOAPIC_ROWS)):
        lines.append(f"[APIC] ioapic idx={index} maxredir={IOAPIC_MAXREDIR}")
    for irq, gsi, vector, trigger, polarity, ioapic, masked in ROUTE_ROWS:
        lines.append(f"[IRQ] route irq={irq} gsi={gsi} vector={vector} "
                     f"trigger={trigger} polarity={polarity} ioapic={ioapic} masked={masked}")
    lines.append("[IRQ] backend=apic imcr=0 pic_masked=1")
    lines.append(f"[IRQ] timer ticks={PROVE_TICKS}")
    lines.append("[IRQ] kbd base=17 count=17")
    lines.append(f"[IRQ] kbd echo byte={ECHO_BYTE} vector={ECHO_VECTOR}")
    lines.append("[IRQ] vector=32 count=152")
    lines.append("[IRQ] vector=33 count=18")
    lines.append(f"[IRQ] dyn vector={DYN_VECTOR} gsi={DYN_GSI} trigger=level "
                 f"polarity=low reuse=1")
    lines.append(f"[APIC] cost frames={COST_FRAMES} tables={COST_TABLES}")
    lines.append("[APIC] apic verified")
    return ("\r\n".join(lines) + "\r\n").encode()


APIC_GOOD = fixture()


def extract_apic_section(block: bytes) -> bytes | None:
    """Slice APIC_START..APIC_VERIFIED line out of a post-IRQ tail."""
    if APIC_START not in block or APIC_VERIFIED not in block:
        return None
    start = block.index(APIC_START)
    end = block.index(APIC_VERIFIED) + len(APIC_VERIFIED)
    if block[end:end + 2] == b"\r\n":
        end += 2
    return block[start:end]


def strip_apic_section(block: bytes) -> tuple:
    """Validate the required APIC section and remove it from the block."""
    section = extract_apic_section(block)
    if section is None:
        if APIC_START in block:
            return block, ["apic section incomplete"]
        return block, ["APIC section missing"]
    errors = validate_apic_output(section)
    start = block.index(APIC_START)
    end = start + len(section)
    return block[:start] + block[end:], errors
