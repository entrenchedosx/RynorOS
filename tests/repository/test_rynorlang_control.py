"""Stage 19b match, break/continue, and result<T,E>.

Covers docs/design/rynorlang-control.md: narrowing matches, loop
jumps, result construction/threading, the Error convention, and the
control-flow backend. v1/19a suites must pass unchanged; native
execution is capability-gated like the Stage 15a matrix.
"""

import ast as _ast
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
from tools.rynorlang import compile as compiler  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402

PARSE_PATH = ROOT / "tools" / "rynorlang" / "parse.py"
ANALYZER_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
RIR_PATH = ROOT / "tools" / "rynorlang" / "rir.py"
COMPILE_PATH = ROOT / "tools" / "rynorlang" / "compile.py"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "control" / "good"
BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "control" / "bad"

GOOD_NAMES = {
    "match_result.rl", "match_literal.rl", "break_continue.rl",
    "error_threading.rl", "nested_match.rl",
}

BAD_CODES = {
    "match_nonexhaustive_int.rl": "SEM_TYPE_MISMATCH",
    "match_nonexhaustive_status.rl": "SEM_TYPE_MISMATCH",
    "match_nonexhaustive_bool.rl": "SEM_TYPE_MISMATCH",
    "match_unreachable.rl": "SEM_TYPE_MISMATCH",
    "match_dup_ok.rl": "SEM_DUPLICATE",
    "break_outside.rl": "SEM_TYPE_MISMATCH",
    "continue_outside.rl": "SEM_TYPE_MISMATCH",
    "match_record.rl": "SEM_TYPE_MISMATCH",
    "match_pattern_type.rl": "SEM_TYPE_MISMATCH",
    "match_ctor_on_int.rl": "SEM_TYPE_MISMATCH",
    "ok_unannotated.rl": "SEM_TYPE_MISMATCH",
    "ok_payload_mismatch.rl": "SEM_TYPE_MISMATCH",
    "result_nested.rl": "SEM_TYPE_MISMATCH",
    "match_binding_shadow.rl": "SEM_DUPLICATE",
    "break_reserved_fn.rl": "SEM_DUPLICATE",
    "match_reserved_fn.rl": "SEM_DUPLICATE",
    "result_arity.rl": "PAR_EXPECTED_TOKEN",
    "match_empty.rl": "PAR_UNEXPECTED_TOKEN",
}

GOLDEN_STDOUT = {
    "match_result.rl": "751",
    "match_literal.rl": "2215",
    "break_continue.rl": "100120",
    "error_threading.rl": "1210neg",
    "nested_match.rl": "10010119932",
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


def _build_native(source, name):
    from tools.rynorlang import program as progmod
    work = Path(tempfile.mkdtemp(prefix="ctl-diff-"))
    try:
        arts, error = progmod.build_program(source, name, work, prog="prog")
        assert error is None, error
        result, error = progmod.run_program(arts["exe"])
        assert error is None, error
        assert result["signal"] is None, result
        return result["exit"], result["stdout"].decode("ascii")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _load_mutant(path, old, new):
    text = Path(path).read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation anchor must be unique: {old!r}"
    target = Path(tempfile.mkdtemp(prefix="ctl-mut-")) / Path(path).name
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    name = f"mut_ctl_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(name, target)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)
        sys.modules.pop(name, None)
    return mod


class ControlLayoutTests(unittest.TestCase):
    def test_01_fixture_inventory_is_exact(self):
        self.assertEqual({p.name for p in GOOD.iterdir() if p.is_file()}, GOOD_NAMES)
        self.assertEqual({p.name for p in BAD.iterdir() if p.is_file()}, set(BAD_CODES))

    def test_02_no_new_lexer_tokens(self):
        from tools.rynorlang import lex as lexmod
        before = dict(lexmod.SINGLE_TOKENS)
        after = dict(lexmod.DOUBLE_TOKENS)
        self.assertNotIn(".", before)
        self.assertNotIn("=>", after)
        kinds = [t.kind for t in lexmod.lex("match x { _ => { return 0; } }", "t.rl").tokens]
        self.assertIn("EQUAL", kinds)


class ControlAcceptTests(unittest.TestCase):
    def test_03_good_fixtures_analyze(self):
        for name in sorted(GOOD_NAMES):
            with self.subTest(good=name):
                result = analyzer.analyze((GOOD / name).read_text(encoding="utf-8"), name)
                self.assertTrue(result.ok, result.diagnostic)

    def test_04_good_fixtures_build_and_verify(self):
        for name in sorted(GOOD_NAMES):
            with self.subTest(good=name):
                result = analyzer.analyze((GOOD / name).read_text(encoding="utf-8"), name)
                self.assertTrue(result.ok)
                module, error = rir.build_rir(result.ast, name)
                self.assertIsNone(error, error)
                self.assertEqual(rir.verify_module(module), [])


class ControlRejectTests(unittest.TestCase):
    def test_05_bad_fixtures_reject_with_exact_code(self):
        for name, code in sorted(BAD_CODES.items()):
            with self.subTest(bad=name):
                result = analyzer.analyze((BAD / name).read_text(encoding="utf-8"), name)
                self.assertFalse(result.ok)
                self.assertIsNone(result.ast)
                self.assertEqual(result.diagnostic.code, code)


class ControlDeterminismTests(unittest.TestCase):
    def test_06_corpus_byte_identical_3x(self):
        for name in sorted(GOOD_NAMES):
            with self.subTest(good=name):
                src = (GOOD / name).read_text(encoding="utf-8")
                first = analyzer.analyze(src, name)
                self.assertTrue(first.ok)
                module, error = rir.build_rir(first.ast, name)
                self.assertIsNone(error)
                first_text = rir.dumps(module)
                for _ in range(2):
                    again = analyzer.analyze(src, name)
                    module2, error2 = rir.build_rir(again.ast, name)
                    self.assertIsNone(error2)
                    self.assertEqual(rir.dumps(module2), first_text)


class ControlGoldenTests(unittest.TestCase):
    def test_07_match_control_flow_golden(self):
        src = ("fn main(): int {\n"
               "  let r: result<int,int> = ok(1);\n"
               "  match r {\n"
               "    ok(v) => { print(v); },\n"
               "    err(e) => { print(e); }\n"
               "  }\n"
               "  while true {\n"
               "    break;\n"
               "  }\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "golden.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "golden.rl")
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])
        text = rir.dumps(module)
        self.assertIn("= result_ok result<int,int>", text)
        self.assertIn("= status_is_ok ", text)
        self.assertIn("= unwrap_ok ", text)
        self.assertIn("= unwrap_err ", text)
        self.assertIn("jmp ", text)

    def test_08_exhaustiveness_boundary(self):
        # bool is exhaustive with both literals; int needs a wildcard.
        ok_bool = analyzer.analyze(
            "fn main(): int { match true { true => { return 1; }, false => { return 0; } } }",
            "b.rl")
        self.assertTrue(ok_bool.ok, ok_bool.diagnostic)
        bad_int = analyzer.analyze(
            "fn main(): int { match 1 { 1 => { return 1; }, 2 => { return 2; } } return 0; }",
            "i.rl")
        self.assertFalse(bad_int.ok)
        self.assertEqual(bad_int.diagnostic.code, "SEM_TYPE_MISMATCH")


@unittest.skipUnless(TOOLCHAIN, "native execution unavailable (nasm + linker required)")
class ControlDifferentialTests(unittest.TestCase):
    def _differential(self, name):
        src = (GOOD / name).read_text(encoding="utf-8")
        result = analyzer.analyze(src, name)
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, name)
        self.assertIsNone(error)
        self.assertEqual(rir.verify_module(module), [])
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"], outcome)
        native_exit, native_out = _build_native(src, name)
        self.assertEqual(native_exit, outcome["exit"])
        self.assertEqual(native_out, "".join(emitted))
        return "".join(emitted)

    def test_09_differentials_exit_and_stdout(self):
        for name in sorted(GOOD_NAMES):
            with self.subTest(good=name):
                self._differential(name)

    def test_10_golden_stdouts(self):
        for name, want in sorted(GOLDEN_STDOUT.items()):
            with self.subTest(good=name):
                self.assertEqual(self._differential(name), want)


class ControlMutationTests(unittest.TestCase):
    def _assert_removal_flips(self, path, old, new, src, code):
        baseline = analyzer.analyze(src, "<baseline>")
        self.assertFalse(baseline.ok)
        self.assertEqual(baseline.diagnostic.code, code)
        mutant = _load_mutant(path, old, new)
        result = mutant.analyze(src, "<mutant>")
        self.assertTrue(result.ok, f"removed check still rejected input: {result.diagnostic}")

    def test_11_exhaustiveness_removed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "        if not covered:\n            self._error(C_TYPE_MISMATCH, \"non-exhaustive match\"",
            "        if False and not covered:\n            self._error(C_TYPE_MISMATCH, \"non-exhaustive match\"",
            "fn main(): int { match 1 { 1 => { return 1; } } return 0; }",
            "SEM_TYPE_MISMATCH")

    def test_12_unreachable_arm_allowed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "            if covered_wild:\n                self._error(C_TYPE_MISMATCH, \"unreachable match arm\"",
            "            if False and covered_wild:\n                self._error(C_TYPE_MISMATCH, \"unreachable match arm\"",
            "fn main(): int { match 1 { _ => { return 0; }, 1 => { return 1; } } }",
            "SEM_TYPE_MISMATCH")

    def test_13_outside_loop_allowed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "                if self._loop_depth <= 0:\n                    self._error(C_TYPE_MISMATCH, f\"'{word}' outside loop\"",
            "                if False and self._loop_depth <= 0:\n                    self._error(C_TYPE_MISMATCH, f\"'{word}' outside loop\"",
            "fn main(): int { break; return 0; }",
            "SEM_TYPE_MISMATCH")

    def test_14_ctor_on_scalar_allowed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "            if shape is None or shape[0] != \"generic\" or shape[1] not in (\"status\", \"result\"):",
            "            if False and (shape is None or shape[0] != \"generic\" or shape[1] not in (\"status\", \"result\")):",
            "fn main(): int { match 1 { ok(x) => { return x; }, _ => { return 0; } } }",
            "SEM_TYPE_MISMATCH")

    def test_07b_match_plumbing_present(self):
        # Structural pin: the builder always emits the tag read, both
        # payload extractions, and branches for a full match (the shape
        # every behavioral mutant below perturbs).
        src = ("fn main(): int {\n"
               "  let r: result<int,int> = ok(1);\n"
               "  match r {\n"
               "    ok(v) => { print(v); },\n"
               "    err(e) => { print(e); }\n"
               "  }\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "t.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "t.rl")
        self.assertIsNone(error)
        text = rir.dumps(module)
        self.assertIn("status_is_ok", text)
        self.assertIn("unwrap_ok", text)
        self.assertIn("unwrap_err", text)

    def test_15_rir_break_target_swapped(self):
        mutant = _load_mutant(
            RIR_PATH,
            "        _header, exit_b = low.loops[-1]\n        low.terminate(cur, {\"op\": \"jmp\", \"tgt\": f\"bb{exit_b}\"})",
            "        _header, exit_b = low.loops[-1]\n        low.terminate(cur, {\"op\": \"jmp\", \"tgt\": f\"bb{_header}\"})")
        src = ("fn main(): int {\n"
               "  while true {\n"
               "    print(1);\n"
               "    break;\n"
               "  }\n"
               "  print(2);\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "b.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "b.rl")
        self.assertIsNone(error)
        self.assertEqual(rir.verify_module(module), [])
        mutant_module, mutant_error = mutant.build_rir(result.ast, "b.rl")
        self.assertIsNone(mutant_error)
        self.assertEqual(mutant.verify_module(mutant_module), [])
        # The only delta is break's jump target (header instead of exit);
        # everything else (including verification) is unchanged, which is
        # exactly why target-sensitivity needs its own pin.
        real_jmps = sorted(line.strip() for line in rir.dumps(module).splitlines() if line.strip().startswith("jmp "))
        mut_jmps = sorted(line.strip() for line in rir.dumps(mutant_module).splitlines() if line.strip().startswith("jmp "))
        self.assertNotEqual(real_jmps, mut_jmps)
        self.assertEqual(len(real_jmps), len(mut_jmps))

    def test_16_rir_unwrap_confusion_detected(self):
        # ok-payload int vs err-payload str differ, so swapping the
        # extraction op is type-visible (status<int>-only swaps would be
        # legitimately undetectable — same int payload both ways).
        src = ("fn main(): int {\n"
               "  let r: result<int,str> = ok(1);\n"
               "  match r {\n"
               "    ok(v) => { print(v); },\n"
               "    err(e) => { print(e); }\n"
               "  }\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "u.rl")
        module, error = rir.build_rir(result.ast, "u.rl")
        self.assertIsNone(error)
        tampered = __import__("json").loads(__import__("json").dumps(module))
        found = False
        for block in tampered["funcs"][0]["blocks"]:
            for instr in block["instrs"]:
                if instr.get("op") == "unwrap_ok":
                    instr["op"] = "unwrap_err"
                    found = True
        self.assertTrue(found)
        problems = rir.verify_module(tampered)
        self.assertTrue(any("unwrap_err result must be str" in p for p in problems), problems)


@unittest.skipUnless(TOOLCHAIN, "native execution unavailable (nasm + linker required)")
class ControlBackendMutationTests(unittest.TestCase):
    def _run_asm(self, asm_text):
        work = Path(tempfile.mkdtemp(prefix="ctl-bmut-asm-"))
        try:
            (work / "prog.asm").write_text(asm_text, encoding="utf-8")
            shutil.copy(ROOT / "tools" / "rynorlang" / "runtime" / "rt_linux.asm", work / "rt_linux.asm")
            nasm, linker = TOOLCHAIN
            subprocess.run([nasm, "-f", "elf64", "prog.asm", "-o", "prog.o"], cwd=str(work), check=True, timeout=120)
            subprocess.run([nasm, "-f", "elf64", "rt_linux.asm", "-o", "rt_linux.o"], cwd=str(work), check=True, timeout=120)
            subprocess.run([linker, "-o", "prog", "prog.o", "rt_linux.o", "--build-id=none"], cwd=str(work), check=True, timeout=120)
            proc = subprocess.run([str(work / "prog")], capture_output=True, timeout=60)
            return proc.returncode, proc.stdout.decode("ascii")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_18_arm_order_swap_diverges(self):
        # Swapping the taken/fallthrough edges of the tag branch in the
        # RIR builder inverts every match: ok inputs run err arms and
        # vice versa. Built with the mutant builder, emitted and run with
        # the real backend.
        src = ("fn main(): int {\n"
               "  let r: result<int,int> = ok(7);\n"
               "  match r {\n"
               "    ok(v) => { print(v); },\n"
               "    err(e) => { print(e); }\n"
               "  }\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "s.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "s.rl")
        self.assertIsNone(error)
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertEqual((outcome["exit"], "".join(emitted)), (0, "7"))
        text = RIR_PATH.read_text(encoding="utf-8")
        old = '        low.terminate(cur, {"op": "br", "cond": tag, "then": f"bb{arm_b}", "else": f"bb{next_b}"})'
        assert text.count(old) == 1, old
        target = Path(tempfile.mkdtemp(prefix="ctl-bmut-")) / RIR_PATH.name
        target.write_text(text.replace(
            old,
            '        low.terminate(cur, {"op": "br", "cond": tag, "then": f"bb{next_b}", "else": f"bb{arm_b}"})',
            1), encoding="utf-8")
        modname = f"mut_brir_{len(sys.modules)}"
        spec = importlib.util.spec_from_file_location(modname, target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            shutil.rmtree(target.parent, ignore_errors=True)
            sys.modules.pop(modname, None)
        mutant_module, mutant_error = mod.build_rir(result.ast, "s.rl")
        self.assertIsNone(mutant_error)
        self.assertEqual(mod.verify_module(mutant_module), [])
        asm_text = compiler.emit_asm(mutant_module)
        self.assertEqual(compiler.check_asm(asm_text), [])
        code, _out = self._run_asm(asm_text)
        self.assertNotEqual((code, _out), (0, "7"))


if __name__ == "__main__":
    unittest.main()
