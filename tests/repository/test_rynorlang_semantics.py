"""Stage 14 semantics tests: stable AST, scopes, types, diagnostics."""
from __future__ import annotations
import ast
import importlib.util
import json
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEXER_PATH = ROOT / "tools" / "rynorlang" / "lex.py"
PARSER_PATH = ROOT / "tools" / "rynorlang" / "parse.py"
ANALYZER_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "semantics" / "good"
BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "semantics" / "bad"

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod

lexer = _load("rynor_lang_lex", LEXER_PATH)
parser = _load("rynor_lang_parse", PARSER_PATH)
analyzer = _load("rynor_lang_analyze", ANALYZER_PATH)

# try alternative import
if analyzer is None:
    try:
        import tools.rynorlang.analyze as analyzer
        ANALYZER_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
    except Exception:
        pass

GOOD_NAMES = {p.name for p in GOOD.iterdir() if p.is_file()} if GOOD.exists() else set()
BAD_NAMES = {p.name for p in BAD.iterdir() if p.is_file()} if BAD.exists() else set()

EXPECTED_GOOD = 12
EXPECTED_BAD = 20

FROZEN_CODES = {"SEM_UNDECLARED","SEM_DUPLICATE","SEM_TYPE_MISMATCH","SEM_ARITY_MISMATCH","SEM_UNKNOWN_FUNCTION"}
EXPECTED_BAD_CODES = {
    "arity_0_mismatch.rl": "SEM_ARITY_MISMATCH",
    "arity_1_mismatch.rl": "SEM_ARITY_MISMATCH",
    "arity_2_mismatch.rl": "SEM_ARITY_MISMATCH",
    "duplicate_fn.rl": "SEM_DUPLICATE",
    "duplicate_shadow_outer.rl": "SEM_DUPLICATE",
    "type_binop_int_bool.rl": "SEM_TYPE_MISMATCH",
    "type_equality_mismatch.rl": "SEM_TYPE_MISMATCH",
    "type_if_cond.rl": "SEM_TYPE_MISMATCH",
    "type_let_mismatch.rl": "SEM_TYPE_MISMATCH",
    "type_logical_int.rl": "SEM_TYPE_MISMATCH",
    "type_relational_str.rl": "SEM_TYPE_MISMATCH",
    "type_return_bare_typed.rl": "SEM_TYPE_MISMATCH",
    "type_return_mismatch.rl": "SEM_TYPE_MISMATCH",
    "type_return_value_untyped.rl": "SEM_TYPE_MISMATCH",
    "type_unary_bang_int.rl": "SEM_TYPE_MISMATCH",
    "type_unary_minus_bool.rl": "SEM_TYPE_MISMATCH",
    "undeclared_out_of_scope.rl": "SEM_UNDECLARED",
    "undeclared_use_before_declare.rl": "SEM_UNDECLARED",
    "unit_as_value.rl": "SEM_TYPE_MISMATCH",
    "unknown_fn.rl": "SEM_UNKNOWN_FUNCTION",
}

def _do_analyze(src, filename="<input>"):
    if analyzer is None:
        raise AssertionError("analyzer failed to import")
    fn = getattr(analyzer, "analyze", None)
    if not callable(fn):
        raise AssertionError("analyze(source, filename) API is missing")
    return _norm(fn(src, filename))

def _norm(res):
    if res is None:
        return (False, None, None, None, None)
    # AnalyzeResult
    diag = getattr(res, "diagnostic", None)
    if diag is None:
        diag = getattr(res, "error", None)
    ast_tree = getattr(res, "ast", None)
    if ast_tree is None:
        ast_tree = getattr(res, "tree", None)
        if ast_tree is None:
            ast_tree = getattr(res, "program", None)
    ok = getattr(res, "ok", None)
    if ok is None:
        ok = diag is None
    code = getattr(diag, "code", None) if diag else None
    if code is None and isinstance(diag, dict):
        code = diag.get("code")
    span = getattr(diag, "span", None) if diag else None
    if span is None and isinstance(diag, dict):
        span = diag.get("span")
    return (bool(ok), ast_tree, diag, code, span)

def _assert_ok(src, msg=""):
    ok, tree, diag, code, span = _do_analyze(src)
    if not ok:
        raise AssertionError(f"expected ok but got {code}: {diag} {msg} src={src!r}")

def _assert_err(src, code=None):
    ok, tree, diag, got, span = _do_analyze(src)
    if ok:
        raise AssertionError(f"expected err but got ok tree={tree}")
    if got is None:
        raise AssertionError(f"missing code {diag}")
    if code and got != code:
        raise AssertionError(f"expected {code} got {got} diag={diag}")
    if got not in FROZEN_CODES and not got.startswith("PAR_") and not got.startswith("LEX_"):
        raise AssertionError(f"unfrozen diagnostic code {got}")
    return diag, got, span

def _extract_spans(node, out=None, seen=None):
    if out is None:
        out=[]
    if seen is None:
        seen=set()
    if node is None or id(node) in seen:
        return out
    seen.add(id(node))
    if isinstance(node, (str,bytes,int,float,bool)):
        return out
    if isinstance(node, (list,tuple)):
        for x in node:
            _extract_spans(x,out,seen)
        return out
    if isinstance(node, dict):
        if "span" in node:
            out.append(node["span"])
        for v in node.values():
            _extract_spans(v,out,seen)
        return out
    # object
    span=getattr(node,"span",None)
    if span is not None:
        out.append(span)
    if hasattr(node,"__dict__"):
        for v in vars(node).values():
            _extract_spans(v,out,seen)
    return out

class SemanticsLayoutTests(unittest.TestCase):
    def test_01_fixture_inventory(self):
        actual_good = {p.name for p in GOOD.iterdir() if p.is_file()} if GOOD.exists() else set()
        actual_bad = {p.name for p in BAD.iterdir() if p.is_file()} if BAD.exists() else set()
        self.assertEqual(len(actual_good), EXPECTED_GOOD, f"good count {actual_good}")
        self.assertEqual(len(actual_bad), EXPECTED_BAD, f"bad count {actual_bad}")
        self.assertEqual(actual_bad, set(EXPECTED_BAD_CODES))
        self.assertGreaterEqual(len(actual_good), 10)
        self.assertLessEqual(len(actual_good), 14)
        self.assertGreaterEqual(len(actual_bad), 16)
        self.assertLessEqual(len(actual_bad), 20)

    def test_02_analyzer_exists(self):
        if ANALYZER_PATH is None or not ANALYZER_PATH.exists():
            self.skipTest("analyzer not present")
        self.assertTrue(ANALYZER_PATH.is_file())
        self.assertGreater(ANALYZER_PATH.stat().st_size, 0)
        self.assertFalse((ROOT / "rynorlang" / "ast" / "analyze.py").exists(), "no duplicate under rynorlang/ast")

    def test_03_stdlib_only(self):
        if not ANALYZER_PATH.exists():
            self.skipTest("no analyzer")
        tree = ast.parse(ANALYZER_PATH.read_text(encoding="utf-8"))
        imports=set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imports.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                imports.add(n.module.split(".")[0])
        allowed={"__future__","argparse","json","sys","pathlib","typing","dataclasses","tools","re","collections"}
        for imp in imports:
            self.assertIn(imp, allowed|{"os","enum","sys","json","pathlib","typing","dataclasses","argparse","tools"}, f"unexpected import {imp}")

    def test_04_no_hedging(self):
        if not ANALYZER_PATH.exists():
            self.skipTest("no analyzer")
        src = ANALYZER_PATH.read_text(encoding="utf-8")
        for forbidden in ("hidden-test","KindStr","FlexInt"):
            self.assertNotIn(forbidden, src)

class SemanticsGoodBadTests(unittest.TestCase):
    def test_05_good_fixtures_ok(self):
        if analyzer is None:
            self.skipTest("no analyzer")
        for p in sorted(GOOD.iterdir()):
            with self.subTest(good=p.name):
                txt=p.read_text(encoding="utf-8")
                ok,tree,diag,code,span=_do_analyze(txt, str(p))
                self.assertTrue(ok, f"good {p.name} got {code} {diag}")
                self.assertIsNotNone(tree)
                self.assertIsNone(diag)

    def test_06_bad_fixtures_err(self):
        if analyzer is None:
            self.skipTest("no analyzer")
        for p in sorted(BAD.iterdir()):
            with self.subTest(bad=p.name):
                txt=p.read_text(encoding="utf-8")
                ok,tree,diag,code,span=_do_analyze(txt, str(p))
                self.assertFalse(ok, f"bad {p.name} should fail")
                self.assertIsNotNone(diag)
                self.assertEqual(code, EXPECTED_BAD_CODES[p.name])
                self.assertIsNotNone(span)
                self.assertIsNotNone(diag.expected, f"bad {p.name} missing expected detail")
                self.assertIsNotNone(diag.got, f"bad {p.name} missing got detail")

    def test_07_every_frozen_code_exercised(self):
        if analyzer is None:
            self.skipTest("no analyzer")
        observed=set()
        for p in BAD.iterdir():
            txt=p.read_text(encoding="utf-8")
            try:
                ok,tree,diag,code,span=_do_analyze(txt, str(p))
            except Exception as e:
                m=re.search(r"SEM_[A-Z_]+", str(e))
                if m:
                    observed.add(m.group(0))
                continue
            if not ok and code:
                observed.add(str(code))
        # also check via direct sources for each code if not enough
        self.assertEqual(FROZEN_CODES - observed, set(), f"missing codes {FROZEN_CODES-observed} observed {observed}")
        # also check analyzer source contains each code
        if ANALYZER_PATH.exists():
            txt=ANALYZER_PATH.read_text(encoding="utf-8")
            for c in FROZEN_CODES:
                self.assertIn(c, txt, f"code {c} not in analyzer source")

class SemanticsRuleTests(unittest.TestCase):
    def test_08_forward_ref_ok(self):
        _assert_ok("fn main(): int { return foo(); } fn foo(): int { return 1; }")

    def test_09_unit_as_expr_stmt_ok(self):
        _assert_ok("fn helper() { return; } fn main() { helper(); }")

    def test_10_unit_as_value_err(self):
        _assert_err("fn helper() { return; } fn main(): int { let x: int = helper(); return x; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn helper() { return; } fn main(): int { return helper(); }", "SEM_TYPE_MISMATCH")

    def test_11_str_equality_ok(self):
        _assert_ok("fn main(): bool { let a: str = \"hi\"; let b: str = \"hi\"; return a == b; }")
        _assert_ok("fn main(): bool { return \"a\" != \"b\"; }")

    def test_12_str_lt_err(self):
        _assert_err("fn main(): bool { return \"a\" < \"b\"; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn main(): bool { let a: str = \"hi\"; return a <= \"lo\"; }", "SEM_TYPE_MISMATCH")

    def test_13_bool_int_mixing(self):
        _assert_err("fn main(): int { return 1 + true; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn main(): bool { return 1 && true; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn main(): bool { let x: int = 1; return x && true; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn main(): int { return true + 1; }", "SEM_TYPE_MISMATCH")

    def test_14_unary_type(self):
        _assert_ok("fn main(): int { let x: int = -5; return x; }")
        _assert_ok("fn main(): bool { let x: bool = !true; return x; }")
        _assert_err("fn main(): int { return -true; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn main(): bool { return !1; }", "SEM_TYPE_MISMATCH")

    def test_15_duplicate_across_nested(self):
        _assert_err("fn main(): int { let x: int = 1; { let x: int = 2; return x; } }", "SEM_DUPLICATE")
        _assert_err("fn main(): int { let x: int = 1; if true { let x: int = 2; } return x; }", "SEM_DUPLICATE")

    def test_16_duplicate_param_and_fn(self):
        _assert_err("fn foo(a: int, a: bool): int { return 1; }", "SEM_DUPLICATE")
        _assert_err("fn foo(): int { return 1; } fn foo(): int { return 2; }", "SEM_DUPLICATE")
        _assert_err("fn foo(a: int): int { let a: int = 1; return a; }", "SEM_DUPLICATE")

    def test_17_use_before_declare(self):
        _assert_err("fn main(): int { let x: int = y; return x; }", "SEM_UNDECLARED")
        _assert_err("fn main(): int { let y: int = x; let x: int = 1; return y; }", "SEM_UNDECLARED")
        _assert_err("fn main(): int { { let x: int = 1; } return x; }", "SEM_UNDECLARED")

    def test_18_arity_mismatches(self):
        _assert_err("fn foo(a: int): int { return a; } fn main(): int { return foo(); }", "SEM_ARITY_MISMATCH")
        _assert_err("fn foo(a: int, b: int): int { return a; } fn main(): int { return foo(1); }", "SEM_ARITY_MISMATCH")
        _assert_err("fn foo(a: int): int { return a; } fn main(): int { return foo(1,2); }", "SEM_ARITY_MISMATCH")
        _assert_ok("fn foo(): int { return 1; } fn main(): int { return foo(); }")
        _assert_ok("fn foo(a: int): int { return a; } fn main(): int { return foo(1); }")

    def test_19_unknown_fn(self):
        _assert_err("fn main(): int { return print(1); }", "SEM_UNKNOWN_FUNCTION")
        _assert_err("fn main(): int { return unknown(); }", "SEM_UNKNOWN_FUNCTION")
        _assert_ok("fn foo(): int { return 1; } fn main(): int { return foo(); }")

    def test_20_bare_return_typed_err(self):
        _assert_err("fn main(): int { return; }", "SEM_TYPE_MISMATCH")

    def test_21_value_return_untyped_err(self):
        _assert_err("fn main() { return 1; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn foo(): int { return 1; } fn main() { return foo(); }", "SEM_TYPE_MISMATCH")

    def test_22_let_type_mismatch(self):
        _assert_err("fn main(): int { let x: int = true; return x; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn main(): int { let x: bool = 1; return 1; }", "SEM_TYPE_MISMATCH")

    def test_23_equality_str_int_mix(self):
        _assert_err("fn main(): bool { return 1 == true; }", "SEM_TYPE_MISMATCH")
        _assert_ok("fn main(): bool { let a: int = 1; let b: int = 1; return a == b; }")

    def test_24_if_while_cond(self):
        _assert_err("fn main() { if 1 { } }", "SEM_TYPE_MISMATCH")
        _assert_err("fn main() { while 1 { } }", "SEM_TYPE_MISMATCH")
        _assert_ok("fn main() { if true { } }")
        _assert_ok("fn main() { while true { } }")

    def test_35_parameter_cannot_shadow_later_function(self):
        _assert_err(
            "fn first(later: int): int { return later; } fn later(): int { return 1; }",
            "SEM_DUPLICATE",
        )

    def test_36_self_recursion_and_nested_argument_type(self):
        _assert_ok("fn recur(x: int): int { return recur(x); }")
        _assert_err(
            "fn take(x: int) {} fn flag(): bool { return true; } fn main() { take(flag()); }",
            "SEM_TYPE_MISMATCH",
        )

class SemanticsSpanTests(unittest.TestCase):
    def test_25_span_accuracy(self):
        if analyzer is None:
            self.skipTest("no analyzer")
        src="fn main(): int { let x: int = y; }"
        ok,tree,diag,code,span=_do_analyze(src)
        self.assertEqual(code, "SEM_UNDECLARED")
        # span should be at y
        self.assertIsNotNone(span)
        # check line/col
        if isinstance(span, dict):
            line=span.get("line")
        else:
            line=getattr(span,"line",None)
        self.assertIsNotNone(line)

    def test_26_symbol_determinism(self):
        if analyzer is None:
            self.skipTest("no analyzer")
        src="fn foo(a: int): int { let x: int = a; return x; } fn bar(): int { let y: int = 1; return y; }"
        ok1,tree1,diag1,code1,span1=_do_analyze(src)
        ok2,tree2,diag2,code2,span2=_do_analyze(src)
        self.assertTrue(ok1 and ok2)
        # check that symbol indices are deterministic
        import json
        def norm(n):
            if isinstance(n, dict):
                return {k: norm(v) for k,v in n.items() if k!="span"}
            if isinstance(n, list):
                return [norm(x) for x in n]
            return n
        self.assertEqual(norm(tree1), norm(tree2))

    def test_27_forward_fn_symbol(self):
        _assert_ok("fn main(): int { return foo(); } fn foo(): int { return 42; }")
        ok,tree,diag,code,span=_do_analyze("fn main(): int { return foo(); } fn foo(): int { return 42; }")
        self.assertTrue(ok)
        # find Call symbol should equal foo's symbol
        def find_calls(node, out=None):
            if out is None:
                out=[]
            if isinstance(node, dict):
                if node.get("kind")=="Call":
                    out.append(node)
                for v in node.values():
                    find_calls(v,out)
            elif isinstance(node, list):
                for x in node:
                    find_calls(x,out)
            return out
        calls=find_calls(tree)
        self.assertGreaterEqual(len(calls),1)
        # find functions
        def find_funcs(node):
            funcs=[]
            if isinstance(node, dict) and node.get("kind")=="Function":
                funcs.append(node)
            if isinstance(node, dict):
                for v in node.values():
                    if isinstance(v, (dict,list)):
                        funcs.extend(find_funcs(v) if isinstance(v, dict) else [item for lst in v for item in ([find_funcs(item)] if isinstance(item, dict) else [])])
            elif isinstance(node, list):
                for x in node:
                    funcs.extend(find_funcs(x) if isinstance(x, dict) else [])
            return funcs
        # simpler: tree is dict with functions
        funcs=tree.get("functions",[]) if isinstance(tree, dict) else []
        foo_sym=None
        for f in funcs:
            if f.get("name")=="foo":
                foo_sym=f.get("symbol")
        self.assertIsNotNone(foo_sym)
        for c in calls:
            if c.get("callee")=="foo":
                self.assertEqual(c.get("symbol"), foo_sym)

    def test_37_multiline_end_position_is_source_accurate(self):
        src = "fn main(): int {\n    return 1;\n}"
        ok, tree, diag, code, span = _do_analyze(src, "multi.rl")
        self.assertTrue(ok, diag)
        function_span = tree["functions"][0]["span"]
        self.assertEqual(function_span["end"], {"line": 3, "column": 2, "offset": len(src)})

class SemanticsApiTests(unittest.TestCase):
    def test_28_api_and_cli(self):
        if analyzer is None or ANALYZER_PATH is None:
            self.skipTest("no analyzer")
        src="fn main(): int { return 1; }"
        ok1,tree1,diag1,code1,span1=_do_analyze(src)
        ok2,tree2,diag2,code2,span2=_do_analyze(src)
        self.assertEqual(ok1, ok2)
        self.assertEqual(code1, code2)
        # CLI success
        import tempfile, subprocess, json
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"good.rl"
            p.write_text("fn main(): int { return 1; }", encoding="utf-8")
            proc=subprocess.run([sys.executable, str(ANALYZER_PATH), str(p)], capture_output=True, text=True, timeout=10, cwd=ROOT)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("SEM_OK", proc.stdout)
            # deterministic 3x
            proc2=subprocess.run([sys.executable, str(ANALYZER_PATH), str(p)], capture_output=True, text=True, timeout=10, cwd=ROOT)
            self.assertEqual(proc.stdout, proc2.stdout)
            proc_repeat=subprocess.run([sys.executable, str(ANALYZER_PATH), str(p)], capture_output=True, text=True, timeout=10, cwd=ROOT)
            self.assertEqual(proc.stdout, proc_repeat.stdout)
            # CLI failure
            bad=Path(td)/"bad.rl"
            bad.write_text("fn main(): int { let x: int = true; }", encoding="utf-8")
            proc3=subprocess.run([sys.executable, str(ANALYZER_PATH), str(bad)], capture_output=True, text=True, timeout=10, cwd=ROOT)
            self.assertEqual(proc3.returncode, 1)
            self.assertNotIn("SEM_OK", proc3.stdout)

    def test_29_lex_parse_passthrough(self):
        if analyzer is None:
            self.skipTest("no analyzer")
        # lex error
        ok,tree,diag,code,span=_do_analyze('"unterminated')
        self.assertFalse(ok)
        self.assertTrue(code.startswith("PAR_") or code.startswith("LEX_"))
        # parse error
        ok2,tree2,diag2,code2,span2=_do_analyze("fn foo(): int { let x: int = 1 }") # missing ;
        self.assertFalse(ok2)
        self.assertTrue(code2.startswith("PAR_"))

    def test_40_invalid_file_api_and_cli_are_bounded(self):
        result = analyzer.analyze_file(None)
        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostic.code, "PAR_INVALID_INPUT")
        missing = ROOT / "tests" / "fixtures" / "rynorlang" / "definitely-missing.rl"
        proc = subprocess.run(
            [sys.executable, str(ANALYZER_PATH), str(missing)],
            capture_output=True, text=True, timeout=10, cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("PAR_INVALID_INPUT", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_41_depth_and_source_size_are_bounded(self):
        deep = "fn main(): int { return " + "(" * 300 + "1" + ")" * 300 + "; }"
        result = analyzer.analyze(deep, "deep.rl")
        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostic.code, "PAR_DEPTH_EXCEEDED")
        oversized = analyzer.analyze(" " * (1024 * 1024 + 1), "large.rl")
        self.assertFalse(oversized.ok)
        self.assertEqual(oversized.diagnostic.code, "PAR_FILE_TOO_LARGE")

    def test_42_random_garbage_returns_one_diagnostic_without_exception(self):
        rng = random.Random(0x14A57)
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789{}();,:!@#$%^&*+-=/\\\" \n\t"
        with tempfile.TemporaryDirectory() as directory:
            for index in range(20):
                source = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 160)))
                with self.subTest(index=index, source=source):
                    result = analyzer.analyze(source, f"garbage-{index}.rl")
                    self.assertFalse(result.ok)
                    self.assertIsNotNone(result.diagnostic)
                    self.assertIsNone(result.ast)
                    path = Path(directory) / f"garbage-{index}.rl"
                    path.write_text(source, encoding="ascii")
                    proc = subprocess.run(
                        [sys.executable, str(ANALYZER_PATH), str(path)],
                        capture_output=True, text=True, timeout=10, cwd=ROOT,
                    )
                    self.assertEqual(proc.returncode, 1)
                    self.assertNotIn("Traceback", proc.stderr)
                    self.assertEqual(proc.stdout, "")

class SemanticsMutationTests(unittest.TestCase):
    def _load_mutant(self, old, new):
        text=ANALYZER_PATH.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, f"mutation anchor must be unique: {old!r}")
        mutated=text.replace(old, new, 1)
        td=tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tmp=Path(td.name)/ANALYZER_PATH.name
        tmp.write_text(mutated, encoding="utf-8")
        module_name=f"mut_analyze_{len(sys.modules)}"
        spec=importlib.util.spec_from_file_location(module_name, tmp)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mod=importlib.util.module_from_spec(spec)
        sys.modules[module_name]=mod
        self.addCleanup(sys.modules.pop, module_name, None)
        spec.loader.exec_module(mod)
        return mod

    def _assert_check_removal_changes_result(self, old, new, bad_src, original_code):
        baseline = analyzer.analyze(bad_src, "<baseline>")
        self.assertFalse(baseline.ok)
        self.assertEqual(baseline.diagnostic.code, original_code)
        mutant = self._load_mutant(old, new)
        result = mutant.analyze(bad_src, "<mutant>")
        self.assertTrue(result.ok, f"removed check still rejected input: {result.diagnostic}")

    def test_30_mutation_scope_lookup(self):
        self._assert_check_removal_changes_result(
            'if entry is None:\n                    self._error(C_UNDECLARED,',
            'if entry is None:\n                    entry = (0, "int", node.span)\n                if False:\n                    self._error(C_UNDECLARED,',
            "fn main(): int { return x; }", "SEM_UNDECLARED")

    def test_31_mutation_duplicate(self):
        self._assert_check_removal_changes_result(
            'if name in self.global_funcs:\n                    self._error(C_DUPLICATE, f"duplicate function',
            'if False and name in self.global_funcs:\n                    self._error(C_DUPLICATE, f"duplicate function',
            "fn foo(): int { return 1; } fn foo(): int { return 2; }", "SEM_DUPLICATE")

    def test_32_mutation_type_binop(self):
        self._assert_check_removal_changes_result(
            'if ltype != "int" or rtype != "int":\n                        self._error(C_TYPE_MISMATCH, f"operator',
            'if False and (ltype != "int" or rtype != "int"):\n                        self._error(C_TYPE_MISMATCH, f"operator',
            "fn main(): int { return 1 + true; }", "SEM_TYPE_MISMATCH")

    def test_33_mutation_arity(self):
        self._assert_check_removal_changes_result(
            "if len(args) != expected_arity:\n                    self._error(C_ARITY_MISMATCH,",
            "if False and len(args) != expected_arity:\n                    self._error(C_ARITY_MISMATCH,",
            "fn foo(a: int): int { return a; } fn main(): int { return foo(); }", "SEM_ARITY_MISMATCH")

    def test_34_mutation_unknown_fn(self):
        self._assert_check_removal_changes_result(
            'if callee_name not in self.global_funcs:\n                    self._error(C_UNKNOWN_FUNCTION,',
            'if callee_name not in self.global_funcs:\n                    self.global_funcs[callee_name] = {"params": [], "ret_type": "int", "symbol": 0}\n                if False:\n                    self._error(C_UNKNOWN_FUNCTION,',
            "fn main(): int { return bar(); }", "SEM_UNKNOWN_FUNCTION")

    def test_38_mutation_no_shadowing(self):
        self._assert_check_removal_changes_result(
            "if name in scope:\n                self._error(C_DUPLICATE,",
            "if False and name in scope:\n                self._error(C_DUPLICATE,",
            "fn main(): int { let x: int = 1; { let x: int = 2; } return x; }", "SEM_DUPLICATE")

    def test_39_mutation_unit_as_value(self):
        self._assert_check_removal_changes_result(
            'if result_type == "unit" and not allow_unit:\n                    self._error(C_TYPE_MISMATCH,',
            'if result_type == "unit" and not allow_unit:\n                    result_type = "int"\n                if False:\n                    self._error(C_TYPE_MISMATCH,',
            "fn helper() {} fn main() { let x: int = helper(); }", "SEM_TYPE_MISMATCH")
