"""Strict Stage 12 conformance tests for the host-side RynorLang lexer."""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
LEXER_PATH = ROOT / "tools" / "rynorlang" / "lex.py"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "lexer" / "good"
BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "lexer" / "bad"
_spec = importlib.util.spec_from_file_location("rynorlang_stage12_lexer", LEXER_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load lexer module: {LEXER_PATH}")
lexer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lexer
_spec.loader.exec_module(lexer)

KEYWORDS = {
    "fn": "FN",
    "let": "LET",
    "if": "IF",
    "else": "ELSE",
    "while": "WHILE",
    "return": "RETURN",
    "true": "TRUE",
    "false": "FALSE",
    "int": "INT_TYPE",
    "bool": "BOOL_TYPE",
    "str": "STR_TYPE",
}

GOOD_NAMES = {
    "all_tokens.rl", "comments.rl", "escapes.rl", "hello.rl",
    "identifiers.rl", "integers_boundary.rl", "integers.rl", "keywords.rl",
    "maximal_munch.rl", "multiline.rl", "operators_double.rl",
    "operators_single.rl", "span_accuracy.rl", "string_comment_interaction.rl",
    "strings.rl", "whitespace.rl",
}

BAD_NAMES = {
    "int_overflow.rl", "int_overflow_large.rl", "invalid_char_at.rl",
    "invalid_char_backtick.rl", "invalid_char_dollar.rl", "invalid_char_dot.rl",
    "invalid_char_hash.rl", "invalid_escape_0.rl", "invalid_escape_a.rl",
    "invalid_escape_q.rl", "invalid_escape_u.rl", "invalid_escape_x.rl",
    "invalid_utf8.rl", "non_ascii_byte.rl", "non_ascii_emoji.rl",
    "non_ascii_latin.rl", "unescaped_newline.rl",
    "unterminated_string_eof.rl", "unterminated_string_eof2.rl",
}


def kinds(source: str) -> list[str]:
    result = lexer.lex(source, "test.rl")
    if result.diagnostic is not None:
        raise AssertionError(result.diagnostic)
    return [token.kind for token in result.tokens]


def run_cli(path: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LEXER_PATH), *arguments, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


class LexerLayoutTests(unittest.TestCase):
    def test_01_mandated_tool_exists(self):
        self.assertTrue(LEXER_PATH.is_file())

    def test_02_no_duplicate_language_tree_implementation(self):
        self.assertFalse((ROOT / "rynorlang" / "lexer" / "lex.py").exists())

    def test_03_fixture_inventory_is_exact(self):
        self.assertEqual({path.name for path in GOOD.iterdir() if path.is_file()}, GOOD_NAMES)
        self.assertEqual({path.name for path in BAD.iterdir() if path.is_file()}, BAD_NAMES)

    def test_04_lexer_uses_only_standard_library_modules(self):
        tree = ast.parse(LEXER_PATH.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertLessEqual(imports, {"__future__", "argparse", "json", "sys", "dataclasses", "pathlib", "typing"})

    def test_05_no_compatibility_or_hidden_test_hedging(self):
        source = LEXER_PATH.read_text(encoding="utf-8")
        for forbidden in ("hidden-test", "KindStr", "LexCodeStr", "FlexInt", "inspect.currentframe", "CallableLexModule"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class LexerTokenTests(unittest.TestCase):
    def test_06_empty_source_is_only_eof(self):
        result = lexer.lex("")
        self.assertEqual(result.diagnostic, None)
        self.assertEqual([(token.kind, token.lexeme) for token in result.tokens], [("EOF", "")])

    def test_07_exact_keyword_set(self):
        source = " ".join(KEYWORDS)
        result = lexer.lex(source)
        self.assertTrue(result.ok)
        self.assertEqual([token.kind for token in result.tokens[:-1]], list(KEYWORDS.values()))

    def test_08_non_keywords_remain_identifiers(self):
        words = "give when otherwise and or not i64 unit use print"
        self.assertEqual(kinds(words), ["IDENTIFIER"] * 10 + ["EOF"])

    def test_09_identifier_grammar(self):
        self.assertEqual(kinds("_ a Z _0 name_2"), ["IDENTIFIER"] * 5 + ["EOF"])

    def test_10_keyword_prefixes_are_identifiers(self):
        self.assertEqual(kinds("fnx letter if2 returning"), ["IDENTIFIER"] * 4 + ["EOF"])

    def test_11_decimal_integers(self):
        self.assertEqual(kinds("0 7 42 123456789"), ["INTEGER"] * 4 + ["EOF"])

    def test_12_signed_maximum_integer(self):
        result = lexer.lex("9223372036854775807")
        self.assertTrue(result.ok)
        self.assertEqual(result.tokens[0].lexeme, "9223372036854775807")

    def test_13_leading_zeroes_do_not_overflow(self):
        result = lexer.lex("00009223372036854775807")
        self.assertTrue(result.ok)
        self.assertEqual(result.tokens[0].kind, "INTEGER")

    def test_14_line_comments_are_discarded(self):
        self.assertEqual(kinds("let // ignored == @\nname"), ["LET", "IDENTIFIER", "EOF"])

    def test_15_comment_marker_inside_string_is_text(self):
        result = lexer.lex('"// text"')
        self.assertTrue(result.ok)
        self.assertEqual(result.tokens[0].value, "// text")

    def test_16_strings_keep_raw_lexeme(self):
        result = lexer.lex('"hello"')
        self.assertEqual((result.tokens[0].kind, result.tokens[0].lexeme, result.tokens[0].value), ("STRING", '"hello"', "hello"))

    def test_17_only_frozen_escapes_decode(self):
        result = lexer.lex('"\\\\\\\"\\n\\t"')
        self.assertTrue(result.ok)
        self.assertEqual(result.tokens[0].value, '\\"\n\t')

    def test_18_single_character_tokens(self):
        source = "+ - * / % ! = < > ( ) { } ; , :"
        self.assertEqual(lexer.SINGLE_TOKENS["!"], "BANG")
        expected = list(lexer.SINGLE_TOKENS.values()) + ["EOF"]
        self.assertEqual(kinds(source), expected)

    def test_19_double_character_tokens(self):
        source = "== != <= >= && || ->"
        self.assertEqual(kinds(source), list(lexer.DOUBLE_TOKENS.values()) + ["EOF"])

    def test_20_equals_maximal_munch(self):
        self.assertEqual(kinds("a===b"), ["IDENTIFIER", "EQ_EQ", "EQUAL", "IDENTIFIER", "EOF"])

    def test_21_other_operators_use_maximal_munch(self):
        self.assertEqual(kinds("a-->b<=c"), ["IDENTIFIER", "MINUS", "ARROW", "IDENTIFIER", "LESS_EQ", "IDENTIFIER", "EOF"])

    def test_22_initial_span_is_one_based_with_byte_offset(self):
        token = lexer.lex("fn").tokens[0]
        self.assertEqual(token.span, lexer.Span("<input>", 1, 1, 0, 2))

    def test_23_newline_resets_column(self):
        token = lexer.lex("fn\n    let").tokens[1]
        self.assertEqual((token.span.line, token.span.column, token.span.offset), (2, 5, 7))

    def test_24_comments_advance_source_position(self):
        token = lexer.lex("//abc\nlet").tokens[0]
        self.assertEqual((token.span.line, token.span.column, token.span.offset), (2, 1, 6))

    def test_25_eof_span_is_post_input(self):
        eof = lexer.lex("x\n").tokens[-1]
        self.assertEqual((eof.kind, eof.span.line, eof.span.column, eof.span.offset, eof.span.length), ("EOF", 2, 1, 2, 0))


class LexerErrorTests(unittest.TestCase):
    def assert_error(self, source: str | bytes, code: str):
        result = lexer.lex_bytes(source) if isinstance(source, bytes) else lexer.lex(source)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, code)
        return result

    def test_26_invalid_character(self):
        result = self.assert_error("@", "LEX_INVALID_CHAR")
        self.assertEqual(result.diagnostic.span, lexer.Span("<input>", 1, 1, 0, 1))

    def test_27_non_ascii_identifier_is_rejected(self):
        self.assert_error("café", "LEX_INVALID_CHAR")
        comment = self.assert_error("// café", "LEX_INVALID_CHAR")
        self.assertEqual(comment.diagnostic.span.offset, 6)

    def test_28_non_ascii_string_is_rejected(self):
        result = self.assert_error('"é"', "LEX_INVALID_CHAR")
        self.assertEqual(result.diagnostic.span.offset, 1)

    def test_29_non_ascii_raw_byte_is_rejected(self):
        result = self.assert_error(b"x\xff", "LEX_INVALID_CHAR")
        self.assertEqual(result.diagnostic.span.offset, 1)

    def test_30_first_overflow_value_is_rejected(self):
        self.assert_error("9223372036854775808", "LEX_INT_OVERFLOW")

    def test_31_large_integer_is_rejected(self):
        self.assert_error("9" * 1000, "LEX_INT_OVERFLOW")

    def test_32_unterminated_string_at_eof(self):
        self.assert_error('"abc', "LEX_UNTERMINATED_STRING")

    def test_33_trailing_escape_is_unterminated_string(self):
        self.assert_error('"abc\\', "LEX_UNTERMINATED_STRING")

    def test_34_unescaped_line_feed_terminates_string(self):
        self.assert_error('"abc\ndef"', "LEX_UNTERMINATED_STRING")

    def test_35_unescaped_carriage_return_terminates_string(self):
        self.assert_error('"abc\rdef"', "LEX_UNTERMINATED_STRING")

    def test_36_unknown_escape_is_rejected(self):
        result = self.assert_error('"a\\q"', "LEX_INVALID_ESCAPE")
        self.assertEqual((result.diagnostic.span.offset, result.diagnostic.span.length), (2, 2))

    def test_37_carriage_return_escape_is_rejected(self):
        self.assert_error('"a\\r"', "LEX_INVALID_ESCAPE")

    def test_38_lexing_stops_at_first_diagnostic(self):
        result = self.assert_error("let x = 1; @ # $", "LEX_INVALID_CHAR")
        self.assertEqual(result.diagnostic.span.offset, 11)
        self.assertFalse(any(token.lexeme in {"#", "$"} for token in result.tokens))
        ordered = self.assert_error(b"@\xff", "LEX_INVALID_CHAR")
        self.assertEqual(ordered.diagnostic.span.offset, 0)

    def test_39_exactly_one_mibibyte_is_accepted(self):
        result = lexer.lex_bytes(b"//" + b"a" * (lexer.MAX_SOURCE_BYTES - 2))
        self.assertTrue(result.ok)
        self.assertEqual([token.kind for token in result.tokens], ["EOF"])

    def test_40_more_than_one_mibibyte_is_rejected_before_scanning(self):
        result = self.assert_error(b"@" + b"a" * lexer.MAX_SOURCE_BYTES, "LEX_FILE_TOO_LARGE")
        self.assertEqual(result.tokens, ())
        with tempfile.NamedTemporaryFile(suffix=".rl", delete=False) as source_file:
            source_file.write(b"a" * (lexer.MAX_SOURCE_BYTES + 4096))
            source_path = pathlib.Path(source_file.name)
        try:
            file_result = lexer.lex_file(source_path)
            self.assertEqual(file_result.diagnostic.code, "LEX_FILE_TOO_LARGE")
            self.assertEqual(file_result.diagnostic.span.offset, lexer.MAX_SOURCE_BYTES)
        finally:
            source_path.unlink()


class LexerApiAndCliTests(unittest.TestCase):
    def test_41_public_result_has_fixed_types(self):
        result = lexer.lex("let x = 1")
        self.assertIsInstance(result, lexer.LexResult)
        self.assertIsInstance(result.tokens, tuple)
        self.assertIsNone(result.diagnostic)

    def test_42_diagnostic_codes_do_not_alias(self):
        invalid = lexer.lex("@").diagnostic
        overflow = lexer.lex("9223372036854775808").diagnostic
        self.assertNotEqual(invalid.code, overflow.code)
        self.assertNotEqual(invalid, overflow)

    def test_43_invalid_argument_types_fail_without_exception(self):
        self.assertEqual(lexer.lex(None).diagnostic.code, "LEX_INVALID_INPUT")
        self.assertEqual(lexer.lex_bytes("text").diagnostic.code, "LEX_INVALID_INPUT")

    def test_44_library_output_is_deterministic(self):
        self.assertEqual(lexer.lex("fn x() {}"), lexer.lex("fn x() {}"))

    def test_45_cli_success_is_exact_json(self):
        process = run_cli(GOOD / "span_accuracy.rl", "--json")
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["tokens"][0]["kind"], "FN")
        self.assertEqual(process.stderr, "")

    def test_46_cli_error_is_diagnostic_only_on_stderr(self):
        process = run_cli(BAD / "invalid_char_at.rl", "--json")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout, "")
        self.assertEqual(json.loads(process.stderr)["diagnostic"]["code"], "LEX_INVALID_CHAR")

    def test_47_cli_missing_file_is_distinct_io_failure(self):
        process = run_cli(ROOT / "missing-stage12-input.rl")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, "")

    def test_48_cli_output_is_byte_deterministic(self):
        first = run_cli(GOOD / "all_tokens.rl", "--json")
        second = run_cli(GOOD / "all_tokens.rl", "--json")
        self.assertEqual((first.returncode, first.stdout, first.stderr), (second.returncode, second.stdout, second.stderr))

    def test_49_all_fixtures_have_the_expected_outcome(self):
        for path in sorted(GOOD.iterdir()):
            with self.subTest(good=path.name):
                self.assertTrue(lexer.lex_file(path).ok)
        for path in sorted(BAD.iterdir()):
            with self.subTest(bad=path.name):
                self.assertFalse(lexer.lex_file(path).ok)


if __name__ == "__main__":
    unittest.main()
