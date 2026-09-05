"""Stage 13 conformance tests for the host-side RynorLang parser."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PARSER_PATH = ROOT / "tools" / "rynorlang" / "parse.py"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "parser" / "good"
BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "parser" / "bad"


def load_parser(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load parser module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parser = load_parser(PARSER_PATH, "rynorlang_stage13_parser")


def parse_ok(source: str):
    result = parser.parse(source, "test.rl")
    if not result.ok:
        raise AssertionError(result.diagnostic)
    return result.root


def parse_bad(source: str, code: str | None = None):
    result = parser.parse(source, "test.rl")
    if result.ok:
        raise AssertionError("malformed source parsed successfully")
    if code is not None and result.diagnostic.code != code:
        raise AssertionError(f"expected {code}, got {result.diagnostic.code}")
    return result.diagnostic


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def first(node, kind: str):
    return next(item for item in walk(node) if item.kind == kind)


def expression(source: str):
    root = parse_ok(f"fn main() {{ {source}; }}")
    return first(root, "ExprStmt").children[0]


def mutated(old: str, new: str, name: str):
    source = PARSER_PATH.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"mutation anchor {name} is not unique")
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "parse.py"
    path.write_text(source.replace(old, new), encoding="utf-8")
    module = load_parser(path, f"rynorlang_mutant_{name}")
    return directory, module


class Stage13ParserTests(unittest.TestCase):
    def test_01_parser_file_exists(self):
        self.assertTrue(PARSER_PATH.is_file())

    def test_02_host_only_imports(self):
        source = PARSER_PATH.read_text(encoding="utf-8")
        import ast
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        allowed = {"__future__", "argparse", "json", "sys", "threading", "dataclasses", "pathlib", "typing", "tools"}
        for imp in imports:
            self.assertIn(imp, allowed, f"unexpected import {imp}")

    def test_03_public_api_exists(self):
        for name in ("parse", "parse_bytes", "parse_file", "parse_tokens"):
            self.assertTrue(callable(getattr(parser, name)))

    def test_04_result_is_frozen(self):
        result = parser.parse("")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.root = None

    def test_05_nodes_are_frozen(self):
        root = parse_ok("")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            root.kind = "Other"

    def test_06_fixture_inventory(self):
        self.assertEqual(14, len(list(GOOD.glob("*.rl"))))
        self.assertEqual(21, len(list(BAD.glob("*.rl"))))

    def test_07_all_good_fixtures_parse(self):
        for path in sorted(GOOD.glob("*.rl")):
            with self.subTest(path=path.name):
                self.assertTrue(parser.parse_file(path).ok, parser.parse_file(path).diagnostic)

    def test_08_all_bad_fixtures_reject(self):
        for path in sorted(BAD.glob("*.rl")):
            with self.subTest(path=path.name):
                self.assertFalse(parser.parse_file(path).ok)

    def test_09_lexer_error_is_wrapped(self):
        diagnostic = parse_bad('fn x(){ "\\q"; }', "PAR_LEX_ERROR")
        self.assertEqual("LEX_INVALID_ESCAPE", diagnostic.got_kind)

    def test_10_bang_token_integrates(self):
        node = expression("!false")
        self.assertEqual(("UnaryExpr", "!", "BooleanLiteral"), (node.kind, node.text, node.children[0].kind))

    def test_11_empty_program(self):
        self.assertEqual("Program", parse_ok("").kind)

    def test_12_empty_function(self):
        self.assertEqual("FunctionDef", parse_ok("fn main() {}").children[0].kind)

    def test_13_colon_return_type(self):
        function = parse_ok("fn main(): int { return 1; }").children[0]
        self.assertEqual("int", first(function, "Type").text)

    def test_14_parameters(self):
        params = first(parse_ok("fn f(a: int, b: str) {}"), "ParamList")
        self.assertEqual(("a", "b"), tuple(node.text for node in params.children))

    def test_15_let_statement(self):
        self.assertEqual("LetStmt", first(parse_ok("fn f(){ let x: int = 1; }"), "LetStmt").kind)

    def test_16_return_without_value(self):
        self.assertEqual((), first(parse_ok("fn f(){ return; }"), "ReturnStmt").children)

    def test_17_if_else(self):
        self.assertEqual(3, len(first(parse_ok("fn f(){ if true {} else {} }"), "IfStmt").children))

    def test_18_else_if(self):
        outer = first(parse_ok("fn f(){ if true {} else if false {} }"), "IfStmt")
        self.assertEqual("IfStmt", outer.children[2].kind)

    def test_19_while(self):
        self.assertEqual("WhileStmt", first(parse_ok("fn f(){ while true {} }"), "WhileStmt").kind)

    def test_20_calls(self):
        call = expression("f(1, 2)")
        self.assertEqual(("CallExpr", 2), (call.kind, len(call.children[1].children)))

    def test_21_parameter_trailing_comma_rejected(self):
        parse_bad("fn f(a: int,) {}", "PAR_EXPECTED_TOKEN")

    def test_22_call_trailing_comma_rejected(self):
        parse_bad("fn f(){ g(1,); }", "PAR_EXPECTED_TOKEN")

    def test_23_arrow_return_rejected(self):
        parse_bad("fn f() -> int {}", "PAR_EXPECTED_TOKEN")

    def test_24_missing_semicolon_rejected(self):
        parse_bad("fn f(){ return 1 }", "PAR_EXPECTED_TOKEN")

    def test_25_top_level_statement_rejected(self):
        parse_bad("let x: int = 1;", "PAR_UNEXPECTED_TOKEN")

    def test_26_unknown_type_rejected(self):
        parse_bad("fn f(a: custom) {}", "PAR_EXPECTED_TOKEN")

    def test_27_double_comma_rejected(self):
        parse_bad("fn f(a: int,, b: int) {}", "PAR_EXPECTED_TOKEN")

    def test_28_multiplication_precedes_addition(self):
        node = expression("1 + 2 * 3")
        self.assertEqual(("AdditiveExpr", "MultiplicativeExpr"), (node.kind, node.children[1].kind))

    def test_29_addition_precedes_comparison(self):
        node = expression("1 + 2 < 4")
        self.assertEqual(("RelationalExpr", "AdditiveExpr"), (node.kind, node.children[0].kind))

    def test_30_comparison_precedes_equality(self):
        node = expression("1 < 2 == true")
        self.assertEqual(("EqualityExpr", "RelationalExpr"), (node.kind, node.children[0].kind))

    def test_31_equality_precedes_and(self):
        node = expression("1 == 1 && true")
        self.assertEqual(("AndExpr", "EqualityExpr"), (node.kind, node.children[0].kind))

    def test_32_and_precedes_or(self):
        node = expression("true || false && true")
        self.assertEqual(("OrExpr", "AndExpr"), (node.kind, node.children[1].kind))

    def test_33_binary_is_left_associative(self):
        node = expression("9 - 3 - 1")
        self.assertEqual(("AdditiveExpr", "AdditiveExpr"), (node.kind, node.children[0].kind))

    def test_34_unary_is_right_associative(self):
        node = expression("!!false")
        self.assertEqual("UnaryExpr", node.children[0].kind)

    def test_35_grouping_overrides_precedence(self):
        node = expression("(1 + 2) * 3")
        self.assertEqual(("MultiplicativeExpr", "GroupExpr"), (node.kind, node.children[0].kind))

    def test_36_node_spans_are_byte_based(self):
        source = "fn f() { return 1; }"
        root = parse_ok(source)
        self.assertEqual((0, len(source)), (root.span.offset, root.span.length))

    def test_37_multiline_span_start(self):
        root = parse_ok("\n\nfn f() {}")
        function = root.children[0]
        self.assertEqual((3, 1, 2), (function.span.line, function.span.column, function.span.offset))

    def test_38_literal_preserves_value(self):
        node = expression('"a\\n"')
        self.assertEqual("a\n", node.value)

    def test_39_identifier_preserves_text(self):
        self.assertEqual("hello", expression("hello").text)

    def test_40_diagnostic_has_location(self):
        diagnostic = parse_bad("fn f(){\n return 1\n}")
        self.assertGreaterEqual(diagnostic.span.line, 2)
        self.assertGreaterEqual(diagnostic.span.column, 1)
        self.assertGreaterEqual(diagnostic.span.offset, 0)

    def test_41_unexpected_eof_code(self):
        parse_bad("fn f(){", "PAR_UNEXPECTED_EOF")

    def test_42_invalid_token_sequence_rejected(self):
        lexed = parser.lex("fn f() {}")
        parser.parse_tokens(tuple(reversed(lexed.tokens)))
        self.assertEqual("PAR_INVALID_INPUT", parser.parse_tokens(tuple(reversed(lexed.tokens))).diagnostic.code)
        eof = parser.lex("", "test.rl").tokens[0]
        malformed = [(dataclasses.replace(eof, span=None),), (None, eof)]
        for field in ("line", "column", "offset", "length"):
            for value in (None, "bad", 1.5, True):
                malformed.append((dataclasses.replace(
                    eof, span=dataclasses.replace(eof.span, **{field: value})),))
        malformed.extend([
            (dataclasses.replace(eof, span=dataclasses.replace(eof.span, filename=None)),),
            (dataclasses.replace(eof, lexeme=" "),),
            (dataclasses.replace(eof, value="unexpected"),),
            (dataclasses.replace(eof, kind=[]), eof),
        ])
        for changes in (
            {"line": 2}, {"column": 2},
            {"offset": 2, "line": 3, "column": 2},
            {"offset": 1024 * 1024 + 1, "column": 1024 * 1024 + 2},
        ):
            malformed.append((dataclasses.replace(eof, span=dataclasses.replace(eof.span, **changes)),))
        positioned = parser.lex("fn f(){\n return;\n}", "test.rl").tokens
        for index, changes in (
            (1, {"filename": "other.rl"}),
            (1, {"column": 3}),
            (1, {"line": 3, "column": 1}),
            (6, {"line": 1}),
            (6, {"column": 100}),
        ):
            token = positioned[index]
            changed = dataclasses.replace(token, span=dataclasses.replace(token.span, **changes))
            malformed.append(positioned[:index] + (changed,) + positioned[index + 1:])
        for tokens in malformed:
            with self.subTest(tokens=tokens):
                result = parser.parse_tokens(tokens)
                self.assertFalse(result.ok)
                self.assertIsNone(result.root)
                self.assertEqual("PAR_INVALID_INPUT", result.diagnostic.code)
                self.assertIsInstance(result.diagnostic.span.filename, str)
                self.assertEqual((1, 1, 0, 0), (
                    result.diagnostic.span.line, result.diagnostic.span.column,
                    result.diagnostic.span.offset, result.diagnostic.span.length))
        for source in (" \tfn f() {}", "\n\nfn f(){\r\n\treturn;\n}",
                       "// lead\nfn f() {} // end", " " * (1024 * 1024)):
            with self.subTest(valid_positions=source[:40]):
                self.assertTrue(parser.parse_tokens(parser.lex(source, "test.rl").tokens).ok)

        tokens = parser.lex('fn f(){ "abc"; }').tokens
        literal_index = next(i for i, token in enumerate(tokens) if token.kind == "STRING")
        literal = tokens[literal_index]
        for changes in (
            {"kind": "INTEGER", "lexeme": "99999", "value": None},
            {"kind": "TRUE", "lexeme": "false", "value": None},
            {"kind": "IDENTIFIER", "lexeme": "while", "value": None},
            {"lexeme": '"a\\q"'},
            {"value": None}, {"value": 1}, {"value": "wrong"},
            {"lexeme": None}, {"kind": []},
            {"span": dataclasses.replace(literal.span, length=0)},
        ):
            changed = dataclasses.replace(literal, **changes)
            candidate = tokens[:literal_index] + (changed,) + tokens[literal_index + 1:]
            with self.subTest(changes=changes):
                # The well-formed integer replacement is a positive control.
                if changes.get("kind") == "INTEGER":
                    self.assertTrue(parser.parse_tokens(candidate).ok)
                else:
                    self.assertEqual("PAR_INVALID_INPUT", parser.parse_tokens(candidate).diagnostic.code)
        for text in ("9223372036854775808", "1_000", "12abc", "-1", "１２"):
            span = dataclasses.replace(eof.span, length=len(text))
            integer = parser.Token("INTEGER", text, span)
            end = dataclasses.replace(eof, span=dataclasses.replace(
                eof.span, offset=len(text), column=len(text) + 1))
            with self.subTest(integer=text):
                self.assertEqual("PAR_INVALID_INPUT", parser.parse_tokens((integer, end)).diagnostic.code)

    def test_43_missing_eof_rejected(self):
        tokens = parser.lex("fn f() {}").tokens[:-1]
        self.assertEqual("PAR_INVALID_INPUT", parser.parse_tokens(tokens).diagnostic.code)

    def test_44_parse_bytes(self):
        self.assertTrue(parser.parse_bytes(b"fn f() {}", "bytes.rl").ok)

    def test_45_non_ascii_bytes_rejected(self):
        self.assertEqual("PAR_LEX_ERROR", parser.parse_bytes(b"\xff", "bad.rl").diagnostic.code)

    def test_46_file_too_large(self):
        result = parser.parse(" " * (1024 * 1024 + 1), "large.rl")
        self.assertEqual("PAR_FILE_TOO_LARGE", result.diagnostic.code)

    def test_47_cli_is_deterministic(self):
        path = GOOD / "all_constructs.rl"
        first_run = subprocess.run([sys.executable, str(PARSER_PATH), str(path), "--json"], capture_output=True, text=True, check=False, timeout=10)
        second_run = subprocess.run([sys.executable, "-O", str(PARSER_PATH), str(path), "--json"], capture_output=True, text=True, check=False, timeout=10)
        self.assertEqual((0, first_run.stdout, ""), (first_run.returncode, second_run.stdout, first_run.stderr))
        self.assertEqual("Program", json.loads(first_run.stdout)["kind"])

    def test_48_mutation_trailing_parameter_is_detected(self):
        old = 'while self.match("COMMA"):\n                    # MUTATION_POINT_PARAM_TRAILING_COMMA\n                    if self.at("RIGHT_PAREN"):'
        new = 'while self.match("COMMA") and not self.at("RIGHT_PAREN"):\n                    # MUTATION_POINT_PARAM_TRAILING_COMMA\n                    if self.at("RIGHT_PAREN"):'
        directory, mutant = mutated(old, new, "param_comma")
        try:
            self.assertTrue(mutant.parse("fn f(a: int,) {}").ok)
            self.assertFalse(parser.parse("fn f(a: int,) {}").ok)
        finally:
            directory.cleanup()

    def test_49_mutation_return_arrow_is_detected(self):
        directory, mutant = mutated('if self.match("COLON"):', 'if self.match("ARROW"):', "return_arrow")
        try:
            self.assertTrue(mutant.parse("fn f() -> int {}").ok)
            self.assertFalse(parser.parse("fn f() -> int {}").ok)
        finally:
            directory.cleanup()

    def test_50_mutation_depth_guard_is_detected(self):
        directory, mutant = mutated("if self.depth > PARSE_MAX_DEPTH:", "if False:", "depth")
        source = "fn f(){ " + "(" * 300 + "1" + ")" * 300 + "; }"
        try:
            self.assertTrue(mutant.parse(source).ok)
            self.assertEqual("PAR_DEPTH_EXCEEDED", parser.parse(source).diagnostic.code)
        finally:
            directory.cleanup()

    def test_51_mutation_precedence_is_detected(self):
        directory, mutant = mutated('"PLUS": (5, "AdditiveExpr"),', '"PLUS": (7, "AdditiveExpr"),', "precedence")
        try:
            result = mutant.parse("fn f(){ 1 + 2 * 3; }")
            mutated_expr = next(node for node in walk(result.root) if node.kind == "ExprStmt").children[0]
            self.assertEqual("MultiplicativeExpr", mutated_expr.kind)
            self.assertEqual("AdditiveExpr", expression("1 + 2 * 3").kind)
        finally:
            directory.cleanup()

    def test_52_mutation_trailing_program_token_is_detected(self):
        directory, mutant = mutated('if not self.at("EOF"):\n            self.fail("PAR_UNEXPECTED_TOKEN", "only function definitions are allowed at top level", ("FN", "EOF"))', 'if False:\n            self.fail("PAR_UNEXPECTED_TOKEN", "only function definitions are allowed at top level", ("FN", "EOF"))', "program_trailing")
        try:
            self.assertTrue(mutant.parse("fn f() {} 1").ok)
            self.assertFalse(parser.parse("fn f() {} 1").ok)
        finally:
            directory.cleanup()

    def test_53_nested_calls_obey_exact_depth_limit(self):
        def nested_calls(count):
            return "fn f(){ " + "g(" * count + "1" + ")" * count + "; }"

        # Function + block consume two levels, leaving 254 nested calls within
        # the frozen total limit of 256. The next call must fail deterministically.
        self.assertTrue(parser.parse(nested_calls(254)).ok)
        too_deep = parser.parse(nested_calls(255))
        self.assertFalse(too_deep.ok)
        self.assertEqual("PAR_DEPTH_EXCEEDED", too_deep.diagnostic.code)

    def test_54_cli_serializes_wide_flat_trees_without_traceback(self):
        # Left-iterative productions (chained calls, expression chains) stay
        # within the grammar depth budget but produce unbounded tree width.
        # The CLI must serialize them (or report PAR_DEPTH_EXCEEDED), never
        # leak a RecursionError traceback.
        with tempfile.TemporaryDirectory(prefix="parser-wide-") as directory:
            for label, source in (
                ("chained", "fn f(){ g" + "()" * 600 + "; }"),
                ("chain", "fn f(){ let x: int = " + " && ".join(["true"] * 600) + "; }"),
            ):
                path = Path(directory) / f"{label}.rl"
                path.write_text(source, encoding="utf-8")
                for optimized in (False, True):
                    argv = [sys.executable, *(["-O"] if optimized else []),
                            str(PARSER_PATH), str(path), "--json"]
                    run = subprocess.run(argv, capture_output=True, text=True,
                                          check=False, timeout=60)
                    self.assertEqual("", run.stderr, f"{label} o={optimized}: {run.stderr[:300]}")
                    self.assertEqual(0, run.returncode, f"{label} o={optimized}")
                    self.assertEqual("Program", json.loads(run.stdout)["kind"])


if __name__ == "__main__":
    unittest.main()
