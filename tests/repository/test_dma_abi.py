"""DMA-A1 header pins: arena geometry, limits, result order, API surface.

The DMA layer is an internal kernel API (no CPL3 surface): these pins
freeze the virt/phys/bus descriptor contract and the contiguous-PMM
extension that dma.c, dma-test.c, and the host validator share.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "kernel/include/dma.h"
PMM_HEADER = ROOT / "kernel/include/pmm.h"


def _text():
    return HEADER.read_text(encoding="utf-8")


def _define(name):
    match = re.search(r"#define %s (0x[0-9A-Fa-f]+|\d+)u?(?:ULL)?\b" % name,
                      _text())
    assert match, name
    return int(match.group(1), 0)


class DmaHeaderTests(unittest.TestCase):
    def test_defines(self):
        want = {"DMA_PAGE_SIZE": 4096, "DMA_VA_MAX_PAGES": 262144,
                "DMA_MAX_BUFFERS": 16,
                "DMA_ADDR_ANY": 0xFFFFFFFFFFFFFFFF,
                "DMA_ADDR_32BIT": 0xFFFFFFFF,
                "DMA_MAGIC_LIVE": 0x444D4101,
                "DMA_MAGIC_DEAD": 0x444D4100}
        for name, value in want.items():
            with self.subTest(name=name):
                self.assertEqual(_define(name), value)

    def test_va_base(self):
        self.assertIn("#define DMA_VA_BASE 0xFFFFC08000000000ULL", _text())

    def test_arena_geometry_asserts(self):
        body = (ROOT / "kernel/mm/dma.c").read_text(encoding="utf-8")
        for fragment in ("DMA arena PML4 slot 385",
                         "DMA arena stays inside slot 385",
                         "DMA arena canonical high",
                         "DMA arena page-aligned"):
            self.assertIn(fragment, body)

    def test_result_order(self):
        body = _text()
        names = ["DMA_OK", "DMA_NOT_READY", "DMA_INVALID",
                 "DMA_ALIGNMENT", "DMA_OVERFLOW", "DMA_OOM",
                 "DMA_VM_ERROR", "DMA_BUSY", "DMA_CONTEXT",
                 "DMA_NOT_ALLOCATED"]
        positions = [body.index("    %s" % name) for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("DMA_OK = 0", body)

    def test_buffer_struct_fields(self):
        body = _text()
        for field in ("virt", "phys", "bus", "size", "alloc_size",
                      "align", "magic", "slot"):
            self.assertIn(field, body)

    def test_api_surface(self):
        body = _text()
        for decl in ("dma_initialize", "dma_alloc", "dma_free",
                     "dma_check", "dma_debug_va_limit", "dma_self_test"):
            self.assertIn(decl, body)

    def test_sync_barrier_surface(self):
        body = _text()
        for decl in ("dma_phys_to_bus", "dma_sync_for_device",
                     "dma_sync_for_cpu", "dma_rmb", "dma_wmb"):
            self.assertIn(decl, body)

    def test_no_userspace_surface(self):
        body = _text()
        for token in ("syscall", "SYS_", "rt_", "USER_", "cpl3_syscall"):
            self.assertNotIn(token, body)

    def test_pmm_contiguous_surface(self):
        body = PMM_HEADER.read_text(encoding="utf-8")
        self.assertIn("pmm_alloc_contiguous", body)
        self.assertIn("pmm_scan_run", body)
