"""M6 native QEMU proof: compile M6 fjoin/argv/use shapes with the baby
backend, bake as CPL3 guests, boot RynorOS in QEMU, compare exit
statuses vs oracle. Mirrors probe_m1native.py infrastructure.

Shell argv: typing `name arg...` spawns with argv[0]=name plus the
typed words (sh.c build_spec forwards cmd->argc/argv); the oracle
argv vectors below mirror exactly that, and natm6-a0 pins argv[0].
"""
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

Q = chr(34)
NATIVE_M6 = [
    ("natm6-join", 'fn main(): int { let s: status<str> = fjoin(' + Q + 'ab' + Q + ', ' + Q + 'cd' + Q + '); let u: str = unwrap_or(s, ' + Q + 'd' + Q + '); return len(u); }\n', "", ["natm6-join"]),
    ("natm6-direct", 'fn main(): int { let s: status<str> = fjoin(' + Q + '' + Q + ', ' + Q + 'xyz' + Q + '); let u: str = unwrap_or(s, ' + Q + 'd' + Q + '); return len(u); }\n', "", ["natm6-direct"]),
    ("natm6-err", 'fn main(): int { let s: status<str> = fjoin(' + Q + 'a' + Q + ', ' + Q + '' + Q + '); let u: str = unwrap_or(s, ' + Q + 'dflt' + Q + '); return len(u); }\n', "", ["natm6-err"]),
    ("natm6-use", 'fn main(): int { use ' + Q + 'm' + Q + '; return 9; }\n', "", ["natm6-use"]),
    ("natm6-a0", 'fn main(): int { let s: status<str> = argv(0); let u: str = unwrap_or(s, ' + Q + 'dd' + Q + '); return len(u); }\n', "", ["natm6-a0"]),
    ("natm6-a1", 'fn main(): int { let s: status<str> = argv(1); let u: str = unwrap_or(s, ' + Q + 'dd' + Q + '); return len(u); }\n', "hello", ["natm6-a1", "hello"]),
    ("natm6-aob", 'fn main(): int { let s: status<str> = argv(3); let u: str = unwrap_or(s, ' + Q + 'dd' + Q + '); return len(u); }\n', "", ["natm6-aob"]),
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
    work = ROOT / "build/native-m6"
    work.mkdir(parents=True, exist_ok=True)
    blobs = _compile_shell(work)
    from test_filesystem import GOOD_ENTRIES
    entries = list(GOOD_ENTRIES)
    entries.append(("/bin/sh", blobs["sh"]))
    entries.append(("/bin/echo", blobs["sh_echo"]))
    raws = []
    for name, src, _args, _argv in NATIVE_M6:
        raw = backend_bytes(combo, src)
        raws.append(raw)
        entries.append(("/bin/" + name, raw))
    drive = work / "m6.img"
    drive.write_bytes(fs_build(entries))
    dest = work / "image"
    build_image(ROOT, dest, shell_boot=True)
    keys = []
    for name, _src, args, _argv in NATIVE_M6:
        line = name if not args else name + " " + args
        keys += K(line + "\n")
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
    assert len(dones) == len(NATIVE_M6) + 1, dones
    for (name, src, _args, argv), got in zip(NATIVE_M6, dones):
        result = analyzer.analyze(src, "t.rl", profile="core")
        assert result.ok, name
        module, error = rir_mod.build_rir(result.ast, "t.rl")
        assert error is None, name
        want = oracle_mod.run_rir(module, out=[], argv=argv)["exit"] & 0xFFFFFFFF
        match = (got == (want & 0xFF))
        ok = ok and match
        print('%s native=%d oracle=%d(low8=%d) %s' % (name, got, want, want & 0xFF, 'OK' if match else 'MISMATCH'))
    print('NATIVE_ALL_OK' if ok else 'NATIVE_FAILED')


if __name__ == "__main__":
    main()
