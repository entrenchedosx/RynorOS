/* RYNOROS xHCI-A1: first modern device driver (command + event engine).
 *
 * Production xHCI host-controller driver: class-matched PCI claim, BAR
 * mapping, capability decode, extended-capability walk, BIOS/OS legacy
 * handoff, halt/reset, DMA structures (DCBAA, scratchpads, command and
 * event rings, ERST), Interrupter 0, MSI-X through the INT-A2 pci_irq
 * API, NO-OP command lifecycle, and ordered teardown with rollback.
 *
 * Layouts frozen in docs/design/xhci.md (Intel xHCI Rev 1.2b, Linux
 * spec-derived headers, QEMU implementation under test). No USB
 * enumeration, slots, endpoints, or transfers (xHCI-A2/USB-A1). */
#ifndef RYNOR_XHCI_H
#define RYNOR_XHCI_H

#include "cpu.h"
#include "dma.h"

/* --- PCI identity (spec §5.2.2; QEMU hcd-xhci-pci.c) ---------------------- */
#define XHCI_PCI_CLASS    0x0Cu
#define XHCI_PCI_SUBCLASS 0x03u
#define XHCI_PCI_PROGIF   0x30u

/* --- Capability registers (BAR0+off; Linux xhci-caps.h) -------------------- */
#define XHCI_CAP_CAPLEN    0x00u
#define XHCI_CAP_HCIVER    0x02u
#define XHCI_CAP_HCSP1     0x04u
#define XHCI_CAP_HCSP2     0x08u
#define XHCI_CAP_HCCP1     0x10u
#define XHCI_CAP_DBOFF     0x14u
#define XHCI_CAP_RTSOFF    0x18u
#define XHCI_CAP_MINLEN    0x20u

/* --- Operational registers (op = BAR0+CAPLENGTH) --------------------------- */
#define XHCI_OP_USBCMD 0x00u
#define XHCI_OP_USBSTS 0x04u
#define XHCI_OP_PAGESZ 0x08u
#define XHCI_OP_DNCTRL 0x14u
#define XHCI_OP_CRCR   0x18u
#define XHCI_OP_DCBAA  0x30u
#define XHCI_OP_CONFIG 0x38u
#define XHCI_OP_PORTS  0x400u
#define XHCI_PORT_STRIDE 0x10u

#define XHCI_CMD_RS   (1u << 0)
#define XHCI_CMD_HCRST (1u << 1)
#define XHCI_CMD_INTE (1u << 2)
#define XHCI_STS_HCH  (1u << 0)
#define XHCI_STS_HSE  (1u << 2)
#define XHCI_STS_EINT (1u << 3)
#define XHCI_STS_PCD  (1u << 4)
#define XHCI_STS_CNR  (1u << 11)
#define XHCI_STS_HCE  (1u << 12)

#define XHCI_CRCR_RCS (1u << 0)
#define XHCI_CRCR_CS  (1u << 1)
#define XHCI_CRCR_CA  (1u << 2)
#define XHCI_CRCR_CRR (1u << 3)

/* --- Runtime registers (rt = BAR0+RTSOFF) ---------------------------------- */
#define XHCI_RT_MFINDEX 0x00u
#define XHCI_RT_INTR    0x20u
#define XHCI_INTR_STRIDE 0x20u
#define XHCI_IMAN_IP    (1u << 0)
#define XHCI_IMAN_IE    (1u << 1)
#define XHCI_ERDP_EHB   (1u << 3)

/* --- TRB (16 bytes; Linux xhci.h) ------------------------------------------ */
#define XHCI_TRB_SIZE   16u
#define XHCI_TRB_C      (1u << 0)
#define XHCI_TRB_TC     (1u << 1)
#define XHCI_TRB_TYPE_SHIFT 10
#define XHCI_TRB_TYPE_MASK  0x3Fu
#define XHCI_TRB_INTR_SHIFT 22

#define XHCI_TRB_LINK      6u
#define XHCI_TRB_NOOP_CMD  23u
#define XHCI_TRB_EV_XFER   32u
#define XHCI_TRB_EV_CMPL   33u
#define XHCI_TRB_EV_PORT   34u

/* Completion codes (status bits 31:24). */
#define XHCI_CC_SUCCESS  1u
#define XHCI_CC_TRBERR   5u
#define XHCI_CC_PARAM    17u
#define XHCI_CC_EVRFULL  21u
#define XHCI_CC_RSTOP    24u
#define XHCI_CC_ABORTED  25u
#define XHCI_CC_STOPPED  26u

/* --- Extended capabilities -------------------------------------------------- */
#define XHCI_XCAP_LEGACY 1u
#define XHCI_XCAP_SUPP   2u
#define XHCI_LEG_BIOS_OWNED (1u << 16)
#define XHCI_LEG_OS_OWNED   (1u << 24)
/* SMI-disable clear mask (Linux xhci-ext-caps.h DISABLE_SMI). */
#define XHCI_LEG_SMI_MASK   0xE0072000u

/* --- Driver bounds (§77) ----------------------------------------------------- */
#define XHCI_MAX_SLOTS_EN 8u
#define XHCI_CMD_TRBS     32u
#define XHCI_EVT_TRBS     32u
#define XHCI_TOKENS       8u
#define XHCI_MAX_SPADS    8u
#define XHCI_XCAP_TTL     64u
/* NOOP round-trips in microseconds; 1M spins still clear a ~100ms
   worst case by an order of magnitude while failing loud in seconds
   (a wedged controller must surface inside the boot harness window). */
#define XHCI_SPIN_BUDGET  1000000ULL

enum xhci_result {
    XHCI_OK = 0,
    XHCI_INVALID,
    XHCI_ABSENT,
    XHCI_REFUSED,
    XHCI_NOMEM,
    XHCI_HW,
    XHCI_RANGE,
    XHCI_STATE,
    XHCI_TIMEOUT,
    XHCI_BUSY
};

/* Fault-injection stages (§48); production always passes NONE. */
enum xhci_fail_at {
    XHCI_FAIL_NONE = 0,
    XHCI_FAIL_BAR,
    XHCI_FAIL_CAPS,
    XHCI_FAIL_LEGACY,
    XHCI_FAIL_HALT,
    XHCI_FAIL_RESET,
    XHCI_FAIL_DCBAA,
    XHCI_FAIL_SCRATCH,
    XHCI_FAIL_CMDRING,
    XHCI_FAIL_EVTRING,
    XHCI_FAIL_ERST,
    XHCI_FAIL_INTR,
    XHCI_FAIL_BME,
    XHCI_FAIL_MSIX,
    XHCI_FAIL_START
};

enum xhci_token_state {
    XHCI_TOK_FREE = 0,
    XHCI_TOK_SUBMITTED,
    XHCI_TOK_COMPLETED,
    XHCI_TOK_ERROR
};

struct xhci_trb {
    cpu_u32 param_lo, param_hi, status, control;
};

struct xhci_caps {
    cpu_u8 caplen;
    cpu_u16 hciver;
    cpu_u8 slots;
    cpu_u16 intrs;
    cpu_u16 ports;
    cpu_u16 spads;
    cpu_u8 csz, ac64;
    cpu_u16 xecp;
    cpu_u32 dboff, rtsoff, pagesz;
    cpu_u16 vendor, device;
    cpu_u8 class_, subclass, progif, rev;
};

struct xhci_token {
    cpu_u8 state;
    cpu_u8 ccode;
    cpu_u16 reserved;
    cpu_u64 trb_bus;
};

struct xhci_ring {
    struct dma_buffer dma;
    cpu_u32 count;
    cpu_u32 enq;
    cpu_u8 pcs;
    cpu_u8 wraps;
};

struct xhci_ering {
    struct dma_buffer dma;
    cpu_u32 count;
    cpu_u32 deq;
    cpu_u8 ccs;
    cpu_u8 wraps;
    cpu_u64 erdp_shadow;
};

struct xhci_hcd;
/* Completion hook: invoked in IRQ context for every consumed event
   (type/cc/ptr/slot decoded). IRQ-context rules apply: no alloc, no
   prints, bounded work. */
typedef void (*xhci_event_hook)(struct xhci_hcd *h, cpu_u8 type, cpu_u8 cc,
                                cpu_u64 ptr, cpu_u8 slot, cpu_u8 ccs,
                                void *ctx);

struct xhci_hcd {
    cpu_u8 claimed;
    cpu_u8 started;
    cpu_u8 failed;
    cpu_u8 desync;
    cpu_u8 noprogress;
    cpu_u8 caps_valid;
    cpu_u8 max_slots_en;
    cpu_u8 bus, dev, fn;
    cpu_u8 bar_ord;
    cpu_u16 cmd0;
    struct xhci_caps caps;
    cpu_u64 mmio;
    cpu_u64 mmio_len;
    cpu_u64 op_base, rt_base, db_base;
    cpu_u32 irq_handle;
    cpu_u8 irq_vec;
    struct dma_buffer dcb;
    struct dma_buffer spad_arr;
    struct dma_buffer spads[XHCI_MAX_SPADS];
    struct xhci_ring cmd;
    struct xhci_ering evt;
    struct dma_buffer erst;
    struct xhci_token tokens[XHCI_TOKENS];
    cpu_u64 irq_count;
    cpu_u64 cmpl_count;
    cpu_u64 port_events;
    cpu_u64 other_events;
    cpu_u8 last_user;
    cpu_u64 last_rip;
    cpu_u8 last_isr;
    xhci_event_hook hook;
    void *hook_ctx;
};

/* Diagnostic register reads (test/transcript support; range-checked). */
enum xhci_result xhci_op_read(struct xhci_hcd *h, cpu_u32 reg, cpu_u32 *out);
enum xhci_result xhci_rt_read(struct xhci_hcd *h, cpu_u32 reg, cpu_u32 *out);
enum xhci_result xhci_bar_read(struct xhci_hcd *h, cpu_u64 off, cpu_u32 *out);

/* MMIO backend seam (direct volatile access by default; the gated
   self-test installs a scripted backend for synthetic fixtures). */
struct xhci_mmio_ops {
    cpu_u32 (*read32)(struct xhci_hcd *h, cpu_u64 off);
    void (*write32)(struct xhci_hcd *h, cpu_u64 off, cpu_u32 v);
};
void xhci_mmio_install(const struct xhci_mmio_ops *ops);
void xhci_mmio_default(void);

/* Staged bring-up (claim through start) with fault injection. */
enum xhci_result xhci_claim(struct xhci_hcd *h);
enum xhci_result xhci_map_bar(struct xhci_hcd *h);
enum xhci_result xhci_read_caps(struct xhci_hcd *h);
/* Walk extended caps; calls visit(h, id, cap_off, next, ctx) per cap
   (next in DWORDs, 0 = last). */
enum xhci_result xhci_walk_xcaps(struct xhci_hcd *h,
    enum xhci_result (*visit)(struct xhci_hcd *h, cpu_u8 id,
                              cpu_u64 off, cpu_u8 next, void *ctx),
    void *ctx);
enum xhci_result xhci_legacy_handoff(struct xhci_hcd *h);
enum xhci_result xhci_halt(struct xhci_hcd *h);
enum xhci_result xhci_reset(struct xhci_hcd *h);
enum xhci_result xhci_setup_dma(struct xhci_hcd *h);
enum xhci_result xhci_setup_interrupter(struct xhci_hcd *h);
enum xhci_result xhci_enable_bme(struct xhci_hcd *h);
enum xhci_result xhci_arm_msix(struct xhci_hcd *h);
enum xhci_result xhci_start(struct xhci_hcd *h);
enum xhci_result xhci_submit_noop(struct xhci_hcd *h, cpu_u8 *tok_out);
enum xhci_token_state xhci_token_state(struct xhci_hcd *h, cpu_u8 tok);
enum xhci_result xhci_wait_token(struct xhci_hcd *h, cpu_u8 tok);
enum xhci_result xhci_teardown(struct xhci_hcd *h);
enum xhci_result xhci_init(struct xhci_hcd *h, enum xhci_fail_at fail_at);

/* Gated self-test driver (test images only). */
void xhci_self_test(void);

/* Pure helpers (synthetic-testable without hardware). */
cpu_u32 xhci_encode_noop(struct xhci_trb *t, cpu_u8 pcs, cpu_u16 intr);
void xhci_encode_link(struct xhci_trb *t, cpu_u64 next_bus, cpu_u8 pcs);
int xhci_decode_event(const struct xhci_trb *t, cpu_u8 ccs, cpu_u8 *type,
                      cpu_u8 *cc, cpu_u64 *ptr, cpu_u8 *slot);

#endif
