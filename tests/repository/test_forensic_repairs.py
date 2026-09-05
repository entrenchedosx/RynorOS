"""Forensic readiness regressions — host-verifiable repairs, no QEMU required."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class ForensicRepairTests(unittest.TestCase):
    def test_lex_string_api_rejects_non_ascii_without_raising(self):
        # Arbitrary Python strings, including unencodable lone surrogates, must
        # produce the frozen lexical diagnostic rather than an encoding error.
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location("forensic_lex", ROOT / "tools/rynorlang/lex.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        for source in (chr(233), chr(0xD800), "ok\n" + chr(0xDFFF)):
            r = mod.lex(source)
            self.assertEqual(r.diagnostic.code, "LEX_INVALID_CHAR")
        oversized = "a" * (mod.MAX_SOURCE_BYTES + 1)
        r = mod.lex(oversized)
        self.assertEqual(r.diagnostic.code, "LEX_FILE_TOO_LARGE")
        self.assertEqual(r.diagnostic.span.offset, mod.MAX_SOURCE_BYTES)

    def test_kernel_irq_context_complete(self):
        # All PMM/VM entry points must reject IRQ context, not just IF=0.
        pmm = (ROOT / "kernel/mm/pmm.c").read_text(encoding="utf-8")
        vm = (ROOT / "kernel/mm/vm.c").read_text(encoding="utf-8")
        heap = (ROOT / "kernel/mm/heap.c").read_text(encoding="utf-8")
        for needle in ("pmm_initialize", "vm_create", "vm_initialize", "vm_frame_access"):
            src = pmm if "pmm" in needle else vm
            # The function body must mention irq_in_context after its definition.
            idx = src.find(needle)
            self.assertNotEqual(idx, -1, needle)
            window = src[idx:idx + 800]
            self.assertIn("irq_in_context", window, f"{needle} must check irq_in_context")
        self.assertIn("cpu_interrupts_disabled() && !irq_in_context()", heap)
        self.assertIn('#include "irq.h"', pmm)
        self.assertIn('#include "irq.h"', vm)

    def test_build_invalidates_stale_image_before_compile(self):
        text = (ROOT / "tools/build/build.py").read_text(encoding="utf-8")
        # build() must unlink stale artifacts before validate and py_compile loop.
        build_fn = text[text.find("def build()"):text.find("def test()")]
        self.assertIn("ARTIFACTS", build_fn)
        self.assertIn("unlink", build_fn)
        self.assertLess(build_fn.find("unlink"), build_fn.find("py_compile"))
        self.assertLess(build_fn.find("unlink"), build_fn.find("if not validate"))

    def test_shell_mutate_no_bare_assert(self):
        text = (ROOT / "tests/integration/test_shell.py").read_text(encoding="utf-8")
        # Module-level _mutate must not use bare assert (stripped under -O).
        mutate = text[text.find("def _mutate"):text.find("class ShellIntegrationTests")]
        self.assertNotIn("\n        assert ", mutate)
        self.assertIn("raise AssertionError", mutate)

    def test_docs_counts_current(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        shell = (ROOT / "docs/design/shell.md").read_text(encoding="utf-8")
        self.assertIn("299 repository", readme)
        self.assertIn("162 integration", readme)
        self.assertIn("299", arch)
        self.assertIn("162", arch)
        self.assertIn("299 repository", shell)
        self.assertIn("162 integration", shell)
        self.assertNotIn("253 repository", readme + shell)

    def test_arch_irq_invariant(self):
        arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("!irq_in_context()", arch)

    def test_shell_gate_order_truth(self):
        shell_md = (ROOT / "docs/design/shell.md").read_text(encoding="utf-8")
        main_c = (ROOT / "kernel/core/main.c").read_text(encoding="utf-8")
        # Code: gate precedes shell_self_test; doc must say after gate.
        self.assertLess(main_c.find("pmm_check"), main_c.find("shell_self_test"))
        self.assertIn("after the final", shell_md)

    def test_readme_audit_links(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("stage8-audit.md", readme)
        self.assertIn("stage10-audit.md", readme)
        # Must not point primary Stage10 milestone to superseded report.
        self.assertNotIn("docs/reports/stage10.md]", readme)

    def test_qemu_invalidates_before_validation(self):
        text = (ROOT / "tools/host/qemu.py").read_text(encoding="utf-8")
        fn = text[text.find("def boot_image"):text.find('machine = "pc-i440fx')]
        self.assertIn('write_bytes(b"")', fn)
        self.assertIn('unlink(missing_ok=True)', fn)
        self.assertIn("run.json", fn)
        # Invalidation must precede validation failures.
        self.assertLess(fn.find("mkdir"), fn.find("key_sequence"))
        self.assertLess(fn.find("run.json"), fn.find("Boot timeout"))
        self.assertLess(fn.find("run.json"), fn.find("Boot image missing"))


if __name__ == "__main__":
    unittest.main()
