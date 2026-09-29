/* INT-A1 interrupt core: LAPIC + IOAPIC drivers, vector allocator, route
   table, PIC-to-APIC activation, and the self-test driver. Legacy ISA IRQs
   keep vectors 32-47 on both backends; the 48-127 pool is for INT-A2 MSI. */
#include "apic.h"
#include "io.h"
#include "pmm.h"
#include "serial.h"

static const char *error = "none";
static int lapic_ready;
static int ioapic_ready;
static int active;
static int imcr_apic;
static cpu_u64 lapic_phys;
static cpu_u64 lapic_va;
static cpu_u32 bsp_id;
static cpu_u32 lapic_ver;
static unsigned int lapic_max_lvt;
static unsigned int ioapic_count;
static unsigned int ioapic_max[ACPI_MAX_IOAPICS];
static cpu_u64 vector_counts[256];

static cpu_u8 vector_state[256];
static unsigned int vector_owner[256];

#define VEC_FREE 0
#define VEC_RESERVED 1
#define VEC_IRQ 2

static struct apic_route legacy_routes[IRQ_COUNT];
static struct apic_route dyn_routes[APIC_DYNAMIC_ROUTES];

/* --- raw CPU/MSR access ------------------------------------------------ */

static void cpuid(cpu_u32 leaf, cpu_u32 *a, cpu_u32 *b, cpu_u32 *c, cpu_u32 *d)
{
    __asm__ volatile ("cpuid" : "=a"(*a), "=b"(*b), "=c"(*c), "=d"(*d) : "a"(leaf), "c"(0));
}

static cpu_u64 rdmsr(cpu_u32 msr)
{
    cpu_u32 lo, hi;
    __asm__ volatile ("rdmsr" : "=a"(lo), "=d"(hi) : "c"(msr));
    return (cpu_u64)lo | ((cpu_u64)hi << 32);
}

static void wrmsr(cpu_u32 msr, cpu_u64 value)
{
    __asm__ volatile ("wrmsr" : : "c"(msr), "a"((cpu_u32)value), "d"((cpu_u32)(value >> 32)));
}

static cpu_u64 read_cr3(void)
{
    cpu_u64 v;
    __asm__ volatile ("mov %%cr3,%0" : "=r"(v));
    return v;
}

static void write_cr3(cpu_u64 v)
{
    __asm__ volatile ("mov %0,%%cr3" : : "r"(v) : "memory");
}

/* IRQ dispatch runs on the user CR3 for CPL3 frames (the handler phase
   needs user mappings: user_publish_stop requires the user address
   space), but slot 509 is deliberately never shared into user roots.
   Every APIC MMIO access round-trips through the kernel CR3 when needed.
   Safe: IF=0 throughout dispatch and all foreground callers (no
   reentrancy), and stack/code/data are shared-high mappings present on
   both roots. No-op on the kernel CR3. */
cpu_u64 apic_mmio_enter(void)
{
    struct vm_space *k = vm_kernel_space();
    cpu_u64 kroot = k ? k->root : 0;
    cpu_u64 now = read_cr3();
    if (kroot && now != kroot) write_cr3(kroot);
    return now;
}

void apic_mmio_exit(cpu_u64 saved)
{
    if (saved != read_cr3()) write_cr3(saved);
}

static cpu_u32 lapic_read(cpu_u32 reg)
{
    cpu_u64 saved = apic_mmio_enter();
    cpu_u32 v = *(volatile cpu_u32 *)(lapic_va + reg);
    apic_mmio_exit(saved);
    return v;
}

static void lapic_write(cpu_u32 reg, cpu_u32 value)
{
    cpu_u64 saved = apic_mmio_enter();
    *(volatile cpu_u32 *)(lapic_va + reg) = value;
    apic_mmio_exit(saved);
}

static cpu_u32 ioapic_read(unsigned int index, cpu_u8 reg)
{
    cpu_u64 base = APIC_IOAPIC_VA(index);
    cpu_u64 saved = apic_mmio_enter();
    *(volatile cpu_u32 *)(base + IOAPIC_SEL) = reg;
    cpu_u32 v = *(volatile cpu_u32 *)(base + IOAPIC_WIN);
    apic_mmio_exit(saved);
    return v;
}

static void ioapic_write(unsigned int index, cpu_u8 reg, cpu_u32 value)
{
    cpu_u64 base = APIC_IOAPIC_VA(index);
    cpu_u64 saved = apic_mmio_enter();
    *(volatile cpu_u32 *)(base + IOAPIC_SEL) = reg;
    *(volatile cpu_u32 *)(base + IOAPIC_WIN) = value;
    apic_mmio_exit(saved);
}

/* --- LAPIC ------------------------------------------------------------- */

enum apic_result apic_lapic_init(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    if (lapic_ready) return APIC_BUSY;
    if (!acpi_topology()->present) return APIC_NOT_READY;
    cpu_u32 a, b, c, d;
    cpuid(1, &a, &b, &c, &d);
    if (!(d & (1u << 9))) {
        error = "cpuid_no_apic";
        return APIC_HW;
    }
    cpu_u64 base_msr = rdmsr(0x1b);
    if (base_msr & (1ULL << 10)) {
        error = "x2apic_unsupported";
        return APIC_HW;
    }
    cpu_u64 hw_base = base_msr & 0xffffff000ULL;
    cpu_u64 fw_base = acpi_topology()->lapic_base;
    if (!fw_base || fw_base != hw_base) {
        error = "lapic_base_mismatch";
        return APIC_HW;
    }
    if (!(base_msr & (1ULL << 11))) {
        wrmsr(0x1b, base_msr | (1ULL << 11));
        if (!(rdmsr(0x1b) & (1ULL << 11))) {
            error = "apic_enable";
            return APIC_HW;
        }
    }
    if (vm_map_device(vm_kernel_space(), APIC_LAPIC_VA, fw_base, 1, VM_WRITE) != VM_OK) {
        error = "lapic_map";
        return APIC_MAP;
    }
    lapic_phys = fw_base;
    lapic_va = APIC_LAPIC_VA;
    /* No DFR programming: INT-A1 uses physical destination mode only, which
       ignores the destination format register (a logical-mode register).
       Strict DFR readback is also unportable (QEMU returns the 4-bit model,
       not the written all-ones word), so the SVR enable below is the LAPIC
       liveness proof instead. */
    lapic_write(LAPIC_SVR, (1u << 8) | APIC_VECTOR_SPURIOUS);
    if (lapic_read(LAPIC_SVR) != ((1u << 8) | APIC_VECTOR_SPURIOUS)) {
        error = "lapic_svr";
        return APIC_HW;
    }
    bsp_id = (lapic_read(LAPIC_ID) >> 24) & 0xffu;
    lapic_ver = lapic_read(LAPIC_VERSION);
    lapic_max_lvt = (lapic_ver >> 16) & 0xffu;
    lapic_ready = 1;
    return APIC_OK;
}

cpu_u32 apic_bsp_id(void) { return bsp_id; }
cpu_u64 apic_lapic_base(void) { return lapic_phys; }

void apic_lapic_eoi(void) { lapic_write(LAPIC_EOI, 0); }

int apic_lapic_isr_set(unsigned int vector)
{
    if (!lapic_ready || vector > 255) return 0;
    cpu_u32 reg = LAPIC_ISR_BASE + (vector / 32) * 0x10;
    return (lapic_read(reg) & (1u << (vector % 32))) != 0;
}

cpu_u32 apic_lapic_version(unsigned int *max_lvt)
{
    if (max_lvt) *max_lvt = lapic_max_lvt;
    return lapic_ver;
}

/* --- IOAPIC ------------------------------------------------------------ */

enum apic_result apic_ioapic_init(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    if (ioapic_ready) return APIC_BUSY;
    const struct acpi_topology *t = acpi_topology();
    if (!t->present || !t->ioapic_count) return APIC_NOT_READY;
    for (unsigned int i = 0; i < t->ioapic_count; ++i) {
        if (vm_map_device(vm_kernel_space(), APIC_IOAPIC_VA(i), t->ioapics[i].base, 1,
                          VM_WRITE) != VM_OK) {
            error = "ioapic_map";
            return APIC_MAP;
        }
        /* ID register is informational only: routing addresses IOAPICs by
           MMIO base, never by ID, so a mismatch is not fatal. */
        (void)ioapic_read(i, IOAPIC_ID);
        cpu_u32 ver = ioapic_read(i, IOAPIC_VER);
        ioapic_max[i] = (ver >> 16) & 0xffu;
        /* Quiesce: mask every redirection entry before any route exists. */
        for (unsigned int pin = 0; pin <= ioapic_max[i]; ++pin) {
            ioapic_write(i, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin), 0);
            ioapic_write(i, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin + 1), 0);
            ioapic_write(i, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin), 1u << 16);
        }
    }
    ioapic_count = t->ioapic_count;
    /* GSI ranges must be pairwise disjoint: overlapping ranges would make
       ownership ambiguous, so fail closed instead of guessing. */
    {
        cpu_u32 bases[ACPI_MAX_IOAPICS];
        for (unsigned int i = 0; i < ioapic_count; ++i) bases[i] = t->ioapics[i].gsi_base;
        if (apic_ranges_overlap(bases, ioapic_max, ioapic_count)) {
            error = "gsi_overlap";
            return APIC_HW;
        }
    }
    ioapic_ready = 1;
    return APIC_OK;
}

void apic_redir_bits(unsigned int vector, unsigned int dest, int level, int low, int mask,
                     cpu_u32 *lo, cpu_u32 *hi)
{
    *lo = (vector & 0xffu) | ((cpu_u32)(low & 1) << 13) | ((cpu_u32)(level & 1) << 15) |
          ((cpu_u32)(mask & 1) << 16);
    *hi = (cpu_u32)(dest & 0xffu) << 24;
}

unsigned int apic_ioapic_count(void) { return ioapic_count; }

unsigned int apic_ioapic_max(cpu_u32 index)
{
    if (index >= ioapic_count) return 0;
    return ioapic_max[index];
}

enum apic_result apic_gsi_owner_at(cpu_u32 gsi, const cpu_u32 *bases,
                                     const unsigned int *maxes, unsigned int n,
                                     unsigned int *index, unsigned int *pin)
{
    if (!bases || !maxes || !index || !pin || !n) return APIC_INVALID;
    for (unsigned int i = 0; i < n; ++i) {
        cpu_u64 base = bases[i];
        cpu_u64 end = base + maxes[i];
        if (end < base) continue;
        if ((cpu_u64)gsi >= base && (cpu_u64)gsi <= end) {
            *index = i;
            *pin = (unsigned int)((cpu_u64)gsi - base);
            return APIC_OK;
        }
    }
    return APIC_NOT_FOUND;
}

enum apic_result apic_gsi_owner(cpu_u32 gsi, unsigned int *index, unsigned int *pin)
{
    const struct acpi_topology *t = acpi_topology();
    if (!t->present || !ioapic_ready) return APIC_NOT_READY;
    cpu_u32 bases[ACPI_MAX_IOAPICS];
    for (unsigned int i = 0; i < ioapic_count; ++i) bases[i] = t->ioapics[i].gsi_base;
    return apic_gsi_owner_at(gsi, bases, ioapic_max, ioapic_count, index, pin);
}

int apic_ranges_overlap(const cpu_u32 *bases, const unsigned int *maxes, unsigned int n)
{
    if (!bases || !maxes) return 1;
    for (unsigned int i = 0; i < n; ++i)
        for (unsigned int j = i + 1; j < n; ++j) {
            cpu_u64 a0 = bases[i];
            cpu_u64 a1 = a0 + maxes[i];
            cpu_u64 b0 = bases[j];
            cpu_u64 b1 = b0 + maxes[j];
            if (a1 < a0 || b1 < b0 || (a0 <= b1 && b0 <= a1)) return 1;
        }
    return 0;
}

enum apic_result apic_program_route(unsigned int ioapic, unsigned int pin,
                                    unsigned int vector, unsigned int dest,
                                    int level, int low, int mask)
{
    if (!ioapic_ready || ioapic >= ioapic_count || pin > ioapic_max[ioapic] ||
        vector > 255 || dest > 255 || (level & ~1) || (low & ~1) || (mask & ~1))
        return APIC_INVALID;
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    cpu_u32 lo = 0, hi = 0;
    apic_redir_bits(vector, dest, level, low, mask, &lo, &hi);
    /* High half first while masked; the entry is never half-live. */
    ioapic_write(ioapic, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin + 1), hi);
    ioapic_write(ioapic, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin), lo | (1u << 16));
    if (!mask)
        ioapic_write(ioapic, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin), lo);
    return APIC_OK;
}

enum apic_result apic_read_route(unsigned int ioapic, unsigned int pin,
                                 unsigned int *vector, unsigned int *dest,
                                 int *level, int *low, int *mask)
{
    if (!ioapic_ready || ioapic >= ioapic_count || pin > ioapic_max[ioapic] || !vector ||
        !dest || !level || !low || !mask)
        return APIC_INVALID;
    cpu_u32 lo = ioapic_read(ioapic, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin));
    cpu_u32 hi = ioapic_read(ioapic, (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin + 1));
    *vector = lo & 0xffu;
    *dest = (hi >> 24) & 0xffu;
    *level = (lo >> 15) & 1;
    *low = (lo >> 13) & 1;
    *mask = (lo >> 16) & 1;
    return APIC_OK;
}

enum apic_result apic_set_route_mask(unsigned int ioapic, unsigned int pin, int mask)
{
    if (!ioapic_ready || ioapic >= ioapic_count || pin > ioapic_max[ioapic] || (mask & ~1))
        return APIC_INVALID;
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    cpu_u8 reg = (cpu_u8)(IOAPIC_REDIR_BASE + 2 * pin);
    cpu_u32 lo = ioapic_read(ioapic, reg);
    if (mask)
        lo |= 1u << 16;
    else
        lo &= ~(1u << 16);
    ioapic_write(ioapic, reg, lo);
    return APIC_OK;
}

/* --- vector allocator -------------------------------------------------- */

void apic_vector_init(void)
{
    for (unsigned int v = 0; v < 256; ++v) {
        vector_state[v] = VEC_FREE;
        vector_owner[v] = 0;
    }
    for (unsigned int v = 0; v < 48; ++v) vector_state[v] = VEC_RESERVED;
    for (unsigned int v = 128; v < 256; ++v) vector_state[v] = VEC_RESERVED;
}

int apic_vector_alloc(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return -1;
    for (unsigned int v = APIC_VECTOR_DYNAMIC_BASE; v <= APIC_VECTOR_DYNAMIC_END; ++v)
        if (vector_state[v] == VEC_FREE) {
            vector_state[v] = VEC_IRQ;
            return (int)v;
        }
    return -1;
}

int apic_vector_alloc_aligned(unsigned int n)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return -1;
    if (n == 0 || n > 32 || (n & (n - 1)) != 0) return -1;
    for (unsigned int base = APIC_VECTOR_DYNAMIC_BASE;
         base + n - 1 <= APIC_VECTOR_DYNAMIC_END; ++base) {
        unsigned int i;
        if (base % n != 0) continue;
        for (i = 0; i < n; ++i)
            if (vector_state[base + i] != VEC_FREE) break;
        if (i != n) continue;
        for (i = 0; i < n; ++i) vector_state[base + i] = VEC_IRQ;
        return (int)base;
    }
    return -1;
}

enum apic_result apic_vector_claim(unsigned int vector, unsigned int owner)
{
    if (!cpu_interrupts_disabled()) return APIC_CONTEXT;
    if (vector < APIC_VECTOR_IRQ_BASE || vector > APIC_VECTOR_IRQ_END) return APIC_INVALID;
    if (vector_state[vector] != VEC_RESERVED) return APIC_STATE;
    vector_state[vector] = VEC_IRQ;
    vector_owner[vector] = owner;
    return APIC_OK;
}

enum apic_result apic_vector_release(unsigned int vector)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    if (vector < APIC_VECTOR_DYNAMIC_BASE || vector > APIC_VECTOR_DYNAMIC_END)
        return APIC_INVALID;
    if (vector_state[vector] != VEC_IRQ) return APIC_STATE;
    vector_state[vector] = VEC_FREE;
    vector_owner[vector] = 0;
    return APIC_OK;
}

unsigned int apic_vector_owner(unsigned int vector)
{
    if (vector > 255) return 0;
    return vector_owner[vector];
}

unsigned int apic_vector_state(unsigned int vector)
{
    if (vector > 255) return VEC_RESERVED;
    return vector_state[vector];
}

unsigned int apic_vector_free(void)
{
    unsigned int free = 0;
    for (unsigned int v = APIC_VECTOR_DYNAMIC_BASE; v <= APIC_VECTOR_DYNAMIC_END; ++v)
        free += vector_state[v] == VEC_FREE;
    return free;
}

void apic_vector_restore(const cpu_u8 *state, const unsigned int *owner)
{
    if (!state || !owner) return;
    for (unsigned int v = 0; v < 256; ++v) {
        vector_state[v] = state[v] <= VEC_IRQ ? state[v] : VEC_RESERVED;
        vector_owner[v] = owner[v];
    }
}

/* --- ISA IRQ to GSI ---------------------------------------------------- */

enum apic_result apic_irq_gsi_at(unsigned int irq, const struct acpi_iso *isos,
                                   unsigned int count, cpu_u32 *gsi, int *level, int *low)
{
    if (!gsi || !level || !low || irq >= IRQ_COUNT) return APIC_INVALID;
    if (count && !isos) return APIC_INVALID;
    for (unsigned int i = 0; i < count; ++i) {
        if (isos[i].bus != 0 || isos[i].source != irq) continue;
        unsigned int pol = isos[i].flags & 3u;
        unsigned int trg = (isos[i].flags >> 2) & 3u;
        if (pol == 2 || trg == 2) return APIC_INVALID;
        *gsi = isos[i].gsi;
        *level = trg == 3;
        *low = pol == 3;
        return APIC_OK;
    }
    *gsi = irq;
    *level = 0;
    *low = 0;
    return APIC_OK;
}

enum apic_result apic_irq_gsi(unsigned int irq, cpu_u32 *gsi, int *level, int *low)
{
    const struct acpi_topology *t = acpi_topology();
    if (!t->present) return APIC_NOT_READY;
    return apic_irq_gsi_at(irq, t->isos, t->iso_count, gsi, level, low);
}

/* --- route table ------------------------------------------------------- */

static enum apic_result program_legacy(struct apic_route *route)
{
    cpu_u32 gsi = 0;
    int level = 0, low = 0;
    if (apic_irq_gsi(route->irq, &gsi, &level, &low) != APIC_OK) return APIC_INVALID;
    unsigned int index = 0, pin = 0;
    if (apic_gsi_owner(gsi, &index, &pin) != APIC_OK) return APIC_NOT_FOUND;
    route->gsi = gsi;
    route->ioapic = index;
    route->pin = pin;
    route->level = level;
    route->low = low;
    route->vector = APIC_VECTOR_IRQ_BASE + route->irq;
    if (apic_program_route(index, pin, route->vector, bsp_id, level, low, 1) != APIC_OK)
        return APIC_HW;
    unsigned int vector = 0, dest = 0;
    int rlevel = 0, rlow = 0, rmask = 0;
    if (apic_read_route(index, pin, &vector, &dest, &rlevel, &rlow, &rmask) != APIC_OK ||
        vector != route->vector || dest != bsp_id || rlevel != level || rlow != low ||
        rmask != 1)
        return APIC_HW;
    route->programmed = 1;
    route->masked = 1;
    return APIC_OK;
}

enum apic_result apic_route_register(unsigned int irq, irq_handler handler, void *opaque)
{
    if (irq >= IRQ_COUNT || irq == 2 || !handler) return APIC_INVALID;
    if (!cpu_interrupts_disabled()) return APIC_CONTEXT;
    struct apic_route *route = &legacy_routes[irq];
    if (route->used) return APIC_STATE;
    route->used = 1;
    route->legacy = 1;
    route->irq = irq;
    route->vector = APIC_VECTOR_IRQ_BASE + irq;
    route->masked = 1;
    route->programmed = 0;
    route->handler = handler;
    route->opaque = opaque;
    route->msi = APIC_ROUTE_NONE;
    route->msi_bdf = 0;
    route->msi_index = 0;
    if (apic_vector_claim(route->vector, irq) != APIC_OK) {
        route->used = 0;
        return APIC_STATE;
    }
    if (active && program_legacy(route) != APIC_OK) {
        route->used = 0;
        vector_state[route->vector] = VEC_RESERVED;
        vector_owner[route->vector] = 0;
        return APIC_HW;
    }
    return APIC_OK;
}

enum apic_result apic_route_set_handler(unsigned int irq, irq_handler handler, void *opaque)
{
    if (irq >= IRQ_COUNT || !handler) return APIC_INVALID;
    if (!cpu_interrupts_disabled()) return APIC_CONTEXT;
    struct apic_route *route = &legacy_routes[irq];
    if (!route->used) return APIC_STATE;
    route->handler = handler;
    route->opaque = opaque;
    return APIC_OK;
}

enum apic_result apic_route_set_masked(unsigned int irq, int masked)
{
    if (irq >= IRQ_COUNT || (masked & ~1)) return APIC_INVALID;
    /* No in-handler guard: the timer masks IRQ0 from inside its own handler. */
    if (!cpu_interrupts_disabled()) return APIC_CONTEXT;
    struct apic_route *route = &legacy_routes[irq];
    if (!route->used) return APIC_STATE;
    route->masked = masked;
    if (active) {
        if (!route->programmed) return APIC_STATE;
        return apic_set_route_mask(route->ioapic, route->pin, masked);
    }
    return APIC_OK;
}

struct apic_route *apic_route_for_vector(unsigned int vector)
{
    if (vector >= APIC_VECTOR_IRQ_BASE && vector <= APIC_VECTOR_IRQ_END) {
        struct apic_route *route = &legacy_routes[vector - APIC_VECTOR_IRQ_BASE];
        return route->used ? route : 0;
    }
    for (unsigned int i = 0; i < APIC_DYNAMIC_ROUTES; ++i)
        if (dyn_routes[i].used && dyn_routes[i].vector == vector) return &dyn_routes[i];
    return 0;
}

struct apic_route *apic_route_for_irq(unsigned int irq)
{
    if (irq >= IRQ_COUNT) return 0;
    return legacy_routes[irq].used ? &legacy_routes[irq] : 0;
}

enum apic_result apic_route_gsi_register(cpu_u32 gsi, int level, int low,
                                         irq_handler handler, void *opaque,
                                         unsigned int *vector)
{
    if (!handler || !vector || (level & ~1) || (low & ~1)) return APIC_INVALID;
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    if (!active) return APIC_NOT_READY;
    unsigned int slot = APIC_DYNAMIC_ROUTES;
    for (unsigned int i = 0; i < APIC_DYNAMIC_ROUTES; ++i)
        if (!dyn_routes[i].used) {
            slot = i;
            break;
        }
    if (slot == APIC_DYNAMIC_ROUTES) return APIC_EXHAUSTED;
    unsigned int index = 0, pin = 0;
    if (apic_gsi_owner(gsi, &index, &pin) != APIC_OK) return APIC_NOT_FOUND;
    int allocated = apic_vector_alloc();
    if (allocated < 0) return APIC_EXHAUSTED;
    struct apic_route *route = &dyn_routes[slot];
    route->used = 1;
    route->legacy = 0;
    route->irq = 0;
    route->gsi = gsi;
    route->vector = (unsigned int)allocated;
    route->ioapic = index;
    route->pin = pin;
    route->level = level;
    route->low = low;
    route->masked = 1;
    route->handler = handler;
    route->opaque = opaque;
    route->msi = APIC_ROUTE_NONE;
    route->msi_bdf = 0;
    route->msi_index = 0;
    vector_owner[route->vector] = 0x100 + slot;
    if (apic_program_route(index, pin, route->vector, bsp_id, level, low, 1) != APIC_OK) {
        route->used = 0;
        (void)apic_vector_release(route->vector);
        return APIC_HW;
    }
    route->programmed = 1;
    *vector = route->vector;
    return APIC_OK;
}

enum apic_result apic_route_gsi_unregister(unsigned int vector)
{
    if (vector < APIC_VECTOR_DYNAMIC_BASE || vector > APIC_VECTOR_DYNAMIC_END)
        return APIC_INVALID;
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    for (unsigned int i = 0; i < APIC_DYNAMIC_ROUTES; ++i) {
        struct apic_route *route = &dyn_routes[i];
        if (!route->used || route->vector != vector) continue;
        /* MSI routes have no IOAPIC pin: masking pin 0 here would
           silence the timer. They unregister through the MSI path,
           whose caller masked the device first. */
        if (route->msi != APIC_ROUTE_NONE) return APIC_INVALID;
        /* Mask before teardown so hardware can never deliver into a
           half-freed route (mirrors the DMA ownership discipline). */
        if (route->programmed &&
            apic_set_route_mask(route->ioapic, route->pin, 1) != APIC_OK)
            return APIC_HW;
        route->programmed = 0;
        route->used = 0;
        route->handler = 0;
        route->opaque = 0;
        route->msi = APIC_ROUTE_NONE;
        route->msi_bdf = 0;
        route->msi_index = 0;
        return apic_vector_release(vector);
    }
    return APIC_NOT_FOUND;
}

enum apic_result apic_route_msi_register(unsigned int kind, cpu_u32 bdf,
                                          unsigned int index,
                                          unsigned int vector,
                                          irq_handler handler, void *opaque)
{
    if ((kind != APIC_ROUTE_MSI && kind != APIC_ROUTE_MSIX) || !handler)
        return APIC_INVALID;
    if (vector < APIC_VECTOR_DYNAMIC_BASE || vector > APIC_VECTOR_DYNAMIC_END)
        return APIC_INVALID;
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    if (!active) return APIC_NOT_READY;
    if (vector_state[vector] != VEC_IRQ) return APIC_STATE;
    if (apic_route_for_vector(vector)) return APIC_STATE;
    unsigned int slot = APIC_DYNAMIC_ROUTES;
    for (unsigned int i = 0; i < APIC_DYNAMIC_ROUTES; ++i)
        if (!dyn_routes[i].used) {
            slot = i;
            break;
        }
    if (slot == APIC_DYNAMIC_ROUTES) return APIC_EXHAUSTED;
    struct apic_route *route = &dyn_routes[slot];
    route->used = 1;
    route->legacy = 0;
    route->irq = 0;
    route->gsi = 0;
    route->vector = vector;
    route->ioapic = 0;
    route->pin = 0;
    route->level = 0;
    route->low = 0;
    route->masked = 1;
    route->programmed = 1;
    route->handler = handler;
    route->opaque = opaque;
    route->msi = kind;
    route->msi_bdf = bdf;
    route->msi_index = index;
    vector_owner[vector] = 0x200 + slot;
    return APIC_OK;
}

enum apic_result apic_route_msi_unregister(unsigned int vector)
{
    if (vector < APIC_VECTOR_DYNAMIC_BASE || vector > APIC_VECTOR_DYNAMIC_END)
        return APIC_INVALID;
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    for (unsigned int i = 0; i < APIC_DYNAMIC_ROUTES; ++i) {
        struct apic_route *route = &dyn_routes[i];
        if (!route->used || route->vector != vector) continue;
        if (route->msi == APIC_ROUTE_NONE) return APIC_INVALID;
        /* No IOAPIC mask arm: the MSI caller masked the device-side
           vector first (its teardown order guarantees it). */
        route->programmed = 0;
        route->used = 0;
        route->handler = 0;
        route->opaque = 0;
        route->msi = APIC_ROUTE_NONE;
        route->msi_bdf = 0;
        route->msi_index = 0;
        return apic_vector_release(vector);
    }
    return APIC_NOT_FOUND;
}

/* --- activation -------------------------------------------------------- */

static enum apic_result imcr_to_apic(void)
{
    io_out8(IMCR_SELECT, IMCR_REG);
    io_out8(IMCR_DATA, IMCR_APIC_MODE);
    io_out8(IMCR_SELECT, IMCR_REG);
    imcr_apic = io_in8(IMCR_DATA) == IMCR_APIC_MODE;
    /* Proceed either way: the timer delivery proof below is the ground
       truth for routing (no-IMCR chipsets route ISA to the IOAPIC
       directly). A failed IMCR on legacy hardware shows up as missing
       ticks, never as silent misrouting. */
    return APIC_OK;
}

enum apic_result apic_activate(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return APIC_CONTEXT;
    if (active) return APIC_BUSY;
    if (!acpi_topology()->present) return APIC_NOT_READY;
    if (pic_in_service() != 0) {
        error = "pic_busy";
        return APIC_STATE;
    }
    if (apic_lapic_init() != APIC_OK) return APIC_HW;
    if (apic_ioapic_init() != APIC_OK) return APIC_HW;
    for (unsigned int irq = 0; irq < IRQ_COUNT; ++irq) {
        struct apic_route *route = &legacy_routes[irq];
        if (!route->used) continue;
        if (program_legacy(route) != APIC_OK) {
            error = "route_program";
            return APIC_HW;
        }
    }
    /* Preserve the logical enabled set across the flip (PIC mask now,
       IOAPIC masks after). IRQ2 cascade is never a route. Full width:
       a byte would silently drop the slave lines. */
    unsigned int was_on = 0;
    for (unsigned int irq = 0; irq < IRQ_COUNT; ++irq) {
        struct apic_route *route = &legacy_routes[irq];
        if (route->used && !route->masked) was_on |= 1u << irq;
    }
    (void)imcr_to_apic();
    io_out8(0x21, 0xff);
    io_out8(0xa1, 0xff);
    if (io_in8(0x21) != 0xff || io_in8(0xa1) != 0xff || pic_in_service() != 0) {
        error = "pic_quiesce";
        return APIC_HW;
    }
    active = 1;
    for (unsigned int irq = 0; irq < IRQ_COUNT; ++irq) {
        struct apic_route *route = &legacy_routes[irq];
        if (!route->used) continue;
        int masked = !(was_on & (1u << irq));
        route->masked = masked;
        if (apic_set_route_mask(route->ioapic, route->pin, masked) != APIC_OK) {
            error = "route_unmask";
            return APIC_HW;
        }
    }
    return APIC_OK;
}

int apic_active(void) { return active; }

void apic_note_vector(unsigned int vector)
{
    if (vector < 256) ++vector_counts[vector];
}

cpu_u64 apic_vector_count(unsigned int vector)
{
    if (vector > 255) return 0;
    return vector_counts[vector];
}

int apic_check(void)
{
    if (!active || !lapic_ready || !ioapic_ready) return 0;
    if (lapic_read(LAPIC_SVR) != ((1u << 8) | APIC_VECTOR_SPURIOUS)) return 0;
    if (io_in8(0x21) != 0xff || io_in8(0xa1) != 0xff || pic_in_service() != 0) return 0;
    for (unsigned int irq = 0; irq < IRQ_COUNT; ++irq) {
        const struct apic_route *route = &legacy_routes[irq];
        if (!route->used || !route->programmed) continue;
        unsigned int vector = 0, dest = 0;
        int level = 0, low = 0, mask = 0;
        if (apic_read_route(route->ioapic, route->pin, &vector, &dest, &level, &low,
                            &mask) != APIC_OK)
            return 0;
        if (vector != route->vector || dest != bsp_id || level != route->level ||
            low != route->low || mask != route->masked)
            return 0;
    }
    return 1;
}

const char *apic_error(void) { return error; }

/* --- self-test driver -------------------------------------------------- */

static void st_require(int condition, const char *reason)
{
    if (condition) return;
    serial_write("[APIC] failure=");
    serial_write(reason);
    serial_write(" detail=");
    serial_write(error);
    serial_write("\r\n");
    serial_flush();
    cpu_halt();
}

static void st_text(const char *value)
{
    st_require(serial_write(value), "serial");
}
static void st_number(cpu_u64 value)
{
    char buffer[21];
    unsigned int at = sizeof(buffer) - 1;
    buffer[at] = 0;
    do {
        buffer[--at] = (char)('0' + value % 10);
        value /= 10;
    } while (value);
    st_text(buffer + at);
}
static void st_field(const char *name, cpu_u64 value)
{
    st_text(name);
    st_number(value);
}

static volatile cpu_u64 prove_ticks;
static void prove_timer(cpu_u32 vector, void *opaque)
{
    (void)vector;
    (void)opaque;
    ++prove_ticks;
}

/* Keyboard delivery probe: records the vector and the 8042 output byte.
   Runs with the kbd driver's ISR swapped out, so the driver state machine
   never sees this byte; the byte is consumed here. */
static volatile cpu_u64 prove_kbd_ticks;
static volatile cpu_u32 prove_kbd_vec;
static volatile cpu_u8 prove_kbd_byte;
static void prove_kbd(cpu_u32 vector, void *opaque)
{
    (void)opaque;
    ++prove_kbd_ticks;
    prove_kbd_vec = vector;
    prove_kbd_byte = io_in8(0x60);
}

static void print_topology(void)
{
    const struct acpi_topology *t = acpi_topology();
    st_field("[ACPI] rsdp rev=", t->rsdp_revision);
    st_field(" xsdt=", (cpu_u64)t->via_xsdt);
    st_field(" entries=", t->root_entries);
    st_text("\r\n");
    st_field("[ACPI] madt lapic=", t->lapic_base);
    st_field(" flags=", t->madt_flags);
    st_field(" cpus=", t->cpu_count);
    st_field(" ioapics=", t->ioapic_count);
    st_field(" isos=", t->iso_count);
    st_field(" nmis=", t->nmi_count);
    st_field(" skipped=", t->skipped_records);
    st_field(" dups=", t->duplicate_isos);
    st_text("\r\n");
    for (unsigned int i = 0; i < t->cpu_count; ++i) {
        st_field("[ACPI] cpu uid=", t->cpus[i].uid);
        st_field(" apic=", t->cpus[i].apic_id);
        st_field(" flags=", t->cpus[i].flags);
        st_text("\r\n");
    }
    for (unsigned int i = 0; i < t->ioapic_count; ++i) {
        /* Firmware facts only (MADT carries no pin count); the measured
           redirection maximum prints after activation below. */
        st_field("[ACPI] ioapic id=", t->ioapics[i].id);
        st_field(" base=", t->ioapics[i].base);
        st_field(" gsi=", t->ioapics[i].gsi_base);
        st_text("\r\n");
    }
    for (unsigned int i = 0; i < t->iso_count; ++i) {
        /* Raw firmware values; resolved trigger/polarity is what the
           [IRQ] route lines below carry. */
        st_field("[ACPI] iso bus=", t->isos[i].bus);
        st_field(" irq=", t->isos[i].source);
        st_field(" gsi=", t->isos[i].gsi);
        st_field(" flags=", t->isos[i].flags);
        st_text("\r\n");
    }
}

static void print_fallback(const char *reason, const struct pmm_statistics *s0,
                           cpu_u64 tables0)
{
    st_text("[APIC] unavailable reason=");
    st_text(reason);
    st_text("\r\n[IRQ] backend=pic\r\n");
    struct pmm_statistics s1;
    st_require(pmm_statistics(&s1) == PMM_OK, "stats1");
    cpu_u64 frames = 0;
    cpu_u64 tables = 0;
    if (s1.allocated_bytes >= s0->allocated_bytes)
        frames = (s1.allocated_bytes - s0->allocated_bytes) / PMM_PAGE_SIZE;
    if (vm_kernel_space()->table_pages >= tables0)
        tables = vm_kernel_space()->table_pages - tables0;
    st_field("[APIC] cost frames=", frames);
    st_field(" tables=", tables);
    st_text("\r\n[APIC] apic verified\r\n");
    serial_flush();
}

void apic_self_test(void)
{
    st_require(cpu_interrupts_disabled() && !irq_in_context(), "context");
    st_require(serial_write("[APIC] self-test started\r\n"), "serial");
    acpi_test_synthetic();
    apic_test_synthetic();
    st_field("[APIC] synthetic acpi=", acpi_test_cases());
    st_field(" apic=", apic_test_cases());
    st_text("\r\n");
    struct pmm_statistics s0;
    st_require(pmm_statistics(&s0) == PMM_OK, "stats0");
    cpu_u64 tables0 = vm_kernel_space()->table_pages;
    if (acpi_discover() != ACPI_OK) {
        print_fallback(acpi_error(), &s0, tables0);
        return;
    }
    print_topology();
    if (apic_activate() != APIC_OK) {
        print_fallback(apic_error(), &s0, tables0);
        return;
    }
    unsigned int max_lvt = 0;
    cpu_u32 version = apic_lapic_version(&max_lvt);
    st_field("[APIC] lapic id=", bsp_id);
    st_field(" version=", version & 0xffu);
    st_field(" maxlvt=", max_lvt);
    st_text("\r\n");
    for (unsigned int i = 0; i < apic_ioapic_count(); ++i) {
        st_field("[APIC] ioapic idx=", i);
        st_field(" maxredir=", apic_ioapic_max(i));
        st_text("\r\n");
    }
    for (unsigned int irq = 0; irq < IRQ_COUNT; ++irq) {
        const struct apic_route *route = apic_route_for_irq(irq);
        if (!route) continue;
        st_field("[IRQ] route irq=", irq);
        st_field(" gsi=", route->gsi);
        st_field(" vector=", route->vector);
        st_text(route->level ? " trigger=level" : " trigger=edge");
        st_text(route->low ? " polarity=low" : " polarity=high");
        st_field(" ioapic=", route->ioapic);
        st_field(" masked=", (cpu_u64)route->masked);
        st_text("\r\n");
    }
    st_text("[IRQ] backend=apic");
    st_field(" imcr=", (cpu_u64)imcr_apic);
    st_text(" pic_masked=1\r\n");
    /* Timer proof on the APIC path: prime-count collection excludes any
       duplicate delivery (5 and 11 admit no multiple), with a masked
       quiet window between the two runs. */
    struct apic_route *r0 = apic_route_for_irq(0);
    st_require(r0 && r0->used, "route0");
    irq_handler saved_handler = r0->handler;
    void *saved_opaque = r0->opaque;
    int saved_masked = r0->masked;
    /* Vector counts accumulate since boot (PIC-era keyboard IRQs included),
       so silence is a delta against a pre-proof baseline, not absolute zero. */
    cpu_u64 kbd0 = apic_vector_count(APIC_VECTOR_IRQ_BASE + 1);
    st_require(apic_route_set_handler(0, prove_timer, 0) == APIC_OK, "prove_install");
    st_require(apic_route_set_masked(0, 0) == APIC_OK, "prove_unmask");
    prove_ticks = 0;
    while (prove_ticks < 5)
        __asm__ volatile ("sti; hlt; cli" : : : "memory");
    st_require(prove_ticks == 5, "prove_count5");
    st_require(apic_route_set_masked(0, 1) == APIC_OK, "prove_mask");
    /* Masked quiet window: must exceed one tick period (10ms) with margin,
       so a broken mask would deliver. 5M spins is ~100ms+ even under TCG. */
    for (volatile cpu_u64 spin = 0; spin < 5000000ULL; ++spin) {
    }
    st_require(prove_ticks == 5, "prove_quiet");
    st_require(apic_route_set_masked(0, 0) == APIC_OK, "prove_reunmask");
    while (prove_ticks < 11)
        __asm__ volatile ("sti; hlt; cli" : : : "memory");
    st_require(prove_ticks == 11, "prove_count11");
    st_require(apic_route_set_masked(0, saved_masked) == APIC_OK, "prove_remask");
    st_require(apic_route_set_handler(0, saved_handler, saved_opaque) == APIC_OK,
               "prove_restore");
    st_field("[IRQ] timer ticks=", prove_ticks);
    st_text("\r\n");
    /* No host keys arrive during this phase: IRQ1 must be silent. */
    st_require(apic_vector_count(APIC_VECTOR_IRQ_BASE + 1) == kbd0, "kbd_quiet");
    st_field("[IRQ] kbd base=", kbd0);
    st_field(" count=", apic_vector_count(APIC_VECTOR_IRQ_BASE + 1));
    st_text("\r\n");
    /* Keyboard DELIVERY proof on the APIC path: the 8042 ECHO command
       makes the keyboard return one byte (0xEE), which must arrive as
       exactly one vector-33 IRQ. Bounded at every step; the IRQ0 line
       stays masked so the only wake source is the echo itself. */
    {
        struct apic_route *r1 = apic_route_for_irq(1);
        st_require(r1 && r1->used, "kbd_route");
        irq_handler kbd_saved = r1->handler;
        void *kbd_opaque = r1->opaque;
        int kbd_masked = r1->masked;
        for (unsigned int i = 0; i < 256; ++i) {
            if (!(io_in8(0x64) & 1)) break;
            (void)io_in8(0x60);
        }
        st_require(!(io_in8(0x64) & 1), "kbd_drain");
        st_require(apic_route_set_handler(1, prove_kbd, 0) == APIC_OK, "kbd_install");
        st_require(apic_route_set_masked(1, 0) == APIC_OK, "kbd_unmask");
        prove_kbd_ticks = 0;
        prove_kbd_vec = 0;
        prove_kbd_byte = 0;
        {
            cpu_u64 spin = 0;
            while ((io_in8(0x64) & 2) && spin < 1000000ULL) ++spin;
            st_require(!(io_in8(0x64) & 2), "kbd_echo_ibf");
        }
        io_out8(0x60, 0xee);
        /* IF=1 bounded spin: pending IRQs deliver between iterations, and
           the bound fails loudly instead of HLT-hanging on a dead line. */
        __asm__ volatile ("sti" : : : "memory");
        for (cpu_u64 spin = 0; spin < 20000000ULL && !prove_kbd_ticks; ++spin) {
        }
        __asm__ volatile ("cli" : : : "memory");
        st_require(prove_kbd_ticks == 1, "kbd_echo_count");
        st_require(prove_kbd_vec == APIC_VECTOR_IRQ_BASE + 1, "kbd_echo_vec");
        st_require(prove_kbd_byte == 0xee, "kbd_echo_byte");
        st_require(apic_route_set_masked(1, kbd_masked) == APIC_OK, "kbd_remask");
        st_require(apic_route_set_handler(1, kbd_saved, kbd_opaque) == APIC_OK,
                   "kbd_restore");
        st_field("[IRQ] kbd echo byte=", prove_kbd_byte);
        st_field(" vector=", prove_kbd_vec);
        st_text("\r\n");
    }
    st_field("[IRQ] vector=", APIC_VECTOR_IRQ_BASE);
    st_field(" count=", apic_vector_count(APIC_VECTOR_IRQ_BASE));
    st_text("\r\n");
    st_field("[IRQ] vector=", APIC_VECTOR_IRQ_BASE + 1);
    st_field(" count=", apic_vector_count(APIC_VECTOR_IRQ_BASE + 1));
    st_text("\r\n");
    /* Live dynamic-route exercise on a dead pin (QEMU: no parallel port):
       PCI-INTx-shaped level/low registration, readback, unregister with
       mask-before-teardown, then first-fit vector reuse. */
    {
        unsigned int vector = 0;
        st_require(apic_route_gsi_register(7, 1, 1, prove_timer, 0, &vector) == APIC_OK &&
                       vector == APIC_VECTOR_DYNAMIC_BASE,
                   "dyn_register");
        unsigned int rvector = 0, rdest = 0;
        int rlevel = 0, rlow = 0, rmask = 0;
        struct apic_route *dyn = apic_route_for_vector(vector);
        st_require(dyn && !dyn->legacy, "dyn_lookup");
        st_require(apic_read_route(dyn->ioapic, dyn->pin, &rvector, &rdest, &rlevel, &rlow,
                                   &rmask) == APIC_OK &&
                       rvector == vector && rdest == bsp_id && rlevel == 1 && rlow == 1 &&
                       rmask == 1,
                   "dyn_readback");
        st_require(apic_route_gsi_unregister(vector) == APIC_OK, "dyn_unregister");
        st_require(apic_read_route(dyn->ioapic, dyn->pin, &rvector, &rdest, &rlevel, &rlow,
                                   &rmask) == APIC_OK &&
                       rmask == 1,
                   "dyn_masked");
        st_require(apic_route_gsi_register(7, 1, 1, prove_timer, 0, &vector) == APIC_OK &&
                       vector == APIC_VECTOR_DYNAMIC_BASE,
                   "dyn_reuse");
        st_require(apic_route_gsi_unregister(vector) == APIC_OK, "dyn_release");
        st_field("[IRQ] dyn vector=", APIC_VECTOR_DYNAMIC_BASE);
        st_text(" gsi=7 trigger=level polarity=low reuse=1\r\n");
    }
    st_require(apic_check(), "check");
    {
        struct pmm_statistics s1;
        st_require(pmm_statistics(&s1) == PMM_OK, "stats1");
        cpu_u64 frames = (s1.allocated_bytes - s0.allocated_bytes) / PMM_PAGE_SIZE;
        cpu_u64 tables = vm_kernel_space()->table_pages - tables0;
        st_require(frames == 1 && tables == 1, "cost");
        st_field("[APIC] cost frames=", frames);
        st_field(" tables=", tables);
        st_text("\r\n");
    }
    st_text("[APIC] apic verified\r\n");
    serial_flush();
}
