"""Stage 19e host groundwork: fread/fjoin builtins and --profile core.

Covers docs/design/rynorlang-selfhost.md §§3/6 (host side):
exact file bytes in and out, err-code table (NOTFOUND/OORANGE/
NOMEM/IO), path-join rules, core-dialect exclusions with
SEM_PROFILE_EXCLUDED, reserved-name collisions, RIR verifier
pins, CLI plumbing, and the status<str> err-binding regression.
The guest baby compiler and proof chain arrive in later slices;
this suite must stay green through all of them.
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402

ANALYZER_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
RT_ASM_PATH = ROOT / "tools" / "rynorlang" / "runtime" / "rt_linux.asm"

CORE_OK = "fn main(): int { print(len(\"abc\")); return 0; }\n"

CORE_EXCLUSIONS = {
    "map type": "fn main(): int { let m: map<int,int,4> = {}; print(m); return 0; }",
    "result type": "fn main(): int { let r: result<int,int> = ok(1); print(1); return 0; }",
    "ok ctor": "fn f(): result<int,int> { return ok(1); }\nfn main(): int { return 0; }",
    "err ctor": "fn f(): result<int,int> { return err(0); }\nfn main(): int { return 0; }",
    "insert": "fn main(): int { let m: map<int,int,4> = {}; print(insert(m, 1, 2)); return 0; }",
    "get": "fn main(): int { let m: map<int,int,4> = {}; print(get(m, 1)); return 0; }",
    "print map": "fn main(): int { let m: map<int,int,4> = {}; print(m); return 0; }",
    "print result": "fn main(): int { let r: result<int,int> = ok(1); print(r); return 0; }",
    "match result": ("fn main(): int { let r: result<int,int> = ok(1); match r { "
                     "ok(v) => { print(v); }, err(e) => { print(e); } } return 0; }"),
    "nested map payload": "fn main(): int { let l: list<map<int,int,2>,2> = []; print(l); return 0; }",
    "record map field": "record R { m: map<int,int,2> }\nfn main(): int { return 0; }",
}


def _toolchain():
    nasm = shutil.which("nasm") or shutil.which("nasm.exe")
    if nasm is None:
        return None
    for linker in ("ld.lld", "ld"):
        if shutil.which(linker):
            return (nasm, linker)
    return None


TOOLCHAIN = _toolchain()


def _write_data(directory, name="data.txt", content=b"hello-from-file"):
    target = Path(directory) / name
    target.write_bytes(content)
    return target


class FreadAcceptTests(unittest.TestCase):
    def test_01_read_exact_bytes(self):
        directory = tempfile.mkdtemp(prefix="selfhost-")
        self.addCleanup(shutil.rmtree, directory, True)
        target = _write_data(directory)
        src = (f"fn main(): int {{\n"
               f"  let r: status<str> = fread(\"{target}\", 0, 100);\n"
               f"  match r {{\n"
               f"    ok(v) => {{ print(v); return 0; }},\n"
               f"    err(e) => {{ print(e); return 1; }}\n"
               f"  }}\n"
               f"}}\n")
        result = analyzer.analyze(src, "f.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "f.rl")
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"], outcome)
        self.assertEqual((outcome["exit"], "".join(emitted)), (0, "hello-from-file"))

    def test_02_offset_read_and_empty_at_end(self):
        directory = tempfile.mkdtemp(prefix="selfhost-")
        self.addCleanup(shutil.rmtree, directory, True)
        target = _write_data(directory, content=b"abcdefghij")
        src = (f"fn main(): int {{\n"
               f"  let a: status<str> = fread(\"{target}\", 4, 3);\n"
               f"  match a {{\n"
               f"    ok(v) => {{ print(v); }},\n"
               f"    err(e) => {{ print(e); return 1; }}\n"
               f"  }}\n"
               f"  let b: status<str> = fread(\"{target}\", 10, 5);\n"
               f"  match b {{\n"
               f"    ok(v) => {{ print(len(v)); }},\n"
               f"    err(e) => {{ print(e); return 2; }}\n"
               f"  }}\n"
               f"  return 0;\n"
               f"}}\n")
        result = analyzer.analyze(src, "f.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "f.rl")
        self.assertIsNone(error, error)
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"], outcome)
        self.assertEqual((outcome["exit"], "".join(emitted)), (0, "efg0"))

    def test_03_fjoin_rules(self):
        src = ("fn main(): int {\n"
               "  let a: status<str> = fjoin(\"/src\", \"a.rl\");\n"
               "  match a {\n"
               "    ok(v) => { print(v); },\n"
               "    err(e) => { print(e); return 1; }\n"
               "  }\n"
               "  let b: status<str> = fjoin(\"\", \"a.rl\");\n"
               "  match b {\n"
               "    ok(v) => { print(v); },\n"
               "    err(e) => { print(e); return 2; }\n"
               "  }\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "j.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "j.rl")
        self.assertIsNone(error, error)
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"], outcome)
        self.assertEqual((outcome["exit"], "".join(emitted)), (0, "/src/a.rla.rl"))


class FreadRejectTests(unittest.TestCase):
    def _code_of(self, src):
        result = analyzer.analyze(src, "t.rl")
        self.assertFalse(result.ok)
        return result.diagnostic.code

    def test_04_arity_and_type_checks(self):
        self.assertEqual(self._code_of(
            'fn main(): int { print(fread("a")); return 0; }'), "SEM_ARITY_MISMATCH")
        self.assertEqual(self._code_of(
            'fn main(): int { print(fread(1, 0, 1)); return 0; }'), "SEM_TYPE_MISMATCH")
        self.assertEqual(self._code_of(
            'fn main(): int { print(fread("a", "b", 1)); return 0; }'), "SEM_TYPE_MISMATCH")
        self.assertEqual(self._code_of(
            'fn main(): int { print(fjoin("a")); return 0; }'), "SEM_ARITY_MISMATCH")
        self.assertEqual(self._code_of(
            'fn main(): int { print(fjoin(1, "a")); return 0; }'), "SEM_TYPE_MISMATCH")

    def test_05_runtime_err_codes(self):
        directory = tempfile.mkdtemp(prefix="selfhost-")
        self.addCleanup(shutil.rmtree, directory, True)
        target = _write_data(directory, content=b"12345")
        cases = [
            (f'fread("{directory}/missing", 0, 10)', 2),
            (f'fread("{target}", 99, 10)', 3),
            (f'fread("{target}", 0 - 1, 10)', 3),
            (f'fread("{target}", 0, 16385)', 3),
            ('fjoin("/a", "")', 3),
            ('fjoin("/a", "/b")', 3),
        ]
        for expr, code in cases:
            with self.subTest(expr=expr):
                src = (f"fn main(): int {{\n"
                       f"  let r: status<str> = {expr};\n"
                       f"  match r {{\n"
                       f"    ok(v) => {{ print(v); return 100; }},\n"
                       f"    err(e) => {{ print(e); return 0; }}\n"
                       f"  }}\n"
                       f"}}\n")
                result = analyzer.analyze(src, "t.rl")
                self.assertTrue(result.ok, result.diagnostic)
                module, error = rir.build_rir(result.ast, "t.rl")
                self.assertIsNone(error, error)
                emitted: list = []
                outcome = oracle.run_rir(module, out=emitted)
                self.assertIsNone(outcome["trapped"], outcome)
                self.assertEqual((outcome["exit"], "".join(emitted)), (0, str(code)))

    def test_06_reserved_names(self):
        for src in ("fn fread(): int { return 1; }\nfn main(): int { return 0; }",
                    "fn fjoin(): int { return 1; }\nfn main(): int { return 0; }",
                    "record fread { a: int }\nfn main(): int { return 0; }"):
            with self.subTest(src=src.splitlines()[0]):
                result = analyzer.analyze(src, "t.rl")
                self.assertFalse(result.ok)
                self.assertEqual(result.diagnostic.code, "SEM_DUPLICATE")


class CoreProfileTests(unittest.TestCase):
    def test_07_core_accepts_core(self):
        result = analyzer.analyze(CORE_OK, "t.rl", profile="core")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "t.rl")
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])

    def test_08_core_excludes_noncore(self):
        for label, src in sorted(CORE_EXCLUSIONS.items()):
            with self.subTest(cell=label):
                result = analyzer.analyze(src, "t.rl", profile="core")
                self.assertFalse(result.ok, label)
                self.assertEqual(result.diagnostic.code, "SEM_PROFILE_EXCLUDED", label)

    def test_09_default_and_strict_unaffected(self):
        for label, src in sorted(CORE_EXCLUSIONS.items()):
            with self.subTest(cell=label):
                default = analyzer.analyze(src, "t.rl")
                self.assertTrue(default.ok, (label, default.diagnostic))
                strict = analyzer.analyze(src, "t.rl", profile="strict")
                self.assertTrue(strict.ok, (label, strict.diagnostic))

    def test_10_core_allows_status_match_and_fread(self):
        src = ("fn main(): int {\n"
               "  let r: status<str> = fread(\"/x\", 0, 1);\n"
               "  match r {\n"
               "    ok(v) => { print(v); return 0; },\n"
               "    err(e) => { print(e); return 1; }\n"
               "  }\n"
               "}\n")
        result = analyzer.analyze(src, "t.rl", profile="core")
        self.assertTrue(result.ok, result.diagnostic)


class VerifierTests(unittest.TestCase):
    def test_11_verifier_pins_new_ops(self):
        result = analyzer.analyze(CORE_OK, "t.rl")
        module, error = rir.build_rir(result.ast, "t.rl")
        self.assertIsNone(error)
        tampered = __import__("json").loads(__import__("json").dumps(module))
        tampered["funcs"][0]["blocks"][0]["instrs"].append(
            {"op": "str_fread", "dst": "%0", "type": "int",
             "path": "%0", "offset": "%0", "length": "%0"})
        problems = rir.verify_module(tampered)
        self.assertTrue(any("str_fread result must be status<str>" in p for p in problems), problems)

    def test_12_status_str_err_binding_regression(self):
        # _status_payload_type returned the payload type for ErrPat on
        # status (right only when the payload happens to be int); a
        # status<str> err binding must lower to int and run.
        src = ("fn main(): int {\n"
               "  let r: status<str> = fread(\"/nope\", 0, 1);\n"
               "  match r {\n"
               "    ok(v) => { print(v); return 7; },\n"
               "    err(e) => { print(e); return 0; }\n"
               "  }\n"
               "}\n")
        result = analyzer.analyze(src, "t.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "t.rl")
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"], outcome)
        self.assertEqual((outcome["exit"], "".join(emitted)), (0, "2"))


class CliTests(unittest.TestCase):
    def test_13_cli_profile_values(self):
        directory = tempfile.mkdtemp(prefix="selfhost-")
        self.addCleanup(shutil.rmtree, directory, True)
        probe = Path(directory) / "probe.rl"
        probe.write_text(CORE_OK, encoding="utf-8")
        for profile, code in (("core", 0), ("bogus", 2)):
            with self.subTest(profile=profile):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / "rynorlang" / "analyze.py"),
                     "--profile", profile, str(probe)],
                    capture_output=True, text=True, timeout=30, cwd=str(ROOT))
                self.assertEqual(proc.returncode, code, proc.stderr)

    def test_14_no_sign_extended_negatives_in_runtime(self):
        # mov eax,-N zero-extends (positive!); err returns must sign-extend.
        text = RT_ASM_PATH.read_text(encoding="utf-8")
        bad = [line for line in text.splitlines()
               if "mov eax, -" in line and "syscall" not in line]
        self.assertEqual(bad, [])


@unittest.skipUnless(TOOLCHAIN, "native execution unavailable (nasm + linker required)")
class SelfhostDifferentialTests(unittest.TestCase):
    def _native(self, src, workdir):
        from tools.rynorlang import program as progmod
        arts, error = progmod.build_program(src, "t.rl", workdir, prog="prog")
        self.assertIsNone(error, error)
        result, error = progmod.run_program(arts["exe"])
        self.assertIsNone(error, error)
        self.assertIsNone(result["signal"], result)
        return result["exit"], result["stdout"].decode("ascii")

    def _oracle(self, src):
        result = analyzer.analyze(src, "t.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "t.rl")
        self.assertIsNone(error, error)
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"], outcome)
        return outcome["exit"], "".join(emitted)

    def test_15_fread_fjoin_native_matches_oracle(self):
        directory = tempfile.mkdtemp(prefix="selfhost-")
        self.addCleanup(shutil.rmtree, directory, True)
        target = _write_data(directory, content=b"abcdef")
        src = (f"fn main(): int {{\n"
               f"  let r: status<str> = fread(\"{target}\", 1, 3);\n"
               f"  match r {{\n"
               f"    ok(v) => {{ print(v); }},\n"
               f"    err(e) => {{ print(e); return 1; }}\n"
               f"  }}\n"
               f"  let j: status<str> = fjoin(\"{directory}\", \"data.txt\");\n"
               f"  match j {{\n"
               f"    ok(v) => {{ print(v); }},\n"
               f"    err(e) => {{ print(e); return 2; }}\n"
               f"  }}\n"
               f"  let m: status<str> = fread(\"{directory}/missing\", 0, 3);\n"
               f"  match m {{\n"
               f"    ok(v) => {{ print(v); return 3; }},\n"
               f"    err(e) => {{ print(e); }}\n"
               f"  }}\n"
               f"  return 0;\n"
               f"}}\n")
        want = self._oracle(src)
        self.assertEqual(want, (0, f"bcd{directory}/data.txt2"))
        work = Path(tempfile.mkdtemp(prefix="selfhost-nat-"))
        try:
            self.assertEqual(self._native(src, work), want)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_16_core_program_runs_natively(self):
        want = self._oracle(CORE_OK)
        self.assertEqual(want, (0, "3"))
        work = Path(tempfile.mkdtemp(prefix="selfhost-nat-"))
        try:
            self.assertEqual(self._native(CORE_OK, work), want)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
