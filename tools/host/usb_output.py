"""Host-side validator for the USB-A1 [USB] transcript section.

Parses the gated self-test rows and re-derives the proof
independently: Supported Protocol ranges (disjoint, covering the
scanned ports), PORTSC snapshot consistency (CCS/PED/PLS/speed),
the 11-stage rollback order, the Address Device control word
(recomputed bit-exact: type 11, slot match, BSR=0 so the xHC owns
SET_ADDRESS), the output-context witness (slot Addressed, EP0
Running, MPS match), an independent re-parse of the 18 raw
descriptor bytes cross-checked against the parsed row, the EP0
transfer-ring model (10 TRBs, Link skip, TD-atomic advance,
wrap count), and zero net allocation. Raises USBError with a
precise reason; returns the parsed rows on success.

Production contract: docs/design/usb.md. QEMU specifics (port
number, slot id, DMA bases, VID/PID) are captured from the
transcript, never frozen here.
"""


class USBError(Exception):
    pass


SYNTH_CASES = 101
EP0_TRBS = 10
TRB_SIZE = 16
DESC_LEN = 18
ROLLBACK_STAGES = ("PROTO", "SCAN", "RESET", "ENSLOT", "DCTX",
                   "ICTX", "EP0RING", "ADDR", "DESC8", "EVAL",
                   "DESC18")
REPEAT_READS = 5
# MPS0 by negotiated speed id (xHCI default mapping; PSI-derived
# speeds land on the same USB legal values).
MPS_BY_SPEED = {1: 8, 2: 8, 3: 64, 4: 512}
SPEED_NAMES = {0: "??", 1: "FS", 2: "LS", 3: "HS", 4: "SS",
               5: "??", 6: "??", 7: "??", 8: "SSP"}
STATES = ("disconn", "conn", "reset", "enabled", "slot", "addr",
          "addressed", "ep0", "desc8", "done", "failed")


def _fields(line):
    parts = line.split()
    out = {}
    for token in parts[1:]:
        if "=" in token:
            key, _, value = token.partition("=")
            out[key] = value
        elif token in SPEED_NAMES.values() and "speed" not in out:
            out["speed"] = token
    if len(parts) > 2 and parts[1] == "port" and parts[2].isdigit():
        out["num"] = parts[2]
    return out


def _kind(line):
    parts = line.split()
    if len(parts) < 2:
        return ""
    if parts[1] == "live" and len(parts) > 2 and parts[2] == "ok":
        return "live"
    if parts[1] == "usb" and len(parts) > 2 and parts[2] == "verified":
        return "verified"
    if parts[1] == "synth" and len(parts) > 2 and parts[2] == "ok":
        return "synth"
    return parts[1]


def parse_usb_section(text):
    """Split the transcript into ordered [USB] rows plus failures."""
    rows = []
    failures = []
    for raw in text.splitlines():
        line = raw.strip()
        if "failure=" in line or "rejected frame" in line or \
                "panic" in line:
            failures.append(line)
        if line.startswith("[USB] failure="):
            continue
        if line.startswith("[USB] "):
            fields = _fields(line)
            fields["kind"] = _kind(line)
            fields["line"] = line
            rows.append(fields)
    return rows, failures


def _hex(value, what):
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        raise USBError("%s: bad hex %r" % (what, value))


def _dec(value, what):
    try:
        return int(value, 10)
    except (TypeError, ValueError):
        raise USBError("%s: bad decimal %r" % (what, value))


def _pop(rows, kind, what):
    if not rows or rows[0]["kind"] != kind:
        got = rows[0]["line"] if rows else "<end>"
        raise USBError("%s: expected %s row, got %r" % (what, kind, got))
    return rows.pop(0)


def _check_synth(rows):
    synth = _pop(rows, "synth", "synth")
    if _dec(synth.get("cases"), "synth cases") != SYNTH_CASES:
        raise USBError("synth cases %r" % (synth.get("line"),))


def _check_cost_zero(rows):
    cost = _pop(rows, "cost", "cost")
    if _hex(cost.get("alloc0"), "cost") != _hex(cost.get("alloc1"),
                                               "cost"):
        raise USBError("net allocation: %r" % (cost.get("line"),))


def _check_protos_ports(rows):
    """Parse proto + port inventory; return (protos, ports, connected)."""
    protos = []
    while rows and rows[0]["kind"] == "proto":
        row = rows.pop(0)
        try:
            major, _, minor = row.get("rev", "").partition(".")
            major, minor = int(major), int(minor)
            lo, _, hi = row.get("ports", "").partition("..")
            lo, hi = int(lo), int(hi)
            psi = int(row.get("psi", ""))
        except (TypeError, ValueError):
            raise USBError("proto row: %r" % (row["line"],))
        if major not in (2, 3) or minor not in (0, 1, 2):
            raise USBError("proto rev: %r" % (row["line"],))
        if lo < 1 or hi < lo or psi < 0 or psi > 8:
            raise USBError("proto range: %r" % (row["line"],))
        for (plo, phi) in protos:
            if lo <= phi and plo <= hi:
                raise USBError("proto overlap: %r" % (row["line"],))
        protos.append((lo, hi))
    if not protos:
        raise USBError("no proto rows")
    ports = []
    connected = []
    expect = 1
    while rows and rows[0]["kind"] == "port":
        row = rows.pop(0)
        num = _dec(row.get("num"), "port num")
        if num != expect:
            raise USBError("port order: %r want %d"
                           % (row["line"], expect))
        expect += 1
        proto = row.get("proto", "")
        owner = None
        for i, (plo, phi) in enumerate(protos):
            if plo <= num <= phi:
                owner = i
        if proto == "none":
            if owner is not None:
                raise USBError("port owner: %r" % (row["line"],))
        elif owner is None or _dec(proto, "port proto") != owner:
            raise USBError("port owner: %r" % (row["line"],))
        state = row.get("s", "")
        if state not in STATES:
            raise USBError("port state: %r" % (row["line"],))
        ccs = _dec(row.get("ccs"), "port ccs")
        ped = _dec(row.get("ped"), "port ped")
        pls = _dec(row.get("pls"), "port pls")
        spd = _dec(row.get("spd"), "port spd")
        if ccs not in (0, 1) or ped not in (0, 1):
            raise USBError("port ccs/ped: %r" % (row["line"],))
        if pls < 0 or pls > 15:
            raise USBError("port pls: %r" % (row["line"],))
        if spd < 0 or spd > 15:
            raise USBError("port spd: %r" % (row["line"],))
        if row.get("speed") != SPEED_NAMES.get(spd, "??"):
            raise USBError("port speed name: %r" % (row["line"],))
        if state == "disconn":
            if ccs or ped or spd:
                raise USBError("disconn with link: %r" % (row["line"],))
            if pls != 5:
                raise USBError("disconn pls: %r" % (row["line"],))
        elif state == "conn":
            if not ccs or ped:
                raise USBError("conn flags: %r" % (row["line"],))
            if spd not in MPS_BY_SPEED and spd < 8:
                raise USBError("conn speed: %r" % (row["line"],))
            connected.append(num)
        else:
            raise USBError("scan-time state: %r" % (row["line"],))
        ports.append(num)
    if not ports:
        raise USBError("no port rows")
    return protos, ports, connected


def verify_absent_section(text):
    """Validate the USB_ABSENT transcript: synth, absent, zero cost."""
    rows, failures = parse_usb_section(text)
    if failures:
        raise USBError("failures present: %r" % (failures[0],))
    kinds = [r["kind"] for r in rows]
    if kinds != ["synth", "absent", "cost", "live", "verified"]:
        raise USBError("absent section shape %r" % (kinds,))
    if _dec(rows[0].get("cases"), "synth cases") != SYNTH_CASES:
        raise USBError("synth cases %r" % (rows[0].get("line"),))
    if _hex(rows[2].get("alloc0"), "cost") != _hex(rows[2].get("alloc1"),
                                                  "cost"):
        raise USBError("absent cost not zero: %r" % (rows[2].get("line"),))
    if _dec(rows[3].get("devices"), "live devices") != 0:
        raise USBError("absent devices %r" % (rows[3].get("line"),))
    return rows


def verify_nodevice_section(text):
    """Validate the present-controller, no-device transcript."""
    rows, failures = parse_usb_section(text)
    if failures:
        raise USBError("failures present: %r" % (failures[0],))
    rows = list(rows)
    all_rows = list(rows)
    _check_synth(rows)
    _, _, connected = _check_protos_ports(rows)
    if connected:
        raise USBError("no-device with connected ports %r" % (connected,))
    nodev = _pop(rows, "no-device", "no-device")
    if nodev["line"] != "[USB] no-device":
        raise USBError("no-device row: %r" % (nodev["line"],))
    _check_cost_zero(rows)
    live = _pop(rows, "live", "live")
    if _dec(live.get("devices"), "live devices") != 0:
        raise USBError("live devices: %r" % (live["line"],))
    _pop(rows, "verified", "verified")
    if rows:
        raise USBError("trailing rows: %r" % (rows[0]["line"],))
    return all_rows


def verify_usb_section(text):
    """Validate the full live enumeration proof. Returns parsed rows."""
    rows, failures = parse_usb_section(text)
    if failures:
        raise USBError("failures present: %r" % (failures[0],))
    rows = list(rows)
    all_rows = list(rows)

    _check_synth(rows)
    _, _, connected = _check_protos_ports(rows)
    if len(connected) != 1:
        raise USBError("want exactly one connected port, got %r"
                       % (connected,))

    for stage in ROLLBACK_STAGES:
        row = _pop(rows, "rollback", "rollback")
        if row.get("stage") != stage or not row["line"].endswith(" ok"):
            raise USBError("rollback %s: %r" % (stage, row["line"]))

    slot = _pop(rows, "slot", "slot")
    slot_id = _dec(slot.get("id"), "slot id")
    port_num = _dec(slot.get("port"), "slot port")
    addr = _dec(slot.get("addr"), "slot addr")
    if slot_id < 1 or port_num != connected[0] or addr < 1:
        raise USBError("slot row: %r" % (slot["line"],))

    ctx = _pop(rows, "ctx", "ctx")
    if _dec(ctx.get("slot"), "ctx slot") != slot_id:
        raise USBError("ctx slot: %r" % (ctx["line"],))
    dctx = _hex(ctx.get("dctx"), "ctx dctx")
    ictx = _hex(ctx.get("ictx"), "ctx ictx")
    ep0ring = _hex(ctx.get("ep0ring"), "ctx ep0ring")
    mps = _dec(ctx.get("mps"), "ctx mps")
    if dctx & 0xFFF or ictx & 0xFFF:
        raise USBError("contexts not page frames: %r" % (ctx["line"],))
    if ep0ring & 0xF:
        raise USBError("ep0 ring misaligned: %r" % (ctx["line"],))
    if len({dctx, ictx, ep0ring}) != 3:
        raise USBError("context aliasing: %r" % (ctx["line"],))
    if mps not in (8, 16, 32, 64, 512):
        raise USBError("ctx mps: %r" % (ctx["line"],))

    addr_row = _pop(rows, "addr", "addr")
    if _dec(addr_row.get("slot"), "addr slot") != slot_id or \
            not addr_row["line"].endswith(" ok"):
        raise USBError("addr row: %r" % (addr_row["line"],))
    # Bit-exact Address Device command word: type 11, this slot,
    # BSR=0 (xHC performs SET_ADDRESS; software never re-issues).
    want_ctl = (slot_id << 24) | (11 << 10)
    if _hex(addr_row.get("ctl"), "addr ctl") != want_ctl:
        raise USBError("addr ctl want %x: %r"
                       % (want_ctl, addr_row["line"]))

    out = _pop(rows, "out", "out")
    if _dec(out.get("slot"), "out slot") != slot_id or \
            _dec(out.get("st"), "out st") != 2 or \
            _dec(out.get("ep0"), "out ep0") != 1 or \
            _dec(out.get("mps"), "out mps") != mps:
        raise USBError("out witness: %r" % (out["line"],))

    eval_row = _pop(rows, "eval", "eval")
    if _dec(eval_row.get("slot"), "eval slot") != slot_id or \
            _dec(eval_row.get("mps"), "eval mps") != mps or \
            not eval_row["line"].endswith(" ok"):
        raise USBError("eval row: %r" % (eval_row["line"],))

    portev = _pop(rows, "portev", "portev")
    if _dec(portev.get("port"), "portev port") != port_num or \
            not portev["line"].endswith(" ok"):
        raise USBError("portev row: %r" % (portev["line"],))

    desc = _pop(rows, "desc", "desc")
    if _dec(desc.get("slot"), "desc slot") != slot_id or \
            _dec(desc.get("len"), "desc len") != DESC_LEN:
        raise USBError("desc row: %r" % (desc["line"],))
    raw = desc.get("raw", "")
    if len(raw) != DESC_LEN * 2:
        raise USBError("desc raw length: %r" % (desc["line"],))
    try:
        blob = bytes(int(raw[i:i + 2], 16)
                     for i in range(0, len(raw), 2))
    except ValueError:
        raise USBError("desc raw hex: %r" % (desc["line"],))
    # Independent re-parse of the raw bytes (USB 2.0 §9.6.1).
    if blob[0] != DESC_LEN or blob[1] != 1:
        raise USBError("desc header: %r" % (desc["line"],))
    bcd = blob[2] | (blob[3] << 8)
    mps0 = blob[7]
    vid = blob[8] | (blob[9] << 8)
    pid = blob[10] | (blob[11] << 8)
    cls = blob[4]
    cfgs = blob[17]
    if vid == 0 or cfgs == 0 or bcd == 0:
        raise USBError("desc insane: %r" % (desc["line"],))
    if mps0 != mps:
        raise USBError("desc mps0 %d vs ctx mps %d" % (mps0, mps))

    parsed = _pop(rows, "parsed", "parsed")
    if _hex(parsed.get("vid"), "parsed vid") != vid or \
            _hex(parsed.get("pid"), "parsed pid") != pid or \
            _hex(parsed.get("bcd"), "parsed bcd") != bcd or \
            _dec(parsed.get("cls"), "parsed cls") != cls or \
            _dec(parsed.get("mps0"), "parsed mps0") != mps0 or \
            _dec(parsed.get("cfgs"), "parsed cfgs") != cfgs:
        raise USBError("parsed vs raw: %r" % (parsed["line"],))

    # EP0 ring model: enumerate consumed 2 TDs (Setup+Data+Status),
    # so the 5 repeat reads start at enq=6; each TD advances 3 and
    # the Link slot (index 9) wraps with TD-atomicity intact.
    enq, wraps = 6, 0
    for i in range(1, REPEAT_READS + 1):
        if enq % 3 != 0:
            raise USBError("model desync at read %d" % i)
        want_ptr = ep0ring + (enq + 2) * TRB_SIZE
        done = _pop(rows, "xferdone", "xferdone %d" % i)
        if _dec(done.get("slot"), "xfer slot") != slot_id or \
                _dec(done.get("ep"), "xfer ep") != 1 or \
                _dec(done.get("cc"), "xfer cc") != 1 or \
                _dec(done.get("resid"), "xfer resid") != 0 or \
                _hex(done.get("ptr"), "xfer ptr") != want_ptr or \
                not done["line"].endswith(" ok"):
            raise USBError("xferdone %d want ptr %x: %r"
                           % (i, want_ptr, done["line"]))
        enq += 3
        if enq >= EP0_TRBS - 1:
            enq, wraps = 0, wraps + 1

    wrap = _pop(rows, "wrap", "wrap")
    if _dec(wrap.get("ep0"), "wrap ep0") != wraps or \
            _dec(wrap.get("reads"), "wrap reads") != REPEAT_READS or \
            _dec(wrap.get("identical"), "wrap identical") != 1:
        raise USBError("wrap vs model (ep0=%d): %r"
                       % (wraps, wrap["line"],))

    noop = _pop(rows, "noopalive", "noopalive")
    if noop["line"] != "[USB] noopalive ok":
        raise USBError("noopalive: %r" % (noop["line"],))

    cleanup = _pop(rows, "cleanup", "cleanup")
    if cleanup["line"] != "[USB] cleanup slot=0 ok":
        raise USBError("cleanup: %r" % (cleanup["line"],))

    _check_cost_zero(rows)
    live = _pop(rows, "live", "live")
    if _dec(live.get("devices"), "live devices") != 1:
        raise USBError("live devices: %r" % (live["line"],))
    _pop(rows, "verified", "verified")
    if rows:
        raise USBError("trailing rows: %r" % (rows[0]["line"],))
    return all_rows
