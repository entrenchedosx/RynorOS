"""Stage 11 shell/monitor output validation.

The shell always emits a synthetic parser/dispatch section. When the image is
built with RYNOR_SHELL_INTERACTIVE the dedicated integration test injects a
fixed host-selected key script and the shell emits a real interactive session
section as well; the validator detects interactivity by the presence of the
session-start marker and checks the complete input echo chain against the
script, the command results against an independent FNV-1a fold, and the final
accounting. Fixtures are synthetic; QEMU key delivery is a separate, real
emulator trace. """
import re

SHELL_START = (b"[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage11 shell monitor\r\n"
               b"[SHELL] self-test started\r\n")
SHELL_END = b"[TEST] shell monitor verified\r\n"
SHELL_SKIP = b"[SHELL] interactive session skipped (host did not request input)\r\n"

# Host-selectable QEMU key name -> translated Set-1 make scan byte.
SCANS = {
    "a": 0x1e, "b": 0x30, "c": 0x2e, "d": 0x20, "e": 0x12, "f": 0x21,
    "g": 0x22, "h": 0x23, "i": 0x17, "j": 0x24, "k": 0x25, "l": 0x26,
    "m": 0x32, "n": 0x31, "o": 0x18, "p": 0x19, "q": 0x10, "r": 0x13,
    "s": 0x1f, "t": 0x14, "u": 0x16, "v": 0x2f, "w": 0x11, "x": 0x2d,
    "y": 0x15, "z": 0x2c,
    "1": 0x02, "2": 0x03,
    "spc": 0x39, "ret": 0x1c,
}
ASCII_OF = {"spc": " ", "ret": None}
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    ASCII_OF[_c] = _c

# Fixed interactive session script: four commands typed one key at a time.
# The trailing command is deliberately unknown so rejection is exercised with
# real input. One host sendkey maps to exactly one "[SHELL] waiting" marker.
# The script stores QEMU key names, so a space is the "spc" name; the line
# editor rebuilds the command text through ASCII_OF.
SCRIPT = tuple(
    "spc" if ch == " " else ch
    for token in ("upper hello", "count a1b2", "digest ab", "bogus")
    for ch in (*token, "ret")
)
SHELL_KEYS = SCRIPT  # QEMU key names, in order
KEY_BUDGET = len(SCRIPT)  # must equal the kernel's SHELL_SESSION_KEYS


def fnv1a(data: bytes) -> int:
    h = 0xcbf29ce484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001b3) & ((1 << 64) - 1)
    return h


def digest_hex(data: bytes) -> str:
    v = fnv1a(data)
    # Kernel prints the digest bytes little-endian (d[0] is the LSB).
    return "".join("%02X" % ((v >> (8 * i)) & 0xFF) for i in range(8))


# Synthetic evidence that always appears, in order. Each element is either a
# literal line or a record; 'exec' lines are compared after tokenization.
def _synthetic_lines() -> list[str]:
    big_digits = "1" * 30
    return [
        "[SHELL] tokenizer, bounds and empty line verified (synthetic)",
        "[SHELL] exec=version", "RynorOS 0.1.0",
        "[SHELL] exec=help",
        "commands: help version echo <text> upper <text> count <text> digest <text> clear",
        '[SHELL] exec=echo arg="hi"', "hi",
        '[SHELL] exec=upper arg="abc123"', "ABC123",
        '[SHELL] exec=upper arg="12345678901234567890123456789012345678901"',
        "error: upper accepts at most 40 characters",
        '[SHELL] exec=count arg="a1b2"', "2",
        '[SHELL] exec=digest arg="ab"', "0x" + digest_hex(b"ab"),
        "[SHELL] exec=clear", "[SHELL] clear: display redraw requested",
        "[SHELL] exec=bogus", "error: unknown command",
        "[SHELL] error: empty command",
        "[SHELL] error: too many arguments",
        "[SHELL] error: invalid command line",
        '[SHELL] exec=echo arg="hi"', "error: echo requires one argument",
        '[SHELL] exec=upper arg="hi"', "error: upper requires one argument",
        '[SHELL] exec=count arg="hi"', "error: count requires one argument",
        '[SHELL] exec=digest arg="hi"', "error: digest requires one argument",
        '[SHELL] exec=version arg="extra"', "error: version takes no arguments",
        '[SHELL] exec=help arg="extra"', "error: help takes no arguments",
        f'[SHELL] exec=count arg="{big_digits}"', "30",
        "[SHELL] exec=bogus", "error: unknown command",
        "[SHELL] exec=version", "RynorOS 0.1.0",
        "[SHELL] dispatch and error rejection verified (synthetic)",
    ]


def _interactive_lines(script=SCRIPT) -> list[tuple[str, str]]:
    """Returns (exact line label, expected line) pairs emitted by the session."""
    out = []
    buf = ""
    for i, key in enumerate(script):
        if key not in SCANS or key not in ASCII_OF:
            raise ValueError("unsupported shell key %r" % (key,))
        scan = SCANS[key]
        ascii_ch = ASCII_OF[key] if ASCII_OF[key] is not None else "?"
        out.append(("literal", f"[SHELL] waiting for input={i}"))
        out.append(("literal", f"[SHELL] key={i} scan=0x{scan:02x} ascii='{ascii_ch}' line=\"{buf}\""))
        if key == "ret":
            head, _, arg = buf.partition(" ")
            exec_line = "[SHELL] exec=" + head + (' arg="%s"' % arg if arg else "")
            out.append(("literal", f'[SHELL] line="{buf}"'))
            out.append(("literal", exec_line))
            if head == "echo" and arg:
                out.append(("literal", arg))
            elif head == "upper" and arg:
                out.append(("literal", arg.upper()))
            elif head == "count" and arg:
                out.append(("literal", str(sum(ch.isdigit() for ch in arg))))
            elif head == "digest" and arg:
                out.append(("literal", "0x" + digest_hex(arg.encode("ascii"))))
            else:
                out.append(("literal", "error: unknown command"))
            buf = ""
        else:
            buf += ascii_ch
    out.append(("literal", "[SHELL] interactive session complete"))
    out.append(("literal", f"[SHELL] keys={len(script)} received_scan_bytes={2 * len(script)}"))
    out.append(("literal", "[SHELL] real keyboard session verified"))
    return out


def _fixture() -> bytes:
    lines = ["[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage11 shell monitor",
             "[SHELL] self-test started"]
    lines += _synthetic_lines()
    lines += ["[SHELL] interactive session skipped (host did not request input)"]
    lines += ["[SHELL] final allocated_bytes=122880 free_bytes=65802240 table_pages=14",
              "[TEST] shell monitor verified"]
    return ("\r\n".join(lines) + "\r\n").encode()

SHELL_GOOD = _fixture()


def parse_shell_output(output: bytes, previous=None, script=SCRIPT) -> dict:
    if len(output) > 65536:
        raise ValueError("SHELL output too large")
    text = output.decode("ascii").splitlines()
    if text[0] != "[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage11 shell monitor":
        raise ValueError("SHELL banner mismatch")
    if text[1] != "[SHELL] self-test started":
        raise ValueError("SHELL self-test marker missing")
    pos = 2

    def next_line():
        nonlocal pos
        if pos >= len(text):
            raise ValueError("SHELL output ended early")
        line = text[pos]; pos += 1
        return line

    for expected in _synthetic_lines():
        got = next_line()
        if got != expected:
            raise ValueError("SHELL synthetic mismatch: got %r want %r" % (got, expected))

    interactive = text[pos] == "[SHELL] interactive session started"
    if interactive:
        pos += 1
        for _, expected in _interactive_lines(script):
            got = next_line()
            if got != expected:
                raise ValueError("SHELL interactive mismatch: got %r want %r" % (got, expected))
    else:
        got = next_line()
        if got != "[SHELL] interactive session skipped (host did not request input)":
            raise ValueError("SHELL interactive marker mismatch: got %r" % got)

    m = re.fullmatch(r"\[SHELL\] final allocated_bytes=(\d+) free_bytes=(\d+) table_pages=(\d+)",
                     next_line())
    if not m:
        raise ValueError("SHELL final accounting line malformed")
    allocated, free, tables = (int(v) for v in m.groups())
    for v in (allocated, free):
        if v % 4096:
            raise ValueError("SHELL accounting not page aligned")
    if previous:
        if (allocated, free, tables) != (previous["allocated"], previous["free"], previous["tables"]):
            raise ValueError("SHELL accounting does not match prior baseline")
    if next_line() != "[TEST] shell monitor verified":
        raise ValueError("SHELL end marker missing")
    if pos != len(text):
        raise ValueError("SHELL unexpected trailing records")
    return dict(allocated=allocated, free=free, tables=tables, interactive=interactive,
                keys=len(script))


def validate_shell_output(output: bytes, previous=None, script=SCRIPT):
    try:
        parse_shell_output(output, previous, script)
    except (ValueError, IndexError, UnicodeDecodeError) as error:
        return [str(error)]
    return []
