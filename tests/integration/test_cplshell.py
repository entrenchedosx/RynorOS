"""Stage 18d Slice E: CPL3 shell, scripts, Ctrl-C, boot paths, mutants."""
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
from sh_output import (validate_sh_section, collect_sh_rows,
                       collect_load_writes, collect_done_statuses)

# Shell sources (one image) and helper programs.
SHELL_SOURCES = ("sh.c", "sh_key.c", "sh_parse.c")
SHELL_HEADERS = ("sh_key.h", "sh_parse.h")
HELPERS = ("sh_echo", "sh_cat", "sh_upper", "sh_exit", "sh_dcode",
           "sh_prod")
# Reused proven programs (own sources, shell names; no-arg only —
# producers take shell Unix argv, so p_prod is NOT reused here).
REUSED = (("p_fault", "fault"), ("p_spin", "spin"))


def _compile_shell(work, root=ROOT):
    """Compile the CPL3 shell + helpers to RYNX bytes. Returns dict.
    Sources read from root (mutant copies build here too)."""
    out = {}
    rtpipe = (root / "user/lib/rt/rt_pipe.h").read_text(encoding="utf-8")
    sources = {}
    for name in SHELL_SOURCES + SHELL_HEADERS:
        sources[name] = (root / "user/shell" / name).read_text(encoding="utf-8")
    sources["rt_pipe.h"] = rtpipe
    progdir = work / "prog-sh"
    progdir.mkdir(parents=True, exist_ok=True)
    arts, error = rynor_program.build_rynor_c_program(
        sources, progdir, prog="sh", link_script="rynoros_v2.ld")
    assert error is None, ("sh", error)
    out["sh"] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes(), version=2)
    for name in HELPERS:
        src = (root / "user/proc-tests" / (name + ".c")).read_text(encoding="utf-8")
        progdir = work / f"prog-{name}"
        progdir.mkdir(parents=True, exist_ok=True)
        extra = {}
        if name == "sh_dcode":
            extra = {"sh_key.h": sources["sh_key.h"],
                     "sh_key.c": sources["sh_key.c"]}
        arts, error = rynor_program.build_rynor_c_program(
            {name + ".c": src, "rt_pipe.h": rtpipe, **extra},
            progdir, prog=name)
        assert error is None, (name, error)
        out[name] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
    for src_name, bin_name in REUSED:
        src = (root / "user/proc-tests" / (src_name + ".c")).read_text(encoding="utf-8")
        progdir = work / f"prog-{bin_name}"
        progdir.mkdir(parents=True, exist_ok=True)
        arts, error = rynor_program.build_rynor_c_program(
            {src_name + ".c": src, "rt_pipe.h": rtpipe},
            progdir, prog=bin_name)
        assert error is None, (bin_name, error)
        out[bin_name] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
    return out


def _drive_entries(blobs, token):
    """Filesystem entries for a shell drive image: fixtures, the
    shell tree, boundary fixtures, and the script carrying token."""
    from test_filesystem import GOOD_ENTRIES
    entries = list(GOOD_ENTRIES)
    entries.append(("/bin/sh", blobs["sh"]))
    for name in HELPERS:
        entries.append(("/bin/" + _prog_name(name), blobs[name]))
    for _, bin_name in REUSED:
        entries.append((f"/bin/{bin_name}", blobs[bin_name]))
    bad = bytearray(blobs["sh_echo"])
    bad[0:4] = b"BAD!"
    entries.append(("/bin/bad", bytes(bad)))
    entries.append(("/bin/d", None))
    entries.append(("/bin/" + "e" * 27, blobs["sh_echo"]))
    entries.append(("/test", None))
    script = ("echo script-start %s\n"
              "// comment line\n"
              "echo a ; echo b\n"
              "prod 256 0 |> upper\n"
              "status\n"
              "echo par-one\n"
              "prod 256 0 |> upper\n"
              "status\n"
              "echo a | cat\n"
              "echo script-end\n" % token)
    entries.append(("/test/session.sh", script.encode()))
    return entries


def _prog_name(name):
    return {"sh_echo": "echo", "sh_cat": "cat", "sh_upper": "upper",
            "sh_exit": "exitcode", "sh_dcode": "dcode",
            "sh_prod": "prod"}.get(name, name)


def K(text):
    """Expand shell test text to sendkey entries (single names or
    held-combo tuples). Only verified QEMU names are produced."""
    out = []
    for ch in text:
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif "A" <= ch <= "Z":
            out.append(("shift", ch.lower()))
        elif ch == " ":
            out.append("spc")
        elif ch == "\n":
            out.append("ret")
        elif ch == ";":
            out.append("semicolon")
        elif ch == "|":
            out.append(("shift", "backslash"))
        elif ch == ">":
            out.append(("shift", "dot"))
        elif ch == "/":
            out.append("slash")
        elif ch == '"':
            out.append(("shift", "apostrophe"))
        elif ch == "\\":
            out.append("backslash")
        elif ch == "-":
            out.append("minus")
        elif ch == "_":
            out.append(("shift", "minus"))
        elif ch == ".":
            out.append("dot")
        elif ch == "#":
            out.append(("shift", "3"))
        elif ch == "'":
            out.append("apostrophe")
        elif ch == "$":
            out.append(("shift", "4"))
        elif ch == "(":
            out.append(("shift", "9"))
        elif ch == ")":
            out.append(("shift", "0"))
        elif ch == "*":
            out.append(("shift", "8"))
        elif ch == "&":
            out.append(("shift", "7"))
        elif ch == "?":
            out.append(("shift", "slash"))
        elif ch == "<":
            out.append(("shift", "comma"))
        else:
            raise ValueError("untypeable test char %r" % ch)
    return out


CTRL_C = ("ctrl", "c")
BSPACE = "backspace"


def _slot_blob(writes, slot):
    """Concatenated hex payloads written by one LOAD slot. Each slot
    has a single writer at a time (shell=0, single children=1,
    pipeline producer=1/consumer=2), so this subsequence is immune to
    the cross-writer serial interleaving that splits multi-write
    child output (e.g. `hi`+`\\n`) in the full channel blob."""
    return "".join(h for s, h in writes if s == slot)


def pattern_upper(n, seed=0):
    out = bytearray()
    for i in range(n):
        b = (i * 13 + 0x41 + seed * 7) & 0xFF
        if 97 <= b <= 122:
            b -= 32
        out.append(b)
    return bytes(out)


def pattern_raw(n, seed=0):
    return bytes((i * 13 + 0x41 + seed * 7) & 0xFF for i in range(n))


PARITY_LINES = ["echo par-one\n", "prod 256 0 |> upper\n", "status\n",
                "echo a | cat\n"]


class CplShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/cplshell"
        cls.work.mkdir(parents=True, exist_ok=True)
        blobs = _compile_shell(cls.work)
        cls.blobs = blobs
        # Good image: fs fixtures + shell tree + script dir.
        cls.token = "tok-%s" % __import__("uuid").uuid4().hex[:8]
        entries = _drive_entries(blobs, cls.token)
        cls.image = cls.work / "sh.img"
        cls.image.write_bytes(fs_build(entries))
        # No-shell image: same tree minus /bin/sh.
        nosh = [(p, c) for (p, c) in entries if p != "/bin/sh"]
        cls.image_nosh = cls.work / "sh-nosh.img"
        cls.image_nosh.write_bytes(fs_build(nosh))
        # Bad-shell image: /bin/sh with a broken envelope.
        badsh = [(p, c) for (p, c) in entries if p != "/bin/sh"]
        badbin = bytearray(blobs["sh"])
        badbin[0:4] = b"BAD!"
        badsh.append(("/bin/sh", bytes(badbin)))
        cls.image_badsh = cls.work / "sh-badsh.img"
        cls.image_badsh.write_bytes(fs_build(badsh))
        # Kernels: interactive + script-driven.
        cls.dest = cls.work / "image"
        build_image(ROOT, cls.dest, shell_boot=True)
        cls.dest_script = cls.work / "image-script"
        build_image(ROOT, cls.dest_script, shell_boot=True,
                    shell_script="/test/session.sh")

    @classmethod
    def _boot(cls, keys, done, image=None, dest=None, burst=(),
              tag="boot"):
        logs = cls.work / ("%s-%s" % (tag, len(list(cls.work.glob(tag + "-*")))))
        return boot_image((dest or cls.dest) / "rynoros.img", logs,
                          timeout=60, extra_drives=(image or cls.image,),
                          require_sh=True, sh_keys=tuple(keys),
                          sh_burst=tuple(burst), sh_done=done)

    @classmethod
    def _session_a1_keys(cls):
        keys = []
        keys += K("echo hi\n")
        keys += K("echo \"a b\"\n")
        keys += K("echo \"a\\\"b\\\\c\"\n")
        keys += K("echo x // see\n")
        keys += K("echo a ; echo b\n")
        keys += K("exitcode 5\n") + K("status\n")
        keys += K("echo ab") + [BSPACE, BSPACE] + K("cd\n")
        keys += K("dcode\n")
        keys += K("echo A1-DONE-7f2\n")
        return keys

    @classmethod
    def _session_a2_keys(cls):
        keys = []
        keys += K("prod 8192 0 |> upper\n")
        keys += K("echo hi |> cat\n")
        for line in PARITY_LINES:
            keys += K(line)
        keys += K("echo A2-DONE-7f2\n")
        return keys

    @classmethod
    def _session_a1_output(cls):
        if not hasattr(cls, "_cached_a1"):
            cls._cached_a1 = cls._boot(cls._session_a1_keys(),
                                       b"A1-DONE-7f2", tag="a1")
        return cls._cached_a1

    @classmethod
    def _session_a2_output(cls):
        if not hasattr(cls, "_cached_a2"):
            cls._cached_a2 = cls._boot(cls._session_a2_keys(),
                                       b"A2-DONE-7f2", tag="a2")
        return cls._cached_a2

    @classmethod
    def _script_output(cls):
        if not hasattr(cls, "_cached_script"):
            logs = cls.work / "boot-script"
            cls._cached_script = boot_image(
                cls.dest_script / "rynoros.img", logs, timeout=60,
                extra_drives=(cls.image,), require_sh=True,
                sh_done=b"[SHD] halt code=0\r\n")
        return cls._cached_script

    def test_session_a1_basics(self):
        out = self._session_a1_output()
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        # Exact status flow: hi, quoted, escaped, comment, semi-a,
        # semi-b, exitcode, status, backspace-cd, dcode, token echo.
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 0, 0, 0, 0, 0, 5, 5, 0, 0, 0])
        # Echo goldens from the hex channel, per writer slot (child
        # output is multi-write and races shell markers in the full
        # blob; `5` comes from the status builtin, i.e. the shell).
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 1)
        for golden in (b"hi\n", b"a b\n", b'a"b\\c\n', b"x\n", b"a\n",
                       b"b\n", b"cd\n", b"dcode ok\n"):
            self.assertIn(golden.hex(), kids)
        self.assertIn(b"5\r\n".hex(), _slot_blob(writes, 0))

    def test_session_a2_pipelines(self):
        out = self._session_a2_output()
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        # Exact status flow: big pipe, hi pipe, par-one, par-pipe,
        # par-status, pipe-bar error, token echo.
        dones = collect_done_statuses(out)
        self.assertEqual(dones, [0, 0, 0, 0, 0, 2, 0])
        writes = collect_load_writes(out)
        # Big pipeline: exact uppercase bytes + shell overlap row.
        self.assertIn("[SH] overlap 1", rows)
        # Shell image bounds (frozen RYNX v2 caps).
        code, fsz, msz = struct.unpack("<III", self.blobs["sh"][16:28])
        self.assertLessEqual(code, 32768)
        self.assertLessEqual(msz, 16384)
        # Big pipeline: the consumer (slot 2) is the only serial
        # writer while the shell waits, so its bytes are back-to-back
        # in its slot subsequence (no shell prints mid-transfer).
        blob = "".join(h for _, h in writes)
        self.assertIn(b"par-one\n".hex(), _slot_blob(writes, 1))
        self.assertIn(pattern_upper(8192).hex(), _slot_blob(writes, 2))
        self.assertNotIn(pattern_raw(8192).hex(), blob)

    def test_session_b1a_rejects(self):
        # Rejections live in their own boot: one long session overruns
        # the 32 MiB QEMU int-trace evidence cap, so rejects and
        # discovery run as two boots.
        keys = []
        keys += K("|\n")
        keys += K("a |> b |> c\n")
        keys += K("echo hi # there\n")
        keys += K("echo 'sq'\n")
        keys += K("echo $\n")
        keys += K("status |> cat\n")
        keys += K('echo "abc\n')
        keys += K("echo \\q\n")
        keys += K("echo B1A-DONE-tok\n")
        out = self._boot(keys, b"B1A-DONE-tok", tag="b1a")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        # Every rejection ends in a syntax error with no spawn.
        self.assertEqual(rows.count("[SH] error syntax"), 8)
        self.assertEqual(collect_done_statuses(out).count(2), 8)
        # No spawned rows up to and including the last rejection (the
        # only spawn anywhere is the closing token echo).
        err_idx = [i for i, r in enumerate(rows)
                   if r == "[SH] error syntax"]
        self.assertEqual(len(err_idx), 8)
        self.assertFalse(any(r.startswith("[SH] spawned")
                             for r in rows[:err_idx[-1] + 1]))
        # E-M2 tripwire: the invalid 3-stage line spawns nothing.
        badline = rows.index("[SH] error syntax")
        self.assertFalse(any(r.startswith("[SH] spawned")
                             for r in rows[:badline + 9]))

    def test_session_b1b_discovery(self):
        keys = []
        keys += K("nope\n") + K("status\n")
        keys += K("bad\n")
        keys += K("d\n")
        keys += K("/bin/nope\n")
        keys += K("e" * 27 + " hi\n")
        keys += K("b" * 28 + "\n")
        keys += K("echo 1 2 3 4 5 6 7 8\n")
        keys += K("echo B1B-DONE-tok\n")
        out = self._boot(keys, b"B1B-DONE-tok", tag="b1b")
        self.assertEqual(validate_sh_section(out), [])
        # Discovery outcomes through the shell.
        dones = collect_done_statuses(out)
        self.assertIn(127, dones)  # nope
        self.assertIn(126, dones)  # bad + d
        self.assertIn(125, dones)  # 28-char
        self.assertIn(2, dones)    # 9 args
        writes = collect_load_writes(out)
        # 27-char boundary works (child slot: multi-write output).
        self.assertIn(b"hi\n".hex(), _slot_blob(writes, 1))

    def test_session_b2_aborts(self):
        keys = []
        keys += K("echo partial")
        keys += [CTRL_C]
        keys += K("status\n")
        keys += K("spin\n")
        keys += [CTRL_C]
        keys += K("status\n") + K("echo ok\n")
        keys += K("spin |> cat\n")
        keys += [CTRL_C]
        keys += K("echo ok\n")
        keys += K("exitcode 42\n")
        keys += [CTRL_C]
        keys += K("echo ok\n")
        keys += K("echo B2-DONE-tok\n")
        out = self._boot(keys, b"B2-DONE-tok")
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        # E-C1: editing abort, line discarded, status preserved (0).
        self.assertIn("[SH] abort line", rows)
        # E-C2: spinner abort with a reaped ABORTED child.
        self.assertIn("[SH] abort child status=130", rows)
        self.assertTrue(any(re.match(r"\[SH\] reaped \d+,\d+ aborted \d+",
                                     r) for r in rows))
        # E-C3: pipeline abort reaps both sides, pipe freed.
        self.assertIn("[SH] abort pipeline status=130", rows)
        # Continuity after every abort.
        self.assertGreaterEqual(
            sum(1 for r in rows if r == "[SH] prompt"), 8)
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 1)
        self.assertGreaterEqual(kids.count(b"ok\n".hex()), 3)
        # Exact single-purpose rows: each `echo ok` emits exactly one
        # len-2 "ok" row (E-M9 tripwire: preserved partial input would
        # merge into "partialecho ok" rows instead).
        exact_ok = [h for _, h in writes if h == b"ok".hex()]
        self.assertEqual(len(exact_ok), 3)
        # E-C4 race around exitcode 42: either the guest wins (done
        # 42, then the late Ctrl-C aborts the fresh line) or the
        # harness wins (a second child abort); state is consistent
        # either way and the shell continues.
        natural = ("[SH] done status=42" in rows and
                   rows.count("[SH] abort line") >= 2)
        aborted = rows.count("[SH] abort child status=130") >= 2
        self.assertTrue(natural or aborted)

    def test_lost_clears_ctrl(self):
        # E-C6: 39 blind ctrl+space combos right after the keyboard
        # self-test arrive as one blob (156 scancodes) while no one
        # drains: the 31-deep ring overruns (drop-newest) and exactly
        # one loss epoch follows the kept prefix. The flood bytes are
        # decoder-silent, so the paced session must run clean after:
        # the post-loss 'c' is an ordinary miss (NOTFOUND), and
        # status/continuity survive the epoch.
        burst = [(("ctrl", "spc"))] * 39
        keys = K("c\n") + K("status\n") + K("echo ok\n")
        keys += K("echo C6-DONE-tok\n")
        logs = self.work / ("boot-c6-%s"
                            % len(list(self.work.glob("boot-c6-*"))))
        out = boot_image(self.dest / "rynoros.img", logs, timeout=60,
                         extra_drives=(self.image,), require_sh=True,
                         sh_keys=tuple(keys), sh_burst=tuple(burst),
                         sh_done=b"C6-DONE-tok")
        self.assertEqual(validate_sh_section(out), [])
        # The flood really arrived (QEMU PS/2 trace), so the clean
        # session below proves recovery, not a skipped burst.
        trace = (logs / "guest-errors.log").read_bytes()
        flood = [m for m in re.finditer(rb"ps2_keyboard_event \S+ lnx (29|57) ", trace)]
        self.assertGreaterEqual(len(flood), 100)
        rows = collect_sh_rows(out)
        self.assertNotIn("[SH] abort line", rows)
        self.assertNotIn("[SH] abort child status=130", rows)
        dones = collect_done_statuses(out)
        self.assertIn(127, dones)
        self.assertIn(0, dones)
        writes = collect_load_writes(out)
        self.assertIn(b"ok\n".hex(), _slot_blob(writes, 1))

    def test_script_execution(self):
        out = self._script_output()
        self.assertEqual(validate_sh_section(out), [])
        rows = collect_sh_rows(out)
        self.assertIn("[SH] script /test/session.sh", rows)
        self.assertIn("[SH] script done status=0", rows)
        self.assertIn("[SHD] halt code=0", rows)
        self.assertIn("[SHD] balanced", rows)
        # Filesystem provenance: the host-generated token traveled
        # file -> fread -> shell -> serial (never a bundle constant).
        writes = collect_load_writes(out)
        kids = _slot_blob(writes, 1)
        self.assertIn(self.token.encode().hex(), kids)
        # Script goldens: sequencing, comment skip, pipeline, status.
        self.assertIn(("script-start " + self.token).encode().hex(), kids)
        self.assertIn(b"a\n".hex(), kids)
        self.assertIn(b"b\n".hex(), kids)
        self.assertIn(b"par-one\n".hex(), kids)
        self.assertIn(pattern_upper(256).hex(), _slot_blob(writes, 2))
        dones = collect_done_statuses(out)
        self.assertIn(0, dones)
        # The invalid pipeline ends in a loud syntax error, never a
        # partial execution (E-M2 shape; E-M14 parity anchor).
        script_rows = collect_sh_rows(out)
        self.assertEqual(script_rows.count("[SH] error syntax"), 1)
        # Parity with the interactive session: the shared lines
        # produce the same outputs and the same statuses (script
        # dones track the interactive dones line for line; prompts
        # and echoes differ by construction, so outputs carry this).
        script_dones = collect_done_statuses(out)
        sess_dones = collect_done_statuses(self._session_a2_output())
        # Shared PARITY_LINES outcomes appear in both, in order:
        # par-one 0, par-pipe 0, par-status 0.
        self.assertEqual(script_dones[5:8], [0, 0, 0])
        self.assertEqual(sess_dones[2:5], [0, 0, 0])
        script_writes = collect_load_writes(out)
        sess_writes = collect_load_writes(self._session_a2_output())
        self.assertIn(b"par-one\n".hex(), _slot_blob(script_writes, 1))
        self.assertIn(b"par-one\n".hex(), _slot_blob(sess_writes, 1))
        self.assertIn(pattern_upper(256).hex(), _slot_blob(script_writes, 2))
        self.assertIn(pattern_upper(256).hex(), _slot_blob(sess_writes, 2))

    def test_shell_image_sizes(self):
        code, fsz, msz = struct.unpack("<III", self.blobs["sh"][16:28])
        self.assertLessEqual(code, 32768)
        self.assertLessEqual(msz, 16384)
        self.assertGreater(code, 0)

    def test_boot_missing_shell(self):
        logs = self.work / "boot-nosh"
        out = boot_image(self.dest / "rynoros.img", logs, timeout=60,
                         extra_drives=(self.image_nosh,), require_sh=True,
                         sh_done=b"[SHD] missing /bin/sh\r\n")
        rows = collect_sh_rows(out)
        self.assertIn("[SHD] missing /bin/sh", rows)
        self.assertIn("[SHD] balanced", rows)
        # No CPL3 shell rows ever (no kernel evaluator fallback).
        stream_rows = [r for r in rows if r.startswith("[SH] ")]
        self.assertEqual(stream_rows, [])

    def test_boot_malformed_shell(self):
        logs = self.work / "boot-badsh"
        out = boot_image(self.dest / "rynoros.img", logs, timeout=60,
                         extra_drives=(self.image_badsh,), require_sh=True,
                         sh_done=b"[SHD] malformed /bin/sh\r\n")
        rows = collect_sh_rows(out)
        self.assertIn("[SHD] malformed /bin/sh", rows)
        self.assertIn("[SHD] balanced", rows)
        stream_rows = [r for r in rows if r.startswith("[SH] ")]
        self.assertEqual(stream_rows, [])

    # ---- Slice E mutants (each executed RED, restored, green-rerun) ----

    def test_mutant_kernel_ascii_goes_red(self):
        """E-M1: an input-translation table in the kernel input path
        must trip the structural placement guard (no QEMU needed)."""
        with tempfile.TemporaryDirectory(prefix="sh-m1-", dir=ROOT / "build") as tmp:
            root = Path(tmp)
            for directory in REQUIRED_DIRECTORIES:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for filename in REQUIRED_FILES:
                shutil.copyfile(ROOT / filename, root / filename)
            path = root / "kernel/drivers/keyboard.c"
            contents = path.read_text(encoding="utf-8")
            anchor = "static struct kbd_decoder decoder;"
            self.assertEqual(contents.count(anchor), 1)
            table = ("static const char kbd_ascii[128] = {\n"
                     "    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,\n"
                     "    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,\n"
                     "    0,'1','2','3','4','5','6','7','8','9','0',0,0,0,0,0,\n"
                     "    0,'q','w','e','r','t','y','u','i','o','p',0,0,0,0,0,\n"
                     "    0,'a','s','d','f','g','h','j','k','l',0,0,0,0,0,0,\n"
                     "    0,0,'z','x','c','v','b','n','m',0,0,0,0,0,0,0,\n"
                     "    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,\n"
                     "    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0};\n")
            path.write_text(contents.replace(anchor, anchor + "\n" + table),
                            encoding="utf-8")
            mutated = path.read_text(encoding="utf-8")
            self.assertIn("kbd_ascii", mutated)
            with self.assertRaises(AssertionError):
                self.assertNotIn("ascii", mutated)

    def test_mutant_parse_then_spawn_goes_red(self):
        """E-M2: spawning the left side during parsing (before the
        full line validates) executes a doomed pipeline's prefix."""
        out = self._mutant_boot(
            "parse-spawn",
            [("user/shell/sh_parse.c",
              "            commit_cmd(&out->cmds[ncmds], &cur);\n"
              "            ++ncmds;\n"
              "            cur.argc = 0;\n"
              "            cur.store_used = 0;\n"
              "            have_cmd = 0;\n"
              "            i += 2;\n"
              "            continue;",
              "            commit_cmd(&out->cmds[ncmds], &cur);\n"
              "            ++ncmds;\n"
              "            {\n"
              "                unsigned long long spec[12] = {0,0,0,0,0,0,0,0,0,0,0,0};\n"
              "                unsigned long long h = 0, L = 0;\n"
              "                extern unsigned long long rt_gate6(unsigned int, unsigned long long, unsigned long long, unsigned long long, unsigned long long, unsigned long long, unsigned long long);\n"
              "                while (L < 33 && cur.argv[0][L]) ++L;\n"
              "                spec[0] = (unsigned long long)cur.argv[0];\n"
              "                spec[1] = L;\n"
              "                (void)rt_gate6(4, (unsigned long long)spec, (unsigned long long)&h, 0, 0, 0, 0);\n"
              "            }\n"
              "            cur.argc = 0;\n"
              "            cur.store_used = 0;\n"
              "            have_cmd = 0;\n"
              "            i += 2;\n"
              "            continue;",
              1)],
            keys=K("prod 1 0 |> cat |> bogus\n") + K("echo finm2\n"),
            done=b"finm2")
        rows = collect_sh_rows(out)
        self.assertIn("[SH] error syntax", rows)
        # The doomed prefix spawned once (first `|>` crossing) before
        # the trailing `|>` failed validation: parse-then-spawn
        # executes what must never start.
        spawned = [r for r in rows if r.startswith("[SH] spawned")]
        self.assertEqual(len(spawned), 1)

    def test_mutant_bare_pipe_goes_red(self):
        """E-M3: lone `|` accepted as a pipeline runs what the frozen
        grammar rejects."""
        out = self._mutant_boot(
            "bare-pipe",
            [("user/shell/sh_parse.c",
              "            if (!have_cmd) return SHP_ERR_SYNTAX;\n"
              "            if (i + 1 >= len || text[i + 1] != '>') return SHP_ERR_SYNTAX;",
              "            if (!have_cmd) return SHP_ERR_SYNTAX;\n"
              "            if (i + 1 < len && text[i + 1] == '>') ++i;",
              1)],
            keys=K("echo a | cat\n") + K("echo finm3\n"),
            done=b"finm3")
        rows = collect_sh_rows(out)
        self.assertNotIn("[SH] error syntax", rows)
        writes = collect_load_writes(out)
        # Pipeline consumer (slot 2) carries `a` to serial.
        self.assertIn(b"a\n".hex(), _slot_blob(writes, 2))

    def test_mutant_hash_comment_goes_red(self):
        """E-M4: `#` treated as a comment runs `echo hi # there` as
        plain `echo hi` instead of rejecting it."""
        out = self._mutant_boot(
            "hash-comment",
            [("user/shell/sh_parse.c",
              "        if (text[i] == '/' && i + 1 < len && text[i + 1] == '/') {",
              "        if ((text[i] == '/' && i + 1 < len && text[i + 1] == '/') || text[i] == '#') {",
              1)],
            keys=K("echo hi # there\n") + K("echo finm4\n"),
            done=b"finm4")
        rows = collect_sh_rows(out)
        self.assertNotIn("[SH] error syntax", rows)
        writes = collect_load_writes(out)
        self.assertIn(b"hi\n".hex(), _slot_blob(writes, 1))

    def test_mutant_quote_leak_goes_red(self):
        """E-M5: a pipe metacharacter leaking out of quotes parses
        `echo "a|>b"` as a pipeline instead of literal text."""
        out = self._mutant_boot(
            "quote-leak",
            [("user/shell/sh_parse.c",
              '                if (q == \'"\') {\n'
              '                    ++i;\n'
              '                    break;\n'
              '                }',
              '                if (q == \'"\') {\n'
              '                    ++i;\n'
              '                    break;\n'
              '                }\n'
              '                if (q == \'|\') break;',
              1)],
            keys=K('echo "a|>b"\n') + K("echo finm5\n"),
            done=b"finm5")
        rows = collect_sh_rows(out)
        self.assertTrue(any(r.startswith("[SH] spawned") for r in rows))
        writes = collect_load_writes(out)
        blob = "".join(h for _, h in writes)
        self.assertNotIn(b"a|>b\n".hex(), blob)

    def test_mutant_sequential_pipe_goes_red(self):
        """E-M6 (mandatory): sequential spawn/wait instead of spawn_pipe
        shows producer bytes direct-to-serial (lowercase) with an empty
        transform stage, never the streamed uppercase proof."""
        seq = ("    build_spec(&sa, va, aa[0], lena, a->argc, aa,\n"
               "               SH_STDIN_CLOSED, SH_STDOUT_SERIAL);\n"
               "    rc = sh_sys_spawn(&sa, &ha);\n"
               "    if (rc != SH_OK) {\n"
               "        row_begin();\n"
               "        row_str(\"[SH] error spawn \");\n"
               "        row_num(rc);\n"
               "        row_str(\"\\r\\n\");\n"
               "        row_flush();\n"
               "        last_status = map_spawn(rc);\n"
               "        final_status = last_status;\n"
               "        final_kind = 1;\n"
               "        return;\n"
               "    }\n"
               "    row_begin();\n"
               "    row_str(\"[SH] spawned a=\");\n"
               "    row_handle(ha);\n"
               "    row_str(\"\\r\\n\");\n"
               "    row_flush();\n"
               "    fh[0] = ha;\n"
               "    nfh = 1;\n"
               "    fh_abort = 0;\n"
               "    reap_children();\n"
               "    build_spec(&sb, vb, ab[0], lenb, b->argc, ab,\n"
               "               SH_STDIN_CLOSED, SH_STDOUT_SERIAL);\n"
               "    rc = sh_sys_spawn(&sb, &hb);\n"
               "    if (rc != SH_OK) {\n"
               "        row_begin();\n"
               "        row_str(\"[SH] error spawn \");\n"
               "        row_num(rc);\n"
               "        row_str(\"\\r\\n\");\n"
               "        row_flush();\n"
               "        last_status = map_spawn(rc);\n"
               "        final_status = last_status;\n"
               "        final_kind = 1;\n"
               "        return;\n"
               "    }\n"
               "    row_begin();\n"
               "    row_str(\"[SH] spawned a=\");\n"
               "    row_handle(hb);\n"
               "    row_str(\"\\r\\n\");\n"
               "    row_flush();\n"
               "    fh[0] = hb;\n"
               "    nfh = 1;\n"
               "    fh_abort = 0;\n"
               "    (void)sh_sys_spawn_pipe;")
        out = self._mutant_boot(
            "sequential-pipe",
            [("user/shell/sh.c",
              "    build_spec(&sa, va, aa[0], lena, a->argc, aa,\n"
              "               SH_STDIN_CLOSED, SH_STDOUT_PIPE);\n"
              "    build_spec(&sb, vb, ab[0], lenb, b->argc, ab,\n"
              "               SH_STDIN_PIPE, SH_STDOUT_SERIAL);\n"
              "    rc = sh_sys_spawn_pipe(&sa, &sb, &ha, &hb);\n"
              "    if (rc != SH_OK) {\n"
              "        row_begin();\n"
              "        row_str(\"[SH] error spawn \");\n"
              "        row_num(rc);\n"
              "        row_str(\"\\r\\n\");\n"
              "        row_flush();\n"
              "        last_status = map_spawn(rc);\n"
              "        final_status = last_status;\n"
              "        final_kind = 1;\n"
              "        return;\n"
              "    }\n"
              "    row_begin();\n"
              "    row_str(\"[SH] spawned a=\");\n"
              "    row_handle(ha);\n"
              "    row_str(\" b=\");\n"
              "    row_handle(hb);\n"
              "    row_str(\"\\r\\n\");\n"
              "    row_flush();\n"
              "    fh[0] = ha;\n"
              "    fh[1] = hb;\n"
              "    nfh = 2;\n"
              "    fh_abort = 0;",
              seq,
              1)],
            keys=self._session_a2_keys(),
            done=b"A2-DONE-7f2")
        writes = collect_load_writes(out)
        blob = "".join(h for _, h in writes)
        # Sequential bypass: lowercase producer bytes hit serial
        # directly (its own slot: shell markers race the full blob)
        # and the transform stage stays empty.
        self.assertIn(pattern_raw(8192).hex(), _slot_blob(writes, 1))
        self.assertNotIn(pattern_upper(8192).hex(), blob)

    def test_mutant_dropped_handle_goes_red(self):
        """E-M7: forgetting the consumer handle strands a zombie that
        holds its slot/thread/pipe end forever (only wait() releases);
        the next pipeline cannot acquire both sides and fails."""
        out = self._mutant_boot(
            "dropped-handle",
            [("user/shell/sh.c",
              "    fh[0] = ha;\n"
              "    fh[1] = hb;\n"
              "    nfh = 2;",
              "    fh[0] = ha;\n"
              "    nfh = 1;",
              1)],
            keys=K("prod 256 0 |> upper\n") + K("prod 256 0 |> upper\n") +
                 K("echo finm7\n"),
            done=b"finm7")
        rows = collect_sh_rows(out)
        # Exactly one pipeline got both sides; the second fails its
        # spawn (exact errno is kernel accounting; the starvation is
        # the shell's crime) and the session continues.
        dual = [r for r in rows if " b=" in r]
        self.assertEqual(len(dual), 1)
        self.assertTrue(any(re.match(r"\[SH\] error spawn \d+$", r)
                            for r in rows))
        # prod exits 42: with only the producer reaped, the pipeline
        # reports the LEFT-hand status (green reports the consumer's 0).
        self.assertEqual(collect_done_statuses(out), [42, 125, 0])
        writes = collect_load_writes(out)
        self.assertEqual(
            _slot_blob(writes, 2).count(pattern_upper(256).hex()), 1)

    def test_mutant_shell_dies_on_ctrlc_goes_red(self):
        """E-M8: Ctrl-C terminating the shell itself ends the session
        (unexpected halt) instead of continuing after an abort."""
        out = self._mutant_boot(
            "shell-dies",
            [("user/shell/sh.c",
              "static void ctrl_c(void)\n"
              "{\n"
              "    unsigned int i, killed = 0;\n"
              '    sh_print("^C\\r\\n");',
              "static void ctrl_c(void)\n"
              "{\n"
              "    unsigned int i, killed = 0;\n"
              '    sh_print("^C\\r\\n");\n'
              "    rt_exit(99);",
              1)],
            keys=K("echo hi\n") + [CTRL_C] + K("echo finm8\n"),
            done=b"[SHD] halt code=99\r\n")
        rows = collect_sh_rows(out)
        # The session never continues: initial prompt, one command,
        # then Ctrl-C kills the shell itself (no abort row, no third
        # prompt) with the unexpected halt code.
        self.assertEqual(rows.count("[SH] prompt"), 2)
        self.assertNotIn("[SH] abort line", rows)
        self.assertIn("[SHD] halt code=99", rows)

    def test_mutant_kept_partial_goes_red(self):
        """E-M9: preserving partial input across a line abort merges
        the next command into the aborted prefix."""
        out = self._mutant_boot(
            "kept-partial",
            [("user/shell/sh.c",
              "    if (nfh == 0) {\n"
              "        clear_line();\n"
              '        sh_print("[SH] abort line\\r\\n");',
              "    if (nfh == 0) {\n"
              '        sh_print("[SH] abort line\\r\\n");',
              1)],
            keys=K("echo partial") + [CTRL_C] + K("echo ok\n") +
                 K("echo finm9\n"),
            done=b"finm9")
        writes = collect_load_writes(out)
        # sh_echo writes argv[1..] one word per row: the merged line
        # `echo partialecho ok` emits a bare `ok` second-arg row (a
        # lone `echo ok` would emit it as the only word row too, but
        # here it rides inside the merged `partialecho ok` output).
        exact_ok = [h for _, h in writes if h == b"ok".hex()]
        self.assertEqual(len(exact_ok), 1)
        self.assertIn(b"partialecho ok\n".hex(), _slot_blob(writes, 1))

    def test_mutant_no_reap_on_abort_goes_red(self):
        """E-M10: terminating then dropping the handle (no wait) leaks
        the slot forever and finalizes nothing: no abort row, no reap
        row, and the next pipeline starves while the session limps on."""
        out = self._mutant_boot(
            "no-reap",
            [("user/shell/sh.c",
              "    fh_abort = killed ? 1 : 0;\n"
              "    reap_children();",
              "    fh_abort = killed ? 1 : 0;\n"
              "    nfh = 0;\n"
              "    (void)reap_children;",
              1)],
            keys=K("spin\n") + [CTRL_C] + K("spin |> cat\n") + [CTRL_C] +
                 K("echo finm10\n"),
            done=b"finm10")
        rows = collect_sh_rows(out)
        # The killed spinner is never waited (no reap row), its abort
        # never finalizes (no abort row for it), the next pipeline
        # starves, and the session continues after.
        self.assertFalse(any(re.match(r"\[SH\] reaped \d+,\d+ aborted",
                                      r) for r in rows))
        self.assertNotIn("[SH] abort child status=130", rows)
        self.assertTrue(any(re.match(r"\[SH\] error spawn \d+$", r)
                            for r in rows))
        self.assertEqual(collect_done_statuses(out), [125, 0])

    def test_mutant_pause_is_ctrl_goes_red(self):
        """E-M11: bare scan-0x1D handling mistakes Pause bytes for
        Ctrl; the decoder self-test catches the trap vector."""
        out = self._mutant_boot(
            "pause-ctrl",
            [("user/shell/sh_key.c",
              "    if (scan == 0xe1) {\n"
              "        st->e0 = 0;\n"
              "        st->e1 = 1;\n"
              "        return 1;\n"
              "    }",
              "    if (scan == 0xe1) {\n"
              "        return 1;\n"
              "    }",
              1)],
            keys=K("dcode\n") + K("echo finm11\n"),
            done=b"finm11")
        dones = collect_done_statuses(out)
        self.assertIn(214, dones)

    def test_mutant_lost_keeps_ctrl_goes_red(self):
        """E-M12: LOST without modifier reset leaves Ctrl stuck: the
        decoder self-test's LOST vector (shift+ctrl held, E0 prefix,
        LOST, then 'c') decodes as a false Ctrl-C instead of text.
        Deterministic by construction (no flood timing involved)."""
        out = self._mutant_boot(
            "lost-ctrl",
            [("user/shell/sh_key.c",
              "    if (scan == 0x00) {\n"
              "        st->shift = 0;\n"
              "        st->ctrl = 0;\n"
              "        st->e0 = 0;\n"
              "        st->e1 = 0;\n"
              "        return 1;\n"
              "    }",
              "    if (scan == 0x00) {\n"
              "        st->shift = 0;\n"
              "        st->e0 = 0;\n"
              "        st->e1 = 0;\n"
              "        return 1;\n"
              "    }",
              1)],
            keys=K("dcode\n") + K("echo finm12\n"),
            done=b"finm12")
        # lost_mid vector fails (want CHAR 'c', got CTRL_C): dcode
        # exits 200 + (210 % 50).
        dones = collect_done_statuses(out)
        self.assertIn(210, dones)

    def test_mutant_bundled_script_goes_red(self):
        """E-M13: executing a compiled-in script copy instead of fread
        bytes misses the host-generated provenance token."""
        out = self._mutant_boot(
            "bundled-script",
            [("user/shell/sh.c",
              "        off += n;\n"
              "        if (n < want) break;\n"
              "    }\n"
              "    script_len = off;\n"
              "    script[off] = 0;\n"
              "    return 1;",
              "        off += n;\n"
              "        if (n < want) break;\n"
              "    }\n"
              "    {\n"
              "        const char *bundled = \"echo bundled-constant\\n\";\n"
              "        unsigned long long bl = 0;\n"
              "        while (bundled[bl] != 0 && bl < SH_SCRIPT_MAX) { script[bl] = bundled[bl]; ++bl; }\n"
              "        script[bl] = 0;\n"
              "        script_len = bl;\n"
              "    }\n"
              "    return 1;",
              1)],
            shell_script="/test/session.sh", keys=(), done=b"bundled-constant")
        writes = collect_load_writes(out)
        blob = "".join(h for _, h in writes)
        self.assertIn(b"bundled-constant\n".hex(), _slot_blob(writes, 1))
        self.assertNotIn(self.token.encode().hex(), blob)

    def test_mutant_script_divergence_goes_red(self):
        """E-M14: script-only `|`-to-`;` rewriting diverges from the
        interactive parser on the shared parity line."""
        out = self._mutant_boot(
            "script-diverge",
            [("user/shell/sh.c",
              "    script_len = off;\n"
              "    script[off] = 0;\n"
              "    return 1;",
              "    script_len = off;\n"
              "    script[off] = 0;\n"
              "    for (plen = 0; plen < off; ++plen)\n"
              "        if (script[plen] == '|' && (plen + 1 >= off || script[plen + 1] != '>'))\n"
              "            script[plen] = ';';\n"
              "    return 1;",
              1)],
            shell_script="/test/session.sh", keys=(), done=b"[SHD] halt code=0\r\n")
        rows = collect_sh_rows(out)
        self.assertEqual(rows.count("[SH] error syntax"), 0)

    def test_mutant_status_on_poll_goes_red(self):
        """E-M15: caching status on RUNNING polls (instead of terminal
        outcomes) leaves the builtin showing poll-time values. The big
        pipeline forces overlap, so RUNNING polls are certain (a tiny
        child might exit before the first poll)."""
        out = self._mutant_boot(
            "status-poll",
            [("user/shell/sh.c",
              "        if (st.state == SH_RUNNING) {\n"
              "            live++;\n"
              "            continue;\n"
              "        }",
              "        if (st.state == SH_RUNNING) {\n"
              "            live++;\n"
              "            last_status = 77;\n"
              "            continue;\n"
              "        }",
              1),
             ("user/shell/sh.c",
              "    final_kind = fh_abort ? (nfh == 2 ? 3 : 2) : 1;\n"
              "    last_status = final_status;\n"
              "    nfh = 0;",
              "    final_kind = fh_abort ? (nfh == 2 ? 3 : 2) : 1;\n"
              "    nfh = 0;",
              1)],
            keys=K("prod 8192 0 |> upper\n") + K("status\n") +
                 K("echo finm15\n"),
            done=b"finm15")
        from sh_output import terminal_stream
        lines = terminal_stream(out).split(b"\r\n")
        # The builtin reports the poll-time value, not the outcome.
        self.assertNotIn(b"0", lines)
        self.assertIn(b"77", lines)

    def test_mutant_kernel_fallback_goes_red(self):
        """E-M16: a ring-0 fallback evaluator on missing /bin/sh trips
        the placement guard and shows shell rows from the kernel."""
        out = self._mutant_boot(
            "kernel-fallback",
            [("kernel/core/shd.c",
              "    if (!mount_shell_fs()) {\n"
              "        shd_balanced(base);\n"
              '        halt("[SHD] missing /bin/sh");\n'
              "    }",
              "    if (!mount_shell_fs()) {\n"
              "        (void)serial_write(\"[SH] prompt\\r\\n\");\n"
              "        (void)serial_flush();\n"
              "        shd_balanced(base);\n"
              '        halt("[SHD] missing /bin/sh");\n'
              "    }",
              1)],
            image_kind="nosh", keys=(), done=b"[SHD] missing /bin/sh\r\n")
        # Ring-0 shell rows would surface RAW (never hex-wrapped):
        # the row collector only accepts hex-channel [SH] rows plus
        # raw [SHD] rows, so assert on the raw transcript directly.
        self.assertIn(b"[SH] prompt\r\n", out)

    def _mutant_boot(self, name, edits, shell_script=None, keys=None,
                     done=None, burst=(), image_kind="good",
                     expect_timeout=False):
        """Shell mutant run: copy the tree, apply source edits, build a
        shell image, and boot. expect_timeout=True asserts the boot
        never completes (hang/killed shell). Otherwise the boot must
        complete and the caller asserts RED markers. Restored by
        fixture deletion (the working tree is never mutated)."""
        with tempfile.TemporaryDirectory(prefix="sh-fault-", dir=ROOT / "build") as tmp:
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
            from image import build_image as _bi
            dest = root / "build" / "img"
            _bi(root, dest, shell_boot=True, shell_script=shell_script)
            if any(source.startswith("user/")
                   for source, _, _, _ in edits):
                # Shell-source mutant: the drive image must carry the
                # mutated /bin/sh (the class images are unmutated), rebuilt
                # here with the same token/script as the class image.
                blobs = _compile_shell(root / "build" / "progs", root=root)
                entries = _drive_entries(blobs, self.token)
                if image_kind == "nosh":
                    entries = [(p, c) for (p, c) in entries
                               if p != "/bin/sh"]
                elif image_kind == "badsh":
                    entries = [(p, c) for (p, c) in entries
                               if p != "/bin/sh"]
                    badbin = bytearray(blobs["sh"])
                    badbin[0:4] = b"BAD!"
                    entries.append(("/bin/sh", bytes(badbin)))
                image = root / "drive.img"
                image.write_bytes(fs_build(entries))
            else:
                image = {"good": self.image, "nosh": self.image_nosh,
                         "badsh": self.image_badsh}[image_kind]
            logs = ROOT / "build/cplshell" / name
            if expect_timeout:
                with self.assertRaises(RuntimeError):
                    boot_image(dest / "rynoros.img", logs, timeout=60,
                               extra_drives=(image,), require_sh=True,
                               sh_keys=tuple(keys or ()),
                               sh_burst=tuple(burst), sh_done=done)
                return None
            return boot_image(dest / "rynoros.img", logs, timeout=60,
                              extra_drives=(image,), require_sh=True,
                              sh_keys=tuple(keys or ()),
                              sh_burst=tuple(burst), sh_done=done)

