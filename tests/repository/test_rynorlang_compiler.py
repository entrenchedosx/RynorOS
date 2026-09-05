"""Stage 15a compiler tests: emission, native execution, differential oracle, CLI.

Conventions: GOOD/TRAP fixture inventories are exact (missing/extra fixture
files fail); native execution is capability-gated (nasm + ld.lld + an ELF
runner) with an explicit skip reason, and a dedicated test pins the gate
itself; exit codes are 8-bit so full-width arithmetic is proven through
boolean ballots.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RIR_PATH = ROOT / "tools" / "rynorlang" / "rir.py"
COMPILE_PATH = ROOT / "tools" / "rynorlang" / "compile.py"
INTERP_PATH = ROOT / "tools" / "rynorlang" / "interp.py"
HARNESS_ASM = ROOT / "tools" / "rynorlang" / "harness_start.asm"
GOOD = ROOT / "tests" / "fixtures" / "rynorlang" / "compiler" / "good"
TRAP = ROOT / "tests" / "fixtures" / "rynorlang" / "compiler" / "trap"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rir = _load("rynorlang_stage15_rir_c", RIR_PATH)
compiler = _load("rynorlang_stage15_compile", COMPILE_PATH)
interp = _load("rynorlang_stage15_interp", INTERP_PATH)
sys.path.insert(0, str(ROOT))
from tools.rynorlang import analyze as _analyzer

EXPECTED_GOOD = {
    "arith.rl": 19, "boolexit.rl": 1, "boolops.rl": 1, "fib10.rl": 55,
    "loopret.rl": 7,
    "main42.rl": 42, "negdiv.rl": 1, "negmod.rl": 1,
    "nestedloop.rl": 2, "sevenargs.rl": 28, "strarg.rl": 2,
    "stackmix.rl": 42, "strboundary.rl": 42, "strcmpeq.rl": 1,
    "strreturn.rl": 2, "unitmain.rl": 0, "wrapballot.rl": 1,
}
EXPECTED_TRAP = {"divzero.rl": 132, "falloff.rl": 133, "intmin.rl": 136}
# Exit observations are meaningful only below 128: Linux exit codes are 8
# bits, and the WSL runner reports signals in the same 128+N band that real
# exits 128..255 occupy, so value fixtures stay in 0..127 (full-width
# arithmetic is proven through boolean ballots instead). Trap expectations
# above are 128+SIGILL/SIGTRAP/SIGFPE.

MAIN42_ASM = """bits 64
default rel
section .note.GNU-stack noalloc noexec no progbits

section .text
global rl_4_main
rl_4_main:
    push rbp
    mov rbp, rsp
    sub rsp, 16
rl_4_main_bb0:
    mov rax, 42
    mov [rbp - 8], rax
    mov rax, [rbp - 8]
    leave
    ret

"""


def _find_tool(name, override):
    candidate = os.environ.get(override, name)
    return shutil.which(candidate)


def _native_capability():
    """Return (nasm, ld, runner) or (None, reason). Runner is a callable
    mapping an ELF path to a subprocess.CompletedProcess."""
    nasm = _find_tool("nasm", "RYNOR_NASM")
    if nasm is None:
        return None, "nasm not found (set RYNOR_NASM)"
    if os.name == "posix":
        linker = _find_tool("ld.lld", "RYNOR_LLD")
        if linker is None:
            return None, "ld.lld not found (set RYNOR_LLD)"
        def run_posix(path, timeout):
            return subprocess.run([str(path)], capture_output=True, timeout=timeout)
        return (nasm, linker, run_posix), None
    wsl = shutil.which("wsl")
    if wsl is not None:
        probe = subprocess.run(
            [wsl, "-d", "archlinux", "true"],
            capture_output=True, timeout=60)
        if probe.returncode == 0:
            probe_linker = subprocess.run(
                [wsl, "-d", "archlinux", "sh", "-lc", "command -v ld.lld"],
                capture_output=True, text=True, timeout=60)
            if probe_linker.returncode != 0 or not probe_linker.stdout.strip():
                return None, "WSL archlinux ld.lld not found"
            wsl_linker = probe_linker.stdout.strip()

            def to_wsl(path):
                text = str(path).replace("\\", "/")
                if len(text) > 1 and text[1] == ":":
                    text = "/mnt/" + text[0].lower() + text[2:]
                return text

            def run_wsl(path, timeout, _wsl=wsl):
                waiter = ("import os,sys\n"
                          "pid=os.fork()\n"
                          "if pid==0:\n"
                          " os.execv(sys.argv[1],[sys.argv[1]])\n"
                          "_,status=os.waitpid(pid,0)\n"
                          "if os.WIFSIGNALED(status):\n"
                          " print('SIGNAL',os.WTERMSIG(status))\n"
                          "else:\n"
                          " print('EXIT',os.WEXITSTATUS(status))\n")
                proc = subprocess.run(
                    [_wsl, "-d", "archlinux", "python3", "-c", waiter,
                     to_wsl(path)], capture_output=True, text=True,
                    timeout=timeout)
                marker = proc.stdout.strip().splitlines()[-1:] or [""]
                parts = marker[0].split()
                if len(parts) != 2 or parts[0] not in ("EXIT", "SIGNAL"):
                    raise RuntimeError("WSL wait-status probe failed: " +
                                       (proc.stderr or proc.stdout)[-500:])
                code = int(parts[1])
                return subprocess.CompletedProcess(
                    proc.args, -code if parts[0] == "SIGNAL" else code,
                    stdout=proc.stdout, stderr=proc.stderr)

            def link_wsl(output, inputs, _wsl=wsl, _linker=wsl_linker):
                return subprocess.run(
                    [_wsl, "-d", "archlinux", _linker, "-o", to_wsl(output),
                     *(to_wsl(path) for path in inputs), "--build-id=none"],
                    capture_output=True, text=True, timeout=120)

            return (nasm, link_wsl, run_wsl), None
        return None, "wsl archlinux distro not usable"
    return None, "no ELF runner (posix host or wsl archlinux required)"


def _run_native(tools, binary, timeout=60):
    _, _, runner = tools
    proc = runner(binary, timeout)
    code = proc.returncode
    if code is not None and code < 0:
        return ("signal", -code)
    return ("exit", code)


def _oracle_exit(module):
    outcome = interp.run_rir(module)
    if outcome["trapped"] is not None:
        return ("trap", outcome["trapped"])
    return ("exit", outcome["exit"] & 0xFF)


class CompilerLayoutTests(unittest.TestCase):
    def test_01_files_exist(self):
        for path in (RIR_PATH, COMPILE_PATH, INTERP_PATH, HARNESS_ASM):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)

    def test_02_public_api_exists(self):
        for name in ("emit_asm", "compile_source", "check_asm", "COMP_NO_ENTRY", "main"):
            self.assertTrue(hasattr(compiler, name), f"compile.{name}")
        for name in ("run_rir", "OracleRefused", "STEP_LIMIT", "CALL_LIMIT"):
            self.assertTrue(hasattr(interp, name), f"interp.{name}")

    def test_03_oracle_honesty_markers(self):
        for path, markers in (
            (INTERP_PATH, ("TEST-ONLY", "MUST NEVER ship", "Honesty rules", "oracle alone never closes")),
        ):
            text = path.read_text(encoding="utf-8")
            for marker in markers:
                with self.subTest(marker=marker):
                    self.assertIn(marker, text)
        interp_source = INTERP_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import compile", interp_source)
        self.assertNotIn("from tools.rynorlang.compile", interp_source)
        self.assertNotIn("from .compile", interp_source)

    def test_04_fixture_inventory_is_exact(self):
        self.assertEqual({p.name for p in GOOD.iterdir() if p.is_file()},
                         set(EXPECTED_GOOD))
        self.assertEqual({p.name for p in TRAP.iterdir() if p.is_file()},
                         set(EXPECTED_TRAP))
        # Exit observations are 8-bit and the runner reports signals in the
        # 128+N band: value fixtures must stay below 128 (full-width proofs
        # use boolean ballots), or exits and signals become indistinguishable.
        for name, want in EXPECTED_GOOD.items():
            with self.subTest(fixture=name):
                self.assertGreaterEqual(want, 0)
                self.assertLess(want, 128)

    def test_05_capability_gate_reports(self):
        tools, reason = _native_capability()
        if tools is None:
            self.skipTest(f"no native toolchain: {reason}")
        nasm, linker, runner = tools
        self.assertTrue(Path(nasm).is_file() or shutil.which(nasm))
        self.assertTrue(callable(linker) or Path(linker).is_file() or shutil.which(linker))
        self.assertTrue(callable(runner))


class CompilerEmissionTests(unittest.TestCase):
    def _asm(self, src, name="t.rl"):
        asm, error = compiler.compile_source(src, name)
        self.assertIsNone(error, error)
        self.assertEqual(compiler.check_asm(asm), [])
        return asm

    def test_10_main42_exact_asm(self):
        self.assertEqual(self._asm("fn main(): int { return 42; }"), MAIN42_ASM)

    def test_11_prologue_epilogue_shapes(self):
        asm = self._asm((GOOD / "sevenargs.rl").read_text(encoding="utf-8"))
        self.assertIn("    push rbp\n    mov rbp, rsp\n", asm)
        # Param homes are written before use; every function ends its blocks.
        self.assertIn("    leave\n    ret\n", asm)
        # Frame is 16-aligned: sub rsp, N with N % 16 == 0 wherever present.
        import re
        for match in re.finditer(r"sub rsp, (\d+)", asm):
            self.assertEqual(int(match.group(1)) % 16, 0)

    def test_12_no_forbidden_constructs_in_corpus(self):
        for name in sorted(set(EXPECTED_GOOD) | set(EXPECTED_TRAP)):
            directory = GOOD if name in EXPECTED_GOOD else TRAP
            with self.subTest(fixture=name):
                asm = self._asm((directory / name).read_text(encoding="utf-8"), name)
                self.assertEqual(compiler.check_asm(asm), [])

    def test_13_postcheck_flags_injected_abuse(self):
        bad_lines = [
            "    syscall",
            "    call rax",
            "    jmp [rbp - 8]",
            "    movaps xmm0, xmm1",
            "    in eax, dx",
            "    mov rbx, rax",
        ]
        for line in bad_lines:
            with self.subTest(line=line):
                self.assertTrue(compiler.check_asm("bits 64\n" + line + "\n"))

    def test_14_direct_calls_only(self):
        asm = self._asm((GOOD / "fib10.rl").read_text(encoding="utf-8"))
        import re
        for match in re.finditer(r"call\s+(\S+)", asm):
            self.assertTrue(match.group(1).startswith("rl_"))


class CompilerCliTests(unittest.TestCase):
    def _run_cli(self, argv, source_text=None):
        directory = tempfile.TemporaryDirectory(prefix="rlc-cli-")
        self.addCleanup(directory.cleanup)
        args = list(argv)
        if source_text is not None:
            path = Path(directory.name) / "prog.rl"
            path.write_text(source_text, encoding="ascii")
            args.append(str(path))
        proc = subprocess.run([sys.executable, str(COMPILE_PATH), *args],
                              capture_output=True, text=True, timeout=60,
                              cwd=str(ROOT))
        return proc

    def test_20_asm_is_default(self):
        proc = self._run_cli([], "fn main(): int { return 1; }")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rl_4_main:", proc.stdout)
        explicit = self._run_cli(["--asm"], "fn main(): int { return 1; }")
        self.assertEqual(explicit.stdout, proc.stdout)

    def test_21_rir_mode(self):
        proc = self._run_cli(["--rir"], "fn main(): int { return 1; }")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("; rir_version=1", proc.stdout)
        self.assertIn("ret %0", proc.stdout)

    def test_22_exit_codes(self):
        proc = self._run_cli([], "fn main(): int { return true; }")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("rl_main:", proc.stdout)
        proc = self._run_cli([], "fn main(): int { return 1; ")
        self.assertEqual(proc.returncode, 1)
        proc = self._run_cli([])
        self.assertEqual(proc.returncode, 2)
        missing = Path(tempfile.gettempdir()) / "rynor-missing-xyz.rl"
        proc = self._run_cli([str(missing)])
        self.assertEqual(proc.returncode, 2)

    def test_23_cli_determinism(self):
        src = (GOOD / "fib10.rl").read_text(encoding="utf-8")
        directory = tempfile.TemporaryDirectory(prefix="rlc-det-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "prog.rl"
        path.write_text(src, encoding="ascii")

        def run_cli(argv):
            return subprocess.run(
                [sys.executable, str(COMPILE_PATH), *argv, str(path)],
                capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        outs = [run_cli(["--asm"]).stdout for _ in range(3)]
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[1], outs[2])
        rirs = [run_cli(["--rir"]).stdout for _ in range(2)]
        self.assertEqual(rirs[0], rirs[1])


class CompilerNegativeTests(unittest.TestCase):
    def test_30_semantic_failures_emit_nothing(self):
        for src in (
            "fn main(): int { return true; }",
            "fn main(): int { return nope(); }",
            "fn main(): int { let x: int = 1; let x: int = 2; return x; }",
            "fn main(): int { return 1 / 0; }",  # valid program: emits trap, tested natively
        ):
            with self.subTest(src=src[:40]):
                asm, error = compiler.compile_source(src, "bad.rl")
                if "1 / 0" in src:
                    self.assertIsNone(error, error)
                    self.assertIn("ud2", asm)
                else:
                    self.assertIsNone(asm)
                    self.assertTrue(error["code"].startswith(("SEM_", "PAR_")))

    def test_31_no_main_is_entry_error(self):
        for src in (
            "fn f(): int { return 1; }",
            "fn main(a: int): int { return a; }",
            'fn main(): str { return "x"; }',
        ):
            with self.subTest(src=src[:40]):
                asm, error = compiler.compile_source(src, "noentry.rl")
                self.assertIsNone(asm)
                self.assertEqual(error["code"], "COMP_NO_ENTRY")

    def test_32_unverified_rir_is_refused(self):
        module = {"rir_version": 1, "source": "x", "strtab": [], "funcs": []}
        with self.assertRaises(ValueError):
            compiler.emit_asm(module)
        module = {"rir_version": 999, "source": "x", "strtab": [], "funcs": []}
        with self.assertRaises(ValueError):
            compiler.emit_asm(module)

    def test_33_malformed_input_never_crashes(self):
        for bad in (None, [], {}, "fn main() {}",
                    {"rir_version": 1, "source": "x", "strtab": "nope", "funcs": []}):
            with self.subTest(bad=str(bad)[:30]):
                try:
                    compiler.emit_asm(bad)
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
                else:
                    self.fail(f"emit_asm silently accepted {bad!r}")


def _assemble_link_run(testcase, tools, workdir, name, asm):
    """Assemble + link given asm text, run it, return _run_native result."""
    workdir = Path(workdir)
    (workdir / "start.asm").write_bytes(HARNESS_ASM.read_bytes())
    (workdir / f"{name}.asm").write_text(asm, encoding="ascii")
    nasm, linker, _ = tools
    for stem in ("start", name):
        proc = subprocess.run(
            [nasm, "-f", "elf64", str(workdir / f"{stem}.asm"),
             "-o", str(workdir / f"{stem}.o")],
            capture_output=True, text=True, timeout=120)
        testcase.assertEqual(proc.returncode, 0, proc.stderr[-500:])
    binary = workdir / name
    inputs = [workdir / "start.o", workdir / f"{name}.o"]
    if callable(linker):
        proc = linker(binary, inputs)
    else:
        proc = subprocess.run(
            [linker, "-o", str(binary), *(str(path) for path in inputs),
             "--build-id=none"], capture_output=True, text=True, timeout=120)
    testcase.assertEqual(proc.returncode, 0, proc.stderr[-500:])
    return _run_native(tools, binary)


class CompilerNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tools, reason = _native_capability()
        if tools is None:
            raise unittest.SkipTest(f"native execution unavailable: {reason}")
        cls.tools = tools
        cls.workdir = tempfile.TemporaryDirectory(prefix="rl-native-")
        (Path(cls.workdir.name) / "start.asm").write_bytes(HARNESS_ASM.read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.workdir.cleanup()

    def _assemble_link_run(self, tools, workdir, name, asm):
        return _assemble_link_run(self, tools, workdir, name, asm)

    def _execute(self, src, name):
        asm, error = compiler.compile_source(src, name)
        self.assertIsNone(error, error)
        return self._assemble_link_run(
            self.tools, Path(self.workdir.name), name, asm)

    def _oracle(self, src, name):
        result = _analyzer.analyze(src, name)
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, name)
        self.assertIsNone(error, error)
        self.assertEqual(rir.verify_module(module), [])
        return module

    def test_40_exit_fixtures_agree_with_oracle(self):
        for name, want in sorted(EXPECTED_GOOD.items()):
            with self.subTest(fixture=name):
                src = (GOOD / name).read_text(encoding="utf-8")
                module = self._oracle(src, name)
                oracle = interp.run_rir(module)
                self.assertIsNone(oracle["trapped"], oracle)
                kind, native = self._execute(src, name.replace(".", "_"))
                self.assertEqual(kind, "exit")
                self.assertEqual(native, want)
                self.assertEqual(native, oracle["exit"] & 0xFF)

    def test_41_trap_fixtures_raise_signals(self):
        for name, want_rc in sorted(EXPECTED_TRAP.items()):
            with self.subTest(fixture=name):
                src = (TRAP / name).read_text(encoding="utf-8")
                module = self._oracle(src, name)
                oracle = interp.run_rir(module)
                self.assertIsNotNone(oracle["trapped"], oracle)
                kind, native = self._execute(src, name.replace(".", "_"))
                self.assertEqual(kind, "signal")
                want_sig = want_rc - 128
                self.assertEqual(native, want_sig)

    def test_42_probe_program_outside_fixtures_runs(self):
        # The harness executes the produced binary, not a fixture allowlist:
        # this probe exists nowhere in the suite.
        src = "fn sq(n: int): int { return n * n - 2 * n + 1; } fn main(): int { return sq(9); }"
        module = self._oracle(src, "probe.rl")
        oracle = interp.run_rir(module)
        self.assertEqual(oracle["exit"], 64)
        kind, native = self._execute(src, "probe")
        self.assertEqual((kind, native), ("exit", 64))

    def test_43_trap_categories_match_oracle(self):
        pairs = [
            ("fn main(): int { return 1 / 0; }", "div0"),
            ("fn f(x: int): int { if x > 0 { return 1; } } fn main(): int { return f(0 - 5); }",
             "falloff"),
        ]
        for src, category in pairs:
            with self.subTest(category=category):
                module = self._oracle(src, "trapcat.rl")
                oracle = interp.run_rir(module)
                self.assertEqual(oracle["trapped"], category)
                kind, native = self._execute(src, f"trapcat_{category}")
                self.assertEqual(kind, "signal")
                self.assertEqual(native, 4 if category == "div0" else 5)

    def test_44_nested_control_fallthrough_matches_source(self):
        cases = (
            ("fn main(): int { if true { if false { return 1; } } return 2; }", 3),
            ("fn main(): int { { if false { } } return 2; }", 2),
        )
        for index, (src, steps) in enumerate(cases):
            with self.subTest(case=index):
                module = self._oracle(src, "nested-tail.rl")
                oracle = interp.run_rir(module)
                self.assertEqual(oracle, {"exit": 2, "trapped": None,
                                          "steps": steps})
                self.assertEqual(self._execute(src, f"nested_tail_{index}"),
                                 ("exit", 2))

    def test_45_nonlayout_cfg_slot_liveness_matches_oracle(self):
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
        _, count = rir.assign_slots(
            blocks, {"%0": "int", "%1": "bool", "%2": "int"}, 0)
        module = {"rir_version": 1, "source": "cfg-live.rir", "strtab": [],
                  "funcs": [{"name": "main", "symbol": 0, "params": [],
                             "ret": "int", "blocks": blocks,
                             "frameslots": count}]}
        self.assertEqual(interp.run_rir(module)["exit"], 7)
        asm = compiler.emit_asm(module)
        self.assertEqual(self._assemble_link_run(
            self.tools, Path(self.workdir.name), "cfg_live", asm), ("exit", 7))

    def test_46_function_name_cannot_collide_with_generated_label(self):
        src = ("fn main_bb0(): int { return 1; } "
               "fn main(): int { return main_bb0(); }")
        asm, error = compiler.compile_source(src, "label-collision.rl")
        self.assertIsNone(error, error)
        labels = [line for line in asm.splitlines() if line.endswith(":")]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(self._assemble_link_run(
            self.tools, Path(self.workdir.name), "label_collision", asm),
            ("exit", 1))

    def test_47_high_exit_is_not_misclassified_as_signal(self):
        self.assertEqual(self._execute(
            "fn main(): int { return 132; }", "exit_132"), ("exit", 132))


class CompilerMutationTests(unittest.TestCase):
    def _mutant(self, old, new, module="compile"):
        import tempfile
        path = COMPILE_PATH if module == "compile" else RIR_PATH
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, f"anchor must be unique: {old!r}")
        directory = tempfile.TemporaryDirectory(prefix="compiler-mutant-")
        self.addCleanup(directory.cleanup)
        target = Path(directory.name) / path.name
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        name = f"compiler_mutant_{len(sys.modules)}"
        return _load(name, target)

    def _differential(self, backend, src, name="mut.rl"):
        result = _analyzer.analyze(src, name)
        self.assertTrue(result.ok, result.diagnostic)
        module, error = rir.build_rir(result.ast, name)
        self.assertIsNone(error, error)
        asm, error = backend.compile_source(src, name)
        self.assertIsNone(error, error)
        return interp.run_rir(module), asm

    def test_50_opcode_flip_detected(self):
        mutant = self._mutant('            self.out("    add rax, rcx")',
                              '            self.out("    sub rax, rcx")')
        oracle, asm = self._differential(
            mutant, "fn main(): int { return 6 + 7; }")
        self.assertEqual(oracle["exit"], 13)
        self.assertNotIn("add rax, rcx", asm)
        # The flip is caught because behavior diverges from the oracle.

    def test_51_alignment_break_detected(self):
        # main42 needs exactly 1 slot: the mutant emits `sub rsp, 8`,
        # breaking the 16-byte pre-call invariant the real emitter keeps.
        mutant = self._mutant("        frame = (self.frameslots * 8 + 15) // 16 * 16",
                              "        frame = self.frameslots * 8")
        asm, error = mutant.compile_source("fn main(): int { return 42; }", "m.rl")
        self.assertIsNone(error, error)
        import re
        matches = re.findall(r"sub rsp, (\d+)", asm)
        self.assertEqual(matches, ["8"],
                         "mutant must emit the unaligned frame")

    def test_52_return_register_break_detected(self):
        # The harness reads RAX; ret emission must load RAX. The probe keeps
        # a stale value in RAX (7) while returning a different home (42), so
        # a backend that loads any other register visibly diverges.
        # Without native capability, prove structurally that the mutation
        # took effect (and any native run would diverge).
        mutant = self._mutant('            self.load_reg("rax", value)',
                              '            self.load_reg("rbx", value)')
        probe = "fn main(): int { let good: int = 42; let junk: int = 7; return good; }"
        oracle, asm = self._differential(mutant, probe)
        self.assertEqual(oracle["exit"], 42)
        self.assertIn("mov rbx,", asm)
        tools, _ = _native_capability()
        if tools is None:
            self.skipTest("native execution unavailable; structural evidence only")
        workdir = Path(tempfile.mkdtemp(prefix="rl-mut52-"))
        self.addCleanup(shutil.rmtree, str(workdir), True)
        kind, code = _assemble_link_run(self, tools, workdir, "mut52", asm)
        # RAX still holds 7 from the junk computation: exit is 7, never 42.
        self.assertEqual((kind, code), ("exit", 7))

    def test_53_arg_order_swap_detected(self):
        # Reversed marshaling passes (4,20) instead of (20,4): the mutant
        # must compute 4/20 = 0, never 5. Both outcomes stay below 128 so
        # the exit/signal mapping cannot confuse them.
        mutant = self._mutant("        for arg in args:",
                              "        for arg in reversed(args):")
        oracle, asm = self._differential(
            mutant, "fn dv(a: int, b: int): int { return a / b; } "
            "fn main(): int { return dv(20, 4); }")
        self.assertEqual(oracle["exit"], 5)
        tools, _ = _native_capability()
        if tools is None:
            self.skipTest("native execution unavailable; oracle prediction only")
        workdir = Path(tempfile.mkdtemp(prefix="rl-mut53-"))
        self.addCleanup(shutil.rmtree, str(workdir), True)
        kind, code = _assemble_link_run(self, tools, workdir, "mut53", asm)
        self.assertEqual((kind, code), ("exit", 0))

    def test_54_hardcoded_return_detected(self):
        text = COMPILE_PATH.read_text(encoding="utf-8")
        anchor = "    def _emit_term(self, func: dict, block: dict, term: dict) -> None:"
        self.assertEqual(text.count(anchor), 1)
        import tempfile
        directory = tempfile.TemporaryDirectory(prefix="compiler-mutant-")
        self.addCleanup(directory.cleanup)
        target = Path(directory.name) / COMPILE_PATH.name
        target.write_text(text.replace(
            '            if value is not None:\n                self.load_reg("rax", value)',
            '            if value is not None:\n                self.out("    mov rax, 42")',
            1), encoding="utf-8")
        mutant = _load(f"compiler_mutant_{len(sys.modules)}", target)
        oracle, asm = self._differential(
            mutant, "fn main(): int { return 7; }")
        self.assertEqual(oracle["exit"], 7)
        self.assertIn("mov rax, 42", asm)

    def test_55_canned_assembly_detected(self):
        text = COMPILE_PATH.read_text(encoding="utf-8")
        anchor = "    def emit_module(self) -> str:"
        self.assertEqual(text.count(anchor), 1)
        import tempfile
        directory = tempfile.TemporaryDirectory(prefix="compiler-mutant-")
        self.addCleanup(directory.cleanup)
        target = Path(directory.name) / COMPILE_PATH.name
        canned = ('    def emit_module(self) -> str:\n'
                  '        return "bits 64\\ndefault rel\\nsection .text\\nglobal rl_4_main\\n'
                  'rl_4_main:\\n    push rbp\\n    mov rbp, rsp\\n    mov rax, 42\\n    leave\\n    ret\\n"\n')
        target.write_text(text.replace(anchor, canned, 1), encoding="utf-8")
        mutant = _load(f"compiler_mutant_{len(sys.modules)}", target)
        oracle, asm = self._differential(
            mutant, "fn main(): int { return 7; }")
        self.assertEqual(oracle["exit"], 7)
        self.assertNotIn("mov rax, 7", asm)

    def test_56_ignored_rir_detected(self):
        # A backend that ignores its RIR input cannot track two programs.
        got = {}
        for src in ("fn main(): int { return 1; }", "fn main(): int { return 2; }"):
            asm, error = compiler.compile_source(src, "m.rl")
            self.assertIsNone(error, error)
            got[src] = asm
        self.assertNotEqual(got["fn main(): int { return 1; }"],
                            got["fn main(): int { return 2; }"])

    def test_57_wrong_source_acceptance_detected(self):
        # compile_source must surface analyzer diagnostics, never emit code.
        for src, code in (
            ("fn main(): int { return true; }", "SEM_TYPE_MISMATCH"),
            ("fn main(): int { return nope(); }", "SEM_UNKNOWN_FUNCTION"),
            ("fn main(): int { return 1 / 0; }", None),  # valid: emits trap
        ):
            with self.subTest(src=src[:40]):
                asm, error = compiler.compile_source(src, "bad.rl")
                if code is None:
                    self.assertIsNone(error, error)
                    self.assertIn("ud2", asm)
                else:
                    self.assertIsNone(asm)
                    self.assertEqual(error["code"], code)

    def test_58_branch_swap_detected(self):
        # Swapping br targets inverts every condition: `if true` must then
        # take the else path. Proved by execution when capable.
        mutant = self._mutant('            self.out(f\'    jnz {label}_{term["then"]}\')\n'
                              '            self.out(f\'    jmp {label}_{term["else"]}\')',
                              '            self.out(f\'    jnz {label}_{term["else"]}\')\n'
                              '            self.out(f\'    jmp {label}_{term["then"]}\')')
        oracle, asm = self._differential(
            mutant, "fn main(): int { if true { return 1; } return 0; }")
        self.assertEqual(oracle["exit"], 1)
        tools, _ = _native_capability()
        if tools is None:
            self.skipTest("native execution unavailable; oracle prediction only")
        workdir = Path(tempfile.mkdtemp(prefix="rl-mut58-"))
        self.addCleanup(shutil.rmtree, str(workdir), True)
        kind, code = _assemble_link_run(self, tools, workdir, "mut58", asm)
        self.assertEqual((kind, code), ("exit", 0))

    def test_59_string_length_check_detected(self):
        # Content comparison first rejects unequal lengths. Without that
        # gate, `repe cmpsb` runs `lenA` iterations against a shorter `lenB`:
        # it reads out of bounds and can report a prefix as equal. The probe
        # is layout-determined: strtab first-use order places "b" immediately
        # before "bb", so the mutant compares 'b'=='b' and wrongly says equal.
        text = COMPILE_PATH.read_text(encoding="utf-8")
        anchor = '        self.out(f"    jne {base}_ne")'
        self.assertEqual(text.count(anchor), 1)
        asm, error = compiler.compile_source(
            'fn main(): int { if "b" == "bb" { return 1; } return 0; }', "s.rl")
        self.assertIsNone(error, error)
        self.assertIn("repe cmpsb", asm)
        import tempfile
        directory = tempfile.TemporaryDirectory(prefix="compiler-mutant-")
        self.addCleanup(directory.cleanup)
        target = Path(directory.name) / COMPILE_PATH.name
        target.write_text(text.replace(anchor, '        pass  # mutant: no length gate', 1),
                          encoding="utf-8")
        mutant = _load(f"compiler_mutant_{len(sys.modules)}", target)
        src = 'fn main(): int { if "b" == "bb" { return 1; } return 0; }'
        oracle, asm = self._differential(mutant, src)
        self.assertEqual(oracle["exit"], 0)
        tools, _ = _native_capability()
        if tools is None:
            self.skipTest("native execution unavailable; oracle prediction only")
        workdir = Path(tempfile.mkdtemp(prefix="rl-mut59-"))
        self.addCleanup(shutil.rmtree, str(workdir), True)
        kind, code = _assemble_link_run(self, tools, workdir, "mut59", asm)
        self.assertEqual((kind, code), ("exit", 1))

    def test_60_atomic_string_stack_offset_mutation_detected(self):
        mutant = self._mutant(
            "            k = stack_index * 8",
            "            k = (stack_index - len(_ARG_REGS)) * 8")
        src = (GOOD / "strboundary.rl").read_text(encoding="utf-8")
        oracle, asm = self._differential(mutant, src, "strboundary-mut.rl")
        self.assertEqual(oracle["exit"], 42)
        self.assertIn("mov [rsp + -48], rax", asm)
        tools, _ = _native_capability()
        if tools is None:
            self.skipTest("native execution unavailable; structural evidence only")
        workdir = Path(tempfile.mkdtemp(prefix="rl-mut60-"))
        self.addCleanup(shutil.rmtree, str(workdir), True)
        kind, code = _assemble_link_run(self, tools, workdir, "mut60", asm)
        self.assertNotEqual((kind, code), ("exit", 42))

    def test_61_callee_saved_scratch_mutation_detected(self):
        mutant = self._mutant(
            '        self.load_reg("rax", instr["l"])\n'
            '        self.load_reg("rcx", instr["r"])\n'
            '        if op == "+":',
            '        self.load_reg("rax", instr["l"])\n'
            '        self.load_reg("rbx", instr["r"])\n'
            '        if op == "+":')
        asm, error = mutant.compile_source(
            "fn main(): int { return 6 + 7; }", "rbx-mut.rl")
        self.assertIsNone(error, error)
        self.assertTrue(mutant.check_asm(asm))

    def test_62_unsigned_comparison_mutation_detected(self):
        mutant = self._mutant(
            'mapping = {"==": "sete", "!=": "setne", "<": "setl", ">": "setg",',
            'mapping = {"==": "sete", "!=": "setne", "<": "setb", ">": "setg",')
        src = "fn main(): int { if 0 - 1 < 0 { return 1; } return 0; }"
        oracle, asm = self._differential(mutant, src, "signed-mut.rl")
        self.assertEqual(oracle["exit"], 1)
        tools, _ = _native_capability()
        if tools is None:
            self.skipTest("native execution unavailable; oracle prediction only")
        workdir = Path(tempfile.mkdtemp(prefix="rl-mut62-"))
        self.addCleanup(shutil.rmtree, str(workdir), True)
        self.assertEqual(_assemble_link_run(
            self, tools, workdir, "mut62", asm), ("exit", 0))


if __name__ == "__main__":
    unittest.main()
