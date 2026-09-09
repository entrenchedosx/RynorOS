#!/usr/bin/env python3
"""Stage 16 host-native program pipeline: .rl -> RIR -> asm -> object -> ELF.

Host-side, Python 3.10+ standard library only. This module turns a verified
RynorLang program into a real host-native executable and runs it. It reuses
the frozen frontend (lex/parse/analyze), RIR builder/verifier, and NASM
backend, then assembles with NASM, links with LLD (plus the host program
runtime in tools/rynorlang/runtime/rt_linux.asm), and executes the result.

HOST BOOTSTRAP honesty: every artifact from build_program is a host-native
Linux x86-64 ELF for testing (printing/startup use Linux syscalls through
the labeled runtime object). It is NOT a RynorOS userspace program: for the
RynorOS ABI use build_rynor_program (fixed-VA link plus rt_rynor.asm or the
18c library, int $0x80 exit/write/yield per docs/design/syscall-abi.md,
frozen since Stage 18b). Nothing here ships in any RynorOS image.

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
RYNOR_RUNTIME_ASM = Path(__file__).resolve().parent / "runtime" / "rt_rynor.asm"
RYNOR_LINK_SCRIPT = Path(__file__).resolve().parent / "runtime" / "rynoros.ld"
RYNOR_RT_LINK_SCRIPT = Path(__file__).resolve().parent / "runtime" / "rynoros_rt.ld"
# Stage 18c library runtime: freestanding user/lib/rt sources, compiled for
# the fixed-VA target and linked instead of rt_rynor.asm when requested.
RTLIB_DIR = _ROOT / "user" / "lib" / "rt"
RTLIB_GATE_ASM = "rt_gate.asm"
RTLIB_C_SOURCES = ("rt.c", "rt_rl.c")
RTLIB_HEADER = "rt.h"
RTLIB_CLANG_FLAGS = ("--target=x86_64-none-elf", "-std=c11", "-ffreestanding",
                     "-fno-builtin", "-fno-stack-protector", "-fno-pic", "-fno-pie",
                     "-mno-red-zone", "-mgeneral-regs-only", "-fno-ident",
                     "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
                     "-ffunction-sections", "-fdata-sections",
                     "-Wall", "-Wextra", "-Werror", "-O2")
# Per-function sections plus --gc-sections keep each conformance program
# inside the 4 KiB code window (the full library does not fit with all
# call classes linked). The single RW data LOAD comes from the writable
# .data.rtanchor input (see rt_gate.asm), so no segment-merging flags are
# needed; the 18b default link keeps its exact historical flags.
RTLIB_LINK_FLAGS = ("--gc-sections",)

COMP_TOOLCHAIN_MISSING = "COMP_TOOLCHAIN_MISSING"
COMP_ASSEMBLE_FAILED = "COMP_ASSEMBLE_FAILED"
COMP_LINK_FAILED = "COMP_LINK_FAILED"
COMP_NO_RUNTIME = "COMP_NO_RUNTIME"


def _discard(workdir: Path, names) -> None:
    """Remove stale build outputs so a failed rebuild can never leave a
    prior success behind (mirrors image.py/qemu.py invalidation). Missing
    files are ignored; nothing else in the directory is touched."""
    for name in names:
        try:
            (workdir / name).unlink()
        except OSError:
            pass


# Basenames owned by the toolchain staging/linking below. Caller sources
# and prog names colliding with them would be silently overwritten or
# linked twice (last-writer-wins), so they are rejected up front.
_RESERVED_NAMES = frozenset({
    "rt.h", "rt.c", "rt_rl.c", "rt_gate.asm",
    "rt.o", "rt_rl.o", "rt_gate.o",
    "rt_rynor.asm", "rt_rynor.o", "rt_linux.asm", "rt_linux.o",
    "rynoros.ld", "rynoros_rt.ld",
    "rt", "rt_rl", "rt_gate", "rt_rynor", "rt_linux",
    "rynoros", "rynoros_rt",
})


def _reserved(name: str) -> bool:
    if not name or name in _RESERVED_NAMES or name in (".", ".."):
        return True
    return "/" in name or "\\" in name or Path(name).name != name


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

        def link_posix(output, inputs, workdir, flags=()):
            return subprocess.run([linker_bin, "-o", Path(output).name,
                                   *(Path(p).name for p in inputs), "--build-id=none",
                                   *flags],
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

    def link_wsl(output, inputs, workdir, flags=(), _wsl=wsl, _linker=wsl_linker):
        # --cd plus basenames keeps the workdir path out of the linked
        # image (NASM records its input name in the object symbol table).
        return subprocess.run([_wsl, "-d", "archlinux", "--cd", to_wsl(workdir),
                               _linker, "-o", Path(output).name,
                               *(Path(p).name for p in inputs), "--build-id=none",
                               *flags],
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
    if _reserved(prog):
        return None, {"code": "PAR_INVALID_INPUT",
                      "message": f"reserved program name: {prog!r}"}
    _discard(workdir, (f"{prog}.asm", f"{prog}.o", "rt_linux.o", prog,
                       RUNTIME_ASM.name))
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
    module, rir_error = _rir.build_rir(result.ast, filename)
    if rir_error is not None:
        return None, rir_error
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
        # Assemble the runtime by basename too: NASM records its input
        # file name in the object, so the absolute source-tree path
        # would otherwise leak into every linked executable.
        rt_src_path = workdir / RUNTIME_ASM.name
        try:
            rt_src_path.write_bytes(RUNTIME_ASM.read_bytes())
        except OSError as error:
            return None, {"code": COMP_LINK_FAILED, "message": f"cannot stage runtime: {error}"}
        rt_proc = subprocess.run([nasm, "-f", "elf64", rt_src_path.name, "-o", rt_obj_path.name],
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


def _find_clang():
    clang = _find_tool("clang", "RYNOR_CLANG")
    if clang is None:
        return None, {"code": COMP_TOOLCHAIN_MISSING,
                      "message": "clang not found (set RYNOR_CLANG or extend PATH)"}
    return clang, None


def _clang_freestanding_includes(clang):
    """Locate clang's builtin headers (stdarg.h lives there, not in any
    sysroot) for --target=x86_64-none-elf freestanding builds."""
    try:
        proc = subprocess.run([clang, "-print-resource-dir"], capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return None, {"code": COMP_TOOLCHAIN_MISSING,
                      "message": f"clang resource query failed: {error}"}
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, {"code": COMP_TOOLCHAIN_MISSING,
                      "message": "clang -print-resource-dir failed"}
    resource = Path(proc.stdout.strip())
    candidates = [resource / "include"]
    # Layouts vary (‹resource›/include vs versioned sub/sibling dirs);
    # probe one level down and across before giving up.
    for base in (resource, resource.parent):
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        candidates.extend(sub / "include" for sub in entries if sub.is_dir())
    for inc in candidates:
        if (inc / "stdarg.h").is_file():
            return [f"-I{inc.as_posix()}"], None
    return None, {"code": COMP_TOOLCHAIN_MISSING,
                  "message": f"clang builtin headers missing under: {resource}"}


def _build_rtlib_objects(workdir: Path, nasm: str, with_rl: bool,
                         rtlib_dir=None):
    """Stage and build the 18c library objects in workdir (basenames only,
    same path-hygiene rule as the other flows). with_rl adds the RIR
    print helpers (rt_rl.c); rl_entry selects the gate stub flavor
    (-DRL_ENTRY calls rl_4_main, otherwise rt_main). rtlib_dir overrides
    the library source directory (mutation testing stages a copied tree).
    Returns ({"rt_obj","rt_rl_obj","rt_gate_obj"}, None) or (None, diag);
    rt_rl_obj is None when with_rl is false."""
    clang, error = _find_clang()
    if error is not None:
        return None, error
    builtin_includes, error = _clang_freestanding_includes(clang)
    if error is not None:
        return None, error
    libdir = Path(rtlib_dir) if rtlib_dir is not None else RTLIB_DIR
    names = [RTLIB_HEADER, "rt.c"] + ([RTLIB_C_SOURCES[1]] if with_rl else []) + [RTLIB_GATE_ASM]
    try:
        for name in names:
            (workdir / name).write_bytes((libdir / name).read_bytes())
    except OSError as error:
        return None, {"code": COMP_LINK_FAILED,
                      "message": f"cannot stage 18c library inputs: {error}"}
    try:
        objs = {}
        for name in ["rt.c"] + ([RTLIB_C_SOURCES[1]] if with_rl else []):
            out = workdir / (Path(name).stem + ".o")
            proc = subprocess.run(
                [clang, *RTLIB_CLANG_FLAGS, *builtin_includes, "-I.", "-c", name,
                 "-o", out.name],
                capture_output=True, text=True, timeout=120, cwd=str(workdir))
            if proc.returncode != 0:
                return None, {"code": COMP_ASSEMBLE_FAILED,
                              "message": (proc.stderr or proc.stdout).strip()[-2000:]
                              or f"clang {name} failed"}
            objs[name] = out
        gate_out = workdir / "rt_gate.o"
        gate_argv = [nasm, "-f", "elf64"]
        if with_rl:
            gate_argv.append("-DRL_ENTRY")
        gate_argv += [RTLIB_GATE_ASM, "-o", gate_out.name]
        gate_proc = subprocess.run(gate_argv, capture_output=True, text=True,
                                   timeout=120, cwd=str(workdir))
        if gate_proc.returncode != 0:
            return None, {"code": COMP_ASSEMBLE_FAILED,
                          "message": (gate_proc.stderr or gate_proc.stdout).strip()[-2000:]
                          or "nasm rt_gate failed"}
    except (OSError, subprocess.SubprocessError) as error:
        return None, {"code": COMP_LINK_FAILED, "message": f"tool execution failed: {error}"}
    return ({"rt_obj": objs["rt.c"], "rt_rl_obj": objs.get(RTLIB_C_SOURCES[1]),
             "rt_gate_obj": gate_out}, None)


def build_rynor_c_program(sources: dict, workdir: str | Path, prog: str = "prog",
                          rtlib_dir=None):
    """Compile freestanding C sources to a RynorOS userspace ELF.

    sources maps basename -> text (staged verbatim; basenames only, no
    directories). Exactly one program entry is expected: the library
    gate stub calls rt_main, so one source must define it. Links the
    18c library (rt.c, no RIR helpers) with rynoros.ld. rtlib_dir
    overrides the library source directory (mutation testing). Returns
    ({"objs","exe"}, None) or (None, {"code","message"}).
    """
    workdir = Path(workdir)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return None, {"code": "PAR_INVALID_INPUT", "message": f"cannot create {workdir}: {error}"}
    if not sources or any(Path(name).name != name for name in sources):
        return None, {"code": "PAR_INVALID_INPUT", "message": "c sources must be nonempty basenames"}
    if _reserved(prog):
        return None, {"code": "PAR_INVALID_INPUT",
                      "message": f"reserved program name: {prog!r}"}
    for name in sources:
        if name in _RESERVED_NAMES:
            return None, {"code": "PAR_INVALID_INPUT",
                          "message": f"reserved source name: {name!r}"}
    _discard(workdir, [f"{prog}.elf", "rt.o", "rt_rl.o", "rt_gate.o",
                       RTLIB_HEADER, "rt.c", "rt_rl.c", RTLIB_GATE_ASM,
                       RYNOR_RT_LINK_SCRIPT.name] +
                      [Path(name).stem + ".o" for name in sources])
    tools, error = find_toolchain()
    if error is not None:
        return None, error
    nasm, linker, _runner = tools
    if not RYNOR_RT_LINK_SCRIPT.is_file():
        return None, {"code": COMP_NO_RUNTIME, "message": "RynorOS library link script missing"}
    try:
        for name, text in sources.items():
            (workdir / name).write_text(text, encoding="utf-8")
    except OSError as error:
        return None, {"code": "PAR_INVALID_INPUT", "message": f"cannot write c sources: {error}"}
    lib_objs, error = _build_rtlib_objects(workdir, nasm, with_rl=False,
                                             rtlib_dir=rtlib_dir)
    if error is not None:
        return None, error
    clang, error = _find_clang()
    if error is not None:
        return None, error
    builtin_includes, error = _clang_freestanding_includes(clang)
    if error is not None:
        return None, error
    exe_path = workdir / f"{prog}.elf"
    try:
        objs = []
        for name in sources:
            out = workdir / (Path(name).stem + ".o")
            proc = subprocess.run(
                [clang, *RTLIB_CLANG_FLAGS, *builtin_includes, "-I.", "-c", name,
                 "-o", out.name],
                capture_output=True, text=True, timeout=120, cwd=str(workdir))
            if proc.returncode != 0:
                return None, {"code": COMP_ASSEMBLE_FAILED,
                              "message": (proc.stderr or proc.stdout).strip()[-2000:]
                              or f"clang {name} failed"}
            objs.append(out)
        ld_path = workdir / RYNOR_RT_LINK_SCRIPT.name
        try:
            ld_path.write_bytes(RYNOR_RT_LINK_SCRIPT.read_bytes())
        except OSError as error:
            return None, {"code": COMP_LINK_FAILED, "message": f"cannot stage link script: {error}"}
        link_proc = linker(exe_path, [*objs, lib_objs["rt_obj"], lib_objs["rt_gate_obj"]],
                           workdir, ["-T", ld_path.name, "-e", "_start",
                                     *RTLIB_LINK_FLAGS])
        if link_proc.returncode != 0:
            return None, {"code": COMP_LINK_FAILED,
                          "message": (link_proc.stderr or link_proc.stdout).strip()[-2000:]
                          or "link failed"}
    except (OSError, subprocess.SubprocessError) as error:
        return None, {"code": COMP_LINK_FAILED, "message": f"tool execution failed: {error}"}
    return ({"objs": objs, "exe": exe_path}, None)


def build_rynor_program(source: str, filename: str, workdir: str | Path, prog: str = "prog",
                        runtime: str = "rynor", rtlib_dir=None):
    """Compile .rl source to a RynorOS userspace executable (ELF, RynorOS ABI).

    Same frontend as build_program, but links rt_rynor.asm (int $0x80
    gate calls, never Linux syscalls) with the fixed-VA rynoros.ld
    script (.text at USER_CODE_BASE, rodata/data/bss in the data
    window). Writes prog.asm, prog.o, rt_rynor.o, rynoros.ld, prog.elf
    into workdir (created). The ELF is an intermediate for the RYNX
    converter (tools/host/rnyx.py), never executed on the host.
    runtime selects the OS runtime object: "rynor" links rt_rynor.asm
    (direct-gate helpers, Stage 18b behavior, byte-identical); "rtlib"
    links the 18c library instead, so RIR print helpers resolve through
    rt_write (Stage 18c print rebind; host targets are unaffected).
    rtlib_dir overrides the library source directory (mutation testing).
    On success returns ({"asm","obj","rt_obj","elf","rir"}, None);
    on any expected failure returns (None, {"code","message"}).
    """
    if runtime not in ("rynor", "rtlib"):
        raise ValueError(f"unknown RynorOS runtime: {runtime!r}")
    workdir = Path(workdir)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return None, {"code": "PAR_INVALID_INPUT", "message": f"cannot create {workdir}: {error}"}
    if _reserved(prog):
        return None, {"code": "PAR_INVALID_INPUT",
                      "message": f"reserved program name: {prog!r}"}
    _discard(workdir, (f"{prog}.asm", f"{prog}.o", "rt_rynor.o", f"{prog}.elf",
                       RYNOR_RUNTIME_ASM.name, RYNOR_LINK_SCRIPT.name,
                       RYNOR_RT_LINK_SCRIPT.name, "rt.o", "rt_rl.o",
                       "rt_gate.o", RTLIB_HEADER, "rt.c", "rt_rl.c",
                       RTLIB_GATE_ASM))
    tools, error = find_toolchain()
    if error is not None:
        return None, error
    if not RYNOR_RUNTIME_ASM.is_file() or not RYNOR_LINK_SCRIPT.is_file():
        return None, {"code": COMP_NO_RUNTIME, "message": "RynorOS runtime/script missing"}
    nasm, linker, _runner = tools
    asm_text, error = _compile.compile_source(source, filename)
    if error is not None:
        return None, error
    from tools.rynorlang import analyze as _analyze
    result = _analyze.analyze(source, filename)
    module, rir_error = _rir.build_rir(result.ast, filename)
    if rir_error is not None:
        return None, rir_error
    asm_path = workdir / f"{prog}.asm"
    obj_path = workdir / f"{prog}.o"
    rt_obj_path = workdir / "rt_rynor.o"
    exe_path = workdir / f"{prog}.elf"
    try:
        asm_path.write_text(asm_text, encoding="utf-8")
    except OSError as error:
        return None, {"code": "PAR_INVALID_INPUT", "message": f"cannot write {asm_path}: {error}"}
    try:
        asm_proc = subprocess.run([nasm, "-f", "elf64", asm_path.name, "-o", obj_path.name],
                                  capture_output=True, text=True, timeout=120,
                                  cwd=str(workdir))
        if asm_proc.returncode != 0:
            return None, {"code": COMP_ASSEMBLE_FAILED,
                          "message": (asm_proc.stderr or asm_proc.stdout).strip()[-2000:] or "nasm failed"}
        # Stage runtime and link script by basename (same path-hygiene
        # rule as the Linux flow: no workdir or source-tree leak).
        # The rtlib flavor uses the library script (merged RW data LOAD);
        # the default flavor keeps rynoros.ld byte-identical to Stage 18b.
        script_src = RYNOR_RT_LINK_SCRIPT if runtime == "rtlib" else RYNOR_LINK_SCRIPT
        ld_path = workdir / script_src.name
        try:
            ld_path.write_bytes(script_src.read_bytes())
        except OSError as error:
            return None, {"code": COMP_LINK_FAILED, "message": f"cannot stage rynoros inputs: {error}"}
        link_inputs = [obj_path, rt_obj_path]
        link_flags = ["-T", ld_path.name, "-e", "_start"]
        if runtime == "rynor":
            rt_src_path = workdir / RYNOR_RUNTIME_ASM.name
            try:
                rt_src_path.write_bytes(RYNOR_RUNTIME_ASM.read_bytes())
            except OSError as error:
                return None, {"code": COMP_LINK_FAILED, "message": f"cannot stage rynoros inputs: {error}"}
            rt_proc = subprocess.run([nasm, "-f", "elf64", rt_src_path.name, "-o", rt_obj_path.name],
                                     capture_output=True, text=True, timeout=120,
                                     cwd=str(workdir))
            if rt_proc.returncode != 0:
                return None, {"code": COMP_ASSEMBLE_FAILED,
                              "message": (rt_proc.stderr or rt_proc.stdout).strip()[-2000:] or "nasm runtime failed"}
        else:
            lib_objs, error = _build_rtlib_objects(workdir, nasm, with_rl=True,
                                                   rtlib_dir=rtlib_dir)
            if error is not None:
                return None, error
            link_inputs = [obj_path, lib_objs["rt_obj"], lib_objs["rt_rl_obj"],
                           lib_objs["rt_gate_obj"]]
            # Library links add section GC (see build_rynor_c_program).
            # The default link keeps its exact historical flags so 18b
            # artifacts rebuild identically.
            link_flags = link_flags + list(RTLIB_LINK_FLAGS)
        link_proc = linker(exe_path, link_inputs, workdir, link_flags)
        if link_proc.returncode != 0:
            return None, {"code": COMP_LINK_FAILED,
                          "message": (link_proc.stderr or link_proc.stdout).strip()[-2000:] or "link failed"}
    except (OSError, subprocess.SubprocessError) as error:
        return None, {"code": COMP_LINK_FAILED, "message": f"tool execution failed: {error}"}
    return ({"asm": asm_path, "obj": obj_path, "rt_obj": link_inputs[1], "exe": exe_path,
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
