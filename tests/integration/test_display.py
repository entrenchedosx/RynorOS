"""Actual framebuffer handoff, pixel evidence and scoped mutations."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/host"))
from image import build_image
from qemu import boot_image
from repository import REQUIRED_DIRECTORIES, REQUIRED_FILES
from display_output import DISPLAY_START, DISPLAY_END, parse_display_output
from boot_output import POST_IRQ


class DisplayTests(unittest.TestCase):
    def cleanup(self, logs):
        state = json.loads((logs / "run.json").read_text())
        self.assertTrue(state["reaped"])
        self.assertEqual((state["cleanup"], state["returncode"]), ("monitor-quit", 0))

    def section(self, output: bytes) -> bytes:
        return output[output.index(DISPLAY_START):output.index(DISPLAY_END) + len(DISPLAY_END)]

    def build_fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix="fb-fault-", dir=ROOT / "build")
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

    def run_failure(self, reason, pairs, source="kernel/drivers/display.c"):
        root = self.mutate(self.build_fixture(), source, pairs)
        build_image(root)
        logs = ROOT / "build/fb-tests" / self._testMethodName
        try:
            with self.assertRaises(RuntimeError) as error:
                boot_image(root / "build/rynoros.img", logs, timeout=12)
        finally:
            self.cleanup(logs)
        logged = (logs / "serial.log").read_bytes()
        self.assertIn(DISPLAY_START, logged, 'Mutation failed before the display stage')
        self.assertIn(reason, str(error.exception) + logged.decode("ascii"))

    def run_success(self, pairs, source="kernel/drivers/display.c"):
        root = self.mutate(self.build_fixture(), source, pairs)
        build_image(root)
        logs = ROOT / "build/fb-tests" / self._testMethodName
        try:
            boot_image(root / "build/rynoros.img", logs, timeout=30)
        finally:
            self.cleanup(logs)
        return (logs / "serial.log").read_bytes()

    def test_real_framebuffer_pattern_and_host_evidence(self):
        destination = ROOT / "build/fb-tests/normal with spaces"
        build_image(ROOT, destination)
        logs = destination / "logs"
        try:
            output = boot_image(destination / "rynoros.img", logs, timeout=30)
        finally:
            self.cleanup(logs)
        parsed = parse_display_output(self.section(output))
        self.assertEqual((parsed["width"], parsed["height"], parsed["pitch"], parsed["bpp"]),
                         (1024, 768, 4096, 32))
        self.assertEqual(parsed["tables"], 14)
        self.assertIn(POST_IRQ, output)
        dump = logs / "display.pmem"
        self.assertEqual(dump.stat().st_size, parsed["fb_bytes"])
        self.assertTrue((logs / 'display.ppm').is_file())

    def invalid_handoff(self, field_offset, value, reason, size='dword'):
        # Corrupt actual boot metadata, not the validator's predicate.
        self.run_failure('[FB] failure='+reason, [
            ('mov dword [__fb_info_start + 8], 1        ; publish complete handoff',
             'mov dword [__fb_info_start + 8], 1        ; publish complete handoff\n'
             f'    mov {size} [__fb_info_start + {field_offset}], {value}')], source='boot/transition.asm')

    def test_bpp_validation_cannot_pass(self):
        self.invalid_handoff(28,24,'bpp','word')

    def test_memory_model_validation_cannot_pass(self):
        self.invalid_handoff(32,5,'memory_model','byte')

    def test_pixel_mask_validation_cannot_pass(self):
        self.invalid_handoff(40,0xff00,'pixel_masks')

    def test_resolution_validation_cannot_pass(self):
        self.invalid_handoff(16,0,'resolution')

    def test_pitch_alignment_validation_cannot_pass(self):
        self.invalid_handoff(24,4097,'pitch_alignment')

    def test_lfb_alignment_validation_cannot_pass(self):
        self.invalid_handoff(36,4095,'lfb_alignment')

    def test_region_usable_frame_rejected(self):
        self.run_failure('[FB] failure=mmio_ram_rejected',
            [('if (pa < 0x100000 || state == PMM_STATE_FREE || state == PMM_STATE_ALLOCATED) return 0;',
              'if (state == PMM_STATE_ALLOCATED) return 1;\n    if (pa < 0x100000 || state == PMM_STATE_FREE) return 0;')],
            source='kernel/mm/vm.c')

    def test_mapping_outside_mmio_slot_cannot_pass(self):
        self.run_failure("[FB] failure=mapping",
                         [("vm_map_device(vm_kernel_space(), VM_MMIO_BASE,",
                           "vm_map_device(vm_kernel_space(), 0x500000,")])

    def test_wrong_border_color_detected_by_guest(self):
        """A paint bug in the border primitive must be caught by the guest's
        own read-back, not silently accepted by the host."""
        self.run_failure("[FB] failure=read_border_br",
                         [('require(display_fill_rect(0,h-16,w,16,0,0,255),"border_bottom");',
                           'require(display_fill_rect(0,h-16,w,16,0,255,0),"border_bottom");')],
                         source="kernel/drivers/display-test.c")

    def test_guest_pattern_mismatch_detected_by_guest(self):
        self.run_failure("[FB] failure=read_square",
                         [('require(display_fill_rect(w/2-32,h/2-32,64,64,255,0,0),"square");',
                           'require(display_fill_rect(w/2-32,h/2-32,64,64,0,255,0),"square");')],
                         source="kernel/drivers/display-test.c")

    def test_host_pattern_swap_caught_by_host_evidence(self):
        """Swap red and blue in the painted pattern consistently; the guest's
        own read-back still passes, but the host pmemsave evidence detects the
        swapped BGRX bytes independently."""
        self.run_failure("display pixel evidence failed",
                         [('require(display_fill_rect(0,0,w,16,0,0,255),"border_top");',
                           'require(display_fill_rect(0,0,w,16,0,255,0),"border_top");'),
                          ('r==0 && g==0 && b==255,"read_border_tl"',
                           'r==0 && g==255 && b==0,"read_border_tl"')],
                         source="kernel/drivers/display-test.c")

    def test_host_evidence_independent_of_guest_readback(self):
        """Guest read-back skipped but host pmemsave still verifies the real
        pattern, so the evidence does not depend on the guest's own checks."""
        output = self.run_success(
            [("verify_pattern0();", "if (0) verify_pattern0();")],
            source="kernel/drivers/display-test.c")
        self.assertIn(DISPLAY_END, output)
        self.assertIn(POST_IRQ, output)

    def test_canned_success_output_cannot_prove_hardware(self):
        from display_output import DISPLAY_GOOD
        # The real 64 MiB keyboard baseline free is 65818624; the display adds
        # 16384 bytes. Correct the canned accounting so the display section
        # alone passes boot validation.
        canned = (DISPLAY_GOOD.replace(b"free_bytes=921600", b"free_bytes=65802240")
                  .decode("ascii"))
        root = self.mutate(self.build_fixture(),
                           "kernel/drivers/display-test.c",
                           [("void display_self_test(void)\n{\n",
                             "void display_self_test(void)\n{\n    text(" + json.dumps(canned) + "); return;\n")])
        build_image(root)
        logs = ROOT / "build/fb-tests" / self._testMethodName
        try:
            with self.assertRaises(RuntimeError) as error:
                boot_image(root / "build/rynoros.img", logs, timeout=12)
        finally:
            self.cleanup(logs)
        logged = (logs / "serial.log").read_bytes()
        self.assertIn(DISPLAY_START, logged, 'Mutation failed before the display stage')
        combined = str(error.exception) + logged.decode("ascii")
        # Because display_self_test returns before allocating the framebuffer,
        # the real Stage 10 runtime reports the keyboard baseline, which cannot
        # match the forged display baseline: the forgery is also rejected by the
        # runtime accounting gate. Accept either host-side forgery-detection gate.
        self.assertTrue("display pixel evidence failed" in combined or
                        "Runtime accounting does not match display baseline" in combined,
                        combined)

    def test_hardware_pitch_padding(self):
        self.run_success([('bga_write 6, 1024','bga_write 6, 1040')], source='boot/transition.asm')

    def test_wrong_physical_device_base(self):
        self.invalid_handoff(36,'0xfc000000','device_state')

    def test_removed_metadata_validation(self):
        self.run_failure('[FB] failure=metadata_bpp',
            [('if (h->bpp != 32) return "bpp";', 'if (0 && h->bpp != 32) return "bpp";')])

    def test_removed_pixel_bounds(self):
        self.run_failure('[FB] failure=pixel_bounds',
            [('if (!display_surface_valid(s) || x >= s->width || y >= s->height) return 0;',
              'if (!display_surface_valid(s)) return 0;')], source='kernel/drivers/display-surface.c')

    def test_wrong_stride(self):
        self.run_failure('[FB] failure=surface_exact_extent',
            [('(cpu_u64)y * (s->pitch / 4) + x','(cpu_u64)y * s->width + x')],
            source='kernel/drivers/display-surface.c')

    def test_broken_rectangle_clip(self):
        self.run_failure('[FB] failure=surface_exact_extent',
            [('if (right > s->width) right = s->width;',
              'if (right > s->width) right = s->width + 1;')], source='kernel/drivers/display-surface.c')

    def test_noop_text_caught_independently(self):
        self.run_failure('display pixel evidence failed',
            [('{ return display_surface_text(&screen, x, y, s, color(r, g, b)); }',
              '{ if (screen.width && s && x<screen.width && y<screen.height && s[0]>=32) return 1; return display_surface_text(&screen, x, y, s, color(r, g, b)); }')])

    def test_noop_drawing_cannot_print_success(self):
        self.run_failure('display pixel evidence failed',
            [('    pattern0();','    if (0) pattern0();'),
             ('    verify_pattern0();','    if (0) verify_pattern0();')], source='kernel/drivers/display-test.c')

    def test_corrupt_glyph_index(self):
        self.run_failure('display pixel evidence failed',
            [("{'Z', {31,1,2,4,8,16,31}}", "{'Z', {31,16,8,4,2,1,31}}")],
            source='kernel/drivers/display-font.h')

    def test_bypassed_mapping(self):
        self.run_failure('[FB] failure=mapping_cleanup',
            [('if (vm_map_device(vm_kernel_space(), VM_MMIO_BASE, h->lfb_phys, pages, VM_WRITE) != VM_OK)',
              'if (0 && vm_map_device(vm_kernel_space(), VM_MMIO_BASE, h->lfb_phys, pages, VM_WRITE) != VM_OK)')])

    def test_missing_uncached_device_mapping(self):
        self.run_failure('[FB] failure=mmio_pte_state',
            [('return map_pages(s, va, pa, pages, p | VM_DEVICE_UC, 1);',
              'return map_pages(s, va, pa, pages, p, 1);')], source='kernel/mm/vm.c')

    def test_pat3_verification_is_consulted(self):
        """The full PAT3 byte (EAX bits 31:24) must be consulted before the
        first device mapping; QEMU supplies UC there, so requiring another
        value must fail closed before pixel work."""
        self.run_failure('[FB] failure=mmio_invalid_ranges',
            [('if ((a >> 24) != 0) return VM_UNSUPPORTED;',
              'if ((a >> 24) != 1) return VM_UNSUPPORTED;')],
            source='kernel/mm/vm.c')

    def test_ordinary_api_cannot_edit_mmio_slot(self):
        self.run_failure('[FB] failure=mmio_slot_exclusive',
            [('page_index(last, 3) >= 509','page_index(last, 3) >= 510')], source='kernel/mm/vm.c')

    def test_missing_device_nx(self):
        self.run_failure('[FB] failure=mmio_pte_state',
            [('return map_pages(s, va, pa, pages, p | VM_DEVICE_UC, 1);',
              'return map_pages(s, va, pa, pages, p | VM_EXECUTE | VM_DEVICE_UC, 1);')], source='kernel/mm/vm.c')

    def test_partial_mapping_rollback_leak(self):
        self.run_failure('[FB] failure=mmio_oom_rollback',
            [('    if (done != pages) {', '    if (device && done != pages) return r;\n    if (done != pages) {')],
            source='kernel/mm/vm.c')

    def test_removed_all_metadata_validation(self):
        self.run_failure('[FB] failure=metadata_magic',
            [('const char *display_validate_info(const struct boot_fb_info *h)\n{',
              'const char *display_validate_info(const struct boot_fb_info *h)\n{\n    return 0;')])

    def test_skipped_display_initialization(self):
        self.run_failure('[FB] failure=double_init',
            [('    if (!display_initialize()) require(0, display_error());',
              '    if (0 && !display_initialize()) require(0, display_error());')], source='kernel/drivers/display-test.c')
