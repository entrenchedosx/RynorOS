"""Strict Stage 9 records and full framebuffer/scanout reference comparisons.
Fixtures are synthetic; actual pmemsave/screendump files are emulator evidence."""

import re

DISPLAY_START = (b"[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage9 frame buffer\r\n"
                 b"[FB] self-test started\r\n")
DISPLAY_END = b"[TEST] framebuffer api verified\r\n"
STEPS = ("[FB] metadata and guarded drawing/text tests passed",
         "[FB] MMIO ownership, UC/NX permissions and OOM rollback verified",
         "[FB] pattern 0 painted and read back")
VM_MMIO_BASE = 0xFFFFFE8000000000
def fixture() -> bytes:
    """Synthetic display transcript used by fixture composition only; geometry
    mirrors the version-2 PCI/BGA 1024x768x32 handoff and keyboard baseline."""
    return (DISPLAY_START +
            b"\r\n".join(step.encode() for step in STEPS) + b"\r\n" +
            b"[FB] handoff magic=1145586246 version=2 status=1\r\n"
            b"[FB] mode=45253 width=1024 height=768 pitch=4096 bpp=32 memory_model=6\r\n"
            b"[FB] pixel maps red=16711680 green=65280 blue=255\r\n"
            b"[FB] lfb=4244635648 fb_bytes=3145728 pages=768\r\n"
            b"[FB] lfb_end=4247781376\r\n"
            b"[FB] mapped va=18446742424442109952\r\n"
            b"[FB] final allocated_bytes=122880 free_bytes=921600 table_pages=14\r\n" +
            DISPLAY_END)


DISPLAY_GOOD = fixture()
DISPLAY_PIXELS = {
    (0, 0): (255, 0, 0),
    (512, 384): (0, 0, 255),
    (20, 36): (0, 255, 0),
    (300, 100): (0, 0, 0),
}


def _exact(lines, value) -> None:
    if next(lines, None) != value + "\r\n":
        raise ValueError("Display missing/out-of-order line: " + value)


def _record(lines, pattern):
    match = re.fullmatch(pattern + r"\r\n", next(lines, ""))
    if not match:
        raise ValueError("Display invalid numeric record")
    values = tuple(int(n) for n in match.groups())
    if any(n >= 1 << 64 for n in values):
        raise ValueError("Display numeric overflow")
    return values


def parse_display_output(output: bytes, previous: dict | None = None) -> dict:
    if len(output) > 32768:
        raise ValueError("Display output too large")
    lines = iter(output.decode("ascii").splitlines(keepends=True))
    for line in DISPLAY_START.decode().splitlines():
        _exact(lines, line)
    for step in STEPS:
        _exact(lines, step)
    magic, version, status = _record(lines, r"\[FB\] handoff magic=(\d+) version=(\d+) status=(\d+)")
    if magic != 0x44484246 or version != 2 or status != 1:
        raise ValueError("Display handoff header invalid")
    mode, width, height, pitch, bpp, model = _record(
        lines, r"\[FB\] mode=(\d+) width=(\d+) height=(\d+) pitch=(\d+) bpp=(\d+) memory_model=(\d+)")
    if mode != 0xb0c5 or width != 1024 or height != 768 or not 4096 <= pitch <= 16384 or pitch % 4 or bpp != 32 or model != 6:
        raise ValueError("Display geometry does not match 1024x768x32 BGRX")
    red, green, blue = _record(lines, r"\[FB\] pixel maps red=(\d+) green=(\d+) blue=(\d+)")
    if red != 0xFF0000 or green != 0xFF00 or blue != 0xFF:
        raise ValueError("Display pixel masks do not match BGRX 32-bit")
    lfb, fb_bytes, pages = _record(lines, r"\[FB\] lfb=(\d+) fb_bytes=(\d+) pages=(\d+)")
    if lfb % 4096 or fb_bytes != height * pitch or pages != (fb_bytes + 4095) // 4096:
        raise ValueError("Display framebuffer extent invalid")
    lfb_end = _record(lines, r"\[FB\] lfb_end=(\d+)")[0]
    if lfb_end != lfb + fb_bytes or not 0x100000 <= lfb < 1 << 32 or lfb_end > 1 << 32 or fb_bytes > 0x1000000:
        raise ValueError("Display framebuffer window invalid")
    va = _record(lines, r"\[FB\] mapped va=(\d+)")[0]
    if va != VM_MMIO_BASE:
        raise ValueError("Display mapping not in reserved MMIO slot")
    allocated, free, tables = _record(lines, r"\[FB\] final allocated_bytes=(\d+) free_bytes=(\d+) table_pages=(\d+)")
    if previous:
        delta = (pages + 511) // 512 + 2
        if (allocated, free, tables) != (previous["allocated"] + delta * 4096,
                                          previous["free"] - delta * 4096,
                                          previous["tables"] + delta):
            raise ValueError("Display table accounting does not match keyboard baseline")
    if tables != 10 + (pages + 511) // 512 + 2 or allocated % 4096 or free % 4096:
        raise ValueError("Display table page count invalid")
    _exact(lines, "[TEST] framebuffer api verified")
    if next(lines, None) is not None:
        raise ValueError("Display unexpected trailing records")
    return dict(width=width, height=height, pitch=pitch, bpp=bpp, model=model,
                lfb=lfb, lfb_end=lfb + fb_bytes, fb_bytes=fb_bytes, pages=pages, allocated=allocated,
                free=free, tables=tables)


def validate_display_output(output: bytes, previous: dict | None = None) -> list[str]:
    try:
        parse_display_output(output, previous)
    except (ValueError, UnicodeDecodeError) as error:
        return [str(error)]
    return []


# Independent reference glyph specification. Never loaded from guest memory,
# kernel headers or its own read-back: glyph/index mutations must be observable.
GLYPHS = {
 ' ': (0,0,0,0,0,0,0), '0': (14,17,19,21,25,17,14), '1': (4,12,4,4,4,4,14),
 '2': (14,17,1,2,4,8,31), '3': (30,1,1,14,1,1,30), '4': (2,6,10,18,31,2,2),
 '5': (31,16,16,30,1,1,30), '6': (14,16,16,30,17,17,14), '7': (31,1,2,4,8,8,8),
 '8': (14,17,17,14,17,17,14), '9': (14,17,17,15,1,1,14),
 'A': (14,17,17,31,17,17,17), 'B': (30,17,17,30,17,17,30), 'C': (14,17,16,16,16,17,14),
 'D': (30,17,17,17,17,17,30), 'E': (31,16,16,30,16,16,31), 'F': (31,16,16,30,16,16,16),
 'G': (14,17,16,23,17,17,15), 'H': (17,17,17,31,17,17,17), 'I': (14,4,4,4,4,4,14),
 'J': (7,2,2,2,2,18,12), 'K': (17,18,20,24,20,18,17), 'L': (16,16,16,16,16,16,31),
 'M': (17,27,21,21,17,17,17), 'N': (17,25,21,19,17,17,17), 'O': (14,17,17,17,17,17,14),
 'P': (30,17,17,30,16,16,16), 'Q': (14,17,17,17,21,18,13), 'R': (30,17,17,30,20,18,17),
 'S': (15,16,16,14,1,1,30), 'T': (31,4,4,4,4,4,4), 'U': (17,17,17,17,17,17,14),
 'V': (17,17,17,17,17,10,4), 'W': (17,17,17,21,21,21,10), 'X': (17,17,10,4,10,17,17),
 'Y': (17,17,10,4,4,4,4), 'Z': (31,1,2,4,8,16,31), '.': (0,0,0,0,0,6,6),
 '-': (0,0,0,31,0,0,0), ':': (0,6,6,0,6,6,0), '/': (1,1,2,4,8,16,16), '?': (14,17,1,2,4,0,4),
}

def reference_framebuffer(display: dict) -> bytes:
    """Host specification, including every pixel and zeroed row padding."""
    w, h, pitch = display['width'], display['height'], display['pitch']
    if (w, h) != (1024, 768) or not 4096 <= pitch <= 16384 or pitch % 4:
        raise ValueError('Invalid reference geometry')
    data = bytearray(h * pitch)
    def rect(x, y, width, height, rgb):
        bgrx = bytes((*rgb[::-1], 0)) * width
        for row in range(y, y + height):
            start = row * pitch + x * 4
            data[start:start + len(bgrx)] = bgrx
    rect(0, 0, w, 16, (0,0,255)); rect(0,h-16,w,16,(0,0,255))
    rect(0,16,16,h-32,(0,0,255)); rect(w-16,16,16,h-32,(0,0,255))
    rect(w//2-32,h//2-32,64,64,(255,0,0)); rect(16,32,8,8,(0,255,0))
    def text(x, y, message):
        cx, cy = x, y
        for char in message:
            if char == '\n': cx=x; cy+=8; continue
            if char == '\r': cx=x; continue
            for row, bits in enumerate(GLYPHS[char]):
                for col in range(5):
                    if bits & (16 >> col) and cx+col < w and cy+row < h:
                        pos=(cy+row)*pitch+(cx+col)*4
                        data[pos:pos+4]=bytes((224,224,224,0))
            cx+=8
    text(40,32,'RYNOROS FRAME BUFFER'); text(40,48,'BGA 1024X768 STAGE 9')
    text(40,64,'ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789 .-:/?')
    text(40,80,'A\nB\rC'); text(w-3,h-3,'AZ')
    return bytes(data)

def verify_display_pixels(dump: bytes, display: dict) -> None:
    expected = reference_framebuffer(display)
    if len(dump) != len(expected):
        raise ValueError(f'Framebuffer dump length {len(dump)} != {len(expected)}')
    if dump != expected:
        offset = next(i for i, pair in enumerate(zip(dump, expected)) if pair[0] != pair[1])
        raise ValueError(f'Framebuffer byte mismatch at offset {offset}: {dump[offset]} != {expected[offset]}')

def verify_display_scanout(ppm: bytes, display: dict) -> None:
    """Check QEMU's actual display surface, not only guest-selected physical RAM."""
    header = f"P6\n{display['width']} {display['height']}\n255\n".encode()
    raw = reference_framebuffer(display)
    pixels = bytearray()
    for y in range(display['height']):
        row = raw[y*display['pitch']:y*display['pitch']+display['width']*4]
        for x in range(0,len(row),4): pixels.extend((row[x+2],row[x+1],row[x]))
    if ppm != header + pixels:
        raise ValueError('QEMU scanout does not match the complete RGB reference')
