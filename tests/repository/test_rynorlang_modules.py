"""Stage 19c modules, manifests, and the std library.

Covers docs/design/rynorlang-modules.md: file-is-a-module imports,
mandatory stem aliases, qualified alias::name calls and types, the
import DAG (diamond sharing, cycle/duplicate rejection), strict
rlmod.json pins (hash + edition), the std/ toolchain prefix, and the
merged-program differential (native vs oracle). v1/19a/19b suites must
pass unchanged; native execution is capability-gated like the Stage
15a matrix.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as analyzer  # noqa: E402
from tools.rynorlang import interp as oracle  # noqa: E402
from tools.rynorlang import module as mod  # noqa: E402
from tools.rynorlang import rir  # noqa: E402

ANALYZER_PATH = ROOT / "tools" / "rynorlang" / "analyze.py"
MODULE_PATH = ROOT / "tools" / "rynorlang" / "module.py"
STD_DIR = ROOT / "tools" / "rynorlang" / "std"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "modules" / "good"
BAD = ROOT / "tests" / "fixtures" / "rynorlang" / "modules" / "bad"

GOOD_PROJECTS = {
    "basic_import", "dep_main", "diamond", "error_threading_mod",
    "manifest_pinned", "nested_use", "std_use",
}

BAD_CODES = {
    "bad_alias_stem": "MOD_NOT_FOUND",
    "bad_manifest": "MOD_BAD_MANIFEST",
    "bad_path_absolute": "MOD_NOT_FOUND",
    "bad_path_dotdot": "MOD_NOT_FOUND",
    "cycle": "MOD_CYCLE",
    "dup_alias": "MOD_DUPLICATE",
    "edition_mismatch": "MOD_EDITION_MISMATCH",
    "mangle_collision": "MOD_DUPLICATE",
    "missing_file": "MOD_NOT_FOUND",
    "pin_mismatch": "MOD_PIN_MISMATCH",
    "qualified_arity": "SEM_ARITY_MISMATCH",
    "self_import": "MOD_CYCLE",
    "unknown_alias": "SEM_UNDECLARED",
    "unknown_member": "SEM_UNKNOWN_FUNCTION",
    "unpinned_with_manifest": "MOD_PIN_MISMATCH",
    "unqualified_call": "SEM_UNKNOWN_FUNCTION",
    "use_reserved": "SEM_DUPLICATE",
}

GOLDEN = {
    "basic_import": (0, "10{a: 3, b: 4}"),
    "dep_main": (0, "42"),
    "diamond": (0, "203"),
    "error_threading_mod": (0, "7e"),
    "manifest_pinned": (0, "42"),
    "nested_use": (0, "1011"),
    "std_use": (0, "27truetrue112true"),
}

STD_ALIASES = {"math": "math", "str": "str", "test": "test"}


def _toolchain():
    nasm = shutil.which("nasm") or shutil.which("nasm.exe")
    if nasm is None:
        return None
    for linker in ("ld.lld", "ld"):
        if shutil.which(linker):
            return (nasm, linker)
    return None


TOOLCHAIN = _toolchain()


def _std_functions(stem):
    names = []
    for line in (STD_DIR / f"{stem}.rl").read_text(encoding="utf-8").splitlines():
        match = re.match(r"fn (\w+)\(", line)
        if match is not None:
            names.append(match.group(1))
    assert names, f"no functions parsed from std/{stem}.rl"
    return names


def _load_mutant(path, old, new):
    text = Path(path).read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation anchor must be unique: {old!r}"
    target = Path(tempfile.mkdtemp(prefix="mod-mut-")) / Path(path).name
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    name = f"mut_mod_{len(sys.modules)}"
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


def _analyze_mutant_entry(old, new, project):
    # Analyzer mutants run under the real module machinery: module.py
    # imports analyze lazily per call, so a temporary sys.modules swap
    # routes its seeded analysis through the mutant. The parent-package
    # attribute must move too: from-imports prefer it over sys.modules.
    # Restored after.
    import tools.rynorlang as package
    text = ANALYZER_PATH.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"mutation anchor must be unique: {old!r}"
    target = Path(tempfile.mkdtemp(prefix="mod-amut-")) / ANALYZER_PATH.name
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    key = "tools.rynorlang.analyze"
    real = sys.modules.get(key)
    real_attr = package.analyze
    spec = importlib.util.spec_from_file_location(key, target)
    assert spec is not None and spec.loader is not None
    mutant = importlib.util.module_from_spec(spec)
    sys.modules[key] = mutant
    package.analyze = mutant
    try:
        spec.loader.exec_module(mutant)
        return mod.analyze_entry(project / "main.rl")
    finally:
        if real is not None:
            sys.modules[key] = real
        else:
            sys.modules.pop(key, None)
        package.analyze = real_attr
        shutil.rmtree(target.parent, ignore_errors=True)


class ModuleLayoutTests(unittest.TestCase):
    def test_01_fixture_inventory_is_exact(self):
        self.assertEqual({p.name for p in GOOD.iterdir() if p.is_dir()}, GOOD_PROJECTS)
        self.assertEqual({p.name for p in BAD.iterdir() if p.is_dir()}, set(BAD_CODES) | {"no_main"})
        for name in GOOD_PROJECTS | set(BAD_CODES) | {"no_main"}:
            base = GOOD / name if name in GOOD_PROJECTS else BAD / name
            self.assertTrue((base / "main.rl").is_file(), name)
        for name in ("manifest_pinned",):
            self.assertTrue((GOOD / name / "rlmod.json").is_file(), name)
        for name in ("bad_manifest", "edition_mismatch", "pin_mismatch", "unpinned_with_manifest"):
            self.assertTrue((BAD / name / "rlmod.json").is_file(), name)

    def test_02_std_conformance_covers_every_function(self):
        # The RFC leaves exact std contents to the implementation but
        # requires a conformance program exercising every function.
        main = (GOOD / "std_use" / "main.rl").read_text(encoding="utf-8")
        for stem, alias in sorted(STD_ALIASES.items()):
            for func in _std_functions(stem):
                self.assertIn(f"{alias}::{func}(", main, f"std/{stem}.rl::{func}")


class ModuleAcceptTests(unittest.TestCase):
    def test_03_good_projects_analyze(self):
        for name in sorted(GOOD_PROJECTS):
            with self.subTest(good=name):
                program, error = mod.analyze_entry(GOOD / name / "main.rl")
                self.assertIsNone(error, error)
                self.assertIsNotNone(program)

    def test_04_good_projects_build_and_verify(self):
        for name in sorted(GOOD_PROJECTS):
            with self.subTest(good=name):
                program, error = mod.analyze_entry(GOOD / name / "main.rl")
                self.assertIsNone(error, error)
                module, error = rir.build_rir(program, f"{name}/main.rl")
                self.assertIsNone(error, error)
                self.assertEqual(rir.verify_module(module), [])
                asm_text, error = mod.compile_entry(GOOD / name / "main.rl")
                self.assertIsNone(error, error)
                self.assertTrue(asm_text)

    def test_05_basic_import_mangles_across_the_boundary(self):
        # Positive pin: the dep's names arrive mangled and the entry's
        # qualified references resolve to the same stored names.
        program, error = mod.analyze_entry(GOOD / "basic_import" / "main.rl")
        self.assertIsNone(error, error)
        names = {fn["name"] for fn in program["functions"]}
        self.assertIn("calc__double", names)
        self.assertIn("calc__make_pair", names)
        self.assertIn("main", names)
        self.assertNotIn("double", names)
        self.assertEqual({rec["name"] for rec in program.get("records", [])}, {"calc__Pair"})


class ModuleRejectTests(unittest.TestCase):
    def test_06_bad_projects_reject_with_exact_code(self):
        for name, code in sorted(BAD_CODES.items()):
            with self.subTest(bad=name):
                program, error = mod.analyze_entry(BAD / name / "main.rl")
                self.assertIsNone(program)
                self.assertIsNotNone(error)
                self.assertEqual(error["code"], code, error)

    def test_07_no_main_fails_at_entry_not_at_load(self):
        program, error = mod.analyze_entry(BAD / "no_main" / "main.rl")
        self.assertIsNone(error, error)
        self.assertIsNotNone(program)
        asm_text, error = mod.compile_entry(BAD / "no_main" / "main.rl")
        self.assertIsNone(asm_text)
        self.assertEqual(error["code"], "COMP_NO_ENTRY")


class ModuleHonestyTests(unittest.TestCase):
    def _pin_entry(self, project):
        manifest = json.loads((project / "rlmod.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), {"modules"})
        return manifest["modules"]["lib/answer.rl"]

    def test_08_manifest_fixtures_fail_for_the_named_reason(self):
        # Pinned-good hash matches the file (the pin path is exercised);
        # the edition fixture differs ONLY in edition; the hash fixture
        # differs ONLY in hash; the strict fixture pins another file.
        actual = hashlib.sha256(
            (GOOD / "manifest_pinned" / "lib" / "answer.rl").read_bytes()).hexdigest()
        good_pin = self._pin_entry(GOOD / "manifest_pinned")
        self.assertEqual((good_pin["edition"], good_pin["sha256"]), ("v1", actual))
        edition_pin = self._pin_entry(BAD / "edition_mismatch")
        self.assertEqual(edition_pin["sha256"], actual)
        self.assertNotEqual(edition_pin["edition"], "v1")
        mismatch_pin = self._pin_entry(BAD / "pin_mismatch")
        self.assertNotEqual(mismatch_pin["sha256"], actual)
        strict = json.loads((BAD / "unpinned_with_manifest" / "rlmod.json").read_text(encoding="utf-8"))
        self.assertNotIn("lib/answer.rl", strict["modules"])

    def test_09_cycle_messages_are_relative(self):
        _, error = mod.analyze_entry(BAD / "cycle" / "main.rl")
        self.assertEqual(error["code"], "MOD_CYCLE")
        self.assertEqual(error["message"], "import cycle: main.rl -> b.rl -> main.rl")
        _, error = mod.analyze_entry(BAD / "self_import" / "main.rl")
        self.assertEqual(error["code"], "MOD_CYCLE")
        self.assertEqual(error["message"], "import cycle: main.rl -> main.rl")


class ModuleDeterminismTests(unittest.TestCase):
    def test_10_corpus_byte_identical_3x(self):
        for name in sorted(GOOD_PROJECTS):
            with self.subTest(good=name):
                entry = GOOD / name / "main.rl"
                program, error = mod.analyze_entry(entry)
                self.assertIsNone(error, error)
                module, error = rir.build_rir(program, f"{name}/main.rl")
                self.assertIsNone(error, error)
                first_text = rir.dumps(module)
                for _ in range(2):
                    again, error = mod.analyze_entry(entry)
                    self.assertIsNone(error, error)
                    module2, error2 = rir.build_rir(again, f"{name}/main.rl")
                    self.assertIsNone(error2, error2)
                    self.assertEqual(rir.dumps(module2), first_text)


@unittest.skipUnless(TOOLCHAIN, "native execution unavailable (nasm + linker required)")
class ModuleDifferentialTests(unittest.TestCase):
    def _differential(self, name):
        from tools.rynorlang import program as progmod
        entry = GOOD / name / "main.rl"
        program, error = mod.analyze_entry(entry)
        self.assertIsNone(error, error)
        module, error = rir.build_rir(program, f"{name}/main.rl")
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])
        emitted: list = []
        outcome = oracle.run_rir(module, out=emitted)
        self.assertIsNone(outcome["trapped"], outcome)
        work = Path(tempfile.mkdtemp(prefix="mod-diff-"))
        try:
            arts, error = progmod.build_module_program(entry, work, prog="prog")
            self.assertIsNone(error, error)
            result, error = progmod.run_program(arts["exe"])
            self.assertIsNone(error, error)
            self.assertIsNone(result["signal"], result)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.assertEqual(result["exit"], outcome["exit"])
        self.assertEqual(result["stdout"].decode("ascii"), "".join(emitted))
        return result["exit"], "".join(emitted)

    def test_11_differentials_exit_and_stdout(self):
        for name in sorted(GOOD_PROJECTS):
            with self.subTest(good=name):
                self._differential(name)

    def test_12_golden_exit_and_stdout(self):
        for name, want in sorted(GOLDEN.items()):
            with self.subTest(good=name):
                self.assertEqual(self._differential(name), want)


class ModuleMutationTests(unittest.TestCase):
    def _assert_removal_flips_to_ok(self, path, old, new, project):
        _, error = mod.analyze_entry(project / "main.rl")
        self.assertIsNotNone(error)
        mutant = _load_mutant(path, old, new)
        _, mutant_error = mutant.analyze_entry(project / "main.rl")
        self.assertIsNone(mutant_error, f"removed check still rejected input: {mutant_error}")

    def test_13_pin_skip_accepts_unpinned(self):
        self._assert_removal_flips_to_ok(
            MODULE_PATH,
            "    error = _check_pins(files, pins)",
            "    error = None",
            BAD / "unpinned_with_manifest")

    def test_14_edition_ignored_accepts_v9(self):
        self._assert_removal_flips_to_ok(
            MODULE_PATH,
            "        if edition not in KNOWN_EDITIONS:",
            "        if False and edition not in KNOWN_EDITIONS:",
            BAD / "edition_mismatch")

    def test_15_alias_check_weakened_accepts_dashes(self):
        self._assert_removal_flips_to_ok(
            MODULE_PATH,
            "                if not _is_alias(stem):",
            "                if False and not _is_alias(stem):",
            BAD / "bad_alias_stem")

    def test_16_shared_dep_memo_removed_duplicates(self):
        program, error = mod.analyze_entry(GOOD / "diamond" / "main.rl")
        self.assertIsNone(error, error)
        mutant = _load_mutant(
            MODULE_PATH,
            "        if key in files:\n            return None",
            "        if False and key in files:\n            return None")
        _, mutant_error = mutant.analyze_entry(GOOD / "diamond" / "main.rl")
        self.assertIsNotNone(mutant_error)
        self.assertEqual(mutant_error["code"], "MOD_DUPLICATE")

    def test_17_load_collision_removed_falls_through_to_analyzer(self):
        # The load scan fires first (MOD_DUPLICATE naming the squat);
        # removing it exposes the analyzer's seed reservation as
        # backstop (SEM_DUPLICATE) — both layers proven, order pinned.
        _, error = mod.analyze_entry(BAD / "mangle_collision" / "main.rl")
        self.assertIsNotNone(error)
        self.assertEqual(error["code"], "MOD_DUPLICATE")
        self.assertIn("calc__double", error["message"])
        mutant = _load_mutant(
            MODULE_PATH,
            "    error = _check_mangle_collisions(files)",
            "    error = None")
        _, mutant_error = mutant.analyze_entry(BAD / "mangle_collision" / "main.rl")
        self.assertIsNotNone(mutant_error)
        self.assertEqual(mutant_error["code"], "SEM_DUPLICATE")

    def test_18_stored_identity_breaks_qualified_types(self):
        program, error = mod.analyze_entry(GOOD / "basic_import" / "main.rl")
        self.assertIsNone(error, error)
        _, mutant_error = _analyze_mutant_entry(
            "        if self.self_alias is None:\n            return bare",
            "        if True:\n            return bare",
            GOOD / "basic_import")
        self.assertIsNotNone(mutant_error)
        self.assertEqual(mutant_error["code"], "SEM_UNDECLARED")


if __name__ == "__main__":
    unittest.main()
