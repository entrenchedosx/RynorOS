#ifndef RYNOR_APIC_H
#define RYNOR_APIC_H
#include "acpi.h"
#include "cpu.h"
#include "irq.h"

/* INT-A1 interrupt core: LAPIC + IOAPIC + vector allocator + route table.
   Legacy ISA IRQs keep vectors 32-47 on both backends; the dynamic pool
   48-127 is MSI-owned since INT-A2. No SMP startup. */

enum apic_result {
    APIC_OK = 0,
    APIC_NOT_READY,
    APIC_INVALID,
    APIC_NOT_FOUND,
    APIC_MAP,
    APIC_HW,
    APIC_CONTEXT,
    APIC_BUSY,
    APIC_EXHAUSTED,
    APIC_STATE
};

/* IDT vector map (see cpu.c, user.c gate policy). */
#define APIC_VECTOR_IRQ_BASE 32
#define APIC_VECTOR_IRQ_END 47
#define APIC_VECTOR_DYNAMIC_BASE 48
#define APIC_VECTOR_DYNAMIC_END 127
#define APIC_VECTOR_SYSCALL 128
#define APIC_VECTOR_SPURIOUS 255
#define APIC_DYNAMIC_ROUTES 32

/* Slot-509 carve-outs past the 10-page ACPI window (PCI window at +256MB). */
#define APIC_LAPIC_VA (ACPI_WINDOW_VA + ACPI_WINDOW_PAGES * VM_PAGE_SIZE)
#define APIC_IOAPIC_VA(i) (APIC_LAPIC_VA + VM_PAGE_SIZE + (cpu_u64)(i) * VM_PAGE_SIZE)

/* LAPIC register offsets (xAPIC MMIO). */
#define LAPIC_ID 0x20
#define LAPIC_VERSION 0x30
#define LAPIC_TPR 0x80
#define LAPIC_EOI 0xb0
#define LAPIC_SVR 0xf0
#define LAPIC_ISR_BASE 0x100
#define LAPIC_ICR_LO 0x300
#define LAPIC_ICR_HI 0x310
/* ICR shorthands reserved for future IPIs (INT-A1 never sends). */
#define LAPIC_ICR_DST_SELF 0x40000
#define LAPIC_ICR_DST_ALL 0x80000
#define LAPIC_ICR_DST_OTHERS 0xc0000

/* IOAPIC register window + indexed registers. */
#define IOAPIC_SEL 0x00
#define IOAPIC_WIN 0x10
#define IOAPIC_ID 0x00
#define IOAPIC_VER 0x01
#define IOAPIC_REDIR_BASE 0x10

/* Legacy chipset: i440FX/PIIX3 IMCR select/data (readback-verified). */
#define IMCR_SELECT 0x22
#define IMCR_DATA 0x23
#define IMCR_REG 0x70
#define IMCR_APIC_MODE 0x01

/* LAPIC control (xAPIC MMIO only; x2APIC is rejected at init). */
enum apic_result apic_lapic_init(void);
cpu_u32 apic_bsp_id(void);
cpu_u64 apic_lapic_base(void);
void apic_lapic_eoi(void);
int apic_lapic_isr_set(unsigned int vector);
cpu_u32 apic_lapic_version(unsigned int *max_lvt);

/* IOAPIC control. max_redir comes from IOAPICVER, never hardcoded. */
enum apic_result apic_ioapic_init(void);
unsigned int apic_ioapic_count(void);
unsigned int apic_ioapic_max(cpu_u32 index);
enum apic_result apic_gsi_owner(cpu_u32 gsi, unsigned int *index, unsigned int *pin);
/* level/low are 0/1; mask nonzero keeps the entry masked. */
enum apic_result apic_program_route(unsigned int ioapic, unsigned int pin,
                                    unsigned int vector, unsigned int dest,
                                    int level, int low, int mask);
enum apic_result apic_read_route(unsigned int ioapic, unsigned int pin,
                                 unsigned int *vector, unsigned int *dest,
                                 int *level, int *low, int *mask);
enum apic_result apic_set_route_mask(unsigned int ioapic, unsigned int pin, int mask);

/* Vector allocator: 48-127 first-fit; 0-47/128-255 have fixed owners.
   States: 0 free, 1 reserved-static, 2 IRQ-owned. init() is idempotent and
   resets the pool; call only during init/self-test, never after activation. */
void apic_vector_init(void);
int apic_vector_alloc(void);
/* INT-A2: first-fit n-contiguous n-aligned block (n a power of two,
   1..32); the base is returned, or -1 when no aligned block fits.
   Same IF=0/foreground guards as apic_vector_alloc. Multi-vector MSI
   delivery replaces DATA low bits (QEMU msi_prepare_message), so the
   block must be aligned for the device's vectors to land on ours. */
int apic_vector_alloc_aligned(unsigned int n);
enum apic_result apic_vector_claim(unsigned int vector, unsigned int owner);
enum apic_result apic_vector_release(unsigned int vector);
unsigned int apic_vector_owner(unsigned int vector);
unsigned int apic_vector_state(unsigned int vector);
unsigned int apic_vector_free(void);
/* Test-only snapshot restore (synthetic fixtures run on live state). */
void apic_vector_restore(const cpu_u8 *state, const unsigned int *owner);

/* ISA IRQ to GSI with source-override lookup and ISA trigger/polarity
   defaults (edge/high when the override says conforms). Non-ISA buses
   never match: without PCI routing data they cannot route. */
enum apic_result apic_irq_gsi(unsigned int irq, cpu_u32 *gsi, int *level, int *low);

/* Route table (owned here; irq.c dispatches through it). */
#define APIC_ROUTE_NONE 0
#define APIC_ROUTE_MSI 1
#define APIC_ROUTE_MSIX 2
struct apic_route {
    int used;
    int legacy;
    unsigned int irq;
    cpu_u32 gsi;
    unsigned int vector;
    unsigned int ioapic;
    unsigned int pin;
    int level;
    int low;
    int masked;
    int programmed;
    irq_handler handler;
    void *opaque;
    /* INT-A2: MSI/MSI-X routes (0 for legacy/GSI paths). The
       dispatcher masks quiet-by-kind; msi.c owns device state. */
    unsigned int msi;
    cpu_u32 msi_bdf; /* bus<<16 | dev<<8 | fn */
    unsigned int msi_index;
};
enum apic_result apic_route_register(unsigned int irq, irq_handler handler, void *opaque);
enum apic_result apic_route_set_handler(unsigned int irq, irq_handler handler, void *opaque);
enum apic_result apic_route_set_masked(unsigned int irq, int masked);
struct apic_route *apic_route_for_vector(unsigned int vector);
struct apic_route *apic_route_for_irq(unsigned int irq);
enum apic_result apic_route_gsi_register(cpu_u32 gsi, int level, int low,
                                           irq_handler handler, void *opaque,
                                           unsigned int *vector);
enum apic_result apic_route_gsi_unregister(unsigned int vector);
/* INT-A2: MSI/MSI-X route over a caller-allocated vector. kind is
   APIC_ROUTE_MSI/MSIX; bdf packs bus/dev/fn; index is the device
   vector. The device must already be programmed and masked (msi.c
   owns that order); unregister skips the IOAPIC mask arm for these
   routes and refuses kind mismatches. */
enum apic_result apic_route_msi_register(unsigned int kind, cpu_u32 bdf,
                                          unsigned int index,
                                          unsigned int vector,
                                          irq_handler handler, void *opaque);
enum apic_result apic_route_msi_unregister(unsigned int vector);

/* Pure cores shared by the live wrappers and the synthetic fixtures. */
enum apic_result apic_gsi_owner_at(cpu_u32 gsi, const cpu_u32 *bases,
                                   const unsigned int *maxes, unsigned int n,
                                   unsigned int *index, unsigned int *pin);
int apic_ranges_overlap(const cpu_u32 *bases, const unsigned int *maxes, unsigned int n);
enum apic_result apic_irq_gsi_at(unsigned int irq, const struct acpi_iso *isos,
                                 unsigned int count, cpu_u32 *gsi, int *level, int *low);
void apic_redir_bits(unsigned int vector, unsigned int dest, int level, int low, int mask,
                     cpu_u32 *lo, cpu_u32 *hi);
/* Synthetic core fixtures (no hardware); count pinned by the host. */
void apic_test_synthetic(void);
unsigned int apic_test_cases(void);

/* IRQ-context kernel-CR3 guard for MMIO (xHCI-A1: shared with the xHCI
   handler for controller MMIO + event-ring access on CPL3 frames).
   Returns the previous CR3; exit restores it. No-op on kernel CR3. */
cpu_u64 apic_mmio_enter(void);
void apic_mmio_exit(cpu_u64 saved);

/* Backend switch + self-test driver. */
enum apic_result apic_activate(void);
int apic_active(void);
void apic_note_vector(unsigned int vector);
cpu_u64 apic_vector_count(unsigned int vector);
void apic_self_test(void);
int apic_check(void);
const char *apic_error(void);
#endif
