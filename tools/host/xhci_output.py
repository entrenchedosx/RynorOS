"""Host-side validator for the xHCI-A1 [XHCI] transcript section.

Parses the gated self-test rows and re-derives the whole proof
independently: command-ring producer math (31 usable TRBs per lap,
Link skip, PCS latch), event-ring consumer math (flat 32-TRB
segment, CCS gate, ERDP advance), token reuse, the XWORK CPL3
accumulator chain, and zero net allocation. Raises XHCIError with
a precise reason; returns the parsed rows on success.

Production contract: docs/design/xhci.md. QEMU specifics (BDF,
BAR base, DMA bases, MSI-X vector number) are captured from the
transcript, never frozen here.
"""


class XHCIError(Exception):
    pass


SYNTH_CASES = 43
SERIES = 64
CMD_TRBS = 32
CMD_USABLE = 31
EVT_TRBS = 32
TRB_SIZE = 16
ROLLBACK_STAGES = ("BAR", "CAPS", "LEGACY", "HALT", "RESET", "DCBAA",
                   "SCRATCH", "CMDRING", "EVTRING", "ERST", "INTR",
                   "BME", "MSIX", "START")
XWORK_ITERS = 8192
XWORK_YIELDS = 8
XWORK_SEEDS = (0xC0DE000000000000, 0xC0DE000000000001,
               0xC0DE000000000002, 0xC0DE000000000003,
               0xC0DE000000000004)
M64 = (1 << 64) - 1


def xwork_expected():
    """Independent XWORK accumulator chain (user_blob_xwork parity)."""
    r12, r13, r14, r15, rbx = XWORK_SEEDS
    for i in range(1, XWORK_ITERS + 1):
        r12 = (r12 + i) & M64
        r13 = (r13 ^ r12) & M64
        r14 = (r14 + r13) & M64
        r15 = (r15 ^ r14) & M64
        rbx = (rbx + r15) & M64
    return [r12, r13, r14, r15, rbx]


def _fields(line):
    parts = line.split()
    out = {}
    for token in parts[1:]:
        if "=" in token:
            key, _, value = token.partition("=")
            out[key] = value
    return out


def _kind(line):
    parts = line.split()
    if len(parts) < 2:
        return ""
    if parts[1] == "noop" and len(parts) > 2 and parts[2] == "done":
        return "noop-done"
    if parts[1] == "live" and len(parts) > 2 and parts[2] == "ok":
        return "live"
    if parts[1] == "xhci" and len(parts) > 2 and parts[2] == "verified":
        return "verified"
    if parts[1] == "synth" and len(parts) > 2 and parts[2] == "ok":
        return "synth"
    if parts[1] == "reset" and len(parts) > 2 and parts[2] == "ok":
        return "reset"
    if parts[1] == "teardown" and len(parts) > 2 and parts[2] == "ok":
        return "teardown"
    if parts[1] == "rollback":
        return "rollback"
    return parts[1]


def parse_xhci_section(text):
    """Split the transcript into ordered [XHCI] rows plus failures."""
    rows = []
    failures = []
    for raw in text.splitlines():
        line = raw.strip()
        if "failure=" in line or "rejected frame" in line or "panic" in line:
            failures.append(line)
        if line.startswith("[XHCI] failure="):
            continue
        if line.startswith("[XHCI] "):
            fields = _fields(line)
            fields["kind"] = _kind(line)
            fields["raw"] = line
            rows.append(fields)
    return rows, failures


def _hex(value, what):
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        raise XHCIError("%s: bad hex %r" % (what, value))


def _dec(value, what):
    try:
        return int(value, 10)
    except (TypeError, ValueError):
        raise XHCIError("%s: bad decimal %r" % (what, value))


def _pop(rows, kind, what):
    if not rows or rows[0]["kind"] != kind:
        got = rows[0]["raw"] if rows else "<end>"
        raise XHCIError("%s: expected %s row, got %r" % (what, kind, got))
    return rows.pop(0)


def verify_absent_section(text):
    """Validate the XHCI_ABSENT transcript: synth, absent, zero cost."""
    rows, failures = parse_xhci_section(text)
    if failures:
        raise XHCIError("failures present: %r" % (failures[0],))
    kinds = [r["kind"] for r in rows]
    if kinds != ["synth", "absent", "cost", "live", "verified"]:
        raise XHCIError("absent section shape %r" % (kinds,))
    if _dec(rows[0].get("cases"), "synth cases") != SYNTH_CASES:
        raise XHCIError("synth cases %r" % (rows[0].get("raw"),))
    if _hex(rows[2].get("alloc0"), "cost") != _hex(rows[2].get("alloc1"),
                                                  "cost"):
        raise XHCIError("absent cost not zero: %r" % (rows[2].get("raw"),))
    if _dec(rows[3].get("devices"), "live devices") != 0:
        raise XHCIError("absent devices %r" % (rows[3].get("raw"),))
    return rows


def verify_xhci_section(text):
    """Validate the full live proof transcript. Returns parsed rows."""
    rows, failures = parse_xhci_section(text)
    if failures:
        raise XHCIError("failures present: %r" % (failures[0],))
    rows = list(rows)
    all_rows = list(rows)

    synth = _pop(rows, "synth", "synth")
    if _dec(synth.get("cases"), "synth cases") != SYNTH_CASES:
        raise XHCIError("synth cases %r" % (synth.get("raw"),))

    for stage in ROLLBACK_STAGES:
        row = _pop(rows, "rollback", "rollback")
        # Row shape: "rollback stage=BAR ok leaks=0".
        if row.get("stage") != stage or "ok leaks=0" not in row["raw"]:
            raise XHCIError("rollback %s: %r" % (stage, row["raw"]))

    found = _pop(rows, "found", "found")
    cls = found.get("class", "")
    try:
        c, s, p = (int(v, 16) for v in cls.split("."))
    except ValueError:
        raise XHCIError("found class %r" % (found.get("raw"),))
    if (c, s, p) != (0x0C, 0x03, 0x30):
        raise XHCIError("found class %r" % (found.get("raw"),))

    bar = _pop(rows, "bar", "bar")
    bar_size = _hex(bar.get("size"), "bar size")
    if bar_size < 0x1000:
        raise XHCIError("bar size %r" % (bar.get("raw"),))

    caps = _pop(rows, "caps", "caps")
    caplen = _hex(caps.get("caplen"), "caps caplen")
    ac64 = _dec(caps.get("ac64"), "caps ac64")
    pagesz = _hex(caps.get("pagesize"), "caps pagesize")
    dboff = _hex(caps.get("dboff"), "caps dboff")
    rtsoff = _hex(caps.get("rtsoff"), "caps rtsoff")
    if caplen < 0x20 or ac64 not in (0, 1) or not pagesz & 1:
        raise XHCIError("caps insane: %r" % (caps.get("raw"),))
    if bar_size <= dboff or bar_size <= rtsoff:
        raise XHCIError("bar too small for register file")

    seen_legacy_xcap = False
    while rows and rows[0]["kind"] == "xcap":
        row = rows.pop(0)
        if _dec(row.get("id"), "xcap id") == 1:
            seen_legacy_xcap = True
    legacy = _pop(rows, "legacy", "legacy")
    if seen_legacy_xcap != legacy["raw"].startswith("[XHCI] legacy owned"):
        raise XHCIError("legacy row vs xcap walk: %r" % (legacy["raw"],))

    halted = _pop(rows, "halted", "halted")
    if not _hex(halted.get("usbsts"), "halted usbsts") & 1:
        raise XHCIError("not halted: %r" % (halted.get("raw"),))
    reset = _pop(rows, "reset", "reset")
    if _dec(reset.get("cnr"), "reset cnr") != 0:
        raise XHCIError("CNR still set: %r" % (reset.get("raw"),))

    dmaa = _pop(rows, "dmaa", "dmaa")
    cmd_base = _hex(dmaa.get("cmd"), "dmaa cmd")
    evt_base = _hex(dmaa.get("evt"), "dmaa evt")
    if cmd_base & 0x3F:
        raise XHCIError("cmd ring misaligned")
    if (dmaa.get("maxbus"), ac64) not in (("ANY", 1), ("32", 0)):
        raise XHCIError("addr width vs AC64: %r" % (dmaa.get("raw"),))

    slots = _pop(rows, "slots", "slots")
    maxen = _dec(slots.get("maxen"), "slots maxen")
    hw_slots = _dec(caps.get("slots"), "caps slots")
    if not 1 <= maxen <= min(hw_slots, 8):
        raise XHCIError("slots maxen: %r" % (slots.get("raw"),))

    ring_cmd = _pop(rows, "ring", "ring cmd")
    if ring_cmd["raw"] != "[XHCI] ring cmd pcs=1 enq=0 wraps=0":
        raise XHCIError("cmd ring init: %r" % (ring_cmd["raw"],))
    ring_evt = _pop(rows, "ring", "ring evt")
    if ring_evt["raw"] != "[XHCI] ring evt ccs=1 deq=0 wraps=0":
        raise XHCIError("evt ring init: %r" % (ring_evt["raw"],))

    erst = _pop(rows, "erst", "erst")
    if _dec(erst.get("sz"), "erst sz") != 1:
        raise XHCIError("erst size: %r" % (erst.get("raw"),))
    if _hex(erst.get("erdp"), "erst erdp") != evt_base:
        raise XHCIError("erdp not at segment base")

    intr = _pop(rows, "intr", "intr")
    if intr["raw"] != "[XHCI] intr ie=1 imod=0 erstsz=1":
        raise XHCIError("interrupter: %r" % (intr["raw"],))
    _pop(rows, "cmd", "cmd saved")

    msix = _pop(rows, "msix", "msix")
    vec = _dec(msix.get("vec"), "msix vec")
    if not 48 <= vec <= 127:
        raise XHCIError("msix vec outside dynamic pool: %d" % vec)
    if _dec(msix.get("entry"), "msix entry") != 0:
        raise XHCIError("msix entry: %r" % (msix.get("raw"),))

    started = _pop(rows, "started", "started")
    if _hex(started.get("usbsts"), "started usbsts") & 1:
        raise XHCIError("still halted: %r" % (started.get("raw"),))

    # NOOP series: independent ring models walk every command.
    enq, pcs, cmd_wraps = 0, 1, 0
    deq, ccs, evt_wraps = 0, 1, 0
    for i in range(1, SERIES + 1):
        noop = _pop(rows, "noop", "series noop %d" % i)
        tok = _dec(noop.get("token"), "series token")
        if tok > 7:
            raise XHCIError("series token range: %r" % (noop["raw"],))
        want_trb = cmd_base + enq * TRB_SIZE
        if enq == CMD_TRBS - 1:
            raise XHCIError("model hit Link slot at cmd %d" % i)
        if _hex(noop.get("trb"), "series trb") != want_trb:
            raise XHCIError("series cmd %d trb: %r want %x"
                            % (i, noop["raw"], want_trb))
        enq += 1
        if enq == CMD_TRBS - 1:
            enq, pcs, cmd_wraps = 0, pcs ^ 1, cmd_wraps + 1
        _pop(rows, "db", "series db %d" % i)
        irq = _pop(rows, "irq", "series irq %d" % i)
        if _dec(irq.get("vec"), "series irq vec") != vec or \
                _dec(irq.get("count"), "series irq count") != i or \
                _dec(irq.get("isr"), "series irq isr") != 1 or \
                _dec(irq.get("user"), "series irq user") != 0:
            raise XHCIError("series irq %d: %r" % (i, irq["raw"]))
        cmpl = _pop(rows, "cmpl", "series cmpl %d" % i)
        if _dec(cmpl.get("token"), "series cmpl token") != tok or \
                _hex(cmpl.get("ptr"), "series cmpl ptr") != want_trb or \
                _dec(cmpl.get("cc"), "series cmpl cc") != 1 or \
                _dec(cmpl.get("slot"), "series cmpl slot") != 0 or \
                _dec(cmpl.get("type"), "series cmpl type") != 33 or \
                _dec(cmpl.get("cycle"), "series cmpl cycle") != ccs or \
                not cmpl["raw"].endswith(" ok"):
            raise XHCIError("series cmpl %d: %r" % (i, cmpl["raw"]))
        deq += 1
        if deq == EVT_TRBS:
            deq, ccs, evt_wraps = 0, ccs ^ 1, evt_wraps + 1
        done = _pop(rows, "noop-done", "series done %d" % i)
        if _dec(done.get("token"), "series done token") != tok or \
                _dec(done.get("count"), "series done count") != i:
            raise XHCIError("series done %d: %r" % (i, done["raw"]))

    erdp = _pop(rows, "erdp", "erdp")
    if _hex(erdp.get("deq"), "erdp deq") != evt_base + deq * TRB_SIZE or \
            _dec(erdp.get("wraps"), "erdp wraps") != evt_wraps:
        raise XHCIError("erdp vs model: %r" % (erdp["raw"]))
    intx = _pop(rows, "intx", "intx")
    if _dec(intx.get("silent"), "intx silent") != 1:
        raise XHCIError("INTx not silent")

    # CPL3 leg: the 65th command on the same rings (no per-cmd rows).
    enq += 1
    if enq == CMD_TRBS - 1:
        enq, pcs, cmd_wraps = 0, pcs ^ 1, cmd_wraps + 1
    want_ccs = ccs
    deq += 1
    if deq == EVT_TRBS:
        deq, ccs, evt_wraps = 0, ccs ^ 1, evt_wraps + 1

    pirq = _pop(rows, "irq", "cpl3 irq")
    if _dec(pirq.get("vec"), "cpl3 irq vec") != vec or \
            _dec(pirq.get("count"), "cpl3 irq count") != SERIES + 1 or \
            _dec(pirq.get("isr"), "cpl3 irq isr") != 1 or \
            _dec(pirq.get("user"), "cpl3 irq user") != 1:
        raise XHCIError("cpl3 irq: %r" % (pirq["raw"]))
    rip = _hex(pirq.get("rip"), "cpl3 rip")
    if not 0x400000 <= rip < 0x401000:
        raise XHCIError("cpl3 rip outside user code: %x" % rip)

    cpl3 = _pop(rows, "cpl3", "cpl3")
    if _dec(cpl3.get("yields"), "cpl3 yields") != XWORK_YIELDS or \
            _dec(cpl3.get("snap0"), "cpl3 snap0") != 4096 or \
            _dec(cpl3.get("snap1"), "cpl3 snap1") != 5120 or \
            _dec(cpl3.get("final"), "cpl3 final") != XWORK_ITERS or \
            not cpl3["raw"].endswith(" regs=ok cs=23"):
        raise XHCIError("cpl3 row: %r" % (cpl3["raw"]))
    accs = [_hex(v.strip(), "cpl3 acc") for v in cpl3.get("acc", "").split(",")]
    if accs != xwork_expected():
        raise XHCIError("cpl3 accumulators mismatch: %r" % (cpl3["raw"],))
    if want_ccs != 1:
        raise XHCIError("cpl3 event lap model drift")

    teardown = _pop(rows, "teardown", "teardown")
    if teardown["raw"] != ("[XHCI] teardown ok cmd_restored=1 "
                           "dma_free=1 vec_free=1"):
        raise XHCIError("teardown: %r" % (teardown["raw"]))

    # Reinit leg: fresh rings, one command, no wraps.
    rnoop = _pop(rows, "noop", "reinit noop")
    if _hex(rnoop.get("trb"), "reinit trb") & 0x3F:
        raise XHCIError("reinit trb misaligned")
    _pop(rows, "db", "reinit db")
    rdone = _pop(rows, "noop-done", "reinit done")
    if _dec(rdone.get("token"), "reinit done token") != \
            _dec(rnoop.get("token"), "reinit token") or \
            _dec(rdone.get("count"), "reinit done count") != 1:
        raise XHCIError("reinit done: %r" % (rdone["raw"]))

    wrap = _pop(rows, "wrap", "wrap")
    if _dec(wrap.get("cmd"), "wrap cmd") != cmd_wraps or \
            _dec(wrap.get("evt"), "wrap evt") != evt_wraps or \
            _dec(wrap.get("cmds"), "wrap cmds") != SERIES + 2 or \
            _dec(wrap.get("cmpls"), "wrap cmpls") != SERIES + 2:
        raise XHCIError("wrap vs model (cmd=%d evt=%d): %r"
                        % (cmd_wraps, evt_wraps, wrap["raw"]))

    cost = _pop(rows, "cost", "cost")
    if _hex(cost.get("alloc0"), "cost") != _hex(cost.get("alloc1"), "cost"):
        raise XHCIError("net allocation: %r" % (cost["raw"],))
    live = _pop(rows, "live", "live")
    if _dec(live.get("devices"), "live devices") != 1:
        raise XHCIError("live devices: %r" % (live["raw"],))
    _pop(rows, "verified", "verified")
    if rows:
        raise XHCIError("trailing rows: %r" % (rows[0]["raw"],))
    return all_rows
