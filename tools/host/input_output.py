"""Stage 18d Slices A/B input-section validation (test images only)."""
import re

INPUT_START = b"[INPUT] self-test started\r\n"
INPUT_VERIFIED = b"[INPUT] input verified\r\n"
# Host key names for the [INPUT] injection stream. ctrl is left Ctrl
# (single-byte Set-1 0x1D); ctrl_r is right Ctrl (E0-prefixed pair).
# Values are tuples of wire scan bytes (make then break).
INPUT_SCANS = {
    "a": (0x1e, 0x9e), "b": (0x30, 0xb0), "c": (0x2e, 0xae),
    "d": (0x20, 0xa0), "spc": (0x39, 0xb9), "ret": (0x1c, 0x9c),
    "ctrl": (0x1d, 0x9d), "ctrl_r": (0xe0, 0x1d, 0xe0, 0x9d),
}
# Canonical positional keys: P2 eight, then 34 P4 'a' keys. Park windows
# are self-describing (their markers name the keys), so retries never
# disturb positional consumption. Unused tail keys are never sent.
CANONICAL_KEYS = (
    ("a", "b", "c", "d", "a", "b", "a", "b")
    + ("a",) * 34
)


def input_key_sequence(keys):
    if (not isinstance(keys, (tuple, list)) or not keys
            or any(type(k) is not str or k not in INPUT_SCANS for k in keys)):
        raise ValueError("input_keys must be a non-empty tuple of known input key names")
    return tuple(keys)


def expected_input_bytes(keys):
    out = []
    for key in input_key_sequence(keys):
        out.extend(INPUT_SCANS[key])
    return out


def split_input_tail(tail: bytes) -> tuple:
    """Split a trailing contiguous [INPUT] run off tail.

    Returns (head, input_part). Absent input lines -> (tail, b"").
    A present-but-unterminated run is returned whole for the section
    validator to report as incomplete (never a mismatch).
    """
    lines = tail.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    first = None
    for index, line in enumerate(lines):
        if line.strip().startswith(b"[INPUT] "):
            first = index
            break
    if first is None:
        return tail, b""
    # The input section must be one contiguous trailing run.
    for line in lines[first:]:
        if not line.strip().startswith(b"[INPUT] "):
            return tail, b"\r\n".join(lines[first:]) + b"\r\n"
    return ((b"\r\n".join(lines[:first]) + (b"\r\n" if first else b"")),
            b"\r\n".join(lines[first:]) + b"\r\n")


_NUM = r"(\d+)"
_HEX = r"([0-9a-f]*)"


def validate_input_section(part: bytes) -> list:
    """Structural check for the trailing input section.

    Incomplete sections report 'input section incomplete' (the boot loop
    keeps waiting); complete-but-wrong sections report 'input mismatch'.
    """
    if part == b"":
        return []
    lines = part.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    if not lines or lines[0].strip() != INPUT_START.strip():
        return ["input section incomplete"]
    if lines[-1].strip() != INPUT_VERIFIED.strip():
        return ["input section incomplete"]
    allowed = (
        re.compile(rb"\[INPUT\] self-test started"),
        re.compile(rb"\[INPUT\] decode matrix ok"),
        re.compile(rb"\[INPUT\] copy matrix pass=" + _NUM.encode() + rb" fail=" + _NUM.encode()),
        re.compile(rb"\[INPUT\] accounting balanced"),
        re.compile(rb"\[INPUT\] read empty-again ok"),
        re.compile(rb"\[INPUT\] read exact ok"),
        re.compile(rb"\[INPUT\] read zero-length ok"),
        re.compile(rb"\[INPUT\] read short ok"),
        re.compile(rb"\[INPUT\] read hostile-scalars ok"),
        re.compile(rb"\[INPUT\] read hostile-pointers ok"),
        re.compile(rb"\[INPUT\] read preservation ok"),
        re.compile(rb"\[INPUT\] read matrix bytes=" + _NUM.encode()),
        re.compile(rb"\[INPUT\] read matrix ok"),
        re.compile(rb"\[INPUT\] waiting for input=" + _NUM.encode()),
        re.compile(rb"\[INPUT\] payload n=" + _NUM.encode() + rb" hex=" + _HEX.encode()),
        re.compile(rb"\[INPUT\] window attempt=" + _NUM.encode() + rb" keys=[a-z_,]+"),
        re.compile(rb"\[INPUT\] window parked attempt=" + _NUM.encode()),
        re.compile(rb"\[INPUT\] window bytes=" + _NUM.encode()),
        re.compile(rb"\[INPUT\] park matrix ok"),
        re.compile(rb"\[INPUT\] lost dropped=" + _NUM.encode()),
        re.compile(rb"\[INPUT\] lost matrix ok"),
        re.compile(rb"\[INPUT\] input verified"),
    )
    for line in lines:
        text = line.strip()
        if text.startswith(b"[INPUT] failure="):
            return ["guest failure: " + text.decode("ascii", "replace")]
        if not any(rx.fullmatch(text) for rx in allowed):
            return ["input mismatch: unexpected line %r" % text[:60]]
    return []


def validate_input_trace(trace: str, sent_keys, stage8_keys) -> None:
    """Independent emulator evidence for the whole key stream: the port
    reads must equal stage-8 bytes followed by the sent input bytes
    exactly (device event -> PIC IRQ1 ack -> port read per byte), with an
    IRQ1 ack per byte plus the single startup empty IRQ."""
    from kbd_output import expected_events
    stage8 = [ev[0] for ev in expected_events(stage8_keys)]
    expected = stage8 + expected_input_bytes(sent_keys)
    start = trace.find("ps2_keyboard_event ")
    if start < 0:
        raise ValueError("QEMU keyboard trace missing input events")
    trace = trace[start:]
    reads = [int(v, 16) for v in re.findall(r"pckbd_kbd_read_data 0x([0-9a-f]+)", trace)]
    if reads != expected:
        raise ValueError("QEMU data-port reads do not match stage-8 + sent input bytes")
    # One IRQ1 ack per byte inside the slice. The single startup empty
    # IRQ predates the first device event (outside this slice); stage-8
    # pins empty==1 separately, so it is not double-counted here.
    acks = len(re.findall(r"pic_interrupt irq 1 intno 33\b", trace))
    if acks != len(expected):
        raise ValueError("QEMU IRQ1 acknowledgment count mismatch for input phase")
