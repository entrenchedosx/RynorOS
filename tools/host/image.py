"""Original fixed-layout image builder; no filesystem or general bootloader."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from resources import package_resources


IMAGE_SIZE = 1024 * 1024
MAX_PAYLOAD = 32 * 1024
ARTIFACTS = ("boot.bin", "rynorkernel.elf", "rynorkernel.bin", "rynoros.img", "rynoros-resources.zip")


def find_tool(name: str, override: str) -> str:
    candidate = os.environ.get(override, name)
    found = shutil.which(candidate)
    if not found:
        raise FileNotFoundError(
            f"Required host tool {name!r} not found. Add it to PATH or set {override} "
            "to its executable; see docs/design/bootstrap-dependencies.md."
        )
    return str(Path(found).resolve())


def run_tool(command: list[str], root: Path) -> str:
    print("+ " + subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(
            f"Tool failed with exit {result.returncode}: {subprocess.list2cmdline(command)}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout.strip()


def make_image(boot: bytes, payload: bytes) -> bytes:
    if len(boot) != 512 or boot[510:] != b"\x55\xaa":
        raise ValueError("Boot sector must be 512 bytes with the BIOS 55aa signature")
    if not 0 < len(payload) <= MAX_PAYLOAD:
        raise ValueError("Payload must occupy 1..32768 bytes")
    return (boot + payload).ljust(IMAGE_SIZE, b"\0")


def build_image(root: Path, destination: Path | None = None, *,
                test_vector: int = 3, test_armed: bool = True) -> dict:
    if type(test_vector) is not int or test_vector not in (0, 1, 3, 6, 13, 14):
        raise ValueError("Unsupported CPU self-test vector")
    if type(test_armed) is not bool:
        raise ValueError("test_armed must be boolean")
    destination = destination or root / "build"
    destination.mkdir(parents=True, exist_ok=True)
    # Invalidate only named generated deliverables: an unsuccessful rebuild must
    # not leave a stale boot image or success manifest available to mistake for new.
    for name in (*ARTIFACTS, "build-manifest.json"):
        (destination / name).unlink(missing_ok=True)
    clang = find_tool("clang", "RYNOR_CLANG")
    linker = find_tool("ld.lld", "RYNOR_LLD")
    nasm = find_tool("nasm", "RYNOR_NASM")
    version = json.loads((root / "project.json").read_text(encoding="utf-8"))["version"]
    if version != "0.1.0":
        raise ValueError("Unexpected boot banner version; update metadata and boot tests together")
    with tempfile.TemporaryDirectory(prefix="compile-", dir=destination) as temporary:
        output = Path(temporary)
        objects = []
        for source, name in (
            ("boot/transition.asm", "transition.o"),
            ("kernel/arch/x86_64/entry.asm", "entry.o"),
            ("kernel/arch/x86_64/descriptors.asm", "descriptors.o"),
            ("kernel/arch/x86_64/exceptions.asm", "exceptions.o"),
            ("kernel/arch/x86_64/selftest.asm", "selftest.o"),
        ):
            target = output / name
            # Use NASM's default warning set as errors. Its optional -Wall
            # relocation-style warnings reject intentional low-address 16/32-bit
            # relocations in our mixed-mode ELF; LLD checks their actual range.
            run_tool([nasm, "-f", "elf64", "-Werror", f"-DRYNOR_TEST_VECTOR={test_vector}",
                      source, "-o", str(target)], root)
            objects.append(str(target))
        for source, name in (
            ("kernel/core/main.c", "main.o"),
            ("kernel/arch/x86_64/serial.c", "serial.o"),
            ("kernel/arch/x86_64/cpu.c", "cpu.o"),
            ("kernel/interrupts/exceptions.c", "exception-diagnostics.o"),
            ("kernel/interrupts/irq.c", "irq.o"),
            ("kernel/arch/x86_64/pic.c", "pic.o"),
            ("kernel/arch/x86_64/timer.c", "timer.o"),
            ("kernel/mm/map.c", "memory-map.o"),
            ("kernel/mm/pmm.c", "pmm.o"),
            ("kernel/mm/selftest.c", "pmm-selftest.o"),
        ):
            target = output / name
            run_tool([
                clang, "--target=x86_64-none-elf", "-std=c11", "-ffreestanding",
                "-fno-builtin", "-fno-stack-protector", "-fno-pic", "-fno-pie",
                "-mno-red-zone", "-mgeneral-regs-only", "-fno-ident",
                "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
                "-Wall", "-Wextra", "-Werror", "-O2", "-Ikernel/include",
                f'-DRYNOR_VERSION="{version}"', f"-DRYNOR_TEST_VECTOR={test_vector}",
                f"-DRYNOR_TEST_ARMED={int(test_armed)}", "-c", source, "-o", str(target),
            ], root)
            objects.append(str(target))
        link = [linker, "-m", "elf_x86_64", "-T", "kernel/arch/x86_64/linker.ld",
                "--build-id=none", "--fatal-warnings", "-nostdlib", *objects]
        run_tool([*link, "-o", str(output / "rynorkernel.elf")], root)
        run_tool([*link, "--oformat=binary", "-o", str(output / "rynorkernel.bin")], root)
        payload = (output / "rynorkernel.bin").read_bytes()
        if not 0 < len(payload) <= MAX_PAYLOAD:
            raise ValueError("Linked payload exceeds the BIOS load window")
        sectors = (len(payload) + 511) // 512
        run_tool([nasm, "-f", "bin", "-Werror", f"-DPAYLOAD_SECTORS={sectors}",
                  "boot/sector.asm", "-o", str(output / "boot.bin")], root)
        image = make_image((output / "boot.bin").read_bytes(), payload)
        (output / "rynoros.img").write_bytes(image)
        package_resources(root, output / "rynoros-resources.zip")
        manifest = {
            "version": version,
            "cpu_self_test": {"vector": test_vector, "armed": test_armed},
            "target": "x86_64-none-elf",
            "payload_sectors": sectors,
            "tools": {"clang": run_tool([clang, "--version"], root).splitlines()[0],
                      "ld.lld": run_tool([linker, "--version"], root).splitlines()[0],
                      "nasm": run_tool([nasm, "-v"], root)},
            "artifacts": {name: {"bytes": (output / name).stat().st_size,
                         "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}
                          for name in ARTIFACTS},
        }
        for name in ARTIFACTS:
            shutil.copyfile(output / name, destination / name)
        (destination / "build-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
    print(f"Built {destination / 'rynoros.img'} ({len(image)} bytes; {sectors} payload sectors).", flush=True)
    return manifest
