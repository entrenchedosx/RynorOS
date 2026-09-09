"""Stage 18c integration: native runtime conformance in QEMU CPL3.

One shared boot (conformance filesystem, no /rnyx so the 18b loader
section skips) feeds the evidence assertions; mutant libraries boot
separately. Guest programs really execute through the 18b loader
(validated RYNX, fixed mappings, exit/write/yield); the host pins every
deterministic number and byte against golden constants.
"""
import shutil
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
from rt_output import parse_serial as parse_rt, validate as validate_rt, \
    VERIFIED_LINE as RT_VERIFIED_LINE, PROGRAMS as RT_PROGRAMS, \
    CLASS_LINES as RT_CLASS_LINES, WRITE_PAYLOADS as RT_WRITE_PAYLOADS
from load_output import parse_serial as parse_load
from boot_output import validate_boot_output
from test_filesystem import GOOD_ENTRIES
from fs_image import build as fs_build
from tools.rynorlang import program as rynor_program
from tools.host import rnyx

RT_DIR = ROOT / "user/lib/rt"
C_TESTS = ["fmt", "alloc", "write", "nap", "wait", "nosys"]
RLPRINT_SRC = 'fn main(): int { print(42); print(true); print("hi"); return 0; }'


RTLIB_SRC_DIR = RT_DIR / "tests"


def _compile_rt_programs(work, libdir=None):
    """Compile the six C tests plus the .rl print program to RYNX bytes.
    libdir overrides the library sources (mutant trees); None uses the
    live user/lib/rt tree."""
    srcdir = Path(libdir) / "tests" if libdir is not None else RTLIB_SRC_DIR
    out = {}
    for name in C_TESTS:
        src = (srcdir / f"t_{name}.c").read_text(encoding="utf-8")
        progdir = work / f"prog-{name}"
        progdir.mkdir(parents=True, exist_ok=True)
        arts, error = rynor_program.build_rynor_c_program(
            {f"t_{name}.c": src}, progdir, prog=name, rtlib_dir=libdir)
        assert error is None, (name, error)
        out[name] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
    progdir = work / "prog-rlprint"
    progdir.mkdir(parents=True, exist_ok=True)
    arts, error = rynor_program.build_rynor_program(
        RLPRINT_SRC, "rlprint.rl", progdir, prog="rlprint",
        runtime="rtlib", rtlib_dir=libdir)
    assert error is None, ("rlprint", error)
    out["rlprint"] = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
    return out


def _mutate_rtlib(pairs):
    """Copy the repo scaffold plus user/lib/rt, apply (old, new) pairs to
    rt.c in the copy. Returns (tmp, root) like the loader mutant helper."""
    tmp = tempfile.TemporaryDirectory(prefix="rt-fault-", dir=ROOT / "build")
    root = Path(tmp.name)
    from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
    for directory in REQUIRED_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    # The 18c library tree (including tests/) is copied whole first so
    # the file loop below finds its directories; identical content is
    # overwritten in place, then the mutation is applied.
    libdir = root / "user/lib/rt"
    shutil.copytree(RTLIB_SRC_DIR.parent, libdir, dirs_exist_ok=True)
    for filename in REQUIRED_FILES:
        shutil.copyfile(ROOT / filename, root / filename)
    path = libdir / "rt.c"
    contents = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents, encoding="utf-8")
    return tmp, root


class RtIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/rt-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        rnx = _compile_rt_programs(cls.work)
        cls.rnx = rnx
        entries = list(GOOD_ENTRIES)
        entries.append(("/rt", None))
        for name in C_TESTS + ["rlprint"]:
            entries.append((f"/rt/{name}.rnx", rnx[name]))
        cls.image = cls.work / "rt.img"
        cls.image.write_bytes(fs_build(entries))
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination)
        logs = cls.work / "shared-good"
        cls.output = boot_image(cls.destination / "rynoros.img", logs, timeout=60,
                                extra_drives=(cls.image,))
        summary = __import__("json").loads((logs / "run.json").read_text(encoding="utf-8"))
        assert summary["reaped"], summary

    def _writes(self):
        return parse_load(self.output).writes

    def test_good_rt_full_evidence(self):
        self.assertEqual(validate_rt(parse_rt(self.output), self._writes()), [])

    def test_boot_output_accepts_rt_section(self):
        self.assertEqual(validate_boot_output(self.output), [])

    def test_exit_rows_exact(self):
        self.assertEqual(parse_rt(self.output).exits, [(0, 0)] * 7)

    def test_markers_and_accounting(self):
        evidence = parse_rt(self.output)
        self.assertEqual(evidence.balanced, 7)
        self.assertEqual(len(evidence.creates), 7)
        self.assertEqual(len(evidence.destroys), 7)
        self.assertEqual([p for p, _, _, _, _ in evidence.programs], RT_PROGRAMS)

    def test_completion_rt_terminator(self):
        self.assertTrue(boot_complete(self.output))
        stripped = self.output.replace(RT_VERIFIED_LINE, b"")
        self.assertFalse(boot_complete(stripped))

    def test_skip_marker_on_plain_image(self):
        plain = self.work / "plain.img"
        plain.write_bytes(fs_build(list(GOOD_ENTRIES)))
        logs = self.work / "plain-skip"
        output = boot_image(self.destination / "rynoros.img", logs, timeout=60,
                            extra_drives=(plain,))
        self.assertIn(b"[RT] no image, skipped", output)
        self.assertNotIn(b"[RT] rt verified", output)
        self.assertEqual(validate_boot_output(output), [])

    def test_rl_print_rebind_distinct_runtime(self):
        # The rebind is real: same source links different objects and the
        # lib flavor's print bytes arrive through rt_write in CPL3.
        work = self.work / "rebind-compare"
        work.mkdir(parents=True, exist_ok=True)
        arts, error = rynor_program.build_rynor_program(
            RLPRINT_SRC, "rlprint.rl", work, "rlprint")
        self.assertIsNone(error, error)
        rynor_blob = rnyx.elf_to_rnyx(Path(arts["exe"]).read_bytes())
        self.assertNotEqual(rynor_blob, self.rnx["rlprint"])
        evidence = parse_rt(self.output)
        self.assertIn(b"[RT] program path=/rt/rlprint.rnx", self.output)

    def _run_rt_mutation(self, pairs, timeout=60):
        tmp, root = _mutate_rtlib(pairs)
        self.addCleanup(tmp.cleanup)
        work = root / "mut"
        work.mkdir(parents=True, exist_ok=True)
        rnx = _compile_rt_programs(work, libdir=root / "user/lib/rt")
        entries = list(GOOD_ENTRIES)
        entries.append(("/rt", None))
        for name in C_TESTS + ["rlprint"]:
            entries.append((f"/rt/{name}.rnx", rnx[name]))
        image = work / "rt.img"
        image.write_bytes(fs_build(entries))
        build_image(root, root / "build" / "img")
        logs = self.work / self._testMethodName
        try:
            output = boot_image(root / "build" / "img" / "rynoros.img", logs,
                                timeout=timeout, extra_drives=(image,))
            return output, None
        except RuntimeError as err:
            serial = (logs / "serial.log").read_bytes()
            return serial, str(err)

    def _assert_not_clean(self, output, error):
        evidence = parse_rt(output)
        clean = (error is None and
                 validate_rt(evidence, parse_load(output).writes) == [] and
                 b"[RT] rt verified" in output)
        self.assertFalse(clean, "mutant survived with clean verified evidence")

    def _assert_fail_fast(self, output, error):
        # Fail-fast negatives: guest reports failure= (fail-fast via
        # qemu.py driver_failure), never a clean verified boot and never
        # an intended-timeout without a failure line.
        self._assert_not_clean(output, error)
        self.assertIsNotNone(error, "fail-fast mutant should report failure, not complete")
        self.assertIn("failure", error.lower(), error)
        self.assertNotIn(b"[RT] rt verified", output)
        evidence = parse_rt(output)
        writes = parse_load(output).writes
        self.assertTrue(evidence.failures or validate_rt(evidence, writes) != [],
                        "fail-fast mutant must leave failure evidence")

    def test_mut_arena_bound_removed(self):
        output, error = self._run_rt_mutation([(
            "    end = base + size;\n"
            "    if (end < base || end > RT_ARENA_SIZE)\n"
            "        return RT_NOMEM;",
            "    end = base + size;",
        )], timeout=20)
        self._assert_fail_fast(output, error)

    def test_mut_length_check_removed(self):
        output, error = self._run_rt_mutation([(
            "    if (fd != RT_FD_STDOUT)\n"
            "        return RT_INVAL;\n"
            "    if (n > RT_WRITE_MAX)\n"
            "        return RT_RANGE;\n"
            "    if (n == 0)",
            "    if (fd != RT_FD_STDOUT)\n"
            "        return RT_INVAL;\n"
            "    if (n == 0)",
        )], timeout=20)
        self._assert_fail_fast(output, error)

    def test_mut_fmt_measure_removed(self):
        output, error = self._run_rt_mutation([(
            "    if (bad == 1)\n"
            "        return -(long long)RT_INVAL;\n"
            "    if (bad == 2)\n"
            "        return -(long long)RT_RANGE;",
            "    (void)bad;",
        )], timeout=20)
        self._assert_fail_fast(output, error)

    def test_mut_wait_bound_removed(self):
        output, error = self._run_rt_mutation([(
            "        if (i >= max_yields)\n"
            "            return RT_AGAIN;",
            "        (void)max_yields;\n"
            "        (void)i;",
        )], timeout=20)
        # Intended-timeout (non-termination), distinctly from fail-fast:
        # boot times out with no verified marker and no failure lines.
        self._assert_not_clean(output, error)
        self.assertIsNotNone(error, "wait-bound mutant should time out, not complete")
        self.assertIn("timed out", error.lower(), error)
        self.assertNotIn(b"[RT] rt verified", output)
        evidence = parse_rt(output)
        self.assertEqual(evidence.failures, [], evidence.failures)

    def test_mut_fd_check_removed(self):
        output, error = self._run_rt_mutation([(
            "    if (fd != RT_FD_STDOUT)\n"
            "        return RT_INVAL;\n",
            "",
        )], timeout=20)
        # Broken write-row pin verified by execution: guest still exits 0
        # (kernel -1 maps to RT_INVAL), boot completes verified, but the
        # kernel-observed write rows gain the extra fd=2 syscall row.
        self._assert_not_clean(output, error)
        self.assertIsNone(error, f"fd mutant should complete verified, got: {error}")
        self.assertIn(b"[RT] rt verified", output)
        evidence = parse_rt(output)
        self.assertEqual(evidence.failures, [], evidence.failures)
        self.assertEqual(evidence.exits, [(0, 0)] * 7, evidence.exits)
        errors = validate_rt(evidence, parse_load(output).writes)
        self.assertTrue(any("kernel write rows differ" in e for e in errors), errors)
        fds = [(s, f, l, n) for s, f, l, n, _ in parse_load(output).writes]
        self.assertIn((0, 2, 2, 0), fds, fds)


if __name__ == "__main__":
    unittest.main()
