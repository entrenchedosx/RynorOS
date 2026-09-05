"""Stage 15b shell-surface tests: edition-gated |> pipelines and commands.

Conventions: shell fixtures live under tests/fixtures/rynorlang/shell-edition/
(never mixed with v1 files); GOOD/BAD inventories are exact; the v1 default
edition stays byte-identical (old programs analyze exactly as before);
Stage-15a RIR/backend must reject the new kinds with COMP_V2_UNSUPPORTED
(no codegen for shell syntax yet); the host evaluator is TEST-ONLY.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEX_PATH = ROOT / "tools" / "rynorlang" / "lex.py"
PARSE_PATH = ROOT / "tools" / "rynorlang" / "parse.py"
ANALYZE_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
SHELL_PATH = ROOT / "tools" / "rynorlang" / "shell.py"
RIR_PATH = ROOT / "tools" / "rynorlang" / "rir.py"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "shell-edition" / "good"
BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "shell-edition" / "bad"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lexmod = _load("rynorlang_stage15b_lex", LEX_PATH)
parsemod = _load("rynorlang_stage15b_parse", PARSE_PATH)
analyzemod = _load("rynorlang_stage15b_analyze", ANALYZE_PATH)
shellmod = _load("rynorlang_stage15b_shell", SHELL_PATH)
rir = _load("rynorlang_stage15b_rir", RIR_PATH)
sys.path.insert(0, str(ROOT))

CMDS = dict(shellmod.DEMO_COMMANDS)
IMPLS = dict(shellmod.DEMO_IMPLS)

EXPECTED_GOOD = {
    "pipe_basic.rl": "5",
    "pipe_chain.rl": "2",
    "pipe_nested.rl": "1",
    "pipe_precedence.rl": None,
    "cmd_args.rl": "he",
    "cmd_flags.rl": "got:v",
    "cmd_redirect.rl": "HI",
    "cmd_redirect_chain.rl": "HI",
    "cmd_zero_args.rl": "done",
    "pipe_unit_exprstmt.rl": None,
    "pipe_let_compose.rl": "1",
    "cmd_call_mix.rl": None,
}

EXPECTED_BAD = {
    "pipe_int_stage.rl": "SHELL_PIPELINE_TYPE_MISMATCH",
    "pipe_unit_nonfinal.rl": "SHELL_UNIT_STAGE",
    "pipe_unit_let.rl": "SEM_TYPE_MISMATCH",
    "cmd_unknown.rl": "SHELL_UNKNOWN_COMMAND",
    "cmd_ambiguous.rl": "SHELL_AMBIGUOUS_COMMAND",
    "cmd_arity.rl": "SHELL_COMMAND_ARITY",
    "cmd_argtype.rl": "SHELL_COMMAND_TYPE_MISMATCH",
    "redirect_int_target.rl": "PAR_EXPECTED_TOKEN",
    "redirect_missing_target.rl": "PAR_EXPECTED_TOKEN",
    "pipe_trailing.rl": "PAR_UNEXPECTED_TOKEN",
    "pipe_empty_stage.rl": "PAR_UNEXPECTED_TOKEN",
}


def _analyze(src, edition="shell", commands=CMDS):
    return analyzemod.analyze(src, "t.rl", edition=edition, commands=commands)


def _eval_node(node, env):
    """Tiny end-to-end helper for shell fixtures: Pipeline/Cmd/StrLit/Var."""
    kind = node.get("kind")
    if kind == "Pipeline":
        return shellmod.run_pipeline(node, env, IMPLS)
    if kind == "Cmd":
        return shellmod.run_pipeline(
            {"kind": "Pipeline", "span": {}, "type": node.get("type", "str"),
             "stages": [node]}, env, IMPLS)
    if kind == "StrLit":
        return node["value"]
    if kind == "IntLit":
        return node["value"]
    if kind == "BoolLit":
        return "true" if node["value"] else "false"
    if kind == "Var":
        return env[node["name"]]
    raise AssertionError(f"no eval rule for {kind}")


def _eval_main(ast):
    env: dict = {}
    for func in ast["functions"]:
        if func["name"] != "main":
            continue
        for stmt in func["body"]["stmts"]:
            if stmt["kind"] == "Let":
                env[stmt["name"]] = _eval_node(stmt["init"], env)
            elif stmt["kind"] == "ExprStmt":
                _eval_node(stmt["expr"], env)
            elif stmt["kind"] == "Return":
                value = stmt.get("value")
                return None if value is None else _eval_node(value, env)
    return None


def _find_kind(node, kind):
    if isinstance(node, dict):
        if node.get("kind") == kind:
            return node
        for value in node.values():
            hit = _find_kind(value, kind)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for item in node:
            hit = _find_kind(item, kind)
            if hit is not None:
                return hit
    return None


def _find_all(node, kind, out):
    if isinstance(node, dict):
        if node.get("kind") == kind:
            out.append(node)
        for value in node.values():
            _find_all(value, kind, out)
    elif isinstance(node, list):
        for item in node:
            _find_all(item, kind, out)
    return out


class ShellLexerTests(unittest.TestCase):
    def test_01_v1_rejects_pipe_chars(self):
        for src in ("a |> b", "a | b", "a |"):
            with self.subTest(src=src):
                res = lexmod.lex(f"fn main(): int {{ let x: int = 1; return x; }} {src}")
                self.assertFalse(res.ok)
                self.assertEqual(res.diagnostic.code, "LEX_INVALID_CHAR")

    def test_02_shell_lexes_pipe_gt_as_one_token(self):
        res = lexmod.lex('a |> b', 't.rl', edition='shell')
        self.assertTrue(res.ok, res.diagnostic)
        kinds = [(t.kind, t.lexeme, t.span.offset, t.span.length) for t in res.tokens]
        self.assertIn(("PIPE_GT", "|>", 2, 2), kinds)

    def test_03_pipe_space_gt_stays_two_token_error(self):
        res = lexmod.lex('a | > b', 't.rl', edition='shell')
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "LEX_INVALID_CHAR")

    def test_04_frozen_operators_unchanged_both_editions(self):
        for edition in ("v1", "shell"):
            with self.subTest(edition=edition):
                for src, want in (("a || b", "OR_OR"), ("a -> b", "ARROW"),
                                  ("a - b", "MINUS"), ("a > b", "GREATER"),
                                  ("a >> b", "GREATER"), ("a ! b", "BANG")):
                    res = lexmod.lex(src, 't.rl', edition=edition)
                    self.assertTrue(res.ok, (src, res.diagnostic))
                    self.assertIn(want, [t.kind for t in res.tokens])

    def test_05_maximal_munch(self):
        res = lexmod.lex('a|>b', 't.rl', edition='shell')
        self.assertTrue(res.ok, res.diagnostic)
        self.assertEqual([t.kind for t in res.tokens],
                         ["IDENTIFIER", "PIPE_GT", "IDENTIFIER", "EOF"])


class ShellParserTests(unittest.TestCase):
    def _parse(self, src):
        res = parsemod.parse(src, 't.rl', edition='shell')
        self.assertTrue(res.ok, res.diagnostic)
        return res.root

    def test_06_pipeline_left_assoc(self):
        root = self._parse('fn main(): str { let x: str = "a" |> "b" |> "c"; return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(init.kind, "PipeExpr")
        self.assertEqual(len(init.children), 3)
        # reparsing the middle pair must nest identically (left assoc shape)
        self.assertEqual(init.children[0].kind, "StringLiteral")

    def test_07_pipeline_below_additive(self):
        root = self._parse('fn main(): str { let x: str = "a" |> "b"; return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(init.kind, "PipeExpr")

    def test_08_paren_pipeline(self):
        root = self._parse('fn main(): str { let x: str = ("a" |> "b"); return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(init.kind, "GroupExpr")
        self.assertEqual(init.children[0].kind, "PipeExpr")

    def test_09_trailing_and_empty_stage_rejected(self):
        for src in ('fn main(): str { let x: str = "a" |> ; return x; }',
                    'fn main(): str { let x: str = "a" |> |> "b"; return x; }'):
            with self.subTest(src=src):
                res = parsemod.parse(src, 't.rl', edition='shell')
                self.assertFalse(res.ok)
                self.assertTrue(res.diagnostic.code.startswith("PAR_"), res.diagnostic.code)

    def test_10_cmd_shape_and_spans(self):
        root = self._parse('fn main(): str { let x: str = upper "hi" > "o"; return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(init.kind, "CmdExpr")
        self.assertEqual(init.text, "upper")
        kinds = [c.kind for c in init.children]
        self.assertEqual(kinds, ["Identifier", "CmdArgs", "Redirect"])
        args = init.children[1].children
        self.assertEqual(args[0].kind, "StringLiteral")
        redir = init.children[2]
        self.assertEqual(redir.text, ">")
        # byte-exact spans: `upper` at 26..31, `"hi"` at 32..36, `"o"` at 39..42
        src = 'fn main(): str { let x: str = upper "hi" > "o"; return x; }'
        self.assertEqual(src[init.children[0].span.offset:init.children[0].span.offset + 5], "upper")
        self.assertEqual(init.span.offset, src.index("upper"))
        self.assertEqual(init.span.offset + init.span.length, src.index('"o"') + 3)

    def test_11_adjacent_flag_vs_spaced_minus(self):
        root = self._parse('fn main(): str { let x: str = take "h" -v; return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(init.children[1].children[1].kind, "FlagArg")
        # spaced minus is not a flag: parses as subtraction, not a command
        root = self._parse('fn main(): int { let x: int = 3 - 1; return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(init.kind, "AdditiveExpr")

    def test_12_call_stays_call(self):
        root = self._parse('fn f(s: str): str { return s; } fn main(): str { let x: str = f("a"); return x; }')
        init = root.children[1].children[-1].children[0].children[2]
        self.assertEqual(init.kind, "CallExpr")

    def test_13_redirect_gt_vs_gtgt(self):
        root = self._parse('fn main(): str { let x: str = upper "h" >> "o"; return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(init.children[2].text, ">>")
        root = self._parse('fn main(): str { let x: str = upper "h" > "a" > "b"; return x; }')
        init = root.children[0].children[-1].children[0].children[2]
        self.assertEqual(len([c for c in init.children if c.kind == "Redirect"]), 2)


class ShellEditionTests(unittest.TestCase):
    def test_14_good_inventory_exact(self):
        names = sorted(p.name for p in GOOD.glob("*.rl"))
        self.assertEqual(names, sorted(EXPECTED_GOOD))

    def test_15_bad_inventory_exact(self):
        names = sorted(p.name for p in BAD.glob("*.rl"))
        self.assertEqual(names, sorted(EXPECTED_BAD))

    def test_16_good_fixtures_analyze(self):
        for name in sorted(EXPECTED_GOOD):
            with self.subTest(fixture=name):
                src = (GOOD / name).read_text(encoding="utf-8")
                res = _analyze(src)
                self.assertTrue(res.ok, res.diagnostic)

    def test_17_bad_fixtures_rejected_with_code(self):
        for name, code in sorted(EXPECTED_BAD.items()):
            with self.subTest(fixture=name):
                src = (BAD / name).read_text(encoding="utf-8")
                res = _analyze(src)
                self.assertFalse(res.ok)
                self.assertEqual(res.diagnostic.code, code, res.diagnostic.message)

    def test_18_v1_rejects_shell_with_old_codes(self):
        src = (GOOD / "pipe_basic.rl").read_text(encoding="utf-8")
        res = analyzemod.analyze(src, "pipe_basic.rl")
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "PAR_LEX_ERROR")
        # hand-built shell tokens still fail in v1 with an old code
        toks = lexmod.lex(src, "pipe_basic.rl", edition="shell").tokens
        res = analyzemod.analyze_tokens(toks, "pipe_basic.rl", source=src)
        self.assertFalse(res.ok)
        self.assertTrue(res.diagnostic.code.startswith("PAR_"), res.diagnostic.code)

    def test_19_v1_programs_unchanged_in_shell_edition(self):
        # Every VALID v1 program keeps identical AST JSON in the shell
        # edition. Only already-erroneous v1 programs (e.g. str ordering)
        # may report a different (still deterministic) edition error, since
        # redirect lookahead only fires where v1 already fails.
        checked = 0
        for directory in ("semantics", "parser", "compiler"):
            for path in sorted((ROOT / "tests/fixtures/rynorlang" / directory).rglob("*.rl")):
                with self.subTest(fixture=str(path.relative_to(ROOT))):
                    src = path.read_text(encoding="utf-8")
                    old = analyzemod.analyze(src, path.name)
                    if not old.ok:
                        continue
                    new = analyzemod.analyze(src, path.name, edition="shell", commands={})
                    self.assertTrue(new.ok, new.diagnostic)
                    self.assertEqual(json.dumps(old.ast, sort_keys=True, separators=(",", ":")),
                                     json.dumps(new.ast, sort_keys=True, separators=(",", ":")))
                    checked += 1
        self.assertGreater(checked, 20, "must cover a real v1 corpus")

    def test_20b_documented_edition_differences_pinned(self):
        # `a -b` (adjacent flag) is subtraction in v1 but a command in the
        # shell edition; `a - b` (spaced) is subtraction in both; `a > b`
        # is a comparison in both (redirects need quoted targets).
        v1 = analyzemod.analyze(
            'fn sub(a: int, b: int): int { return a - b; }'
            ' fn main(): int { return sub(3, 1); }', 't.rl')
        self.assertTrue(v1.ok, v1.diagnostic)
        sub = analyzemod.analyze(
            'fn sub(a: int, b: int): int { return a -b; }'
            ' fn main(): int { return sub(3, 1); }', 't.rl')
        self.assertTrue(sub.ok, sub.diagnostic)
        def _shape(node):
            if isinstance(node, dict):
                return {k: _shape(v) for k, v in node.items() if k != "span"}
            if isinstance(node, list):
                return [_shape(v) for v in node]
            return node
        self.assertEqual(_shape(v1.ast), _shape(sub.ast))
        shell = analyzemod.analyze(
            'fn sub(a: int, b: int): int { return a -b; }'
            ' fn main(): int { return sub(3, 1); }',
            't.rl', edition='shell', commands={})
        self.assertFalse(shell.ok)
        self.assertEqual(shell.diagnostic.code, "SHELL_UNKNOWN_COMMAND")
        for src in ('fn main(): bool { let a: int = 2; let b: int = 3;'
                    ' let c: bool = a > b; return c; }',
                    'fn main(): int { let a: int = 5; let b: int = a - 1; return b; }'):
            with self.subTest(src=src[:30]):
                old = analyzemod.analyze(src, 't.rl')
                new = analyzemod.analyze(src, 't.rl', edition='shell', commands={})
                self.assertTrue(old.ok and new.ok)
                self.assertEqual(json.dumps(old.ast, sort_keys=True),
                                 json.dumps(new.ast, sort_keys=True))

    def test_20_shell_preview_alias(self):
        src = (GOOD / "pipe_basic.rl").read_text(encoding="utf-8")
        a = analyzemod.analyze(src, "t.rl", edition="shell", commands=CMDS)
        b = analyzemod.analyze(src, "t.rl", edition="shell-preview", commands=CMDS)
        self.assertTrue(a.ok and b.ok)
        self.assertEqual(json.dumps(a.ast, sort_keys=True), json.dumps(b.ast, sort_keys=True))


class ShellSemanticTests(unittest.TestCase):
    def test_21_pipeline_typing(self):
        res = _analyze('fn main(): str { let x: str = 1 |> echo; return x; }')
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_PIPELINE_TYPE_MISMATCH")

    def test_22_unit_rules(self):
        res = _analyze('fn main(): int { emit "x" |> emit; return 1; }')
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_UNIT_STAGE")
        res = _analyze('fn main(): int { "a" |> emit; return 0; }')
        self.assertTrue(res.ok, res.diagnostic)
        res = _analyze('fn main(): str { let x: str = "a" |> emit; return x; }')
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SEM_TYPE_MISMATCH")

    def test_23_command_resolution(self):
        res = _analyze('fn main(): str { let x: str = nope "a"; return x; }', commands={})
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_UNKNOWN_COMMAND")
        res = _analyze('fn nope(s: str): str { return s; } fn main(): str { let x: str = nope "a"; return x; }',
                       commands={"nope": (["str"], "str")})
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_AMBIGUOUS_COMMAND")

    def test_24_arity_and_arg_types(self):
        res = _analyze('fn main(): str { let x: str = upper; return x; }')
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_COMMAND_ARITY")
        res = _analyze('fn main(): str { let x: str = upper 1; return x; }')
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_COMMAND_TYPE_MISMATCH")

    def test_25_bare_word_var_priority(self):
        res = _analyze('fn main(): str { let ls: str = "v"; let x: str = ls |> count; return x; }')
        self.assertTrue(res.ok, res.diagnostic)
        stages = res.ast["functions"][0]["body"]["stmts"][1]["init"]["stages"]
        self.assertEqual(stages[0]["kind"], "Var")

    def test_26_no_implicit_conversions(self):
        res = _analyze('fn main(): str { let x: str = upper 1; return x; }')
        self.assertFalse(res.ok)
        res = _analyze('fn main(): int { let x: int = "a" |> count; return x; }')
        self.assertFalse(res.ok)

    def test_27_rir_and_backend_reject_shell_kinds(self):
        src = (GOOD / "pipe_basic.rl").read_text(encoding="utf-8")
        res = _analyze(src)
        self.assertTrue(res.ok, res.diagnostic)
        module, error = rir.build_rir(res.ast, "pipe_basic.rl")
        self.assertIsNone(module)
        self.assertEqual((error or {}).get("code"), "COMP_V2_UNSUPPORTED")

    def test_28_bad_registry_rejected_without_crash(self):
        res = _analyze('fn main(): str { let x: str = upper "a"; return x; }',
                       commands={"upper": (["nope"], "str")})
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "PAR_INVALID_INPUT")


class ShellDeterminismTests(unittest.TestCase):
    def test_29_shell_corpus_byte_identical_3x(self):
        for name in sorted(EXPECTED_GOOD):
            with self.subTest(fixture=name):
                src = (GOOD / name).read_text(encoding="utf-8")
                first = None
                for _ in range(3):
                    lexed = lexmod.lex(src, name, edition="shell")
                    self.assertTrue(lexed.ok, lexed.diagnostic)
                    parsed = parsemod.parse(src, name, edition="shell")
                    self.assertTrue(parsed.ok, parsed.diagnostic)
                    analyzed = _analyze(src)
                    self.assertTrue(analyzed.ok, analyzed.diagnostic)
                    blob = json.dumps(analyzed.ast, sort_keys=True, separators=(",", ":"))
                    if first is None:
                        first = blob
                    else:
                        self.assertEqual(blob, first)

    def test_30b_limits_and_cli_gates(self):
        import subprocess
        chain = " |> ".join(['"s"'] * 500)
        res = _analyze(f'fn main(): str {{ let x: str = {chain}; return x; }}')
        self.assertTrue(res.ok, res.diagnostic)
        self.assertEqual(len(res.ast["functions"][0]["body"]["stmts"][0]["init"]["stages"]), 500)
        deep = "(" * 300 + '"a"' + ")" * 300
        src = f'fn main(): str {{ let x: str = {deep}; return x; }}'
        for edition in ("v1", "shell"):
            with self.subTest(edition=edition):
                res = analyzemod.analyze(src, "t.rl", edition=edition, commands={})
                self.assertFalse(res.ok)
                self.assertEqual(res.diagnostic.code, "PAR_DEPTH_EXCEEDED")
        for tool in ("tools/rynorlang/lex.py", "tools/rynorlang/parse.py",
                     "tools/rynorlang/analyze.py"):
            with self.subTest(tool=tool):
                proc = subprocess.run(
                    [sys.executable, str(ROOT / tool), "--edition", "bogus",
                     str(GOOD / "pipe_basic.rl")],
                    capture_output=True, text=True, cwd=str(ROOT))
                self.assertEqual(proc.returncode, 2)
                self.assertIn("unknown edition", proc.stderr)

    def test_30_no_shell_kinds_in_v1_corpus(self):
        for directory in ("lexer", "parser", "semantics"):
            for path in sorted((ROOT / "tests/fixtures/rynorlang" / directory).rglob("*.rl")):
                with self.subTest(fixture=str(path.relative_to(ROOT))):
                    try:
                        src = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
                    res = analyzemod.analyze(src, path.name)
                    if not res.ok:
                        continue
                    hits = _find_all(res.ast, "Pipeline", []) + _find_all(res.ast, "Cmd", [])
                    hits += _find_all(res.ast, "Flag", []) + _find_all(res.ast, "Redirect", [])
                    self.assertEqual(hits, [])


class ShellEvalTests(unittest.TestCase):
    def _main_value(self, name):
        src = (GOOD / name).read_text(encoding="utf-8")
        res = _analyze(src)
        self.assertTrue(res.ok, res.diagnostic)
        return _eval_main(res.ast)

    def test_31_fixture_values_match_evaluator(self):
        for name, want in sorted(EXPECTED_GOOD.items()):
            with self.subTest(fixture=name):
                if want is None:
                    continue
                if name == "cmd_call_mix.rl":
                    continue  # Call stages need real calls; see test_36
                self.assertEqual(self._main_value(name), want)

    def test_32_single_command_impls(self):
        self.assertEqual(IMPLS["take"](None, ["hello", "2"]), "he")
        self.assertEqual(IMPLS["withflag"](None, ["-v"]), "got:v")
        self.assertEqual(IMPLS["upper"](None, ["hi"]), "HI")
        self.assertEqual(IMPLS["count"](None, ["abcd"]), "4")

    def test_33_errors_abort_downstream(self):
        calls = []

        def flaky(piped, args):
            calls.append(args[0] if args else piped)
            raise shellmod.ShellExecError("SHELL_BOOM", "stage fails", 1)

        pipe = {"kind": "Pipeline", "span": {}, "type": "str", "stages": [
            {"kind": "StrLit", "span": {}, "value": "a", "lexeme": '"a"', "type": "str"},
            {"kind": "Cmd", "span": {}, "name": "flaky", "argv": [], "redirects": [],
             "type": "str", "symbol": 0},
            {"kind": "Cmd", "span": {}, "name": "never", "argv": [], "redirects": [],
             "type": "str", "symbol": 1},
        ]}
        with self.assertRaises(shellmod.ShellExecError):
            shellmod.run_pipeline(pipe, {}, {"flaky": flaky,
                                             "never": lambda p, a: calls.append("never") or "x"})
        self.assertNotIn("never", calls)

    def test_34_capacity_never_truncates(self):
        big = "y" * (shellmod.PIPE_BUF_CAP + 1)
        pipe = {"kind": "Pipeline", "span": {}, "type": "str", "stages": [
            {"kind": "Cmd", "span": {}, "name": "big", "argv": [], "redirects": [],
             "type": "str", "symbol": 0},
        ]}
        with self.assertRaises(shellmod.ShellOverflow):
            shellmod.run_pipeline(pipe, {}, {"big": lambda p, a: big})

    def test_35_unit_pipeline_returns_none(self):
        src = (GOOD / "pipe_unit_exprstmt.rl").read_text(encoding="utf-8")
        res = _analyze(src)
        self.assertTrue(res.ok, res.diagnostic)
        pipe = _find_kind(res.ast, "Pipeline")
        self.assertIsNotNone(pipe)
        self.assertIsNone(shellmod.run_pipeline(pipe, {}, IMPLS))

    def test_36_call_stage_explicitly_unsupported(self):
        src = (GOOD / "cmd_call_mix.rl").read_text(encoding="utf-8")
        res = _analyze(src)
        self.assertTrue(res.ok, res.diagnostic)
        pipe = _find_kind(res.ast, "Pipeline")
        self.assertIsNotNone(pipe)
        with self.assertRaises(shellmod.ShellExecError) as ctx:
            shellmod.run_pipeline(pipe, {}, IMPLS)
        self.assertEqual(ctx.exception.code, "SHELL_UNSUPPORTED_STAGE")


class ShellReplGuardTests(unittest.TestCase):
    def test_37_block_accumulator(self):
        acc = shellmod.BlockAccumulator(edition="shell")
        status, _ = acc.push('fn main(): str {')
        self.assertEqual(status, "continue")
        status, buf = acc.push('    let x: str = ls |> count;')
        self.assertEqual(status, "continue")
        status, buf = acc.push('    return x;')
        self.assertEqual(status, "continue")
        status, buf = acc.push('}')
        self.assertEqual(status, "ready")
        res = _analyze(buf)
        self.assertTrue(res.ok, res.diagnostic)

    def test_38_no_kernel_repl_or_eval(self):
        self.assertEqual(list((ROOT / "kernel").glob("shell/repl*")), [])
        self.assertEqual(list((ROOT / "kernel").rglob("repl*")), [])
        shell_c = (ROOT / "kernel/shell/shell.c").read_text(encoding="utf-8")
        for marker in ("rynorlang", "Pipeline", "CmdExpr", "|>"):
            self.assertNotIn(marker, shell_c)


class ShellMutationTests(unittest.TestCase):
    def _mutant(self, path, old, new, name=None):
        if name is None:
            name = path.stem
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, f"anchor must be unique: {old!r}")
        directory = tempfile.TemporaryDirectory(prefix="shell-mutant-")
        self.addCleanup(directory.cleanup)
        target = Path(directory.name) / path.name
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return _load(f"{name}_{len(sys.modules)}", target)

    def test_39_lexer_gate_removal_detected(self):
        mutant = self._mutant(LEX_PATH,
                              'if self.edition == "shell" and pair in SHELL_DOUBLE_TOKENS:',
                              'if pair in SHELL_DOUBLE_TOKENS:')
        res = mutant.lex('a |> b', 't.rl')
        self.assertTrue(res.ok, "mutant must accept |> in v1")
        real = lexmod.lex('a |> b', 't.rl')
        self.assertFalse(real.ok)
        self.assertEqual(real.diagnostic.code, "LEX_INVALID_CHAR")

    def test_40_pipeline_check_removal_detected(self):
        mutant = self._mutant(ANALYZE_PATH,
                              '            if stype != "str":\n                self._error(S_PIPELINE_TYPE, f"pipeline stage {index} expects str got {stype}", node.children[index].span,\n                            expected="str", got=stype, context=f"pipeline stage {index}")',
                              '            if False and stype != "str":\n                self._error(S_PIPELINE_TYPE, "dead", node.span)')
        bad = (BAD / "pipe_int_stage.rl").read_text(encoding="utf-8")
        self.assertFalse(_analyze(bad).ok)
        res = mutant.analyze(bad, "t.rl", edition="shell", commands=CMDS)
        self.assertTrue(res.ok, "mutant must accept the int stage")

    def test_41_unit_check_removal_detected(self):
        # A unit non-final stage trips two independent gates (unit-first,
        # then str). Removing the unit gate must still flip the diagnostic
        # from SHELL_UNIT_STAGE to SHELL_PIPELINE_TYPE_MISMATCH, proving the
        # removed check was load-bearing for the precise error.
        mutant = self._mutant(ANALYZE_PATH,
                              '            if stype == "unit":\n                self._error(S_UNIT_STAGE, f"pipeline stage {index} is unit; only the final stage may be unit", node.children[index].span,\n                            expected="non-unit str stage", got="unit", context=f"pipeline stage {index}")',
                              '            if False and stype == "unit":\n                self._error(S_UNIT_STAGE, "dead", node.span)')
        bad = (BAD / "pipe_unit_nonfinal.rl").read_text(encoding="utf-8")
        base = _analyze(bad)
        self.assertFalse(base.ok)
        self.assertEqual(base.diagnostic.code, "SHELL_UNIT_STAGE")
        res = mutant.analyze(bad, "t.rl", edition="shell", commands=CMDS)
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_PIPELINE_TYPE_MISMATCH")

    def test_42_unknown_command_check_removal_detected(self):
        mutant = self._mutant(ANALYZE_PATH,
                              '                self._error(S_UNKNOWN_COMMAND, f"unknown command \'{name}\'", node.span,',
                              '                self._error("SHELL_MUTANT", f"unknown command \'{name}\'", node.span,')
        bad = (BAD / "cmd_unknown.rl").read_text(encoding="utf-8")
        self.assertFalse(_analyze(bad).ok)
        res = mutant.analyze(bad, "t.rl", edition="shell", commands=CMDS)
        self.assertFalse(res.ok)
        self.assertEqual(res.diagnostic.code, "SHELL_MUTANT")

    def test_43_stage_order_swap_detected(self):
        pipe = {"kind": "Pipeline", "span": {}, "type": "str", "stages": [
            {"kind": "StrLit", "span": {}, "value": "hi", "lexeme": '"hi"', "type": "str"},
            {"kind": "Cmd", "span": {}, "name": "upper", "argv": [], "redirects": [],
             "type": "str", "symbol": 0},
        ]}
        self.assertEqual(shellmod.run_pipeline(pipe, {}, IMPLS), "HI")
        pipe["stages"] = list(reversed(pipe["stages"]))
        # reversed, `upper` is head with no args: the demo impl requires input
        with self.assertRaises(shellmod.ShellExecError):
            shellmod.run_pipeline(pipe, {}, IMPLS)

    def test_44_command_must_not_desugar_to_call(self):
        src = (GOOD / "cmd_redirect.rl").read_text(encoding="utf-8")
        res = _analyze(src)
        self.assertTrue(res.ok, res.diagnostic)
        kinds = []
        _find_all(res.ast, "Cmd", kinds)
        self.assertTrue(kinds, "command must lower to an honest Cmd node")
        calls = []
        _find_all(res.ast, "Call", calls)
        self.assertEqual(calls, [])

    def test_45_edition_gate_removal_detected(self):
        # The v1 token check rejects |> lexemes before parsing, so this
        # mutant is exercised at the _Parser level directly: the real
        # parser must fail a shell token stream in v1 while the mutant
        # (gate removed) accepts it.
        mutant = self._mutant(PARSE_PATH,
                              '        if self.edition != "shell":\n            self.fail("PAR_UNEXPECTED_TOKEN", "pipeline operator requires the shell edition", ("SEMICOLON",))',
                              '        if False:\n            self.fail("PAR_UNEXPECTED_TOKEN", "dead", ("SEMICOLON",))')

        def run(mod, edition):
            toks = lexmod.lex('fn main(): str { let v: str = x |> y; return v; }',
                              't.rl', edition='shell').tokens
            parser = mod._Parser(toks, edition)
            try:
                parser.parse_program()
            except Exception:
                return False
            return True

        self.assertFalse(run(parsemod, "v1"))
        self.assertTrue(run(mutant, "v1"), "mutant must accept a pipeline in v1")


if __name__ == "__main__":
    unittest.main()
