"""M3 native QEMU proof: baby-compiled print programs as CPL3 guests."""
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'tools')
sys.path.insert(0, 'tools/host')
sys.path.insert(0, 'tests/integration')
from pathlib import Path
from tests.repository import test_rynorlang_selfhost_emit as bea
from image import build_image
from qemu import boot_image
from fs_image import build as fs_build
from sh_output import collect_done_statuses
from test_cplshell import _compile_shell, K
import struct

ROOT = Path('.').resolve()

NATIVE_M3 = [
    ("natm3-int", 'fn main(): int { print(42); return 0; }\n', b"42"),
    ("natm3-neg", 'fn main(): int { print(0 - 7); return 0; }\n', b"-7"),
    ("natm3-zero", 'fn main(): int { print(0); return 0; }\n', b"0"),
    ("natm3-boolt", 'fn main(): int { print(true); return 0; }\n', b"true"),
    ("natm3-boolf", 'fn main(): int { print(false); return 0; }\n', b"false"),
    ("natm3-seq", 'fn main(): int { print(1); print(true); print("s"); return 0; }\n', b"1trues"),
]


def backend_bytes(combo_src, src):
    (code, off, hexstr) = bea._run_be(combo_src, [("prog", src)])[0]
    assert code == 0, (code, off)
    raw = bytes.fromhex(hexstr)
    ver, arch, hlen, res, entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
    assert (ver, arch, hlen, res, entry) == (2, 1, 28, 0, 0)
    assert fsz == msz and 1 <= csz <= 65536 and 0 <= fsz <= 32768
    assert len(raw) == 28 + csz + fsz
    return raw


def main():
    combo = bea._combo_text()
    work = ROOT / "build/native-m3"
    work.mkdir(parents=True, exist_ok=True)
    blobs = _compile_shell(work)
    from test_filesystem import GOOD_ENTRIES
    entries = list(GOOD_ENTRIES)
    entries.append(("/bin/sh", blobs["sh"]))
    entries.append(("/bin/echo", blobs["sh_echo"]))
    for name, src, _want in NATIVE_M3:
        raw = backend_bytes(combo, src)
        entries.append(("/bin/" + name, raw))
    drive = work / "m3.img"
    drive.write_bytes(fs_build(entries))
    dest = work / "image"
    build_image(ROOT, dest, shell_boot=True)
    keys = []
    for name, _src, _want in NATIVE_M3:
        keys += K(name + "\n")
    keys += K("echo NATQ-DONE\n")
    logs = work / "natq-0"
    out = boot_image(dest / "rynoros.img", logs, timeout=60,
                     extra_drives=(drive,), require_sh=True,
                     sh_keys=tuple(keys), sh_done=b"NATQ-DONE")
    dones = collect_done_statuses(out)
    print("dones=", dones)
    from tools.rynorlang import analyze as analyzer
    from tools.rynorlang import rir as rir_mod
    from tools.rynorlang import interp as oracle_mod
    ok = True
    assert len(dones) == len(NATIVE_M3) + 1, dones
    text = out.decode("utf-8", "replace")
    import re
    slot1 = []
    for l in text.splitlines():
        if l.startswith("[LOAD] write slot=1"):
            m = re.search(r"hex=([0-9a-f]+)", l)
            if m:
                slot1.append(bytes.fromhex(m.group(1)))
    # Last slot-1 write is the NATQ-DONE echo's trailing newline; drop it.
    guest_out = [b for b in slot1 if b != b"\n"]
    # NATQ-DONE marker itself is a shell echo, not guest output.
    guest_out = [b for b in guest_out if b != b"NATQ-DONE"]
    # natm3-seq emits three writes (1, true, s); join per-program by
    # oracle-declared grouping: each program's writes concatenate.
    joined = b"".join(guest_out)
    print("guest slot-1 writes=", [bytes(b).decode() for b in guest_out])
    pos = 0
    for (name, src, want), got in zip(NATIVE_M3, dones):
        result = analyzer.analyze(src, "t.rl", profile="core")
        assert result.ok, name
        module, error = rir_mod.build_rir(result.ast, "t.rl")
        assert error is None, name
        emitted = []
        outcome = oracle_mod.run_rir(module, out=emitted)
        want_exit = outcome["exit"] & 0xFF
        want_out = b"".join(e if isinstance(e, bytes) else str(e).encode() for e in emitted)
        assert want_out == want, (name, want_out, want)
        got_out = joined[pos:pos + len(want)]
        pos += len(want)
        match = (got == want_exit) and (got_out == want)
        ok = ok and match
        print('%s native=%d oracle=%d out=%r %s' % (name, got, want_exit, want, 'OK' if match else 'MISMATCH'))
    assert pos == len(joined), (pos, len(joined))
    print('NATIVE_ALL_OK' if ok else 'NATIVE_FAILED')


if __name__ == "__main__":
    main()
