"""M8 native QEMU proof: baby-compiled str-return programs as CPL3 guests."""
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

HEXCH = ('fn hexch(d: int): str { let h: list<str,16> = [' + Q + '0' + Q + ', ' + Q + '1' + Q + ', ' + Q + '2' + Q + ', ' + Q + '3' + Q + ', ' + Q + '4' + Q + ', ' + Q + '5' + Q + ', ' + Q + '6' + Q + ', ' + Q + '7' + Q + ', ' + Q + '8' + Q + ', ' + Q + '9' + Q + ', ' + Q + 'a' + Q + ', ' + Q + 'b' + Q + ', ' + Q + 'c' + Q + ', ' + Q + 'd' + Q + ', ' + Q + 'e' + Q + ', ' + Q + 'f' + Q + ']; return unwrap_or(h[d], ' + Q + '?' + Q + '); }\n')

NATIVE_M8 = [
    ("natm8-lit", 'fn f(): str { return ' + Q + 'ab' + Q + '; }\nfn main(): int { let u: str = f(); return len(u); }\n'),
    ("natm8-nest", 'fn g(): str { return ' + Q + 'q' + Q + '; }\nfn f(): str { return g(); }\nfn main(): int { let u: str = f(); return unwrap_or(byte_at(u, 0), 0); }\n'),
    ("natm8-hexch", HEXCH + 'fn main(): int { let b: str = hexch(15); return unwrap_or(byte_at(b, 0), 0); }\n'),
    ("natm8-hexchoob", HEXCH + 'fn main(): int { let u: str = hexch(16); return unwrap_or(byte_at(u, 0), 0); }\n'),
    ("natm8-callarg", 'fn g(): str { return ' + Q + 'abcd' + Q + '; }\nfn f(s: str): int { return len(s); }\nfn main(): int { return f(g()); }\n'),
    ("natm8-wide", 'fn h(a: int, b: int, c: int, d: int, e: int, f: int, g: int): str { return ' + Q + 'q' + Q + '; }\nfn main(): int { let u: str = h(1, 2, 3, 4, 5, 6, 7); return unwrap_or(byte_at(u, 0), 0); }\n'),
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
    work = ROOT / "build/native-m8"
    work.mkdir(parents=True, exist_ok=True)
    blobs = _compile_shell(work)
    from test_filesystem import GOOD_ENTRIES
    entries = list(GOOD_ENTRIES)
    entries.append(("/bin/sh", blobs["sh"]))
    entries.append(("/bin/echo", blobs["sh_echo"]))
    for name, src in NATIVE_M8:
        raw = backend_bytes(combo, src)
        entries.append(("/bin/" + name, raw))
    drive = work / "m8.img"
    drive.write_bytes(fs_build(entries))
    dest = work / "image"
    build_image(ROOT, dest, shell_boot=True)
    keys = []
    for name, _src in NATIVE_M8:
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
    assert len(dones) == len(NATIVE_M8) + 1, dones
    for (name, src), got in zip(NATIVE_M8, dones):
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
