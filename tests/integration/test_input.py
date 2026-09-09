"""Stage 18d Slices A/B: CPL3 IRQ1 park path, keyboard staging, syscall 3,
copy_to_user substrate, plus scoped temporary implementation mutations."""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from input_output import (INPUT_VERIFIED, CANONICAL_KEYS, expected_input_bytes,
                          validate_input_section, validate_input_trace)
from kbd_output import KEYS as STAGE8_KEYS

# Worst-case key budget: P2 eight, three attempts of window A, three of
# window B, then 34 P4 keys. The driver consumes a prefix; leftovers are
# never requested (host sends only on markers).
KEYS = CANONICAL_KEYS
REQUIRED_ROWS = (
    b"[INPUT] decode matrix ok",
    b"[INPUT] copy matrix pass=3 fail=8",
    b"[INPUT] read empty-again ok",
    b"[INPUT] read exact ok",
    b"[INPUT] read zero-length ok",
    b"[INPUT] read short ok",
    b"[INPUT] read hostile-scalars ok",
    b"[INPUT] read hostile-pointers ok",
    b"[INPUT] read preservation ok",
    b"[INPUT] read matrix ok",
    b"[INPUT] park matrix ok",
    b"[INPUT] lost matrix ok",
)


class InputTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual((state["cleanup"], state["returncode"]), ("monitor-quit", 0))

    def consumed_keys(self, output):
        """Replay transcript markers to the consumed key sequence.

        Indexed markers consume the positional tuple in order (P2 eight,
        then P4's 34); window markers are self-describing (name their own
        keys, attempts vary). Returns ordered names.
        """
        indexed = []
        windows = []
        for line in output.split(b"\r\n"):
            line = line.strip()
            m = re.fullmatch(rb"\[INPUT\] waiting for input=(\d+)", line)
            if m:
                self.assertEqual(int(m.group(1)), len(indexed))
                indexed.append(KEYS[len(indexed)])
                continue
            m = re.fullmatch(rb"\[INPUT\] window attempt=(\d+) keys=([a-z_,]+)", line)
            if m:
                windows.append(m.group(2).decode().split(","))
        self.assertEqual(len(indexed), 42)  # P2 eight + P4 thirty-four
        self.assertEqual(indexed[:8], list(KEYS[:8]))
        self.assertEqual(indexed[8:], ["a"] * 34)
        self.assertGreaterEqual(len(windows), 2)
        names = indexed[:8]
        for w in windows:
            names.extend(w)
        names.extend(indexed[8:])
        return names
    def payload_stream(self, output):
        out = b""
        for line in output.split(b"\r\n"):
            m = re.fullmatch(rb"\[INPUT\] payload n=(\d+) hex=([0-9a-f]*)", line.strip())
            if m:
                raw = bytes.fromhex(m.group(2).decode())
                self.assertEqual(len(raw), int(m.group(1)))
                out += raw
        return out

    def check_matrix(self, output, logs):
        self.assertIn(b"[INPUT] self-test started", output)
        self.assertIn(INPUT_VERIFIED, output)
        section = output[output.index(b"[INPUT] self-test started"):]
        self.assertEqual(validate_input_section(section), [])
        for row in REQUIRED_ROWS:
            self.assertIn(row, output)
        # Four balance checkpoints: copy, read, park, lost phases.
        self.assertEqual(output.count(b"[INPUT] accounting balanced"), 4)
        names = self.consumed_keys(output)
        wire = bytes(expected_input_bytes(names))
        # Payload order: P2 drains (16 wire bytes), park-window drains in
        # transcript order, then the P4 retained bytes (31) plus the loss
        # marker (0x00) that follows them (drop-newest honest order).
        p4wire = wire[-68:]
        self.assertEqual(len(p4wire), 68)
        expected_payload = wire[:len(wire) - 68] + p4wire[:31] + b"\x00"
        self.assertEqual(self.payload_stream(output), expected_payload)
        trace = (logs / "guest-errors.log").read_text()
        validate_input_trace(trace, names, STAGE8_KEYS)

    def test_gated_input_matrix(self):
        destination = ROOT / "build/input-tests/matrix"
        build_image(ROOT, destination, input_test=True)
        logs = destination / "logs"
        try:
            boot_image(destination / "rynoros.img", logs, timeout=55,
                       input_keys=KEYS, require_input=True)
        finally:
            self.cleanup(logs)
        output = (logs / "serial.log").read_bytes()
        self.check_matrix(output, logs)

    def mutant(self, name, edits, reason):
        """Copy-tree single-purpose mutant: apply (old, new, count) edits,
        build an input-test image, and require RED (no verified marker)."""
        with tempfile.TemporaryDirectory(prefix="input-fault-", dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for d in REQUIRED_DIRECTORIES:
                (root / d).mkdir(parents=True, exist_ok=True)
            for f in REQUIRED_FILES:
                shutil.copyfile(ROOT / f, root / f)
            for source, old, new, count in edits:
                path = root / source
                contents = path.read_text()
                self.assertEqual(contents.count(old), count, (name, source))
                path.write_text(contents.replace(old, new))
            build_image(root, input_test=True)
            logs = ROOT / "build/input-tests" / name
            try:
                with self.assertRaises(RuntimeError) as error:
                    boot_image(root / "build/rynoros.img", logs, timeout=55,
                               input_keys=KEYS, require_input=True)
            finally:
                self.cleanup(logs)
            output = (logs / "serial.log").read_bytes()
            diagnostic = str(error.exception) + output.decode("ascii", errors="replace")
            self.assertTrue(any(r in diagnostic for r in reason), diagnostic)
            self.assertNotIn(INPUT_VERIFIED, output)

    def test_mutant_no_park_goes_red(self):
        """A1: skipping the CPL3 park lets a raw CPL3 frame reach the
        scheduler handoff, which must halt instead of resuming."""
        self.mutant("no-park", [(
            "kernel/interrupts/irq.c",
            "sched_tick(frame) : sched_park_cpl3(frame)",
            "sched_tick(frame) : frame",
            1)], ("[SCHED] failure=handoff_frame", "[SCHED] failure=handoff_stack"))

    def test_mutant_pause_before_ctrl_check_goes_red(self):
        """A2: classifying 0x1D as Ctrl before the Pause check makes the
        Pause sequence alias Ctrl. The stage-8 synthetic prefix vectors
        contain E1 1D, so this mutant trips there first -- earlier RED is
        still RED (no input section ever completes)."""
        self.mutant("pause-ctrl", [(
            "kernel/drivers/keyboard.c",
            "    *out = (struct kbd_event){scan, 0, KBD_EVENT_UNKNOWN, 0};\n"
            "    if (d->pause) {",
            "    *out = (struct kbd_event){scan, 0, KBD_EVENT_UNKNOWN, 0};\n"
            "    if (scan == 0x1d || scan == 0x9d) { out->key = KBD_KEY_CTRL; return 1; }\n"
            "    if (d->pause) {",
            1)], ("[KBD] failure=decode_prefix", "[INPUT] failure=dec_pause"))

    def test_mutant_dequeue_before_validate_goes_red(self):
        """A3: dequeuing keyboard bytes before validating nread_out lets a
        hostile output pointer discard user input."""
        self.mutant("dequeue-first", [(
            "kernel/core/load.c",
            "    if (copy_dest_ok(c, nread_out, sizeof(cpu_u64)) != sizeof(cpu_u64))\n"
            "        return SYS_INVAL;",
            "    (void)nread_out;",
            1)], ("[INPUT] failure=r_preserved", "[INPUT] failure="))

    def test_mutant_user_only_copyout_goes_red(self):
        """B1: checking USER without WRITE lets copy_to_user hit RX pages.
        The RX rejection assert trips first (a second assertVerify would
        catch code modification if the first were removed)."""
        self.mutant("user-only", [(
            "kernel/core/load.c",
            "(m.permissions & (VM_USER | VM_WRITE)) != (VM_USER | VM_WRITE)",
            "(m.permissions & VM_USER) != VM_USER",
            2)], ("[INPUT] failure=copy_code_rc", "[INPUT] failure=copy_code_intact"))

    def test_mutant_first_page_only_goes_red(self):
        """B2: validating only the first output page lets cross-page and
        RX-spill writes through."""
        self.mutant("first-page", [
            ("kernel/core/load.c",
             "    if (uaddr + len < uaddr) return (cpu_u64)-1;\n    while (off < len) {",
             "    if (uaddr + len < uaddr) return (cpu_u64)-1;\n    if (off < len) {",
             1),
            ("kernel/core/load.c",
             "    if (copy_dest_ok(c, uaddr, len) != len) return (cpu_u64)-1;\n    while (off < len) {",
             "    if (copy_dest_ok(c, uaddr, len) != len) return (cpu_u64)-1;\n    if (off < len) {",
             1),
        ], ("[INPUT] failure=copy_cross", "[INPUT] failure=copy_code_intact"))

    def test_mutant_truncated_arg_goes_red(self):
        """B3: truncating fd to 32 bits aliases fd 0x1_00000000 to stdin."""
        self.mutant("trunc-arg", [(
            "kernel/core/load.c",
            "    if (fd != SYS_STDIN) return SYS_INVAL;",
            "    if ((cpu_u32)fd != SYS_STDIN) return SYS_INVAL;",
            1)], ("[INPUT] failure=r_fd64",))
