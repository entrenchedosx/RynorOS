#ifndef RYNOR_MSI_H
#define RYNOR_MSI_H
#include "cpu.h"
#include "irq.h"
#include "pci.h"
#include "apic.h"

/* INT-A2: PCI capability walker + MSI/MSI-X transports + generic PCI
 * IRQ API. No CPL3 surface; foreground-only (IF=0, never in IRQ
 * context) except msi_quiet_route, which is IRQ-safe by construction
 * (config RMW through the serialized backend, MMIO through cached
 * table VAs, never maps, never prints, never halts).
 *
 * Layout authority: docs/design/msi.md §1 (Linux pci_regs.h, QEMU
 * msi.c/msix.h/apic-msidef.h/apic.c). Bit positions below are frozen
 * from those sources, not from memory.
 */

/* Capability IDs + list head (offsets into 256-byte config space). */
#define MSI_CAP_ID_MSI 0x05u
#define MSI_CAP_ID_MSIX 0x11u

/* MSI capability, offsets relative to the capability base. */
#define MSI_MSG_CTRL 0x02u
#define MSI_ADDR_LO 0x04u
#define MSI_ADDR_HI 0x08u
#define MSI_DATA_32 0x08u
#define MSI_MASK_32 0x0Cu
#define MSI_PENDING_32 0x10u
#define MSI_DATA_64 0x0Cu
#define MSI_MASK_64 0x10u
#define MSI_PENDING_64 0x14u
/* MSG_CTRL bits (Linux PCI_MSI_FLAGS_*). */
#define MSI_CTRL_ENABLE 0x0001u
#define MSI_CTRL_QMASK 0x000Eu
#define MSI_CTRL_QSIZE 0x0070u
#define MSI_CTRL_64BIT 0x0080u
#define MSI_CTRL_MASKBIT 0x0100u
#define MSI_VECTORS_MAX 32u

/* MSI-X capability, offsets relative to the capability base. */
#define MSIX_MSG_CTRL 0x02u
#define MSIX_TABLE_REG 0x04u
#define MSIX_PBA_REG 0x08u
/* MSG_CTRL bits (Linux PCI_MSIX_FLAGS_*). */
#define MSIX_CTRL_QSIZE 0x07FFu
#define MSIX_CTRL_MASKALL 0x4000u
#define MSIX_CTRL_ENABLE 0x8000u
/* TABLE/PBA register split (Linux PCI_MSIX_TABLE_*). */
#define MSIX_REG_BIR 0x00000007u
#define MSIX_REG_OFFSET 0xFFFFFFF8u
/* Table entry (Linux PCI_MSIX_ENTRY_*). */
#define MSIX_ENTRY_SIZE 16u
#define MSIX_ENTRY_ADDR 0x0u
#define MSIX_ENTRY_ADDR_HI 0x4u
#define MSIX_ENTRY_DATA 0x8u
#define MSIX_ENTRY_CTRL 0xCu
#define MSIX_ENTRY_MASK 0x1u
#define MSIX_VECTORS_MAX 2048u

/* x86 message (xAPIC physical fixed edge; Linux __irq_msi_compose_msg). */
#define MSI_ADDR_BASE 0xFEE00000u
#define MSI_ADDR_DEST_SHIFT 12u
#define MSI_DATA_VECTOR_MASK 0xFFu

/* Static bounds: 8 live handles, 32 vectors each (covers every QEMU
   device: xhci 16, e1000e 5, nvme parses but 65 exceeds one handle —
   drivers request subsets). */
#define MSI_MAX_HANDLES 8u
#define MSI_MAX_VECTORS 32u

enum msi_result {
    MSI_OK = 0,
    MSI_INVALID,  /* bad argument (BDF/handle/index/count out of range) */
    MSI_ABSENT,   /* no such capability on this function */
    MSI_REFUSED,  /* policy: alignment, MEM-disabled, APIC ID, overlap */
    MSI_NOMEM,    /* vectors, routes, handles, or table space exhausted */
    MSI_HW,       /* readback mismatch after a programmed write */
    MSI_RANGE,    /* table/PBA bounds, wrap, or cap-length overflow */
    MSI_STATE     /* double enable/disable, both-caps, stale handle */
};

enum msi_kind {
    MSI_KIND_MSI = 1,
    MSI_KIND_MSIX = 2
};

/* Walker output. malformed==0 means the list terminated cleanly
   (next==0); otherwise it names the first defect and the offsets
   below still report every MSI/MSI-X cap found before it. */
struct msi_walk {
    int has_msi;
    int has_msix;
    cpu_u8 msi_off;
    cpu_u8 msix_off;
    cpu_u8 malformed; /* 0 none, 1 no-cap-list, 2 ptr-low, 3 ptr-high,
                         4 misaligned, 5 cycle, 6 ttl */
};

struct msi_desc {
    cpu_u8 cap;
    cpu_u8 mmc;      /* log2(max vectors), 0..5 */
    cpu_u8 mme;      /* log2(requested vectors), live value */
    cpu_u8 is64;
    cpu_u8 maskbit;
    cpu_u8 enabled;
    cpu_u8 len;      /* capability bytes: 10/14/20/24 */
};

struct msix_geo {
    cpu_u8 cap;
    cpu_u16 nvec;    /* table size = QSIZE+1, 1..2048 */
    cpu_u8 tbl_bir;
    cpu_u8 pba_bir;
    cpu_u32 tbl_off;
    cpu_u32 pba_off;
    cpu_u8 enabled;
    cpu_u8 masked_all;
    cpu_u8 tbl_entry; /* decoded-entry ordinal for the table BAR */
    cpu_u8 pba_entry; /* decoded-entry ordinal for the PBA BAR */
};

struct msi_msg {
    cpu_u32 addr;
    cpu_u32 hi;
    cpu_u32 data;
};

struct msi_handle_info {
    cpu_u32 kind;    /* enum msi_kind */
    cpu_u32 bus;
    cpu_u32 dev;
    cpu_u32 fn;
    cpu_u32 nvec;
    unsigned int vector[MSI_MAX_VECTORS];
};

/* Capability walk (§4): pure config-space read, safe on any function
   including absent ones (reads fail closed to all-ones, which decodes
   as no-cap-list, not as caps). */
enum msi_result msi_walk_caps(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                              struct msi_walk *out);
/* Parse a located capability into its descriptor/geometry. */
enum msi_result msi_parse_msi(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                              cpu_u8 cap, struct msi_desc *out);
enum msi_result msi_parse_msix(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                               cpu_u8 cap, struct msix_geo *out);
/* Pure message builder (§3.4). Refuses apic_id > 0xFF (no truncation). */
enum msi_result msi_build_message(cpu_u32 apic_id, unsigned int vector,
                                  struct msi_msg *out);

/* Generic enable: nvec power-of-two (MSI, 1..32, <= MMC) or any
   1..32 (MSI-X subset of table index 0..nvec-1). On success *handle
   names the registration and vectors[] holds the owned CPU vectors
   (MSI: contiguous and nvec-aligned; MSI-X: any free). Requires
   IF=0, foreground, APIC backend active. */
enum msi_result pci_irq_enable_msi(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                                   unsigned int nvec, irq_handler handler,
                                   void *opaque, cpu_u32 *handle,
                                   unsigned int *vectors);
enum msi_result pci_irq_enable_msix(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                                    unsigned int nvec, irq_handler handler,
                                    void *opaque, cpu_u32 *handle,
                                    unsigned int *vectors);
/* Auto: prefers MSI-X when present (transcripted, host-pinned). */
enum msi_result pci_irq_enable_auto(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                                    unsigned int nvec, irq_handler handler,
                                    void *opaque, cpu_u32 *handle,
                                    unsigned int *vectors);
/* Teardown: mask → disable → unroute → release → unmap-at-zero.
   Restores INTX_DISABLE only when this handle set it. */
enum msi_result pci_irq_disable(cpu_u32 handle);
/* Per-vector mask (MSI nomask devices refuse; MSI-X always allows). */
enum msi_result pci_irq_mask(cpu_u32 handle, unsigned int index, int mask);
enum msi_result pci_irq_info(cpu_u32 handle, struct msi_handle_info *out);

/* IRQ-context quiet for the dispatcher's unexpected-vector arm: masks
   the single vector, never maps, never fails. */
void msi_quiet_route(unsigned int kind, cpu_u32 bdf, unsigned int index);

const char *msi_error(void);

/* Gated self-test (RYNOR_MSI_TEST images only): mock-backend unit
   rows, synthetic matrices, live-hardware evidence rows, and the
   "[MSI] msi verified" terminator. Silent builds never call it. */
void msi_self_test(void);
unsigned int msi_test_cases(void);

#endif
