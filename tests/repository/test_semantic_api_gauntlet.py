"""Public semantic API regressions: diagnostics, source bounds, and AST contract."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.rynorlang import analyze as semantic
from tools.rynorlang.lex import MAX_SOURCE_BYTES, lex


SOURCE = '''fn identity(value: int): int { return value; }
fn helper() { return; }
fn main(): int {
  let number: int = 0007;
  let text: str = "a\\n\\\"b";
  let flag: bool = !false;
  helper();
  if flag { { identity(number); } } else { while false { 1; } }
  return -number + identity(2);
}
'''

FIELDS = {
    "Program": {"functions"},
    "Function": {"name", "params", "ret_type", "body", "symbol"},
    "Param": {"name", "type", "symbol"},
    "Block": {"stmts"},
    "Let": {"name", "type", "init", "symbol"},
    "If": {"cond", "then", "else"},
    "While": {"cond", "body"},
    "Return": {"value"},
    "ExprStmt": {"expr"},
    "BinOp": {"op", "left", "right", "type"},
    "UnOp": {"op", "operand", "type"},
    "IntLit": {"value", "type"},
    "BoolLit": {"value", "type"},
    "StrLit": {"value", "lexeme", "type"},
    "Var": {"name", "symbol", "type"},
    "Call": {"callee", "args", "symbol", "type"},
}


class SemanticApiGauntletTests(unittest.TestCase):
    def assert_invalid(self, result):
        self.assertFalse(result.ok)
        self.assertIsNone(result.ast)
        self.assertEqual(result.diagnostic.code, "PAR_INVALID_INPUT")
        span = result.diagnostic.span
        self.assertEqual((span.line, span.column, span.offset, span.length), (1, 1, 0, 0))

    def test_malformed_source_and_filename_are_bounded(self):
        tokens = lex("fn main() {}").tokens
        for invalid in (42, False, [], {}, b"fn main() {}"):
            with self.subTest(value=invalid):
                self.assert_invalid(semantic.analyze_tokens(tokens, source=invalid))
                self.assert_invalid(semantic.analyze_tokens(tokens, filename=invalid))
                self.assert_invalid(semantic.analyze("fn main() {}", filename=invalid))
                self.assert_invalid(semantic.analyze_bytes(b"fn main() {}", filename=invalid))

    def test_source_must_match_supplied_tokens(self):
        tokens = lex("fn main() {}", "match.rl").tokens
        # Lexically clean but different sources are token mismatches.
        for source in ("", "\nfn main() {}", "fn other() {}"):
            with self.subTest(source=source):
                self.assert_invalid(semantic.analyze_tokens(tokens, "match.rl", source=source))
        # A source that is itself lexically broken reports its own error
        # (mapped like the sibling entry points), not a generic mismatch.
        broken = semantic.analyze_tokens(tokens, "match.rl", source="fn main() { @ }")
        self.assertFalse(broken.ok)
        self.assertIsNone(broken.ast)
        self.assertEqual(broken.diagnostic.code, "PAR_LEX_ERROR")
        self.assertEqual(broken.diagnostic.got_kind, "LEX_INVALID_CHAR")

    def test_full_diagnostic_equivalence_for_lexical_and_parser_failures(self):
        # Latin-1 deliberately preserves one character per byte for the
        # non-ASCII probe, so all three APIs receive identical coordinates.
        probes = ("// header\n  @", 'fn main() {\n  "unfinished', "// header\n  \xe9",
                  "fn main() {\n  return", "fn main() {\n  missing(); }")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failure.rl"
            for source in probes:
                with self.subTest(source=source):
                    data = source.encode("latin-1")
                    path.write_bytes(data)
                    text_result = semantic.analyze(source, str(path))
                    self.assertFalse(text_result.ok)
                    self.assertIsNone(text_result.ast)
                    self.assertEqual(text_result, semantic.analyze_bytes(data, str(path)))
                    self.assertEqual(text_result, semantic.analyze_file(path))
                    if text_result.diagnostic.code == "PAR_LEX_ERROR":
                        self.assertTrue(text_result.diagnostic.got_kind.startswith("LEX_"))
                    self.assertEqual(text_result.diagnostic.span.line, 2)

    def test_one_mib_limit_accepts_exact_size(self):
        tail = "\nfn main() {}"
        source = " " * (MAX_SOURCE_BYTES - len(tail)) + tail
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limit.rl"
            data = source.encode("ascii")
            path.write_bytes(data)
            result = semantic.analyze(source, str(path))
            self.assertTrue(result.ok, result.diagnostic)
            self.assertEqual(result, semantic.analyze_bytes(data, str(path)))
            self.assertEqual(result, semantic.analyze_file(path))
            function = result.ast["functions"][0]
            self.assertEqual(function["span"]["start"],
                             {"line": 2, "column": 1, "offset": MAX_SOURCE_BYTES - 12})

    def test_one_mib_overflow_preserves_full_diagnostic_and_position(self):
        header = "// header\n"
        source = header + " " * (MAX_SOURCE_BYTES - len(header) - 2) + "\n @"
        self.assertEqual(len(source), MAX_SOURCE_BYTES + 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversize.rl"
            data = source.encode("ascii")
            path.write_bytes(data)
            result = semantic.analyze(source, str(path))
            self.assertFalse(result.ok)
            self.assertIsNone(result.ast)
            self.assertEqual(result.diagnostic.code, "PAR_FILE_TOO_LARGE")
            self.assertEqual(result.diagnostic.got_kind, "LEX_FILE_TOO_LARGE")
            span = result.diagnostic.span
            self.assertEqual((span.line, span.column, span.offset, span.length),
                             (3, 2, MAX_SOURCE_BYTES, 1))
            self.assertEqual(result, semantic.analyze_bytes(data, str(path)))
            self.assertEqual(result, semantic.analyze_file(path))

    def test_all_ast_kinds_have_exact_schema_and_source_positions(self):
        result = semantic.analyze(SOURCE, "schema.rl")
        self.assertTrue(result.ok, result.diagnostic)
        pending = [result.ast]
        seen = set()
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict) and "kind" in value:
                kind = value["kind"]
                seen.add(kind)
                self.assertIn(kind, FIELDS)
                self.assertEqual(set(value), FIELDS[kind] | {"kind", "span"})
                span = value["span"]
                self.assertEqual(set(span), {"filename", "line", "column", "offset", "length", "start", "end"})
                self.assertEqual(span["filename"], "schema.rl")
                for key, offset in (("start", span["offset"]), ("end", span["offset"] + span["length"])):
                    self.assertGreaterEqual(offset, 0)
                    self.assertLessEqual(offset, len(SOURCE))
                    prefix = SOURCE[:offset]
                    self.assertEqual(span[key], {"offset": offset, "line": prefix.count("\n") + 1,
                                                 "column": offset - prefix.rfind("\n")})
                self.assertEqual((span["line"], span["column"]),
                                 (span["start"]["line"], span["start"]["column"]))
                pending.extend(value[field] for field in FIELDS[kind])
        self.assertEqual(seen, set(FIELDS))
        statements = result.ast["functions"][2]["body"]["stmts"]
        self.assertEqual(statements[0]["init"]["value"], "0007")
        self.assertEqual(statements[1]["init"]["value"], 'a\n"b')

    def test_success_ast_equivalence_across_all_public_entrypoints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema.rl"
            data = SOURCE.encode("ascii")
            path.write_bytes(data)
            result = semantic.analyze(SOURCE, str(path))
            self.assertTrue(result.ok, result.diagnostic)
            self.assertEqual(result, semantic.analyze_bytes(data, str(path)))
            self.assertEqual(result, semantic.analyze_file(path))
            tokens = lex(SOURCE, str(path)).tokens
            self.assertEqual(result, semantic.analyze_tokens(tokens, str(path)))
            self.assertEqual(result, semantic.analyze_tokens(tokens, str(path), source=SOURCE))

    def test_iterative_json_is_byte_identical_to_frozen_json_format(self):
        # Probes use only the four frozen string escapes (\\, \", \n, \t):
        # \r is lexically invalid and must stay rejected (see lexer design).
        for source in ("", SOURCE, 'fn f(): str { return "\\t\\n\\\\\\""; }'):
            with self.subTest(source=source):
                result = semantic.analyze(source, "json.rl")
                self.assertTrue(result.ok, result.diagnostic)
                expected = json.dumps(result.ast, sort_keys=True, separators=(",", ":"))
                self.assertEqual("".join(semantic.iter_ast_json(result.ast)), expected)


if __name__ == "__main__":
    unittest.main()
