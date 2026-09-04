#include "vm.h"
#include "paging.h"
#include "io.h"
#include "irq.h"

static struct vm_space kernel_space;
static cpu_u64 window_pt;
static cpu_u64 physical_limit;
static int active;
#define VM_DEVICE_UC 8u /* Internal only: PAT index 3 (PCD|PWT), verified UC. */
extern char __text_end[], __rodata_end[], __data_start[], __fb_info_start[], __fb_info_end[];

int vm_canonical(cpu_u64 va)
{ return (va >> 48) == ((va & (1ULL << 47)) ? 0xffff : 0); }

static enum vm_result context(struct vm_space *s)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return VM_CONTEXT;
    if (!active) return VM_NOT_READY;
    if (!s || s->identity != s || !s->root) return VM_INVALID;
    return VM_OK;
}

static struct page_table *access_table(cpu_u64 physical)
{
    if (!active) return (struct page_table *)physical;
    volatile struct page_table *window = (void *)(VM_WINDOW + VM_PAGE_SIZE);
    window->entry[0].value = physical | PTE_PRESENT | PTE_WRITE | PTE_NX;
    page_invalidate(VM_WINDOW);
    return (struct page_table *)VM_WINDOW;
}

void *vm_frame_access(cpu_u64 physical)
{
    enum pmm_state state;
    if (!active || !cpu_interrupts_disabled() || irq_in_context() || physical % VM_PAGE_SIZE ||
        pmm_query(physical, &state) != PMM_OK || state != PMM_STATE_ALLOCATED) return (void *)0;
    return access_table(physical);
}

static page_entry read_entry(cpu_u64 table, unsigned int index)
{ return ((volatile struct page_table *)access_table(table))->entry[index]; }
static void write_entry(cpu_u64 table, unsigned int index, page_entry entry)
{ ((volatile struct page_table *)access_table(table))->entry[index] = entry; }

static enum vm_result allocate_table(struct vm_space *s, cpu_u64 *physical)
{
    enum pmm_result r = pmm_allocate(physical);
    if (r != PMM_OK) return r == PMM_OUT_OF_MEMORY ? VM_OOM : VM_CORRUPT;
    if (!active && *physical + VM_PAGE_SIZE > (cpu_u64)__identity_limit) {
        if (pmm_release(*physical) != PMM_OK) cpu_halt();
        return VM_OOM; /* Bootstrap must not dereference an unmapped frame. */
    }
    volatile struct page_table *t = access_table(*physical);
    for (unsigned int i = 0; i < VM_ENTRIES; ++i) t->entry[i].value = 0;
    ++s->table_pages;
    return VM_OK;
}
static void release_table(struct vm_space *s, cpu_u64 physical)
{
    if (!s->table_pages || pmm_release(physical) != PMM_OK) cpu_halt();
    --s->table_pages;
}

/* The only generated intermediate encoding is P|W|U, plus hardware A.
   Leaf permissions restrict accesses; no upper-level NX/W/U surprises. */
static enum vm_result child(page_entry e)
{
    if (!pte_has(e, PTE_PRESENT)) return VM_NOT_MAPPED;
    if (pte_has(e, PTE_HUGE)) return VM_UNSUPPORTED;
    if ((e.value & ~(PTE_ADDRESS | PTE_ACCESS)) != PTE_TABLE_FLAGS)
        return VM_CORRUPT;
    enum pmm_state state;
    if (pte_address(e) >= physical_limit || pmm_query(pte_address(e), &state) != PMM_OK ||
        state != PMM_STATE_ALLOCATED) return VM_CORRUPT;
    return VM_OK;
}

static enum vm_result walk(struct vm_space *s, cpu_u64 va, cpu_u64 path[VM_LEVELS])
{
    path[3] = s->root;
    for (unsigned int level = 3; level; --level) {
        page_entry e = read_entry(path[level], page_index(va, level));
        enum vm_result r = child(e);
        if (r != VM_OK) return r;
        path[level - 1] = pte_address(e);
    }
    return VM_OK;
}

static int empty(cpu_u64 physical)
{
    volatile struct page_table *t = access_table(physical);
    for (unsigned int i = 0; i < VM_ENTRIES; ++i) {
        if (physical == window_pt && i == 0) continue;
        if (t->entry[i].value) return 0;
    }
    return 1;
}

static void prune(struct vm_space *s, cpu_u64 va, cpu_u64 path[VM_LEVELS])
{
    unsigned int detached = 0;
    for (unsigned int level = 0; level < 3; ++level) {
        if (!empty(path[level])) break;
        write_entry(path[level + 1], page_index(va, level + 1), (page_entry){0});
        ++detached;
    }
    /* Invalidate AFTER the final parent unlink, BEFORE frame reuse. This also
       clears paging-structure caches; do not rely on incidental window flushes. */
    if (s == &kernel_space) page_invalidate(va);
    for (unsigned int level = 0; level < detached; ++level) release_table(s, path[level]);
}

static enum vm_result range_valid(struct vm_space *s, cpu_u64 va, cpu_u64 pages)
{
    enum vm_result r = context(s);
    if (r != VM_OK) return r;
    if (va % VM_PAGE_SIZE) return VM_ALIGNMENT;
    if (!pages) return VM_INVALID;
    if (pages > (~0ULL - va) / VM_PAGE_SIZE) return VM_OVERFLOW;
    cpu_u64 last = va + pages * VM_PAGE_SIZE - 1;
    if (!vm_canonical(va) || !vm_canonical(last) || (va >> 47) != (last >> 47))
        return VM_NONCANONICAL;
    /* PML4 510..511 reserved for the private frame window and future kernel
       layout. Slot 509 is the MMIO window served exclusively by
       vm_map_device/vm_unmap_device (below), never by ordinary mappings or
       unmap_range. The active low bootstrap footprint is immutable. */
    if (page_index(last, 3) >= 509 || (s == &kernel_space && va < (cpu_u64)__identity_limit))
        return VM_PERMISSION;
    return VM_OK;
}

/* Slot-509 window validator for foreign device mappings. Requires the whole
   range inside the one reserved MMIO slot; ordinary VM operations reject it. */
static enum vm_result device_valid(struct vm_space *s, cpu_u64 va, cpu_u64 pages)
{
    enum vm_result r = context(s);
    if (r != VM_OK) return r;
    if (s != &kernel_space) return VM_PERMISSION;
    if (va % VM_PAGE_SIZE) return VM_ALIGNMENT;
    if (!pages) return VM_INVALID;
    if (pages > (~0ULL - va) / VM_PAGE_SIZE) return VM_OVERFLOW;
    cpu_u64 last = va + pages * VM_PAGE_SIZE - 1;
    if (!vm_canonical(va) || !vm_canonical(last) || (va >> 47) != (last >> 47))
        return VM_NONCANONICAL;
    if (page_index(va, 3) != 509 || page_index(last, 3) != 509) return VM_PERMISSION;
    return VM_OK;
}

static enum vm_result permissions_valid(struct vm_space *s, unsigned int p)
{
    if (p & ~(VM_WRITE | VM_USER | VM_EXECUTE)) return VM_UNSUPPORTED;
    if ((p & VM_WRITE) && (p & VM_EXECUTE)) return VM_PERMISSION;
    if (s == &kernel_space && (p & VM_USER)) return VM_PERMISSION;
    return VM_OK;
}

static page_entry leaf(cpu_u64 pa, unsigned int p)
{
    return (page_entry){pa | PTE_PRESENT | ((p & VM_WRITE) ? PTE_WRITE : 0) |
        ((p & VM_USER) ? PTE_USER : 0) | ((p & VM_EXECUTE) ? 0 : PTE_NX) |
        ((p & VM_DEVICE_UC) ? PTE_PCD | PTE_PWT : 0)};
}

/* One-page transaction: on failed table allocation detach/free every new node. */
static enum vm_result insert(struct vm_space *s, cpu_u64 va, cpu_u64 pa, unsigned int p)
{
    cpu_u64 path[VM_LEVELS] = {0, 0, 0, s->root};
    unsigned int created = 0;
    enum vm_result result = VM_OK;
    for (unsigned int level = 3; level; --level) {
        page_entry e = read_entry(path[level], page_index(va, level));
        if (!e.value) {
            result = allocate_table(s, &path[level - 1]);
            if (result != VM_OK) break;
            created |= 1u << (level - 1);
            e.value = path[level - 1] | PTE_TABLE_FLAGS;
            write_entry(path[level], page_index(va, level), e);
        } else {
            result = child(e);
            if (result != VM_OK) break;
            path[level - 1] = pte_address(e);
        }
    }
    if (result == VM_OK) {
        if (read_entry(path[0], page_index(va, 0)).value) result = VM_EXISTS;
        else {
            write_entry(path[0], page_index(va, 0), leaf(pa, p));
            if (active && s == &kernel_space) page_invalidate(va);
            return VM_OK;
        }
    }
    for (unsigned int level = 0; level < 3; ++level) if (created & (1u << level)) {
        write_entry(path[level + 1], page_index(va, level + 1), (page_entry){0});
    }
    if (created && s == &kernel_space) page_invalidate(va);
    for (unsigned int level = 0; level < 3; ++level)
        if (created & (1u << level)) release_table(s, path[level]);
    return result;
}

enum vm_result vm_query(struct vm_space *s, cpu_u64 va, struct vm_mapping *out)
{
    enum vm_result r = context(s);
    if (r != VM_OK) return r;
    if (!out) return VM_INVALID;
    if (!vm_canonical(va)) return VM_NONCANONICAL;
    /* The private frame window is internal scratch, not a real mapping; it must
       not be reported as an owned leaf even while entry 0 holds a stale frame. */
    if (s == &kernel_space && (va & ~(VM_PAGE_SIZE - 1)) == VM_WINDOW)
        return VM_NOT_MAPPED;
    cpu_u64 path[VM_LEVELS];
    r = walk(s, va, path);
    if (r != VM_OK) return r;
    page_entry e = read_entry(path[0], page_index(va, 0));
    if (!pte_has(e, PTE_PRESENT)) return VM_NOT_MAPPED;
    if (pte_has(e, PTE_HUGE)) return VM_UNSUPPORTED; /* PAT is not supported either. */
    if (e.value & ~(PTE_ADDRESS | PTE_PRESENT | PTE_WRITE | PTE_USER | PTE_ACCESS | PTE_DIRTY | PTE_NX | PTE_PCD | PTE_PWT))
        return VM_UNSUPPORTED;
    cpu_u64 cache = e.value & (PTE_PCD | PTE_PWT);
    if (cache && (cache != (PTE_PCD | PTE_PWT) || s != &kernel_space ||
        page_index(va, 3) != 509 || !pte_has(e, PTE_WRITE) ||
        !pte_has(e, PTE_NX) || pte_has(e, PTE_USER))) return VM_CORRUPT;
    if (pte_address(e) >= physical_limit) return VM_CORRUPT;
    *out = (struct vm_mapping){pte_address(e) + va % VM_PAGE_SIZE,
        (pte_has(e, PTE_WRITE) ? VM_WRITE : 0) | (pte_has(e, PTE_USER) ? VM_USER : 0) |
        (pte_has(e, PTE_NX) ? 0 : VM_EXECUTE), pte_has(e, PTE_ACCESS), pte_has(e, PTE_DIRTY), cache != 0};
    return VM_OK;
}

enum vm_result vm_translate(struct vm_space *s, cpu_u64 va, cpu_u64 *physical)
{
    if (!physical) return VM_INVALID;
    struct vm_mapping m;
    enum vm_result r = vm_query(s, va, &m);
    if (r == VM_OK) *physical = m.physical;
    return r;
}

/* Frame ownership preflight: normal mappings require PMM-owned data frames;
   device mappings require physical memory the PMM will never hand out again
   (firmware-reserved or wholly undescribed MMIO), rejecting usable RAM. */
static int physical_allowed(cpu_u64 pa, int device)
{
    enum pmm_state state;
    if (pmm_query(pa, &state) != PMM_OK) return 0;
    if (!device) return state == PMM_STATE_ALLOCATED;
    if (pa < 0x100000 || state == PMM_STATE_FREE || state == PMM_STATE_ALLOCATED) return 0;
    unsigned int count;
    const struct pmm_region *regions = pmm_regions(&count);
    if (!regions) return 0;
    for (unsigned int i = 0; i < count; ++i)
        if (pa < regions[i].end && pa + VM_PAGE_SIZE > regions[i].base &&
            regions[i].kind != PMM_HOLE && regions[i].kind != PMM_RESERVED) return 0;
    return 1; /* Driver must additionally establish a real device aperture. */
}

static enum vm_result unmap_pages(struct vm_space *s, cpu_u64 va, cpu_u64 pages);

static enum vm_result map_pages(struct vm_space *s, cpu_u64 va, cpu_u64 pa,
                                cpu_u64 pages, unsigned int p, int device)
{
    if (pa % VM_PAGE_SIZE) return VM_ALIGNMENT;
    if (pa >= physical_limit || pages > (physical_limit - pa) / VM_PAGE_SIZE) return VM_PHYSICAL;
    for (cpu_u64 i = 0; i < pages; ++i) {
        if (!physical_allowed(pa + i * VM_PAGE_SIZE, device)) return VM_PHYSICAL;
        struct vm_mapping m;
        enum vm_result r = vm_query(s, va + i * VM_PAGE_SIZE, &m);
        if (r == VM_OK) return VM_EXISTS;
        if (r != VM_NOT_MAPPED) return r;
    }
    cpu_u64 done = 0;
    enum vm_result r = VM_OK;
    for (; done < pages; ++done) {
        r = insert(s, va + done * VM_PAGE_SIZE, pa + done * VM_PAGE_SIZE, p);
        if (r != VM_OK) break;
    }
    if (done != pages) {
        while (done) {
            --done;
            if (unmap_pages(s, va + done * VM_PAGE_SIZE, 1) != VM_OK) cpu_halt();
        }
        return r;
    }
    return VM_OK;
}

enum vm_result vm_map_range(struct vm_space *s, cpu_u64 va, cpu_u64 pa, cpu_u64 pages, unsigned int p)
{
    enum vm_result r = range_valid(s, va, pages);
    if (r != VM_OK) return r;
    if ((r = permissions_valid(s, p)) != VM_OK) return r;
    return map_pages(s, va, pa, pages, p, 0);
}
enum vm_result vm_map(struct vm_space *s, cpu_u64 va, cpu_u64 pa, unsigned int p)
{ return vm_map_range(s, va, pa, 1, p); }

enum vm_result vm_map_device(struct vm_space *s, cpu_u64 va, cpu_u64 pa, cpu_u64 pages, unsigned int p)
{
    enum vm_result r = device_valid(s, va, pages);
    if (r != VM_OK) return r;
    if (p != VM_WRITE) return VM_PERMISSION;
    cpu_u32 a, b, c, d;
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(1), "c"(0));
    if (d & (1u << 16)) {
        __asm__ volatile ("rdmsr" : "=a"(a), "=d"(d) : "c"(0x277));
        if ((a >> 24) != 0) return VM_UNSUPPORTED; /* PAT3 must be UC, not UC-. */
    }
    return map_pages(s, va, pa, pages, p | VM_DEVICE_UC, 1);
}

static enum vm_result unmap_pages(struct vm_space *s, cpu_u64 va, cpu_u64 pages)
{
    for (cpu_u64 i = 0; i < pages; ++i) {
        struct vm_mapping m;
        enum vm_result r = vm_query(s, va + i * VM_PAGE_SIZE, &m);
        if (r != VM_OK) return r;
    }
    for (cpu_u64 i = 0; i < pages; ++i) {
        cpu_u64 address = va + i * VM_PAGE_SIZE, path[VM_LEVELS];
        enum vm_result r = walk(s, address, path);
        if (r != VM_OK) return r;
        write_entry(path[0], page_index(address, 0), (page_entry){0});
        prune(s, address, path);
    }
    return VM_OK;
}

enum vm_result vm_unmap_range(struct vm_space *s, cpu_u64 va, cpu_u64 pages)
{
    enum vm_result r = range_valid(s, va, pages);
    if (r != VM_OK) return r;
    return unmap_pages(s, va, pages);
}
enum vm_result vm_unmap(struct vm_space *s, cpu_u64 va) { return vm_unmap_range(s, va, 1); }

enum vm_result vm_unmap_device(struct vm_space *s, cpu_u64 va, cpu_u64 pages)
{
    enum vm_result r = device_valid(s, va, pages);
    if (r != VM_OK) return r;
    return unmap_pages(s, va, pages);
}

enum vm_result vm_protect(struct vm_space *s, cpu_u64 va, unsigned int p)
{
    enum vm_result r = range_valid(s, va, 1);
    if (r != VM_OK) return r;
    if ((r = permissions_valid(s, p)) != VM_OK) return r;
    struct vm_mapping m;
    if ((r = vm_query(s, va, &m)) != VM_OK) return r;
    cpu_u64 path[VM_LEVELS];
    if ((r = walk(s, va, path)) != VM_OK) return r;
    page_entry e = leaf(m.physical, p);
    e.value |= read_entry(path[0], page_index(va, 0)).value & (PTE_ACCESS | PTE_DIRTY);
    write_entry(path[0], page_index(va, 0), e);
    if (s == &kernel_space) page_invalidate(va);
    return VM_OK;
}

enum vm_result vm_create(struct vm_space *s)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return VM_CONTEXT;
    if (!active) return VM_NOT_READY;
    if (!s || s->identity || s->root || s->table_pages) return VM_INVALID;
    cpu_u64 root;
    enum vm_result r = allocate_table(s, &root);
    if (r == VM_OK) { s->root = root; s->identity = s; }
    return r;
}

/* Bounded depth; raw handles/tables are internal kernel objects, not untrusted
   user input. Count/PMM validation precedes destruction; never free data leaves. */
static int inspect(cpu_u64 table, unsigned int level, cpu_u64 *count)
{
    enum pmm_state state;
    if (pmm_query(table, &state) != PMM_OK || state != PMM_STATE_ALLOCATED) return 0;
    ++*count;
    for (unsigned int i = 0; i < VM_ENTRIES; ++i) {
        page_entry e = read_entry(table, i);
        if (!e.value) continue;
        /* The kernel window PT's entry 0 is a transient access slot rewritten
           on every table access; it is not a mapping the VM API owns. */
        if (!level && table == window_pt && i == 0) continue;
        if (!level) {
            if (!(e.value & PTE_PRESENT) || (e.value & ~(PTE_ADDRESS | PTE_PRESENT | PTE_WRITE |
                PTE_USER | PTE_ACCESS | PTE_DIRTY | PTE_NX | PTE_PCD | PTE_PWT)) || pte_address(e) >= physical_limit) return 0;
            cpu_u64 cache = e.value & (PTE_PCD | PTE_PWT);
            if (cache && (cache != (PTE_PCD | PTE_PWT) || !(e.value & PTE_WRITE) ||
                !(e.value & PTE_NX) || (e.value & PTE_USER))) return 0;
        } else if (child(e) != VM_OK || !inspect(pte_address(e), level - 1, count)) return 0;
    }
    return 1;
}
int vm_check(struct vm_space *s)
{
    if (context(s) != VM_OK) return 0;
    cpu_u64 count = 0;
    return inspect(s->root, 3, &count) && count == s->table_pages;
}
static void destroy_tree(struct vm_space *s, cpu_u64 table, unsigned int level)
{
    if (level) for (unsigned int i = 0; i < VM_ENTRIES; ++i) {
        page_entry e = read_entry(table, i);
        if (pte_has(e, PTE_PRESENT)) destroy_tree(s, pte_address(e), level - 1);
    }
    release_table(s, table);
}
enum vm_result vm_destroy(struct vm_space *s)
{
    enum vm_result r = context(s);
    if (r != VM_OK) return r;
    if (s == &kernel_space) return VM_BUSY;
    if (!vm_check(s)) return VM_CORRUPT;
    destroy_tree(s, s->root, 3);
    s->root = 0; s->identity = (void *)0;
    return VM_OK;
}
struct vm_space *vm_kernel_space(void) { return active ? &kernel_space : (void *)0; }

enum vm_result vm_initialize(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return VM_CONTEXT;
    if (active) return VM_BUSY;
    cpu_u32 a, b, c, d;
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0x80000001), "c"(0));
    if (!(d & (1u << 20))) return VM_UNSUPPORTED; /* Require real NX, no silent weakening. */
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0x80000008), "c"(0));
    if ((a & 255) < 32 || (a & 255) > 52) return VM_UNSUPPORTED;
    physical_limit = 1ULL << (a & 255);
    cpu_u64 cr0, cr3, cr4;
    __asm__ volatile ("mov %%cr0,%0; mov %%cr3,%1; mov %%cr4,%2" : "=r"(cr0), "=r"(cr3), "=r"(cr4));
    if (!(cr0 & (1ULL << 31)) || !(cr0 & (1ULL << 16)) || !(cr4 & (1ULL << 5)) ||
        (cr4 & ((1ULL << 12) | (1ULL << 17) | (1ULL << 7))) || cr3 != (cpu_u64)__page_tables_start)
        return VM_UNSUPPORTED;
    struct pmm_statistics stats;
    if (pmm_statistics(&stats) != PMM_OK) return VM_NOT_READY;
    /* Seven bootstrap frames, all from PMM: root, low PDPT/PD/PT, window PDPT/PD/PT. */
    cpu_u64 f[7];
    unsigned int got = 0;
    enum vm_result r;
    for (; got < 7; ++got) {
        r = allocate_table(&kernel_space, &f[got]);
        if (r != VM_OK) {
            while (got) release_table(&kernel_space, f[--got]);
            return r;
        }
    }
    kernel_space.root = f[0]; kernel_space.identity = &kernel_space;
    window_pt = f[6];
    write_entry(f[0], 0, (page_entry){f[1] | PTE_TABLE_FLAGS});
    write_entry(f[1], 0, (page_entry){f[2] | PTE_TABLE_FLAGS});
    write_entry(f[2], 0, (page_entry){f[3] | PTE_TABLE_FLAGS});
    write_entry(f[0], page_index(VM_WINDOW, 3), (page_entry){f[4] | PTE_TABLE_FLAGS});
    write_entry(f[4], 0, (page_entry){f[5] | PTE_TABLE_FLAGS});
    write_entry(f[5], 0, (page_entry){f[6] | PTE_TABLE_FLAGS});
    write_entry(f[6], 1, leaf(f[6], VM_WRITE));
    /* Only live objects survive: code RX, rodata R/NX, data/stacks/bitmap RW/NX.
       Null, old boot tables and unused low-memory holes are no longer mapped. */
    for (cpu_u64 va = 0; va < (cpu_u64)__identity_limit; va += VM_PAGE_SIZE) {
        unsigned int p = 0; int mapped = 0;
        if (va >= (cpu_u64)__kernel_start && va < (cpu_u64)__text_end) { mapped = 1; p = VM_EXECUTE; }
        else if (va >= (cpu_u64)__text_end && va < (cpu_u64)__rodata_end) mapped = 1;
        else if ((va >= (cpu_u64)__data_start && va < (cpu_u64)__kernel_end) ||
                 (va >= (cpu_u64)__kernel_stack_start && va < (cpu_u64)__kernel_stack_end) ||
                 (va >= stats.metadata_base && va < stats.metadata_base + stats.metadata_bytes)) { mapped = 1; p = VM_WRITE; }
        else if (va >= (cpu_u64)__boot_map_start && va < (cpu_u64)__boot_map_end) mapped = 1;
        else if (va >= (cpu_u64)__fb_info_start && va < (cpu_u64)__fb_info_end) mapped = 1;
        if (mapped) write_entry(f[3], page_index(va, 0), leaf(va, p));
    }
    /* Enable NX before publishing NX-bearing entries, then replace all boot tables. */
    __asm__ volatile ("rdmsr" : "=a"(a), "=d"(d) : "c"(0xc0000080));
    cpu_u32 old_efer_low = a, old_efer_high = d;
    a |= 1u << 11;
    __asm__ volatile ("wrmsr" : : "a"(a), "d"(d), "c"(0xc0000080) : "memory");
    __asm__ volatile ("mov %0,%%cr3" : : "r"(kernel_space.root) : "memory");
    active = 1;
    __asm__ volatile ("mov %%cr3,%0" : "=r"(cr3));
    if (cr3 == kernel_space.root && vm_check(&kernel_space)) return VM_OK;

    /* Initialization is transactional even after the hardware switch. Restore
       the bootstrap address space before making the new tables inaccessible,
       then remove every published field and return their PMM ownership. */
    __asm__ volatile ("mov %0,%%cr3" : : "r"((cpu_u64)__page_tables_start) : "memory");
    __asm__ volatile ("wrmsr" : : "a"(old_efer_low), "d"(old_efer_high),
                      "c"(0xc0000080) : "memory");
    active = 0;
    kernel_space.root = 0;
    kernel_space.identity = (void *)0;
    window_pt = 0;
    physical_limit = 0;
    while (got) release_table(&kernel_space, f[--got]);
    return VM_CORRUPT;
}
