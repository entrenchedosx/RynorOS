"""Original fixed-layout image builder; no filesystem or general bootloader."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from resources import package_resources
from boot_layout import (BOOT_PART_SECTORS, MAX_BOOT_PAYLOAD,
                         REQUIRED_BOOT_SYMBOLS, assemble_boot_payload,
                         check_boot_layout, split_linked_binary)
from kernel_elf import read_symbols


IMAGE_MIN_SIZE = 1024 * 1024
IMAGE_SIZE = IMAGE_MIN_SIZE
MAX_PAYLOAD = MAX_BOOT_PAYLOAD
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
    if not 0 < len(payload) <= MAX_BOOT_PAYLOAD:
        raise ValueError(f"Payload must occupy 1..{MAX_BOOT_PAYLOAD} bytes")
    return (boot + payload).ljust(max(IMAGE_MIN_SIZE, len(boot) + len(payload)), b"\0")


def build_image(root: Path, destination: Path | None = None, *,
                test_vector: int = 3, test_armed: bool = True,
                shell_interactive: bool = False, input_test: bool = False,
                proc_test: bool = False, pipe_test: bool = False,
                pci_test: bool = False, dma_test: bool = False,
                msi_test: bool = False, blk_script_test: bool = False,
                blk_late_test: bool = False,
                blk_late_error_test: bool = False,
                blk_late_verify_test: bool = False,
                blk_late_verify_original_test: bool = False,
                fs_late_test: bool = False,
                fs_late_error_test: bool = False,
                fs_late_verify_test: bool = False,
                fs_late_verify_original_test: bool = False,
                xhci_test: bool = False,
                usb_test: bool = False,
                shell_boot: bool = False, shell_script=None) -> dict:
    if type(test_vector) is not int or test_vector not in (0, 1, 3, 6, 13, 14):
        raise ValueError("Unsupported CPU self-test vector")
    if type(test_armed) is not bool:
        raise ValueError("test_armed must be boolean")
    if type(shell_interactive) is not bool:
        raise ValueError("shell_interactive must be boolean")
    if type(input_test) is not bool:
        raise ValueError("input_test must be boolean")
    if type(proc_test) is not bool:
        raise ValueError("proc_test must be boolean")
    if type(pipe_test) is not bool:
        raise ValueError("pipe_test must be boolean")
    if type(dma_test) is not bool:
        raise ValueError("dma_test must be boolean")
    if type(msi_test) is not bool:
        raise ValueError("msi_test must be boolean")
    if type(blk_script_test) is not bool:
        raise ValueError("blk_script_test must be boolean")
    if type(blk_late_test) is not bool:
        raise ValueError("blk_late_test must be boolean")
    if type(blk_late_verify_test) is not bool:
        raise ValueError("blk_late_verify_test must be boolean")
    if type(blk_late_error_test) is not bool:
        raise ValueError("blk_late_error_test must be boolean")
    if blk_late_error_test and not blk_late_test:
        raise ValueError("blk_late_error_test requires blk_late_test")
    if type(blk_late_verify_original_test) is not bool:
        raise ValueError("blk_late_verify_original_test must be boolean")
    if blk_late_verify_original_test and not blk_late_verify_test:
        raise ValueError("blk_late_verify_original_test requires blk_late_verify_test")
    for name, value in (("fs_late_test", fs_late_test),
                        ("fs_late_error_test", fs_late_error_test),
                        ("fs_late_verify_test", fs_late_verify_test),
                        ("fs_late_verify_original_test", fs_late_verify_original_test)):
        if type(value) is not bool:
            raise ValueError(f"{name} must be boolean")
    if fs_late_error_test and not fs_late_test:
        raise ValueError("fs_late_error_test requires fs_late_test")
    if fs_late_verify_original_test and not fs_late_verify_test:
        raise ValueError("fs_late_verify_original_test requires fs_late_verify_test")
    if fs_late_test and fs_late_verify_test:
        raise ValueError("filesystem late-write and remount test modes are mutually exclusive")
    if sum((blk_script_test, blk_late_test, blk_late_verify_test)) > 1:
        raise ValueError("block test modes are mutually exclusive")
    if type(xhci_test) is not bool:
        raise ValueError("xhci_test must be boolean")
    if type(usb_test) is not bool:
        raise ValueError("usb_test must be boolean")
    if type(shell_boot) is not bool:
        raise ValueError("shell_boot must be boolean")
    if shell_script is not None and (type(shell_script) is not str or not
                                     shell_script.startswith("/")):
        raise ValueError("shell_script must be None or an absolute path")
    if shell_script is not None and not shell_boot:
        raise ValueError("shell_script requires shell_boot")
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
    # Size-critical test-only TUs compile -Os instead of -O2: test
    # drivers have no hot paths, while production TUs stay -O2. (P1-A3
    # measured fs-test.c -Os saving ~4.7KB; PCI-A1 generalizes the rule
    # to all test TUs after measuring pci-test.c -Os saving ~4.5KB
    # alone. Behavior is unchanged: the full QEMU suites re-verify
    # every self-test under -Os before any commit carrying this rule.
    # BOOT-A1 removed the old BIOS window that motivated the diet, but
    # the smaller test images remain cheaper to boot and audit.)
    size_opt_sources = frozenset({
        "kernel/storage/blk-test.c", "kernel/storage/fs-test.c",
        "kernel/drivers/keyboard-test.c",
        "kernel/drivers/display-surface-test.c",
        "kernel/drivers/display-test.c", "kernel/drivers/pci-test.c",
        "kernel/runtime/runtime-test.c", "kernel/runtime/boundary-test.c",
        "kernel/shell/shell-test.c", "kernel/core/scheduler-test.c",
        "kernel/core/user-test.c", "kernel/core/load-test.c",
        "kernel/core/rt-test.c", "kernel/core/read-test.c",
        "kernel/core/proc-test.c", "kernel/core/pipe-test.c",
        "kernel/mm/selftest.c", "kernel/mm/vm-test.c",
        "kernel/mm/heap-test.c", "kernel/mm/dma-test.c",
        "kernel/interrupts/apic-test.c", "kernel/acpi/acpi-test.c",
        "kernel/interrupts/msi-test.c",
        "kernel/drivers/xhci-test.c", "kernel/drivers/usb-test.c",
    })
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
            ("kernel/arch/x86_64/vm-test.asm", "vm-test-entry.o"),
            ("kernel/arch/x86_64/switch.asm", "switch.o"),
            ("kernel/arch/x86_64/scheduler-test.asm", "scheduler-test-entry.o"),
            ("kernel/arch/x86_64/user_entry.asm", "user-entry.o"),
        ):
            target = output / name
            # NASM's default warning set becomes errors. Its optional -Wall
            # relocation-style warnings reject intentional low-address 16/32-bit
            # relocations in our mixed-mode ELF; LLD checks their actual range.
            run_tool([nasm, "-f", "elf64", "-Werror", f"-DRYNOR_TEST_VECTOR={test_vector}",
                      source, "-o", str(target)], root)
            objects.append(str(target))
        for source, name in (
            ("kernel/core/main.c", "main.o"),
            ("kernel/core/memory.c", "memory.o"),
            ("kernel/storage/blk.c", "blk.o"),
            ("kernel/storage/blk-test.c", "blk-test.o"),
            ("kernel/storage/fs.c", "fs.o"),
            ("kernel/storage/fs-test.c", "fs-test.o"),
            ("kernel/drivers/keyboard.c", "keyboard.o"),
            ("kernel/drivers/keyboard-test.c", "keyboard-test.o"),
            ("kernel/drivers/display.c", "display.o"),
            ("kernel/drivers/display-surface.c", "display-surface.o"),
            ("kernel/drivers/display-surface-test.c", "display-surface-test.o"),
            ("kernel/drivers/display-test.c", "display-test.o"),
            ("kernel/drivers/pci.c", "pci.o"),
            ("kernel/drivers/pci-test.c", "pci-test.o"),
            # xHCI objects join only xHCI test images: test-gated code
            # must not perturb the default kernel layout (stale-TLB
            # negative tests are layout-sensitive).
            *((("kernel/drivers/xhci.c", "xhci.o"),
               ("kernel/drivers/xhci-test.c", "xhci-test.o"))
              if (xhci_test or usb_test) else ()),
            *((("kernel/drivers/usb.c", "usb.o"),
               ("kernel/drivers/xhci-usb.c", "xhci-usb.o"),
               ("kernel/drivers/usb-test.c", "usb-test.o"))
              if usb_test else ()),
            ("kernel/runtime/kstring.c", "kstring.o"),
            ("kernel/runtime/kbuf.c", "kbuf.o"),
            ("kernel/runtime/krst.c", "krst.o"),
            ("kernel/runtime/runtime-test.c", "runtime-test.o"),
            ("kernel/runtime/boundary-test.c", "runtime-boundary-test.o"),
            ("kernel/shell/shell.c", "shell.o"),
            ("kernel/shell/shell-test.c", "shell-test.o"),
            ("kernel/core/thread.c", "thread.o"),
            ("kernel/core/scheduler-test.c", "scheduler-test.o"),
            ("kernel/core/user.c", "user.o"),
            ("kernel/core/user-test.c", "user-test.o"),
            ("kernel/core/load.c", "load.o"),
            ("kernel/core/load-test.c", "load-test.o"),
            ("kernel/core/rt-test.c", "rt-test.o"),
            ("kernel/core/read-test.c", "read-test.o"),
            ("kernel/core/proc-test.c", "proc-test.o"),
            ("kernel/core/proc.c", "proc.o"),
            ("kernel/core/pipe.c", "pipe.o"),
            ("kernel/core/pipe-test.c", "pipe-test.o"),
            ("kernel/core/shd.c", "shd.o"),
            ("kernel/arch/x86_64/serial.c", "serial.o"),
            ("kernel/arch/x86_64/cpu.c", "cpu.o"),
            ("kernel/interrupts/exceptions.c", "exception-diagnostics.o"),
            ("kernel/interrupts/irq.c", "irq.o"),
            ("kernel/interrupts/apic.c", "apic.o"),
            ("kernel/interrupts/apic-test.c", "apic-test.o"),
            ("kernel/interrupts/msi.c", "msi.o"),
            ("kernel/interrupts/msi-test.c", "msi-test.o"),
            ("kernel/acpi/acpi.c", "acpi.o"),
            ("kernel/acpi/acpi-test.c", "acpi-test.o"),
            ("kernel/arch/x86_64/pic.c", "pic.o"),
            ("kernel/arch/x86_64/timer.c", "timer.o"),
            ("kernel/mm/map.c", "memory-map.o"),
            ("kernel/mm/pmm.c", "pmm.o"),
            ("kernel/mm/selftest.c", "pmm-selftest.o"),
            ("kernel/mm/vm.c", "vm.o"),
            ("kernel/mm/vm-test.c", "vm-selftest.o"),
            ("kernel/mm/kstack.c", "kstack.o"),
            ("kernel/mm/heap.c", "heap.o"),
            ("kernel/mm/heap-test.c", "heap-test.o"),
            ("kernel/mm/dma.c", "dma.o"),
            ("kernel/mm/dma-test.c", "dma-test.o"),
        ):
            target = output / name
            shell_flags = [f"-DRYNOR_SHELL_INTERACTIVE={int(shell_interactive)}",
                           f"-DRYNOR_INPUT_TEST={int(input_test)}",
                           f"-DRYNOR_PROC_TEST={int(proc_test)}",
                           f"-DRYNOR_PIPE_TEST={int(pipe_test)}",
                           f"-DRYNOR_PCI_TEST={int(pci_test)}",
                           f"-DRYNOR_DMA_TEST={int(dma_test)}",
                           f"-DRYNOR_MSI_TEST={int(msi_test)}",
                           f"-DRYNOR_BLK_SCRIPT_TEST={int(blk_script_test)}",
                           f"-DRYNOR_BLK_LATE_TEST={int(blk_late_test)}",
                           f"-DRYNOR_BLK_LATE_ERROR_TEST={int(blk_late_error_test)}",
                           f"-DRYNOR_BLK_LATE_VERIFY_TEST={int(blk_late_verify_test)}",
                           f"-DRYNOR_BLK_LATE_VERIFY_ORIGINAL_TEST={int(blk_late_verify_original_test)}",
                           f"-DRYNOR_FS_LATE_TEST={int(fs_late_test)}",
                           f"-DRYNOR_FS_LATE_ERROR_TEST={int(fs_late_error_test)}",
                           f"-DRYNOR_FS_LATE_VERIFY_TEST={int(fs_late_verify_test)}",
                           f"-DRYNOR_FS_LATE_VERIFY_ORIGINAL_TEST={int(fs_late_verify_original_test)}",
                           f"-DRYNOR_XHCI_TEST={int(xhci_test)}",
                           f"-DRYNOR_USB_TEST={int(usb_test)}",
                           f"-DRYNOR_SHELL_BOOT={int(shell_boot)}",
                           f'-DSHELL_SCRIPT_PATH="{shell_script or ""}"']
            run_tool([
                clang, "--target=x86_64-none-elf", "-std=c11", "-ffreestanding",
                "-fno-builtin", "-fno-stack-protector", "-fno-pic", "-fno-pie",
                "-mno-red-zone", "-mgeneral-regs-only", "-fno-ident",
                "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
                "-Wall", "-Wextra", "-Werror",
                "-Os" if source in size_opt_sources else "-O2",
                "-Ikernel/include",
                f'-DRYNOR_VERSION="{version}"', f"-DRYNOR_TEST_VECTOR={test_vector}",
                f"-DRYNOR_TEST_ARMED={int(test_armed)}",
                *shell_flags, "-c", source, "-o", str(target),
            ], root)
            objects.append(str(target))
        link = [linker, "-m", "elf_x86_64", "-T", "kernel/arch/x86_64/linker.ld",
                "--build-id=none", "--fatal-warnings", "-nostdlib", *objects]
        run_tool([*link, "-o", str(output / "rynorkernel.elf")], root)
        run_tool([*link, "--oformat=binary", "-o", str(output / "rynorkernel.bin")], root)
        symbols = {name: value for name, (value, _size) in
                   read_symbols(output / "rynorkernel.elf", REQUIRED_BOOT_SYMBOLS).items()}
        layout_errors = check_boot_layout(symbols)
        if layout_errors:
            raise ValueError("Linked boot layout invalid: " + "; ".join(layout_errors))
        boot_part, kernel_file = split_linked_binary(
            (output / "rynorkernel.bin").read_bytes(),
            symbols["__kernel_start"], symbols["__payload_end"])
        payload, boot_metadata = assemble_boot_payload(
            boot_part, kernel_file,
            symbols["__kernel_end"] - symbols["__kernel_start"])
        # The shipped flat binary is the contiguous on-disk boot payload
        # (boot part + header + sector-padded kernel file), not the raw
        # linker binary with its 8 MiB VMA gap.
        (output / "rynorkernel.bin").write_bytes(payload)
        sectors = (len(payload) + 511) // 512
        run_tool([nasm, "-f", "bin", "-Werror",
                  "boot/sector.asm", "-o", str(output / "boot.bin")], root)
        image = make_image((output / "boot.bin").read_bytes(), payload)
        (output / "rynoros.img").write_bytes(image)
        package_resources(root, output / "rynoros-resources.zip")
        manifest = {
            "version": version,
            "cpu_self_test": {"vector": test_vector, "armed": test_armed},
            "experimental_shell_interactive": shell_interactive,
            "experimental_input_test": input_test,
            "experimental_proc_test": proc_test,
            "experimental_pipe_test": pipe_test,
            "experimental_msi_test": msi_test,
            "experimental_blk_script_test": blk_script_test,
            "experimental_blk_late_test": blk_late_test,
            "experimental_blk_late_error_test": blk_late_error_test,
            "experimental_blk_late_verify_test": blk_late_verify_test,
            "experimental_blk_late_verify_original_test": blk_late_verify_original_test,
            "experimental_fs_late_test": fs_late_test,
            "experimental_fs_late_error_test": fs_late_error_test,
            "experimental_fs_late_verify_test": fs_late_verify_test,
            "experimental_fs_late_verify_original_test": fs_late_verify_original_test,
            "experimental_xhci_test": xhci_test,
            "experimental_usb_test": usb_test,
            "experimental_shell_boot": shell_boot,
            "experimental_shell_script": shell_script or "",
            "target": "x86_64-none-elf",
            "payload_sectors": sectors,
            "boot_sectors": BOOT_PART_SECTORS,
            "boot_header": {name: boot_metadata[name] for name in
                            ("file_sectors", "mem_pages", "checksum",
                             "kernel_file_bytes", "kernel_mem_bytes")},
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
