#ifndef RYNOR_ACPI_H
#define RYNOR_ACPI_H
#include "cpu.h"
#include "vm.h"

/* INT-A1 firmware discovery: RSDP, RSDT/XSDT, MADT only. No AML, no power
   management, no PCI routing tables. All parsed data is copied into
   kernel-owned bounded structures; no firmware pointers are retained. */

enum acpi_result {
    ACPI_OK = 0,
    ACPI_NOT_READY,
    ACPI_INVALID,
    ACPI_NOT_FOUND,
    ACPI_CHECKSUM,
    ACPI_LENGTH,
    ACPI_OVERFLOW,
    ACPI_CAPACITY,
    ACPI_MAP,
    ACPI_CONTEXT,
    ACPI_BUSY
};

/* Static bounds (§83-84). Firmware offering more records fails closed. */
#define ACPI_MAX_CPUS 16
#define ACPI_MAX_IOAPICS 8
#define ACPI_MAX_ISOS 32
#define ACPI_MAX_NMIS 16
/* Largest single table mapping (MADT/XSDT/RSDT); anything bigger is rejected. */
#define ACPI_TABLE_MAX_PAGES 4
/* Root-table entries scanned while hunting the MADT; more is rejected. */
#define ACPI_ROOT_SCAN_MAX 256
/* ACPI VA window (slot 509, layout in apic.h): 2 transient scan pages plus
   8 persistent pages (one root table + MADT, 4 pages each at most). */
#define ACPI_WINDOW_PAGES 10
#define ACPI_TRANSIENT_PAGES 2
#define ACPI_PERSISTENT_PAGES 8
/* Slot-509 carve-out: 16MB above the MMIO base (past display's 3MB window,
   below the PCI window at +256MB). LAPIC/IOAPIC VAs live in apic.h. */
#define ACPI_WINDOW_VA (VM_MMIO_BASE + 0x1000000ULL)
#define ACPI_TRANSIENT_VA ACPI_WINDOW_VA
#define ACPI_PERSISTENT_VA (ACPI_WINDOW_VA + ACPI_TRANSIENT_PAGES * VM_PAGE_SIZE)

struct acpi_cpu {
    cpu_u32 uid;
    cpu_u32 apic_id;
    cpu_u32 flags;
};

struct acpi_ioapic {
    cpu_u8 id;
    cpu_u64 base;
    cpu_u32 gsi_base;
};

struct acpi_iso {
    cpu_u8 bus;
    cpu_u8 source;
    cpu_u32 gsi;
    cpu_u16 flags;
};

struct acpi_nmi {
    cpu_u8 processor;
    cpu_u16 flags;
    cpu_u8 lint;
};

struct acpi_topology {
    int present;
    cpu_u32 rsdp_revision;
    int via_xsdt;
    cpu_u64 root_phys;
    unsigned int root_entries;
    cpu_u64 madt_phys;
    cpu_u64 lapic_base;
    int lapic_override;
    cpu_u32 madt_flags;
    unsigned int cpu_count;
    unsigned int ioapic_count;
    unsigned int iso_count;
    unsigned int nmi_count;
    unsigned int skipped_records;
    unsigned int duplicate_isos;
    struct acpi_cpu cpus[ACPI_MAX_CPUS];
    struct acpi_ioapic ioapics[ACPI_MAX_IOAPICS];
    struct acpi_iso isos[ACPI_MAX_ISOS];
    struct acpi_nmi nmis[ACPI_MAX_NMIS];
};

/* Discover firmware tables once (IF=0, foreground, after PMM+VM). Maps the
   RSDP scan area transiently and keeps validated RSDT/XSDT/MADT pages mapped
   in the bounded ACPI window. Safe to call once; second call is ACPI_BUSY.
   Failure leaves no mappings behind except possibly one window page table. */
enum acpi_result acpi_discover(void);
const struct acpi_topology *acpi_topology(void);
int acpi_check(void);
const char *acpi_error(void);

/* Pure parsers over caller-supplied bytes (no mapping, no statics): shared by
   discovery and the synthetic adversarial fixtures. out may be partially
   written on failure; callers must not use it then. */
enum acpi_result acpi_parse_rsdp(const cpu_u8 *bytes, cpu_u64 length,
                                 cpu_u32 *revision, int *have_xsdt,
                                 cpu_u64 *rsdt, cpu_u64 *xsdt);
enum acpi_result acpi_parse_root(const cpu_u8 *bytes, cpu_u64 length, int xsdt,
                                 unsigned int *entries);
/* Entry accessor with bounds checks (pure; discovery maps each candidate). */
enum acpi_result acpi_root_entry(const cpu_u8 *bytes, cpu_u64 length, int xsdt,
                                 unsigned int index, cpu_u64 *phys);
enum acpi_result acpi_parse_madt(const cpu_u8 *bytes, cpu_u64 length,
                                 struct acpi_topology *out);
/* Byte-sum checksum over length bytes (length 0 is invalid). */
int acpi_checksum_ok(const cpu_u8 *bytes, cpu_u64 length);
/* Synthetic parser fixtures (no hardware); count pinned by the host. */
void acpi_test_synthetic(void);
unsigned int acpi_test_cases(void);
#endif
