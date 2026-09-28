/* INT-A1 firmware discovery: RSDP scan, RSDT/XSDT walk, MADT parse.
   No AML. All multi-byte fields use explicit little-endian loads so parser
   behavior never depends on buffer alignment. */
#include "acpi.h"
#include "io.h"
#include "irq.h"
#include "pmm.h"

static struct acpi_topology topology;
static int discovered;
static int busy;
static const char *error = "none";
static cpu_u64 persist_used;
static int transient_mapped;
static cpu_u64 transient_base;

static cpu_u16 rd16(const cpu_u8 *p) { return (cpu_u16)((cpu_u16)p[0] | ((cpu_u16)p[1] << 8)); }
static cpu_u32 rd32(const cpu_u8 *p)
{
    return (cpu_u32)p[0] | ((cpu_u32)p[1] << 8) | ((cpu_u32)p[2] << 16) | ((cpu_u32)p[3] << 24);
}
static cpu_u64 rd64(const cpu_u8 *p)
{
    return (cpu_u64)rd32(p) | ((cpu_u64)rd32(p + 4) << 32);
}
static int sig_eq(const cpu_u8 *p, const char *sig, unsigned int n)
{
    for (unsigned int i = 0; i < n; ++i)
        if (p[i] != (cpu_u8)sig[i]) return 0;
    return 1;
}

int acpi_checksum_ok(const cpu_u8 *bytes, cpu_u64 length)
{
    if (!bytes || !length) return 0;
    cpu_u8 sum = 0;
    for (cpu_u64 i = 0; i < length; ++i) sum = (cpu_u8)(sum + bytes[i]);
    return sum == 0;
}

/* Common SDT header: validates signature/length/checksum, returns length. */
static enum acpi_result check_header(const cpu_u8 *bytes, cpu_u64 length,
                                     const char *sig, cpu_u32 *out_len)
{
    if (!bytes || !out_len) return ACPI_INVALID;
    if (length < 36) return ACPI_LENGTH;
    if (sig && !sig_eq(bytes, sig, 4)) return ACPI_INVALID;
    cpu_u32 len = rd32(bytes + 4);
    if (len < 36 || (cpu_u64)len > length) return ACPI_LENGTH;
    if (!acpi_checksum_ok(bytes, len)) return ACPI_CHECKSUM;
    *out_len = len;
    return ACPI_OK;
}

enum acpi_result acpi_parse_rsdp(const cpu_u8 *bytes, cpu_u64 length,
                                 cpu_u32 *revision, int *have_xsdt,
                                 cpu_u64 *rsdt, cpu_u64 *xsdt)
{
    if (!bytes || !revision || !have_xsdt || !rsdt || !xsdt) return ACPI_INVALID;
    if (length < 20) return ACPI_LENGTH;
    if (!sig_eq(bytes, "RSD PTR ", 8)) return ACPI_INVALID;
    if (!acpi_checksum_ok(bytes, 20)) return ACPI_CHECKSUM;
    cpu_u32 rev = bytes[15];
    *rsdt = rd32(bytes + 16);
    if (!rev) {
        *revision = 0;
        *have_xsdt = 0;
        *xsdt = 0;
        return ACPI_OK;
    }
    /* Revision 1 never shipped; treat any nonzero revision as v2+ and let
       the length field decide. A garbage length fails closed below. */
    if (length < 36) return ACPI_LENGTH;
    cpu_u32 len = rd32(bytes + 20);
    if (len < 36 || len > 4096 || (cpu_u64)len > length) return ACPI_LENGTH;
    if (!acpi_checksum_ok(bytes, len)) return ACPI_CHECKSUM;
    *xsdt = rd64(bytes + 24);
    *revision = rev;
    *have_xsdt = *xsdt != 0;
    return ACPI_OK;
}

enum acpi_result acpi_parse_root(const cpu_u8 *bytes, cpu_u64 length, int xsdt,
                                 unsigned int *entries)
{
    if (!bytes || !entries) return ACPI_INVALID;
    cpu_u32 len = 0;
    enum acpi_result r = check_header(bytes, length, xsdt ? "XSDT" : "RSDT", &len);
    if (r != ACPI_OK) return r;
    cpu_u64 stride = xsdt ? 8 : 4;
    cpu_u64 body = (cpu_u64)len - 36;
    if (body % stride) return ACPI_LENGTH;
    cpu_u64 count = body / stride;
    if (count > ACPI_ROOT_SCAN_MAX) return ACPI_CAPACITY;
    *entries = (unsigned int)count;
    return ACPI_OK;
}

enum acpi_result acpi_root_entry(const cpu_u8 *bytes, cpu_u64 length, int xsdt,
                                 unsigned int index, cpu_u64 *phys)
{
    if (!bytes || !phys) return ACPI_INVALID;
    if (length < 36) return ACPI_LENGTH;
    cpu_u32 len = rd32(bytes + 4);
    if (len < 36 || (cpu_u64)len > length) return ACPI_LENGTH;
    cpu_u64 stride = xsdt ? 8 : 4;
    cpu_u64 body = (cpu_u64)len - 36;
    if (body % stride) return ACPI_LENGTH;
    if ((cpu_u64)index >= body / stride) return ACPI_INVALID;
    cpu_u64 at = 36 + (cpu_u64)index * stride;
    *phys = xsdt ? rd64(bytes + at) : rd32(bytes + at);
    return ACPI_OK;
}

static int iso_seen(const struct acpi_topology *t, cpu_u8 bus, cpu_u8 source)
{
    for (unsigned int i = 0; i < t->iso_count; ++i)
        if (t->isos[i].bus == bus && t->isos[i].source == source) return 1;
    return 0;
}

enum acpi_result acpi_parse_madt(const cpu_u8 *bytes, cpu_u64 length,
                                 struct acpi_topology *out)
{
    if (!bytes || !out) return ACPI_INVALID;
    cpu_u32 len = 0;
    enum acpi_result r = check_header(bytes, length, "APIC", &len);
    if (r != ACPI_OK) return r;
    if (len < 44) return ACPI_LENGTH;
    out->lapic_base = rd32(bytes + 36);
    out->lapic_override = 0;
    out->madt_flags = rd32(bytes + 40);
    out->cpu_count = 0;
    out->ioapic_count = 0;
    out->iso_count = 0;
    out->nmi_count = 0;
    out->skipped_records = 0;
    out->duplicate_isos = 0;
    cpu_u64 off = 44;
    while (off < len) {
        if (off + 2 > len) return ACPI_LENGTH;
        cpu_u8 type = bytes[off];
        cpu_u8 rl = bytes[off + 1];
        /* Zero or under-header lengths can never advance: reject, never loop. */
        if (rl < 2 || off + rl > len) return ACPI_LENGTH;
        switch (type) {
        case 0: {
            if (rl < 8) return ACPI_LENGTH;
            if (out->cpu_count >= ACPI_MAX_CPUS) return ACPI_CAPACITY;
            struct acpi_cpu *c = &out->cpus[out->cpu_count++];
            c->uid = bytes[off + 2];
            c->apic_id = bytes[off + 3];
            c->flags = rd32(bytes + off + 4);
            break;
        }
        case 1: {
            if (rl < 12) return ACPI_LENGTH;
            if (out->ioapic_count >= ACPI_MAX_IOAPICS) return ACPI_CAPACITY;
            struct acpi_ioapic *io = &out->ioapics[out->ioapic_count++];
            io->id = bytes[off + 2];
            io->base = rd32(bytes + off + 4);
            io->gsi_base = rd32(bytes + off + 8);
            break;
        }
        case 2: {
            if (rl < 10) return ACPI_LENGTH;
            if (iso_seen(out, bytes[off + 2], bytes[off + 3])) {
                /* First override wins; later duplicates are ignored loudly. */
                ++out->duplicate_isos;
                break;
            }
            if (out->iso_count >= ACPI_MAX_ISOS) return ACPI_CAPACITY;
            struct acpi_iso *iso = &out->isos[out->iso_count++];
            iso->bus = bytes[off + 2];
            iso->source = bytes[off + 3];
            iso->gsi = rd32(bytes + off + 4);
            iso->flags = rd16(bytes + off + 8);
            break;
        }
        case 4: {
            if (rl < 6) return ACPI_LENGTH;
            if (out->nmi_count >= ACPI_MAX_NMIS) return ACPI_CAPACITY;
            struct acpi_nmi *nmi = &out->nmis[out->nmi_count++];
            nmi->processor = bytes[off + 2];
            nmi->flags = rd16(bytes + off + 3);
            nmi->lint = bytes[off + 5];
            break;
        }
        case 5: {
            /* Type 5 is 12 bytes: type, length, 2 reserved, 8 address. */
            if (rl < 12) return ACPI_LENGTH;
            if (!out->lapic_override) {
                out->lapic_base = rd64(bytes + off + 4);
                out->lapic_override = 1;
            } else {
                ++out->skipped_records;
            }
            break;
        }
        case 9: {
            /* Processor Local x2APIC: same CPU slot, full 32-bit IDs. */
            if (rl < 16) return ACPI_LENGTH;
            if (out->cpu_count >= ACPI_MAX_CPUS) return ACPI_CAPACITY;
            struct acpi_cpu *c = &out->cpus[out->cpu_count++];
            c->apic_id = rd32(bytes + off + 4);
            c->flags = rd32(bytes + off + 8);
            c->uid = rd32(bytes + off + 12);
            break;
        }
        default:
            /* Types 3, 6, 7, 8, 10+ and vendor records: validated for
               framing above, then deliberately skipped. */
            ++out->skipped_records;
            break;
        }
        off += rl;
    }
    return ACPI_OK;
}

const struct acpi_topology *acpi_topology(void) { return &topology; }
const char *acpi_error(void) { return error; }
int acpi_check(void)
{
    return discovered && topology.present && topology.ioapic_count &&
           topology.ioapic_count <= ACPI_MAX_IOAPICS &&
           topology.cpu_count <= ACPI_MAX_CPUS && topology.iso_count <= ACPI_MAX_ISOS &&
           topology.nmi_count <= ACPI_MAX_NMIS && topology.lapic_base != 0;
}

static void unmap_transient(void)
{
    if (!transient_mapped) return;
    (void)vm_unmap_device(vm_kernel_space(), ACPI_TRANSIENT_VA, ACPI_TRANSIENT_PAGES);
    transient_mapped = 0;
    transient_base = 0;
}

static void unmap_persistent(void)
{
    if (!persist_used) return;
    (void)vm_unmap_device(vm_kernel_space(), ACPI_PERSISTENT_VA, persist_used);
    persist_used = 0;
}

static void fail(const char *reason)
{
    error = reason;
    unmap_transient();
    unmap_persistent();
    busy = 0;
}

/* Map [phys, phys+need) through the transient window; *va points at phys.
   need <= 2 pages by construction (header probes and RSDP candidates).
   Sequential candidates usually share a page: remap only when the base
   page changes, so the ROM scan costs ~129 maps instead of ~8K. */
static enum acpi_result map_transient(cpu_u64 phys, cpu_u64 need, cpu_u8 **va)
{
    if (!va) return ACPI_INVALID;
    if (need < 1 || need > ACPI_TRANSIENT_PAGES * VM_PAGE_SIZE) return ACPI_LENGTH;
    if (phys > (cpu_u64)-1 - need) return ACPI_OVERFLOW;
    cpu_u64 base = phys & ~(VM_PAGE_SIZE - 1);
    cpu_u64 off = phys - base;
    if (off + need > ACPI_TRANSIENT_PAGES * VM_PAGE_SIZE) return ACPI_LENGTH;
    if (transient_mapped && base == transient_base) {
        *va = (cpu_u8 *)(ACPI_TRANSIENT_VA + off);
        return ACPI_OK;
    }
    unmap_transient();
    if (vm_map_firmware(vm_kernel_space(), ACPI_TRANSIENT_VA, base,
                        ACPI_TRANSIENT_PAGES, 0) != VM_OK)
        return ACPI_MAP;
    transient_mapped = 1;
    transient_base = base;
    *va = (cpu_u8 *)(ACPI_TRANSIENT_VA + off);
    return ACPI_OK;
}

/* Map a whole SDT persistently (header probe first, then the full extent). */
static enum acpi_result map_table(cpu_u64 phys, cpu_u8 **va, cpu_u32 *length)
{
    if (phys > (cpu_u64)-1 - 36) return ACPI_OVERFLOW;
    cpu_u8 *probe = 0;
    if (map_transient(phys, 36, &probe) != ACPI_OK) return ACPI_MAP;
    /* Length comes from unvalidated firmware: bound it before trusting it. */
    cpu_u8 header[36];
    for (unsigned int i = 0; i < 36; ++i) header[i] = probe[i];
    unmap_transient();
    cpu_u32 len = rd32(header + 4);
    if (len < 36 || len > ACPI_TABLE_MAX_PAGES * VM_PAGE_SIZE) return ACPI_LENGTH;
    cpu_u64 base = phys & ~(VM_PAGE_SIZE - 1);
    cpu_u64 off = phys - base;
    cpu_u64 pages = (off + len + VM_PAGE_SIZE - 1) / VM_PAGE_SIZE;
    if (pages > ACPI_TABLE_MAX_PAGES + 1) return ACPI_LENGTH;
    if (persist_used + pages > ACPI_PERSISTENT_PAGES) return ACPI_CAPACITY;
    cpu_u64 at = ACPI_PERSISTENT_VA + persist_used * VM_PAGE_SIZE;
    if (vm_map_firmware(vm_kernel_space(), at, base, pages, 0) != VM_OK) return ACPI_MAP;
    persist_used += pages;
    *va = (cpu_u8 *)(at + off);
    *length = len;
    return ACPI_OK;
}

/* Try one 16-byte-aligned RSDP candidate; leaves the transient window clean.
   The first signature match wins the whole scan: a corrupt first hit fails
   closed rather than hunting past firmware corruption for a second RSDP. */
static enum acpi_result try_rsdp(cpu_u64 phys, cpu_u32 *revision, int *have_xsdt,
                                 cpu_u64 *rsdt, cpu_u64 *xsdt)
{
    cpu_u8 *va = 0;
    /* Worst case: candidate at a page tail needs bytes from the next page;
       the 2-page window covers a 4KB RSDP from any offset (8192 - 4095). */
    if (map_transient(phys, 36, &va) != ACPI_OK) return ACPI_MAP;
    cpu_u64 avail = ACPI_TRANSIENT_PAGES * VM_PAGE_SIZE - (phys & (VM_PAGE_SIZE - 1));
    enum acpi_result r;
    if (!sig_eq(va, "RSD PTR ", 8)) {
        r = ACPI_NOT_FOUND;
    } else {
        /* Parse in place: every parser access is bounded by avail, so no
           firmware length can escape the mapped window. */
        r = acpi_parse_rsdp(va, avail, revision, have_xsdt, rsdt, xsdt);
    }
    /* Window stays mapped for the next candidate (map_transient remaps
       only on a page change); scan_range releases it at the end. */
    return r;
}

static enum acpi_result scan_range(cpu_u64 start, cpu_u64 length, cpu_u32 *revision,
                                   int *have_xsdt, cpu_u64 *rsdt, cpu_u64 *xsdt)
{
    if (length % 16 || start % 16 || length > 0x20000) return ACPI_INVALID;
    for (cpu_u64 off = 0; off < length; off += 16) {
        enum acpi_result r = try_rsdp(start + off, revision, have_xsdt, rsdt, xsdt);
        if (r == ACPI_OK) { unmap_transient(); return ACPI_OK; }
        if (r != ACPI_NOT_FOUND && r != ACPI_MAP) { unmap_transient(); return r; }
        /* ACPI_MAP (unmappable hole mid-range) skips the candidate: a real
           RSDP lives in firmware memory and is always mappable. */
    }
    unmap_transient();
    return ACPI_NOT_FOUND;
}

static enum acpi_result find_rsdp(cpu_u32 *revision, int *have_xsdt,
                                  cpu_u64 *rsdt, cpu_u64 *xsdt)
{
    /* EBDA pointer at 0x40E first (architectural order), then the BIOS ROM
       area. A malformed EBDA pointer skips EBDA, never fails discovery. */
    cpu_u8 *low = 0;
    if (map_transient(0x400, 16, &low) == ACPI_OK) {
        cpu_u16 seg = rd16(low + 0x0e);
        unmap_transient();
        cpu_u64 ebda = (cpu_u64)seg << 4;
        if (seg && ebda + 1024 <= 0xa0000) {
            enum acpi_result r = scan_range(ebda, 1024, revision, have_xsdt, rsdt, xsdt);
            if (r == ACPI_OK) return ACPI_OK;
            if (r != ACPI_NOT_FOUND && r != ACPI_MAP) return r;
        }
    } else {
        unmap_transient();
    }
    return scan_range(0xe0000, 0x20000, revision, have_xsdt, rsdt, xsdt);
}

enum acpi_result acpi_discover(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) return ACPI_CONTEXT;
    if (discovered) return ACPI_BUSY;
    if (busy) return ACPI_BUSY;
    busy = 1;
    error = "none";
    cpu_u32 revision = 0;
    int have_xsdt = 0;
    cpu_u64 rsdt = 0, xsdt = 0;
    enum acpi_result r = find_rsdp(&revision, &have_xsdt, &rsdt, &xsdt);
    if (r != ACPI_OK) {
        fail(r == ACPI_NOT_FOUND ? "rsdp_not_found" : "rsdp_invalid");
        return r;
    }
    int use_xsdt = have_xsdt;
    cpu_u64 root_phys = use_xsdt ? xsdt : rsdt;
    if (!root_phys) {
        fail("root_null");
        return ACPI_NOT_FOUND;
    }
    cpu_u8 *root = 0;
    cpu_u32 root_len = 0;
    r = map_table(root_phys, &root, &root_len);
    if (r != ACPI_OK) {
        fail("root_map");
        return r;
    }
    unsigned int entries = 0;
    r = acpi_parse_root(root, root_len, use_xsdt, &entries);
    if (r != ACPI_OK) {
        fail("root_invalid");
        return r;
    }
    topology.root_entries = entries;
    cpu_u64 madt_phys = 0;
    for (unsigned int i = 0; i < entries; ++i) {
        cpu_u64 entry = 0;
        if (acpi_root_entry(root, root_len, use_xsdt, i, &entry) != ACPI_OK) continue;
        if (!entry || entry > (cpu_u64)-1 - 36) continue;
        cpu_u8 *probe = 0;
        if (map_transient(entry, 36, &probe) != ACPI_OK) continue;
        int is_apic = sig_eq(probe, "APIC", 4);
        unmap_transient();
        if (is_apic) {
            madt_phys = entry;
            break;
        }
    }
    if (!madt_phys) {
        fail("madt_not_found");
        return ACPI_NOT_FOUND;
    }
    cpu_u8 *madt = 0;
    cpu_u32 madt_len = 0;
    r = map_table(madt_phys, &madt, &madt_len);
    if (r != ACPI_OK) {
        fail("madt_map");
        return r;
    }
    r = acpi_parse_madt(madt, madt_len, &topology);
    if (r != ACPI_OK) {
        fail("madt_invalid");
        return r;
    }
    topology.present = 1;
    topology.rsdp_revision = revision;
    topology.via_xsdt = use_xsdt;
    topology.root_phys = root_phys;
    topology.madt_phys = madt_phys;
    unmap_transient();
    busy = 0;
    discovered = 1;
    return ACPI_OK;
}
