"""Stage 15a RIR tests: build goldens, verifier units, determinism, negatives, mutations."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RIR_PATH = ROOT / "tools" / "rynorlang" / "rir.py"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "compiler" / "good"
TRAP = ROOT / "tests" / "fixtures" / "rynorlang" / "compiler" / "trap"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rir = _load("rynorlang_stage15_rir", RIR_PATH)
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as _analyzer

EXPECTED_GOOD = {
    "arith.rl", "boolexit.rl", "boolops.rl", "fib10.rl", "loopret.rl", "main42.rl",
    "negdiv.rl", "negmod.rl", "nestedloop.rl", "sevenargs.rl",
    "stackmix.rl", "strarg.rl", "strboundary.rl", "strcmpeq.rl",
    "strreturn.rl", "unitmain.rl", "wrapballot.rl",
}
EXPECTED_TRAP = {"divzero.rl", "falloff.rl", "intmin.rl"}

ADD_RIR = """; rir_version=1 source="add.rl" funcs=1
.function add : (a: int, b: int) -> int ; symbol=0 frameslots=3
.block bb0
  %2 = copy int %0
  %3 = copy int %1
  %4 = binop int + %2 %3
  ret %4
"""


def _build_source(text, name="<test>"):
    result = _analyzer.analyze(text, name)
    assert result.ok, result.diagnostic
    module, error = rir.build_rir(result.ast, name)
    assert error is None, error
    return module


def _good_module(name):
    return _build_source((GOOD / name).read_text(encoding="utf-8"), name)


class RirLayoutTests(unittest.TestCase):
    def test_01_module_exists(self):
        self.assertTrue(RIR_PATH.is_file())
        self.assertGreater(RIR_PATH.stat().st_size, 0)

    def test_02_public_api_exists(self):
        for name in ("build_rir", "verify_module", "dumps", "RIR_VERSION",
                     "COMP_V2_UNSUPPORTED", "COMP_STR_TOO_LONG", "COMP_BAD_AST",
                     "COMP_FRAME_TOO_BIG", "COMP_RIR_VERSION", "MAX_FRAMESLOTS",
                     "MAX_STR_LEN"):
            self.assertTrue(hasattr(rir, name), name)

    def test_03_no_hidden_test_hedging(self):
        source = RIR_PATH.read_text(encoding="utf-8")
        for forbidden in ("hidden-test", "KindStr", "FlexInt", "eval(", "exec("):
            self.assertNotIn(forbidden, source)

    def test_04_fixture_inventory_is_exact(self):
        self.assertEqual({p.name for p in GOOD.iterdir() if p.is_file()}, EXPECTED_GOOD)
        self.assertEqual({p.name for p in TRAP.iterdir() if p.is_file()}, EXPECTED_TRAP)


class RirBuildGoldenTests(unittest.TestCase):
    def test_10_add_golden(self):
        src = "fn add(a: int, b: int): int { return a + b; }"
        module = _build_source(src, "add.rl")
        self.assertEqual(rir.verify_module(module), [])
        self.assertEqual(rir.dumps(module), ADD_RIR)

    def test_11_main42_shape(self):
        src = "fn main(): int { return 42; }"
        module = _build_source(src, "main42.rl")
        self.assertEqual(rir.verify_module(module), [])
        text = rir.dumps(module)
        self.assertIn(".function main : () -> int ; symbol=0 frameslots=1", text)
        self.assertIn("%0 = const int 42", text)
        self.assertTrue(text.endswith("  ret %0\n"))

    def test_12_control_flow_shapes(self):
        src = ("fn f(n: int): int { if n > 0 { return 1; } "
               "let t: int = 0; while n > t { let u: int = 1; } return t; }")
        module = _build_source(src, "ctrl.rl")
        self.assertEqual(rir.verify_module(module), [])
        by_id = {}
        func = module["funcs"][0]
        for block in func["blocks"]:
            by_id[block["id"]] = block
        self.assertEqual([b["id"] for b in func["blocks"]],
                         [f"bb{i}" for i in range(len(func["blocks"]))])
        terms = [b["term"]["op"] for b in func["blocks"]]
        self.assertIn("br", terms)
        self.assertIn("jmp", terms)
        self.assertIn("ret", terms)
        # Every br references existing blocks; every block has one terminator.
        ids = set(by_id)
        for block in func["blocks"]:
            term = block["term"]
            for key in ("tgt", "then", "else"):
                if key in term:
                    self.assertIn(term[key], ids)

    def test_13_strtab_dedup_and_order(self):
        src = 'fn s(): str { return "b"; } fn t(): str { return "a"; } fn u(): str { return "b"; }'
        module = _build_source(src, "strs.rl")
        self.assertEqual(rir.verify_module(module), [])
        self.assertEqual([(e["id"], e["bytes"]) for e in module["strtab"]],
                         [(0, "b"), (1, "a")])
        text = rir.dumps(module)
        self.assertIn('.strtab #0 len=1 bytes="b"', text)
        self.assertIn('.strtab #1 len=1 bytes="a"', text)

    def test_14_all_good_fixtures_build_and_verify(self):
        for name in sorted(EXPECTED_GOOD | EXPECTED_TRAP):
            directory = GOOD if name in EXPECTED_GOOD else TRAP
            with self.subTest(fixture=name):
                module = _build_source(
                    (directory / name).read_text(encoding="utf-8"), name)
                self.assertEqual(rir.verify_module(module), [])

    def test_15_nested_control_tail_reaches_outer_continuation(self):
        cases = (
            ("fn main(): int { if true { if false { return 1; } } return 2; }",
             6, {"op": "jmp", "tgt": "bb3"}),
            ("fn main(): int { { if false { } } return 2; }",
             3, {"op": "ret", "v": "%1"}),
        )
        for src, block_index, expected in cases:
            with self.subTest(block=block_index):
                module = _build_source(src, "nested-tail.rl")
                self.assertEqual(rir.verify_module(module), [])
                block = module["funcs"][0]["blocks"][block_index]
                self.assertEqual(block["term"], expected)


class RirDeterminismTests(unittest.TestCase):
    def test_20_three_builds_byte_identical(self):
        src = (GOOD / "fib10.rl").read_text(encoding="utf-8")
        texts = []
        for _ in range(3):
            result = _analyzer.analyze(src, "fib10.rl")
            self.assertTrue(result.ok)
            module, error = rir.build_rir(result.ast, "fib10.rl")
            self.assertIsNone(error)
            texts.append(rir.dumps(module))
        self.assertEqual(texts[0], texts[1])
        self.assertEqual(texts[1], texts[2])

    def test_21_reanalyze_rebuild_stable(self):
        src = (GOOD / "sevenargs.rl").read_text(encoding="utf-8")
        first = rir.dumps(_build_source(src, "sevenargs.rl"))
        second = rir.dumps(_build_source(src, "sevenargs.rl"))
        self.assertEqual(first, second)


class RirNegativeTests(unittest.TestCase):
    def test_30_semantic_failures_produce_no_rir(self):
        for src in (
            "fn main(): int { return true; }",
            "fn main(): int { return nope(); }",
            "fn main(): int { let x: int = 1; let x: int = 2; return x; }",
            "fn main() { return 1; }",
            "@",
        ):
            with self.subTest(src=src[:30]):
                result = _analyzer.analyze(src, "bad.rl")
                self.assertFalse(result.ok)

    def test_31_unknown_ast_kind_rejected(self):
        module, error = rir.build_rir({"kind": "Program", "functions": [
            {"kind": "Pipeline", "span": {}, "name": "x"}]}, "v2.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_V2_UNSUPPORTED")

    def test_32_reserved_type_rejected(self):
        module, error = rir.build_rir({"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "value",
             "body": {"kind": "Block", "stmts": []}, "symbol": 0}]}, "v2.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_V2_UNSUPPORTED")

    def test_33_oversize_string_rejected(self):
        big = "q" * (rir.MAX_STR_LEN + 1)
        module, error = rir.build_rir({"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "str",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Return", "value": {"kind": "StrLit", "value": big,
                                              "lexeme": '"' + big + '"', "type": "str"}}]},
             "symbol": 0}]}, "big.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_STR_TOO_LONG")

    def test_34_malformed_ast_rejected_without_crash(self):
        bad_trees = [
            None, [], "Program", {"kind": "Nope", "functions": []},
            {"kind": "Program"}, {"kind": "Program", "functions": {}},
            {"kind": "Program", "functions": [{"kind": "Function"}]},
            {"kind": "Program", "functions": [
                {"kind": "Function", "name": "f", "params": "nope",
                 "ret_type": "int",
                 "body": {"kind": "Block", "stmts": []}, "symbol": 0}]},
        ]
        for index, tree in enumerate(bad_trees):
            with self.subTest(case=index):
                module, error = rir.build_rir(tree, "bad.rl")
                self.assertIsNone(module)
                self.assertIn(error["code"], ("COMP_BAD_AST", "COMP_V2_UNSUPPORTED"))
        cyclic = {"kind": "UnOp", "op": "-", "type": "int"}
        cyclic["operand"] = cyclic
        tree = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "main", "params": [],
             "ret_type": "int", "symbol": 0,
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Return", "value": cyclic}]}}]}
        module, error = rir.build_rir(tree, "cycle.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_BAD_AST")

    def test_35_deep_flat_chain_builds(self):
        src = "fn main(): int { return " + "1 + " * 5000 + "1; }"
        result = _analyzer.analyze(src, "deep.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "deep.rl")
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])
        params = [{"kind": "Param", "name": f"p{i}", "type": "int",
                   "symbol": i} for i in range(rir.MAX_FRAMESLOTS + 1)]
        wide = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "wide", "params": params,
             "ret_type": None, "symbol": 0,
             "body": {"kind": "Block", "stmts": []}}]}
        module, error = rir.build_rir(wide, "wide.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_FRAME_TOO_BIG")

    def test_36_duplicate_symbol_rejected(self):
        tree = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "int",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Let", "name": "x", "type": "int",
                  "init": {"kind": "IntLit", "value": "1", "type": "int"}, "symbol": 0},
                 {"kind": "Let", "name": "x", "type": "int",
                  "init": {"kind": "IntLit", "value": "2", "type": "int"}, "symbol": 0},
                 {"kind": "Return",
                  "value": {"kind": "Var", "name": "x", "symbol": 0, "type": "int"}}]},
             "symbol": 0}]}
        module, error = rir.build_rir(tree, "dup.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_BAD_AST")

    def test_37_bool_operand_rejected(self):
        tree = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "int",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Return", "value": {"kind": "BinOp", "op": "+",
                                              "left": {"kind": "BoolLit", "value": True, "type": "bool"},
                                              "right": {"kind": "BoolLit", "value": False, "type": "bool"},
                                              "type": "int"}}]},
             "symbol": 0}]}
        module, error = rir.build_rir(tree, "badop.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_BAD_AST")

    def test_38_falloff_marker_present(self):
        # A function whose body can fall off the end must carry exactly one
        # `unreachable` terminator (the fall-off trap site), not silence.
        src = "fn f(x: int): int { if x > 0 { return 1; } }"
        result = _analyzer.analyze(src, "fall.rl")
        self.assertTrue(result.ok)
        module, error = rir.build_rir(result.ast, "fall.rl")
        self.assertIsNone(error, error)
        terms = [b["term"]["op"] for b in module["funcs"][0]["blocks"]]
        self.assertEqual(terms.count("unreachable"), 1)
        self.assertIn("ret", terms)

    def test_38b_out_of_scope_use_rejected(self):
        # A use outside its block must fail closed even though the symbol
        # table saw the declaration: scopes pop with their blocks.
        tree = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "f",
             "params": [{"kind": "Param", "name": "c", "type": "bool", "symbol": 5}],
             "ret_type": "int",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "If",
                  "cond": {"kind": "Var", "name": "c", "symbol": 5, "type": "bool"},
                  "then": {"kind": "Block", "stmts": [
                      {"kind": "Let", "name": "x", "type": "int",
                       "init": {"kind": "IntLit", "value": "1", "type": "int"},
                       "symbol": 0}]},
                  "else": None},
                 {"kind": "Return",
                  "value": {"kind": "Var", "name": "x", "symbol": 0, "type": "int"}}]},
             "symbol": 0}]}
        module, error = rir.build_rir(tree, "scope.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_BAD_AST")

    def test_38c_aliased_dag_lowers_without_crash(self):
        # The same dict object twice (only possible in hand-built input)
        # lowers each occurrence independently; the builder must never raise.
        shared = {"kind": "IntLit", "value": "1", "type": "int"}
        tree = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "int",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Return", "value": {"kind": "BinOp", "op": "+",
                                              "left": shared, "right": shared,
                                              "type": "int"}}]},
             "symbol": 0}]}
        module, error = rir.build_rir(tree, "dag.rl")
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])
        consts = [i for b in module["funcs"][0]["blocks"]
                  for i in b["instrs"] if i["op"] == "const"]
        self.assertEqual(len(consts), 2)

    def test_38d_builder_revalidates_calls(self):
        src = ("fn f(x: int): int { return x; } "
               "fn main(): int { return f(1); }")
        result = _analyzer.analyze(src, "calls.rl")
        self.assertTrue(result.ok, result.diagnostic)
        for callee, args in (("ghost", result.ast["functions"][1]["body"]["stmts"][0]
                              ["value"]["args"]), ("f", [])):
            with self.subTest(callee=callee, arity=len(args)):
                import copy
                tree = copy.deepcopy(result.ast)
                call = tree["functions"][1]["body"]["stmts"][0]["value"]
                call["callee"] = callee
                call["args"] = args
                module, error = rir.build_rir(tree, "calls-mutated.rl")
                self.assertIsNone(module)
                self.assertEqual(error["code"], "COMP_BAD_AST")

    def test_39_const_values_faithful(self):
        # Every literal value must survive lowering exactly (no hardcoded or
        # truncated constants); parametrized over magnitudes and zero.
        for text in ("0", "1", "7", "42", "999", "9223372036854775807"):
            with self.subTest(literal=text):
                src = f"fn main(): int {{ return {text}; }}"
                result = _analyzer.analyze(src, "lit.rl")
                self.assertTrue(result.ok)
                module, error = rir.build_rir(result.ast, "lit.rl")
                self.assertIsNone(error, error)
                consts = [i for b in module["funcs"][0]["blocks"]
                          for i in b["instrs"] if i["op"] == "const"]
                self.assertEqual([(c["type"], c["value"]) for c in consts],
                                 [("int", str(int(text, 10)))])


class RirVerifierTests(unittest.TestCase):
    def _valid(self):
        return _good_module("main42.rl")

    def test_40_valid_module_passes(self):
        module = self._valid()
        # main42.rl holds one function; rebuild single-function golden shape.
        src = "fn main(): int { return 42; }"
        module = _build_source(src, "main42.rl")
        self.assertEqual(rir.verify_module(module), [])

    def test_41_envelope_rejected(self):
        self.assertTrue(rir.verify_module(None))
        self.assertTrue(rir.verify_module([]))
        bad = {"rir_version": 999, "source": "x", "strtab": [], "funcs": []}
        self.assertTrue(any("rir_version" in e for e in rir.verify_module(bad)))
        bad = {"rir_version": 1, "source": "x", "strtab": [], "funcs": {}}
        self.assertTrue(any("funcs must be a list" in e for e in rir.verify_module(bad)))

    def test_42_strtab_rules(self):
        module = self._valid()
        module["strtab"] = [{"id": 1, "len": 2, "bytes": "hi"}]
        self.assertTrue(any("id must be 0" in e for e in rir.verify_module(module)))
        module["strtab"] = [{"id": 0, "len": 9, "bytes": "hi"}]
        self.assertTrue(any("len must equal" in e for e in rir.verify_module(module)))
        module["strtab"] = [{"id": 0, "len": 2, "bytes": "hi"},
                            {"id": 1, "len": 2, "bytes": "hi"}]
        self.assertTrue(any("duplicates" in e for e in rir.verify_module(module)))

    def test_43_terminator_rules(self):
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["term"] = {"op": "jmp", "tgt": "bb99"}
        self.assertTrue(any("unknown block" in e for e in rir.verify_module(module)))
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["term"] = {"op": "ret", "v": "%0", "extra": 1}
        self.assertTrue(any("only carry" in e for e in rir.verify_module(module)))
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"].append({"op": "ret"})
        self.assertTrue(any("must not appear as instruction" in e
                            for e in rir.verify_module(module)))
        module = self._valid()
        module["funcs"][0]["blocks"][0]["term"] = None
        self.assertTrue(any("exactly one terminator" in e
                            for e in rir.verify_module(module)))

    def test_44_def_use_rules(self):
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"][0]["dst"] = "%9"
        block["term"] = {"op": "ret", "v": "%0"}
        errors = rir.verify_module(module)
        self.assertTrue(any("undefined vreg" in e for e in errors))
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"].append({"op": "const", "dst": "%0", "type": "int", "value": "1"})
        self.assertTrue(any("redefines" in e for e in rir.verify_module(module)))

    def test_45_type_rules(self):
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"] = [{"op": "const", "dst": "%0", "type": "bool", "value": True}]
        block["term"] = {"op": "ret", "v": "%0"}
        module["funcs"][0]["ret"] = "int"
        self.assertTrue(any("returns 'bool'" in e for e in rir.verify_module(module)))
        module = self._valid()
        module["funcs"][0]["blocks"][0]["instrs"] = [
            {"op": "const", "dst": "%0", "type": "int", "value": "1"},
            {"op": "const", "dst": "%1", "type": "bool", "value": True},
            {"op": "binop", "operator": "+", "dst": "%2", "type": "int", "l": "%0", "r": "%1"},
        ]
        module["funcs"][0]["blocks"][0]["term"] = {"op": "ret", "v": "%2"}
        self.assertTrue(any("mistyped" in e for e in rir.verify_module(module)))

    def test_46_call_rules(self):
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"] = [{"op": "call", "name": "ghost", "args": []}]
        self.assertTrue(any("unknown function" in e for e in rir.verify_module(module)))
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"] = [{"op": "call", "dst": "%0", "name": "main", "args": []}]
        self.assertTrue(any("dst/type must both be present or absent" in e
                            for e in rir.verify_module(module)))

    def test_46b_call_arity_checked(self):
        module = {
            "rir_version": 1, "source": "x", "strtab": [],
            "funcs": [
                {"name": "t", "symbol": 0,
                 "params": [{"name": "a", "symbol": 0, "type": "int"}],
                 "ret": "int",
                 "blocks": [{"id": "bb0", "instrs": [],
                             "term": {"op": "ret", "v": "%0"}}],
                 "frameslots": 1},
                {"name": "main", "symbol": 1, "params": [], "ret": "int",
                 "blocks": [{"id": "bb0",
                             "instrs": [{"op": "call", "dst": "%0", "type": "int",
                                         "name": "t", "args": []}],
                             "term": {"op": "ret", "v": "%0"}}],
                 "frameslots": 1},
            ],
        }
        self.assertTrue(any("arity" in e for e in rir.verify_module(module)))

    def test_47_reserved_rejected(self):
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"] = [{"op": "make_list", "dst": "%0", "type": "int"}]
        block["term"] = {"op": "ret", "v": "%0"}
        self.assertTrue(any("reserved opcode" in e for e in rir.verify_module(module)))
        module = self._valid()
        block = module["funcs"][0]["blocks"][0]
        block["instrs"][0]["type"] = "value"
        self.assertTrue(any("invalid type" in e for e in rir.verify_module(module)))

    def test_48_frameslots_exact(self):
        module = self._valid()
        module["funcs"][0]["frameslots"] = 99
        self.assertTrue(any("frameslots" in e for e in rir.verify_module(module)))

    def test_49_no_live_overlap_in_homes(self):
        # Every pair of vregs sharing any home-slot half must have
        # non-interleaving lifetimes; otherwise one clobbers a live value.
        # Covers the full fixture corpus plus deep/loop/str stress shapes.
        probes = []
        for name in sorted(EXPECTED_GOOD | EXPECTED_TRAP):
            directory = GOOD if name in EXPECTED_GOOD else TRAP
            probes.append((name, (directory / name).read_text(encoding="utf-8")))
        probes += [
            ("deep5000", "fn main(): int { return " + "1 + " * 5000 + "1; }"),
            ("loopmix", "fn f(n: int): int { let t: int = 0; while n > 0 "
                        "{ if t == 0 { return n; } } return t; }"),
            ("strmix", 'fn id(s: str): str { return s; } fn main(): int '
                       '{ if id("ab") == id("cd") { return 1; } return 0; }'),
        ]
        for label, src in probes:
            with self.subTest(case=label):
                result = _analyzer.analyze(src, label)
                self.assertTrue(result.ok, result.diagnostic)
                module, error = rir.build_rir(result.ast, label)
                self.assertIsNone(error, error)
                self.assertEqual(rir.verify_module(module), [])
                for func in module["funcs"]:
                    vtypes = {f"%{i}": p["type"] for i, p in enumerate(func["params"])}
                    for block in func["blocks"]:
                        for instr in block["instrs"]:
                            if "dst" in instr:
                                vtypes[instr["dst"]] = instr["type"]
                    slot_of, _ = rir.assign_slots(func["blocks"], vtypes,
                                                  len(func["params"]))
                    events = {}
                    for bi, block in enumerate(func["blocks"]):
                        for pos, instr in enumerate(block["instrs"]):
                            if "dst" in instr:
                                events.setdefault(instr["dst"], [None, []])[0] = (bi, pos)
                            operands = [instr[k] for k in ("src", "l", "r", "v")
                                        if k in instr]
                            if instr.get("op") == "call":
                                operands.extend(instr.get("args", []))
                            for vreg in operands:
                                events.setdefault(vreg, [None, []])[1].append((bi, pos))
                        term = block["term"]
                        for key in ("cond", "v"):
                            if key in term:
                                events.setdefault(term[key], [None, []])[1].append(
                                    (bi, len(block["instrs"])))
                    def halves(vreg):
                        start = slot_of.get(vreg)
                        if start is None:
                            return set()
                        width = 2 if vtypes.get(vreg) == "str" else 1
                        return set(range(start, start + width))
                    by_slot = {}
                    for vreg, (definition, uses) in events.items():
                        points = ([definition] if definition else []) + uses
                        points = [point for point in points if point is not None]
                        if not points:
                            continue
                        interval = (min(points), max(points), vreg)
                        for slot in halves(vreg):
                            by_slot.setdefault(slot, []).append(interval)
                    for slot, intervals in by_slot.items():
                        intervals.sort()
                        for left, right in zip(intervals, intervals[1:]):
                            self.assertLess(
                                left[1], right[0],
                                f"{func['name']}: slot {slot} live overlap "
                                f"{left[2]} vs {right[2]}")


    def test_50_branch_def_must_dominate_use(self):
        # A definition on one branch must not authorize a use after the
        # join, even though it precedes the use in layout order (the exact
        # gap a layout-order check misses and real dominance closes).
        def diamond(def_block):
            blocks = [
                {"id": "bb0",
                 "instrs": [{"op": "const", "dst": "%0", "type": "bool",
                             "value": True}],
                 "term": {"op": "br", "cond": "%0", "then": "bb1",
                          "else": "bb2"}},
                {"id": "bb1", "instrs": [], "term": {"op": "jmp", "tgt": "bb3"}},
                {"id": "bb2", "instrs": [], "term": {"op": "jmp", "tgt": "bb3"}},
                {"id": "bb3", "instrs": [], "term": {"op": "ret", "v": "%1"}},
            ]
            blocks[def_block]["instrs"] = blocks[def_block]["instrs"] + [
                {"op": "const", "dst": "%1", "type": "int", "value": "7"}]
            vtypes = {"%0": "bool", "%1": "int"}
            _, slots = rir.assign_slots(blocks, vtypes, 0)
            return {"rir_version": 1, "source": "x", "strtab": [],
                    "funcs": [{"name": "main", "symbol": 0, "params": [],
                               "ret": "int", "blocks": blocks,
                               "frameslots": slots}]}
        bad = diamond(1)  # defined in bb1 (then-branch), used in bb3 (join)
        self.assertTrue(any("not dominated" in e
                            for e in rir.verify_module(bad)), rir.verify_module(bad))
        good = diamond(0)  # defined in bb0 (entry): dominates the join
        self.assertEqual(rir.verify_module(good), [])

    def test_50b_cfg_liveness_prevents_backedge_clobber(self):
        blocks = [
            {"id": "bb0", "instrs": [
                {"op": "const", "dst": "%0", "type": "int", "value": "7"},
                {"op": "const", "dst": "%1", "type": "bool", "value": False}],
             "term": {"op": "br", "cond": "%1", "then": "bb1", "else": "bb2"}},
            {"id": "bb1", "instrs": [], "term": {"op": "ret", "v": "%0"}},
            {"id": "bb2", "instrs": [
                {"op": "const", "dst": "%2", "type": "int", "value": "99"}],
             "term": {"op": "jmp", "tgt": "bb1"}},
        ]
        slots, count = rir.assign_slots(
            blocks, {"%0": "int", "%1": "bool", "%2": "int"}, 0)
        self.assertNotEqual(slots["%0"], slots["%2"])
        module = {"rir_version": 1, "source": "cfg-live.rir", "strtab": [],
                  "funcs": [{"name": "main", "symbol": 0, "params": [],
                             "ret": "int", "blocks": blocks,
                             "frameslots": count}]}
        self.assertEqual(rir.verify_module(module), [])

    def test_50c_malformed_rir_is_diagnosed_without_exception(self):
        import copy
        cases = []
        module = self._valid()
        module["funcs"].append(copy.deepcopy(module["funcs"][0]))
        cases.append(module)
        module = self._valid()
        module["funcs"][0]["blocks"][0]["id"] = []
        cases.append(module)
        module = self._valid()
        module["funcs"][0]["blocks"][0]["term"] = {"op": "jmp", "tgt": []}
        cases.append(module)
        for value in ("1_0", " 1"):
            module = self._valid()
            module["funcs"][0]["blocks"][0]["instrs"][0]["value"] = value
            cases.append(module)
        module = self._valid()
        module["funcs"][0]["name"] = "bad:name"
        cases.append(module)
        module = self._valid()
        module["extra"] = True
        cases.append(module)
        module = self._valid()
        module["funcs"][0]["frameslots"] = True
        cases.append(module)
        for index, module in enumerate(cases):
            with self.subTest(case=index):
                self.assertTrue(rir.verify_module(module))

    def test_50d_same_block_forward_use_reports_before_definition(self):
        # Two-pass def/use: a use preceding its definition in the same block
        # must report "before its definition", not "undefined". This locks
        # the pre-scan so the same-block order check stays load-bearing
        # (a mutant deleting it must be detected).
        blocks = [{"id": "bb0",
                   "instrs": [{"op": "copy", "dst": "%0", "type": "int",
                               "src": "%1"},
                              {"op": "const", "dst": "%1", "type": "int",
                               "value": "2"}],
                   "term": {"op": "ret", "v": "%0"}}]
        slot_of, count = rir.assign_slots(
            blocks, {"%0": "int", "%1": "int"}, 0)
        void = slot_of  # homes exist; verifier checks dominance, not layout
        module = {"rir_version": 1, "source": "x", "strtab": [],
                  "funcs": [{"name": "main", "symbol": 0, "params": [],
                             "ret": "int", "blocks": blocks,
                             "frameslots": count}]}
        errors = rir.verify_module(module)
        self.assertTrue(any("before its definition" in e for e in errors),
                        errors)



class RirMutationTests(unittest.TestCase):
    def _mutant(self, old, new):
        import tempfile
        text = RIR_PATH.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, f"anchor must be unique: {old!r}")
        directory = tempfile.TemporaryDirectory(prefix="rir-mutant-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "rir.py"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return _load(f"rir_mutant_{len(sys.modules)}", path)

    def test_50_builder_operand_check_removal_detected(self):
        # Baseline rejects `1 + true` at build time; the mutant (operand
        # check removed) accepts it. Proves the builder enforces operand
        # types itself instead of inheriting the analyzer's word for it.
        bad = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "int",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Return", "value": {"kind": "BinOp", "op": "+",
                                              "left": {"kind": "IntLit", "value": "1", "type": "int"},
                                              "right": {"kind": "BoolLit", "value": True, "type": "bool"},
                                              "type": "int"}}]},
             "symbol": 0}]}
        module0, error0 = rir.build_rir(bad, "base.rl")
        self.assertIsNone(module0)
        self.assertTrue(error0["code"].startswith("COMP_"))
        mutant = self._mutant(
            '    elif ltype != req_l or rtype != req_r:\n        _fail(COMP_BAD_AST, f"operator \'{op}\' needs {req_l}/{req_r}, got {ltype}/{rtype}")',
            '    elif False and (ltype != req_l or rtype != req_r):\n        _fail(COMP_BAD_AST, f"operator \'{op}\' needs {req_l}/{req_r}, got {ltype}/{rtype}")')
        module, error = mutant.build_rir(bad, "mut.rl")
        self.assertIsNotNone(module, f"mutant must accept the mistyped tree: {error}")
        self.assertTrue(mutant.verify_module(module),
                        "verifier independently rejects the mistyped binop")

    def test_51_verifier_terminator_removal_detected(self):
        # Removing the jump-target check must let a dangling branch through.
        mutant = self._mutant(
            '        if not isinstance(term.get("tgt"), str) or term.get("tgt") not in idset:',
            '        if False:  # mutant: accept any jump target')
        module = _good_module("main42.rl")
        block = module["funcs"][0]["blocks"][0]
        block["term"] = {"op": "jmp", "tgt": "bb99"}
        self.assertTrue(rir.verify_module(module),
                        "real verifier must reject the dangling jump")
        self.assertEqual(mutant.verify_module(module), [],
                         "mutant must accept the dangling jump")

    def test_52_strtab_dedup_removal_detected(self):
        # Without dedup, the second "b" interns a second entry: the golden
        # strtab-order assertion fails, proving dedup is load-bearing.
        mutant = self._mutant(
            "    if value not in str_ids:",
            "    if True:  # mutant: never dedup")
        src = 'fn s(): str { return "b"; } fn t(): str { return "a"; } fn u(): str { return "b"; }'
        result = _analyzer.analyze(src, "order.rl")
        self.assertTrue(result.ok)
        module, error = mutant.build_rir(result.ast, "order.rl")
        self.assertIsNone(error, error)
        self.assertNotEqual([(e["id"], e["bytes"]) for e in module["strtab"]],
                            [(0, "b"), (1, "a")])

    def test_53_frameslots_check_removal_detected(self):
        mutant = self._mutant(
            '    if type(func.get("frameslots")) is not int or func.get("frameslots") != want_slots:',
            '    if False:  # mutant: trust declared frameslots')
        module = _good_module("main42.rl")
        module["funcs"][0]["frameslots"] = 99
        self.assertEqual(mutant.verify_module(module), [],
                         "mutant must accept the wrong frameslots")
        self.assertTrue(rir.verify_module(module),
                        "real verifier must reject the wrong frameslots")

    def test_54_unknown_symbol_rejected_without_crash(self):
        # A dangling symbol reference must fail closed with COMP_BAD_AST.
        # Removing the guard either crashes (red suite) or misbinds (caught
        # below): both outcomes count as detection, never silent acceptance.
        bad = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "int",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Return",
                  "value": {"kind": "Var", "name": "ghost", "symbol": 99, "type": "int"}}]},
             "symbol": 0}]}
        module, error = rir.build_rir(bad, "ghost.rl")
        self.assertIsNone(module)
        self.assertEqual(error["code"], "COMP_BAD_AST")
        mutant = self._mutant(
            '        if not isinstance(sym, int) or sym not in low.symbols:\n'
            '            _fail(COMP_BAD_AST, "Var references an unknown symbol")',
            '        pass  # mutant: bind dangling symbols to nothing')
        try:
            module, error = mutant.build_rir(bad, "mut.rl")
        except KeyError:
            return  # crash = detected (suite goes red, never green-on-broken)
        self.assertIsNotNone(module, "mutant must not cleanly reject like the original")

    def test_55_result_type_faithfulness_detected(self):
        # The builder must preserve the node's declared result type instead
        # of silently substituting the rule result: baseline rejects the
        # lying tree, the mutant accepts it. (The mutant's output is still
        # rule-consistent, so the verifier rightly passes it -- the check
        # guards faithfulness to the analyzed AST, not downstream soundness.)
        bad = {"kind": "Program", "functions": [
            {"kind": "Function", "name": "f", "params": [], "ret_type": "bool",
             "body": {"kind": "Block", "stmts": [
                 {"kind": "Return", "value": {"kind": "BinOp", "op": "==",
                                              "left": {"kind": "IntLit", "value": "1", "type": "int"},
                                              "right": {"kind": "IntLit", "value": "1", "type": "int"},
                                              "type": "int"}}]},
             "symbol": 0}]}
        module0, error0 = rir.build_rir(bad, "base.rl")
        self.assertIsNone(module0)
        self.assertTrue(error0["code"].startswith("COMP_"))
        mutant = self._mutant(
            '    if want != result:\n'
            '        _fail(COMP_BAD_AST, f"operator \'{op}\' result must be {result}, node says {want!r}")',
            '    pass  # mutant: trust any declared result type')
        module, error = mutant.build_rir(bad, "mut.rl")
        self.assertIsNotNone(module, f"mutant must accept the lying tree: {error}")

    def test_56_nested_tail_regression_detects_original_bug(self):
        mutant = self._mutant(
            "    if low.is_open(then_tail):\n"
            "        low.terminate(then_tail, {\"op\": \"jmp\", \"tgt\": f\"bb{join_b}\"})",
            "    if low.is_open(then_b):\n"
            "        low.terminate(then_b, {\"op\": \"jmp\", \"tgt\": f\"bb{join_b}\"})")
        src = ("fn main(): int { if true { if false { return 1; } } "
               "return 2; }")
        result = _analyzer.analyze(src, "nested-tail-mutant.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = mutant.build_rir(result.ast, "nested-tail-mutant.rl")
        self.assertIsNone(error, error)
        self.assertEqual(module["funcs"][0]["blocks"][6]["term"],
                         {"op": "unreachable"})
        block_mutant = self._mutant(
            '    if kind == "Block":\n'
            '        return _lower_block_contents(low, stmt.get("stmts"), cur, fname, ret)',
            '    if kind == "Block":\n'
            '        _lower_block_contents(low, stmt.get("stmts"), cur, fname, ret)\n'
            '        return cur')
        wrapped = "fn main(): int { { if false { } } return 2; }"
        result = _analyzer.analyze(wrapped, "block-tail-mutant.rl")
        self.assertTrue(result.ok, result.diagnostic)
        module, error = block_mutant.build_rir(result.ast, "block-tail-mutant.rl")
        self.assertIsNone(error, error)
        self.assertEqual(module["funcs"][0]["blocks"][3]["term"],
                         {"op": "unreachable"})

    def test_57_cfg_edge_omission_mutation_detected(self):
        mutant = self._mutant(
            "                    succ[bi].add(index_of[target])",
            "                    pass  # mutant: ignore CFG edge")
        blocks = [
            {"id": "bb0", "instrs": [
                {"op": "const", "dst": "%0", "type": "int", "value": "7"},
                {"op": "const", "dst": "%1", "type": "bool", "value": False}],
             "term": {"op": "br", "cond": "%1", "then": "bb1", "else": "bb2"}},
            {"id": "bb1", "instrs": [], "term": {"op": "ret", "v": "%0"}},
            {"id": "bb2", "instrs": [
                {"op": "const", "dst": "%2", "type": "int", "value": "99"}],
             "term": {"op": "jmp", "tgt": "bb1"}},
        ]
        real, _ = rir.assign_slots(blocks, {"%0": "int", "%1": "bool", "%2": "int"}, 0)
        broken, _ = mutant.assign_slots(
            blocks, {"%0": "int", "%1": "bool", "%2": "int"}, 0)
        self.assertNotEqual(real["%0"], real["%2"])
        self.assertEqual(broken["%0"], broken["%2"])


if __name__ == "__main__":
    unittest.main()
