"""Stage 10 real-boot evidence and scoped mutations for the basic runtime."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import importlib.util
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from runtime_output import (RUNTIME_START, RUNTIME_END, RUNTIME_GOOD, W_INPUT,
                            worker_acc, total_fold, fnv1a, parse_runtime_output,
                            validate_runtime_output)
from display_output import DISPLAY_START, DISPLAY_END, parse_display_output
from boot_output import POST_IRQ


class RuntimeTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual((state["cleanup"], state["returncode"]), ("monitor-quit", 0))

    def section(self, output: bytes, start, end) -> bytes:
        return output[output.index(start):output.index(end) + len(end)]

    def build_fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix="rt-fault-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for d in REQUIRED_DIRECTORIES:
            (root / d).mkdir(parents=True, exist_ok=True)
        for f in REQUIRED_FILES:
            shutil.copyfile(ROOT / f, root / f)
        return root

    def mutate(self, root, source, pairs):
        path = root / source
        contents = path.read_text()
        for old, new in pairs:
            self.assertEqual(contents.count(old), 1, old)
            contents = contents.replace(old, new)
        path.write_text(contents)
        return root

    def run_failure(self, reason, pairs, source="kernel/runtime/runtime-test.c"):
        root = self.mutate(self.build_fixture(), source, pairs)
        build_image(root)
        logs = ROOT / "build/rt-tests" / self._testMethodName
        try:
            with self.assertRaises(RuntimeError) as error:
                boot_image(root / "build/rynoros.img", logs, timeout=12)
        finally:
            self.cleanup(logs)
        logged = (logs / "serial.log").read_bytes()
        self.assertIn(RUNTIME_START, logged, 'Mutation failed before the runtime stage')
        self.assertIn(reason, str(error.exception) + logged.decode("ascii"))

    def run_success(self, pairs, source="kernel/runtime/runtime-test.c"):
        root = self.mutate(self.build_fixture(), source, pairs)
        build_image(root)
        logs = ROOT / "build/rt-tests" / self._testMethodName
        try:
            boot_image(root / "build/rynoros.img", logs, timeout=30)
        finally:
            self.cleanup(logs)
        return (logs / "serial.log").read_bytes()

    def run_vanilla(self):
        build_image(ROOT)
        logs = ROOT / "build/rt-tests" / self._testMethodName
        try:
            output = boot_image(ROOT / "build/rynoros.img", logs, timeout=30)
        finally:
            self.cleanup(logs)
        return output

    def test_real_runtime_folds_and_accounting(self):
        destination = ROOT / "build/rt-tests/normal with spaces"
        build_image(ROOT, destination)
        logs = destination / "logs"
        try:
            output = boot_image(destination / "rynoros.img", logs, timeout=30)
        finally:
            self.cleanup(logs)
        display = parse_display_output(self.section(output, DISPLAY_START, DISPLAY_END))
        runtime = parse_runtime_output(self.section(output, RUNTIME_START, RUNTIME_END), display)
        self.assertEqual(runtime["total"], total_fold())
        self.assertEqual(runtime["allocated"], display["allocated"])
        self.assertIn(POST_IRQ, output)

    def test_worker_accounts_match_host_folds(self):
        # The emitted per-worker acc values must equal an independent host fold.
        output = self.run_vanilla()
        section = self.section(output, RUNTIME_START, RUNTIME_END)
        for i, inp in enumerate(W_INPUT):
            token = ("[RUNTIME] worker=%d acc=0x%X" % (i, worker_acc(inp))).encode()
            self.assertIn(token, section)

    def test_format_exact_capacity(self):
        self.run_success([('static void string_tests(void)\n{',
            'static void string_tests(void)\n{\n'
            '    char exact[5];\n'
            '    require(kstr_format(exact, 5, "%s", "abcd") == KSTR_OK && exact[4] == 0, "audit_format_exact");\n'
            '    require(kstr_format(exact, 1, "%s", "") == KSTR_OK && exact[0] == 0, "audit_format_empty");')])

    def test_out_length_alias_rejected(self):
        self.run_success([('static void service_tests(void)\n{',
            'static void service_tests(void)\n{\n'
            '    cpu_u64 alias = 123;\n'
            '    require(krst_call(KRST_SVC_DIGEST, "ab", 2, &alias, 8, &alias) == KRST_BAD_ARGS && alias == 123, "audit_length_alias");')])

    def test_truthful_canned_runtime_is_rejected(self):
        # No intentionally wrong digest: the entire claimed serial result is
        # correct. Independent execution evidence must still reject the fake.
        canned = RUNTIME_GOOD.replace(b"free_bytes=921600", b"free_bytes=65802240").decode('ascii')
        self.run_failure("runtime execution evidence",
            [("void runtime_self_test(void)\n{\n",
              "void runtime_self_test(void)\n{\n    text(" + json.dumps(canned) + "); return;\n")])

    def test_krst_digest_mutation_caught_by_host(self):
        # Break the FNV constant: guest stays self-consistent, so only the
        # independent host recomputation can catch it.
        self.run_failure("worker digest/fold mismatch",
                         [("h *= 0x100000001b3ULL;", "h *= 0x100000001b9ULL;")],
                         source="kernel/runtime/krst.c")

    def test_worker_fold_mutation_caught_by_guest(self):
        # Change only the worker's fold but not the validator's: the guest
        # itself observes the mismatch.
        self.run_failure("[RUNTIME] failure=worker_digest_value",
                         [("acc = acc * 131 + d;", "acc = acc * 133 + d;")])

    def test_upper_service_cannot_pass(self):
        self.run_failure("[RUNTIME] failure=service_binary_upper",
                         [("c = (cpu_u8)(c - 32);", "c = (cpu_u8)c;")],
                         source="kernel/runtime/krst.c")

    def test_count_service_cannot_pass(self):
        self.run_failure("[RUNTIME] failure=reuse_worker_if0",
                         [("if (c >= '0' && c <= '9') ++count;", "if (0 && c >= '0' && c <= '9') ++count;")],
                         source="kernel/runtime/krst.c")

    def test_bounded_copy_overflow_cannot_pass(self):
        self.run_failure("[RUNTIME] failure=copy_guard_exact",
                         [("if (src[n] != 0) return KSTR_OVERFLOW;\n    return kstr_move(dst, src, n + 1);",
                           "if (0 && src[n] != 0) return KSTR_OVERFLOW;\n    return kstr_move(dst, src, n + 1);")],
                         source="kernel/runtime/kstring.c")

    def test_buffer_no_partial_write_cannot_pass(self):
        self.run_failure("[RUNTIME] failure=ring_full",
                         [("if (n > b->cap - b->count) return KBUF_FULL;",
                           "if (0 && n > b->cap - b->count) return KBUF_FULL;")],
                         source="kernel/runtime/kbuf.c")

    def test_worker_rounds_cannot_be_bypassed(self):
        self.run_failure("[RUNTIME] failure=worker_digest_value",
                         [("for (cpu_u64 r = 0; r < ROUNDS; ++r) {",
                           "for (cpu_u64 r = 0; r < 1; ++r) {")])

    def test_canned_success_cannot_prove_real_workers(self):
        # A forged transcript with a truthful-looking shape but a single wrong
        # digest cannot satisfy the host's independent FNV fold check.
        good = ("[RUNTIME] worker=0 acc=0x%X rounds=40" % worker_acc(W_INPUT[0])).encode()
        bad = ("[RUNTIME] worker=0 acc=0x%X rounds=40" % (worker_acc(W_INPUT[0]) + 1)).encode()
        total = ("[RUNTIME] total=%d" % total_fold()).encode()
        canned = (RUNTIME_GOOD
                  .replace(b"free_bytes=921600", b"free_bytes=65802240")
                  .replace(good, bad)
                  .replace(total, ("[RUNTIME] total=%d" % (total_fold() + 1)).encode())
                  .decode("ascii"))
        self.run_failure("worker digest/fold mismatch",
                         [("void runtime_self_test(void)\n{\n",
                           "void runtime_self_test(void)\n{\n    text(" + json.dumps(canned) + "); return;\n")],
                         source="kernel/runtime/runtime-test.c")

    def test_runtime_self_test_keeps_runner_armed(self):
        # runtime_self_test must still run in the real boot path after display.
        self.assertEqual(validate_runtime_output(self.section(
            self.run_vanilla(), RUNTIME_START, RUNTIME_END)), [])

    def test_no_timer_during_services_is_rejected(self):
        self.run_failure("service_preemption_missing", [
            ('irq_set_enabled(0, 1), "runtime_timer_start"', 'irq_set_enabled(0, 0), "runtime_timer_start"'),
            ('attempt <= 131072', 'attempt <= 64')])

    def test_physical_evidence_cannot_be_omitted(self):
        self.run_failure("runtime execution evidence", [
            ('text("[TEST] runtime api verified\\r\\n");',
             'for (unsigned int i = 0; i < WORKERS; ++i) runtime_evidence[i] = (struct worker_out){0};\n'
             '    text("[TEST] runtime api verified\\r\\n");')])

    def test_irq_service_calls_rejected(self):
        self.run_failure("runtime_irq_context",
            [('if (irq_in_context()) return KRST_BAD_CONTEXT;',
              'if (0 && irq_in_context()) return KRST_BAD_CONTEXT;')], source='kernel/runtime/krst.c')

    def test_source_bound_cannot_be_ignored(self):
        self.run_failure("format_guard",
            [('if (n == KSTR_NLEN_MAX) return KSTR_TERMINATION;',
              'if (n == KSTR_NLEN_MAX) return KSTR_OK;')], source='kernel/runtime/kstring.c')

    def test_explicit_source_limit_cannot_be_ignored(self):
        self.run_failure("source_guard",
            [('if (n == src_max) return KSTR_TERMINATION;\n    if (src[n] != 0) return KSTR_OVERFLOW;\n    return kstr_move(dst',
              'if (n == src_max) return KSTR_OK;\n    if (src[n] != 0) return KSTR_OVERFLOW;\n    return kstr_move(dst')],
            source='kernel/runtime/kstring.c')

    def test_format_exact_fit_regression_caught(self):
        self.run_failure("format_exact",
            [('if (n > cap - 1 - used)', 'if (n >= cap - 1 - used)')], source='kernel/runtime/kstring.c')

    def test_ring_head_validation_cannot_be_removed(self):
        self.run_failure("ring_head_corrupt",
            [('b->head < b->cap &&', '1 &&')], source='kernel/runtime/kbuf.c')

    def test_ring_wrong_stride_detected(self):
        self.run_failure("ring_order",
            [('b->data[write] = v;', 'b->data[(write + 1) % b->cap] = v;')], source='kernel/runtime/kbuf.c')

    def test_length_alias_check_cannot_be_removed(self):
        self.run_failure("service_length_alias",
            [('rt_overlap(out, out_cap, out_len, sizeof *out_len)', '0')], source='kernel/runtime/krst.c')

    def test_null_zero_output_cannot_return_success(self):
        self.run_failure("service_null_all",
            [('if (!out) return KRST_BAD_ARGS;', 'if (!out) return KRST_OK;')], source='kernel/runtime/krst.c')

    def test_partial_worker_creation_rolls_back(self):
        self.run_failure('worker_create_rolled_back', [
            ('if (!thread_create(&ids[i], digester, (void *)(cpu_u64)i))',
             'if (i == 3 || !thread_create(&ids[i], digester, (void *)(cpu_u64)i))')])

    def test_skipped_worker_reap_is_rejected(self):
        self.run_failure('resource_balance', [
            ('require(thread_join(ids[i]), "worker_join");',
             'if (i != 3) require(thread_join(ids[i]), "worker_join");')])

    def test_services_bypassed_in_workers_are_rejected(self):
        helper = ('static enum krst_result bypass(enum krst_op op, const void *in, cpu_u64 n, '
                  'void *out, cpu_u64 cap, cpu_u64 *len) {\n'
                  '    (void)op; (void)cap; cpu_u64 d = krst_digest(in, n);\n'
                  '    for (unsigned int i = 0; i < 8; ++i) ((cpu_u8 *)out)[i] = (cpu_u8)(d >> (8*i));\n'
                  '    *len = 8; return KRST_OK;\n}\n')
        self.run_failure('service_preemption_missing', [
            ('static void digester(void *arg)', helper + 'static void digester(void *arg)'),
            ('krst_call(KRST_SVC_DIGEST, in, inlen', 'bypass(KRST_SVC_DIGEST, in, inlen'),
            ('krst_call(KRST_SVC_DIGEST, payload, sizeof payload', 'bypass(KRST_SVC_DIGEST, payload, sizeof payload'),
            ('attempt <= 131072', 'attempt <= 512')])

    def test_guard_mapping_leak_is_rejected(self):
        self.run_failure('resource_balance', [
            ('vm_unmap(vm_kernel_space(), va) == VM_OK && pmm_release(frame) == PMM_OK',
             'vm_unmap(vm_kernel_space(), va) == VM_OK && frame != 0')], source='kernel/runtime/boundary-test.c')

    def test_cpu_trace_cannot_be_omitted(self):
        root = self.mutate(self.build_fixture(), 'tools/host/qemu.py', [
            ('"-d", "guest_errors,int"', '"-d", "guest_errors"')])
        build_image(root)
        spec = importlib.util.spec_from_file_location('audit_no_irq_trace', root / 'tools/host/qemu.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        logs = root / 'build/boot-test'
        try:
            with self.assertRaisesRegex(RuntimeError, 'CPU IRQ trace does not corroborate worker'):
                module.boot_image(root / 'build/rynoros.img', logs, timeout=30)
        finally:
            self.cleanup(logs)
        self.assertIn(RUNTIME_END, (logs / 'serial.log').read_bytes())

    def test_cat_guard_overread_fault_is_detected(self):
        self.run_failure('fault_address=0x0000000040001000', [
            ('if (len == cap) return KSTR_TERMINATION;',
             'if (len == cap && ((volatile char *)dst)[len]) return KSTR_TERMINATION;')],
            source='kernel/runtime/kstring.c')

    def test_formatter_destination_bounds_cannot_be_removed(self):
        self.run_failure('format_overflow', [
            ('if (n > cap - 1 - used) return KSTR_OVERFLOW;',
             'if (0 && n > cap - 1 - used) return KSTR_OVERFLOW;'),
            ('if (need + 1 > cap) return KSTR_OVERFLOW;',
             'if (0 && need + 1 > cap) return KSTR_OVERFLOW;')], source='kernel/runtime/kstring.c')

    def test_buffer_capacity_corruption_is_detected(self):
        self.run_failure('ring_full', [
            ('b->data = storage; b->cap = cap;', 'b->data = storage; b->cap = cap + 1;')],
            source='kernel/runtime/kbuf.c')

    def test_formatter_alias_check_cannot_be_removed(self):
        self.run_failure('format_alias', [
            ('if (rt_overlap(dst, cap, fmt, fmt_len + 1)) return KSTR_INVALID;',
             'if (0 && rt_overlap(dst, cap, fmt, fmt_len + 1)) return KSTR_INVALID;')],
            source='kernel/runtime/kstring.c')

    def test_forged_serial_and_memory_need_real_cpu_execution(self):
        canned = RUNTIME_GOOD.replace(b'free_bytes=921600', b'free_bytes=65802240').decode('ascii')
        assignments = []
        for i, inp in enumerate(W_INPUT):
            probe = fnv1a(bytes((j + i) & 255 for j in range(4096)))
            assignments.append(
                f'runtime_evidence[{i}] = (struct worker_out){{{worker_acc(inp)}ULL, 40, {50+i}, '
                f'KSTACK_BASE+{i*5*4096}ULL, 2, (cpu_u64)__runtime_service_start, '
                f'KSTACK_BASE+{i*5*4096+8192}ULL, {probe}ULL, 2}};')
        self.run_failure('CPU IRQ trace does not corroborate worker', [
            ('void runtime_self_test(void)\n{\n', 'void runtime_self_test(void)\n{\n    ' +
             '\n    '.join(assignments) + '\n    text(' + json.dumps(canned) + '); return;\n')])
