#include "ksched.h"
#include "irq.h"
#include "io.h"
#include "serial.h"

/* An absent guard needs no backing frame. Private registry owns payload RAM. */
struct stack_record {
    struct kstack *owner;
    cpu_u64 generation;
    cpu_u64 frames[KSTACK_PAGES];
};
static struct stack_record records[KSTACK_MAX_THREADS];
static cpu_u64 generation;
static cpu_u64 base_of(cpu_u64 slot) { return KSTACK_BASE + slot * KSTACK_SLOT_SIZE; }
static int context_ok(void) { return cpu_interrupts_disabled() && !irq_in_context(); }
static void require(int ok)
{
    if (!ok) {
        (void)serial_write("[KSTACK] failure=rollback_or_ownership\r\n");
        cpu_halt();
    }
}
int kstack_valid(const struct kstack *h)
{
    return cpu_interrupts_disabled() && h && h->slot < KSTACK_MAX_THREADS && h->generation &&
           records[h->slot].owner == h && records[h->slot].generation == h->generation;
}
int kstack_bounds(const struct kstack *h, cpu_u64 *low, cpu_u64 *high)
{
    if (!cpu_interrupts_disabled() || !low || !high || !kstack_valid(h)) return 0;
    *low = base_of(h->slot) + KSTACK_GUARD_BYTES;
    *high = *low + KSTACK_PAYLOAD_BYTES;
    return 1;
}
int kstack_check(const struct kstack *h)
{
    if (!context_ok() || !kstack_valid(h)) return 0;
    struct vm_mapping m;
    if (vm_query(vm_kernel_space(), base_of(h->slot), &m) != VM_NOT_MAPPED) return 0;
    const struct stack_record *r = &records[h->slot];
    for (unsigned int i = 0; i < KSTACK_PAGES; ++i) {
        enum pmm_state state;
        if (vm_query(vm_kernel_space(), base_of(h->slot) + (i + 1) * VM_PAGE_SIZE, &m) != VM_OK ||
            m.physical != r->frames[i] || m.permissions != VM_WRITE ||
            pmm_query(r->frames[i], &state) != PMM_OK || state != PMM_STATE_ALLOCATED) return 0;
        for (unsigned int j = 0; j < i; ++j) if (r->frames[i] == r->frames[j]) return 0;
    }
    return 1;
}
int kstack_alloc(struct kstack *out)
{
    if (!context_ok() || !out || !vm_kernel_space() || generation == ~0ULL) return 0;
    for (unsigned int i = 0; i < KSTACK_MAX_THREADS; ++i)
        if (records[i].owner == out) return 0;
    if (out->generation || out->slot) return 0;
    unsigned int slot = 0;
    while (slot < KSTACK_MAX_THREADS && records[slot].owner) ++slot;
    if (slot == KSTACK_MAX_THREADS) return 0;
    for (unsigned int i = 0; i <= KSTACK_PAGES; ++i) {
        struct vm_mapping m;
        if (vm_query(vm_kernel_space(), base_of(slot) + i * VM_PAGE_SIZE, &m) != VM_NOT_MAPPED)
            return 0;
    }
    struct stack_record r = {0};
    unsigned int mapped = 0;
    for (; mapped < KSTACK_PAGES; ++mapped) {
        if (pmm_allocate(&r.frames[mapped]) != PMM_OK) break;
        cpu_u64 va = base_of(slot) + (mapped + 1) * VM_PAGE_SIZE;
        if (vm_map(vm_kernel_space(), va, r.frames[mapped], VM_WRITE) != VM_OK) {
            require(pmm_release(r.frames[mapped]) == PMM_OK);
            break;
        }
        for (unsigned int word = 0; word < VM_PAGE_SIZE / 8; ++word)
            ((volatile cpu_u64 *)va)[word] = 0;
    }
    if (mapped != KSTACK_PAGES) {
        while (mapped) {
            --mapped;
            require(vm_unmap(vm_kernel_space(), base_of(slot) + (mapped + 1) * VM_PAGE_SIZE) == VM_OK);
            require(pmm_release(r.frames[mapped]) == PMM_OK);
        }
        return 0;
    }
    r.owner = out; r.generation = ++generation;
    records[slot] = r;
    *out = (struct kstack){slot, r.generation};
    return 1;
}
int kstack_free(struct kstack *h)
{
    if (!context_ok() || !kstack_check(h)) return 0;
    cpu_u64 rsp;
    __asm__ volatile ("mov %%rsp, %0" : "=r"(rsp));
    cpu_u64 base = base_of(h->slot);
    if (rsp >= base && rsp < base + KSTACK_SLOT_SIZE) return 0;
    struct stack_record *r = &records[h->slot];
    /* Validate the WHOLE ownership/mapping set before removing any part. */
    for (unsigned int i = 0; i < KSTACK_PAGES; ++i) {
        require(vm_unmap(vm_kernel_space(), base + (i + 1) * VM_PAGE_SIZE) == VM_OK);
        require(pmm_release(r->frames[i]) == PMM_OK);
    }
    *r = (struct stack_record){0};
    *h = (struct kstack){0};
    return 1;
}
