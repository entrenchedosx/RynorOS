"""Actual device/IRQ/I/O trace checks plus scoped temporary implementation mutations."""
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES,REQUIRED_FILES
from kbd_output import KBD_START,KBD_END,KEYS,parse_kbd_output,validate_keyboard_trace
from boot_output import POST_IRQ

class KeyboardTests(unittest.TestCase):
    def cleanup(self,logs):
        state=json.loads((logs/"run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual((state["cleanup"],state["returncode"]),("monitor-quit",0))
    def test_real_keyboard_host_challenges_and_repeat(self):
        destination=ROOT/"build/kbd-tests/normal"
        build_image(ROOT,destination)
        shuffled=list(KEYS); random.SystemRandom().shuffle(shuffled)
        for i,keys in enumerate((KEYS,tuple(shuffled),("d","x","shift","shift_r","a","a","ret","b"))):
            logs=destination/f"logs-{i}"
            try: output=boot_image(destination/"rynoros.img",logs,keys=keys)
            finally: self.cleanup(logs)
            section=output[output.index(KBD_START):output.index(KBD_END)+len(KBD_END)]
            parsed=parse_kbd_output(section,keys)
            self.assertEqual((parsed["received"],parsed["dropped"]),(16,0))
            self.assertGreater(parsed["ticks"],0)
            self.assertTrue(output.endswith(POST_IRQ))
            validate_keyboard_trace((logs/"guest-errors.log").read_text(),keys)
    def variant(self,name,source,old,new,reason,*,guest_halt=True,keys=KEYS,success=False):
        with tempfile.TemporaryDirectory(prefix="kbd-fault-",dir=ROOT/"build") as tmp:
            root=Path(tmp)
            for d in REQUIRED_DIRECTORIES: (root/d).mkdir(parents=True,exist_ok=True)
            for f in REQUIRED_FILES: shutil.copyfile(ROOT/f,root/f)
            path=root/source
            contents=path.read_text()
            self.assertEqual(contents.count(old),1)
            path.write_text(contents.replace(old,new))
            build_image(root)
            logs=ROOT/"build/kbd-tests"/name
            try:
                if success: boot_image(root/"build/rynoros.img",logs,keys=keys)
                else:
                    with self.assertRaises(RuntimeError) as error:
                        # Whole boot includes prior PMM/VM/scheduler tests.
                        # Still require the intended keyboard failure below;
                        # an earlier timeout is never accepted as evidence.
                        boot_image(root/"build/rynoros.img",logs,timeout=12,keys=keys)
            finally: self.cleanup(logs)
            output=(logs/"serial.log").read_bytes()
            self.assertIn(KBD_START,output)
            if not success:
                reasons = reason if isinstance(reason, tuple) else (reason,)
                diagnostic = str(error.exception)+output.decode("ascii")
                self.assertTrue(any(r in diagnostic for r in reasons), diagnostic)
                if guest_halt:
                    self.assertNotIn(KBD_END,output); self.assertNotIn(POST_IRQ,output)
    def test_masked_irq1_cannot_receive_any_key(self):
        self.variant("masked-irq1","kernel/drivers/keyboard.c",
                     "if (!irq_set_enabled(1, 1)) goto fail;","if (!irq_set_enabled(1, 0)) goto fail;",
                     "[KBD] waiting for input=0")
    def test_isr_discard_cannot_queue_keys(self):
        self.variant("discard","kernel/drivers/keyboard.c",
                     "if (kbd_ring_put(&input, scan) < 0) cpu_halt();","(void)scan;",
                     "[KBD] waiting for input=0")
    def test_no_port_read_cannot_pass(self):
        # With overrun classification, the never-read 0x00 byte is rejected as a
        # controller error instead of being queued, so the loss is also caught.
        self.variant("no-read","kernel/drivers/keyboard.c",
                     "cpu_u8 scan = io_in8(KBD_DATA);","cpu_u8 scan = 0;",
                     ("[KBD] event=0 scan=0", "[KBD] failure=input_loss"))
    def test_live_read_counter_not_assertion_inversion(self):
        self.variant("counter","kernel/drivers/keyboard.c",
                     "increment(&stats.reads);","/* omitted real read accounting */",
                     "[KBD] failure=hardware_counts")
    def test_runtime_press_release_swap_is_detected(self):
        self.variant("swap","kernel/drivers/keyboard.c",
                     "result = kbd_stream_next(&input, &decoder, &consumed_epoch, out);",
                     "result = kbd_stream_next(&input, &decoder, &consumed_epoch, out); if (result == KBD_EVENT && out->type) out->type = out->type == KBD_EVENT_PRESS ? KBD_EVENT_RELEASE : KBD_EVENT_PRESS;",
                     "KBD event does not match host input",guest_halt=False)
    def test_runtime_decoder_bypass_is_detected(self):
        self.variant("decoder-bypass","kernel/drivers/keyboard.c",
                     "result = kbd_stream_next(&input, &decoder, &consumed_epoch, out);",
                     "result = kbd_stream_next(&input, &decoder, &consumed_epoch, out); if (result == KBD_EVENT) {out->type=KBD_EVENT_UNKNOWN; out->key=0;}",
                     "KBD event does not match host input",guest_halt=False)
    def test_runtime_queue_bypass_is_detected(self):
        self.variant("queue-bypass","kernel/drivers/keyboard.c",
                     "result = kbd_stream_next(&input, &decoder, &consumed_epoch, out);",
                     "if (input.head != input.tail) {result = KBD_EVENT; *out=(struct kbd_event){30,30,KBD_EVENT_PRESS};} else result = kbd_stream_next(&input, &decoder, &consumed_epoch, out);",
                     "[KBD] failure=hardware_counts")
    def test_loss_boundary_is_not_silent(self):
        self.variant("lost-boundary","kernel/drivers/keyboard.c",
                     "if (epoch != *seen) {","if (0 && epoch != *seen) {",
                     "[KBD] failure=loss_boundary")
    def test_ring_retained_content_is_checked(self):
        self.variant("ring-content","kernel/drivers/keyboard.c",
                     "(struct kbd_sample){q->epoch, scan}","(struct kbd_sample){q->epoch, (cpu_u8)(scan ^ 1)}",
                     "[KBD] failure=ring_fifo")
    def test_drop_newest_not_overwrite_oldest(self):
        self.variant("overwrite","kernel/drivers/keyboard.c",
                     "++q->dropped; ++q->epoch; return 0;",
                     "q->data[q->tail].scan = scan; ++q->dropped; ++q->epoch; return 0;",
                     "[KBD] failure=ring_fifo")
    def test_wraparound_is_checked(self):
        self.variant("wrap","kernel/drivers/keyboard.c",
                     "q->tail = (q->tail + 1u) & KBD_MASK;","q->tail = (q->tail + 2u) & KBD_MASK;",
                     "[KBD] failure=ring_empty")
    def test_capacity_is_exact(self):
        self.variant("capacity","kernel/drivers/keyboard.c",
                     "if (next == q->tail)","if (((next + 1u) & KBD_MASK) == q->tail)",
                     "[KBD] failure=ring_fill")
    def test_extended_key_tail_not_ordinary_enter(self):
        self.variant("prefix","kernel/drivers/keyboard.c",
                     "if (d->extended) { d->extended = 0; return 0; }","d->extended = 0;",
                     "[KBD] failure=decode_prefix")
    def test_status_error_not_input(self):
        self.variant("status-error","kernel/drivers/keyboard.c",
                     "cpu_u8 status = io_in8(KBD_CMD);\n    if (!(status",
                     "cpu_u8 status = io_in8(KBD_CMD) | STATUS_ERROR;\n    if (!(status",
                     "[KBD] failure=input_loss")
    def test_auxiliary_byte_not_keyboard(self):
        self.variant("aux","kernel/drivers/keyboard.c",
                     "cpu_u8 status = io_in8(KBD_CMD);\n    if (!(status",
                     "cpu_u8 status = io_in8(KBD_CMD) | STATUS_AUX;\n    if (!(status",
                     "[KBD] waiting for input=0")
    def test_obf_required_before_data_read(self):
        self.variant("obf","kernel/drivers/keyboard.c",
                     "cpu_u8 status = io_in8(KBD_CMD);\n    if (!(status",
                     "cpu_u8 status = io_in8(KBD_CMD) & ~STATUS_OBF;\n    if (!(status",
                     "[KBD] waiting for input=0")
    def test_empty_irq_accounting_cannot_be_removed(self):
        """The startup empty IRQ must still be counted: the only irqs increment
        site is shared with empty-IRQ accounting, so removing the branch leaves
        irqs == 0 and the guest's irqs == reads + empty invariant halts."""
        self.variant("empty-irq","kernel/drivers/keyboard.c",
                     "increment(&stats.irqs);\n    cpu_u8 status = io_in8(KBD_CMD);\n    if (!(status & STATUS_OBF)) { increment(&stats.empty_irqs); return; }",
                     "cpu_u8 status = io_in8(KBD_CMD);\n    if (!(status & STATUS_OBF)) { return; }",
                     "[KBD] failure=hardware_counts")
    def test_bad_controller_reply_masks_and_latches_failure(self):
        self.variant("controller-fail","kernel/drivers/keyboard.c",
                     "command(0xaa) || !read_reply(&reply) || reply != 0x55",
                     "command(0xab) || !read_reply(&reply) || reply != 0x55",
                     "[KBD] initialization failed phase=controller_test")
    def test_bad_keyboard_ack_masks_and_latches_failure(self):
        self.variant("ack-fail","kernel/drivers/keyboard.c",
                     "!keyboard_command(0xf5)","!keyboard_command(0xee)",
                     "[KBD] initialization failed phase=keyboard_commands")
    def test_resend_retry_is_bounded(self):
        self.variant("resend-fail","kernel/drivers/keyboard.c",
                     "!keyboard_command(0xf5)","!keyboard_command(0x05)",
                     "[KBD] initialization failed phase=keyboard_commands")
        trace=(ROOT/"build/kbd-tests/resend-fail/guest-errors.log").read_text()
        self.assertEqual(trace.count("pckbd_kbd_read_data 0xfe"),3)
    def test_bad_interface_reply_is_rejected(self):
        self.variant("interface-fail","kernel/drivers/keyboard.c",
                     "command(0xab) || !read_reply(&reply) || reply != 0)",
                     "command(0xaa) || !read_reply(&reply) || reply != 0)",
                     "[KBD] initialization failed phase=interface_test")
    def test_firmware_translation_off_is_not_inherited(self):
        self.variant("cold-translation","kernel/drivers/keyboard.c",
                     'init_error = "quiesce";',
                     'init_error = "quiesce"; if (!command(0x60) || !data(0x34)) goto fail;',
                     "",success=True)
    def test_fixed_synthetic_bytes_cannot_replace_input(self):
        self.variant("synthetic-replay","kernel/drivers/keyboard.c",
                     "if (kbd_ring_put(&input, scan) < 0) cpu_halt();",
                     "static unsigned int n; static const cpu_u8 fake[]={30,158,48,176,46,174,32,160,57,185,28,156,30,158,32,160}; (void)scan; if (kbd_ring_put(&input, fake[n++ % 16]) < 0) cpu_halt();",
                     "KBD event does not match host input",guest_halt=False,
                     keys=("d","b","c","a","spc","ret","d","a"))
    def test_no_host_input_cannot_pass(self):
        destination=ROOT/"build/kbd-tests/no-host"
        build_image(ROOT,destination)
        logs=destination/"logs"
        try:
            with self.assertRaisesRegex(RuntimeError,"timed out"):
                boot_image(destination/"rynoros.img",logs,timeout=3,inject_keys=False)
        finally: self.cleanup(logs)
        self.assertNotIn(KBD_END,(logs/"serial.log").read_bytes())
    def test_canned_success_output_cannot_prove_hardware(self):
        from kbd_output import KBD_GOOD
        canned=KBD_GOOD.replace(b"free_bytes=937984",b"free_bytes=65818624").decode()
        self.variant("canned-output","kernel/drivers/keyboard-test.c",
                     "void keyboard_self_test(void)\n{",
                     "void keyboard_self_test(void)\n{\n    text("+json.dumps(canned)+"); return;",
                     ("Keyboard completed without all host inputs",
                      "QEMU data-port reads do not match injected input"),guest_halt=False)
        logs = ROOT / 'build/kbd-tests/canned-output'
        self.assertIn(POST_IRQ, (logs / 'serial.log').read_bytes())
        # Later runtime work can outlast all eight injections. Never depend on
        # the race to finish first: the real I/O trace must independently reject
        # this forgery, regardless of which host gate notices it first.
        with self.assertRaisesRegex(ValueError, 'QEMU (keyboard trace missing input events|data-port reads do not match injected input)'):
            validate_keyboard_trace((logs / 'guest-errors.log').read_text())
