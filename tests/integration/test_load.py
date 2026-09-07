"""Stage 18b integration: executable loading and syscalls in QEMU.

One shared boot (GOOD filesystem + RYNX programs) feeds the evidence
assertions; malformed images and mutant kernels boot separately. Guest
programs really execute in CPL3 (entry/exit/write/preemption); the host
rechecks every deterministic number against the image file.
"""
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT / "tools/rynorlang"))
sys.path.insert(0, str(ROOT))
from image import build_image
from qemu import boot_image, boot_complete
from load_output import parse_serial, validate, VERIFIED_LINE
from boot_output import validate_boot_output
from test_filesystem import GOOD_ENTRIES
from fs_image import build as fs_build
from fs_output import decode as fs_decode, file_bytes as fs_file_bytes
from tools.rynorlang import program as rynor_program
from tools.host import rnyx

RNYX_PATHS = {
    "exit42": "/rnyx/exit42.rnx",
    "writehello": "/rnyx/writehello.rnx",
    "fib27": "/rnyx/fib27.rnx",
}

SOURCES = {
    "exit42": "fn main(): int { return 42; }",
    "writehello": 'fn main(): int { print("hello"); return 0; }',
    "fib27": ("fn fib(n: int): int { if n <= 1 { return n; } "
              "return fib(n - 1) + fib(n - 2); } "
              "fn main(): int { return fib(27); }"),
}

# Bad-envelope reasons in file order (must match load-test.c table).
BAD_REASONS = ["magic", "version", "arch", "header", "header", "entry",
               "code_size", "code_size", "data_size", "data_size",
               "shape", "shape", "data_size"]

SYS_PROBE_ASM = """bits 64
    mov eax, 2
    mov ebx, 1
    mov ecx, 0x400000
    mov edx, 8
    int 0x80
    cmp eax, 8
    jne .fail
    mov eax, 2
    mov ebx, 2
    mov ecx, 0x400000
    mov edx, 8
    int 0x80
    cmp eax, -1
    jne .fail
    mov eax, 2
    mov ebx, 1
    mov ecx, 0x8000
    mov edx, 8
    int 0x80
    cmp eax, -1
    jne .fail
    mov eax, 2
    mov ebx, 1
    mov rcx, 0x600000
    mov rdx, -1
    int 0x80
    cmp eax, -1
    jne .fail
    mov eax, 2
    mov ebx, 1
    mov ecx, 0x500000
    mov edx, 8
    int 0x80
    cmp eax, -1
    jne .fail
    mov eax, 2
    mov ebx, 1
    mov rcx, 0xFFFF800000000000
    mov rdx, 8
    int 0x80
    cmp eax, -1
    jne .fail
    mov eax, 0
    mov ebx, 0
    int 0x80
.fail:
    mov eax, 0
    mov ebx, 101
    int 0x80
"""


def _compile_programs(work):
    """Compile the three sources to RYNX bytes. Returns name -> bytes."""
    import os
    nasm = os.environ.get("RYNOR_NASM", "nasm")
    out = {}
    for name, src in SOURCES.items():
        progdir = work / f"prog-{name}"
        progdir.mkdir(parents=True, exist_ok=True)
        arts, error = rynor_program.build_rynor_program(src, name + ".rl", progdir, prog=name)
        assert error is None, (name, error)
        out[name] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
    # BSS envelope: P1 code with a zero-file 64-byte data tail.
    p1 = out["exit42"]
    code_len = struct.unpack("<I", p1[16:20])[0]
    code = p1[28:28 + code_len]
    out["bsszero"] = rnyx.build_envelope(code, 0, 64, b"")
    # Hostile probe: hand-assembled via NASM (absolute immediates only,
    # runs at the code base, no relocations possible by construction).
    asm_path = work / "sysprobe.asm"
    asm_path.write_text(SYS_PROBE_ASM, encoding="utf-8")
    bin_path = work / "sysprobe.bin"
    proc = subprocess.run([nasm, "-f", "bin", str(asm_path), "-o", str(bin_path)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out["sysprobe"] = rnyx.build_envelope(bin_path.read_bytes(), 0, 0, b"")
    # Malformed matrix: mutate the good exit42 envelope per class.
    for index in range(13):
        out[f"bad{index:02d}"] = _corrupt(index, out["exit42"])
    return out


def _corrupt(index, good):
    blob = bytearray(good)
    if index == 0:
        blob[0:4] = b"BAD!"
    elif index == 1:
        struct.pack_into("<H", blob, 4, 2)
    elif index == 2:
        struct.pack_into("<H", blob, 6, 2)
    elif index == 3:
        struct.pack_into("<H", blob, 8, 32)
    elif index == 4:
        struct.pack_into("<H", blob, 10, 1)
    elif index == 5:
        struct.pack_into("<I", blob, 12, 8)
    elif index == 6:
        struct.pack_into("<I", blob, 16, 0)
    elif index == 7:
        struct.pack_into("<I", blob, 16, 8192)
    elif index == 8:
        struct.pack_into("<I", blob, 20, 8192)
    elif index == 9:
        struct.pack_into("<I", blob, 20, 64)
        struct.pack_into("<I", blob, 24, 63)
    elif index == 10:
        blob = blob[:-10]
    elif index == 11:
        blob = blob + bytes(16)
    elif index == 12:
        struct.pack_into("<I", blob, 24, 8192)
    else:
        raise AssertionError(index)
    return bytes(blob)


def _mutate_copy(pairs, source):
    tmp = tempfile.TemporaryDirectory(prefix="load-fault-", dir=ROOT / "build")
    root = Path(tmp.name)
    from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
    for directory in REQUIRED_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for filename in REQUIRED_FILES:
        shutil.copyfile(ROOT / filename, root / filename)
    path = root / source
    contents = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents, encoding="utf-8")
    return tmp, root


class LoadIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/load-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        rnyx_blobs = _compile_programs(cls.work)
        entries = list(GOOD_ENTRIES)
        entries.append(("/rnyx", None))
        entries.append(("/rnyx/exit42.rnx", rnyx_blobs["exit42"]))
        entries.append(("/rnyx/writehello.rnx", rnyx_blobs["writehello"]))
        entries.append(("/rnyx/fib27.rnx", rnyx_blobs["fib27"]))
        entries.append(("/rnyx/bsszero.rnx", rnyx_blobs["bsszero"]))
        entries.append(("/rnyx/sysprobe.rnx", rnyx_blobs["sysprobe"]))
        for index in range(13):
            entries.append((f"/rnyx/bad{index:02d}.rnx", rnyx_blobs[f"bad{index:02d}"]))
        cls.image = cls.work / "load.img"
        cls.image.write_bytes(fs_build(entries))
        cls.image_bytes = cls.image.read_bytes()
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination)
        logs = cls.work / "shared-good"
        cls.output = boot_image(cls.destination / "rynoros.img", logs, timeout=60,
                                extra_drives=(cls.image,))
        summary = __import__("json").loads((logs / "run.json").read_text(encoding="utf-8"))
        assert summary["reaped"], summary

    def _files(self):
        fs = fs_decode(self.image_bytes)
        return {"/" + name: fs_file_bytes(self.image_bytes, fs, name)
                for name in fs.entries if fs.entries[name].ftype == 1}

    def test_good_load_full_evidence(self):
        self.assertEqual(validate(parse_serial(self.output), self._files()), [])

    def test_boot_output_accepts_load_section(self):
        self.assertEqual(validate_boot_output(self.output), [])

    def test_exit_rows_exact(self):
        evidence = parse_serial(self.output)
        self.assertEqual(evidence.exits,
                         [(0, 42), (0, 0), (0, 196418), (0, 42), (1, 42),
                          (0, 42), (0, 0)])

    def test_write_row_exact(self):
        evidence = parse_serial(self.output)
        self.assertEqual(len(evidence.writes), 7)
        slot, fd, length, nwritten, payload = evidence.writes[0]
        self.assertEqual((slot, fd, length, nwritten, payload), (0, 1, 5, 5, b"hello"))
        files = self._files()
        envelope = files["/rnyx/sysprobe.rnx"]
        code_len = struct.unpack("<I", envelope[16:20])[0]
        self.assertEqual(code_len, len(envelope) - 28)
        _, _, _, _, probe_payload = evidence.writes[1]
        self.assertEqual(probe_payload, envelope[28:28 + 8])
        # The probe's five hostile calls follow in program order: only
        # (fd, len, nwritten) are asserted here, the validator pins more.
        self.assertEqual(
            [(f, l, n) for _, f, l, n, _ in evidence.writes[1:]],
            [(1, 8, 8), (2, 8, 0), (1, 8, 0), (1, 0xFFFFFFFFFFFFFFFF, 0),
             (1, 8, 0), (1, 8, 0)])

    def test_reject_matrix_exact(self):
        evidence = parse_serial(self.output)
        self.assertEqual(sorted(evidence.rejects),
                         sorted((f"/rnyx/bad{i:02d}.rnx", reason)
                                for i, reason in enumerate(BAD_REASONS)))

    def test_markers_and_accounting(self):
        evidence = parse_serial(self.output)
        self.assertEqual(evidence.balanced, 8)
        self.assertEqual(len(evidence.creates), 7)
        self.assertEqual(len(evidence.destroys), 7)

    def test_completion_load_terminator(self):
        self.assertTrue(boot_complete(self.output))
        stripped = self.output.replace(VERIFIED_LINE, b"")
        self.assertFalse(boot_complete(stripped))

    def test_skip_marker_on_plain_image(self):
        plain = self.work / "plain.img"
        plain.write_bytes(fs_build(list(GOOD_ENTRIES)))
        logs = self.work / "plain-skip"
        output = boot_image(self.destination / "rynoros.img", logs, timeout=60,
                            extra_drives=(plain,))
        self.assertIn(b"[LOAD] no image, skipped", output)
        self.assertNotIn(b"[LOAD] load verified", output)
        self.assertEqual(validate_boot_output(output), [])

    def _run_load_mutation(self, pairs, source, timeout=60):
        tmp, root = _mutate_copy(pairs, source)
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / self._testMethodName
        try:
            output = boot_image(root / "build" / "img" / "rynoros.img", logs,
                                timeout=timeout, extra_drives=(self.image,))
            return output, None
        except RuntimeError as err:
            serial = (logs / "serial.log").read_bytes()
            return serial, str(err)

    def test_mut_envelope_magic_skipped(self):
        output, error = self._run_load_mutation([(
            "    if (rd32le(img) != RNYX_MAGIC) return RNYX_ERR_MAGIC;",
            "    { (void)img; }",
        )], source="kernel/core/load.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[LOAD] load verified", output)

    def test_mut_copyin_user_check_removed(self):
        output, error = self._run_load_mutation([(
            "            if (vm_query(&c->space, page, &m) != VM_OK ||\n"
            "                !(m.permissions & VM_USER))\n"
            "                return (cpu_u64)-1;",
            "            (void)page; { (void)m; }",
        )], source="kernel/core/load.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[LOAD] load verified", output)

    def test_mut_syscall_numbers_swapped(self):
        output, error = self._run_load_mutation([(
            "    if (reason == SYS_WRITE) {",
            "    if (reason == 9) {",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[LOAD] load verified", output)

    def test_mut_write_status_faked(self):
        output, error = self._run_load_mutation([(
            "        c->gprs[0] = out;",
            "        c->gprs[0] = 0;",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[LOAD] load verified", output)

    def test_mut_exit_ignored(self):
        output, error = self._run_load_mutation([(
            "        if (reason == SYS_EXIT) {\n"
            "            c->state = USER_EXITED; c->exit_code = code; ++c->gate_exits;",
            "        if (reason == SYS_EXIT) {\n"
            "            ++c->gate_exits;",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[LOAD] load verified", output)

    def test_mut_loader_maps_rwx(self):
        output, error = self._run_load_mutation([(
            "    if (vm_map(&c->space, USER_CODE_BASE, c->code_frame, VM_USER | VM_EXECUTE) != VM_OK ||",
            "    if (vm_map(&c->space, USER_CODE_BASE, c->code_frame, VM_USER | VM_WRITE | VM_EXECUTE) != VM_OK ||",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[LOAD] load verified", output)

    def test_mut_hardcoded_exit_status(self):
        output, error = self._run_load_mutation([(
            "            c->state = USER_EXITED; c->exit_code = code; ++c->gate_exits;",
            "            c->state = USER_EXITED; c->exit_code = 42; ++c->gate_exits;",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[LOAD] load verified", output)


if __name__ == "__main__":
    unittest.main()
