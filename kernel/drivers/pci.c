/* PCI-A1 discovery + BAR resources over Configuration Mechanism #1.
 *
 * pc-i440fx exposes legacy PCI config space at 0xCF8/0xCFC (no ACPI/MCFG
 * on this target yet); display.c already proves the ports work. All
 * config traffic funnels through the pci_cfg_ops backend so a future
 * ECAM transport drops in without touching enumeration. Static
 * registry only (no heap, no pointers to transient buffers); silent
 * operation (all evidence rows live in gated pci-test.c).
 */
#include "pci.h"
#include "cpu.h"
#include "io.h"
#include "serial.h"
#include "vm.h"

#define PCI_CF8_ADDRESS 0x0CF8u
#define PCI_CF8_DATA 0x0CFCu
#define PCI_CF8_ENABLE 0x80000000u
#define PCI_BAR_ADDR32 0xFFFFFFF0u
#define PCI_BAR_ADDR_IO 0xFFFFFFFCu

static struct pci_device registry[PCI_MAX_DEVICES];
static cpu_u32 registry_count;
static int ready;
static cpu_u64 stat_reads;
static cpu_u64 stat_writes;
static cpu_u64 stat_buses;
static cpu_u64 stat_functions;
static cpu_u64 stat_bars;
static cpu_u64 va_cursor;

/* Single-CPU CF8/CFC critical section: the address/data pair is shared
   with display.c's local reader, so every transaction runs IF=0 with
   the incoming flag state restored (works from any IRQ state). */
static cpu_u64 irq_lock(void)
{
    cpu_u64 flags;
    __asm__ volatile ("pushfq; pop %0" : "=r"(flags) : : "memory");
    __asm__ volatile ("cli" : : : "memory");
    return flags;
}

static void irq_unlock(cpu_u64 flags)
{
    __asm__ volatile ("push %0; popfq" : : "r"(flags) : "memory");
}

static cpu_u32 cf8_addr(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off)
{
    return PCI_CF8_ENABLE | (bus << 16) | (dev << 11) | (fn << 8) |
        (off & 0xFCu);
}

static cpu_u32 cf8_read(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off)
{
    cpu_u64 flags = irq_lock();
    cpu_u32 val;
    io_out32(PCI_CF8_ADDRESS, cf8_addr(bus, dev, fn, off));
    val = io_in32(PCI_CF8_DATA);
    irq_unlock(flags);
    return val;
}

static void cf8_write(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                      cpu_u32 val)
{
    cpu_u64 flags = irq_lock();
    io_out32(PCI_CF8_ADDRESS, cf8_addr(bus, dev, fn, off));
    io_out32(PCI_CF8_DATA, val);
    irq_unlock(flags);
}

static const struct pci_cfg_ops legacy_ops = { cf8_read, cf8_write };
static const struct pci_cfg_ops *ops = &legacy_ops;

void pci_cfg_install_ops(const struct pci_cfg_ops *next)
{
    ops = next ? next : &legacy_ops;
}

static int bdf_ok(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    return bus <= PCI_MAX_BUS && dev <= PCI_MAX_DEVICE &&
        fn <= PCI_MAX_FUNCTION;
}

cpu_u8 pci_cfg_read8(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off)
{
    cpu_u32 dword;
    if (!bdf_ok(bus, dev, fn) || off >= PCI_CFG_SPACE_BYTES)
        return 0xFFu;
    ++stat_reads;
    dword = ops->read(bus, dev, fn, off & ~3u);
    return (cpu_u8)((dword >> ((off & 3u) * 8u)) & 0xFFu);
}

cpu_u16 pci_cfg_read16(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off)
{
    cpu_u32 dword;
    /* Word lane: naturally aligned pairs only, inside the space. */
    if (!bdf_ok(bus, dev, fn) || (off & 1u) ||
        off + 1u >= PCI_CFG_SPACE_BYTES)
        return 0xFFFFu;
    ++stat_reads;
    dword = ops->read(bus, dev, fn, off & ~3u);
    return (cpu_u16)((dword >> ((off & 2u) * 8u)) & 0xFFFFu);
}

cpu_u32 pci_cfg_read32(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off)
{
    if (!bdf_ok(bus, dev, fn) || (off & 3u) ||
        off >= PCI_CFG_SPACE_BYTES)
        return 0xFFFFFFFFu;
    ++stat_reads;
    return ops->read(bus, dev, fn, off);
}

void pci_cfg_write8(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                    cpu_u8 val)
{
    cpu_u32 dword;
    cpu_u32 shift;
    if (!bdf_ok(bus, dev, fn) || off >= PCI_CFG_SPACE_BYTES)
        return;
    ++stat_writes;
    dword = ops->read(bus, dev, fn, off & ~3u);
    shift = (off & 3u) * 8u;
    dword = (dword & ~(0xFFu << shift)) | ((cpu_u32)val << shift);
    ops->write(bus, dev, fn, off & ~3u, dword);
}

void pci_cfg_write16(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                     cpu_u16 val)
{
    cpu_u32 dword;
    cpu_u32 shift;
    if (!bdf_ok(bus, dev, fn) || (off & 1u) ||
        off + 1u >= PCI_CFG_SPACE_BYTES)
        return;
    ++stat_writes;
    dword = ops->read(bus, dev, fn, off & ~3u);
    shift = (off & 2u) * 8u;
    dword = (dword & ~(0xFFFFu << shift)) | ((cpu_u32)val << shift);
    ops->write(bus, dev, fn, off & ~3u, dword);
}

void pci_cfg_write32(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                     cpu_u32 val)
{
    if (!bdf_ok(bus, dev, fn) || (off & 3u) ||
        off >= PCI_CFG_SPACE_BYTES)
        return;
    ++stat_writes;
    ops->write(bus, dev, fn, off, val);
}

/* BAR size probe transaction: save BAR (+high) and COMMAND, decode off,
   write all-ones, read the mask, restore BAR then COMMAND exactly.
   Returns the raw mask dword(s); *mask_high only when is64. */
static void bar_probe(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                      int is64, cpu_u16 *cmd_saved, cpu_u32 *mask_low,
                      cpu_u32 *mask_high, cpu_u32 raw_low, cpu_u32 raw_high)
{
    cpu_u16 cmd = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
    *cmd_saved = cmd;
    pci_cfg_write16(bus, dev, fn, PCI_CFG_COMMAND,
                    (cpu_u16)(cmd & ~(PCI_COMMAND_IO | PCI_COMMAND_MEM)));
    pci_cfg_write32(bus, dev, fn, off, 0xFFFFFFFFu);
    if (is64)
        pci_cfg_write32(bus, dev, fn, off + 4u, 0xFFFFFFFFu);
    *mask_low = pci_cfg_read32(bus, dev, fn, off);
    *mask_high = is64 ? pci_cfg_read32(bus, dev, fn, off + 4u) : 0;
    /* Restore data path first, then re-enable decoding (never the
       reverse: decode must never run on probe residue). */
    pci_cfg_write32(bus, dev, fn, off, raw_low);
    if (is64)
        pci_cfg_write32(bus, dev, fn, off + 4u, raw_high);
    pci_cfg_write16(bus, dev, fn, PCI_CFG_COMMAND, cmd);
}

static int power_of_two(cpu_u64 v)
{
    return v != 0 && (v & (v - 1u)) == 0;
}

/* Post-probe restoration check: the BAR word(s) and COMMAND must read
   back exactly what was saved. A mismatch means broken hardware (or a
   broken probe); the caller drops the BAR instead of trusting it. */
static int bar_restored(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                        int is64, cpu_u16 cmd, cpu_u32 raw_low,
                        cpu_u32 raw_high)
{
    if (pci_cfg_read32(bus, dev, fn, off) != raw_low)
        return 0;
    if (is64 && pci_cfg_read32(bus, dev, fn, off + 4u) != raw_high)
        return 0;
    return pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND) == cmd;
}

/* Derive + validate one BAR into *out. slot consumed: 1, or 2 for a
   well-formed 64-bit BAR. Returns slots consumed, or 0 when the slot
   holds no usable BAR (zero, malformed, overflow, misaligned). */
static cpu_u32 bar_decode(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 slot,
                          cpu_u32 nslots, struct pci_bar *out)
{
    cpu_u32 off = PCI_CFG_BAR0 + slot * 4u;
    cpu_u32 raw = pci_cfg_read32(bus, dev, fn, off);
    cpu_u16 cmd_saved = 0;
    cpu_u32 mask_low = 0;
    cpu_u32 mask_high = 0;
    cpu_u64 base;
    cpu_u64 size;
    if (raw == 0)
        return 0;
    out->index = (cpu_u8)slot;
    out->prefetchable = 0;
    out->is64 = 0;
    out->raw_low = raw;
    out->raw_high = 0;
    out->mapped_va = 0;
    if (raw & 1u) {
        out->kind = (cpu_u8)PCI_BAR_IO;
        base = (cpu_u64)(raw & PCI_BAR_ADDR_IO);
        bar_probe(bus, dev, fn, off, 0, &cmd_saved, &mask_low,
                  &mask_high, raw, 0);
        size = (cpu_u64)(((~(mask_low & PCI_BAR_ADDR_IO)) + 1u) &
            0xFFFFFFFFu);
        (void)mask_high;
        if (!bar_restored(bus, dev, fn, off, 0, cmd_saved, raw, 0))
            return 0;
    } else {
        cpu_u32 type = (raw >> 1) & 3u;
        out->prefetchable = (cpu_u8)((raw >> 3) & 1u);
        if (type == 2u) {
            cpu_u32 high;
            cpu_u64 combined;
            if (slot + 1u >= nslots)
                return 0; /* high half outside the layout */
            high = pci_cfg_read32(bus, dev, fn, off + 4u);
            out->kind = (cpu_u8)PCI_BAR_MMIO64;
            out->is64 = 1;
            out->raw_high = high;
            base = ((cpu_u64)high << 32) | (raw & PCI_BAR_ADDR32);
            bar_probe(bus, dev, fn, off, 1, &cmd_saved, &mask_low,
                      &mask_high, raw, high);
            combined = ((cpu_u64)mask_high << 32) |
                (mask_low & PCI_BAR_ADDR32);
            size = ~combined + 1u;
            if (!bar_restored(bus, dev, fn, off, 1, cmd_saved, raw,
                              high))
                return 0;
            if (combined == 0 || size == 0 || !power_of_two(size) ||
                base % size != 0 || size > (cpu_u64)(~0ULL) - base)
                return 0;
            out->base = base;
            out->size = size;
            ++stat_bars;
            return 2;
        }
        out->kind = (cpu_u8)PCI_BAR_MMIO32;
        base = (cpu_u64)(raw & PCI_BAR_ADDR32);
        bar_probe(bus, dev, fn, off, 0, &cmd_saved, &mask_low,
                  &mask_high, raw, 0);
        size = (cpu_u64)(((~(mask_low & PCI_BAR_ADDR32)) + 1u) &
            0xFFFFFFFFu);
        (void)mask_high;
        if (!bar_restored(bus, dev, fn, off, 0, cmd_saved, raw, 0))
            return 0;
        if (size == 0 || !power_of_two(size) || base % size != 0 ||
            size > 0x100000000ULL - base)
            return 0;
        out->base = base;
        out->size = size;
        ++stat_bars;
        return 1;
    }
    /* I/O space is 16-bit: base and size stay inside 64KiB. */
    if (size == 0 || !power_of_two(size) || base % size != 0 ||
        base > 0xFFFFu || size > 0x10000u)
        return 0;
    out->base = base;
    out->size = size;
    ++stat_bars;
    return 1;
}

/* Fill one device record (identity, BARs, bridge windows). The vendor
   check already passed. Returns 1 when the record was stored, 0 when
   the registry is full (caller stops the scan). */
static int scan_function(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                         cpu_u16 vendor, struct pci_device *out,
                         cpu_u8 *sec_bus, cpu_u8 *sub_bus)
{
    cpu_u8 header;
    cpu_u32 slot;
    cpu_u32 nslots;
    cpu_u32 i;
    out->bus = bus;
    out->device = dev;
    out->function = fn;
    out->vendor_id = vendor;
    out->device_id = pci_cfg_read16(bus, dev, fn, PCI_CFG_DEVICE_ID);
    out->command = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
    out->status = pci_cfg_read16(bus, dev, fn, PCI_CFG_STATUS);
    out->revision = pci_cfg_read8(bus, dev, fn, PCI_CFG_REVISION);
    out->prog_if = pci_cfg_read8(bus, dev, fn, PCI_CFG_PROG_IF);
    out->subclass = pci_cfg_read8(bus, dev, fn, PCI_CFG_SUBCLASS);
    out->class_code = pci_cfg_read8(bus, dev, fn, PCI_CFG_CLASS);
    header = pci_cfg_read8(bus, dev, fn, PCI_CFG_HEADER_TYPE);
    out->header_type = (cpu_u8)(header & 0x7Fu);
    out->header_layout = (cpu_u8)(header & 0x7Fu);
    out->irq_line = pci_cfg_read8(bus, dev, fn, PCI_CFG_IRQ_LINE);
    out->irq_pin = pci_cfg_read8(bus, dev, fn, PCI_CFG_IRQ_PIN);
    out->bar_count = 0;
    out->sec_bus = 0;
    out->sub_bus = 0;
    for (i = 0; i < PCI_MAX_BAR_ENTRIES; ++i) {
        out->bars[i].index = (cpu_u8)i;
        out->bars[i].kind = (cpu_u8)PCI_BAR_NONE;
        out->bars[i].prefetchable = 0;
        out->bars[i].is64 = 0;
        out->bars[i].raw_low = 0;
        out->bars[i].raw_high = 0;
        out->bars[i].base = 0;
        out->bars[i].size = 0;
        out->bars[i].mapped_va = 0;
    }
    /* Only endpoint (0) and bridge (1) layouts have BARs; anything
       else stays a valid identity-only device. ROM BARs (0x30) and
       bridge windows are never size-probed. */
    if (out->header_layout == 0)
        nslots = PCI_MAX_BAR_SLOTS;
    else if (out->header_layout == 1)
        nslots = PCI_MAX_BRIDGE_BARS;
    else
        return 1;
    slot = 0;
    while (slot < nslots) {
        cpu_u32 used;
        if (out->bar_count >= PCI_MAX_BAR_ENTRIES)
            break;
        used = bar_decode(bus, dev, fn, slot, nslots,
                          &out->bars[out->bar_count]);
        if (used == 0) {
            ++slot;
            continue;
        }
        ++out->bar_count;
        slot += used;
    }
    if (out->header_layout == 1) {
        *sec_bus = pci_cfg_read8(bus, dev, fn, PCI_CFG_BRIDGE_SECONDARY);
        *sub_bus = pci_cfg_read8(bus, dev, fn, PCI_CFG_BRIDGE_SUBORDINATE);
        out->sec_bus = *sec_bus;
        out->sub_bus = *sub_bus;
    }
    return 1;
}

enum pci_result pci_initialize(void)
{
    /* Iterative bridge-aware walk: bus 0 first, then bridge-claimed
       buses in discovery order. Visited bitmap + hard queue bound
       stop cycles and runaway firmware. */
    static cpu_u8 queue[PCI_MAX_BUSES];
    static cpu_u64 visited[4];
    cpu_u32 qhead = 0;
    cpu_u32 qtail = 0;
    cpu_u32 i;
    for (i = 0; i < 4u; ++i)
        visited[i] = 0;
    for (i = 0; i < PCI_MAX_BUSES; ++i)
        queue[i] = 0;
    registry_count = 0;
    stat_buses = 0;
    stat_functions = 0;
    stat_bars = 0;
    queue[0] = 0;
    qtail = 1;
    visited[0] = 1u; /* bus 0 claimed before the loop */
    while (qhead < qtail) {
        cpu_u32 bus = queue[qhead++];
        cpu_u32 dev;
        ++stat_buses;
        for (dev = 0; dev <= PCI_MAX_DEVICE; ++dev) {
            cpu_u16 vendor;
            cpu_u8 header;
            cpu_u32 maxfn;
            cpu_u32 fn;
            vendor = pci_cfg_read16(bus, dev, 0, PCI_CFG_VENDOR_ID);
            if (vendor == PCI_VENDOR_ABSENT)
                continue;
            header = pci_cfg_read8(bus, dev, 0, PCI_CFG_HEADER_TYPE);
            maxfn = (header & 0x80u) ? PCI_MAX_FUNCTION : 0;
            for (fn = 0; fn <= maxfn; ++fn) {
                cpu_u16 v = fn == 0 ? vendor :
                    pci_cfg_read16(bus, dev, fn, PCI_CFG_VENDOR_ID);
                cpu_u8 sec = 0;
                cpu_u8 sub = 0;
                struct pci_device *slot;
                if (v == PCI_VENDOR_ABSENT)
                    continue;
                ++stat_functions;
                if (registry_count >= PCI_MAX_DEVICES) {
                    ready = 1; /* partial registry, fail safe */
                    return PCI_FULL;
                }
                slot = &registry[registry_count];
                scan_function(bus, dev, fn, v, slot, &sec, &sub);
                slot->multifunction = (cpu_u8)((header & 0x80u) != 0);
                ++registry_count;
                /* Bridge-claimed buses: malformed ranges, self-loops,
                   and revisits are ignored, never followed. */
                if (slot->header_layout != 1 || sec == 0 ||
                    sec > sub || sec == bus)
                    continue;
                if ((visited[sec / 64u] >> (sec % 64u)) & 1u)
                    continue;
                if (qtail >= PCI_MAX_BUSES)
                    continue;
                visited[sec / 64u] |= 1ULL << (sec % 64u);
                queue[qtail++] = sec;
            }
        }
    }
    ready = 1;
    return PCI_OK;
}

int pci_initialized(void)
{
    return ready;
}

cpu_u32 pci_device_count(void)
{
    return registry_count;
}

const struct pci_device *pci_device_at(cpu_u32 index)
{
    if (index >= registry_count)
        return 0;
    return &registry[index];
}

const struct pci_device *pci_find_bdf(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    cpu_u32 i;
    for (i = 0; i < registry_count; ++i)
        if (registry[i].bus == bus && registry[i].device == dev &&
            registry[i].function == fn)
            return &registry[i];
    return 0;
}

const struct pci_device *pci_find_vendor_device(cpu_u16 vendor,
                                                cpu_u16 device,
                                                cpu_u32 start)
{
    cpu_u32 i;
    for (i = start; i < registry_count; ++i)
        if (registry[i].vendor_id == vendor &&
            registry[i].device_id == device)
            return &registry[i];
    return 0;
}

const struct pci_device *pci_find_class(cpu_u32 class_code,
                                        cpu_u32 subclass, cpu_u32 prog_if,
                                        cpu_u32 start)
{
    cpu_u32 i;
    if ((class_code != PCI_MATCH_ANY && class_code > 0xFFu) ||
        (subclass != PCI_MATCH_ANY && subclass > 0xFFu) ||
        (prog_if != PCI_MATCH_ANY && prog_if > 0xFFu))
        return 0;
    for (i = start; i < registry_count; ++i) {
        if (class_code != PCI_MATCH_ANY &&
            registry[i].class_code != (cpu_u8)class_code)
            continue;
        if (subclass != PCI_MATCH_ANY &&
            registry[i].subclass != (cpu_u8)subclass)
            continue;
        if (prog_if != PCI_MATCH_ANY &&
            registry[i].prog_if != (cpu_u8)prog_if)
            continue;
        return &registry[i];
    }
    return 0;
}

static struct pci_device *dev_mut(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    cpu_u32 i;
    for (i = 0; i < registry_count; ++i)
        if (registry[i].bus == bus && registry[i].device == dev &&
            registry[i].function == fn)
            return &registry[i];
    return 0;
}

enum pci_result pci_map_bar(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                            cpu_u32 bar_entry, cpu_u64 *va_out)
{
    struct pci_device *d = dev_mut(bus, dev, fn);
    struct pci_bar *bar;
    cpu_u64 offset;
    cpu_u64 total;
    cpu_u64 pages;
    cpu_u64 va;
    cpu_u64 pa_page;
    if (!d || !va_out)
        return PCI_INVALID;
    if (bar_entry >= d->bar_count)
        return PCI_INVALID;
    bar = &d->bars[bar_entry];
    if (bar->kind != PCI_BAR_MMIO32 && bar->kind != PCI_BAR_MMIO64)
        return PCI_UNSUPPORTED;
    if (bar->size == 0)
        return PCI_RANGE;
    if (bar->mapped_va != 0) {
        *va_out = bar->mapped_va;
        return PCI_OK;
    }
    /* Overflow-checked page span: total/4096 + tail needs no +4095. */
    offset = bar->base & 0xFFFu;
    if (bar->size > (cpu_u64)(~0ULL) - offset)
        return PCI_RANGE;
    total = offset + bar->size;
    pages = total / VM_PAGE_SIZE + (total % VM_PAGE_SIZE != 0);
    if (pages == 0)
        return PCI_RANGE;
    if (va_cursor == 0)
        va_cursor = PCI_MMIO_VA_BASE;
    /* va_cursor + pages*4096 must stay inside the PCI VA window. */
    if (pages > (VM_MMIO_END - va_cursor) / VM_PAGE_SIZE)
        return PCI_FULL;
    va = va_cursor;
    pa_page = bar->base & ~(cpu_u64)0xFFFu;
    if (vm_map_device(vm_kernel_space(), va, pa_page, pages, VM_WRITE) !=
        VM_OK)
        return PCI_VM_ERROR;
    va_cursor += pages * VM_PAGE_SIZE;
    bar->mapped_va = va + offset;
    *va_out = bar->mapped_va;
    return PCI_OK;
}

enum pci_result pci_unmap_bar(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                              cpu_u32 bar_entry)
{
    struct pci_device *d = dev_mut(bus, dev, fn);
    struct pci_bar *bar;
    cpu_u64 offset;
    cpu_u64 total;
    cpu_u64 pages;
    cpu_u64 va;
    if (!d)
        return PCI_INVALID;
    if (bar_entry >= d->bar_count)
        return PCI_INVALID;
    bar = &d->bars[bar_entry];
    if (bar->mapped_va == 0)
        return PCI_INVALID;
    offset = bar->base & 0xFFFu;
    total = offset + bar->size;
    pages = total / VM_PAGE_SIZE + (total % VM_PAGE_SIZE != 0);
    va = bar->mapped_va - offset;
    if (vm_unmap_device(vm_kernel_space(), va, pages) != VM_OK)
        return PCI_VM_ERROR;
    bar->mapped_va = 0;
    /* VA cursor never rewinds (documented PCI-A1 lifetime). */
    return PCI_OK;
}

void pci_stats_read(struct pci_stats *out)
{
    if (!out)
        return;
    out->cfg_reads = stat_reads;
    out->cfg_writes = stat_writes;
    out->buses_scanned = stat_buses;
    out->functions_seen = stat_functions;
    out->bars_sized = stat_bars;
    out->registry_bytes = (cpu_u64)sizeof registry;
}

const char *pci_class_name(cpu_u8 class_code, cpu_u8 subclass)
{
    switch (class_code) {
    case 0x01u:
        if (subclass == 0x01u)
            return "ide";
        if (subclass == 0x06u)
            return "sata";
        if (subclass == 0x08u)
            return "nvme";
        return "storage";
    case 0x02u:
        return "network";
    case 0x03u:
        return "display";
    case 0x04u:
        return "multimedia";
    case 0x06u:
        if (subclass == 0x00u)
            return "host-bridge";
        if (subclass == 0x01u)
            return "isa-bridge";
        if (subclass == 0x04u)
            return "pci-bridge";
        return "bridge";
    case 0x0Cu:
        if (subclass == 0x03u)
            return "usb";
        return "serial-bus";
    default:
        return "unknown";
    }
}
