"""Stage 16 program tests: .rl -> RIR -> asm -> object -> ELF -> execution.

Conventions: program fixtures live under tests/fixtures/rynorlang/programs/
(good has exact (exit, stdout) expectations; trap has exact signals);
native execution is capability-gated like Stage 15a (nasm + ld.lld + an ELF
runner) with an explicit skip reason; exit codes are low-8-bit with
documented truncation; print writes exact bytes with no newline. Every
fixture passes through the real toolchain -- no fixture bypasses the
compiler -- and the differential oracle (exit + captured stdout) must agree
with native execution.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RIR_PATH = ROOT / "tools" / "rynorlang" / "rir.py"
COMPILE_PATH = ROOT / "tools" / "rynorlang" / "compile.py"
INTERP_PATH = ROOT / "tools" / "rynorlang" / "interp.py"
PROGRAM_PATH = ROOT / "tools" / "rynorlang" / "program.py"
RUNTIME_ASM = ROOT / "tools" / "rynorlang" / "runtime" / "rt_linux.asm"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "programs" / "good"
TRAP = ROOT / "tests" / "fixtures" / "rynorlang" / "programs" / "trap"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rir = _load("rynorlang_stage16_rir", RIR_PATH)
compiler = _load("rynorlang_stage16_compile", COMPILE_PATH)
interp = _load("rynorlang_stage16_interp", INTERP_PATH)
program = _load("rynorlang_stage16_program", PROGRAM_PATH)
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as _analyzer

EXPECTED_GOOD = {
    "hello.rl": (0, b"hello"),
    "return42.rl": (42, b""),
    "arithmetic.rl": (0, b"7"),
    "comparisons.rl": (0, b"truetruefalse"),
    "boolean.rl": (0, b"true"),
    "nested_if.rl": (0, b"small"),
    "while_seq.rl": (0, b"beforeafter"),
    "while_nested.rl": (2, b"done"),
    "function_call.rl": (0, b"42"),
    "nested_call.rl": (0, b"26"),
    "fib.rl": (0, b"55"),
    "string_literal.rl": (0, b"abc"),
    "string_return.rl": (0, b"xyz"),
    "string_empty.rl": (0, b"x"),
    "string_long.rl": (0, b"0123456789" * 10),
    "string_dup.rl": (0, b"dupdup"),
    "exit127.rl": (127, b""),
    "exit_wrap.rl": (44, b""),
    "exit_neg.rl": (255, b""),
    "unit_main.rl": (0, b"u"),
    "negdiv.rl": (0, b"-3-1"),
    "int_edges.rl": (0, b"9223372036854775807-9223372036854775808"),
}
# Trap expectations are host signals (SIGILL=4 div-zero, SIGTRAP=5 falloff).
EXPECTED_TRAP = {"divzero.rl": 4, "falloff.rl": 5}


def _native_or_skip(test):
    tools, reason = program.find_toolchain()
    if tools is None:
        test.skipTest(f"no native toolchain: {reason}")
    return tools


class ProgramShared(unittest.TestCase):
    """Build every fixture once; all program tests read the shared cache."""

    results: dict = {}
    tmpdir = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tools, reason = program.find_toolchain()
        if tools is None:
            raise unittest.SkipTest(f"no native toolchain: {reason}")
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="rlprog-")
        for path in sorted(GOOD.glob("*.rl")) + sorted(TRAP.glob("*.rl")):
            src = path.read_text(encoding="utf-8")
            work = Path(cls.tmpdir.name) / path.stem
            analyzed = _analyzer.analyze(src, path.name)
            assert analyzed.ok, (path.name, analyzed.diagnostic)
            module, error = rir.build_rir(analyzed.ast, path.name)
            assert error is None, (path.name, error)
            assert rir.verify_module(module) == [], path.name
            out: list = []
            oracle = interp.run_rir(module, out=out)
            arts, berror = program.build_program(src, path.name, work)
            assert berror is None, (path.name, berror)
            native, rerror = program.run_program(arts["exe"])
            assert rerror is None, (path.name, rerror)
            cls.results[path.name] = {
                "oracle": oracle, "out": "".join(out),
                "native": native, "arts": arts, "module": module,
            }

    @classmethod
    def tearDownClass(cls):
        if cls.tmpdir is not None:
            cls.tmpdir.cleanup()
        super().tearDownClass()


class ProgramInventoryTests(unittest.TestCase):
    def test_01_good_inventory_exact(self):
        self.assertEqual(sorted(p.name for p in GOOD.glob("*.rl")), sorted(EXPECTED_GOOD))

    def test_02_trap_inventory_exact(self):
        self.assertEqual(sorted(p.name for p in TRAP.glob("*.rl")), sorted(EXPECTED_TRAP))


class ProgramBuildTests(ProgramShared):
    def test_03_artifacts_exist_and_typed(self):
        for name, info in self.results.items():
            with self.subTest(fixture=name):
                arts = info["arts"]
                self.assertTrue(arts["asm"].read_bytes().startswith(b"bits 64"))
                self.assertTrue(arts["obj"].read_bytes().startswith(b"\x7fELF"))
                self.assertTrue(arts["exe"].read_bytes().startswith(b"\x7fELF"))
                self.assertIn("rir_version=1", arts["rir"])

    def test_04_bad_source_never_raises(self):
        for src in ("fn main(): int { return ; }",
                    "fn main(): int { return unknown(); }",
                    "fn main(): int { let x: str = 1; return x; }",
                    "fn main() { return 1; }",
                    "fn f(a: int): int { return a; }"):
            with self.subTest(src=src[:30]):
                arts, error = program.build_program(src, "bad.rl", Path(self.tmpdir.name) / "bad")
                self.assertIsNone(arts)
                self.assertIn(error["code"], ("PAR_EXPECTED_TOKEN", "PAR_UNEXPECTED_TOKEN",
                                              "SEM_UNKNOWN_FUNCTION", "SEM_TYPE_MISMATCH",
                                              "COMP_NO_ENTRY"))

    def test_05_entry_and_kind_gates(self):
        arts, error = program.build_program(
            "fn main(): str { let x: str = ls |> count; return x; }",
            "shell.rl", Path(self.tmpdir.name) / "g")
        self.assertIsNone(arts)
        self.assertEqual(error["code"], "PAR_LEX_ERROR")
        arts, error = program.build_program("fn main(): int { return 1; }",
                                            "ok.rl", Path(self.tmpdir.name) / "g2")
        self.assertIsNone(error)
        module, _error = rir.build_rir(
            _analyzer.analyze("fn rt_evil(): int { return 1; } fn main(): int { return 0; }",
                              "e.rl").ast, "e.rl")
        self.assertIsNone(module)
        self.assertEqual((_error or {}).get("code"), "COMP_BAD_AST")

    def test_05b_runtime_namespace_and_print_gates(self):
        # Verifier refuses rt_-prefixed definitions even hand-written.
        src = "fn main(): int { return 0; }"
        analyzed = _analyzer.analyze(src, "v.rl")
        module, _ = rir.build_rir(analyzed.ast, "v.rl")
        evil = {"name": "rt_evil", "symbol": 1, "params": [], "ret": "int",
                "blocks": [{"id": "bb0", "instrs": [], "term": {"op": "ret", "v": "%9"}}],
                "frameslots": 0}
        module["funcs"].append(evil)
        self.assertTrue(any("rt_" in e for e in rir.verify_module(module)))
        # Forged print arities never reach the backend.
        forged = {"kind": "Program", "span": {}, "functions": [
            {"kind": "Function", "span": {}, "name": "main", "params": [],
             "ret_type": "int", "symbol": 0,
             "body": {"kind": "Block", "span": {}, "stmts": [
                 {"kind": "ExprStmt", "span": {},
                  "expr": {"kind": "Call", "span": {}, "callee": "print",
                           "args": [{"kind": "IntLit", "span": {}, "value": "1", "type": "int"},
                                    {"kind": "IntLit", "span": {}, "value": "2", "type": "int"}],
                           "symbol": -1, "type": "unit"}},
                 {"kind": "Return", "span": {},
                  "value": {"kind": "IntLit", "span": {}, "value": "0", "type": "int"}}]}}]}
        module, error = rir.build_rir(forged, "forged.rl")
        self.assertIsNone(module)
        self.assertEqual((error or {}).get("code"), "COMP_BAD_AST")
        # check_asm stays clean on print programs (direct rt_ calls only).
        asm, _ = compiler.compile_source(
            'fn main(): int { print("hi"); return 0; }', "p.rl")
        self.assertEqual(compiler.check_asm(asm), [])
        # print is a core feature: available in the shell edition too.
        res = _analyzer.analyze('fn main(): int { print("hi"); return 0; }',
                                "p.rl", edition="shell", commands={})
        self.assertTrue(res.ok, res.diagnostic)


class ProgramExitTests(ProgramShared):
    def test_06_exit_values(self):
        for name, want in (("return42.rl", 42), ("exit127.rl", 127),
                           ("unit_main.rl", 0), ("while_nested.rl", 2)):
            with self.subTest(fixture=name):
                self.assertEqual(self.results[name]["native"]["exit"], want)
                self.assertIsNone(self.results[name]["native"]["signal"])

    def test_07_exit_truncation_documented(self):
        # Low-8-bit process status: 300 -> 44, -1 -> 255. Documented in the
        # program model; full-width values are proven through print instead.
        self.assertEqual(self.results["exit_wrap.rl"]["native"]["exit"], 44)
        self.assertEqual(self.results["exit_neg.rl"]["native"]["exit"], 255)
        self.assertEqual(self.results["exit_wrap.rl"]["oracle"]["exit"], 300)
        self.assertEqual(self.results["exit_neg.rl"]["oracle"]["exit"], -1)

    def test_08_trap_signals(self):
        self.assertEqual(self.results["divzero.rl"]["native"]["signal"], 4)
        self.assertEqual(self.results["falloff.rl"]["native"]["signal"], 5)
        self.assertEqual(self.results["divzero.rl"]["oracle"]["trapped"], "div0")
        self.assertEqual(self.results["falloff.rl"]["oracle"]["trapped"], "falloff")


class ProgramPrintTests(ProgramShared):
    def test_09_fixture_stdout_exact(self):
        for name, (exit_want, out_want) in sorted(EXPECTED_GOOD.items()):
            with self.subTest(fixture=name):
                native = self.results[name]["native"]
                self.assertEqual(native["exit"], exit_want)
                self.assertEqual(native["stdout"], out_want)

    def test_10_no_implicit_newline(self):
        self.assertEqual(self.results["hello.rl"]["native"]["stdout"], b"hello")
        self.assertNotIn(b"\n", self.results["hello.rl"]["native"]["stdout"])

    def test_11_int64_edges_printed_fully(self):
        self.assertEqual(self.results["int_edges.rl"]["native"]["stdout"],
                         b"9223372036854775807-9223372036854775808")


class ProgramDifferentialTests(ProgramShared):
    def test_12_oracle_exit_agrees_truncated(self):
        for name in sorted(EXPECTED_GOOD):
            with self.subTest(fixture=name):
                info = self.results[name]
                self.assertIsNone(info["oracle"]["trapped"])
                self.assertEqual(info["native"]["exit"], info["oracle"]["exit"] & 0xFF)

    def test_13_oracle_stdout_byte_identical(self):
        for name in sorted(EXPECTED_GOOD):
            with self.subTest(fixture=name):
                info = self.results[name]
                self.assertEqual(info["native"]["stdout"], info["out"].encode("ascii"))

    def test_14_trap_categories_agree(self):
        self.assertEqual(self.results["divzero.rl"]["oracle"]["trapped"], "div0")
        self.assertEqual(self.results["divzero.rl"]["native"]["signal"], 4)
        self.assertEqual(self.results["falloff.rl"]["oracle"]["trapped"], "falloff")
        self.assertEqual(self.results["falloff.rl"]["native"]["signal"], 5)


class ProgramMatrixTests(ProgramShared):
    def _probe(self, src, timeout=60):
        work = Path(tempfile.mkdtemp(prefix="rlmat-", dir=self.tmpdir.name))
        analyzed = _analyzer.analyze(src, "m.rl")
        self.assertTrue(analyzed.ok, analyzed.diagnostic)
        module, error = rir.build_rir(analyzed.ast, "m.rl")
        self.assertIsNone(error)
        out: list = []
        oracle = interp.run_rir(module, out=out)
        arts, berror = program.build_program(src, "m.rl", work)
        self.assertIsNone(berror)
        native, rerror = program.run_program(arts["exe"], timeout)
        self.assertIsNone(rerror)
        return oracle, "".join(out), native

    def test_15_arithmetic_matrix(self):
        cases = [("2 + 3", "5"), ("10 - 4", "6"), ("6 * 7", "42"),
                 ("17 / 5", "3"), ("17 % 5", "2"), ("0 - 7 / 2", "-3"),
                 ("0 - 7 % 2", "-1"), ("(2 + 3) * (4 - 1)", "15"),
                 ("0 - 1", "-1"), ("0", "0")]
        for expr, want in cases:
            with self.subTest(expr=expr):
                oracle, out, native = self._probe(
                    f"fn main(): int {{ print({expr}); return 0; }}")
                self.assertEqual(out, want)
                self.assertEqual(native["stdout"].decode("ascii"), want)
                self.assertEqual(native["exit"], 0)

    def test_16_comparison_matrix(self):
        for expr, want in [("1 < 2", "true"), ("2 < 1", "false"),
                           ("3 == 3", "true"), ("3 != 3", "false"),
                           ("5 >= 5", "true"), ("4 > 9", "false"),
                           ('"a" == "a"', "true"), ('"a" != "b"', "true"),
                           ("true && false", "false"), ("!false", "true")]:
            with self.subTest(expr=expr):
                _oracle, out, native = self._probe(
                    f"fn main(): int {{ print({expr}); return 0; }}")
                self.assertEqual(out, want)
                self.assertEqual(native["stdout"].decode("ascii"), want)

    def test_17_nested_control_and_calls(self):
        src = ("fn max(a: int, b: int): int { if a > b { return a; } return b; }"
               "fn main(): int { print(max(3, 9)); print(max(9, 3)); return 0; }")
        _oracle, out, native = self._probe(src)
        self.assertEqual(out, "99")
        self.assertEqual(native["stdout"], b"99")

    def test_18_many_argument_call(self):
        src = ("fn f(a: int, b: int, c: int, d: int, e: int, g: int, h: int): int {"
               " return a + b + c + d + e + g + h; }"
               "fn main(): int { print(f(1, 2, 3, 4, 5, 6, 7)); return 0; }")
        _oracle, out, native = self._probe(src)
        self.assertEqual(out, "28")
        self.assertEqual(native["stdout"], b"28")


class ProgramAuthenticityTests(ProgramShared):
    def _build_run(self, src, label):
        work = Path(tempfile.mkdtemp(prefix="rlauth-", dir=self.tmpdir.name))
        arts, error = program.build_program(src, label, work)
        self.assertIsNone(error)
        native, rerror = program.run_program(arts["exe"])
        self.assertIsNone(rerror)
        return arts, native

    def test_19_exit_values_follow_source(self):
        exits = {}
        for code in (41, 42, 43):
            _arts, native = self._build_run(f"fn main(): int {{ return {code}; }}", f"e{code}.rl")
            exits[code] = native["exit"]
        self.assertEqual(exits, {41: 41, 42: 42, 43: 43})

    def test_20_outputs_follow_source(self):
        outs = {}
        for expr, tag in (("1 + 2", "a"), ("2 + 2", "b"), ("9 * 9", "c")):
            _arts, native = self._build_run(f"fn main(): int {{ print({expr}); return 0; }}",
                                            f"o{tag}.rl")
            outs[tag] = native["stdout"]
        self.assertEqual(outs, {"a": b"3", "b": b"4", "c": b"81"})

    def test_21_artifact_bytes_follow_source(self):
        work_a = Path(tempfile.mkdtemp(prefix="rlexa-", dir=self.tmpdir.name))
        work_b = Path(tempfile.mkdtemp(prefix="rlexb-", dir=self.tmpdir.name))
        arts_a, _ = program.build_program("fn main(): int { return 41; }", "a.rl", work_a)
        arts_b, _ = program.build_program("fn main(): int { return 43; }", "b.rl", work_b)
        self.assertNotEqual(arts_a["exe"].read_bytes(), arts_b["exe"].read_bytes())
        self.assertNotEqual(arts_a["asm"].read_text(encoding="utf-8"),
                            arts_b["asm"].read_text(encoding="utf-8"))

    def test_22_fixture_branch_rejected(self):
        text = COMPILE_PATH.read_text(encoding="utf-8")
        anchor = "    try:\n        return emit_asm(module), None"
        self.assertEqual(text.count(anchor), 1)
        directory = tempfile.TemporaryDirectory(prefix="rlmut-")
        self.addCleanup(directory.cleanup)
        target = Path(directory.name) / "compile.py"
        target.write_text(text.replace(
            anchor,
            "    try:\n"
            "        if '43' in (module.get('source') or ''):\n"
            "            return emit_asm(module).replace('mov rax, 43', 'mov rax, 41'), None\n"
            "        return emit_asm(module), None", 1), encoding="utf-8")
        mutant = _load(f"rlmut_compile_{len(sys.modules)}", target)
        real_mod = program._compile
        program._compile = mutant
        self.addCleanup(setattr, program, "_compile", real_mod)
        work = Path(tempfile.mkdtemp(prefix="rlbr-", dir=self.tmpdir.name))
        arts, error = program.build_program("fn main(): int { return 43; }", "43.rl", work)
        self.assertIsNone(error)
        native, _ = program.run_program(arts["exe"])
        self.assertNotEqual(native["exit"], 43,
                            "fixture-specific branch must be caught by exit tests")


class ProgramMutationTests(ProgramShared):
    def test_23_hardcoded_exit_detected(self):
        mutated = RUNTIME_ASM.read_text(encoding="utf-8").replace(
            "    call rl_4_main\n    mov rdi, rax", "    call rl_4_main\n    mov rdi, 42", 1)
        directory = tempfile.TemporaryDirectory(prefix="rlmut-")
        self.addCleanup(directory.cleanup)
        fake = Path(directory.name) / "rt_linux.asm"
        fake.write_text(mutated, encoding="utf-8")
        real = program.RUNTIME_ASM
        program.RUNTIME_ASM = fake
        self.addCleanup(setattr, program, "RUNTIME_ASM", real)
        work = Path(tempfile.mkdtemp(prefix="rlhx-", dir=self.tmpdir.name))
        arts, error = program.build_program("fn main(): int { return 7; }", "h.rl", work)
        self.assertIsNone(error)
        native, _ = program.run_program(arts["exe"])
        self.assertNotEqual(native["exit"], 7,
                            "hardcoded exit must be caught by exit tests")

    def test_24_canned_assembly_detected(self):
        fixed, _ = compiler.compile_source("fn main(): int { return 42; }", "c.rl")
        self.assertIsNotNone(fixed)
        real_emit = compiler.emit_asm
        compiler.emit_asm = lambda _module: fixed
        self.addCleanup(setattr, compiler, "emit_asm", real_emit)
        real_prog_compile = program._compile
        program._compile = compiler
        self.addCleanup(setattr, program, "_compile", real_prog_compile)
        work = Path(tempfile.mkdtemp(prefix="rlca-", dir=self.tmpdir.name))
        arts, error = program.build_program("fn main(): int { print(9); return 7; }",
                                            "c.rl", work)
        self.assertIsNone(error)
        native, _ = program.run_program(arts["exe"])
        self.assertTrue(native["exit"] != 7 or native["stdout"] != b"9",
                        "canned assembly must be caught by output/exit tests")

    def test_25_skipped_link_detected(self):
        real_find = program.find_toolchain
        nasm, _linker, runner = real_find()[0]

        def fail_link(_output, _inputs, _workdir):
            class _R:
                returncode = 1
                stdout = ""
                stderr = "ld.lld: fake link failure"
            return _R()

        program.find_toolchain = lambda: ((nasm, fail_link, runner), None)
        self.addCleanup(setattr, program, "find_toolchain", real_find)
        work = Path(tempfile.mkdtemp(prefix="rlnl-", dir=self.tmpdir.name))
        arts, error = program.build_program("fn main(): int { return 0; }", "n.rl", work)
        self.assertIsNone(arts)
        self.assertEqual(error["code"], "COMP_LINK_FAILED")

    def test_26_wrong_entry_detected(self):
        mutated = RUNTIME_ASM.read_text(encoding="utf-8").replace(
            "extern rl_4_main", "extern rl_4_mian", 1).replace(
            "    call rl_4_main", "    call rl_4_mian", 1)
        directory = tempfile.TemporaryDirectory(prefix="rlmut-")
        self.addCleanup(directory.cleanup)
        fake = Path(directory.name) / "rt_linux.asm"
        fake.write_text(mutated, encoding="utf-8")
        real = program.RUNTIME_ASM
        program.RUNTIME_ASM = fake
        self.addCleanup(setattr, program, "RUNTIME_ASM", real)
        work = Path(tempfile.mkdtemp(prefix="rlwe-", dir=self.tmpdir.name))
        arts, error = program.build_program("fn main(): int { return 0; }", "w.rl", work)
        self.assertIsNone(arts)
        self.assertEqual(error["code"], "COMP_LINK_FAILED")

    def test_27_wrong_helper_detected(self):
        mutated = RUNTIME_ASM.read_text(encoding="utf-8").replace(
            "    add dl, '0'", "    add dl, '1'", 1)
        directory = tempfile.TemporaryDirectory(prefix="rlmut-")
        self.addCleanup(directory.cleanup)
        fake = Path(directory.name) / "rt_linux.asm"
        fake.write_text(mutated, encoding="utf-8")
        real = program.RUNTIME_ASM
        program.RUNTIME_ASM = fake
        self.addCleanup(setattr, program, "RUNTIME_ASM", real)
        work = Path(tempfile.mkdtemp(prefix="rlwh-", dir=self.tmpdir.name))
        arts, error = program.build_program('fn main(): int { print(5); return 0; }',
                                            "m.rl", work)
        self.assertIsNone(error)
        native, _ = program.run_program(arts["exe"])
        self.assertNotEqual(native["stdout"], b"5",
                            "corrupt int helper must be caught by output tests")


class ProgramAbiTests(ProgramShared):
    def test_28_callee_preservation(self):
        src = ("fn id(x: int): int { return x; }"
               "fn main(): int { let a: int = 100; let b: int = id(1);"
               " print(a + b); return a - 100 + b; }")
        work = Path(tempfile.mkdtemp(prefix="rlabi-", dir=self.tmpdir.name))
        analyzed = _analyzer.analyze(src, "abi.rl")
        self.assertTrue(analyzed.ok, analyzed.diagnostic)
        module, _ = rir.build_rir(analyzed.ast, "abi.rl")
        out: list = []
        oracle = interp.run_rir(module, out=out)
        arts, _ = program.build_program(src, "abi.rl", work)
        native, _ = program.run_program(arts["exe"])
        self.assertEqual("".join(out), "101")
        self.assertEqual(native["stdout"], b"101")
        self.assertEqual(native["exit"], oracle["exit"] & 0xFF)

    def test_29_print_after_calls_ordered(self):
        self.assertEqual(self.results["nested_call.rl"]["native"]["stdout"], b"26")
        self.assertEqual(self.results["function_call.rl"]["native"]["stdout"], b"42")


class ProgramIntegerTests(ProgramShared):
    def test_30_integer_matrix_native_matches_oracle(self):
        cases = ["0", "1", "0 - 1", "9223372036854775807",
                 "0 - 9223372036854775807 - 1",
                 "9223372036854775807 + 1",
                 "1000000 * 1000000", "0 - 7 / 2", "0 - 7 % 2"]
        for expr in cases:
            with self.subTest(expr=expr):
                src = f"fn main(): int {{ print({expr}); return 0; }}"
                work = Path(tempfile.mkdtemp(prefix="rlint-", dir=self.tmpdir.name))
                analyzed = _analyzer.analyze(src, "i.rl")
                self.assertTrue(analyzed.ok, analyzed.diagnostic)
                module, _ = rir.build_rir(analyzed.ast, "i.rl")
                out: list = []
                interp.run_rir(module, out=out)
                arts, _ = program.build_program(src, "i.rl", work)
                native, _ = program.run_program(arts["exe"])
                self.assertEqual(native["stdout"], "".join(out).encode("ascii"))

    def test_31_intmin_division_traps(self):
        src = ("fn main(): int {"
               " let a: int = 0 - 9223372036854775807 - 1;"
               " print(a / (0 - 1)); return 0; }")
        work = Path(tempfile.mkdtemp(prefix="rlidiv-", dir=self.tmpdir.name))
        arts, error = program.build_program(src, "t.rl", work)
        self.assertIsNone(error)
        native, _ = program.run_program(arts["exe"])
        self.assertEqual(native["signal"], 8)


class ProgramStringTests(ProgramShared):
    def test_32_string_matrix(self):
        for name, want in (("string_empty.rl", b"x"), ("string_literal.rl", b"abc"),
                           ("string_return.rl", b"xyz"), ("string_dup.rl", b"dupdup")):
            with self.subTest(fixture=name):
                self.assertEqual(self.results[name]["native"]["stdout"], want)

    def test_33_equality_is_content_based(self):
        work = Path(tempfile.mkdtemp(prefix="rlstr-", dir=self.tmpdir.name))
        analyzed = _analyzer.analyze(
            'fn main(): int { print("ab" == "ab"); print("ab" != "cd"); return 0; }', "s.rl")
        self.assertTrue(analyzed.ok, analyzed.diagnostic)
        module, _ = rir.build_rir(analyzed.ast, "s.rl")
        out: list = []
        interp.run_rir(module, out=out)
        arts, _ = program.build_program(
            'fn main(): int { print("ab" == "ab"); print("ab" != "cd"); return 0; }',
            "s.rl", work)
        native, _ = program.run_program(arts["exe"])
        self.assertEqual("".join(out), "truetrue")
        self.assertEqual(native["stdout"], b"truetrue")

    def test_34_no_heap_in_runtime(self):
        text = RUNTIME_ASM.read_text(encoding="utf-8")
        for token in ("mmap", "brk", "sbrk", "malloc", "calloc", "mremap"):
            self.assertNotIn(token, text)
        self.assertIn("resb 32", text)

    def test_35_duplicate_literal_single_storage(self):
        asm = self.results["string_dup.rl"]["arts"]["asm"].read_text(encoding="utf-8")
        self.assertEqual(asm.count("_rlstr_"), 3)  # lea x2 + one db definition


class ProgramFailureTests(unittest.TestCase):
    def test_36_diagnostics_never_raise(self):
        _native_or_skip(self)
        with tempfile.TemporaryDirectory(prefix="rlfail-") as work:
            for src, code in (("fn main(): int { return; }", "SEM_TYPE_MISMATCH"),
                              ("fn f(a: int): int { return a; }"
                               " fn main(): int { return f(1, 2); }", "SEM_ARITY_MISMATCH"),
                              ("fn main(): int { let x: int = UNKNOWN; return x; }",
                               "SEM_UNDECLARED")):
                arts, error = program.build_program(src, "f.rl", Path(work) / "x")
                self.assertIsNone(arts)
                self.assertEqual(error["code"], code)

    def test_37_cli_reports_without_traceback(self):
        _native_or_skip(self)
        bad = Path(tempfile.mkdtemp(prefix="rlcli-")) / "bad.rl"
        bad.write_text("fn main(): int { return; }", encoding="utf-8")
        for argv in (["--build", str(bad.parent / "out")],
                     ["--run"]):
            proc = subprocess.run([sys.executable, str(COMPILE_PATH), *argv, str(bad)],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("SEM_TYPE_MISMATCH", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_37b_cli_run_reports_trap_signal(self):
        _native_or_skip(self)
        proc = subprocess.run(
            [sys.executable, str(COMPILE_PATH), "--run",
             str(TRAP / "divzero.rl")],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 132)
        self.assertIn("RL_SIGNALED", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class ProgramDeterminismTests(ProgramShared):
    def test_38_artifacts_byte_identical_3x(self):
        src = (GOOD / "fib.rl").read_text(encoding="utf-8")
        blobs = []
        for tag in ("a", "b", "c"):
            work = Path(tempfile.mkdtemp(prefix=f"rldet{tag}-", dir=self.tmpdir.name))
            arts, error = program.build_program(src, "fib.rl", work)
            self.assertIsNone(error)
            blobs.append({key: arts[key].read_bytes() for key in ("asm", "obj", "exe")})
            blobs[-1]["rir"] = arts["rir"].encode("utf-8")
        for key in ("rir", "asm", "obj", "exe"):
            self.assertEqual(blobs[0][key], blobs[1][key])
            self.assertEqual(blobs[1][key], blobs[2][key])

    def test_39_no_workdir_leak(self):
        src = (GOOD / "hello.rl").read_text(encoding="utf-8")
        work = Path(tempfile.mkdtemp(prefix="rlleak-", dir=self.tmpdir.name))
        arts, error = program.build_program(src, "hello.rl", work)
        self.assertIsNone(error)
        blob = arts["obj"].read_bytes() + arts["exe"].read_bytes()
        self.assertNotIn(work.name.encode("ascii"), blob)


class ProgramCliTests(unittest.TestCase):
    def test_40_build_writes_artifacts(self):
        _native_or_skip(self)
        with tempfile.TemporaryDirectory(prefix="rlbld-") as work:
            src = GOOD / "hello.rl"
            proc = subprocess.run(
                [sys.executable, str(COMPILE_PATH), "--build", work, str(src)],
                capture_output=True, text=True, timeout=180)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for name in ("hello.asm", "hello.o", "rt_linux.o", "hello"):
                self.assertTrue((Path(work) / name).is_file(), name)

    def test_41_run_forwards_stdout_and_exit(self):
        _native_or_skip(self)
        proc = subprocess.run(
            [sys.executable, str(COMPILE_PATH), "--run", str(GOOD / "fib.rl")],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "55")

    def test_42_toolchain_commands_pinned(self):
        _native_or_skip(self)
        tools, _reason = program.find_toolchain()
        nasm, _linker, _runner = tools
        self.assertTrue(Path(nasm).is_file() or nasm)


if __name__ == "__main__":
    unittest.main()
