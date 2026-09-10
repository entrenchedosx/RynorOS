"""Stage 18d Slice D: stateless fread, discovery, pipes, spawn_pipe,
true concurrent streaming, plus scoped temporary implementation
mutations."""
import json
import re
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
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from fs_image import build as fs_build
from tools.rynorlang import program as rynor_program
from tools.host import rnyx
from test_filesystem import GOOD_ENTRIES
from pipe_output import (FREAD_VERIFIED, PIPE_VERIFIED,
                         validate_fread_section, validate_pipe_section,
                         collect_load_writes, collect_transfers,
                         extract_fread_section, extract_pipe_section)

# Slice C programs (the /t/ image keeps every lifecycle dependency so
# the proc phases also run on this image).
C_SOURCES = ("p_exit42", "p_argv", "p_fault", "p_spin", "p_big",
             "p_nest", "p_selfterm", "p_alias", "p_regprobe", "p_eof")
# Slice D programs (producer/consumer/faulters/probes).
D_SOURCES = ("p_prod", "p_cons", "p_fprod", "p_fcons",
             "p_freadprobe", "p_pipeprobe", "p_discprobe", "p_rxread")

EXIT_BLOB = bytes.fromhex("b800000000bb2a000000cd80")
MAXDATA_ASM = """bits 64
    mov rax, [0x600000]
    or rax, [0x600ff8]
    or rax, [0x601000]
    or rax, [0x601ff8]
    or rax, [0x602000]
    or rax, [0x602ff8]
    or rax, [0x603000]
    or rax, [0x603ff8]
    test rax, rax
    jnz .bad
    mov eax, 0
    mov ebx, 42
    int 0x80
    ud2
.bad:
    mov eax, 0
    mov ebx, 43
    int 0x80
    ud2
"""

HELLO = b"slice-d fread probe line one\nslice-d fread probe line two\n"
BIG = bytes((i * 13 + 0x41) & 0xFF for i in range(20000))

REQUIRED_FREAD_ROWS = (
    b"[FREAD] abi pins ok",
    b"[FREAD] basic ok",
    b"[FREAD] bounds ok",
    b"[FREAD] probe ok",
    b"[FREAD] discovery probe ok",
    b"[FREAD] discovery matrix ok",
)
REQUIRED_PIPE_ROWS = (
    b"[PIPE] abi pins ok",
    b"[PIPE] pipe invariants ok",
    b"[PIPE] small stream ok",
    b"[PIPE] wraparound ok",
    b"[PIPE] oversized full ok",
    b"[PIPE] oversized empty ok",
    b"[PIPE] empty-live ok",
    b"[PIPE] terminal eof ok",
    b"[PIPE] eof-before-wait ok",
    b"[PIPE] hostile read ok",
    b"[PIPE] syscall probe ok",
    b"[PIPE] rollback matrix ok",
    b"[PIPE] thread exhaustion ok",
    b"[PIPE] interaction matrix ok",
    b"[PIPE] reuse ok",
    b"[PIPE] tick observability ok",
)


def _compile_programs(work):
    """Compile Slice C + D guest programs to RYNX bytes."""
    import os
    out = {}
    rtpipe = (ROOT / "user/lib/rt/rt_pipe.h").read_text(encoding="utf-8")
    for name in C_SOURCES + D_SOURCES:
        src = (ROOT / "user/proc-tests" / (name + ".c")).read_text(encoding="utf-8")
        progdir = work / f"prog-{name}"
        progdir.mkdir(parents=True, exist_ok=True)
        if name == "p_big":
            arts, error = rynor_program.build_rynor_c_program(
                {name + ".c": src, "rt_pipe.h": rtpipe}, progdir, prog=name,
                link_script="rynoros_v2.ld")
            assert error is None, (name, error)
            blob = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes(), version=2)
        else:
            arts, error = rynor_program.build_rynor_c_program(
                {name + ".c": src, "rt_pipe.h": rtpipe}, progdir, prog=name)
            assert error is None, (name, error)
            blob = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
        out[name] = blob
    code_len = struct.unpack("<I", out["p_big"][16:20])[0]
    data_memsz = struct.unpack("<I", out["p_big"][24:28])[0]
    assert code_len > 4096 and data_memsz > 4096, (code_len, data_memsz)
    return out


def _envelope(code: bytes, filesz: int, memsz: int, data: bytes, version: int = 2) -> bytes:
    header = (b"RYNX" + struct.pack("<HHHH", version, 1, 28, 0)
              + struct.pack("<IIII", 0, len(code), filesz, memsz))
    return header + bytes(code) + bytes(data)


class PipeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/pipe-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        blobs = _compile_programs(cls.work)
        import os
        nasm = os.environ.get("RYNOR_NASM", "nasm")
        asm_path = cls.work / "maxdata.asm"
        asm_path.write_text(MAXDATA_ASM, encoding="utf-8")
        bin_path = cls.work / "maxdata.bin"
        proc = subprocess.run([nasm, "-f", "bin", str(asm_path), "-o", str(bin_path)],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr[-2000:]
        maxdata_code = bin_path.read_bytes()
        entries = list(GOOD_ENTRIES)
        entries.append(("/t", None))
        entries.append(("/t/exit42.rnx", blobs["p_exit42"]))
        for name in ("p_argv", "p_fault", "p_spin", "p_nest", "p_selfterm",
                     "p_alias", "p_regprobe", "p_eof"):
            entries.append((f"/t/{name}.rnx", blobs[name]))
        entries.append(("/t/p_big.rnx", blobs["p_big"]))
        entries.append(("/t/v1exit.rnx", blobs["p_exit42"]))
        bad = bytearray(blobs["p_exit42"])
        bad[0:4] = b"BAD!"
        entries.append(("/t/v1badmagic.rnx", bytes(bad)))
        bad = bytearray(blobs["p_exit42"])
        struct.pack_into("<H", bad, 4, 2)
        entries.append(("/t/v1badver.rnx", bytes(bad)))
        bad = bytearray(blobs["p_exit42"])
        entries.append(("/t/v1badshape.rnx", bytes(bad[:-10])))
        entries.append(("/t/maxcode.rnx", _envelope(EXIT_BLOB + bytes(32768 - 12), 0, 0, b"")))
        entries.append(("/t/maxcode1.rnx", _envelope(EXIT_BLOB + bytes(32769 - 12), 0, 0, b"")))
        entries.append(("/t/maxdata.rnx", _envelope(maxdata_code, 0, 16384, b"")))
        entries.append(("/t/maxdata1.rnx", _envelope(maxdata_code, 0, 16385, b"")))
        for iname, mutate in (("badv2ver", lambda b: struct.pack_into("<H", b, 4, 3)),
                              ("badv2rsv", lambda b: struct.pack_into("<H", b, 10, 1)),
                              ("badv2entry", lambda b: struct.pack_into("<I", b, 12, 8)),
                              ("badv2code0", lambda b: struct.pack_into("<I", b, 16, 0)),
                              ("badv2codeovf",
                               lambda b: struct.pack_into("<I", b, 16, 0xFFFFFFFF))):
            bad = bytearray(_envelope(EXIT_BLOB, 0, 0, b""))
            mutate(bad)
            entries.append((f"/t/{iname}.rnx", bytes(bad)))
        # Slice D /bin/ executables (absolute + bare-name discovery).
        # (GOOD_ENTRIES already carries the /bin directory itself.)
        entries.append(("/bin/ok.rnx", blobs["p_exit42"]))
        badbin = bytearray(blobs["p_exit42"])
        badbin[0:4] = b"BAD!"
        entries.append(("/bin/bad.rnx", bytes(badbin)))
        entries.append(("/bin/" + "c" * 27, blobs["p_exit42"]))
        entries.append(("/bin/d", None))
        for name in D_SOURCES:
            entries.append((f"/bin/{name}.rnx", blobs[name]))
        # Slice D /f/ data files.
        entries.append(("/f", None))
        entries.append(("/f/hello.txt", HELLO))
        entries.append(("/f/empty.txt", b""))
        entries.append(("/f/big.bin", BIG))
        entries.append(("/f/bad.rnx", bytes(badbin)))
        cls.image = cls.work / "pipe.img"
        cls.image.write_bytes(fs_build(entries))
        cls.image_bytes = cls.image.read_bytes()
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination, proc_test=True, pipe_test=True)
        logs = cls.work / "shared-good"
        cls.output = boot_image(cls.destination / "rynoros.img", logs, timeout=60,
                                extra_drives=(cls.image,), require_pipe=True)
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        assert summary["reaped"], summary
        cls.logs = logs

    def test_matrix_full_evidence(self):
        output = self.output
        self.assertIn(b"[PROC] proc verified", output)
        fpart = extract_fread_section(output)
        ppart = extract_pipe_section(output)
        self.assertIsNotNone(fpart)
        self.assertIsNotNone(ppart)
        self.assertEqual(validate_fread_section(output), [])
        self.assertEqual(validate_pipe_section(output), [])
        for row in REQUIRED_FREAD_ROWS:
            self.assertIn(row, output)
        for row in REQUIRED_PIPE_ROWS:
            self.assertIn(row, output)
        self.assertIn(FREAD_VERIFIED, output)
        self.assertIn(PIPE_VERIFIED, output)
        self.assertEqual(fpart.count(b"[FREAD] accounting balanced"), 5)
        self.assertEqual(ppart.count(b"[PIPE] accounting balanced"), 27)

    def test_fread_echo_goldens(self):
        fpart = extract_fread_section(self.output)
        writes = collect_load_writes(fpart)
        hexes = [h for _, h in writes]
        self.assertEqual(hexes, [HELLO.hex(), BIG[:64].hex()])

    def test_transfer_evidence(self):
        ppart = extract_pipe_section(self.output)
        transfers = collect_transfers(ppart)
        by_name = {}
        for t in transfers:
            by_name.setdefault(t["name"], []).append(t)
        # Wraparound: one 8192-byte stream turns the 4096 ring exactly
        # twice per direction, with forced scheduler overlap.
        self.assertIn("wrap", by_name)
        wrap = by_name["wrap"][0]
        self.assertEqual((wrap["bytes"], wrap["turns"], wrap["overlap"]),
                         (8192, 4, 1))
        # Oversized streams: genuine backpressure in both directions
        # (throttled peer per direction, deterministic).
        sfull = by_name["stream_full"][0]
        self.assertEqual(sfull["bytes"], 8192)
        self.assertGreaterEqual(sfull["full"], 1)
        sempty = by_name["stream_empty"][0]
        self.assertEqual(sempty["bytes"], 8192)
        self.assertGreaterEqual(sempty["empty"], 1)
        # Ten rotating-seed reuse iterations, all byte-exact.
        self.assertEqual(len(re.findall(rb"\[PIPE\] reuse iter=\d+", ppart)), 10)

    def _mutant(self, name, edits, reasons, fread_verified=True):
        with tempfile.TemporaryDirectory(prefix="pipe-fault-", dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            for source, old, new, count in edits:
                path = root / source
                contents = path.read_text(encoding="utf-8")
                self.assertEqual(contents.count(old), count, (name, source))
                path.write_text(contents.replace(old, new), encoding="utf-8")
            # Pipe-only images (proc phases skipped): every mutant
            # marker lives in the FREAD/PIPE sections, and the shorter
            # boot keeps RED latencies far from the deadline.
            build_image(root, root / "build" / "img", proc_test=False, pipe_test=True)
            logs = ROOT / "build/pipe-tests" / name
            try:
                with self.assertRaises(RuntimeError) as error:
                    boot_image(root / "build/img/rynoros.img", logs, timeout=60,
                               extra_drives=(self.image,), require_pipe=True)
            finally:
                state = json.loads((logs / "run.json").read_text())
                self.assertTrue(state["reaped"])
            output = (logs / "serial.log").read_bytes()
            diagnostic = str(error.exception) + output.decode("ascii", errors="replace")
            self.assertTrue(any(r in diagnostic for r in reasons), diagnostic)
            self.assertNotIn(PIPE_VERIFIED, output)
            if fread_verified:
                self.assertIn(FREAD_VERIFIED, output)
            else:
                self.assertNotIn(FREAD_VERIFIED, output)

    def test_mutant_fread_no_wbit_goes_red(self):
        """D-M1: dropping the USER+WRITE pre-check on the fread target
        still rejects RX (copyout is two-pass) but with the wrong class
        (INVAL, not BADARG), so the hostile matrix goes RED."""
        self._mutant("fread-no-wbit", [(
            "kernel/core/load.c",
            "    if (copy_dest_ok(c, buf, len) != len) return SYS_BADARG;",
            "    if (copy_dest_ok(c, buf, 0) != 0) return SYS_BADARG;",
            1)], ("[FREAD] failure=freadprobe_wait",), fread_verified=False)

    def test_mutant_user_path_to_fs_goes_red(self):
        """D-M2: handing the raw userspace pathname to the filesystem
        faults on the kernel address space (no staging); the guest
        never returns, so the boot times out with no VERIFIED marks."""
        self._mutant("user-path", [
            ("kernel/core/load.c",
             "    fread_path[path_len] = 0;\n"
             "    if (!fs_path_ok((const char *)fread_path)) return SYS_BADARG;",
             "    (void)fread_path;\n"
             "    if (!fs_path_ok((const char *)path_ptr)) return SYS_BADARG;",
             1),
            ("kernel/core/load.c",
             "        int rc = kern_fread((const char *)fread_path, offset, read_stage,\n"
             "                            0, &m);",
             "        int rc = kern_fread((const char *)path_ptr, offset, read_stage,\n"
             "                            0, &m);",
             1),
            ("kernel/core/load.c",
             "        int rc = kern_fread((const char *)fread_path, offset + done,\n"
             "                            read_stage, chunk, &m);",
             "        int rc = kern_fread((const char *)path_ptr, offset + done,\n"
             "                            read_stage, chunk, &m);",
             1),
        ], ("timed out",), fread_verified=False)

    def test_mutant_collapsed_image_classes_goes_red(self):
        """D-M3: mapping non-file executables to NOTFOUND collapses the
        frozen distinction on both spawn paths; the directory-spawn
        rows go RED first in the discovery probe."""
        self._mutant("collapsed-classes", [
            ("kernel/core/proc.c",
             "    if (fs_stat(path, &st) != FS_OK) return SYS_NOTFOUND;\n"
             "    if (st.type != FS_TYPE_FILE) return SYS_MALFORMED;",
             "    if (fs_stat(path, &st) != FS_OK) return SYS_NOTFOUND;\n"
             "    if (st.type != FS_TYPE_FILE) return SYS_NOTFOUND;",
             1),
            ("kernel/core/proc.c",
             "    if (fs_stat((const char *)resolved_path, &st) != FS_OK) return SYS_NOTFOUND;\n"
             "    if (st.type != FS_TYPE_FILE) return SYS_MALFORMED;",
             "    if (fs_stat((const char *)resolved_path, &st) != FS_OK) return SYS_NOTFOUND;\n"
             "    if (st.type != FS_TYPE_FILE) return SYS_NOTFOUND;",
             1),
        ], ("[FREAD] failure=dp_wait",), fread_verified=False)

    def test_mutant_partial_admission_goes_red(self):
        """D-M4: admitting A while B fails strands a visible child; the
        rollback census goes RED with exactly one live slot."""
        self._mutant("partial-admit", [(
            "kernel/core/proc.c",
            "    rc = prepare_side(slot_b, img, img_len, arg_ptrs_b, arg_lens_b, nargs_b);\n"
            "    if (heap_free(img) != HEAP_OK) panic(\"pipe_img\");\n"
            "    img = 0;\n"
            "    if (rc != SYS_OK) {\n"
            "        (void)pipe_ring_free(ring);\n"
            "        rollback_slot(slot_a);\n"
            "        rollback_slot(slot_b);\n"
            "        return rc;\n"
            "    }",
            "    rc = prepare_side(slot_b, img, img_len, arg_ptrs_b, arg_lens_b, nargs_b);\n"
            "    if (heap_free(img) != HEAP_OK) panic(\"pipe_img\");\n"
            "    img = 0;\n"
            "    if (rc != SYS_OK) {\n"
            "        (void)pipe_ring_free(ring);\n"
            "        slots[slot_a].state = PL_ACTIVE;\n"
            "        *handle_a_out = (cpu_u64)slot_a | (slots[slot_a].gen << 32);\n"
            "        rollback_slot(slot_b);\n"
            "        return rc;\n"
            "    }",
            1)], ("[PIPE] failure=rb_nochild",))

    def test_mutant_unbounded_pipe_goes_red(self):
        """D-M5: a 16 KiB ring (frozen 4096 cap intact everywhere else)
        swallows the 8192-byte proof transfer without ever filling or
        turning, so the quantitative backpressure gates go RED while
        bytes stay exact."""
        self._mutant("unbounded", [
            ("kernel/core/pipe.c",
             "    if (heap_alloc(PIPE_CAP, 8, &p) != HEAP_OK) return 0;",
             "    if (heap_alloc(16384, 8, &p) != HEAP_OK) return 0;",
             1),
            ("kernel/core/pipe.c",
             "    for (i = 0; i < PIPE_CAP; ++i) ((cpu_u8 *)p)[i] = 0;",
             "    for (i = 0; i < 16384; ++i) ((cpu_u8 *)p)[i] = 0;",
             1),
            ("kernel/core/pipe.c",
             "    if (count > PIPE_CAP) return 0;",
             "    if (count > 16384) return 0;",
             1),
            ("kernel/core/pipe.c",
             "    if (rpos >= PIPE_CAP || wpos >= PIPE_CAP) return 0;",
             "    if (rpos >= 16384 || wpos >= 16384) return 0;",
             1),
            ("kernel/core/pipe.c",
             "    space = PIPE_CAP - count;",
             "    space = 16384 - count;",
             1),
            ("kernel/core/pipe.c",
             "    if (count > PIPE_CAP) return (cpu_u64)-2;",
             "    if (count > 16384) return (cpu_u64)-2;",
             1),
            ("kernel/core/pipe.c",
             "    if (count > PIPE_CAP) return SYS_INVAL;",
             "    if (count > 16384) return SYS_INVAL;",
             1),
            ("kernel/core/pipe.c",
             "        ring[wpos++] = src[i];\n"
             "        if (wpos == PIPE_CAP) {",
             "        ring[wpos++] = src[i];\n"
             "        if (wpos == 16384) {",
             1),
            ("kernel/core/pipe.c",
             "        dst[i] = ring[(rpos + i) % PIPE_CAP];",
             "        dst[i] = ring[(rpos + i) % 16384];",
             1),
            ("kernel/core/pipe.c",
             "    if (n > count || n > PIPE_CAP || rpos >= PIPE_CAP) return 0;\n"
             "    for (i = 0; i < n; ++i) {\n"
             "        if (++rpos == PIPE_CAP) {",
             "    if (n > count || n > 16384 || rpos >= 16384) return 0;\n"
             "    for (i = 0; i < n; ++i) {\n"
             "        if (++rpos == 16384) {",
             1),
        ], ("[PIPE] failure=wrap_turns",))

    def test_mutant_sequential_pipeline_goes_red(self):
        """D-M6: refusing reads while the writer lives serializes the
        pipeline into a deadlock (the stalled producer can never be
        drained); the transfer bound goes RED."""
        self._mutant("sequential", [(
            "kernel/core/pipe.c",
            "    if (count > PIPE_CAP) return SYS_INVAL;\n"
            "    if (!count) {",
            "    if (count > PIPE_CAP) return SYS_INVAL;\n"
            "    if (writer_open) {\n"
            "        ++empty_stalls;\n"
            "        return SYS_AGAIN;\n"
            "    }\n"
            "    if (!count) {",
            1)], ("[PIPE] failure=wrap_wait",))

    def test_mutant_wrap_bug_goes_red(self):
        """D-M7: reading past the ring end without modulo hands the
        consumer heap garbage after the first turn; the byte-exact
        check goes RED."""
        self._mutant("wrap-bug", [(
            "kernel/core/pipe.c",
            "        dst[i] = ring[(rpos + i) % PIPE_CAP];",
            "        dst[i] = ring[rpos + i];",
            1)], ("[PIPE] failure=wrap_wait",))

    def test_mutant_zombie_holds_writer_goes_red(self):
        """D-M8: a natural exit that records the process but never
        closes the writer endpoint leaves every consumer on the AGAIN
        path forever (EOF becomes unreachable before any wait); the
        first transfer wedges and goes RED."""
        self._mutant("zombie-writer", [(
            "kernel/core/proc.c",
            "    s->state = terminal;\n"
            "    if (terminal == PL_EXITED) {\n"
            "        s->exit_code = c->exit_code;\n"
            "    } else {\n"
            "        s->fault_vector = c->fault_vector;\n"
            "        s->fault_error = c->fault_error;\n"
            "    }\n"
            "    if (!pipe_note_terminal(slot, s->gen)) panic(\"gate_pipe\");",
            "    s->state = terminal;\n"
            "    if (terminal == PL_EXITED) {\n"
            "        s->exit_code = c->exit_code;\n"
            "    } else {\n"
            "        s->fault_vector = c->fault_vector;\n"
            "        s->fault_error = c->fault_error;\n"
            "        if (!pipe_note_terminal(slot, s->gen)) panic(\"gate_pipe\");\n"
            "    }",
            1)], ("[PIPE] failure=inv_wait",))

    def test_mutant_early_dequeue_goes_red(self):
        """D-M9: dequeuing before successful userspace publication plus
        no output pre-check loses the staged prefix on a hostile
        destination; the follow-up stream arrives shifted and goes RED."""
        self._mutant("early-dequeue", [
            ("kernel/core/load.c",
             "                if (buf + len < buf) return SYS_INVAL;\n"
             "                if (copy_dest_ok(c, buf, len) != len) return SYS_INVAL;",
             "                if (buf + len < buf) return SYS_INVAL;",
             1),
            ("kernel/core/load.c",
             "                if (n > 0) {\n"
             "                    if (copy_to_user(c, buf, read_stage, n) != n)\n"
             "                        return SYS_INVAL;\n"
             "                }\n"
             "                if (copy_to_user(c, nread_out, (const cpu_u8 *)&n,\n"
             "                                 sizeof(n)) != sizeof(n))\n"
             "                    return SYS_INVAL;\n"
             "                if (n > 0 && !pipe_read_commit(n)) return SYS_INVAL;\n"
             "                return SYS_OK;",
             "                if (n > 0 && !pipe_read_commit(n)) return SYS_INVAL;\n"
             "                if (n > 0) {\n"
             "                    if (copy_to_user(c, buf, read_stage, n) != n)\n"
             "                        return SYS_INVAL;\n"
             "                }\n"
             "                if (copy_to_user(c, nread_out, (const cpu_u8 *)&n,\n"
             "                                 sizeof(n)) != sizeof(n))\n"
             "                    return SYS_INVAL;\n"
             "                return SYS_OK;",
             1),
        ], ("[PIPE] failure=rx_wait",))

    def test_mutant_drop_on_full_goes_red(self):
        """D-M10: acknowledging unwritten bytes on a full pipe shifts
        the producer past the consumer; the byte-exact check on the
        forced-full run goes RED."""
        self._mutant("drop-on-full", [(
            "kernel/core/pipe.c",
            "        ++full_stalls;\n"
            "        return 0;",
            "        ++full_stalls;\n"
            "        return len;",
            1)], ("[PIPE] failure=sf_wait",))

    def test_mutant_stale_ownership_goes_red(self):
        """D-M11: recording zero endpoint generations destroys pipe
        authority at admission; the attach invariant fails closed."""
        self._mutant("stale-owner", [(
            "kernel/core/pipe.c",
            "    wslot = wslot_in;\n"
            "    wgen = wgen_in;\n"
            "    rslot = rslot_in;\n"
            "    rgen = rgen_in;",
            "    wslot = wslot_in;\n"
            "    wgen = 0;\n"
            "    rslot = rslot_in;\n"
            "    rgen = 0;",
            1)], ("[PIPE] failure=attach_check",))

    def test_mutant_leaked_ring_rollback_goes_red(self):
        """D-M12: skipping the ring free on a second-half admission
        failure leaks exactly one ring; the first balance gate after
        a failing second half (the probe's) goes RED."""
        self._mutant("leaked-ring", [(
            "kernel/core/proc.c",
            "    rc = fetch_image(path_b, &img, &img_len);\n"
            "    if (rc != SYS_OK) {\n"
            "        (void)pipe_ring_free(ring);\n"
            "        rollback_slot(slot_a);\n"
            "        rollback_slot(slot_b);\n"
            "        return rc;\n"
            "    }",
            "    rc = fetch_image(path_b, &img, &img_len);\n"
            "    if (rc != SYS_OK) {\n"
            "        rollback_slot(slot_a);\n"
            "        rollback_slot(slot_b);\n"
            "        return rc;\n"
            "    }",
            1)], ("[PIPE] failure=pp_balance",))
