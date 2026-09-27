"""PCI-A1 header pins: config offsets, masks, limits, enum order.

The PCI layer is an internal kernel API (no CPL3 surface): these pins
freeze the config-space map and the registry/mapping contract that
pci.c, pci-test.c, and the host validator share.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "kernel/include/pci.h"


def _text():
    return HEADER.read_text(encoding="utf-8")


def _define(name):
    match = re.search(r"#define %s (0x[0-9A-Fa-f]+|\d+)u?(?:ULL)?\b" % name,
                      _text())
    assert match, name
    return int(match.group(1), 0)


class PciHeaderTests(unittest.TestCase):
    def test_config_offsets(self):
        want = {"PCI_CFG_VENDOR_ID": 0x00, "PCI_CFG_DEVICE_ID": 0x02,
                "PCI_CFG_COMMAND": 0x04, "PCI_CFG_STATUS": 0x06,
                "PCI_CFG_REVISION": 0x08, "PCI_CFG_PROG_IF": 0x09,
                "PCI_CFG_SUBCLASS": 0x0A, "PCI_CFG_CLASS": 0x0B,
                "PCI_CFG_HEADER_TYPE": 0x0E, "PCI_CFG_BAR0": 0x10,
                "PCI_CFG_ROM_BAR": 0x30, "PCI_CFG_BRIDGE_PRIMARY": 0x18,
                "PCI_CFG_BRIDGE_SECONDARY": 0x19,
                "PCI_CFG_BRIDGE_SUBORDINATE": 0x1A,
                "PCI_CFG_IRQ_LINE": 0x3C, "PCI_CFG_IRQ_PIN": 0x3D,
                "PCI_CFG_SPACE_BYTES": 256}
        for name, value in want.items():
            with self.subTest(name=name):
                self.assertEqual(_define(name), value)

    def test_absent_and_command_bits(self):
        self.assertEqual(_define("PCI_VENDOR_ABSENT"), 0xFFFF)
        self.assertEqual(_define("PCI_COMMAND_IO"), 0x0001)
        self.assertEqual(_define("PCI_COMMAND_MEM"), 0x0002)
        self.assertEqual(_define("PCI_COMMAND_BUS_MASTER"), 0x0004)

    def test_bounds(self):
        want = {"PCI_MAX_BUS": 255, "PCI_MAX_DEVICE": 31,
                "PCI_MAX_FUNCTION": 7, "PCI_MAX_DEVICES": 16,
                "PCI_MAX_BAR_SLOTS": 6, "PCI_MAX_BAR_ENTRIES": 4,
                "PCI_MAX_BRIDGE_BARS": 2,
                "PCI_MAX_BUSES": 32, "PCI_MATCH_ANY": 0xFFFFFFFF}
        for name, value in want.items():
            with self.subTest(name=name):
                self.assertEqual(_define(name), value)

    def test_result_order(self):
        body = _text()
        names = ["PCI_OK", "PCI_INVALID", "PCI_ABSENT", "PCI_FULL",
                 "PCI_UNSUPPORTED", "PCI_RANGE", "PCI_VM_ERROR"]
        positions = [body.index("    %s" % name) for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("PCI_OK = 0", body)

    def test_bar_kind_order(self):
        body = _text()
        for fragment in ("PCI_BAR_NONE = 0", "PCI_BAR_IO = 1",
                         "PCI_BAR_MMIO32 = 2", "PCI_BAR_MMIO64 = 3"):
            self.assertIn(fragment, body)

    def test_mmio_va_carveup(self):
        self.assertIn("#define PCI_MMIO_VA_BASE (VM_MMIO_BASE + 0x10000000ULL)",
                      _text())

    def test_transport_surface(self):
        body = _text()
        for decl in ("pci_cfg_read8", "pci_cfg_read16", "pci_cfg_read32",
                     "pci_cfg_write8", "pci_cfg_write16", "pci_cfg_write32",
                     "pci_cfg_install_ops"):
            self.assertIn(decl, body)

    def test_registry_and_map_surface(self):
        body = _text()
        for decl in ("pci_initialize", "pci_initialized",
                     "pci_device_count", "pci_device_at", "pci_find_bdf",
                     "pci_find_vendor_device", "pci_find_class",
                     "pci_map_bar", "pci_unmap_bar", "pci_stats_read",
                     "pci_class_name", "pci_self_test"):
            self.assertIn(decl, body)

    def test_no_userspace_surface(self):
        body = _text()
        for token in ("syscall", "SYS_", "rt_", "USER_", "cpl3_syscall"):
            self.assertNotIn(token, body)

    def test_device_struct_fields(self):
        body = _text()
        for field in ("vendor_id", "device_id", "class_code", "subclass",
                      "prog_if", "revision", "header_type", "multifunction",
                      "command", "status", "bar_count", "sec_bus",
                      "sub_bus"):
            self.assertIn(field, body)

    def test_bar_struct_fields(self):
        body = _text()
        for field in ("kind", "prefetchable", "is64", "raw_low",
                      "raw_high", "base", "size", "mapped_va"):
            self.assertIn(field, body)
