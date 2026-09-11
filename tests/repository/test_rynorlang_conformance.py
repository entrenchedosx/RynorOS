"""Stage 19d conformance, determinism, and the self-host checklist.

Covers docs/design/rynorlang-conformance.md: the bounded
type/rule/cap pairing matrix (new cells plus references to the
19a-19c pins, never duplicates), canonical-encoding pins,
ungated 3x text-artifact determinism over the full corpus,
gated 3x linked/run evidence, the banned-nondeterminism pin,
--profile=strict R1/R2, the kLOC counter, and the mutant gate.
v1/19a/19b/19c suites must pass unchanged.
"""

import hashlib
import importlib.util
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.rynorlang import agtypes  # noqa: E402
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import compile as compiler  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import kloc  # noqa: E402
from tools.rynorlang import module as mod  # noqa: E402
from tools.rynorlang import rir  # noqa: E402

ANALYZER_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
AGTYPE_PATH = ROOT / "tools" / "rynorlang" / "agtypes.py"
INTERP_PATH = ROOT / "tools" / "rynorlang" / "interp.py"
MODULE_PATH = ROOT / "tools" / "rynorlang" / "module.py"
TOOLS_DIR = ROOT / "tools" / "rynorlang"
AGG_GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "aggregates" / "good"
CTL_GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "control" / "good"
MOD_GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "modules" / "good"
CONF_GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "conformance" / "good"
CONF_BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "conformance" / "bad"
CONF_PROJ = ROOT / "tests" / "fixtures" / "rynorlang" / "conformance" / "projects"

# Frozen pairing set (§3 of the RFC). Every cell names its pin.
PAIR_CELLS = frozenset({
    "print_int", "print_bool", "print_str", "print_record",
    "print_list", "print_map", "print_status", "print_result",
    "eq_int", "eq_bool", "eq_str", "eq_record", "eq_list", "eq_map",
    "eq_status", "eq_result", "eq_mismatch",
    "key_int", "key_str", "key_bool",
    "match_int", "match_str", "match_bool", "match_status",
    "match_result", "match_exhaust_bool", "match_exhaust_int",
    "match_unreachable", "match_bind",
    "nest_status_single", "nest_status_nested", "nest_result_nested",
    "nest_list_record", "nest_result_map", "nest_list_nested",
    "qual_type", "qual_call", "qual_construct", "qual_nested_module",
    "qual_std", "qual_arity_bad", "qual_unknown_alias",
    "qual_unknown_member", "qual_unqualified",
    "shadow_param", "shadow_let", "shadow_match_bind",
    "shadow_let_import", "shadow_reserved_use",
    "order_load_before_sem", "order_cycle_chain",
    "cap_source", "cap_str", "cap_depth", "cap_uses",
    "cap_import_depth", "cap_modules", "cap_manifest", "cap_alias",
    "cap_agg", "cap_nesting", "cap_frameslots", "cap_oracle_budgets",
    "profile_r1_shell", "profile_r1_v1", "profile_r1_default",
    "profile_r2_unpinned", "profile_r2_pinned", "profile_r2_std",
    "profile_r2_default",
    "canon_types", "canon_keys", "canon_json", "canon_rir",
    "canon_symbols", "canon_print", "canon_asm",
    "banned_sources",
    "kloc_units", "kloc_std",
    "guest_rleval", "guest_rlen",
})

# Cell -> pin. "conf:test_NN" pins live here; "agg:/ctl:/mod:/sem:"
# reference the 19a-19c suites (no duplication).
COVERAGE = {
    "print_int": "agg:PRINT goldens", "print_bool": "mod:std_use golden",
    "print_str": "mod:error_threading_mod golden", "print_record": "mod:basic_import golden",
    "print_list": "conf:test_06", "print_map": "conf:test_06",
    "print_status": "conf:test_06", "print_result": "conf:test_06",
    "eq_int": "agg:goldens", "eq_bool": "conf:test_06", "eq_str": "conf:test_06",
    "eq_record": "conf:test_06", "eq_list": "conf:test_06", "eq_map": "conf:test_06",
    "eq_status": "conf:test_06", "eq_result": "conf:test_06",
    "eq_mismatch": "conf:test_07",
    "key_int": "agg:maxcap", "key_str": "agg:map_basic", "key_bool": "conf:test_06",
    "match_int": "ctl:match_literal", "match_str": "ctl:match_literal",
    "match_bool": "ctl:test_08", "match_status": "conf:test_06",
    "match_result": "ctl:match_result", "match_exhaust_bool": "ctl:test_08",
    "match_exhaust_int": "ctl:test_08", "match_unreachable": "ctl:BAD_CODES match_unreachable",
    "match_bind": "ctl:match_literal",
    "nest_status_single": "agg:list_basic", "nest_status_nested": "conf:test_07",
    "nest_result_nested": "ctl:BAD_CODES result_nested", "nest_list_record": "conf:test_06",
    "nest_result_map": "conf:test_06", "nest_list_nested": "agg:list_nested",
    "qual_type": "mod:basic_import", "qual_call": "mod:basic_import",
    "qual_construct": "conf:test_08", "qual_nested_module": "mod:nested_use",
    "qual_std": "mod:std_use", "qual_arity_bad": "mod:BAD_CODES qualified_arity",
    "qual_unknown_alias": "mod:BAD_CODES unknown_alias",
    "qual_unknown_member": "mod:BAD_CODES unknown_member",
    "qual_unqualified": "mod:BAD_CODES unqualified_call",
    "shadow_param": "sem:test_59", "shadow_let": "sem:test_60",
    "shadow_match_bind": "ctl:BAD_CODES match_binding_shadow",
    "shadow_let_import": "conf:test_07", "shadow_reserved_use": "mod:BAD_CODES use_reserved",
    "order_load_before_sem": "mod:test_17", "order_cycle_chain": "mod:test_09",
    "cap_source": "conf:test_12", "cap_str": "conf:test_13", "cap_depth": "conf:test_14",
    "cap_uses": "conf:test_15", "cap_import_depth": "conf:test_16",
    "cap_modules": "conf:test_17", "cap_manifest": "conf:test_18",
    "cap_alias": "conf:test_19", "cap_agg": "agg:test_08b",
    "cap_nesting": "agg:GOOD maxcap", "cap_frameslots": "agg:test_08b",
    "cap_oracle_budgets": "compiler:trap fixtures",
    "profile_r1_shell": "conf:test_22", "profile_r1_v1": "conf:test_22",
    "profile_r1_default": "conf:test_22", "profile_r2_unpinned": "conf:test_23",
    "profile_r2_pinned": "conf:test_23", "profile_r2_std": "conf:test_23",
    "profile_r2_default": "conf:test_23",
    "canon_types": "conf:test_09", "canon_keys": "conf:test_09",
    "canon_json": "conf:test_10", "canon_rir": "conf:test_11",
    "canon_symbols": "conf:test_11", "canon_print": "conf:test_06",
    "canon_asm": "conf:test_20",
    "banned_sources": "conf:test_21",
    "kloc_units": "conf:test_24", "kloc_std": "conf:test_24",
    "guest_rleval": "integration:test_rleval", "guest_rlen": "integration:test_rlen",
}

CONF_GOOD_GOLDEN = {
    "eq_pairs.rl": (0, "falsetruetruetruetruetruetrue"),
    "print_values.rl": (0, "[3, 1]{1: 10, 2: 20}ok(5)err(2)ok(7){a: 1, b: 2}"),
    "map_keys.rl": (0, "{true: 1, false: 0}{x: 9}"),
    "list_record.rl": (0, "[{a: 1, b: 2}, {a: 3, b: 4}]"),
    "result_map.rl": (0, "{a: 1}"),
    "match_status.rl": (1, "5"),
}

CONF_BAD_CODES = {
    "eq_mismatch.rl": "SEM_TYPE_MISMATCH",
    "status_nested.rl": "SEM_TYPE_MISMATCH",
    "map_key_mismatch.rl": "SEM_TYPE_MISMATCH",
}

BANNED_PATTERNS = (
    r"import random", r"import time", r"from time", r"import datetime",
    r"os\.urandom", r"os\.listdir", r"os\.getenv", r"os\.getpid",
    r"random\.", r"time\.(time|monotonic|perf_counter|sleep|clock)",
)


def _toolchain():
    nasm = shutil.which("nasm") or shutil.which("nasm.exe")
    if nasm is None:
        return None
    for linker in ("ld.lld", "ld"):
        if shutil.which(linker):
            return (nasm, linker)
    return None


TOOLCHAIN = _toolchain()


def _project(files):
    directory = Path(tempfile.mkdtemp(prefix="conf-proj-"))
    for rel, text in files.items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory


def _oracle_of_ast(ast, name):
    module, error = rir.build_rir(ast, name)
    assert error is None, error
    assert rir.verify_module(module) == []
    emitted: list = []
    outcome = oracle.run_rir(module, out=emitted)
    assert outcome["trapped"] is None, outcome
    return outcome["exit"], "".join(emitted)


def _asm_text_of_ast(ast, name):
    module, error = rir.build_rir(ast, name)
    assert error is None, error
    assert rir.verify_module(module) == []
    return compiler.emit_asm(module)


def _load_mutant(path, old, new):
    text = Path(path).read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation anchor must be unique: {old!r}"
    target = Path(tempfile.mkdtemp(prefix="conf-mut-")) / Path(path).name
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    name = f"mut_conf_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(name, target)
    assert spec is not None and spec.loader is not None
    mutant = importlib.util.module_from_spec(spec)
    sys.modules[name] = mutant
    try:
        spec.loader.exec_module(mutant)
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)
        sys.modules.pop(name, None)
    return mutant


class ConformanceLayoutTests(unittest.TestCase):
    def test_01_pairing_inventory_is_exact(self):
        self.assertEqual(set(COVERAGE), PAIR_CELLS)
        for cell, pin in COVERAGE.items():
            self.assertTrue(pin, cell)

    def test_02_fixture_inventory_is_exact(self):
        self.assertEqual({p.name for p in CONF_GOOD.iterdir() if p.is_file()},
                         set(CONF_GOOD_GOLDEN))
        self.assertEqual({p.name for p in CONF_BAD.iterdir() if p.is_file()},
                         set(CONF_BAD_CODES))
        self.assertEqual({p.name for p in CONF_PROJ.iterdir() if p.is_dir()},
                         {"qual_construct"})


class ConformanceMatrixTests(unittest.TestCase):
    def test_03_good_matrix_analyzes(self):
        for name in sorted(CONF_GOOD_GOLDEN):
            with self.subTest(good=name):
                result = analyzer.analyze((CONF_GOOD / name).read_text(encoding="utf-8"), name)
                self.assertTrue(result.ok, result.diagnostic)

    def test_04_bad_matrix_rejects_exact(self):
        for name, code in sorted(CONF_BAD_CODES.items()):
            with self.subTest(bad=name):
                result = analyzer.analyze((CONF_BAD / name).read_text(encoding="utf-8"), name)
                self.assertFalse(result.ok)
                self.assertEqual(result.diagnostic.code, code)

    def test_05_let_shadowing_an_import_is_duplicate(self):
        directory = _project({
            "lib/calc.rl": "fn double(x: int): int { return x + x; }\n",
            "main.rl": ("use \"lib/calc.rl\";\n"
                        "fn main(): int { let calc__double: int = 1; return calc__double; }\n"),
        })
        self.addCleanup(shutil.rmtree, directory, True)
        program, error = mod.analyze_entry(directory / "main.rl")
        self.assertIsNone(program)
        self.assertEqual(error["code"], "SEM_DUPLICATE")

    def test_06_good_matrix_goldens(self):
        for name, want in sorted(CONF_GOOD_GOLDEN.items()):
            with self.subTest(good=name):
                result = analyzer.analyze((CONF_GOOD / name).read_text(encoding="utf-8"), name)
                self.assertTrue(result.ok, result.diagnostic)
                self.assertEqual(_oracle_of_ast(result.ast, name), want)

    def test_07_inline_bad_rejects_exact(self):
        cases = [
            ("status<status<int>> via get",
             'fn main(): int { let s: status<status<int>> = get({"k": 5}, "k"); return 0; }',
             "SEM_TYPE_MISMATCH"),
        ]
        for label, src, code in cases:
            with self.subTest(cell=label):
                result = analyzer.analyze(src, "inline.rl")
                self.assertFalse(result.ok)
                self.assertEqual(result.diagnostic.code, code)

    def test_08_qualified_construction_project(self):
        entry = CONF_PROJ / "qual_construct" / "main.rl"
        program, error = mod.analyze_entry(entry)
        self.assertIsNone(error, error)
        names = {fn["name"] for fn in program["functions"]}
        self.assertIn("calc__Pair", {r["name"] for r in program.get("records", [])})
        self.assertNotIn("Pair", names)
        self.assertEqual(_oracle_of_ast(program, "qual_construct/main.rl"), (0, "7"))


class CanonicalEncodingTests(unittest.TestCase):
    def test_09_type_strings_and_key_bytes(self):
        shapes = {
            "int": "int", "bool": "bool", "str": "str",
            "status<int>": "status<int>", "result<int,str>": "result<int,str>",
            "list<int,2>": "list<int,2>", "map<str,int,4>": "map<str,int,4>",
            "list<Pair,2>": "list<Pair,2>",
        }
        for text, want in shapes.items():
            with self.subTest(type=text):
                self.assertEqual(agtypes.canonical(agtypes.parse_type(text)), want)
        self.assertEqual(agtypes.fnv1a64(b"k"), 12638198195671924106)
        self.assertEqual(agtypes.key_bytes("int", 5), b"\x05\x00\x00\x00\x00\x00\x00\x00")
        self.assertEqual(agtypes.key_bytes("str", "k"), b"k")
        self.assertEqual(agtypes.key_bytes("bool", True), b"\x01")

    def test_10_ast_json_is_sorted(self):
        result = analyzer.analyze("fn main(): int { return 1; }", "t.rl")
        self.assertTrue(result.ok)
        text = "".join(analyzer.iter_ast_json(result.ast))
        self.assertTrue(text.startswith('{"functions":'))
        compact = __import__("json").dumps(result.ast, sort_keys=True, separators=(",", ":"))
        self.assertEqual(text, compact)

    def test_11_rir_header_and_merge_symbols(self):
        result = analyzer.analyze("fn main(): int { return 1; }", "hdr.rl")
        module, error = rir.build_rir(result.ast, "hdr.rl")
        self.assertIsNone(error)
        first = rir.dumps(module).splitlines()[0]
        self.assertTrue(first.startswith("; rir_version="), first)
        self.assertIn('source="hdr.rl"', first)
        program, error = mod.analyze_entry(MOD_GOOD / "diamond" / "main.rl")
        self.assertIsNone(error, error)
        self.assertEqual([(f["name"], f["symbol"]) for f in program["functions"]],
                         [("main", 0), ("b__left", 1), ("d__leaf", 2), ("c__right", 3)])


class ConformanceCapTests(unittest.TestCase):
    def _temp_source(self, data: bytes):
        directory = Path(tempfile.mkdtemp(prefix="conf-cap-"))
        self.addCleanup(shutil.rmtree, directory, True)
        target = directory / "cap.rl"
        target.write_bytes(data)
        return target

    def test_12_cap_source_bytes(self):
        base = b"fn main(): int { return 0; }\n"
        for size, ok in ((1048576, True), (1048577, False)):
            with self.subTest(bytes=size):
                target = self._temp_source(base + b" " * (size - len(base)))
                result = analyzer.analyze_file(target)
                if ok:
                    self.assertTrue(result.ok, result.diagnostic)
                else:
                    self.assertFalse(result.ok)
                    self.assertEqual(result.diagnostic.code, "PAR_FILE_TOO_LARGE")

    def test_13_cap_str_len(self):
        for size, ok in ((4096, True), (4097, False)):
            with self.subTest(chars=size):
                result = analyzer.analyze(
                    "fn main(): int { print(\"" + "a" * size + "\"); return 0; }", "s.rl")
                if ok:
                    self.assertTrue(result.ok, result.diagnostic)
                else:
                    self.assertFalse(result.ok)
                    self.assertEqual(result.diagnostic.code, "SEM_LIMIT_EXCEEDED")

    def test_14_cap_depth(self):
        # if+block cost 2 depth units per level: 127 nests fit 256, 128 do not.
        for levels, ok in ((127, True), (128, False)):
            with self.subTest(levels=levels):
                src = ("fn main(): int {\n" + "  if true {\n" * levels
                       + "    return 1;\n" + "  }\n" * levels + "}\n")
                result = analyzer.analyze(src, "deep.rl")
                if ok:
                    self.assertTrue(result.ok, result.diagnostic)
                else:
                    self.assertFalse(result.ok)
                    self.assertEqual(result.diagnostic.code, "PAR_DEPTH_EXCEEDED")

    def test_15_cap_uses_per_file(self):
        # 64 identical uses stay under the module cap (2 files) and pass;
        # the 65th statement trips the per-file bound.
        for count, ok in ((64, True), (65, False)):
            with self.subTest(uses=count):
                directory = _project({
                    "lib/a.rl": "fn f(): int { return 1; }\n",
                    "main.rl": "use \"lib/a.rl\";\n" * count + "fn main(): int { return 0; }\n",
                })
                self.addCleanup(shutil.rmtree, directory, True)
                _, error = mod.analyze_entry(directory / "main.rl")
                if ok:
                    self.assertIsNone(error, error)
                else:
                    self.assertIsNotNone(error)
                    self.assertEqual(error["code"], "MOD_NOT_FOUND")
                    self.assertIn("too many imports", error["message"])

    def test_16_cap_import_depth(self):
        # 17 files chain to depth 16 (bound, ok); 18 files reach 17 (reject).
        for total, ok in ((17, True), (18, False)):
            with self.subTest(files=total):
                files = {"main.rl": "use \"m1.rl\";\nfn main(): int { return 0; }\n"}
                for index in range(1, total):
                    nxt = f"use \"m{index + 1}.rl\";\n" if index + 1 < total else ""
                    files[f"m{index}.rl"] = nxt + f"fn f{index}(): int {{ return {index}; }}\n"
                directory = _project(files)
                self.addCleanup(shutil.rmtree, directory, True)
                _, error = mod.analyze_entry(directory / "main.rl")
                if ok:
                    self.assertIsNone(error, error)
                else:
                    self.assertIsNotNone(error)
                    self.assertEqual(error["code"], "MOD_CYCLE")
                    self.assertIn("import depth exceeds 16", error["message"])

    def test_17_cap_modules(self):
        for libs, ok in ((63, True), (64, False)):
            with self.subTest(libs=libs):
                files = {f"lib/m{i}.rl": f"fn f{i}(): int {{ return {i}; }}\n" for i in range(libs)}
                files["main.rl"] = "".join(f"use \"lib/m{i}.rl\";\n" for i in range(libs))
                files["main.rl"] += "fn main(): int { return 0; }\n"
                directory = _project(files)
                self.addCleanup(shutil.rmtree, directory, True)
                _, error = mod.analyze_entry(directory / "main.rl")
                if ok:
                    self.assertIsNone(error, error)
                else:
                    self.assertIsNotNone(error)
                    self.assertEqual(error["code"], "MOD_NOT_FOUND")
                    self.assertIn("too many modules", error["message"])

    def test_18_cap_manifest_bytes(self):
        import hashlib
        import json as _json
        for size, ok in ((65536, True), (65537, False)):
            with self.subTest(bytes=size):
                directory = _project({
                    "lib/a.rl": "fn f(): int { return 1; }\n",
                    "main.rl": "use \"lib/a.rl\";\nfn main(): int { return 0; }\n",
                })
                self.addCleanup(shutil.rmtree, directory, True)
                digest = hashlib.sha256((directory / "lib" / "a.rl").read_bytes()).hexdigest()
                doc = {"modules": {"lib/a.rl": {"sha256": digest, "edition": "v1"}}}
                # Pad inside modules: extra pins name unimported files
                # (ignored by design); top-level keys stay exactly
                # {modules} and every entry stays valid. Key bytes are
                # ASCII so each added char is exactly one byte.
                spare = "lib/" + "p" * 8 + ".rl"
                doc["modules"][spare] = {"sha256": "0" * 64, "edition": "v1"}
                raw = _json.dumps(doc).encode()
                grown = "lib/" + "p" * (8 + (size - len(raw))) + ".rl"
                doc["modules"][grown] = doc["modules"].pop(spare)
                raw = _json.dumps(doc).encode()
                self.assertEqual(len(raw), size)
                (directory / "rlmod.json").write_bytes(raw)
                _, error = mod.analyze_entry(directory / "main.rl")
                if ok:
                    self.assertIsNone(error, error)
                else:
                    self.assertIsNotNone(error)
                    self.assertEqual(error["code"], "MOD_BAD_MANIFEST")

    def test_19_cap_alias_len(self):
        for size, ok in ((64, True), (65, False)):
            with self.subTest(chars=size):
                stem = "a" * size
                directory = _project({
                    f"lib/{stem}.rl": "fn f(): int { return 1; }\n",
                    "main.rl": f"use \"lib/{stem}.rl\";\nfn main(): int {{ return 0; }}\n",
                })
                self.addCleanup(shutil.rmtree, directory, True)
                _, error = mod.analyze_entry(directory / "main.rl")
                if ok:
                    self.assertIsNone(error, error)
                else:
                    self.assertIsNotNone(error)
                    self.assertEqual(error["code"], "MOD_NOT_FOUND")


class ConformanceDeterminismTests(unittest.TestCase):
    def _corpus_texts(self):
        texts = []
        for path in sorted(AGG_GOOD.glob("*.rl")) + sorted(CTL_GOOD.glob("*.rl")):
            result = analyzer.analyze(path.read_text(encoding="utf-8"), path.name)
            self.assertTrue(result.ok, (path, result.diagnostic))
            texts.append((path.name, result.ast))
        for project in sorted(MOD_GOOD.iterdir()) + sorted(CONF_PROJ.iterdir()):
            entry = project / "main.rl"
            program, error = mod.analyze_entry(entry)
            self.assertIsNone(error, (project, error))
            texts.append((f"{project.parent.name}/{project.name}/main.rl", program))
        for path in sorted(CONF_GOOD.glob("*.rl")):
            result = analyzer.analyze(path.read_text(encoding="utf-8"), path.name)
            self.assertTrue(result.ok, (path, result.diagnostic))
            texts.append((path.name, result.ast))
        return texts

    def test_20_text_artifacts_byte_identical_3x(self):
        for name, ast in self._corpus_texts():
            with self.subTest(corpus=name):
                first = _asm_text_of_ast(ast, name)
                for _ in range(2):
                    self.assertEqual(_asm_text_of_ast(ast, name), first)

    def test_21_banned_nondeterminism_sources_absent(self):
        hits = []
        for path in sorted(TOOLS_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for pattern in BANNED_PATTERNS:
                if re.search(pattern, text):
                    hits.append(f"{path.name}: {pattern}")
        self.assertEqual(hits, [])


class ConformanceProfileTests(unittest.TestCase):
    def test_22_profile_r1_shell_excluded(self):
        shell_src = "fn main(): int { true |> print; return 0; }\n"
        strict = analyzer.analyze(shell_src, "s.rl", edition="shell", profile="strict")
        self.assertFalse(strict.ok)
        self.assertEqual(strict.diagnostic.code, "SEM_PROFILE_EXCLUDED")
        default = analyzer.analyze(shell_src, "s.rl", edition="shell")
        self.assertFalse(default.ok)
        self.assertNotEqual(default.diagnostic.code, "SEM_PROFILE_EXCLUDED")
        clean = analyzer.analyze("fn main(): int { print(1); return 0; }\n",
                                 "v.rl", profile="strict")
        self.assertTrue(clean.ok, clean.diagnostic)

    def test_23_profile_r2_manifest_required(self):
        nested = MOD_GOOD / "nested_use" / "main.rl"
        _, error = mod.analyze_entry(nested)
        self.assertIsNone(error, error)
        _, error = mod.analyze_entry(nested, profile="strict")
        self.assertIsNotNone(error)
        self.assertEqual(error["code"], "MOD_PIN_MISMATCH")
        self.assertIn("--profile=strict", error["message"])
        pinned = MOD_GOOD / "manifest_pinned" / "main.rl"
        _, error = mod.analyze_entry(pinned, profile="strict")
        self.assertIsNone(error, error)
        std_only = MOD_GOOD / "std_use" / "main.rl"
        _, error = mod.analyze_entry(std_only, profile="strict")
        self.assertIsNone(error, error)


class ConformanceKlocTests(unittest.TestCase):
    def test_24_kloc_counter(self):
        directory = Path(tempfile.mkdtemp(prefix="conf-kloc-"))
        self.addCleanup(shutil.rmtree, directory, True)
        sample = directory / "sample.rl"
        sample.write_text(
            "// full-line comment\n"
            "\n"
            "fn main(): int { // trailing comment counts\n"
            "  return 0; // trailing\n"
            "}\n"
            "   \n"
            "// another\n",
            encoding="utf-8")
        self.assertEqual(kloc.count_lines(sample), 3)
        self.assertEqual(kloc.count_lines(directory / "missing.rl"), 0)
        std_dir = TOOLS_DIR / "std"
        files, lines = kloc.count_tree(std_dir)
        self.assertEqual(files, 3)
        self.assertGreater(lines, 0)
        again_files, again_lines = kloc.count_tree(std_dir)
        self.assertEqual((files, lines), (again_files, again_lines))
        self.assertEqual(kloc.count_tree(sample), (1, 3))


class ConformanceMutationTests(unittest.TestCase):
    def test_25_canonical_corruption_spreads(self):
        # Unit-sensitivity pin: the test_09 exact strings catch any
        # canonical drift; here the mutant proves corruption propagates
        # through the generic branch (not just the scalar leaf).
        # Behavioral fallout (mistyped programs) is gated by the
        # native-vs-oracle differentials, which share this function.
        mutant = _load_mutant(
            AGTYPE_PATH,
            '    if kind in ("scalar", "nominal"):\n        return node[1]',
            '    if kind in ("scalar", "nominal"):\n        return "bool" if node[1] == "int" else node[1]')
        self.assertEqual(mutant.canonical(mutant.parse_type("int")), "bool")
        self.assertNotEqual(
            mutant.canonical(mutant.parse_type("list<int,2>")), "list<int,2>")

    def test_26_record_render_order_swapped(self):
        program, error = mod.analyze_entry(
            ROOT / "tests" / "fixtures" / "rynorlang" / "modules" / "good"
            / "basic_import" / "main.rl")
        self.assertIsNone(error, error)
        mutant = _load_mutant(
            INTERP_PATH,
            "for fname, ft in fields]",
            "for fname, ft in reversed(fields)]")
        module, build_error = rir.build_rir(program, "basic_import/main.rl")
        self.assertIsNone(build_error)
        emitted: list = []
        outcome = mutant.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"])
        self.assertNotIn("{a: 3, b: 4}", "".join(emitted))

    def test_27_key_bytes_flipped(self):
        # Unit-sensitivity pin across all three key kinds (exact bytes
        # in test_09). Behavioral fallout is gated by test_33: the
        # native emitter re-implements these bytes independently, so a
        # real drift diverges native-vs-oracle on every map fixture.
        mutant = _load_mutant(
            AGTYPE_PATH,
            'return (int(value) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")',
            'return (int(value) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")')
        self.assertNotEqual(mutant.key_bytes("int", 5), agtypes.key_bytes("int", 5))
        self.assertNotEqual(mutant.key_bytes("int", 256), agtypes.key_bytes("int", 256))

    def test_28_profile_bypass_accepts_shell(self):
        mutant = _load_mutant(
            ANALYZER_PATH,
            '                if self.profile == "strict":',
            '                if False and self.profile == "strict":')
        result = mutant.analyze("fn main(): int { true |> print; return 0; }\n",
                                "s.rl", edition="shell", profile="strict")
        if not result.ok:
            self.assertNotEqual(result.diagnostic.code, "SEM_PROFILE_EXCLUDED")

    def test_29_manifest_order_sensitivity(self):
        directory = _project({
            "lib/a.rl": "fn f(): int { return 1; }\n",
            "lib/b.rl": "fn g(): int { return 2; }\n",
            "main.rl": ("use \"lib/a.rl\";\nuse \"lib/b.rl\";\n"
                        "fn main(): int { return a::f() + b::g(); }\n"),
        })
        self.addCleanup(shutil.rmtree, directory, True)
        import hashlib
        import json as _json
        pins = {"modules": {}}
        for rel in ("lib/b.rl", "lib/a.rl"):
            digest = hashlib.sha256((directory / rel).read_bytes()).hexdigest()
            pins["modules"][rel] = {"sha256": digest, "edition": "v1"}
        (directory / "rlmod.json").write_text(_json.dumps(pins), encoding="utf-8")
        program, error = mod.analyze_entry(directory / "main.rl")
        self.assertIsNone(error, error)
        mutant = _load_mutant(
            MODULE_PATH,
            "    pins[key] = (digest, edition)",
            "    pins[key] = (digest, edition)\n"
            "        if list(pins) != sorted(pins):\n"
            "            return None, {\"code\": MOD_BAD_MANIFEST, \"message\": \"manifest keys must be sorted\"}")
        _, mutant_error = mutant.analyze_entry(directory / "main.rl")
        self.assertIsNotNone(mutant_error)
        self.assertEqual(mutant_error["code"], "MOD_BAD_MANIFEST")

    def test_30_merge_order_entry_last_diverges(self):
        program, error = mod.analyze_entry(MOD_GOOD / "diamond" / "main.rl")
        self.assertIsNone(error, error)
        module, build_error = rir.build_rir(program, "diamond/main.rl")
        self.assertIsNone(build_error)
        first = rir.dumps(module)
        mutant = _load_mutant(
            MODULE_PATH,
            "        for _alias, dep_key, _path_text in files[key][\"deps\"]:",
            "        for _alias, dep_key, _path_text in reversed(files[key][\"deps\"]):")
        mutant_program, mutant_error = mutant.analyze_entry(MOD_GOOD / "diamond" / "main.rl")
        self.assertIsNone(mutant_error)
        mutant_module, mutant_build_error = rir.build_rir(mutant_program, "diamond/main.rl")
        self.assertIsNone(mutant_build_error)
        self.assertNotEqual(rir.dumps(mutant_module), first)

    def test_31_step_budget_slashed_traps(self):
        result = analyzer.analyze(
            (CONF_GOOD / "eq_pairs.rl").read_text(encoding="utf-8"), "eq_pairs.rl")
        self.assertTrue(result.ok)
        module, build_error = rir.build_rir(result.ast, "eq_pairs.rl")
        self.assertIsNone(build_error)
        mutant = _load_mutant(INTERP_PATH, "STEP_LIMIT = 10_000_000", "STEP_LIMIT = 10")
        emitted: list = []
        outcome = mutant.run_rir(module, out=emitted)
        self.assertIsNotNone(outcome["trapped"])

    def test_32_module_cap_lifted_accepts_65(self):
        # The MAX_MODULES bound loads-bear: with the check removed, a
        # 65-module project (rejected in test_17) analyzes cleanly.
        files = {f"lib/m{i}.rl": f"fn f{i}(): int {{ return {i}; }}\n" for i in range(64)}
        files["main.rl"] = "".join(f"use \"lib/m{i}.rl\";\n" for i in range(64))
        files["main.rl"] += "fn main(): int { return 0; }\n"
        directory = _project(files)
        self.addCleanup(shutil.rmtree, directory, True)
        _, error = mod.analyze_entry(directory / "main.rl")
        self.assertIsNotNone(error)
        self.assertEqual(error["code"], "MOD_NOT_FOUND")
        mutant = _load_mutant(
            MODULE_PATH,
            "        if len(files) >= MAX_MODULES:",
            "        if False and len(files) >= MAX_MODULES:")
        _, mutant_error = mutant.analyze_entry(directory / "main.rl")
        self.assertIsNone(mutant_error, f"removed check still rejected input: {mutant_error}")


@unittest.skipUnless(TOOLCHAIN, "native execution unavailable (nasm + linker required)")
class ConformanceNativeTests(unittest.TestCase):
    def _native(self, build):
        from tools.rynorlang import program as progmod
        work = Path(tempfile.mkdtemp(prefix="conf-nat-"))
        try:
            kind, entry, name, ast = build
            if kind == "module":
                arts, error = progmod.build_module_program(entry, work, prog="prog")
            else:
                source = (CONF_GOOD / entry).read_text(encoding="utf-8") if kind == "conf" else Path(entry).read_text(encoding="utf-8")
                arts, error = progmod.build_program(source, name, work, prog="prog")
            self.assertIsNone(error, error)
            result, error = progmod.run_program(arts["exe"])
            self.assertIsNone(error, error)
            self.assertIsNone(result["signal"], result)
            exe_hash = hashlib.sha256(Path(arts["exe"]).read_bytes()).hexdigest()
            return result["exit"], result["stdout"].decode("ascii"), exe_hash
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _builds(self):
        builds = []
        for name in sorted(CONF_GOOD_GOLDEN):
            result = analyzer.analyze((CONF_GOOD / name).read_text(encoding="utf-8"), name)
            self.assertTrue(result.ok, result.diagnostic)
            builds.append(("conf", name, name, result.ast))
        for project in sorted(MOD_GOOD.iterdir()) + sorted(CONF_PROJ.iterdir()):
            program, error = mod.analyze_entry(project / "main.rl")
            self.assertIsNone(error, (project, error))
            builds.append(("module", project / "main.rl",
                           f"{project.parent.name}/{project.name}/main.rl", program))
        for path in sorted(AGG_GOOD.glob("*.rl")) + sorted(CTL_GOOD.glob("*.rl")):
            result = analyzer.analyze(path.read_text(encoding="utf-8"), path.name)
            self.assertTrue(result.ok, (path, result.diagnostic))
            builds.append(("file", str(path), path.name, result.ast))
        return builds

    def test_33_linked_artifacts_stable_3x_and_match_oracle(self):
        for kind, entry, name, ast in self._builds():
            with self.subTest(corpus=name):
                want_exit, want_out = _oracle_of_ast(ast, name)
                if kind == "conf":
                    self.assertEqual((want_exit, want_out), CONF_GOOD_GOLDEN[entry])
                seen = set()
                for _ in range(3):
                    seen.add(self._native((kind, entry, name, ast)))
                # Byte-identical linked artifacts: exit, stdout, AND
                # executable bytes stable across all three builds.
                self.assertEqual(len(seen), 1)
                exit_code, stdout, _exe_hash = seen.pop()
                self.assertEqual((exit_code, stdout), (want_exit, want_out))


if __name__ == "__main__":
    unittest.main()
