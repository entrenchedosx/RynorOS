"""Bounded real-emulator serial smoke test, with owned-process cleanup."""

import json
import math
import os
from pathlib import Path
import subprocess
import time

from image import find_tool
from exception_output import BOOT_PREFIX, VECTOR_NAMES
from boot_output import validate_boot_output


EXPECTED_OUTPUT = BOOT_PREFIX  # Stage 1 compatibility prefix, not the full Stage 5 log.


def boot_image(image: Path, logs: Path, timeout: float = 10.0, *, test_vector: int = 3,
               memory_mib: int = 64) -> bytes:
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("Boot timeout must be finite and in (0, 60] seconds")
    if type(test_vector) is not int or test_vector not in VECTOR_NAMES:
        raise ValueError("Unsupported expected exception vector")
    if type(memory_mib) is not int or not 8 <= memory_mib <= 4096:
        raise ValueError("QEMU test RAM must be an integer in [8, 4096] MiB")
    if not image.is_file():
        raise FileNotFoundError(f"Boot image missing: {image}")
    qemu = find_tool("qemu-system-x86_64", "RYNOR_QEMU")
    logs.mkdir(parents=True, exist_ok=True)
    serial = (logs / "serial.log").resolve()
    diagnostic = (logs / "qemu.log").resolve()
    debug = (logs / "guest-errors.log").resolve()
    # Never accept bytes from an earlier run, including failed runs.
    serial.write_bytes(b"")
    debug.write_bytes(b"")
    command = [
        qemu, "-machine", "pc-i440fx-10.0", "-accel", "tcg", "-cpu", "qemu64",
        "-m", f"{memory_mib}M", "-smp", "1", "-bios", "bios-256k.bin", "-display", "none", "-vga", "none",
        "-nic", "none", "-parallel", "none", "-boot", "order=c,strict=on",
        "-drive", f"file={str(image.resolve()).replace(',', ',,')},format=raw,if=ide,snapshot=on",
        "-serial", f"file:{serial}", "-monitor", "stdio", "-no-reboot",
        "-d", "guest_errors", "-D", str(debug),
    ]
    print("QEMU: " + subprocess.list2cmdline(command), flush=True)
    observed = b""
    failure = None
    cleanup = "not-started"
    start = time.monotonic()
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with diagnostic.open("wb") as diagnostic_file:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=diagnostic_file,
            stderr=subprocess.STDOUT, creationflags=creationflags,
        )
        try:
            while time.monotonic() - start < timeout:
                observed = serial.read_bytes()
                if process.poll() is not None:
                    failure = f"QEMU exited before test completion (exit {process.returncode})"
                    break
                if not validate_boot_output(observed, test_vector):
                    break
                time.sleep(0.05)
            else:
                failure = (f"Boot timed out after {timeout:g}s: " +
                           "; ".join(validate_boot_output(observed, test_vector)))
        finally:
            # HMP quit gives QEMU a normal shutdown; terminate/kill are bounded
            # fallbacks only. Always reap this exact child, never other QEMU PIDs.
            if process.poll() is None:
                try:
                    # Keep the Windows stdio monitor open until it consumes the
                    # complete command. communicate() immediately closes stdin;
                    # its EOF can race the monitor's final newline processing.
                    process.stdin.write(b"quit\n")
                    process.stdin.flush()
                    process.wait(timeout=3)
                    cleanup = "monitor-quit"
                except (subprocess.TimeoutExpired, OSError):
                    cleanup = "terminate"
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        cleanup = "kill"
                        process.kill()
                        process.wait(timeout=2)
            else:
                cleanup = "already-exited"
                process.wait()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            summary = {
                "command": command, "pid": process.pid, "returncode": process.returncode,
                "cleanup": cleanup, "reaped": process.poll() is not None,
                "timeout_seconds": timeout, "elapsed_seconds": round(time.monotonic() - start, 3),
            }
            (logs / "run.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if cleanup != "monitor-quit" or process.returncode != 0:
        failure = failure or f"QEMU did not shut down normally: {cleanup}, exit {process.returncode}"
    observed = serial.read_bytes()
    errors = validate_boot_output(observed, test_vector)
    if errors:
        failure = failure or "; ".join(errors)
    if failure:
        raise RuntimeError(
            f"{failure}\nSerial captured: {observed!r}\n"
            f"QEMU diagnostics: {diagnostic.read_text(encoding='utf-8', errors='replace')[-4000:]}\n"
            f"Logs: {logs.resolve()}"
        )
    print(observed.decode("ascii").replace("\r\n", "\n"), end="", flush=True)
    print("QEMU boot test passed; emulator exited normally and was reaped.", flush=True)
    return observed
