"""Stage 19a aggregate values: fixtures, goldens, differentials, mutants.

Covers docs/design/rynorlang-aggregates.md: records, lists, maps,
status values, builtins, bitops, byte_at, static caps, and the
five-engine pipeline. v1 suites must pass unchanged (the gate that
keeps 19a additive); native execution is capability-gated like the
Stage 15a matrix.
"""

import ast as _ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import agtypes  # noqa: E402
from tools.rynorlang import compile as compiler  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import rir  # noqa: E402

ANALYZER_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
RIR_PATH = ROOT / "tools" / "rynorlang" / "rir.py"
COMPILE_PATH = ROOT / "tools" / "rynorlang" / "compile.py"
AGTYES_PATH = ROOT / "tools" / "rynorlang" / "agtypes.py"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "aggregates" / "good"
BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "aggregates" / "bad"

GOOD_NAMES = {
    "record_basic.rl", "record_nested.rl", "record_empty.rl",
    "record_holding.rl", "list_basic.rl", "list_nested.rl",
    "map_basic.rl", "map_probe.rl", "status_flow.rl",
    "print_format.rl", "bitops.rl", "byte_at.rl", "maxcap.rl",
    "funcs_agg.rl", "empty_coll.rl", "nesting_ok.rl",
}

BAD_CODES = {
    "rec_unknown_type.rl": "SEM_UNDECLARED",
    "rec_fn_callee.rl": "SEM_TYPE_MISMATCH",
    "rec_missing_field.rl": "SEM_ARITY_MISMATCH",
    "rec_unknown_field.rl": "SEM_UNDECLARED",
    "rec_dup_field.rl": "SEM_DUPLICATE",
    "rec_dup_decl.rl": "SEM_DUPLICATE",
    "rec_reserved_name.rl": "SEM_DUPLICATE",
    "rec_recursive.rl": "SEM_TYPE_MISMATCH",
    "rec_mutual.rl": "SEM_TYPE_MISMATCH",
    "rec_unknown_field_use.rl": "SEM_UNDECLARED",
    "rec_field_nonrecord.rl": "SEM_TYPE_MISMATCH",
    "rec_positional.rl": "SEM_TYPE_MISMATCH",
    "list_overcap.rl": "SEM_LIMIT_EXCEEDED",
    "list_cap_zero.rl": "SEM_LIMIT_EXCEEDED",
    "list_elem_mismatch.rl": "SEM_TYPE_MISMATCH",
    "list_index_nonint.rl": "SEM_TYPE_MISMATCH",
    "list_index_nonlist.rl": "SEM_TYPE_MISMATCH",
    "list_too_big.rl": "SEM_LIMIT_EXCEEDED",
    "map_bad_key.rl": "SEM_TYPE_MISMATCH",
    "map_overcap.rl": "SEM_LIMIT_EXCEEDED",
    "map_dup_key.rl": "SEM_DUPLICATE",
    "map_get_nonmap.rl": "SEM_TYPE_MISMATCH",
    "status_nested.rl": "SEM_TYPE_MISMATCH",
    "status_bare.rl": "SEM_ARITY_MISMATCH",
    "status_elem.rl": "SEM_TYPE_MISMATCH",
    "unwrap_default_mismatch.rl": "SEM_TYPE_MISMATCH",
    "is_ok_nonstatus.rl": "SEM_TYPE_MISMATCH",
    "len_record.rl": "SEM_TYPE_MISMATCH",
    "byte_at_nonstring.rl": "SEM_TYPE_MISMATCH",
    "bitop_type.rl": "SEM_TYPE_MISMATCH",
    "bitop_eq_prec.rl": "SEM_TYPE_MISMATCH",
    "tilde_bool.rl": "SEM_TYPE_MISMATCH",
    "nesting_deep.rl": "SEM_LIMIT_EXCEEDED",
    "type_arity.rl": "PAR_EXPECTED_TOKEN",
    "builtin_redefine.rl": "SEM_DUPLICATE",
    "push_arity.rl": "SEM_ARITY_MISMATCH",
}

# Exact native stdout for the canonical-format fixture (hand-verified:
# records in declaration order, lists, slot-order map, status, scalars).
PRINT_FORMAT_STDOUT = "{x: 3, y: 4}[1, 2]{a: 1}ok([1, 2])err(2)truehi42"
BITOPS_STDOUT = "473-788-264-9223372036854775808true"

# v1 scalar paths must emit byte-identical assembly (toolchain-native
# NASM text is deterministic from RIR; these pin the no-drift gate).
V1_ASM_HASHES = {
    "hello.rl": "3407f74305bc4334c53adca143839575d71cd624e40fe3b074395b436877a594",
    "fib.rl": "9a9a43d3c6b94c0d82d7c51f2224f8122c6c093497426f2205ffb40dfba530a3",
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
    """Compile+link+run via the real pipeline; returns (exit, stdout)."""
    from tools.rynorlang import program as progmod
    work = Path(tempfile.mkdtemp(prefix="agg-diff-"))
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
    mutated = text.replace(old, new, 1)
    td = tempfile.TemporaryDirectory()
    target = Path(td.name) / Path(path).name
    target.write_text(mutated, encoding="utf-8")
    name = f"mut_agg_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(name, target)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        pass
    td.cleanup()
    sys.modules.pop(name, None)
    return mod


class AggregateLayoutTests(unittest.TestCase):
    def test_01_fixture_inventory_is_exact(self):
        self.assertEqual({p.name for p in GOOD.iterdir() if p.is_file()}, GOOD_NAMES)
        self.assertEqual({p.name for p in BAD.iterdir() if p.is_file()}, set(BAD_CODES))

    def test_02_agtypes_uses_only_standard_library(self):
        tree = _ast.parse(AGTYES_PATH.read_text(encoding="utf-8"))
        imports = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, _ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertLessEqual(imports, {"__future__"})

    def test_03_canonical_type_algebra(self):
        cases = [
            ("int", ("scalar", "int"), "int", 8),
            ("list<int,4>", ("generic", "list", (("scalar", "int"), ("cap", 4))), "list<int,4>", 40),
            ("map<str,int,8>", None, "map<str,int,8>", 264),
            ("status<int>", None, "status<int>", 24),
            ("Point", ("nominal", "Point"), "Point", None),
        ]
        for text, shape, canon, size in cases:
            with self.subTest(text=text):
                node = agtypes.parse_type(text)
                self.assertIsNotNone(node)
                if shape is not None:
                    self.assertEqual(node, shape)
                self.assertEqual(agtypes.canonical(node), canon)
                self.assertIsNone(agtypes.validate_type(node))
                if size is not None:
                    self.assertEqual(agtypes.size_of(node, {}), size)
        for bad, tag in [("list<int,0>", "bad-cap"), ("list<int>", "arity"),
                         ("map<list<int,2>,int,4>", "bad-key"),
                         ("status<status<int>>", "nested-status"),
                         ("Point<int>", "unknown-base")]:
            with self.subTest(bad=bad):
                self.assertEqual(agtypes.validate_type(agtypes.parse_type(bad)), tag)
        self.assertEqual(agtypes.validate_type(agtypes.parse_type("list<int,1024>")), None)
        self.assertEqual(agtypes.check_bounded(agtypes.parse_type("list<int,1024>"), {}), "too-big")
        self.assertEqual(agtypes.check_bounded(agtypes.parse_type("list<int,1023>"), {}), None)
        self.assertEqual(agtypes.slot_width_for_size(8), 1)
        self.assertEqual(agtypes.slot_width_for_size(9), 2)
        self.assertEqual((agtypes.ERR_FULL, agtypes.ERR_NOTFOUND, agtypes.ERR_OORANGE), (1, 2, 3))
        self.assertEqual(agtypes.MAX_AGG_BYTES, 8192)
        self.assertEqual(agtypes.MAX_TYPE_NESTING, 8)


class AggregateAcceptTests(unittest.TestCase):
    def test_04_good_fixtures_analyze(self):
        for name in sorted(GOOD_NAMES):
            with self.subTest(good=name):
                result = analyzer.analyze((GOOD / name).read_text(encoding="utf-8"), name)
                self.assertTrue(result.ok, result.diagnostic)
                self.assertIsNotNone(result.ast)

    def test_05_good_fixtures_build_and_verify(self):
        for name in sorted(GOOD_NAMES):
            with self.subTest(good=name):
                result = analyzer.analyze((GOOD / name).read_text(encoding="utf-8"), name)
                self.assertTrue(result.ok)
                module, error = rir.build_rir(result.ast, name)
                self.assertIsNone(error, error)
                self.assertEqual(rir.verify_module(module), [])


class AggregateRejectTests(unittest.TestCase):
    def test_06_bad_fixtures_reject_with_exact_code(self):
        for name, code in sorted(BAD_CODES.items()):
            with self.subTest(bad=name):
                result = analyzer.analyze((BAD / name).read_text(encoding="utf-8"), name)
                self.assertFalse(result.ok)
                self.assertIsNone(result.ast)
                self.assertEqual(result.diagnostic.code, code)


class AggregateDeterminismTests(unittest.TestCase):
    def test_07_corpus_byte_identical_3x(self):
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


class AggregateGoldenTests(unittest.TestCase):
    def test_08_record_list_rir_golden(self):
        src = ("record Point { x: int, y: int }\n"
               "fn main(): int {\n"
               "  let p: Point = Point(x: 1, y: 2);\n"
               "  let l: list<int,2> = [3];\n"
               "  print(p->x + unwrap_or(l[0], 0));\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "golden.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "golden.rl")
        self.assertIsNone(error)
        self.assertEqual(rir.verify_module(module), [])
        text = rir.dumps(module)
        self.assertIn(".rectype Point : (x: int, y: int)", text)
        self.assertIn("= make_record Point(", text)
        self.assertIn("= make_list list<int,2>(", text)
        self.assertIn("= get_field int ", text)
        self.assertIn("= list_idx status<int> ", text)
        self.assertIn("= status_unwrap_or ", text)
        self.assertIn("frameslots=", text)

    def test_08b_exact_size_boundary_accepts_type_rejects_frame(self):
        # list<int,1023> is exactly 8192 bytes: the analyzer accepts the
        # type (exact-cap accept side; reject side is list_too_big.rl),
        # but placing it beside any live temp exceeds the independent
        # 1024-slot frame cap, which fails honestly (COMP_FRAME_TOO_BIG,
        # never a trap). Both caps are load-bearing by design (RFC §4).
        src = "fn main(): int { let l: list<int,1023> = [0]; return 0; }\n"
        result = analyzer.analyze(src, "boundary.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, "boundary.rl")
        self.assertIsNone(module)
        self.assertEqual((error or {}).get("code"), "COMP_FRAME_TOO_BIG")

    def test_09_v1_assembly_hashes_unchanged(self):
        base = ROOT / "tests" / "fixtures" / "rynorlang" / "programs" / "good"
        for name, digest in sorted(V1_ASM_HASHES.items()):
            with self.subTest(program=name):
                asm, error = compiler.compile_source((base / name).read_text(encoding="utf-8"), name)
                self.assertIsNone(error, error)
                self.assertEqual(hashlib.sha256(asm.encode("utf-8")).hexdigest(), digest)


@unittest.skipUnless(TOOLCHAIN, "native execution unavailable (nasm + linker required)")
class AggregateDifferentialTests(unittest.TestCase):
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

    def test_10_differentials_exit_and_stdout(self):
        for name in sorted(GOOD_NAMES):
            with self.subTest(good=name):
                self._differential(name)

    def test_11_print_format_golden_stdout(self):
        self.assertEqual(self._differential("print_format.rl"), PRINT_FORMAT_STDOUT)

    def test_12_bitops_golden_stdout(self):
        self.assertEqual(self._differential("bitops.rl"), BITOPS_STDOUT)


class AggregateMutationTests(unittest.TestCase):
    def _assert_removal_flips(self, path, old, new, src, code):
        baseline = analyzer.analyze(src, "<baseline>")
        self.assertFalse(baseline.ok)
        self.assertEqual(baseline.diagnostic.code, code)
        mutant = _load_mutant(path, old, new)
        result = mutant.analyze(src, "<mutant>")
        self.assertTrue(result.ok, f"removed check still rejected input: {result.diagnostic}")

    def test_13_list_overcap_check_removed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "                if len(kids) > cap:\n                    self._error(C_LIMIT_EXCEEDED,\n                                f\"list literal of {len(kids)} exceeds capacity {cap}\"",
            "                if False and len(kids) > cap:\n                    self._error(C_LIMIT_EXCEEDED,\n                                f\"list literal of {len(kids)} exceeds capacity {cap}\"",
            "fn main(): int { let l: list<int,1> = [1, 2]; return 0; }",
            "SEM_LIMIT_EXCEEDED")

    def test_14_record_dup_field_allowed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "            if fname in seen:\n                self._error(C_DUPLICATE, f\"duplicate field '{fname}' in record '{name}'\"",
            "            if False and fname in seen:\n                self._error(C_DUPLICATE, f\"duplicate field '{fname}' in record '{name}'\"",
            "record P { x: int, x: int }\nfn main(): int { return 0; }",
            "SEM_DUPLICATE")

    def test_15_recursion_check_returns_zero(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "            if name in busy:\n                self._error(C_TYPE_MISMATCH, f\"recursive record '{name}'\"",
            "            if name in busy:\n                return 0\n            if False:\n                self._error(C_TYPE_MISMATCH, f\"recursive record '{name}'\"",
            "record N { next: N }\nfn main(): int { return 0; }",
            "SEM_TYPE_MISMATCH")

    def test_16_cap_zero_allowed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "        elif err == \"bad-cap\":\n            self._error(C_LIMIT_EXCEEDED, \"capacity must be at least 1\", tnode.span,\n                        expected=\"N >= 1\", context=\"type\")",
            "        elif err == \"bad-cap\":\n            pass  # MUTANT: capacity floor removed",
            "fn main(): int { let l: list<int,0> = []; return 0; }",
            "SEM_LIMIT_EXCEEDED")

    def test_17_status_nesting_allowed(self):
        # Self-contained: only the annotation is wrong, so neutering the
        # nesting guard flips the whole program to accepted.
        self._assert_removal_flips(
            ANALYZER_PATH,
            "        elif err == \"nested-status\":\n            self._error(C_TYPE_MISMATCH, \"status cannot nest inside other types\", tnode.span,\n                        expected=\"non-status payload\", context=\"type\")",
            "        elif err == \"nested-status\":\n            pass  # MUTANT: nesting guard removed",
            "fn f(s: status<status<int>>): int { return 0; }\nfn main(): int { return 0; }",
            "SEM_TYPE_MISMATCH")

    def test_18_map_key_check_removed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "        elif err == \"bad-key\":\n            self._error(C_TYPE_MISMATCH, \"map keys must be int, bool, or str\", tnode.span,\n                        expected=\"int, bool, or str key\", context=\"type\")",
            "        elif err == \"bad-key\":\n            pass  # MUTANT: key guard removed",
            "fn main(): int { let m: map<list<int,2>,int,4> = {}; return 0; }",
            "SEM_TYPE_MISMATCH")

    def test_19_builtin_reserved_check_removed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "                if name in AGG_BUILTINS:\n                    self._error(C_DUPLICATE,",
            "                if False and name in AGG_BUILTINS:\n                    self._error(C_DUPLICATE,",
            "fn push(l: list<int,4>): int { return 0; }\nfn main(): int { return 0; }",
            "SEM_DUPLICATE")

    def test_20_field_existence_check_removed(self):
        self._assert_removal_flips(
            ANALYZER_PATH,
            "        for fname, ftype, _fspan in self.record_decls[otype][\"fields\"]:\n            if fname == want:\n                return ({\"kind\": \"Field\"",
            "        for fname, ftype, _fspan in self.record_decls[otype][\"fields\"]:\n            if True or fname == want:\n                return ({\"kind\": \"Field\"",
            "record P { x: int }\nfn main(): int { let p: P = P(x: 1); print(p->z); return 0; }",
            "SEM_UNDECLARED")

    def test_21_rir_list_cap_check_removed(self):
        # Hand-built over-capacity make_list (the builder would reject it
        # first by design): the verifier mutant accepts it, the real
        # verifier flags the capacity breach.
        module = {"rir_version": 1, "source": "hand.rl", "strtab": [],
                  "funcs": [{"name": "main", "symbol": 0, "params": [],
                             "ret": "int", "frameslots": 4,
                             "blocks": [{"id": "bb0",
                                         "instrs": [
                                             {"op": "const", "dst": "%0", "type": "int", "value": "1"},
                                             {"op": "const", "dst": "%1", "type": "int", "value": "2"},
                                             {"op": "make_list", "dst": "%2", "type": "list<int,1>",
                                              "args": ["%0", "%1"]}],
                                         "term": {"op": "ret", "v": "%0"}}]}]}
        mutant = _load_mutant(
            RIR_PATH,
            "        if len(args) > cap:\n            errors.append(f\"func '{name}': {where} make_list of {len(args)} exceeds capacity {cap}\")",
            "        if False and len(args) > cap:\n            errors.append(f\"func '{name}': {where} make_list of {len(args)} exceeds capacity {cap}\")")
        self.assertEqual(mutant.verify_module(module), [])
        real_problems = rir.verify_module(module)
        self.assertTrue(any("exceeds capacity" in problem for problem in real_problems), real_problems)

    def test_22_rir_width_ignorance_detected(self):
        src = "fn main(): int { let l: list<int,4> = [1, 2]; print(len(l)); return 0; }"
        result = analyzer.analyze(src, "w.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "w.rl")
        self.assertIsNone(error)
        self.assertEqual(rir.verify_module(module), [])
        frameslots = module["funcs"][0]["frameslots"]
        self.assertGreater(frameslots, 3)
        tampered = json.loads(json.dumps(module))
        tampered["funcs"][0]["frameslots"] = 3
        self.assertTrue(any("frameslots" in problem for problem in rir.verify_module(tampered)))

    def test_23_rir_field_type_check_removed(self):
        # The tampered value is never consumed observably, so only the
        # get_field-result check can fire: the mutant accepts, real flags.
        mutant = _load_mutant(
            RIR_PATH,
            "        if instr.get(\"type\") != ftype:\n            errors.append(f\"func '{name}': {where} get_field result must be {ftype}\")",
            "        if False and instr.get(\"type\") != ftype:\n            errors.append(f\"func '{name}': {where} get_field result must be {ftype}\")")
        src = ("record P { x: int, y: str }\nfn main(): int {\n"
               "  let p: P = P(x: 1, y: \"a\");\n  let a: int = p->x;\n  return 0;\n}")
        result = analyzer.analyze(src, "f.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "f.rl")
        self.assertIsNone(error)
        tampered = json.loads(json.dumps(module))
        for block in tampered["funcs"][0]["blocks"]:
            for instr in block["instrs"]:
                if instr.get("op") == "get_field":
                    # bool: same home width as int, so only the result-type
                    # check can fire (no frameslots cascade either way).
                    instr["type"] = "bool"
        self.assertEqual(mutant.verify_module(tampered), [])
        real_problems = rir.verify_module(tampered)
        self.assertTrue(any("get_field result" in problem for problem in real_problems), real_problems)


@unittest.skipUnless(TOOLCHAIN, "native execution unavailable (nasm + linker required)")
class AggregateBackendMutationTests(unittest.TestCase):
    def _native_of(self, compile_mod, src, name):
        from tools.rynorlang import program as progmod
        work = Path(tempfile.mkdtemp(prefix="agg-mut-"))
        try:
            arts, error = progmod.build_program(src, name, work, prog="prog")
            assert error is None, error
            result, error = progmod.run_program(arts["exe"])
            assert error is None, error
            return result
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _compile_with(self, path, old, new, src, name):
        text = Path(path).read_text(encoding="utf-8")
        assert text.count(old) == 1, old
        mutated = text.replace(old, new, 1)
        td = tempfile.TemporaryDirectory()
        target = Path(td.name) / Path(path).name
        target.write_text(mutated, encoding="utf-8")
        modname = f"mut_backend_{len(sys.modules)}"
        spec = importlib.util.spec_from_file_location(modname, target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            pass
        # The mutant emitter needs the real RIR (same shapes in/out).
        result = analyzer.analyze(src, name)
        assert result.ok
        module, error = rir.build_rir(result.ast, name)
        assert error is None
        assert rir.verify_module(module) == []
        asm_text = mod.emit_asm(module)
        self.assertEqual(compiler.check_asm(asm_text), [])
        td.cleanup()
        sys.modules.pop(modname, None)
        return asm_text

    def _run_asm(self, asm_text):
        import subprocess as _sub
        work = Path(tempfile.mkdtemp(prefix="agg-mut-asm-"))
        try:
            (work / "prog.asm").write_text(asm_text, encoding="utf-8")
            shutil.copy(ROOT / "tools" / "rynorlang" / "runtime" / "rt_linux.asm", work / "rt_linux.asm")
            nasm, linker = TOOLCHAIN
            _sub.run([nasm, "-f", "elf64", "prog.asm", "-o", "prog.o"], cwd=str(work), check=True, timeout=120)
            _sub.run([nasm, "-f", "elf64", "rt_linux.asm", "-o", "rt_linux.o"], cwd=str(work), check=True, timeout=120)
            _sub.run([linker, "-o", "prog", "prog.o", "rt_linux.o", "--build-id=none"], cwd=str(work), check=True, timeout=120)
            proc = _sub.run([str(work / "prog")], capture_output=True, timeout=60)
            return proc.returncode, proc.stdout.decode("ascii")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_24_sret_slot_moved_breaks_aggregate_returns(self):
        from tools.rynorlang import program as _pm
        _ = _pm
        src = ("record Point { x: int, y: int }\n"
               "fn move(p: Point, dx: int): Point { return Point(x: p->x + dx, y: p->y); }\n"
               "fn main(): int { let p: Point = Point(x: 3, y: 4); print(move(p, 10)); return 0; }\n")
        result = analyzer.analyze(src, "sret.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "sret.rl")
        self.assertIsNone(error)
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertEqual((outcome["exit"], "".join(emitted)), (0, "{x: 13, y: 4}"))
        asm_text = self._compile_with(
            str(ROOT / "tools" / "rynorlang" / "compile.py"),
            '            self.out("    mov [rsp + 0], rax")',
            '            self.out("    mov [rsp + 8], rax")',
            src, "sret.rl")
        code, _out = self._run_asm(asm_text)
        self.assertNotEqual((code, _out), (0, "{x: 13, y: 4}"))

    def test_25_hash_prime_changed_breaks_map_find(self):
        # Three keys whose slot order provably differs between the frozen
        # prime and prime+6 (computed offline): real prints in real-prime
        # slot order, the mutant cannot reproduce it.
        src = ("fn main(): int {\n"
               "  let m: map<str,int,8> = {\"a\": 1, \"b\": 2, \"c\": 3};\n"
               "  print(m);\n"
               "  return 0;\n"
               "}\n")
        result = analyzer.analyze(src, "h.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "h.rl")
        self.assertIsNone(error)
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertEqual((outcome["exit"], "".join(emitted)), (0, "{c: 3, a: 1, b: 2}"))
        asm_text = self._compile_with(
            str(ROOT / "tools" / "rynorlang" / "compile.py"),
            '        self.out("    mov r10, 1099511628211")',
            '        self.out("    mov r10, 1099511628217")',
            src, "h.rl")
        code, _out = self._run_asm(asm_text)
        self.assertNotEqual((code, _out), (0, "{c: 3, a: 1, b: 2}"))


if __name__ == "__main__":
    unittest.main()
