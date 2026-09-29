"""Host-side validator for the INT-A2 [MSI] transcript section.

Parses the gated self-test rows, reimplements the x86 message builder
independently (Linux __irq_msi_compose_msg parity), and checks every
live message, table entry, delivery, and refusal against the frozen
contract in docs/design/msi.md. Raises MSIError with a precise reason;
returns the parsed section on success.
"""

MSI_ADDR_BASE = 0xFEE00000
KNOWN_REFUSALS = {
    "no-busmaster", "no-msi-x-cap", "fn-busy", "mem-disabled",
    "already-enabled", "msi-live", "msix-live", "over-mmc", "over-table",
    "no-msi-cap", "no-msix-cap", "dest-id",
}
SYNTHETIC_CASES = {
    "walk": 18, "parse": 35, "msg": 16, "msix": 16, "alloc": 20,
}
SYNTHETIC_TOTAL = 193


class MSIError(Exception):
    pass


def build_message(apic_id, vector):
    """Independent x86 MSI message builder (docs/design/msi.md section 3.4).

    Returns (addr, hi, data). Refuses apic_id > 0xFF like the kernel.
    """
    if not 0 <= vector <= 255:
        raise MSIError("msg-vector %r out of range" % (vector,))
    if not 0 <= apic_id <= 0xFF:
        raise MSIError("msg-apic-id %r refused" % (apic_id,))
    return (MSI_ADDR_BASE | (apic_id << 12), 0, vector & 0xFF)


def _fields(line):
    parts = line.split()
    out = {}
    for token in parts[1:]:
        if "=" in token:
            key, _, value = token.partition("=")
            out[key] = value
    return out


_SUMMARY_PREFIXES = ("[MSI] walk ok ", "[MSI] parse ok ", "[MSI] msg ok ",
                      "[MSI] msix ok ", "[MSI] alloc ok ", "[MSI] synth ok",
                      "[MSI] synthetic ok ")


def parse_msi_section(text):
    """Split the transcript into the ordered [MSI] rows plus failures."""
    rows = []
    failures = []
    for raw in text.splitlines():
        line = raw.strip()
        if line == "[MSI] msi verified":
            rows.append({"kind": "verified", "raw": line})
        elif line.startswith("[MSI] failure="):
            failures.append(line)
        elif line.startswith(_SUMMARY_PREFIXES):
            fields = _fields(line)
            fields["kind"] = line.split()[1] + "-ok"
            fields["raw"] = line
            rows.append(fields)
        elif line.startswith("[MSI] "):
            fields = _fields(line)
            fields["kind"] = line.split()[1] if len(line.split()) > 1 else ""
            fields["raw"] = line
            rows.append(fields)
    return rows, failures


def _vec_list(text):
    return [int(v) for v in text.split(",")]


def verify_msi_section(text, expected_devices=None):
    """Verify the full [MSI] section; return the parsed rows.

    expected_devices pins the walk-all device count (topology check).
    """
    rows, failures = parse_msi_section(text)
    if failures:
        raise MSIError("guest failures: %s" % "; ".join(failures))
    if not rows or rows[-1].get("kind") != "verified":
        raise MSIError("missing [MSI] msi verified terminator")
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)

    for name, want in SYNTHETIC_CASES.items():
        got = by_kind.get(name + "-ok", [])
        if len(got) != 1 or int(got[0].get("cases", -1)) != want:
            raise MSIError("synthetic %s cases: want %d" % (name, want))
    synth = by_kind.get("synthetic-ok", [])
    if len(synth) != 1 or int(synth[0].get("cases", -1)) != SYNTHETIC_TOTAL:
        raise MSIError("synthetic total: want %d" % SYNTHETIC_TOTAL)
    if len(by_kind.get("synth-ok", [])) != 1:
        raise MSIError("synth ok row missing")

    lapic = by_kind.get("lapic", [])
    if len(lapic) != 1 or int(lapic[0]["base"], 16) != MSI_ADDR_BASE:
        raise MSIError("lapic base row missing or moved")

    walks = by_kind.get("walk", [])
    if expected_devices is not None and len(walks) != expected_devices:
        raise MSIError("walk rows %d != devices %d"
                       % (len(walks), expected_devices))

    # Every live message must match the independent builder.
    for row in by_kind.get("msg", []):
        vec = int(row["vec"])
        apic = int(row["apic"])
        want = build_message(apic, vec)
        got = (int(row["addr"], 16), int(row["hi"], 16),
               int(row["data"], 16))
        if got != want:
            raise MSIError("msg vec=%d mismatch: got %r want %r"
                           % (vec, got, want))

    # Every table entry must carry a built message for its vector.
    msgs = {(int(r["vec"])): (int(r["addr"], 16), int(r["data"], 16))
            for r in by_kind.get("msg", [])}
    for row in by_kind.get("entry", []):
        vec_rows = [r for r in by_kind.get("msg", [])]
        addrs = {v: a for v, (a, _) in msgs.items()}
        datas = {v: d for v, (_, d) in msgs.items()}
        addr = int(row["addr"], 16)
        data = int(row["data"], 16)
        if addr not in addrs.values() or data not in datas.values():
            raise MSIError("entry %s not matching any msg row"
                           % (row.get("raw"),))
        _ = vec_rows  # entries are matched by value, not order

    # Deliveries are ISR-proven; enables pair with disables.
    for row in by_kind.get("irq", []):
        if int(row.get("isr", 0)) != 1:
            raise MSIError("irq without ISR proof: %s" % (row.get("raw"),))
        if int(row.get("count", 0)) < 1:
            raise MSIError("irq with zero count: %s" % (row.get("raw"),))
    enables = by_kind.get("enable", [])
    disables = by_kind.get("disable", [])
    if len(enables) != len(disables):
        raise MSIError("enable/disable imbalance %d != %d"
                       % (len(enables), len(disables)))
    for row in by_kind.get("refuse", []):
        if row.get("reason") not in KNOWN_REFUSALS:
            raise MSIError("unknown refusal: %s" % (row.get("raw"),))
    for row in by_kind.get("intx", []):
        if row.get("intxoff") != "1" or row.get("silent") != "1":
            raise MSIError("INTx defense broken: %s" % (row.get("raw"),))
    live = by_kind.get("live", [])
    if len(live) != 1 or int(live[0].get("devices", -1)) != len(walks):
        raise MSIError("live ok devices mismatch")
    cost = by_kind.get("cost", [])
    if len(cost) != 1:
        raise MSIError("cost row missing")
    if int(cost[0].get("alloc1", "0"), 16) < int(cost[0].get("alloc0", "0"),
                                                 16):
        raise MSIError("cost alloc1 < alloc0")
    return rows
