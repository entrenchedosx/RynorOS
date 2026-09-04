#include "heap.h"
#include "pmm.h"
#include "vm.h"
#include "io.h"
#include "irq.h"
#include "serial.h"

/* Address-ordered boundary tags, no auxiliary free list. All operations are
   bounded by arena_bytes / HEAP_MIN and require the single CPU with IF=0.
   Validate the complete partition before mutation; never follow payload links. */
#define HEAP_HDR 16u
#define HEAP_FTR 16u
#define HEAP_MIN 48u
#define HEAP_GRAN 16u
#define HEAP_MAGIC 0x524e484541504f42ULL
#define HEAP_FREE 1u
_Static_assert((HEAP_MAGIC & HEAP_FREE) == 0, "distinct used/free tags");
_Static_assert(HEAP_PAGE_SIZE == VM_PAGE_SIZE, "heap/VM page size");
_Static_assert(HEAP_ARENA_BYTES % HEAP_PAGE_SIZE == 0, "whole arena pages");
struct heap_block { cpu_u64 size, info; };
_Static_assert(sizeof(struct heap_block) == HEAP_HDR, "heap tag layout");

static cpu_u64 heap_base = HEAP_BASE, heap_arena = HEAP_ARENA_BYTES;
static cpu_u64 heap_mapped, heap_used, heap_used_blocks, heap_free_blocks;
static int heap_ready;
static struct heap_block *hb(cpu_u64 address) { return (void *)address; }
static cpu_u64 block_payload(cpu_u64 address) { return address + HEAP_HDR; }
static int context_ok(void) { return cpu_interrupts_disabled() && !irq_in_context(); }

static void publish(cpu_u64 address, cpu_u64 size, int free)
{
    struct heap_block tag = {size, HEAP_MAGIC | (free ? HEAP_FREE : 0)};
    *hb(address) = tag;
    *hb(address + size - HEAP_FTR) = tag;
}

/* Only read a footer after subtraction-based bounds checks. The cursor always
   advances by a validated size; fake headers inside payloads cannot enter it. */
int heap_check(void)
{
    if (!context_ok() || !heap_ready) return 0;
    cpu_u64 cursor = heap_base, end = heap_base + heap_arena;
    cpu_u64 used = 0, allocated = 0, free_count = 0;
    int previous_free = 0;
    while (cursor < end) {
        if (end - cursor < HEAP_MIN) return 0;
        struct heap_block tag = *hb(cursor);
        if (tag.size < HEAP_MIN || tag.size % HEAP_GRAN || tag.size > end - cursor ||
            (tag.info != HEAP_MAGIC && tag.info != (HEAP_MAGIC | HEAP_FREE))) return 0;
        struct heap_block footer = *hb(cursor + tag.size - HEAP_FTR);
        if (tag.size != footer.size || tag.info != footer.info) return 0;
        int free = tag.info == (HEAP_MAGIC | HEAP_FREE);
        if (free) {
            if (previous_free) return 0;
            ++free_count;
        } else { used += tag.size; ++allocated; }
        previous_free = free;
        cursor += tag.size;
    }
    return cursor == end && used == heap_used && allocated == heap_used_blocks &&
           free_count == heap_free_blocks && heap_mapped == heap_arena;
}

enum heap_result heap_initialize(void)
{
    if (!context_ok()) return HEAP_CONTEXT;
    if (heap_ready) return HEAP_BUSY;
    struct vm_space *space = vm_kernel_space();
    if (!space) return HEAP_NOT_READY;
    for (cpu_u64 i = 0; i < HEAP_ARENA_PAGES; ++i) {
        struct vm_mapping m;
        enum vm_result vr = vm_query(space, HEAP_BASE + i * HEAP_PAGE_SIZE, &m);
        if (vr == VM_OK) return HEAP_MAPPING_CONFLICT;
        if (vr != VM_NOT_MAPPED) return HEAP_VM_ERROR;
    }
    cpu_u64 mapped = 0;
    enum heap_result result = HEAP_OK;
    for (; mapped < HEAP_ARENA_PAGES; ++mapped) {
        cpu_u64 frame;
        enum pmm_result pr = pmm_allocate(&frame);
        if (pr != PMM_OK) {
            result = pr == PMM_OUT_OF_MEMORY ? HEAP_OUT_OF_MEMORY : HEAP_CORRUPT;
            break;
        }
        enum vm_result vr = vm_map(space, HEAP_BASE + mapped * HEAP_PAGE_SIZE, frame, VM_WRITE);
        if (vr != VM_OK) {
            if (pmm_release(frame) != PMM_OK) cpu_halt();
            result = vr == VM_OOM ? HEAP_OUT_OF_MEMORY : HEAP_VM_ERROR;
            break;
        }
    }
    if (result != HEAP_OK) {
        while (mapped) {
            cpu_u64 va = HEAP_BASE + --mapped * HEAP_PAGE_SIZE;
            struct vm_mapping m;
            /* Detach and invalidate before returning backing memory to PMM. */
            if (vm_query(space, va, &m) != VM_OK || vm_unmap(space, va) != VM_OK ||
                pmm_release(m.physical) != PMM_OK) {
                (void)serial_write("[HEAP] failure=initialization_rollback\r\n");
                cpu_halt();
            }
        }
        return result;
    }
    heap_used = 0; heap_used_blocks = 0; heap_free_blocks = 1;
    heap_mapped = HEAP_ARENA_BYTES;
    publish(HEAP_BASE, HEAP_ARENA_BYTES, 1);
    heap_ready = 1;
    return HEAP_OK;
}

enum heap_result heap_alloc(cpu_u64 size, cpu_u64 align, void **out)
{
    if (!context_ok()) return HEAP_CONTEXT;
    if (!heap_ready) return HEAP_NOT_READY;
    if (!out) return HEAP_INVALID;
    if (!size || size > ~0ULL - (HEAP_HDR + HEAP_FTR + HEAP_GRAN - 1)) return HEAP_OVERFLOW;
    if (align < HEAP_ALIGN_MIN || align > HEAP_ALIGN_MAX || (align & (align - 1)))
        return HEAP_ALIGNMENT;
    cpu_u64 needed = ((size + HEAP_GRAN - 1) & ~(cpu_u64)(HEAP_GRAN - 1)) + HEAP_HDR + HEAP_FTR;
    if (!heap_check()) return HEAP_CORRUPT;
    if (needed > heap_arena) return HEAP_OUT_OF_MEMORY;
    for (cpu_u64 cur = heap_base; cur < heap_base + heap_arena; cur += hb(cur)->size) {
        struct heap_block tag = *hb(cur);
        if (tag.info != (HEAP_MAGIC | HEAP_FREE)) continue;
        cpu_u64 gap = (align - block_payload(cur) % align) % align;
        /* Zero lead is valid. Nonzero fragments must hold a complete block. */
        while (gap && gap < HEAP_MIN) gap += align;
        if (gap > tag.size || needed > tag.size - gap) continue;
        cpu_u64 used = needed, tail = tag.size - gap - needed;
        if (tail < HEAP_MIN) { used += tail; tail = 0; }
        cpu_u64 chosen = cur + gap;
        if (gap) publish(cur, gap, 1);
        publish(chosen, used, 0);
        if (tail) publish(chosen + used, tail, 1);
        heap_free_blocks = heap_free_blocks - 1 + (gap != 0) + (tail != 0);
        heap_used += used; ++heap_used_blocks;
        *out = (void *)block_payload(chosen);
        return HEAP_OK;
    }
    return HEAP_OUT_OF_MEMORY;
}

enum heap_result heap_free(void *raw)
{
    if (!context_ok()) return HEAP_CONTEXT;
    if (!heap_ready) return HEAP_NOT_READY;
    cpu_u64 pointer = (cpu_u64)raw;
    if (pointer < heap_base + HEAP_HDR || pointer >= heap_base + heap_arena ||
        pointer % HEAP_GRAN) return HEAP_INVALID;
    if (!heap_check()) return HEAP_CORRUPT;
    cpu_u64 target = pointer - HEAP_HDR, cur = heap_base, previous = 0;
    while (cur < target) { previous = cur; cur += hb(cur)->size; }
    if (cur != target) return HEAP_INVALID;
    if (hb(cur)->info != HEAP_MAGIC) return HEAP_CORRUPT;
    cpu_u64 released = hb(cur)->size, size = released;
    cpu_u64 next = cur + size;
    if (next < heap_base + heap_arena && hb(next)->info == (HEAP_MAGIC | HEAP_FREE)) {
        size += hb(next)->size; --heap_free_blocks;
    }
    if (previous && hb(previous)->info == (HEAP_MAGIC | HEAP_FREE)) {
        size += hb(previous)->size; cur = previous; --heap_free_blocks;
    }
    publish(cur, size, 1);
    ++heap_free_blocks; --heap_used_blocks; heap_used -= released;
    return HEAP_OK;
}

enum heap_result heap_statistics(struct heap_statistics *out)
{
    if (!context_ok()) return HEAP_CONTEXT;
    if (!heap_ready) return HEAP_NOT_READY;
    if (!out) return HEAP_INVALID;
    if (!heap_check()) return HEAP_CORRUPT;
    *out = (struct heap_statistics){heap_arena, heap_mapped, heap_used,
        heap_arena - heap_used, heap_used_blocks, heap_free_blocks};
    return HEAP_OK;
}
