#ifndef RYNOR_DMA_H
#define RYNOR_DMA_H
#include "cpu.h"

#define DMA_PAGE_SIZE 4096ULL
/* Dedicated DMA virtual arena: PML4 slot 385, bump-allocated, 1 GiB.
   RAM-backed DMA mappings live here; heap (slot 384) and PCI MMIO
   (slot 509, uncached device memory) are different categories. */
#define DMA_VA_BASE 0xFFFFC08000000000ULL
#define DMA_VA_MAX_PAGES 262144ULL
#define DMA_MAX_BUFFERS 16u
/* Inclusive last-byte device address. ANY = unconstrained; 32BIT keeps
   bus + alloc_size - 1 <= 0xFFFFFFFF for address-limited devices. */
#define DMA_ADDR_ANY 0xFFFFFFFFFFFFFFFFULL
#define DMA_ADDR_32BIT 0xFFFFFFFFULL
#define DMA_MAGIC_LIVE 0x444D4101u
#define DMA_MAGIC_DEAD 0x444D4100u

enum dma_result {
    DMA_OK = 0,
    DMA_NOT_READY,
    DMA_INVALID,
    DMA_ALIGNMENT,
    DMA_OVERFLOW,
    DMA_OOM,
    DMA_VM_ERROR,
    DMA_BUSY,
    DMA_CONTEXT,
    DMA_NOT_ALLOCATED
};
/* virt: kernel mapping (DMA arena). phys: first frame's physical
   address. bus: device-visible address from dma_phys_to_bus() (DMA-A1:
   bus == phys; never derive it by hand). size: requested bytes.
   alloc_size: whole mapped pages. align: granted alignment. magic and
   slot are DMA-owned: dma_free validates then poisons the descriptor. */
struct dma_buffer {
    void *virt;
    cpu_u64 phys, bus, size, alloc_size, align;
    cpu_u32 magic, slot;
};

/* DMA-A1 freezes bus == phys (no IOMMU, no translation). The ONLY
   replacement point for future IOMMU/IOVA support; drivers must call
   this (via dma_alloc's bus field) instead of assuming bus = phys. */
static inline cpu_u64 dma_phys_to_bus(cpu_u64 phys) { return phys; }
/* Coherent-x86 sync boundaries: documented no-ops. Non-coherent
   platforms are unsupported; see docs/design/dma.md. */
static inline void dma_sync_for_device(const struct dma_buffer *buf) { (void)buf; }
static inline void dma_sync_for_cpu(const struct dma_buffer *buf) { (void)buf; }
/* Descriptor-ring ordering for the frozen TSO model: x86 forbids
   store-store and load-load reordering and drains the store buffer in
   FIFO order, so a compiler barrier is the complete implementation.
   Non-TSO ports revisit these two functions; call sites are stable. */
static inline void dma_rmb(void) { __asm__ volatile ("" : : : "memory"); }
static inline void dma_wmb(void) { __asm__ volatile ("" : : : "memory"); }

/* Single CPU, IF=0, foreground; no IRQ handler may call. */
enum dma_result dma_initialize(void);
enum dma_result dma_alloc(cpu_u64 size, cpu_u64 align, cpu_u64 max_bus,
                         struct dma_buffer *out);
enum dma_result dma_free(struct dma_buffer *buf);
int dma_check(void);
/* Rollback-test hook: cap the VA arena at pages (clamped to
   DMA_VA_MAX_PAGES; 0 restores the full arena). Production code never
   calls this; it only constrains, never widens. */
void dma_debug_va_limit(cpu_u64 pages);
void dma_self_test(void);
#endif
