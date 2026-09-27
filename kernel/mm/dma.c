#include "dma.h"
#include "pmm.h"
#include "vm.h"
#include "io.h"
#include "irq.h"
#include "serial.h"

/* Arena geometry is verified by the compiler, not trusted by hand. */
_Static_assert(DMA_VA_BASE % DMA_PAGE_SIZE == 0, "DMA arena page-aligned");
_Static_assert((DMA_VA_BASE >> 48) == 0xFFFFu, "DMA arena canonical high");
_Static_assert(((DMA_VA_BASE >> 39) & 511u) == 385u, "DMA arena PML4 slot 385");
_Static_assert(DMA_VA_BASE + DMA_VA_MAX_PAGES * DMA_PAGE_SIZE > DMA_VA_BASE,
               "DMA arena end cannot wrap");
_Static_assert((((DMA_VA_BASE + DMA_VA_MAX_PAGES * DMA_PAGE_SIZE - 1) >> 39) & 511u) == 385u,
               "DMA arena stays inside slot 385");
_Static_assert(DMA_PAGE_SIZE == PMM_PAGE_SIZE, "DMA/PMM page size");
_Static_assert(DMA_PAGE_SIZE == VM_PAGE_SIZE, "DMA/VM page size");

struct dma_entry {
    cpu_u32 magic, live;
    cpu_u64 virt, phys, pages, size, align;
};
static struct dma_entry registry[DMA_MAX_BUFFERS];
static cpu_u64 va_cursor = DMA_VA_BASE, va_debug_pages;
static int ready;

static int context_ok(void) { return cpu_interrupts_disabled() && !irq_in_context(); }

/* Rollback/unwind helper: frames this call owns were all verified
   allocated, so a release failure means PMM self-contradiction. The
   kernel diagnoses, then halts (heap.c rollback precedent), never
   double-accounts and never hangs silently. */
static void release_frames(cpu_u64 phys, cpu_u64 pages)
{
    for (cpu_u64 i = 0; i < pages; ++i)
        if (pmm_release(phys + i * DMA_PAGE_SIZE) != PMM_OK) {
            (void)serial_write("[DMA] failure=pmm-rollback\r\n");
            (void)serial_flush();
            cpu_halt();
        }
}

enum dma_result dma_initialize(void)
{
    if (!context_ok()) return DMA_CONTEXT;
    if (ready) return DMA_BUSY;
    if (!vm_kernel_space()) return DMA_NOT_READY;
    for (unsigned int i = 0; i < DMA_MAX_BUFFERS; ++i)
        registry[i] = (struct dma_entry){0};
    va_cursor = DMA_VA_BASE;
    va_debug_pages = 0;
    ready = 1;
    return DMA_OK;
}

void dma_debug_va_limit(cpu_u64 pages)
{
    if (!context_ok() || !ready) return;
    va_debug_pages = pages > DMA_VA_MAX_PAGES ? DMA_VA_MAX_PAGES : pages;
}

enum dma_result dma_alloc(cpu_u64 size, cpu_u64 align, cpu_u64 max_bus,
                         struct dma_buffer *out)
{
    if (!context_ok()) return DMA_CONTEXT;
    if (!ready) return DMA_NOT_READY;
    if (!out) return DMA_INVALID;
    if (!size) return DMA_INVALID;
    if (!align || (align & (align - 1))) return DMA_ALIGNMENT;
    if (size > ~0ULL - (DMA_PAGE_SIZE - 1)) return DMA_OVERFLOW;
    cpu_u64 frames = (size + DMA_PAGE_SIZE - 1) / DMA_PAGE_SIZE;
    cpu_u64 alloc_size = frames * DMA_PAGE_SIZE;
    int slot = -1;
    for (unsigned int i = 0; i < DMA_MAX_BUFFERS; ++i)
        if (!registry[i].live) { slot = (int)i; break; }
    if (slot < 0) return DMA_BUSY;
    cpu_u64 limit_end = max_bus == DMA_ADDR_ANY ? 0 : max_bus + 1;
    cpu_u64 phys = 0;
    enum pmm_result pr = pmm_alloc_contiguous(frames, align, limit_end, &phys);
    if (pr == PMM_OUT_OF_MEMORY) return DMA_OOM;
    if (pr != PMM_OK) return DMA_INVALID;
    cpu_u64 va_pages = va_debug_pages ? va_debug_pages : DMA_VA_MAX_PAGES;
    cpu_u64 va_limit = DMA_VA_BASE + va_pages * DMA_PAGE_SIZE;
    if (va_cursor < DMA_VA_BASE || va_cursor > va_limit || alloc_size > va_limit - va_cursor) {
        release_frames(phys, frames); /* VA failure rolls physical back. */
        return DMA_OOM;
    }
    cpu_u64 va = va_cursor;
    va_cursor += alloc_size;
    struct vm_space *space = vm_kernel_space();
    if (!space) {
        release_frames(phys, frames);
        return DMA_NOT_READY;
    }
    /* Single transactional call: vm_map_range rolls its own tables
       back on failure, so no partial PTE state can leak here. The VA
       cursor is bump-only and stays advanced (documented). */
    if (vm_map_range(space, va, phys, frames, VM_WRITE) != VM_OK) {
        release_frames(phys, frames);
        return DMA_VM_ERROR;
    }
    /* Zero-on-alloc through the fresh mapping: no stale kernel or
       process memory may reach a device. */
    volatile cpu_u8 *zero = (volatile cpu_u8 *)va;
    for (cpu_u64 i = 0; i < alloc_size; ++i) zero[i] = 0;
    registry[slot] = (struct dma_entry){DMA_MAGIC_LIVE, 1, va, phys, frames, size, align};
    *out = (struct dma_buffer){(void *)va, phys, dma_phys_to_bus(phys), size,
                              alloc_size, align, DMA_MAGIC_LIVE, (cpu_u32)slot};
    return DMA_OK;
}

enum dma_result dma_free(struct dma_buffer *buf)
{
    if (!context_ok()) return DMA_CONTEXT;
    if (!ready) return DMA_NOT_READY;
    if (!buf) return DMA_INVALID;
    if (buf->magic != DMA_MAGIC_LIVE || buf->slot >= DMA_MAX_BUFFERS) return DMA_INVALID;
    struct dma_entry *entry = &registry[buf->slot];
    if (!entry->live || entry->magic != DMA_MAGIC_LIVE ||
        entry->virt != (cpu_u64)buf->virt || entry->phys != buf->phys ||
        entry->pages * DMA_PAGE_SIZE != buf->alloc_size) return DMA_INVALID;
    /* Drivers must have quiesced the device first; the DMA layer cannot
       stop hardware. Unmap (invalidate) before returning frames to PMM. */
    struct vm_space *space = vm_kernel_space();
    if (!space || vm_unmap_range(space, entry->virt, entry->pages) != VM_OK) cpu_halt();
    release_frames(entry->phys, entry->pages);
    entry->live = 0;
    entry->magic = DMA_MAGIC_DEAD;
    *buf = (struct dma_buffer){(void *)0, 0, 0, 0, 0, 0, DMA_MAGIC_DEAD, 0};
    return DMA_OK;
}

int dma_check(void)
{
    if (!context_ok() || !ready) return 0;
    struct vm_space *space = vm_kernel_space();
    if (!space) return 0;
    if (va_cursor < DMA_VA_BASE || va_cursor > DMA_VA_BASE + DMA_VA_MAX_PAGES * DMA_PAGE_SIZE)
        return 0;
    for (unsigned int i = 0; i < DMA_MAX_BUFFERS; ++i) {
        struct dma_entry *e = &registry[i];
        if (!e->live) continue;
        if (e->magic != DMA_MAGIC_LIVE || !e->pages || e->virt % DMA_PAGE_SIZE ||
            e->phys % DMA_PAGE_SIZE || e->virt < DMA_VA_BASE || e->virt > va_cursor ||
            e->pages > (va_cursor - e->virt) / DMA_PAGE_SIZE) return 0;
        for (cpu_u64 j = 0; j < e->pages; ++j) {
            enum pmm_state state;
            if (pmm_query(e->phys + j * DMA_PAGE_SIZE, &state) != PMM_OK ||
                state != PMM_STATE_ALLOCATED) return 0;
            struct vm_mapping m;
            if (vm_query(space, e->virt + j * DMA_PAGE_SIZE, &m) != VM_OK ||
                m.physical != e->phys + j * DMA_PAGE_SIZE ||
                m.permissions != VM_WRITE || m.uncached) return 0;
        }
        for (unsigned int k = i + 1; k < DMA_MAX_BUFFERS; ++k) {
            struct dma_entry *o = &registry[k];
            if (!o->live) continue;
            cpu_u64 a0 = e->phys, a1 = e->phys + e->pages * DMA_PAGE_SIZE;
            cpu_u64 b0 = o->phys, b1 = o->phys + o->pages * DMA_PAGE_SIZE;
            if (a0 < b1 && b0 < a1) return 0;
            a0 = e->virt; a1 = e->virt + e->pages * DMA_PAGE_SIZE;
            b0 = o->virt; b1 = o->virt + o->pages * DMA_PAGE_SIZE;
            if (a0 < b1 && b0 < a1) return 0;
        }
    }
    return 1;
}
