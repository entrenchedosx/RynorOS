#include "ksched.h"
#include "paging.h"
#include "io.h"
#include "serial.h"

/* Per-thread kernel stacks: KSTACK_PAGES payload frames plus a faulting guard
   page, all real PMM frames mapped RW/NX through the active kernel address
   space at a dedicated high virtual slot. The guard page is mapped then
   unmapped so any access below the payload raises a page fault, but its PMM
   frame stays allocated (a borrowed leaf) for the whole stack lifetime. */

static cpu_u64 slot_used;   /* one bit per KSTACK_MAX_THREADS slot */

static int context_ok(void) { return cpu_interrupts_disabled(); }

static void panic(const char *reason)
{
    if (serial_write("[KSTACK] failure=")) (void)serial_write(reason);
    (void)serial_write("\r\n");
    serial_flush();
    cpu_halt();
}

static void require(int condition, const char *reason)
{ if (!condition) panic(reason); }

static int slot_held(cpu_u64 slot)
{ return slot < KSTACK_MAX_THREADS && ((slot_used >> slot) & 1); }

static int slot_take(cpu_u64 slot)
{
    if (slot >= KSTACK_MAX_THREADS || slot_held(slot)) return 0;
    slot_used |= 1ULL << slot;
    return 1;
}

static void slot_release(cpu_u64 slot)
{ slot_used &= ~(1ULL << slot); }

int kstack_valid(const struct kstack *h)
{
    if (!h || h->magic != KSTACK_MAGIC || h->magic_inv != KSTACK_MAGIC_INV) return 0;
    if (h->slot >= KSTACK_MAX_THREADS || h->base != kstack_base_of(h->slot)) return 0;
    if (h->guard_phys % VM_PAGE_SIZE) return 0;
    return 1;
}

int kstack_alloc(struct kstack *out)
{
    if (!out || !context_ok() || !vm_kernel_space()) return 0;
    cpu_u64 slot;
    for (slot = 0; slot < KSTACK_MAX_THREADS; ++slot) if (!slot_held(slot)) break;
    if (slot >= KSTACK_MAX_THREADS) return 0;
    if (!slot_take(slot)) return 0;

    struct kstack h = {0};
    h.slot = slot;
    h.base = kstack_base_of(slot);
    h.magic = KSTACK_MAGIC;
    h.magic_inv = KSTACK_MAGIC_INV;

    /* Preflight: every slot page must be free in the kernel space. */
    for (cpu_u64 i = 0; i < KSTACK_SLOT_PAGES; ++i) {
        struct vm_mapping m;
        enum vm_result r = vm_query(vm_kernel_space(), h.base + i * VM_PAGE_SIZE, &m);
        if (r == VM_OK) goto fail_release_slot;
        if (r != VM_NOT_MAPPED) goto fail_release_slot;
    }

    /* Allocate all payload + guard frames first so failure leaves nothing mapped. */
    cpu_u64 frames[KSTACK_SLOT_PAGES];
    cpu_u64 got = 0;
    for (; got < KSTACK_SLOT_PAGES; ++got) {
        enum pmm_result r = pmm_allocate(&frames[got]);
        if (r != PMM_OK) break;
    }
    if (got != KSTACK_SLOT_PAGES) {
        while (got) require(pmm_release(frames[--got]) == PMM_OK, "alloc_release");
        goto fail_release_slot;
    }
    h.guard_phys = frames[0];   /* lowest slot page is the guard */
    for (cpu_u64 i = 0; i < KSTACK_PAGES; ++i) h.payload_phys[i] = frames[i + KSTACK_GUARD_PAGES];

    /* Map every slot page RW/NX, then unmask the guard page so it faults. */
    cpu_u64 mapped = 0;
    for (; mapped < KSTACK_SLOT_PAGES; ++mapped) {
        if (vm_map(vm_kernel_space(), h.base + mapped * VM_PAGE_SIZE, frames[mapped], VM_WRITE) != VM_OK)
            break;
    }
    if (mapped != KSTACK_SLOT_PAGES) {
        while (mapped) { require(vm_unmap(vm_kernel_space(), h.base + --mapped * VM_PAGE_SIZE) == VM_OK,
                                 "alloc_unmap"); }
        for (cpu_u64 i = 0; i < KSTACK_SLOT_PAGES; ++i)
            require(pmm_release(frames[i]) == PMM_OK, "alloc_release2");
        goto fail_release_slot;
    }
    /* Guard is now a genuine faulting non-present page, frame still owned. */
    if (vm_unmap(vm_kernel_space(), h.base) != VM_OK) {
        for (cpu_u64 i = KSTACK_SLOT_PAGES; i > 0; --i)
            require(vm_unmap(vm_kernel_space(), h.base + (i - 1) * VM_PAGE_SIZE) == VM_OK, "alloc_unmap3");
        for (cpu_u64 i = 0; i < KSTACK_SLOT_PAGES; ++i)
            require(pmm_release(frames[i]) == PMM_OK, "alloc_release3");
        goto fail_release_slot;
    }
    struct vm_mapping gm;
    require(vm_query(vm_kernel_space(), h.base, &gm) == VM_NOT_MAPPED, "guard_nonpresent");
    *out = h;
    return 1;

fail_release_slot:
    slot_release(slot);
    return 0;
}

int kstack_free(struct kstack *h)
{
    if (!kstack_valid(h) || !context_ok()) return 0;
    /* Unmap the payload pages (the guard is already non-present). */
    for (cpu_u64 i = KSTACK_GUARD_PAGES; i < KSTACK_SLOT_PAGES; ++i) {
        cpu_u64 va = h->base + i * VM_PAGE_SIZE;
        struct vm_mapping m;
        if (vm_query(vm_kernel_space(), va, &m) == VM_OK)
            require(vm_unmap(vm_kernel_space(), va) == VM_OK, "free_unmap");
    }
    struct vm_mapping gm;
    require(vm_query(vm_kernel_space(), h->base, &gm) == VM_NOT_MAPPED, "free_guard_still_hidden");
    /* Release the guard frame first, then the payload frames. */
    require(pmm_release(h->guard_phys) == PMM_OK, "free_release_guard");
    for (cpu_u64 i = 0; i < KSTACK_PAGES; ++i)
        require(pmm_release(h->payload_phys[i]) == PMM_OK, "free_release_payload");
    slot_release(h->slot);
    h->magic = 0; h->magic_inv = 0;
    return 1;
}
