#!/usr/bin/env python3
"""Stage 16 host-native program pipeline: .rl -> RIR -> asm -> object -> ELF.

Host-side, Python 3.10+ standard library only. This module turns a verified
RynorLang program into a real host-native executable and runs it. It reuses
the frozen frontend (lex/parse/analyze), RIR builder/verifier, and NASM
backend, then assembles with NASM, links with LLD (plus the host program
runtime in tools/rynorlang/runtime/rt_linux.asm), and executes the result.

HOST BOOTSTRAP honesty: every artifact here is a host-native Linux x86-64
ELF for testing. It is NOT a RynorOS userspace program: no RynorOS syscall
interface exists yet (Stage 18b), so printing and startup use Linux syscalls
through the labeled runtime object. Nothing here ships in any RynorOS image.

All entry points return (value, None) or (None, {"code","message"}) and never
raise on expected failures (bad source, missing toolchain, assembler/linker
errors). Only programming errors (wrong Python types) raise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.rynorlang import rir as _rir
from tools.rynorlang import compile as _compile

RUNTIME_ASM = Path(__file__).resolve().parent / "runtime" / "rt_linux.asm"

COMP_TOOLCHAIN_MISSING = "COMP_TOOLCHAIN_MISSING"
COMP_ASSEMBLE_FAILED = "COMP_ASSEMBLE_FAILED"
COMP_LINK_FAILED = "COMP_LINK_FAILED"
COMP_NO_RUNTIME = "COMP_NO_RUNTIME"


def _find_tool(name: str, override: str):
    candidate = os.environ.get(override, name)
    return shutil.which(candidate)


def find_toolchain():
    """Return ((nasm, linker, runner), None) or (None, {"code","message"}).

    linker(inputs, output) assembles-links via (argv, to_guest) closures;
    runner(exe, timeout) executes and reports. The split mirrors the Stage
    15a native harness: Windows NASM assembles, WSL archlinux links+runs
    (or a POSIX host links+runs directly).
    """
    nasm = _find_tool("nasm", "RYNOR_NASM")
    if nasm is None:
        return None, {"code": COMP_TOOLCHAIN_MISSING,
                      "message": "nasm not found (set RYNOR_NASM or extend PATH)"}
    if not RUNTIME_ASM.is_file():
        return None, {"code": COMP_NO_RUNTIME,
                      "message": f"host runtime missing: {RUNTIME_ASM}"}
    if os.name == "posix":
        linker_bin = _find_tool("ld.lld", "RYNOR_LLD")
        if linker_bin is None:
            return None, {"code": COMP_TOOLCHAIN_MISSING,
                          "message": "ld.lld not found (set RYNOR_LLD or extend PATH)"}

        def link_posix(output, inputs, workdir):
            return subprocess.run([linker_bin, "-o", Path(output).name,
                                   *(Path(p).name for p in inputs), "--build-id=none"],
                                  capture_output=True, text=True, timeout=120,
                                  cwd=str(workdir))

        def run_posix(path, timeout):
            return subprocess.run([str(path)], capture_output=True, timeout=timeout)

        return (nasm, link_posix, run_posix), None
    wsl = shutil.which("wsl")
    if wsl is None:
        return None, {"code": COMP_TOOLCHAIN_MISSING,
                      "message": "no ELF runner (POSIX host or wsl archlinux required)"}
    try:
        probe = subprocess.run([wsl, "-d", "archlinux", "true"],
                               capture_output=True, timeout=60)
        if probe.returncode != 0:
            return None, {"code": COMP_TOOLCHAIN_MISSING,
                          "message": "wsl archlinux distro not usable"}
        probe_linker = subprocess.run(
            [wsl, "-d", "archlinux", "sh", "-lc", "command -v ld.lld"],
            capture_output=True, text=True, timeout=60)
        if probe_linker.returncode != 0 or not probe_linker.stdout.strip():
            return None, {"code": COMP_TOOLCHAIN_MISSING,
                          "message": "WSL archlinux ld.lld not found"}
        wsl_linker = probe_linker.stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return None, {"code": COMP_TOOLCHAIN_MISSING,
                      "message": f"toolchain probe failed: {error}"}

    def to_wsl(path):
        text = str(path).replace("\\", "/")
        if len(text) > 1 and text[1] == ":":
            text = "/mnt/" + text[0].lower() + text[2:]
        return text

    def link_wsl(output, inputs, workdir, _wsl=wsl, _linker=wsl_linker):
        # --cd plus basenames keeps the workdir path out of the linked
        # image (NASM records its input name in the object symbol table).
        return subprocess.run([_wsl, "-d", "archlinux", "--cd", to_wsl(workdir),
                               _linker, "-o", Path(output).name,
                               *(Path(p).name for p in inputs), "--build-id=none"],
                              capture_output=True, text=True, timeout=120)

    def run_wsl(path, timeout, _wsl=wsl):
        # The wait-status probe cannot separate the child's stdout from its
        # own marker lines, so the child redirects stdout to a sidecar file
        # (visible to Windows through /mnt/d) which is read back verbatim.
        out_path = str(path) + ".stdout"
        waiter = ("import os,sys\n"
                  "out=open(sys.argv[2],'wb')\n"
                  "pid=os.fork()\n"
                  "if pid==0:\n"
                  " os.dup2(out.fileno(),1)\n"
                  " os.execv(sys.argv[1],[sys.argv[1]])\n"
                  "_,status=os.waitpid(pid,0)\n"
                  "out.close()\n"
                  "if os.WIFSIGNALED(status):\n"
                  " print('SIGNAL',os.WTERMSIG(status))\n"
                  "else:\n"
                  " print('EXIT',os.WEXITSTATUS(status))\n")
        proc = subprocess.run([_wsl, "-d", "archlinux", "python3", "-c", waiter,
                               to_wsl(path), to_wsl(out_path)], capture_output=True,
                              text=True, timeout=timeout + 30)
        marker = proc.stdout.strip().splitlines()[-1:] or [""]
        parts = marker[0].split()

        class _Result:
            pass
        result = _Result()
        try:
            with open(out_path, "rb") as handle:
                result.stdout = handle.read()
        except OSError:
            result.stdout = b""
        result.stderr = proc.stderr.encode("utf-8", "replace")
        if len(parts) == 2 and parts[0] == "EXIT":
            result.returncode = int(parts[1])
        elif len(parts) == 2 and parts[0] == "SIGNAL":
            result.returncode = -(int(parts[1]))
        else:
            raise RuntimeError("WSL wait-status probe failed: " + proc.stdout[-500:] + proc.stderr[-500:])
        return result

    return (nasm, link_wsl, run_wsl), None


def build_program(source: str, filename: str, workdir: str | Path, prog: str = "prog"):
    """Compile .rl source to a linked host-native executable.

    Writes prog.asm, prog.o, rt_linux.o, prog into workdir (created). On
    success returns ({"asm","obj","rt_obj","exe","rir"}, None); on any
    expected failure returns (None, {"code","message"}).
    """
    workdir = Path(workdir)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return None, {"code": "PAR_INVALID_INPUT", "message": f"cannot create {workdir}: {error}"}
    tools, error = find_toolchain()
    if error is not None:
        return None, error
    nasm, linker, _runner = tools
    asm_text, error = _compile.compile_source(source, filename)
    if error is not None:
        return None, error
    # RIR text for inspection/determinism (rebuilt deterministically).
    from tools.rynorlang import analyze as _analyze
    result = _analyze.analyze(source, filename)
    module, _ = _rir.build_rir(result.ast, filename)
    asm_path = workdir / f"{prog}.asm"
    obj_path = workdir / f"{prog}.o"
    rt_obj_path = workdir / "rt_linux.o"
    exe_path = workdir / prog
    try:
        asm_path.write_text(asm_text, encoding="utf-8")
    except OSError as error:
        return None, {"code": "PAR_INVALID_INPUT", "message": f"cannot write {asm_path}: {error}"}
    try:
        # Basenames with cwd=workdir: the workdir path must not leak into
        # the object/executable (NASM records its input file name).
        asm_proc = subprocess.run([nasm, "-f", "elf64", asm_path.name, "-o", obj_path.name],
                                  capture_output=True, text=True, timeout=120,
                                  cwd=str(workdir))
        if asm_proc.returncode != 0:
            return None, {"code": COMP_ASSEMBLE_FAILED,
                          "message": (asm_proc.stderr or asm_proc.stdout).strip()[-2000:] or "nasm failed"}
        rt_proc = subprocess.run([nasm, "-f", "elf64", str(RUNTIME_ASM), "-o", rt_obj_path.name],
                                 capture_output=True, text=True, timeout=120,
                                 cwd=str(workdir))
        if rt_proc.returncode != 0:
            return None, {"code": COMP_ASSEMBLE_FAILED,
                          "message": (rt_proc.stderr or rt_proc.stdout).strip()[-2000:] or "nasm runtime failed"}
        link_proc = linker(exe_path, [obj_path, rt_obj_path], workdir)
        if link_proc.returncode != 0:
            return None, {"code": COMP_LINK_FAILED,
                          "message": (link_proc.stderr or link_proc.stdout).strip()[-2000:] or "link failed"}
    except (OSError, subprocess.SubprocessError) as error:
        return None, {"code": COMP_LINK_FAILED, "message": f"tool execution failed: {error}"}
    return ({"asm": asm_path, "obj": obj_path, "rt_obj": rt_obj_path, "exe": exe_path,
             "rir": _rir.dumps(module)}, None)


def run_program(exe: str | Path, timeout: int = 60):
    """Execute a built program. Returns (result, None) or (None, diag).

    result is {"exit": int|None, "signal": int|None, "stdout": bytes}.
    exit is the low-8-bit process status; signal is set when killed by one.
    """
    tools, error = find_toolchain()
    if error is not None:
        return None, error
    _nasm, _linker, runner = tools
    try:
        os.unlink(str(exe) + ".stdout")
    except OSError:
        pass
    try:
        proc = runner(str(exe), timeout)
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        return None, {"code": COMP_LINK_FAILED, "message": f"execution failed: {error}"}
    code = proc.returncode
    if code is not None and code < 0:
        return {"exit": None, "signal": -code, "stdout": proc.stdout}, None
    return {"exit": code & 0xFF if code is not None else None, "signal": None,
            "stdout": proc.stdout}, None


def main_build(args) -> int:
    """Implement compile.py --build/--run (args namespace with source/build/run)."""
    import tempfile
    try:
        raw = args.source.read_bytes()
    except OSError as error:
        print(f"{args.source}:1:1:0: PAR_INVALID_INPUT: {error}", file=sys.stderr)
        return 2
    try:
        source = raw.decode("ascii")
    except UnicodeDecodeError:
        print(f"{args.source}:1:1:0: PAR_LEX_ERROR: "
              "RynorLang Stage 12 source is ASCII-only", file=sys.stderr)
        return 1
    if args.run:
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="rlrun-") as work:
            arts, error = build_program(source, str(args.source), work,
                                        args.source.stem or "prog")
            if error is not None:
                print(f"{args.source}:1:1:0: {error['code']}: {error['message']}",
                      file=sys.stderr)
                return 1
            result, error = run_program(arts["exe"])
            if error is not None:
                print(f"{args.source}:1:1:0: {error['code']}: {error['message']}",
                      file=sys.stderr)
                return 1
            sys.stdout.buffer.write(result["stdout"])
            sys.stdout.buffer.flush()
            if result["signal"] is not None:
                print(f"{args.source}:1:1:0: RL_SIGNALED: signal {result['signal']}",
                      file=sys.stderr)
                return 128 + result["signal"]
            return result["exit"] if result["exit"] is not None else 1
    arts, error = build_program(source, str(args.source), args.build,
                                args.source.stem or "prog")
    if error is not None:
        print(f"{args.source}:1:1:0: {error['code']}: {error['message']}", file=sys.stderr)
        return 1
    return 0
