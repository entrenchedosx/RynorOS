"""Native QEMU proof for the self-host backend (Stage 19e).

BE-A M7 (wrong-exit-register mutant) was PENDING for lack of native
execution; it is closed here: baseline exits 42 natively, the mutant
exits 0 natively (mov ecx,eax instead of mov ebx,eax in _start), for
the intended semantic reason on real hardware emulation.

A compact native smoke set covers BE-A/BE-B/BE-C slices; every native
exit is compared against the trusted host oracle. Emulator, oracle,
byte-validation, and mutant suites are retained; native proof
complements them.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
sys.path.insert(0, str(ROOT / "tools/rynorlang"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/integration"))
from image import build_image
from qemu import boot_image
from fs_image import build as fs_build
from sh_output import collect_done_statuses, collect_load_writes
from test_cplshell import _compile_shell, K, _slot_blob
from tests.repository import test_rynorlang_selfhost_emit as bea


M7_SRC = "fn main(): int { return 42; }\n"

SMOKE_CASES = [
    ("nat00", "fn main(): int { return 42; }\n"),  # BE-A const
    ("nat01", "fn main(): int { return 1 + 2 * 3; }\n"),  # BE-A arith
    ("nat02", "fn main(): int { return 4 * 5; }\n"),  # BE-A mul
    ("nat03", "fn main(): int { return 0 - (0 - 7); }\n"),  # BE-A signed
    ("nat04", "fn id(x: int): int { return x; }\nfn main(): int { return id(7); }\n"),  # BE-B 1 param
    ("nat05", "fn sub(a: int, b: int): int { return a - b; }\nfn main(): int { return sub(9, 2); }\n"),  # BE-B noncomm
    ("nat06", "fn add(a: int, b: int): int { return a + b; }\nfn main(): int { let x: int = add(2, 3); return x * 4; }\n"),  # BE-B locals
    ("nat07", "fn id(x: int): int { return x; }\nfn add(a: int, b: int): int { return a + b; }\nfn main(): int { return add(id(3), 4); }\n"),  # BE-B nested
    ("nat08", "fn main(): int { if 1 == 1 { return 17; } else { return 93; } }\n"),  # BE-C if/else
    ("nat09", "fn main(): int { if 2 < 2 { return 17; } else { return 93; } }\n"),  # BE-C cmp boundary
    ("nat10", "fn main(): int { while true { break; } return 8; }\n"),  # BE-C while/break
    ("nat11", "fn main(): int { while true { while true { break; } break; } return 13; }\n"),  # BE-C nested
    ("nat12", "fn f(x: int): int { return x * 3; }\nfn g(x: int): int { if x == 1 { return f(4); } return f(5); }\nfn main(): int { return g(1) + g(2); }\n"),  # BE-C calls+branch
]

M7_MUT_OLD = ("fn e_start(mainoff: int, acc: int): int {\n"
              "  let disp: int = mainoff - 5;\n"
              "  let a0: int = e_b(232, acc);\n"
              "  let a1: int = e_le32(disp, a0);\n"
              "  let a2: int = e_b(137, a1);\n"
              "  let a3: int = e_b(195, a2);")
M7_MUT_NEW = ("fn e_start(mainoff: int, acc: int): int {\n"
              "  let disp: int = mainoff - 5;\n"
              "  let a0: int = e_b(232, acc);\n"
              "  let a1: int = e_le32(disp, a0);\n"
              "  let a2: int = e_b(137, a1);\n"
              "  let a3: int = e_b(193, a2);")

NATIVE_PRINT_CASES = [
    ("natd0", 'fn main(): int { print("x"); return 0; }\n', b"x", 0),
    ("natd1", 'fn main(): int { print("hello"); return 0; }\n', b"hello", 0),
    ("natd2", 'fn main(): int { print("a\\nb"); return 0; }\n', b"a\nb", 0),
    ("natd3", 'fn main(): int { print("ab"); print("cde"); return 5; }\n', b"abcde", 5),
    ("natd4", 'fn main(): int { if true { print("T"); } else { print("F"); } return 1; }\n', b"T", 1),
    ("natd5", 'fn h(): int { print("h"); return 0; }\nfn main(): int { h(); return 4; }\n', b"h", 4),
]

NATIVE_RECORD_CASES = [
    "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 10, b: 32); return p->b; }\n",
    "record Inner { x: int }\nrecord Outer { a: int, inner: Inner, b: int }\nfn main(): int { let o: Outer = Outer(a: 1, inner: Inner(x: 2), b: 3); return o->a + o->inner->x + o->b; }\n",
    "record Pair { a: int, b: int }\nfn get_x(p: Pair): int { return p->a; }\nfn main(): int { return get_x(Pair(a: 7, b: 8)); }\n",
    "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn main(): int { let p: Pair = mk(3, 4); return p->a + p->b; }\n",
    "record Pair { a: int, b: int }\nfn mk(a: int, b: int): Pair { return Pair(a: a, b: b); }\nfn sum(p: Pair): int { return p->a + p->b; }\nfn main(): int { return sum(mk(20, 22)); }\n",
    "record B { x: int }\nrecord A { y: int, x: int }\nfn f(p: A): int { return p->x; }\nfn main(): int { return f(A(y: 5, x: 7)); }\n",
    "record Pair { a: int, b: int }\nfn main(): int { let p: Pair = Pair(a: 1, b: 2); if p->a == 1 { return 17; } else { return 93; } }\n",
]


def _backend_bytes(combo_src, src):
    (code, off, hexstr) = bea._run_be(combo_src, [("prog", src)])[0]
    assert code == 0, (code, off)
    raw = bytes.fromhex(hexstr)
    bea._parse_rnyx(raw)
    return raw


def _backend_bytes_data(combo_src, src):
    import struct
    (code, off, hexstr) = bea._run_be(combo_src, [("prog", src)])[0]
    assert code == 0, (code, off)
    raw = bytes.fromhex(hexstr)
    ver, arch, hlen, res, entry, csz, fsz, msz = struct.unpack("<HHHHIIII", raw[4:28])
    assert (ver, arch, hlen, res, entry) == (2, 1, 28, 0, 0)
    assert fsz == msz and 1 <= csz <= 65536 and 0 <= fsz <= 32768
    assert len(raw) == 28 + csz + fsz
    return raw


class NativeBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/native-backend"
        cls.work.mkdir(parents=True, exist_ok=True)
        combo = bea._combo_text()
        assert combo.count(M7_MUT_OLD) == 1
        mutant_combo = combo.replace(M7_MUT_OLD, M7_MUT_NEW)
        cls.m7_base = _backend_bytes(combo, M7_SRC)
        cls.m7_mut = _backend_bytes(mutant_combo, M7_SRC)
        cls.smoke = []
        for name, src in SMOKE_CASES:
            cls.smoke.append((name, _backend_bytes(combo, src)))
        blobs = _compile_shell(cls.work)
        from test_filesystem import GOOD_ENTRIES
        entries = list(GOOD_ENTRIES)
        entries.append(("/bin/sh", blobs["sh"]))
        entries.append(("/bin/echo", blobs["sh_echo"]))
        entries.append(("/bin/natm7", cls.m7_base))
        entries.append(("/bin/natm7m", cls.m7_mut))
        for name, raw in cls.smoke:
            entries.append(("/bin/" + name, raw))
        cls.natprint = []
        for name, src, _w, _e in NATIVE_PRINT_CASES:
            cls.natprint.append((name, _backend_bytes_data(combo, src)))
        cls.natrec = []
        for i, src in enumerate(NATIVE_RECORD_CASES):
            cls.natrec.append(("natr%d" % i, _backend_bytes(combo, src)))
        for name, raw in cls.natprint:
            entries.append(("/bin/" + name, raw))
        for name, raw in cls.natrec:
            entries.append(("/bin/" + name, raw))
        cls.drive = cls.work / "native.img"
        cls.drive.write_bytes(fs_build(entries))
        cls.dest = cls.work / "image"
        build_image(ROOT, cls.dest, shell_boot=True)

    @classmethod
    def _boot(cls, names, tag):
        keys = []
        for name in names:
            keys += K(name + "\n")
        keys += K("echo %s-DONE\n" % tag.upper().replace("-", ""))
        logs = cls.work / ("%s-%s" % (tag, len(list(cls.work.glob(tag + "-*")))))
        out = boot_image(cls.dest / "rynoros.img", logs, timeout=60,
                         extra_drives=(cls.drive,), require_sh=True,
                         sh_keys=tuple(keys),
                         sh_done=("%s-DONE" % tag.upper().replace("-", "")).encode())
        return collect_done_statuses(out)

    def test_m7_baseline_native(self):
        dones = self._boot(["natm7"], tag="m7base")
        self.assertEqual(dones[0], 42)

    def test_m7_mutant_red_native(self):
        dones = self._boot(["natm7m"], tag="m7mut")
        self.assertEqual(dones[0], 0)

    def test_native_smoke_matches_oracle(self):
        names = [name for name, _src in SMOKE_CASES]
        dones = self._boot(names, tag="smoke")
        self.assertEqual(len(dones), len(names) + 1)
        for (name, src), got in zip(SMOKE_CASES, dones):
            self.assertEqual(got, bea._oracle_exit(src), name)

    def test_native_print_stdout_and_exit(self):
        names = [name for name, _src, _w, _e in NATIVE_PRINT_CASES]
        keys = []
        for name in names:
            keys += K(name + "\n")
        keys += K("echo NATP-DONE\n")
        logs = self.work / ("natp-%s" % len(list(self.work.glob("natp-*"))))
        out = boot_image(self.dest / "rynoros.img", logs, timeout=60,
                         extra_drives=(self.drive,), require_sh=True,
                         sh_keys=tuple(keys), sh_done=b"NATP-DONE")
        dones = collect_done_statuses(out)
        self.assertEqual(len(dones), len(names) + 1)
        for (name, _src, _w, want), got in zip(NATIVE_PRINT_CASES, dones):
            self.assertEqual(got, want, name)
        blob = _slot_blob(collect_load_writes(out), 1)
        for _name, _src, want_out, _e in NATIVE_PRINT_CASES:
            self.assertIn(want_out.hex(), blob)

    def test_native_records_match_oracle(self):
        from tools.rynorlang import analyze as analyzer
        from tools.rynorlang import rir as rir_mod
        from tools.rynorlang import interp as oracle_mod
        names = ["natr%d" % i for i in range(len(NATIVE_RECORD_CASES))]
        dones = self._boot(names, tag="natrec")
        self.assertEqual(len(dones), len(names) + 1)
        for name, src, got in zip(names, NATIVE_RECORD_CASES, dones):
            result = analyzer.analyze(src, "t.rl", profile="core")
            self.assertTrue(result.ok, name)
            module, error = rir_mod.build_rir(result.ast, "t.rl")
            self.assertIsNone(error, name)
            outcome = oracle_mod.run_rir(module, out=[])
            self.assertEqual(got, outcome["exit"] & 0xFFFFFFFF, name)


if __name__ == "__main__":
    unittest.main()
