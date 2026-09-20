"""M4 native QEMU proof: baby-compiled recursion programs as CPL3 guests."""
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

NATIVE_M4 = [
    ("natm4-fact", 'fn fact(x: int): int { if x == 0 { return 1; } return x * fact(x - 1); }\nfn main(): int { return fact(5); }\n'),
    ("natm4-fib", 'fn fib(x: int): int { if x == 0 { return 0; } if x == 1 { return 1; } return fib(x - 1) + fib(x - 2); }\nfn main(): int { return fib(6); }\n'),
    ("natm4-sum", 'fn sum(x: int): int { if x == 0 { return 0; } return x + sum(x - 1); }\nfn main(): int { return sum(10); }\n'),
    ("natm4-deep", 'fn foo(x: int): int { if x == 0 { return 0; } return foo(x - 1); }\nfn main(): int { return foo(50); }\n'),
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
    work = ROOT / "build/native-m4"
    work.mkdir(parents=True, exist_ok=True)
    blobs = _compile_shell(work)
    from test_filesystem import GOOD_ENTRIES
    entries = list(GOOD_ENTRIES)
    entries.append(("/bin/sh", blobs["sh"]))
    entries.append(("/bin/echo", blobs["sh_echo"]))
    for name, src in NATIVE_M4:
        raw = backend_bytes(combo, src)
        entries.append(("/bin/" + name, raw))
    drive = work / "m4.img"
    drive.write_bytes(fs_build(entries))
    dest = work / "image"
    build_image(ROOT, dest, shell_boot=True)
    keys = []
    for name, _src in NATIVE_M4:
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
    assert len(dones) == len(NATIVE_M4) + 1, dones
    for (name, src), got in zip(NATIVE_M4, dones):
        result = analyzer.analyze(src, "t.rl", profile="core")
        assert result.ok, name
        module, error = rir_mod.build_rir(result.ast, "t.rl")
        assert error is None, name
        want = oracle_mod.run_rir(module, out=[])["exit"] & 0xFFFFFFFF
        match = (got == (want & 0xFF))
        ok = ok and match
        print('%s native=%d oracle=%d(low8=%d) %s' % (name, got, want, want & 0xFF, 'OK' if match else 'MISMATCH'))
    print('NATIVE_ALL_OK' if ok else 'NATIVE_FAILED')


if __name__ == "__main__":
    main()
