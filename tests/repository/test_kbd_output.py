"""Synthetic parser/monitor fixtures, not hardware execution evidence."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tools/host"))
from kbd_output import (KBD_START,KBD_END,KBD_GOOD,KEYS,fixture,expected_events,
                        parse_kbd_output,validate_kbd_output,validate_keyboard_trace,key_sequence)
from qemu import _inject_pending_keys

class KbdOutputTests(unittest.TestCase):
    def test_valid_parser_fixture(self):
        self.assertEqual(validate_kbd_output(KBD_GOOD),[])
        self.assertEqual(parse_kbd_output(KBD_GOOD)["received"],16)
    def test_every_line_required(self):
        for line in KBD_GOOD.splitlines(keepends=True):
            self.assertTrue(validate_kbd_output(KBD_GOOD.replace(line,b"",1)))
    def test_counters_and_accounting(self):
        for old,new in ((b"reads=16",b"reads=15"),(b"received=16",b"received=9"),
                        (b"dropped=0",b"dropped=1"),(b"irqs=17",b"irqs=16"),
                        (b"worker_runs=5000",b"worker_runs=0"),(b"errors=0",b"errors=1")):
            self.assertTrue(validate_kbd_output(KBD_GOOD.replace(old,new)))
        previous=dict(allocated=106496,free=942080,tables=10)
        self.assertTrue(validate_kbd_output(KBD_GOOD,previous=previous))
    def test_missing_completion_and_start(self):
        for token in (KBD_START,KBD_END):
            self.assertTrue(validate_kbd_output(KBD_GOOD.replace(token,b"")))
    def test_press_release_raw_and_key_agree(self):
        for old,new in ((b"scan=30",b"scan=31"),(b"type=1",b"type=2"),
                        (b"key=30",b"key=0"),(b"event=1 ",b"event=0 ")):
            self.assertTrue(validate_kbd_output(KBD_GOOD.replace(old,new)))
    def test_unbounded_duplicate_and_bad_encoding(self):
        for output in (KBD_GOOD*2,KBD_GOOD+b"\xff",KBD_GOOD*100,b"\x00"+KBD_END):
            self.assertTrue(validate_kbd_output(output))
    def test_host_challenge_and_unknown_key(self):
        keys=("d","x","shift","shift_r","ret","a","a","b")
        self.assertEqual(validate_kbd_output(fixture(keys),keys),[])
        self.assertTrue(validate_kbd_output(fixture(keys),KEYS))
    def test_monitor_input_rejects_commands(self):
        for keys in (("a\nquit",)*8,("a",)*7,"a",("invalid",)*8):
            with self.assertRaises(ValueError): key_sequence(keys)
    def test_missing_monitor_does_not_spin(self):
        class Process: stdin=None
        with self.assertRaises(RuntimeError):
            _inject_pending_keys(Process(),b"[KBD] waiting for input=0\r\n",KEYS,[0])
    def test_trace_requires_device_irq_and_port_read_order(self):
        records=[]
        for i,(scan,_,_) in enumerate(expected_events(KEYS)):
            records.extend((f"ps2_keyboard_event addr lnx 1 down {1-i%2} modifier 0x0 modifiers 0x0 set 2 xlate 1",
                            "pic_interrupt irq 1 intno 33",f"pckbd_kbd_read_data 0x{scan:02x}"))
        trace="\n".join(records)
        validate_keyboard_trace(trace)
        # Firmware/controller can queue release before the make is consumed.
        queued=records.copy()
        queued[:6]=[records[0],records[3],records[1],records[2],records[4],records[5]]
        validate_keyboard_trace('\n'.join(queued))
        premature=records.copy(); premature[1],premature[2]=premature[2],premature[1]
        with self.assertRaises(ValueError): validate_keyboard_trace('\n'.join(premature))
        for bad in (trace.replace("pic_interrupt irq 1 intno 33",""),
                    trace.replace("pckbd_kbd_read_data 0x1e","pckbd_kbd_read_data 0x00"),
                    trace.replace("set 2 xlate 1","set 2 xlate 0"),""):
            with self.assertRaises(ValueError): validate_keyboard_trace(bad)
