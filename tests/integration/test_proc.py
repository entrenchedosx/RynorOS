"""Stage 18d Slice C: process table, spawn/wait/terminate, argv, RYNX v2,
plus scoped temporary implementation mutations."""
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
from proc_output import (PROC_VERIFIED, validate_proc_section,
                         collect_load_writes)

C_SOURCES = ("p_exit42", "p_argv", "p_fault", "p_spin", "p_big",
             "p_nest", "p_selfterm", "p_alias", "p_regprobe", "p_eof")

EXIT_BLOB = bytes.fromhex("b800000000bb2a000000cd80")
# maxdata verifier: exit 42 iff the first/last qword of all 4 data pages
# are zero, else 43 (catches v2 page-offset tiling bugs in-guest).
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

REQUIRED_ROWS = (
    b"[PROC] abi pins ok",
    b"[PROC] selfterm ok",
    b"[PROC] table matrix ok",
    b"[PROC] sequential ok",
    b"[PROC] full matrix ok",
    b"[PROC] wait matrix ok",
    b"[PROC] terminate matrix ok",
    b"[PROC] fault matrix ok",
    b"[PROC] argv matrix ok",
    b"[PROC] rynx matrix ok",
    b"[PROC] owner matrix ok",
    b"[PROC] guest matrix ok",
    b"[PROC] big matrix ok",
)


def _compile_programs(work):
    """Compile Slice C guest programs to RYNX bytes. Returns name -> bytes."""
    import os
    nasm = os.environ.get("RYNOR_NASM", "nasm")
    out = {}
    for name in C_SOURCES:
        src = (ROOT / "user/proc-tests" / (name + ".c")).read_text(encoding="utf-8")
        progdir = work / f"prog-{name}"
        progdir.mkdir(parents=True, exist_ok=True)
        if name == "p_big":
            arts, error = rynor_program.build_rynor_c_program(
                {name + ".c": src}, progdir, prog=name, link_script="rynoros_v2.ld")
            assert error is None, (name, error)
            blob = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes(), version=2)
        else:
            arts, error = rynor_program.build_rynor_c_program(
                {name + ".c": src}, progdir, prog=name)
            assert error is None, (name, error)
            blob = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
        out[name] = blob
    # Real v2 multi-page proof: code and data both span two pages.
    code_len = struct.unpack("<I", out["p_big"][16:20])[0]
    data_memsz = struct.unpack("<I", out["p_big"][24:28])[0]
    assert code_len > 4096 and data_memsz > 4096, (code_len, data_memsz)
    return out


def _envelope(code: bytes, filesz: int, memsz: int, data: bytes, version: int = 2) -> bytes:
    header = (b"RYNX" + struct.pack("<HHHH", version, 1, 28, 0)
              + struct.pack("<IIII", 0, len(code), filesz, memsz))
    return header + bytes(code) + bytes(data)


class ProcIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/proc-tests"
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
        # fs-test fixtures first: the guest fs driver mounts the first
        # mountable device and requires its own files there.
        entries = list(GOOD_ENTRIES)
        entries.append(("/t", None))
        entries.append(("/t/exit42.rnx", blobs["p_exit42"]))
        for name in ("p_argv", "p_fault", "p_spin", "p_nest", "p_selfterm",
                     "p_alias", "p_regprobe", "p_eof"):
            entries.append((f"/t/{name}.rnx", blobs[name]))
        entries.append(("/t/p_big.rnx", blobs["p_big"]))
        # v1 regression envelopes (hostile + shape).
        entries.append(("/t/v1exit.rnx", blobs["p_exit42"]))
        bad = bytearray(blobs["p_exit42"])
        bad[0:4] = b"BAD!"
        entries.append(("/t/v1badmagic.rnx", bytes(bad)))
        bad = bytearray(blobs["p_exit42"])
        struct.pack_into("<H", bad, 4, 2)
        entries.append(("/t/v1badver.rnx", bytes(bad)))
        bad = bytearray(blobs["p_exit42"])
        entries.append(("/t/v1badshape.rnx", bytes(bad[:-10])))
        # v2 exact boundaries (exit-blob code + pads).
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
        cls.image = cls.work / "proc.img"
        cls.image.write_bytes(fs_build(entries))
        cls.image_bytes = cls.image.read_bytes()
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination, proc_test=True)
        logs = cls.work / "shared-good"
        cls.output = boot_image(cls.destination / "rynoros.img", logs, timeout=60,
                                extra_drives=(cls.image,), require_proc=True)
        summary = json.loads((logs / "run.json").read_text(encoding="utf-8"))
        assert summary["reaped"], summary
        cls.logs = logs

    def test_matrix_full_evidence(self):
        output = self.output
        self.assertIn(b"[PROC] self-test started", output)
        self.assertIn(PROC_VERIFIED, output)
        section = output[output.index(b"[PROC] self-test started"):]
        self.assertEqual(validate_proc_section(section), [])
        for row in REQUIRED_ROWS:
            self.assertIn(row, output)
        # Fourteen balance checkpoints (thirteen phases + final).
        self.assertEqual(output.count(b"[PROC] accounting balanced"), 14)
        # First spawn arrangement: slot 0, generation 1 exactly.
        m = re.search(rb"\[PROC\] spawn slot=(\d+) gen=(\d+)", output)
        self.assertIsNotNone(m)
        self.assertEqual((m.group(1), m.group(2)), (b"0", b"1"))
        # Selfterm exact handle is asserted in-guest; the wait row follows.
        self.assertIn(b"[PROC] wait state=1 code=0", output)
        # Fault vector for ud2 is #UD (6).
        self.assertIn(b"[PROC] wait state=2 code=6", output)

    def test_argv_goldens_exact(self):
        writes = collect_load_writes(
            self.output[self.output.index(b"[PROC] self-test started"):])
        hexes = [h for _, h in writes]
        # p_argv writes one row per argument; order across vectors is
        # fixed by the driver phase order (a1, a8, a255, a256, path+q).
        want = (["6869"]  # "hi"
                + ["61%02x" % (0x30 + i) for i in range(8)]  # a0..a7
                + ["58" * 254]  # 254 X
                + ["59" * 255]  # 255 Y
                + ["2f742f705f617267762e726e78", "71"]  # /t/p_argv.rnx, then q
                + ["6269676f6b"])  # bigok
        pos = 0
        for golden in want:
            try:
                pos = hexes.index(golden, pos) + 1
            except ValueError:
                self.fail("argv golden missing in order: %r" % golden[:32])

    def _mutant(self, name, edits, reasons):
        with tempfile.TemporaryDirectory(prefix="proc-fault-", dir=ROOT / "build") as tmp:
            root = Path(tmp)
            from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            for source, old, new, count in edits:
                path = root / source
                contents = path.read_text(encoding="utf-8")
                self.assertEqual(contents.count(old), count, (name, source))
                path.write_text(contents.replace(old, new), encoding="utf-8")
            build_image(root, proc_test=True)
            logs = ROOT / "build/proc-tests" / name
            try:
                with self.assertRaises(RuntimeError) as error:
                    boot_image(root / "build/rynoros.img", logs, timeout=60,
                               extra_drives=(self.image,), require_proc=True)
            finally:
                state = json.loads((logs / "run.json").read_text())
                self.assertTrue(state["reaped"])
            output = (logs / "serial.log").read_bytes()
            diagnostic = str(error.exception) + output.decode("ascii", errors="replace")
            self.assertTrue(any(r in diagnostic for r in reasons), diagnostic)
            self.assertNotIn(PROC_VERIFIED, output)

    def test_mutant_stale_generation_accepted_goes_red(self):
        """C-M1: skipping generation equality lets a stale handle alias
        a reused slot."""
        self._mutant("stale-gen", [(
            "kernel/core/proc.c",
            "    if (slots[slot].gen != gen) return 0;",
            "    (void)gen;",
            1)], ("[PROC] failure=tab_stale",))

    def test_mutant_consume_before_copyout_goes_red(self):
        """C-M2: consuming before output publication loses terminal status
        on hostile pointers (plus softened backstop)."""
        self._mutant("consume-first", [
            ("kernel/core/proc.c",
             "    if (status_out + sizeof(staged) < status_out) return SYS_BADARG;\n"
             "    if (copy_dest_ok(caller, status_out, sizeof(staged)) != sizeof(staged))\n"
             "        return SYS_BADARG;",
             "    (void)status_out;",
             1),
            ("kernel/core/proc.c",
             '        panic("wait_publish");',
             "        return SYS_INVAL;",
             1),
        ], ("[PROC] failure=guest_reg_wait",))

    def test_mutant_second_wait_succeeds_goes_red(self):
        """C-M3: failing to recycle the generation lets a consumed handle
        alias the next occupant."""
        self._mutant("gen-keep", [(
            "kernel/core/proc.c",
            "    if (++s->gen == 0) ++s->gen;\n    return 1;\n}",
            "    return 1;\n}",
            1)], ("[PROC] failure=tab_gen_bump",))

    def test_mutant_partial_admission_goes_red(self):
        """C-M4: forcing the create-failure branch without recycling the
        slot fails admission immediately (the rollback path is
        load-bearing; without the recycle the slot would additionally
        wedge as LOADING, which proc_check tolerates only transiently)."""
        self._mutant("no-rollback", [(
            "kernel/core/proc.c",
            "    if (!user_create_loaded(&c, (const char *)(img + lay.code_off), lay.code_len,\n"
            "                            (const char *)(img + lay.data_off), lay.data_filesz,\n"
            "                            lay.data_memsz)) {\n"
            "        slots[slot].state = PL_FREE;\n"
            "        return SYS_NOMEM;\n"
            "    }",
            "    if (!user_create_loaded(&c, (const char *)(img + lay.code_off), lay.code_len,\n"
            "                            (const char *)(img + lay.data_off), lay.data_filesz,\n"
            "                            lay.data_memsz) || 1) {\n"
            "        return SYS_NOMEM;\n"
            "    }",
            1)], ("[PROC] failure=self_spawn",))

    def test_mutant_nul_scan_removed_goes_red(self):
        """C-M5: dropping staged NUL validation admits malformed argv."""
        self._mutant("nul-scan", [(
            "kernel/core/proc.c",
            "        for (cpu_u64 k = 0; k < len; ++k)\n"
            "            if (dst[k] == 0) return SYS_BADARG;",
            "        (void)dst;",
            1)], ("[PROC] failure=guest_reg_wait",))

    def test_mutant_rsp_shift_goes_red(self):
        """C-M6: shifting the argv RSP breaks the 16-alignment contract
        enforced at build time (trips on the very first spawn, whose
        empty block is also shifted)."""
        self._mutant("rsp-shift", [(
            "kernel/core/user.c",
            "        c->rsp = USER_STACK_TOP - total;",
            "        c->rsp = USER_STACK_TOP - total + 8;",
            1)], ("[PROC] failure=self_spawn",))

    def test_mutant_terminate_marks_aborted_goes_red(self):
        """C-M7: marking ABORTED without the kill flag leaves a live bound
        worker behind; the join bound trips."""
        self._mutant("mark-abort", [(
            "kernel/core/proc.c",
            "    s->kill_requested = 1;",
            "    s->state = PL_ABORTED;",
            1)], ("[PROC] failure=",))

    def test_mutant_rynx_cap_removed_goes_red(self):
        """C-M8: removing the v2 code cap admits oversized images past
        validation (caught at the create backstop with the wrong code)."""
        self._mutant("cap-removed", [(
            "kernel/core/load.c",
            "        code_max = RNYX_V2_CODE_MAX;",
            "        code_max = (cpu_u64)-1;",
            1)], ("[PROC] failure=rynx_reject",))
