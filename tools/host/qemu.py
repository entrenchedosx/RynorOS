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
from kbd_output import KEYS, KBD_END, key_sequence, validate_keyboard_trace, validate_irq0_trace
from display_output import DISPLAY_END, DISPLAY_START, parse_display_output, verify_display_pixels, verify_display_scanout
from shell_output import SHELL_KEYS, SCANS as SHELL_SCANS
from kernel_elf import read_symbols
from runtime_output import verify_runtime_memory, verify_runtime_trace


EXPECTED_OUTPUT = BOOT_PREFIX  # Compatibility prefix, not the full execution log.


def _inject_pending_keys(process, observed: bytes, keys: list[str], next_index: list[int],
                         shell_keys: tuple[str, ...] | list[str] = (), shell_index: list[int] | None = None) -> None:
    """Send one validated host-selected key after its serial request. The Stage 8
    keys are driven by the "[KBD] waiting" markers and the Stage 11 interactive
    shell keys by the "[SHELL] waiting" markers; each stream advances only when
    its own marker is present, so the phases never collide. No fixed startup
    sleep; emulator key-release timing still exists."""
    if shell_index is None:
        shell_index = [0]
    if next_index[0] < len(keys):
        marker = b"[KBD] waiting for input=%d\r\n" % next_index[0]
        if marker not in observed:
            return
        if process.stdin is None:
            raise RuntimeError("QEMU monitor input is unavailable")
        process.stdin.write(b"sendkey %s\n" % keys[next_index[0]].encode())
        process.stdin.flush()
        next_index[0] += 1
    if shell_index[0] < len(shell_keys):
        marker = b"[SHELL] waiting for input=%d\r\n" % shell_index[0]
        if marker not in observed:
            return
        if process.stdin is None:
            raise RuntimeError("QEMU monitor input is unavailable")
        process.stdin.write(b"sendkey %s\n" % shell_keys[shell_index[0]].encode())
        process.stdin.flush()
        shell_index[0] += 1


def _capture_display_evidence(process, observed: bytes, logs: Path, deadline: float) -> None:
    """Verify full physical bytes AND actual display scanout independently,
    after guest completion and within the original boot deadline."""
    _, _, display_section = observed.partition(DISPLAY_START)
    display_section, sep, _ = display_section.partition(DISPLAY_END)
    if sep == b"":
        raise ValueError("Display framebuffer section incomplete")
    display = parse_display_output(DISPLAY_START + display_section + DISPLAY_END)
    if process.stdin is None:
        raise RuntimeError("QEMU monitor input is unavailable for framebuffer dump")
    path = (logs / "display.pmem").resolve()
    path.unlink(missing_ok=True)
    screen = (logs / 'display.ppm').resolve()
    screen.unlink(missing_ok=True)
    disk = json.dumps(str(path).replace("\\", "/"), ensure_ascii=False)
    screen_disk = json.dumps(str(screen).replace("\\", "/"), ensure_ascii=False)
    process.stdin.write((f"pmemsave 0x{display['lfb']:x} 0x{display['fb_bytes']:x} {disk}\n"
                         f"screendump {screen_disk}\n").encode('utf-8'))
    process.stdin.flush()
    ppm_size = len(f"P6\n{display['width']} {display['height']}\n255\n") + display['width']*display['height']*3
    while time.monotonic() < deadline:
        try:
            if path.stat().st_size >= display["fb_bytes"] and screen.stat().st_size >= ppm_size:
                break
        except OSError:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError("display framebuffer pmemsave dump did not complete")
    try:
        verify_display_pixels(path.read_bytes(), display)
        verify_display_scanout(screen.read_bytes(), display)
    except ValueError as error:
        raise RuntimeError("display pixel evidence failed: " + str(error)) from error


def _capture_runtime_evidence(process, image, logs, deadline):
    names = ('runtime_evidence', '__runtime_service_start', '__runtime_service_end',
             '__kernel_start', '__kernel_end')
    symbols = read_symbols(image.with_name('rynorkernel.elf'), names)
    address, size = symbols['runtime_evidence']
    low, high = symbols['__kernel_start'][0], symbols['__kernel_end'][0]
    if size != 7 * 9 * 8 or not low <= address < address + size <= high <= 0x70000:
        raise ValueError('runtime execution evidence: invalid ELF memory extent')
    path = (logs / 'runtime.pmem').resolve()
    path.unlink(missing_ok=True)
    filename = json.dumps(str(path).replace('\\', '/'), ensure_ascii=False)
    process.stdin.write(f'pmemsave 0x{address:x} {size} {filename}\n'.encode())
    process.stdin.flush()
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size >= size:
            verify_runtime_memory(path.read_bytes(), symbols['__runtime_service_start'][0],
                                  symbols['__runtime_service_end'][0])
            trace = logs / 'guest-errors.log'
            if trace.stat().st_size > 32 * 1024 * 1024:
                raise ValueError('runtime execution evidence: CPU trace oversized')
            verify_runtime_trace(path.read_bytes(), trace.read_text(encoding='ascii', errors='replace'),
                                 symbols['__runtime_service_start'][0], symbols['__runtime_service_end'][0])
            return
        time.sleep(0.01)
    raise RuntimeError('runtime execution evidence: physical dump timed out')


def boot_image(image: Path, logs: Path, timeout: float = 10.0, *, test_vector: int = 3,
               memory_mib: int = 64, cpu_model: str = "qemu64",
               max_ram_below_4g_mib: int | None = None,
               keys: tuple[str, ...] = KEYS, inject_keys: bool = True,
               shell_interactive: bool = False) -> bytes:
    keys = key_sequence(keys)
    shell_keys = tuple(SHELL_KEYS) if shell_interactive else ()
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("Boot timeout must be finite and in (0, 60] seconds")
    if type(test_vector) is not int or test_vector not in VECTOR_NAMES:
        raise ValueError("Unsupported expected exception vector")
    if type(memory_mib) is not int or not 8 <= memory_mib <= 4096:
        raise ValueError("QEMU test RAM must be an integer in [8, 4096] MiB")
    if cpu_model not in ("qemu64", "max", "qemu64,-nx"):
        raise ValueError("Unsupported audit CPU model")
    if max_ram_below_4g_mib is not None and (type(max_ram_below_4g_mib) is not int or
                                           not 32 <= max_ram_below_4g_mib <= 4096):
        raise ValueError("Low RAM limit must be an integer in [32, 4096] MiB")
    if not image.is_file():
        raise FileNotFoundError(f"Boot image missing: {image}")
    qemu = find_tool("qemu-system-x86_64", "RYNOR_QEMU")
    logs.mkdir(parents=True, exist_ok=True)
    serial = (logs / "serial.log").resolve()
    diagnostic = (logs / "qemu.log").resolve()
    debug = (logs / "guest-errors.log").resolve()
    # Never accept bytes from an earlier run, including failed runs. Evidence
    # files too: stale dumps beside a failed run must not be mistaken for new.
    serial.write_bytes(b"")
    debug.write_bytes(b"")
    for evidence in ("display.pmem", "display.ppm", "runtime.pmem"):
        (logs / evidence).unlink(missing_ok=True)
    machine = "pc-i440fx-10.0"
    if max_ram_below_4g_mib is not None:
        machine += f",max-ram-below-4g={max_ram_below_4g_mib}M"
    command = [
        qemu, "-machine", machine, "-accel", "tcg,tb-size=32", "-cpu", cpu_model,
        "-m", f"{memory_mib}M", "-smp", "1", "-bios", "bios-256k.bin", "-display", "none", "-vga", "std",
        "-nic", "none", "-parallel", "none", "-boot", "order=c,strict=on",
        "-drive", f"file={str(image.resolve()).replace(',', ',,')},format=raw,if=ide,snapshot=on",
        "-serial", f"file:{serial}", "-monitor", "stdio", "-no-reboot",
        "-d", "guest_errors,int", "-D", str(debug),
        "-trace", "enable=pckbd_kbd_read_data",
        "-trace", "enable=ps2_keyboard_event",
        "-trace", "enable=pic_interrupt",
    ]
    print("QEMU: " + subprocess.list2cmdline(command), flush=True)
    observed = b""
    failure = None
    cleanup = "not-started"
    next_key = [0]
    shell_key = [0]
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
                # Complete fatal driver diagnostics are explicit failure, not a
                # reason to spend the remaining boot deadline waiting for HLT.
                # Wait for CRLF so shutdown cannot truncate the diagnostic.
                driver_failure = next((line for line in observed.split(b"\r\n")[:-1]
                                       if line.startswith((b"[KBD] failure=", b"[FB] failure=",
                                                          b"[RUNTIME] failure=", b"[SHELL] failure=",
                                                          b"[GATE] failure=", b"[TIMER] failure=",
                                                          b"[SCHED] failure=", b"[HEAP] failure=",
                                                          b"[VM] failure="))), None)
                if driver_failure is not None:
                    failure = driver_failure.decode('ascii', errors='replace')
                    break
                if inject_keys:
                    _inject_pending_keys(process, observed, list(keys), next_key,
                                         list(shell_keys), shell_key)
                if not validate_boot_output(observed, test_vector, keys,
                                            require_shell=shell_interactive):
                    if test_vector == 3 and next_key[0] != len(keys):
                        failure = "Keyboard completed without all host inputs"
                    elif test_vector == 3:
                        # Evidence capture gets its own short grace: a guest that
                        # completes in the final boot-deadline window must not be
                        # failed because the 3 MiB dump needs another second.
                        deadline = max(start + timeout, time.monotonic() + 5.0)
                        try:
                            _capture_display_evidence(process, observed, logs, deadline)
                            _capture_runtime_evidence(process, image, logs, deadline)
                        except (ValueError, RuntimeError) as error:
                            failure = str(error)
                    break
                time.sleep(0.05)
            else:
                failure = (f"Boot timed out after {timeout:g}s: " +
                           "; ".join(validate_boot_output(
                               observed, test_vector, keys,
                               require_shell=shell_interactive)))
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
                "keyboard_inputs_sent": next_key[0], "keyboard_keys": list(keys),
                "shell_inputs_sent": shell_key[0], "shell_keys": list(shell_keys),
            }
            (logs / "run.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if cleanup != "monitor-quit" or process.returncode != 0:
        failure = failure or f"QEMU did not shut down normally: {cleanup}, exit {process.returncode}"
    observed = serial.read_bytes()
    errors = validate_boot_output(observed, test_vector, keys,
                                  require_shell=shell_interactive)
    if test_vector == 3 and KBD_END in observed and not errors:
        trace = debug.read_text(encoding="utf-8", errors="replace")
        try:
            validate_keyboard_trace(trace, keys,
                                    tuple(SHELL_SCANS[key] for key in shell_keys))
            validate_irq0_trace(trace)
        except ValueError as error:
            failure = failure or str(error)
    if shell_interactive and shell_key[0] != len(shell_keys):
        failure = failure or "Shell completed without all host inputs"
    if errors:
        failure = failure or "; ".join(errors)
    if failure:
        summary["failure"] = failure
        (logs / "run.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(
            f"{failure}\nSerial captured: {observed!r}\n"
            f"QEMU diagnostics: {diagnostic.read_text(encoding='utf-8', errors='replace')[-4000:]}\n"
            f"Logs: {logs.resolve()}"
        )
    print(observed.decode("ascii").replace("\r\n", "\n"), end="", flush=True)
    print("QEMU boot test passed; emulator exited normally and was reaped.", flush=True)
    return observed
