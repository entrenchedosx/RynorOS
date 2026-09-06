"""Stage 18a integration: protected userspace in QEMU.

One shared good boot feeds the evidence assertions; mutant kernels boot
separately. The guest runs CPL3 blobs for real (entry/exit/faults/timer
preemption); the host rechecks every deterministic number.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image, boot_complete
from user_output import parse_serial, validate
from boot_output import validate_boot_output


def _mutate_copy(pairs, source):
    tmp = tempfile.TemporaryDirectory(prefix="user-fault-", dir=ROOT / "build")
    root = Path(tmp.name)
    from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
    for d in REQUIRED_DIRECTORIES:
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_FILES:
        shutil.copyfile(ROOT / f, root / f)
    path = root / source
    contents = path.read_text(encoding="utf-8")
    for old, new in pairs:
        if contents.count(old) != 1:
            raise AssertionError(old)
        contents = contents.replace(old, new)
    path.write_text(contents, encoding="utf-8")
    return tmp, root


class UserspaceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = ROOT / "build/user-tests"
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.destination = cls.work / "image"
        build_image(ROOT, cls.destination)
        logs = cls.work / "shared-good"
        cls.output = boot_image(cls.destination / "rynoros.img", logs, timeout=60)
        summary = __import__("json").loads((logs / "run.json").read_text(encoding="utf-8"))
        assert summary["reaped"], summary

    def test_good_user_full_evidence(self):
        self.assertEqual(validate(parse_serial(self.output)), [])

    def test_boot_output_accepts_user_section(self):
        self.assertEqual(validate_boot_output(self.output), [])

    def test_exit_and_yield_rows_exact(self):
        evidence = parse_serial(self.output)
        self.assertEqual(sorted(evidence.exits), [(0, 7), (0, 9), (0, 42), (1, 7)])
        self.assertEqual(evidence.yields, [(0, 1)])

    def test_fault_matrix_exact(self):
        evidence = parse_serial(self.output)
        self.assertEqual([(v, e) for _, v, e, _, _ in evidence.faults],
                         [(6, 0), (14, 5), (14, 4), (14, 7),
                          (14, 0x15), (13, 0), (14, 4),
                          (14, 5), (14, 7), (14, 0x15), (14, 0x15),
                          (13, 0), (13, 0x10), (13, 0x40), (13, 0x1C),
                          (13, 0x08), (13, 0x18), (13, 0x20),
                          (0, 0), (6, 0), (13, 0), (14, 7),
                          (128, 0x99)])
        self.assertEqual(len(evidence.creates), 30)
        self.assertEqual(len(evidence.destroys), 30)
        # v==14 rows in print order: five fixed landmarks, three
        # supervisor-violation reads/writes, the kernel fetch, the stack
        # fetch, then the kernel-stack push fault.
        f14 = [(e, rip, cr2) for _, v, e, rip, cr2 in evidence.faults
               if v == 14]
        self.assertEqual([c for _, _, c in f14[:5]],
                         [0x8000, 0xFFFFFFFF80000000, 0x400000,
                          0x600000, 0x0])
        self.assertEqual((f14[8][1], f14[8][2]), (0x7FF000, 0x7FF000))
        # Supervisor-violation rows carry link addresses the host cannot
        # pin: assert the violation shape (canonical, outside the whole
        # user range) and, for the fetch row, rip == cr2.
        for e, rip, cr2 in (f14[5], f14[6], f14[7], f14[9]):
            self.assertIn(e, (5, 7, 0x15))
            self.assertIn(cr2 >> 48, (0, 0xFFFF))
            self.assertNotEqual(cr2, 0)
            self.assertFalse(0x400000 <= cr2 < 0x800000)
        self.assertEqual(f14[7][1], f14[7][2])

    def test_preemption_counts_exact(self):
        evidence = parse_serial(self.output)
        self.assertEqual(evidence.preempts, [(0, 13)])
        self.assertEqual(evidence.worker_preempts, [13])
        self.assertEqual(evidence.cpl3, [(26, 26, 28)])

    def test_markers_and_accounting(self):
        evidence = parse_serial(self.output)
        self.assertEqual(evidence.balanced, 7)
        for marker in (b"[USER] lifecycle verified",
                       b"[USER] oom rollback verified",
                       b"[USER] gate verified",
                       b"[USER] faults verified",
                       b"[USER] preemption verified"):
            self.assertIn(marker, evidence.markers)

    def _run_user_mutation(self, pairs, source, timeout=60):
        tmp, root = _mutate_copy(pairs, source)
        self.addCleanup(tmp.cleanup)
        build_image(root, root / "build" / "img")
        logs = self.work / self._testMethodName
        try:
            output = boot_image(root / "build" / "img" / "rynoros.img", logs,
                                timeout=timeout)
            return output, None
        except RuntimeError as err:
            serial = (logs / "serial.log").read_bytes()
            return serial, str(err)

    def test_mut_printed_vector_detected(self):
        # Guest logic intact (boots green) but the printed fault vector is
        # wrong: only the host validator can catch this class.
        output, error = self._run_user_mutation([(
            '    field(" vector=", c->fault_vector);',
            '    field(" vector=", c->fault_vector + 1);',
        )], source="kernel/core/user.c")
        self.assertIsNone(error)
        self.assertIn(b"[USER] user verified", output)
        self.assertTrue(validate(parse_serial(output)))

    def test_mut_printed_ticks_detected(self):
        output, error = self._run_user_mutation([(
            '    field(" ticks=", s1.ticks - s0.ticks);',
            '    field(" ticks=", s1.ticks - s0.ticks + 1);',
        )], source="kernel/core/user-test.c")
        self.assertIsNone(error)
        self.assertIn(b"[USER] user verified", output)
        self.assertTrue(validate(parse_serial(output)))

    def test_mut_exit_code_assert_fails_guest(self):
        # Guest-side expectation broken: the guest must halt itself loudly
        # before the final marker, and the host must reject the transcript.
        output, _error = self._run_user_mutation([(
            "    require(c->state == USER_EXITED && c->exit_code == 42 && c->gate_exits == 1, \"exit_record\");",
            "    require(c->state == USER_EXITED && c->exit_code == 43 && c->gate_exits == 1, \"exit_record\");",
        )], source="kernel/core/user-test.c")
        self.assertIn(b"[USER] failure=exit_record", output)
        self.assertNotIn(b"[USER] user verified", output)
        self.assertTrue(validate(parse_serial(output)))

    def test_mut_idt_gate_dpl_breaks_userspace(self):
        # The 0x80 gate without DPL3 is unreachable from CPL3: the first
        # gate exit faults instead, the guest fails its own record
        # assert, and the boot never completes.
        output, error = self._run_user_mutation([(
            "        (cpu_u16)gate, CPU_CODE_SELECTOR, 0, 0xee,",
            "        (cpu_u16)gate, CPU_CODE_SELECTOR, 0, 0x8e,",
        )], source="kernel/arch/x86_64/cpu.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[USER] user verified", output)

    def test_mut_gdt_user_dpl_breaks_entry(self):
        # A DPL0 user-code descriptor is caught by the build-time GDT
        # verifier before any CPL3 entry: fail closed at init.
        output, error = self._run_user_mutation([(
            "    kernel_gdt[4] = 0x00affb000000ffffULL;",
            "    kernel_gdt[4] = 0x00af9b000000ffffULL;",
        )], source="kernel/arch/x86_64/cpu.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[USER] user verified", output)

    def test_mut_rsp0_desync_breaks_exit(self):
        # RSP0 aimed into user memory puts the exit frame off the exit
        # stack: the handler must reject it instead of trusting it.
        # (A desync into unmapped memory triple-faults even earlier;
        # both are fail-closed, this one asserts the handler check.)
        output, error = self._run_user_mutation([(
            "    cpu_set_rsp0(c->exit_top);\n    ++c->entries;",
            "    cpu_set_rsp0(USER_DATA_BASE + VM_PAGE_SIZE);\n    ++c->entries;",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertIn(b"[USER] failure=exit_rsp0", output)
        self.assertNotIn(b"[USER] user verified", output)

    def test_mut_supervisor_code_breaks_entry(self):
        # A user code page mapped supervisor-only faults on CPL3 fetch:
        # containment, never silent entry.
        output, error = self._run_user_mutation([(
            "    if (vm_map(&c->space, USER_CODE_BASE, c->code_frame, VM_USER | VM_EXECUTE) != VM_OK ||",
            "    if (vm_map(&c->space, USER_CODE_BASE, c->code_frame, VM_EXECUTE) != VM_OK ||",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[USER] user verified", output)

    def test_mut_skipped_transition_detected(self):
        # The transition itself stubbed to a fake success: exact exit
        # codes, fault rows, tick and preemption counts all disagree, so
        # the guest must fail itself before the final marker.
        output, error = self._run_user_mutation([(
            "    ++c->entries;\n    return user_enter_asm(build_frame(c), &link->kern_save, c->space.root);",
            "    ++c->entries;\n    c->state = USER_EXITED; c->exit_code = 0;\n    return USER_RUN_EXITED;",
        )], source="kernel/core/user.c", timeout=20)
        self.assertIsNotNone(error)
        self.assertNotIn(b"[USER] user verified", output)

    def test_completion_requires_verified_marker(self):
        # Lock in the completion-race fix: a fully valid transcript is
        # complete if and only if the final marker is present.
        self.assertTrue(boot_complete(self.output))
        stripped = self.output.replace(b"[USER] user verified\r\n", b"")
        self.assertNotIn(b"[USER] user verified", stripped)
        self.assertFalse(boot_complete(stripped))

    def test_completion_rejects_partial_userspace(self):
        # Shell-complete with a started-but-unfinished userspace section
        # (the old premature-success window) is not completion.
        head, sep, _ = self.output.partition(b"[USER] self-test started\r\n")
        self.assertTrue(sep)
        partial = head + sep + b"[USER] create slot=0 code_size=14 tables=6\r\n"
        self.assertFalse(boot_complete(partial))

    def test_completion_rejects_shell_only_boot(self):
        # Shell-complete with no userspace section at all: valid prefix,
        # not a complete boot.
        from shell_output import SHELL_END
        head, sep, _ = self.output.partition(SHELL_END)
        self.assertTrue(sep)
        self.assertFalse(boot_complete(head + sep))


if __name__ == "__main__":
    unittest.main()
