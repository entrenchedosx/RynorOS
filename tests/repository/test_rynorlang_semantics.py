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
        allowed={"__future__","argparse","json","sys","pathlib","typing","dataclasses","tools","re","collections","bisect","types"}
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
        src = (
            "fn foo(a: int, b: int): int { let x: int = a; "
            "{ let y: int = b; return y; } "
            "{ let y: int = x; return y; } return x; } "
            "fn bar(a: int): int { let x: int = a; return x; }"
        )
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
        # Repeated names in sibling blocks and separate functions must bind
        # to their own declaration, while nested uses retain outer bindings.
        declarations = []
        references = []
        pending = [tree1]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if node.get("kind") in ("Function", "Param", "Let"):
                    declarations.append((node["kind"], node["name"], node["symbol"]))
                elif node.get("kind") == "Var":
                    references.append((node["name"], node["symbol"]))
                pending.extend(reversed(list(node.values())))
            elif isinstance(node, list):
                pending.extend(reversed(node))
        self.assertEqual(declarations, [
            ("Function", "foo", 0), ("Param", "a", 2), ("Param", "b", 3),
            ("Let", "x", 4), ("Let", "y", 5), ("Let", "y", 6),
            ("Function", "bar", 1), ("Param", "a", 7), ("Let", "x", 8),
        ])
        self.assertEqual(references, [
            ("a", 2), ("b", 3), ("y", 5), ("x", 4),
            ("y", 6), ("x", 4), ("a", 7), ("x", 8),
        ])

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
            # Flat expressions are valid even when their AST exceeds Python's
            # recursive JSON encoder depth. Check the complete CLI output.
            chain = Path(td) / "flat-chain.rl"
            chain.write_text("fn main(): int { return " + " + ".join(["1"] * 5000) + "; }", encoding="ascii")
            flat = subprocess.run(
                [sys.executable, str(ANALYZER_PATH), str(chain)],
                capture_output=True, text=True, timeout=30, cwd=ROOT,
            )
            self.assertEqual(flat.returncode, 0, flat.stderr)
            self.assertEqual(flat.stderr, "")
            # Decode in a separate process so this test never changes its own
            # recursion limit (the standard JSON decoder is recursive too).
            decoded = subprocess.run(
                [sys.executable, "-c",
                 "import json,sys; sys.setrecursionlimit(30000); "
                 "tree=json.loads(sys.stdin.read()); "
                 "print(tree['kind']); print(tree['functions'][0]['name'])"],
                input=flat.stdout.removesuffix("SEM_OK\n"), capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertEqual(decoded.stdout.splitlines(), ["Program", "main"])
            self.assertTrue(flat.stdout.endswith("\nSEM_OK\n"))
            self.assertEqual(flat.stdout.count('"kind":"BinOp"'), 4999)

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

    def test_43_analyzer_matches_parser_depth_boundary_exactly(self):
        """The parser accepts up to 254 nested constructs; the analyzer must
        accept exactly the same programs, never fewer."""
        cases = {
            "while": lambda n: "fn f() { " + "while true { " * n + "}" * n + " }",
            "block": lambda n: "fn f(): int { " + "{ " * n + "let x: int = 1; " + "}" * n + " return 1; }",
            "call": lambda n: "fn g(a: int): int { return a; } fn f(): int { return " + "g(" * n + "1" + ")" * n + "; }",
            "unary": lambda n: "fn f(): bool { return " + "!" * n + "true; }",
            "group": lambda n: "fn f(): int { return " + "(" * n + "1" + ")" * n + "; }",
        }
        for label, build in cases.items():
            for depth in (250, 254):
                src = build(depth)
                self.assertTrue(parser.parse(src).ok, f"{label} {depth} must parse")
                with self.subTest(construct=label, depth=depth):
                    ok, tree, diag, code, span = _do_analyze(src)
                    self.assertTrue(ok, f"{label} {depth} rejected by analyzer: {code}")
            src = build(255)
            self.assertFalse(parser.parse(src).ok, f"{label} 255 must not parse")
            ok, tree, diag, code, span = _do_analyze(src)
            self.assertFalse(ok, f"{label} 255 must fail everywhere")
            self.assertEqual(code, "PAR_DEPTH_EXCEEDED")

        # A flat chain has no parser nesting limit, despite a deeply nested
        # left-associated AST. Lower every operator without truncation.
        for typ, literal, operator in (("int", "1", "+"), ("bool", "true", "&&")):
            with self.subTest(flat_operator=operator):
                src = f"fn f(): {typ} {{ return " + f" {operator} ".join([literal] * 5000) + "; }"
                self.assertTrue(parser.parse(src).ok)
                ok, tree, diag, code, span = _do_analyze(src)
                self.assertTrue(ok, f"flat chain rejected: {code}")
                pending = [tree]
                binary_count = 0
                while pending:
                    node = pending.pop()
                    if isinstance(node, dict):
                        if node.get("kind") == "BinOp":
                            binary_count += 1
                            self.assertEqual(node["type"], typ)
                            self.assertEqual(node["op"], operator)
                        pending.extend(node.values())
                    elif isinstance(node, list):
                        pending.extend(node)
                self.assertEqual(binary_count, 4999)

    def test_44_non_identifier_callee_is_rejected_at_the_callee(self):
        """A call whose callee is not a plain identifier must be rejected as
        non-callable at the callee span with an honest message. It must never
        resolve through str(None): defining a function literally named None
        must not make (f)() resolve to it."""
        for src in (
            "fn f(): int { return 1; } fn main(): int { return f()(); }",
            "fn f(): int { return 1; } fn None(): int { return 2; } fn main(): int { return (f)(); }",
            "fn main(): int { return 1(2); }",
            'fn main(): int { return "f"(); }',
            "fn main(): int { return true(); }",
        ):
            with self.subTest(src=src):
                ok, tree, diag, code, span = _do_analyze(src)
                self.assertFalse(ok)
                self.assertEqual(code, "SEM_UNKNOWN_FUNCTION")
                self.assertIn("is not a function name", diag.message,
                              "callee diagnostic must be honest, never 'unknown function None'")
                self.assertNotEqual(diag.got, "None")
        ok, tree, diag, code, span = _do_analyze(
            "fn f(): int { return 1; } fn main(): int { return f(); }")
        self.assertTrue(ok)

    def test_45_analyze_bytes_matches_analyze(self):
        for src in (
            "fn main(): int { return 1; }",
            "fn f(): str { return \"a\\nb\"; }",
            "fn main(): int { let x: bool = 1; }",
            "fn main(): int { return y; }",
        ):
            with self.subTest(src=src):
                a = analyzer.analyze(src, "bytes-eq.rl")
                b = analyzer.analyze_bytes(src.encode("ascii"), "bytes-eq.rl")
                self.assertEqual(a.ok, b.ok)
                self.assertEqual(a.diagnostic.code if a.diagnostic else None,
                                 b.diagnostic.code if b.diagnostic else None)
                if a.ok:
                    self.assertEqual(json.dumps(a.ast, sort_keys=True),
                                     json.dumps(b.ast, sort_keys=True))
        bad = analyzer.analyze_bytes(chr(0xE9).encode("latin-1"), "bad.rl")
        self.assertFalse(bad.ok)
        self.assertEqual(bad.diagnostic.code, "PAR_LEX_ERROR")

    def test_46_else_if_chain_lowering_and_typing(self):
        ok, tree, diag, code, span = _do_analyze(
            "fn f(n: int): int { if n > 0 { return 1; } else if n < 0 { return 2; } else { return 3; } }")
        self.assertTrue(ok, f"else-if chain rejected: {code}")
        body = tree["functions"][0]["body"]["stmts"][0]
        self.assertEqual(body["kind"], "If")
        self.assertEqual(body["then"]["kind"], "Block")
        self.assertEqual(body["else"]["kind"], "If", "else-if must lower to nested If")
        self.assertEqual(body["else"]["else"]["kind"], "Block")
        _assert_err("fn f(n: int): int { if n > 0 { return 1; } else if 1 { return 2; } }",
                    "SEM_TYPE_MISMATCH")

    def test_47_bool_equality_and_let_shadows_function(self):
        _assert_ok("fn main(): bool { let a: bool = true; return a == false; }")
        _assert_ok("fn main(): bool { return true != false; }")
        _assert_err("fn main(): bool { return true == 1; }", "SEM_TYPE_MISMATCH")
        _assert_err("fn foo(): int { return 1; } fn main(): int { let foo: int = 1; return foo; }",
                    "SEM_DUPLICATE")

    def test_48_span_values_point_at_offending_tokens(self):
        src = "fn main(): int { let x: bool = 1; }"
        d = _assert_err(src, "SEM_TYPE_MISMATCH")[0]
        self.assertEqual((d.span.line, d.span.column, d.span.offset, d.span.length),
                         (1, 32, 31, 1), "let mismatch points at the initializer '1'")
        src = "fn main(): int { let x: int = y; }"
        d = _assert_err(src, "SEM_UNDECLARED")[0]
        self.assertEqual((d.span.column, d.span.offset, d.span.length), (31, 30, 1))
        src = "fn foo(): int { return 1; } fn foo(): int { return 2; }"
        d = _assert_err(src, "SEM_DUPLICATE")[0]
        self.assertEqual((d.span.column, d.span.offset), (29, 28),
                         "duplicate points at the second declaration")
        src = "fn t(a: int): int { return a; } fn main(): int { return t(true); }"
        d = _assert_err(src, "SEM_TYPE_MISMATCH")[0]
        self.assertEqual((d.span.column, d.span.offset, d.span.length), (59, 58, 4),
                         "arg type points at the offending argument 'true'")
        src = 'fn main(): bool { return 1 < "a"; }'
        d = _assert_err(src, "SEM_TYPE_MISMATCH")[0]
        self.assertEqual((d.span.column, d.span.offset, d.span.length), (26, 25, 7),
                         "relational error covers the offending operator expression")

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
            "if len(args) != expected_arity:\n                        self._error(C_ARITY_MISMATCH,",
            "if False and len(args) != expected_arity:\n                        self._error(C_ARITY_MISMATCH,",
            "fn foo(a: int): int { return a; } fn main(): int { return foo(); }", "SEM_ARITY_MISMATCH")

    def test_34_mutation_unknown_fn(self):
        self._assert_check_removal_changes_result(
            'if callee_name not in self.global_funcs:\n                        self._error(C_UNKNOWN_FUNCTION,',
            'if callee_name not in self.global_funcs:\n                        self.global_funcs[callee_name] = {"params": [], "ret_type": "int", "symbol": 0}\n                    if False:\n                        self._error(C_UNKNOWN_FUNCTION,',
            "fn main(): int { return bar(); }", "SEM_UNKNOWN_FUNCTION")

    def test_38_mutation_no_shadowing(self):
        self._assert_check_removal_changes_result(
            "if name in scope:\n                self._error(C_DUPLICATE,",
            "if False and name in scope:\n                self._error(C_DUPLICATE,",
            "fn main(): int { let x: int = 1; { let x: int = 2; } return x; }", "SEM_DUPLICATE")

    def test_39_mutation_unit_as_value(self):
        # A unit-typed initializer is diagnosed by the dedicated unit check,
        # not by the generic type-equality check below it: removing the first
        # must flip the message from "let initializer ... is unit" to the
        # equality form, proving the dedicated check fires first.
        text = ANALYZER_PATH.read_text(encoding="utf-8")
        anchor = ('if init_type == "unit":\n'
                  '                    self._error(C_TYPE_MISMATCH, f"let initializer for \'{name}\' is unit", expr_node.span,\n'
                  '                                expected=typ, got="unit", name=name, context="let")\n')
        self.assertEqual(text.count(anchor), 1, "let-unit guard missing")
        mutant = self._load_mutant(
            anchor,
            'if False and init_type == "unit":\n'
            '                    self._error(C_TYPE_MISMATCH, f"let initializer for \'{name}\' is unit", expr_node.span,\n'
            '                                expected=typ, got="unit", name=name, context="let")\n')
        probe = "fn helper() {} fn main() { let x: int = helper(); }"
        baseline = analyzer.analyze(probe, "<baseline>")
        self.assertFalse(baseline.ok)
        self.assertIn("is unit", baseline.diagnostic.message)
        result = mutant.analyze(probe, "<mutant>")
        self.assertFalse(result.ok)
        self.assertNotIn("is unit", result.diagnostic.message,
                         "unit-guard removal must fall through to the equality message")

    def test_49_mutation_equality_typing(self):
        self._assert_check_removal_changes_result(
            'if ltype != rtype or ltype not in ("int","bool","str"):',
            'if False and (ltype != rtype or ltype not in ("int","bool","str")):',
            'fn main(): bool { return 1 == "a"; }', "SEM_TYPE_MISMATCH")

    def test_50_mutation_relational_typing(self):
        self._assert_check_removal_changes_result(
            'if ltype != "int" or rtype != "int":\n                        self._error(C_TYPE_MISMATCH, f"relational',
            'if False and (ltype != "int" or rtype != "int"):\n                        self._error(C_TYPE_MISMATCH, f"relational',
            'fn main(): bool { return "a" < "b"; }', "SEM_TYPE_MISMATCH")

    def test_51_mutation_logical_typing(self):
        self._assert_check_removal_changes_result(
            'if ltype != "bool" or rtype != "bool":',
            'if False and (ltype != "bool" or rtype != "bool"):',
            "fn main(): bool { return 1 && true; }", "SEM_TYPE_MISMATCH")

    def test_52_mutation_unary_typing(self):
        self._assert_check_removal_changes_result(
            'if op == "-":\n                if otype != "int":\n                    self._error(C_TYPE_MISMATCH, f"unary',
            'if op == "-":\n                if False and otype != "int":\n                    self._error(C_TYPE_MISMATCH, f"unary',
            "fn main(): int { return -true; }", "SEM_TYPE_MISMATCH")

    def test_53_mutation_if_condition(self):
        self._assert_check_removal_changes_result(
            'if ctype != "bool":\n                    self._error(C_TYPE_MISMATCH, f"if condition',
            'if False and ctype != "bool":\n                    self._error(C_TYPE_MISMATCH, f"if condition',
            "fn main() { if 1 { } }", "SEM_TYPE_MISMATCH")

    def test_54_mutation_while_condition(self):
        self._assert_check_removal_changes_result(
            'if ctype != "bool":\n                    self._error(C_TYPE_MISMATCH, f"while condition',
            'if False and ctype != "bool":\n                    self._error(C_TYPE_MISMATCH, f"while condition',
            "fn main() { while 1 { } }", "SEM_TYPE_MISMATCH")

    def test_55_mutation_return_typing(self):
        self._assert_check_removal_changes_result(
            'if init_type != ret_type:\n                        self._error(C_TYPE_MISMATCH, f"return expects',
            'if False and init_type != ret_type:\n                        self._error(C_TYPE_MISMATCH, f"return expects',
            "fn main(): int { return true; }", "SEM_TYPE_MISMATCH")

    def test_56_mutation_let_typing(self):
        self._assert_check_removal_changes_result(
            'if init_type != typ:\n                    self._error(C_TYPE_MISMATCH, f"let \'{name}\' expects',
            'if False and init_type != typ:\n                    self._error(C_TYPE_MISMATCH, f"let \'{name}\' expects',
            "fn main(): int { let x: bool = 1; return 1; }", "SEM_TYPE_MISMATCH")

    def test_57_mutation_argument_typing(self):
        self._assert_check_removal_changes_result(
            'if atype != ptype:\n                            self._error(C_TYPE_MISMATCH, f"arg {i}',
            'if False and atype != ptype:\n                            self._error(C_TYPE_MISMATCH, f"arg {i}',
            "fn t(a: int): int { return a; } fn main(): int { return t(true); }",
            "SEM_TYPE_MISMATCH")

    def test_58_mutation_duplicate_parameter(self):
        self._assert_check_removal_changes_result(
            'if pname in seen:\n                        self._error(C_DUPLICATE, f"duplicate parameter',
            'if False and pname in seen:\n                        self._error(C_DUPLICATE, f"duplicate parameter',
            "fn foo(a: int, a: bool): int { return 1; }", "SEM_DUPLICATE")

    def test_59_mutation_parameter_shadows_function(self):
        self._assert_check_removal_changes_result(
            'if pname in self.global_funcs:\n                        self._error(C_DUPLICATE, f"duplicate declaration',
            'if False and pname in self.global_funcs:\n                        self._error(C_DUPLICATE, f"duplicate declaration',
            "fn first(later: int): int { return later; } fn later(): int { return 1; }",
            "SEM_DUPLICATE")

    def test_60_mutation_let_shadows_function(self):
        self._assert_check_removal_changes_result(
            'if name in self.global_funcs:\n            self._error(C_DUPLICATE, f"duplicate declaration \'{name}\' shadows function',
            'if False and name in self.global_funcs:\n            self._error(C_DUPLICATE, f"duplicate declaration \'{name}\' shadows function',
            "fn foo(): int { return 1; } fn main(): int { let foo: int = 1; return foo; }",
            "SEM_DUPLICATE")

    def test_61_mutation_non_identifier_callee_honesty(self):
        # The non-identifier-callee guard exists for diagnostic honesty: a
        # grouped/chained callee has text None, and resolving through it
        # produced the misleading "unknown function 'None'". Removing the
        # guard must flip the message, which test_44 pins.
        text = ANALYZER_PATH.read_text(encoding="utf-8")
        anchor = 'if callee_node.kind != "Identifier":'
        self.assertEqual(text.count(anchor), 1, "callee-kind guard missing")
        mutant = self._load_mutant(
            'if callee_node.kind != "Identifier":\n'
            '                        self._error(C_UNKNOWN_FUNCTION, f"called expression is not a function name", callee_node.span,\n'
            '                                    expected="identifier callee", got=callee_node.kind, context="call")\n',
            'if False and callee_node.kind != "Identifier":\n'
            '                        self._error(C_UNKNOWN_FUNCTION, f"called expression is not a function name", callee_node.span,\n'
            '                                    expected="identifier callee", got=callee_node.kind, context="call")\n')
        probe = "fn main(): int { return 1(2); }"
        baseline = analyzer.analyze(probe, "<baseline>")
        self.assertFalse(baseline.ok)
        self.assertIn("is not a function name", baseline.diagnostic.message)
        result = mutant.analyze(probe, "<mutant>")
        self.assertFalse(result.ok)
        self.assertNotIn("is not a function name", result.diagnostic.message,
                         "guard removal must change the diagnostic to the misleading form")

    def test_62_trampoline_drive_contract(self):
        from types import GeneratorType
        drive = analyzer.Analyzer._drive
        # Nested generators evaluate depth-first with value plumbing.
        def leaf():
            return 7
            yield  # pragma: no cover - makes this a generator without yielding
        def middle():
            value = yield leaf()
            return value * 2
        def root():
            return (yield middle())
        self.assertEqual(drive(root()), 14)
        # A non-generator work item is a contract violation, never silent.
        def bad():
            yield 42
        with self.assertRaises(TypeError):
            drive(bad())
        # Abort-style exceptions propagate (diagnostic discipline belongs to
        # the raiser, the driver only unwinds).
        class Boom(Exception):
            pass
        def raiser():
            raise Boom()
            yield
        with self.assertRaises(Boom):
            drive(raiser())

    def test_63_analyzer_depth_guard_fires_on_its_own(self):
        # test_43 proves parser and analyzer agree through the public pipeline,
        # but there the parser rejects first, so the analyzer's own guard is
        # never exercised. Drive Analyzer directly with a hand-built tree that
        # bypasses the parser: only the analyzer's counter can reject it, and
        # its own message (not the parser's) must be reported.
        Span = lexer.Span
        ParseNode = parser.ParseNode
        span = Span("<deep>", 1, 1, 0, 0)
        body = ParseNode("Block", span, ())
        for _ in range(300):
            cond = ParseNode("BooleanLiteral", span, (), text="true")
            body = ParseNode("Block", span, (ParseNode("WhileStmt", span, (cond, body)),))
        fn = ParseNode("FunctionDef", span,
                       (ParseNode("Identifier", span, (), text="f"), body), text="f")
        program = ParseNode("Program", span, (fn,))
        result = analyzer.Analyzer(program, source="").analyze()
        self.assertFalse(result.ok)
        self.assertIsNone(result.ast)
        self.assertEqual(result.diagnostic.code, "PAR_DEPTH_EXCEEDED")
        self.assertEqual(result.diagnostic.message, "nesting depth exceeds 256")
        # Removing the analyzer guard must flip the verdict: the trampoline
        # lowers without the Python call stack, so no host RecursionError
        # stands in for the counter.
        mutant = self._load_mutant(
            "        if self.depth > MAX_DEPTH:\n",
            "        if False and self.depth > MAX_DEPTH:\n")
        deep_result = mutant.Analyzer(program, source="").analyze()
        self.assertTrue(deep_result.ok, deep_result.diagnostic)
