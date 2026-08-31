"""Synthetic parser fixtures and pixel evidence checks, not emulator data."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/host"))
from boot_output import POST_IRQ
from display_output import (DISPLAY_START, DISPLAY_END, DISPLAY_GOOD, DISPLAY_PIXELS,
                            STEPS, parse_display_output, validate_display_output,
                            verify_display_pixels, VM_MMIO_BASE)
from display_output import reference_framebuffer, verify_display_scanout


def pixel_dump(display=None):
    """Explicit synthetic full-screen reference, not emulator evidence."""
    if display is None:
        display = parse_display_output(DISPLAY_GOOD)
    return reference_framebuffer(display)


class FbOutputTests(unittest.TestCase):
    def test_valid_fixture(self):
        self.assertEqual(validate_display_output(DISPLAY_GOOD), [])
        parsed = parse_display_output(DISPLAY_GOOD)
        self.assertEqual((parsed["width"], parsed["height"], parsed["pitch"], parsed["bpp"]),
                         (1024, 768, 4096, 32))
        self.assertEqual(parsed["tables"], 14)
        self.assertEqual(parsed["lfb"] % 4096, 0)
        self.assertEqual(parsed["lfb_end"], parsed["lfb"] + parsed["fb_bytes"])

    def test_accounting_against_keyboard_baseline(self):
        previous = dict(allocated=106496, free=937984, tables=10)
        # 4 table pages exactly matches the 768-page display mapping.
        self.assertEqual(validate_display_output(DISPLAY_GOOD, previous), [])
        for old, new in ((b"final allocated_bytes=122880", b"final allocated_bytes=122881"),
                         (b"free_bytes=921600", b"free_bytes=922000"),
                         (b"table_pages=14", b"table_pages=15")):
            with self.subTest(old=old, new=new):
                self.assertTrue(validate_display_output(DISPLAY_GOOD.replace(old, new), previous))

    def test_every_line_required(self):
        for line in DISPLAY_GOOD.splitlines(keepends=True):
            with self.subTest(line=line):
                self.assertTrue(validate_display_output(DISPLAY_GOOD.replace(line, b"", 1)))

    def test_steps_and_handoff_header(self):
        for old, new in ((b"magic=1145586246", b"magic=1"),
                         (b"version=2", b"version=1"),
                         (b"status=1", b"status=2"),
                         (b"memory_model=6", b"memory_model=5"),
                         (b"width=1024", b"width=1000"),
                         (b"height=768", b"height=760"),
                         (b"pitch=4096", b"pitch=4092"),
                         (b"bpp=32", b"bpp=24"),
                         (b"red=16711680", b"red=65280"),
                         (b"green=65280", b"green=255"),
                         (b"blue=255", b"blue=0"),
                         (b"mapped va=18446742424442109952", b"mapped va=0")):
            with self.subTest(old=old, new=new):
                self.assertTrue(validate_display_output(DISPLAY_GOOD.replace(old, new)))

    def test_framebuffer_window_and_lfb_alignment(self):
        for old, new in ((b"lfb=4244635648", b"lfb=4244635649"),
                         (b"lfb=4244635648", b"lfb=0"),
                         (b"fb_bytes=3145728", b"fb_bytes=33554432"),
                         (b"pages=768", b"pages=767")):
            with self.subTest(old=old, new=new):
                self.assertTrue(validate_display_output(DISPLAY_GOOD.replace(old, new)))

    def test_unbounded_duplicate_and_bad_encoding(self):
        for output in (DISPLAY_GOOD * 2, DISPLAY_GOOD + b"\xff", DISPLAY_GOOD * 100,
                       b"\x00" + DISPLAY_END):
            with self.subTest(output=type(output).__name__):
                self.assertTrue(validate_display_output(output))
            self.assertTrue(validate_display_output(output))

    def test_pixel_dump_verification(self):
        good = pixel_dump()
        verify_display_pixels(good, parse_display_output(DISPLAY_GOOD))
        for x, y in (*DISPLAY_PIXELS, (41,32), (43,65), (1023,767), (701,543)):
            swap_offset = y * 4096 + x * 4
            breaker = bytearray(good)
            breaker[swap_offset] = (breaker[swap_offset] + 1) & 0xff
            with self.subTest(pixel=str(x) + "," + str(y)):
                with self.assertRaises(ValueError):
                    verify_display_pixels(bytes(breaker), parse_display_output(DISPLAY_GOOD))

    def test_pixel_dump_short(self):
        with self.assertRaises(ValueError):
            verify_display_pixels(b"\x00" * 100, parse_display_output(DISPLAY_GOOD))

    def test_complete_boot_ordering(self):
        from kbd_output import KBD_GOOD
        from boot_output import validate_boot_output
        from test_exception_output import parser_fixture
        from test_pmm_output import fixture as pmm_fixture
        from test_vm_output import fixture as vm_fixture
        from test_heap_output import fixture as heap_fixture
        from timer_output import TIMER_OUTPUT
        from sched_output import SCHED_GOOD
        before=parser_fixture()+pmm_fixture()+vm_fixture()+heap_fixture()+TIMER_OUTPUT+SCHED_GOOD
        compose=before+KBD_GOOD+DISPLAY_GOOD+POST_IRQ
        self.assertEqual(validate_boot_output(compose),[])
        for broken in (before+DISPLAY_GOOD+KBD_GOOD+POST_IRQ, compose.replace(DISPLAY_GOOD,b''),
                       compose.replace(DISPLAY_GOOD,DISPLAY_GOOD*2), compose+DISPLAY_GOOD):
            self.assertTrue(validate_boot_output(broken))

    def test_full_image_rejects_absent_text_and_extra_bytes(self):
        info=parse_display_output(DISPLAY_GOOD)
        data=bytearray(pixel_dump(info))
        for y in range(32,96): data[y*4096+40*4:y*4096+400*4]=bytes(360*4)
        with self.assertRaises(ValueError): verify_display_pixels(data,info)
        with self.assertRaises(ValueError): verify_display_pixels(pixel_dump(info)+b'\0',info)

    def test_pitch_padding_is_checked(self):
        info=parse_display_output(DISPLAY_GOOD); info['pitch']=4160
        data=bytearray(pixel_dump(info)); verify_display_pixels(data,info)
        data[4096]=1
        with self.assertRaises(ValueError): verify_display_pixels(data,info)

    def test_scanout_geometry_colors_text_and_length(self):
        info=parse_display_output(DISPLAY_GOOD); raw=pixel_dump(info)
        rgb=bytearray(len(raw)//4*3)
        rgb[0::3]=raw[2::4]; rgb[1::3]=raw[1::4]; rgb[2::3]=raw[0::4]
        ppm=b'P6\n1024 768\n255\n'+rgb
        verify_display_scanout(ppm,info)
        for bad in (ppm+b'\0',ppm.replace(b'1024',b'1023',1),ppm[:-1],ppm[:17]+bytes(len(rgb))):
            with self.assertRaises(ValueError): verify_display_scanout(bad,info)
