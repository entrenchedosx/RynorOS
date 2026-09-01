"""Strict event validation. Fixtures are synthetic; QEMU tracing is separate."""
import re
KBD_START = b"[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage8 hardware input\r\n[KBD] self-test started\r\n"
KBD_END = b"[TEST] keyboard input verified\r\n"
KEYS = ("a", "b", "c", "d", "spc", "ret", "a", "d")
# QEMU key name -> translated Set-1 byte; x deliberately remains UNKNOWN.
SCANS = {"a": 0x1e, "b": 0x30, "c": 0x2e, "d": 0x20, "spc": 0x39,
         "ret": 0x1c, "shift": 0x2a, "shift_r": 0x36, "x": 0x2d}
STEPS = ("[KBD] queue FIFO, capacity, wrap and loss verified (synthetic)",
         "[KBD] Set-1 subset and prefix isolation verified (synthetic)",
         "[KBD] i8042 configured, Set-2 translated to Set-1, irq1 enabled")
def key_sequence(keys):
    if not isinstance(keys, (tuple, list)) or len(keys) != 8 or any(type(k) is not str or k not in SCANS for k in keys):
        raise ValueError("Exactly eight allowed QEMU keys required")
    return tuple(keys)
def expected_events(keys):
    events = []
    for key in key_sequence(keys):
        scan = SCANS[key]
        events.extend(((scan, 0 if key == "x" else scan, 0 if key == "x" else 1),
                       (scan | 0x80, 0 if key == "x" else scan, 0 if key == "x" else 2)))
    return events
def fixture(keys=KEYS):
    events=expected_events(keys)
    return (KBD_START + ("\r\n".join(STEPS)+"\r\n").encode() +
        b"".join((f"[KBD] waiting for input={i}\r\n" +
                  "".join(f"[KBD] event={j} scan={events[j][0]} key={events[j][1]} type={events[j][2]}\r\n"
                          for j in (2*i, 2*i+1))).encode() for i in range(8)) +
        b"[KBD] irqs=17 reads=16 received=16 dropped=0 errors=0 auxiliary=0 empty=1\r\n"
        b"[KBD] concurrent timer_ticks=80 worker_runs=5000\r\n"
        b"[KBD] final allocated_bytes=106496 free_bytes=937984 table_pages=10\r\n" + KBD_END)
KBD_GOOD=fixture()
def parse_kbd_output(output: bytes, keys=KEYS, previous=None) -> dict:
    if len(output)>16384: raise ValueError("KBD output too large")
    lines=iter(output.decode("ascii").splitlines(keepends=True))
    def exact(s):
        if next(lines,None)!=s+"\r\n": raise ValueError("KBD missing/out-of-order line: "+s)
    def rec(pattern):
        match=re.fullmatch(pattern+r"\r\n",next(lines,""))
        if not match: raise ValueError("KBD invalid numeric record")
        values=tuple(int(n) for n in match.groups())
        if any(n>=1<<64 for n in values): raise ValueError("KBD numeric overflow")
        return values
    for line in KBD_START.decode().splitlines(): exact(line)
    for step in STEPS: exact(step)
    events=expected_events(keys)
    for i in range(8):
        exact(f"[KBD] waiting for input={i}")
        for j in (2*i,2*i+1):
            actual=rec(r"\[KBD\] event=(\d+) scan=(\d+) key=(\d+) type=(\d+)")
            if actual!=(j,*events[j]): raise ValueError("KBD event does not match host input")
    irqs,reads,received,dropped,errors,aux,empty=rec(
        r"\[KBD\] irqs=(\d+) reads=(\d+) received=(\d+) dropped=(\d+) errors=(\d+) auxiliary=(\d+) empty=(\d+)")
    # The pinned QEMU controller deterministically latches exactly one startup
    # IRQ without an output byte (documented in keyboard.md). Requiring it means
    # the empty-IRQ accounting cannot be silently removed: a variant that stops
    # counting empty deliveries shifts irqs and is rejected here.
    if reads!=16 or received!=16 or dropped or errors or aux or irqs!=reads+empty or empty!=1:
        raise ValueError("KBD hardware counters invalid")
    ticks,runs=rec(r"\[KBD\] concurrent timer_ticks=(\d+) worker_runs=(\d+)")
    if not ticks or not runs: raise ValueError("KBD concurrent execution missing")
    allocated,free,tables=rec(r"\[KBD\] final allocated_bytes=(\d+) free_bytes=(\d+) table_pages=(\d+)")
    if allocated!=106496 or free%4096 or tables!=10: raise ValueError("KBD final accounting invalid")
    if previous and (allocated,free,tables)!=(previous["allocated"],previous["free"],previous["tables"]):
        raise ValueError("KBD resources changed from scheduler baseline")
    exact("[TEST] keyboard input verified")
    if next(lines,None) is not None: raise ValueError("KBD unexpected trailing records")
    return dict(received=received,dropped=dropped,reads=reads,irqs=irqs,empty=empty,
                allocated=allocated,free=free,tables=tables,ticks=ticks,runs=runs)
def validate_kbd_output(output, keys=KEYS, previous=None):
    try: parse_kbd_output(output,keys,previous)
    except (ValueError,UnicodeDecodeError) as error: return [str(error)]
    return []
def validate_keyboard_trace(trace, keys=KEYS, extra_scans=()):
    """Independent emulator evidence: device events, PIC acknowledgments, I/O reads.
    Trace event support is required from the documented QEMU build."""
    start = trace.find("ps2_keyboard_event ")
    if start < 0:
        raise ValueError("QEMU keyboard trace missing input events")
    trace = trace[start:]
    expected_scans = [ev[0] for ev in expected_events(keys)]
    for scan in extra_scans:
        if type(scan) is not int or not 0 < scan < 0x80:
            raise ValueError("QEMU extra keyboard scan invalid")
        expected_scans.extend((scan, scan | 0x80))
    reads = [int(v, 16) for v in re.findall(r"pckbd_kbd_read_data 0x([0-9a-f]+)", trace)]
    if reads != expected_scans:
        raise ValueError("QEMU data-port reads do not match injected input")
    events = re.findall(r"ps2_keyboard_event [^\r\n]* down ([01]) [^\r\n]* set (\d+) xlate (\d+)", trace)
    count = len(expected_scans)
    if events != [(str(i % 2 == 0 and 1 or 0), "2", "1") for i in range(count)]:
        raise ValueError("QEMU keyboard make/break or scan configuration mismatch")
    if len(re.findall(r"pic_interrupt irq 1 intno 33\b", trace)) != count:
        raise ValueError("QEMU IRQ1 acknowledgment count mismatch")
    positions = [[m.start() for m in re.finditer(pattern, trace)] for pattern in
                 (r"ps2_keyboard_event ", r"pic_interrupt irq 1 intno 33\b", r"pckbd_kbd_read_data ")]
    for i in range(count):
        if not positions[0][i] < positions[1][i] < positions[2][i]:
            raise ValueError("QEMU input/IRQ1/read ordering invalid")
        # A release may already be queued before the guest consumes the make.
        # FIFO byte order and each byte's device -> IRQ -> read chain above are
        # required; no cross-byte timing assumption is valid here.


# Minimum genuine IRQ0 deliveries that must precede the first IRQ1 (keyboard)
# delivery in the time-ordered trace: exactly 3 timer ticks plus 72 scheduler
# ticks (24+24+24, each phase's guest self-test waits for its budget then
# masks IRQ0). All of these happen before the keyboard phase enables IRQ1, so
# a guest printing canned timer or scheduler text without real PIT deliveries
# cannot reach this count, even though later phases (keyboard, runtime)
# legitimately deliver many more IRQ0s of their own.
IRQ0_BEFORE_KEYBOARD = 3 + 72


def validate_irq0_trace(trace):
    """Independent emulator evidence that the timer and scheduler phases were
    driven by real PIC IRQ0 deliveries, not canned serial text."""
    irq0 = [m.start() for m in re.finditer(r"pic_interrupt irq 0 intno 32\b", trace)]
    irq1 = re.search(r"pic_interrupt irq 1 intno 33\b", trace)
    before = len(irq0) if irq1 is None else sum(1 for p in irq0 if p < irq1.start())
    if irq1 is None:
        raise ValueError("QEMU trace missing IRQ1 keyboard deliveries")
    if before < IRQ0_BEFORE_KEYBOARD:
        raise ValueError(f"QEMU IRQ0 deliveries before keyboard {before} below the real-execution floor {IRQ0_BEFORE_KEYBOARD}")
