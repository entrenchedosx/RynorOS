#ifndef RYNOR_PCI_H
#define RYNOR_PCI_H
#include "cpu.h"
#include "vm.h"

/* PCI-A1: PCI discovery + BAR resource mapping (internal kernel API).
 *
 * Configuration Mechanism #1 (0xCF8/0xCFC) is the bootstrap transport
 * on the pc-i440fx QEMU target (no ACPI/MCFG here yet); all config
 * access funnels through pci_cfg_read/write so a future ECAM transport
 * or test mock can replace the backend without touching enumeration.
 * No PCI surface reaches CPL3 in this slice (see docs/design/pci.md).
 */

/* Standard 256-byte configuration header offsets. */
#define PCI_CFG_VENDOR_ID 0x00u
#define PCI_CFG_DEVICE_ID 0x02u
#define PCI_CFG_COMMAND 0x04u
#define PCI_CFG_STATUS 0x06u
#define PCI_CFG_REVISION 0x08u
#define PCI_CFG_PROG_IF 0x09u
#define PCI_CFG_SUBCLASS 0x0Au
#define PCI_CFG_CLASS 0x0Bu
#define PCI_CFG_HEADER_TYPE 0x0Eu
#define PCI_CFG_BAR0 0x10u
#define PCI_CFG_ROM_BAR 0x30u
#define PCI_CFG_BRIDGE_PRIMARY 0x18u
#define PCI_CFG_BRIDGE_SECONDARY 0x19u
#define PCI_CFG_BRIDGE_SUBORDINATE 0x1Au
#define PCI_CFG_IRQ_LINE 0x3Cu
#define PCI_CFG_IRQ_PIN 0x3Du
#define PCI_CFG_SPACE_BYTES 256u

#define PCI_VENDOR_ABSENT 0xFFFFu
#define PCI_COMMAND_IO 0x0001u
#define PCI_COMMAND_MEM 0x0002u
#define PCI_COMMAND_BUS_MASTER 0x0004u
/* PCI-A1 never sets BUS_MASTER and never enables decoding globally;
   BAR sizing clears IO+MEM temporarily and restores COMMAND exactly. */

#define PCI_MAX_BUS 255u
#define PCI_MAX_DEVICE 31u
#define PCI_MAX_FUNCTION 7u
#define PCI_MAX_DEVICES 16u
/* Config BAR slots scanned per endpoint (bridges scan
   PCI_MAX_BRIDGE_BARS); decoded entries stored per device. Slots
   beyond the entry bound are never decoded (documented truncation:
   4 entries cover every QEMU device; extras stay unmapped). */
#define PCI_MAX_BAR_SLOTS 6u
#define PCI_MAX_BAR_ENTRIES 4u
#define PCI_MAX_BRIDGE_BARS 2u
#define PCI_MAX_BUSES 32u
#define PCI_MATCH_ANY 0xFFFFFFFFu

/* MMIO virtual space carve-up inside VMM slot 509: the display owns
   [VM_MMIO_BASE, +256MiB) for the framebuffer aperture (its maximum
   validated size); PCI mappings start above it and bump upward. */
#define PCI_MMIO_VA_BASE (VM_MMIO_BASE + 0x10000000ULL)

enum pci_result {
    PCI_OK = 0,
    PCI_INVALID,     /* bad argument (BDF/offset/index out of range) */
    PCI_ABSENT,      /* no device/function at this address */
    PCI_FULL,        /* registry, bus, or VA budget exhausted */
    PCI_UNSUPPORTED, /* header layout / ROM / I/O-as-MMIO: refused */
    PCI_RANGE,       /* base+size wrap, zero size, or misalignment */
    PCI_VM_ERROR     /* underlying vm_map/unmap/query failed */
};

enum pci_bar_kind {
    PCI_BAR_NONE = 0,
    PCI_BAR_IO = 1,
    PCI_BAR_MMIO32 = 2,
    PCI_BAR_MMIO64 = 3
};

struct pci_bar {
    cpu_u8 index;        /* config BAR slot 0..5 (a 64-bit BAR occupies
                            index and index+1 but is ONE entry) */
    cpu_u8 kind;         /* enum pci_bar_kind */
    cpu_u8 prefetchable; /* memory BARs only */
    cpu_u8 is64;
    cpu_u32 raw_low;     /* firmware value, preserved verbatim */
    cpu_u32 raw_high;    /* 64-bit high half, else 0 */
    cpu_u64 base;        /* decoded physical base (flag bits masked) */
    cpu_u64 size;        /* probed bytes, 0 when unimplemented */
    cpu_u64 mapped_va;   /* adjusted virtual base, 0 when unmapped */
};

struct pci_device {
    cpu_u32 bus;
    cpu_u32 device;
    cpu_u32 function;
    cpu_u16 vendor_id;
    cpu_u16 device_id;
    cpu_u8 class_code;
    cpu_u8 subclass;
    cpu_u8 prog_if;
    cpu_u8 revision;
    cpu_u8 header_type;   /* low 7 bits (MF bit reported separately) */
    cpu_u8 multifunction; /* header/function-0 MF bit */
    cpu_u16 command;      /* enumeration-time value */
    cpu_u16 status;
    cpu_u8 irq_line;
    cpu_u8 irq_pin;
    cpu_u8 header_layout; /* 0 endpoint, 1 bridge, else raw value */
    cpu_u8 bar_count;     /* valid entries in bars[] (<= 4) */
    cpu_u8 sec_bus;       /* bridge secondary (layout 1 only) */
    cpu_u8 sub_bus;       /* bridge subordinate (layout 1 only) */
    struct pci_bar bars[PCI_MAX_BAR_ENTRIES];
};

/* Transport backend: dword-granular config access. Implementations
   receive validated bus/device/function/offset (offset dword-aligned
   and < 256). Reads return the dword; writes publish it. */
struct pci_cfg_ops {
    cpu_u32 (*read)(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off);
    void (*write)(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                  cpu_u32 val);
};

/* Byte/word/dword config access with full validation. Out-of-range
   BDF or offset reads fail closed to all-ones (absent-device
   semantics); invalid writes are ignored. Single-CPU safe from any
   IRQ state: the legacy backend serializes the CF8/CFC pair. */
cpu_u8 pci_cfg_read8(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off);
cpu_u16 pci_cfg_read16(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off);
cpu_u32 pci_cfg_read32(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off);
void pci_cfg_write8(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                    cpu_u8 val);
void pci_cfg_write16(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                     cpu_u16 val);
void pci_cfg_write32(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                     cpu_u32 val);
/* Install a test/mock backend (pci-test.c only); NULL restores the
   legacy CF8/CFC backend. Never called by production code. */
void pci_cfg_install_ops(const struct pci_cfg_ops *ops);

/* Silent full scan into the static registry; idempotent (rebuilds
   from scratch). No transcript output: safe on every boot. Prior
   mapped_va records are cleared (callers unmap before re-init). */
enum pci_result pci_initialize(void);
int pci_initialized(void);
cpu_u32 pci_device_count(void);
/* Borrowed pointers into the registry; invalidated by the next
   pci_initialize. NULL when out of range / no match. */
const struct pci_device *pci_device_at(cpu_u32 index);
const struct pci_device *pci_find_bdf(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn);
const struct pci_device *pci_find_vendor_device(cpu_u16 vendor,
                                                cpu_u16 device,
                                                cpu_u32 start);
const struct pci_device *pci_find_class(cpu_u32 class_code,
                                        cpu_u32 subclass, cpu_u32 prog_if,
                                        cpu_u32 start);
/* Each class field accepts PCI_MATCH_ANY as a wildcard. */

/* Map one decoded MMIO BAR through vm_map_device (supervisor UC).
   bar_entry is the decoded-entry ordinal (0..bar_count), NOT the raw
   config slot. I/O BARs and unimplemented entries are refused. On
   success *va_out is the page-offset-adjusted virtual base and the
   mapping is recorded (repeat maps return the stored address). */
enum pci_result pci_map_bar(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                            cpu_u32 bar_entry, cpu_u64 *va_out);
enum pci_result pci_unmap_bar(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                              cpu_u32 bar_entry);

struct pci_stats {
    cpu_u64 cfg_reads;
    cpu_u64 cfg_writes;
    cpu_u64 buses_scanned;
    cpu_u64 functions_seen;
    cpu_u64 bars_sized;
    cpu_u64 registry_bytes;
};
void pci_stats_read(struct pci_stats *out);

/* Diagnostic class name ("network", "display", ...); "unknown" for
   anything unlisted. Unknown classes are still valid devices. */
const char *pci_class_name(cpu_u8 class_code, cpu_u8 subclass);

/* Gated self-test (RYNOR_PCI_TEST images only): mock-transport unit
   rows, synthetic topology rows, live-hardware evidence rows, and the
   "[PCI] pci verified" terminator. Silent builds never call it. */
void pci_self_test(void);

#endif
