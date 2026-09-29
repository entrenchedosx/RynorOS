/* RYNOROS xHCI-A1: production xHCI host-controller driver.
 * See kernel/include/xhci.h and docs/design/xhci.md. */
#include "xhci.h"
#include "apic.h"
#include "cpu.h"
#include "dma.h"
#include "irq.h"
#include "msi.h"
#include "pci.h"

typedef char xhci_trb_size_check[sizeof(struct xhci_trb) == 16 ? 1 : -1];

/* --- MMIO backend ---------------------------------------------------------- */

static cpu_u32 xhci_direct_read(struct xhci_hcd *h, cpu_u64 off)
{
    return *(volatile cpu_u32 *)(h->mmio + off);
}

static void xhci_direct_write(struct xhci_hcd *h, cpu_u64 off, cpu_u32 v)
{
    *(volatile cpu_u32 *)(h->mmio + off) = v;
}

static const struct xhci_mmio_ops xhci_direct_ops = {
    xhci_direct_read, xhci_direct_write
};
static const struct xhci_mmio_ops *xhci_ops = &xhci_direct_ops;

void xhci_mmio_install(const struct xhci_mmio_ops *ops)
{
    if (ops && ops->read32 && ops->write32)
        xhci_ops = ops;
}

void xhci_mmio_default(void)
{
    xhci_ops = &xhci_direct_ops;
}

/* Range-checked 32-bit access at BAR-relative byte offset. */
static enum xhci_result xh_r32(struct xhci_hcd *h, cpu_u64 off, cpu_u32 *out)
{
    if (!h || !out || !h->mmio || off > h->mmio_len ||
        4 > h->mmio_len - off)
        return XHCI_INVALID;
    *out = xhci_ops->read32(h, off);
    return XHCI_OK;
}

static enum xhci_result xh_w32(struct xhci_hcd *h, cpu_u64 off, cpu_u32 v)
{
    if (!h || !h->mmio || off > h->mmio_len || 4 > h->mmio_len - off)
        return XHCI_INVALID;
    xhci_ops->write32(h, off, v);
    return XHCI_OK;
}

/* u64 register pair (low then high). */
static enum xhci_result xh_r64(struct xhci_hcd *h, cpu_u64 off, cpu_u64 *out)
{
    cpu_u32 lo, hi;
    enum xhci_result r = xh_r32(h, off, &lo);
    if (r != XHCI_OK)
        return r;
    r = xh_r32(h, off + 4, &hi);
    if (r != XHCI_OK)
        return r;
    *out = ((cpu_u64)hi << 32) | lo;
    return XHCI_OK;
}

static enum xhci_result xh_w64(struct xhci_hcd *h, cpu_u64 off, cpu_u64 v)
{
    enum xhci_result r = xh_w32(h, off, (cpu_u32)v);
    if (r != XHCI_OK)
        return r;
    return xh_w32(h, off + 4, (cpu_u32)(v >> 32));
}

/* Block accessors: base + register offset with overflow-safe range check. */
static enum xhci_result xh_cap(struct xhci_hcd *h, cpu_u64 reg, cpu_u32 *out)
{
    if (!h || reg > h->mmio_len)
        return XHCI_RANGE;
    return xh_r32(h, reg, out);
}

static enum xhci_result xh_op_r(struct xhci_hcd *h, cpu_u64 reg, cpu_u32 *out)
{
    cpu_u64 base = h->op_base;
    if (!h || reg > h->mmio_len || base > h->mmio_len - reg)
        return XHCI_RANGE;
    return xh_r32(h, base + reg, out);
}

static enum xhci_result xh_op_w(struct xhci_hcd *h, cpu_u64 reg, cpu_u32 v)
{
    cpu_u64 base = h->op_base;
    if (!h || reg > h->mmio_len || base > h->mmio_len - reg)
        return XHCI_RANGE;
    return xh_w32(h, base + reg, v);
}

static enum xhci_result xh_rt_r(struct xhci_hcd *h, cpu_u64 reg, cpu_u32 *out)
{
    cpu_u64 base = h->rt_base;
    if (!h || reg > h->mmio_len || base > h->mmio_len - reg)
        return XHCI_RANGE;
    return xh_r32(h, base + reg, out);
}

static enum xhci_result xh_rt_w(struct xhci_hcd *h, cpu_u64 reg, cpu_u32 v)
{
    cpu_u64 base = h->rt_base;
    if (!h || reg > h->mmio_len || base > h->mmio_len - reg)
        return XHCI_RANGE;
    return xh_w32(h, base + reg, v);
}

static enum xhci_result xh_db_w(struct xhci_hcd *h, cpu_u32 n, cpu_u32 v)
{
    cpu_u64 base = h->db_base;
    cpu_u64 reg = (cpu_u64)n * 4u;
    if (!h || reg > h->mmio_len || base > h->mmio_len - reg)
        return XHCI_RANGE;
    return xh_w32(h, base + reg, v);
}

enum xhci_result xhci_op_read(struct xhci_hcd *h, cpu_u32 reg, cpu_u32 *out)
{
    if (!h || !out)
        return XHCI_INVALID;
    return xh_op_r(h, reg, out);
}

enum xhci_result xhci_rt_read(struct xhci_hcd *h, cpu_u32 reg, cpu_u32 *out)
{
    if (!h || !out)
        return XHCI_INVALID;
    return xh_rt_r(h, reg, out);
}

enum xhci_result xhci_bar_read(struct xhci_hcd *h, cpu_u64 off, cpu_u32 *out)
{
    if (!h || !out)
        return XHCI_INVALID;
    return xh_r32(h, off, out);
}

/* --- Claim ------------------------------------------------------------------ */

static cpu_u8 xhci_claimed;
static cpu_u8 xhci_claim_b, xhci_claim_d, xhci_claim_f;

enum xhci_result xhci_claim(struct xhci_hcd *h)
{
    const struct pci_device *d;
    if (!h)
        return XHCI_INVALID;
    if (xhci_claimed)
        return XHCI_STATE;
    d = pci_find_class(XHCI_PCI_CLASS, XHCI_PCI_SUBCLASS, XHCI_PCI_PROGIF, 0);
    if (!d)
        return XHCI_ABSENT;
    if (d->bus > 255 || d->device > 31 || d->function > 7)
        return XHCI_HW;
    h->bus = (cpu_u8)d->bus;
    h->dev = (cpu_u8)d->device;
    h->fn = (cpu_u8)d->function;
    h->caps.vendor = d->vendor_id;
    h->caps.device = d->device_id;
    h->caps.class_ = d->class_code;
    h->caps.subclass = d->subclass;
    h->caps.progif = d->prog_if;
    h->caps.rev = d->revision;
    {
        unsigned int t;
        h->started = 0;
        h->failed = 0;
        h->desync = 0;
        h->noprogress = 0;
        h->caps_valid = 0;
        h->max_slots_en = 0;
        h->bar_ord = 0xFFu;
        h->cmd0 = 0;
        h->mmio = 0;
        h->mmio_len = 0;
        h->op_base = 0;
        h->rt_base = 0;
        h->db_base = 0;
        h->dcb.virt = 0;
        h->dcb.magic = 0;
        h->spad_arr.virt = 0;
        h->spad_arr.magic = 0;
        h->cmd.dma.virt = 0;
        h->cmd.dma.magic = 0;
        h->evt.dma.virt = 0;
        h->evt.dma.magic = 0;
        h->erst.virt = 0;
        h->erst.magic = 0;
        for (t = 0; t < XHCI_MAX_SPADS; ++t) {
            h->spads[t].virt = 0;
            h->spads[t].magic = 0;
        }
        h->irq_handle = 0xFFFFFFFFu;
        h->irq_vec = 0;
        h->irq_count = 0;
        h->cmpl_count = 0;
        h->port_events = 0;
        h->other_events = 0;
        h->hook = 0;
        h->hook_ctx = 0;
        h->last_user = 0;
        h->last_rip = 0;
        h->last_isr = 0;
        for (t = 0; t < XHCI_TOKENS; ++t) {
            h->tokens[t].state = XHCI_TOK_FREE;
            h->tokens[t].ccode = 0;
            h->tokens[t].trb_bus = 0;
        }
    }
    h->claimed = 1;
    xhci_claimed = 1;
    xhci_claim_b = h->bus;
    xhci_claim_d = h->dev;
    xhci_claim_f = h->fn;
    return XHCI_OK;
}

/* --- BAR --------------------------------------------------------------------- */

enum xhci_result xhci_map_bar(struct xhci_hcd *h)
{
    const struct pci_device *d;
    cpu_u32 i, ord = 0xFFFFFFFFu;
    cpu_u16 cmd;
    cpu_u64 va = 0;
    if (!h || !h->claimed)
        return XHCI_INVALID;
    d = pci_find_bdf(h->bus, h->dev, h->fn);
    if (!d)
        return XHCI_HW;
    for (i = 0; i < d->bar_count; ++i) {
        if ((d->bars[i].kind == PCI_BAR_MMIO32 ||
             d->bars[i].kind == PCI_BAR_MMIO64) &&
            d->bars[i].size != 0) {
            ord = i;
            break;
        }
    }
    if (ord == 0xFFFFFFFFu)
        return XHCI_ABSENT;
    /* MEM decode required before any MMIO; save COMMAND for restore. */
    cmd = pci_cfg_read16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND);
    h->cmd0 = cmd;
    if (!(cmd & PCI_COMMAND_MEM)) {
        pci_cfg_write16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND,
                        (cpu_u16)(cmd | PCI_COMMAND_MEM));
        cmd = pci_cfg_read16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND);
        if (!(cmd & PCI_COMMAND_MEM))
            return XHCI_HW;
    }
    if (pci_map_bar(h->bus, h->dev, h->fn, ord, &va) != PCI_OK || !va)
        return XHCI_NOMEM;
    h->bar_ord = (cpu_u8)ord;
    h->mmio = va;
    h->mmio_len = d->bars[ord].size;
    return XHCI_OK;
}

/* --- Capability registers ----------------------------------------------------- */

enum xhci_result xhci_read_caps(struct xhci_hcd *h)
{
    cpu_u32 v, p1, p2, hcc, db, rt;
    cpu_u16 hi, lo;
    if (!h || !h->mmio)
        return XHCI_INVALID;
    if (xh_r32(h, XHCI_CAP_CAPLEN, &v) != XHCI_OK)
        return XHCI_HW;
    h->caps.caplen = (cpu_u8)(v & 0xFFu);
    h->caps.hciver = (cpu_u16)((v >> 16) & 0xFFFFu);
    if (h->caps.caplen < XHCI_CAP_MINLEN || h->caps.caplen >= h->mmio_len)
        return XHCI_RANGE;
    h->op_base = h->caps.caplen;
    if (xh_cap(h, XHCI_CAP_HCSP1, &p1) != XHCI_OK ||
        xh_cap(h, XHCI_CAP_HCSP2, &p2) != XHCI_OK ||
        xh_cap(h, XHCI_CAP_HCCP1, &hcc) != XHCI_OK ||
        xh_cap(h, XHCI_CAP_DBOFF, &db) != XHCI_OK ||
        xh_cap(h, XHCI_CAP_RTSOFF, &rt) != XHCI_OK)
        return XHCI_HW;
    h->caps.slots = (cpu_u8)(p1 & 0xFFu);
    h->caps.intrs = (cpu_u16)((p1 >> 8) & 0x7FFu);
    h->caps.ports = (cpu_u16)((p1 >> 24) & 0xFFu);
    if (!h->caps.slots || !h->caps.intrs)
        return XHCI_RANGE;
    hi = (cpu_u16)((p2 >> 21) & 0x1Fu);
    lo = (cpu_u16)((p2 >> 27) & 0x1Fu);
    h->caps.spads = (cpu_u16)((hi << 5) | lo);
    h->caps.ac64 = (cpu_u8)(hcc & 1u);
    h->caps.csz = (cpu_u8)((hcc >> 2) & 1u);
    h->caps.xecp = (cpu_u16)((hcc >> 16) & 0xFFFFu);
    h->db_base = db & 0xFFFFFFFCu;
    h->rt_base = rt & 0xFFFFFFE0u;
    if (!h->db_base || h->db_base >= h->mmio_len ||
        !h->rt_base || h->rt_base >= h->mmio_len)
        return XHCI_RANGE;
    if (h->caps.xecp && (cpu_u64)h->caps.xecp * 4u >= h->mmio_len)
        return XHCI_RANGE;
    /* Operational span: op base through CONFIG + one port bank for
       diagnostics. Doorbell 0 and interrupter 0 must fit. */
    if (h->op_base + XHCI_OP_CONFIG + 4 > h->mmio_len ||
        h->db_base + 4 > h->mmio_len ||
        h->rt_base + XHCI_RT_INTR + XHCI_INTR_STRIDE > h->mmio_len)
        return XHCI_RANGE;
    if (xh_op_r(h, XHCI_OP_PAGESZ, &h->caps.pagesz) != XHCI_OK)
        return XHCI_HW;
    h->caps_valid = 1;
    return XHCI_OK;
}

/* --- Extended capability walker ------------------------------------------------ */

enum xhci_result xhci_walk_xcaps(struct xhci_hcd *h,
    enum xhci_result (*visit)(struct xhci_hcd *h, cpu_u8 id,
                              cpu_u64 off, cpu_u8 next, void *ctx),
    void *ctx)
{
    cpu_u64 off;
    cpu_u64 seen[8];
    unsigned int nseen = 0, ttl;
    if (!h || !h->mmio || !visit)
        return XHCI_INVALID;
    if (!h->caps.xecp)
        return XHCI_OK;
    off = (cpu_u64)h->caps.xecp * 4u;
    for (ttl = 0; ttl < XHCI_XCAP_TTL; ++ttl) {
        cpu_u32 hdr;
        cpu_u8 id, next;
        unsigned int i;
        if (off + 4 > h->mmio_len)
            return XHCI_RANGE;
        for (i = 0; i < nseen; ++i)
            if (seen[i] == off)
                return XHCI_RANGE;
        if (nseen < 8)
            seen[nseen++] = off;
        if (xh_r32(h, off, &hdr) != XHCI_OK)
            return XHCI_HW;
        id = (cpu_u8)(hdr & 0xFFu);
        next = (cpu_u8)((hdr >> 8) & 0xFFu);
        if (id == 0)
            return XHCI_RANGE;
        {
            enum xhci_result r = visit(h, id, off, next, ctx);
            if (r != XHCI_OK)
                return r;
        }
        if (!next)
            return XHCI_OK;
        off += (cpu_u64)next * 4u;
    }
    return XHCI_RANGE;
}

/* --- Bounded waits (§12) ------------------------------------------------------ */

/* Poll BAR-relative register until masked condition holds. want_set nonzero
   waits for all mask bits set, else for all clear. */
static enum xhci_result xh_wait_bar(struct xhci_hcd *h, cpu_u64 off,
                                    cpu_u32 mask, int want_set)
{
    cpu_u64 spin;
    cpu_u32 v;
    if (!h || !mask)
        return XHCI_INVALID;
    for (spin = 0; spin < XHCI_SPIN_BUDGET; ++spin) {
        if (xh_r32(h, off, &v) != XHCI_OK)
            return XHCI_HW;
        if (want_set) {
            if ((v & mask) == mask)
                return XHCI_OK;
        } else if (!(v & mask)) {
            return XHCI_OK;
        }
    }
    return XHCI_TIMEOUT;
}

static enum xhci_result xh_wait_op(struct xhci_hcd *h, cpu_u64 reg,
                                   cpu_u32 mask, int want_set)
{
    cpu_u64 base;
    if (!h || reg > h->mmio_len)
        return XHCI_INVALID;
    base = h->op_base;
    if (base > h->mmio_len - reg)
        return XHCI_RANGE;
    return xh_wait_bar(h, base + reg, mask, want_set);
}

/* --- BIOS/OS legacy handoff ---------------------------------------------------- */

struct xhci_leg_ctx {
    cpu_u64 off;
    cpu_u8 found;
};

static enum xhci_result xhci_leg_visit(struct xhci_hcd *h, cpu_u8 id,
                                       cpu_u64 off, cpu_u8 next, void *ctx)
{
    struct xhci_leg_ctx *c = (struct xhci_leg_ctx *)ctx;
    (void)h;
    (void)next;
    if (!c)
        return XHCI_INVALID;
    if (id == XHCI_XCAP_LEGACY && !c->found) {
        c->found = 1;
        c->off = off;
    }
    return XHCI_OK;
}

enum xhci_result xhci_legacy_handoff(struct xhci_hcd *h)
{
    struct xhci_leg_ctx c;
    enum xhci_result r;
    cpu_u32 v;
    if (!h || !h->mmio || !h->caps_valid)
        return XHCI_INVALID;
    c.off = 0;
    c.found = 0;
    r = xhci_walk_xcaps(h, xhci_leg_visit, &c);
    if (r != XHCI_OK)
        return r;
    if (!c.found)
        return XHCI_OK;
    if (c.off + 8 > h->mmio_len)
        return XHCI_RANGE;
    if (xh_r32(h, c.off + 4, &v) != XHCI_OK)
        return XHCI_HW;
    if (v & XHCI_LEG_BIOS_OWNED) {
        if (xh_w32(h, c.off + 4, v | XHCI_LEG_OS_OWNED) != XHCI_OK)
            return XHCI_HW;
        r = xh_wait_bar(h, c.off + 4, XHCI_LEG_BIOS_OWNED, 0);
        if (r != XHCI_OK)
            return r;
    }
    /* Owned (or BIOS never owned): disable legacy SMIs, keep OS_OWNED. */
    if (xh_r32(h, c.off + 4, &v) != XHCI_OK)
        return XHCI_HW;
    v = (v & ~XHCI_LEG_SMI_MASK) | XHCI_LEG_OS_OWNED;
    if (xh_w32(h, c.off + 4, v) != XHCI_OK)
        return XHCI_HW;
    if (xh_r32(h, c.off + 4, &v) != XHCI_OK)
        return XHCI_HW;
    if (!(v & XHCI_LEG_OS_OWNED) || (v & XHCI_LEG_BIOS_OWNED))
        return XHCI_HW;
    return XHCI_OK;
}

/* --- Halt / reset -------------------------------------------------------------- */

enum xhci_result xhci_halt(struct xhci_hcd *h)
{
    cpu_u32 cmd;
    if (!h || !h->mmio || !h->caps_valid)
        return XHCI_INVALID;
    if (xh_op_r(h, XHCI_OP_USBCMD, &cmd) != XHCI_OK)
        return XHCI_HW;
    if (cmd & XHCI_CMD_RS) {
        if (xh_op_w(h, XHCI_OP_USBCMD, cmd & ~XHCI_CMD_RS) != XHCI_OK)
            return XHCI_HW;
    }
    return xh_wait_op(h, XHCI_OP_USBSTS, XHCI_STS_HCH, 1);
}

enum xhci_result xhci_reset(struct xhci_hcd *h)
{
    cpu_u32 cmd, sts;
    enum xhci_result r;
    if (!h || !h->mmio || !h->caps_valid)
        return XHCI_INVALID;
    if (xh_op_r(h, XHCI_OP_USBSTS, &sts) != XHCI_OK)
        return XHCI_HW;
    if (!(sts & XHCI_STS_HCH))
        return XHCI_STATE;
    if (xh_op_r(h, XHCI_OP_USBCMD, &cmd) != XHCI_OK)
        return XHCI_HW;
    if (xh_op_w(h, XHCI_OP_USBCMD, cmd | XHCI_CMD_HCRST) != XHCI_OK)
        return XHCI_HW;
    r = xh_wait_op(h, XHCI_OP_USBCMD, XHCI_CMD_HCRST, 0);
    if (r != XHCI_OK)
        return r;
    r = xh_wait_op(h, XHCI_OP_USBSTS, XHCI_STS_CNR, 0);
    if (r != XHCI_OK)
        return r;
    if (xh_op_r(h, XHCI_OP_USBCMD, &cmd) != XHCI_OK ||
        xh_op_r(h, XHCI_OP_USBSTS, &sts) != XHCI_OK)
        return XHCI_HW;
    if ((cmd & XHCI_CMD_RS) || !(sts & XHCI_STS_HCH))
        return XHCI_HW;
    return XHCI_OK;
}

/* --- PCI COMMAND: bus mastering ------------------------------------------------- */

enum xhci_result xhci_enable_bme(struct xhci_hcd *h)
{
    cpu_u16 cmd;
    if (!h || !h->claimed)
        return XHCI_INVALID;
    cmd = pci_cfg_read16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND);
    if (!(cmd & PCI_COMMAND_MEM))
        return XHCI_STATE;
    if (!(cmd & PCI_COMMAND_BUS_MASTER)) {
        pci_cfg_write16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND,
                        (cpu_u16)(cmd | PCI_COMMAND_BUS_MASTER));
        cmd = pci_cfg_read16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND);
        if (!(cmd & PCI_COMMAND_BUS_MASTER))
            return XHCI_HW;
    }
    return XHCI_OK;
}

/* --- TRB helpers (pure) -------------------------------------------------------- */

cpu_u32 xhci_encode_noop(struct xhci_trb *t, cpu_u8 pcs, cpu_u16 intr)
{
    if (!t || intr > 1023)
        return 0;
    t->param_lo = 0;
    t->param_hi = 0;
    t->status = ((cpu_u32)intr << XHCI_TRB_INTR_SHIFT);
    t->control = ((cpu_u32)XHCI_TRB_NOOP_CMD << XHCI_TRB_TYPE_SHIFT) |
                 (pcs ? XHCI_TRB_C : 0u);
    return 1;
}

void xhci_encode_link(struct xhci_trb *t, cpu_u64 next_bus, cpu_u8 pcs)
{
    t->param_lo = (cpu_u32)next_bus;
    t->param_hi = (cpu_u32)(next_bus >> 32);
    t->status = 0;
    t->control = ((cpu_u32)XHCI_TRB_LINK << XHCI_TRB_TYPE_SHIFT) |
                 XHCI_TRB_TC | (pcs ? XHCI_TRB_C : 0u);
}

/* Validate an event TRB against the expected consumer cycle; on success
   fills type/cc/ptr/slot. Returns nonzero when the TRB is ready AND
   well-formed (unknown types/codes still decode; callers decide). */
int xhci_decode_event(const struct xhci_trb *t, cpu_u8 ccs, cpu_u8 *type,
                      cpu_u8 *cc, cpu_u64 *ptr, cpu_u8 *slot)
{
    cpu_u8 c;
    if (!t || !type || !cc || !ptr || !slot)
        return 0;
    c = (t->control & XHCI_TRB_C) ? 1 : 0;
    if (c != (ccs ? 1 : 0))
        return 0;
    *type = (cpu_u8)((t->control >> XHCI_TRB_TYPE_SHIFT) & XHCI_TRB_TYPE_MASK);
    *cc = (cpu_u8)((t->status >> 24) & 0xFFu);
    *ptr = ((cpu_u64)t->param_hi << 32) | t->param_lo;
    *slot = (cpu_u8)((t->control >> 24) & 0xFFu);
    return 1;
}

/* --- DMA structures -------------------------------------------------------------- */

/* Test-only fault injection (§48); xhci_init sets, clears on exit. */
static enum xhci_fail_at xhci_inject = XHCI_FAIL_NONE;

static enum xhci_result xhci_alloc(struct xhci_hcd *h, cpu_u64 size,
                                   cpu_u64 align, struct dma_buffer *out)
{
    cpu_u64 max_bus = h->caps.ac64 ? DMA_ADDR_ANY : DMA_ADDR_32BIT;
    if (dma_alloc(size, align, max_bus, out) != DMA_OK)
        return XHCI_NOMEM;
    if (!h->caps.ac64 && out->bus > DMA_ADDR_32BIT)
        return XHCI_RANGE;
    return XHCI_OK;
}

enum xhci_result xhci_setup_dma(struct xhci_hcd *h)
{
    cpu_u32 cfg, back32;
    cpu_u64 back;
    cpu_u64 n;
    unsigned int i;
    struct xhci_trb *cmd;
    cpu_u64 *erst;
    if (!h || !h->mmio || !h->caps_valid)
        return XHCI_INVALID;
    if (!(h->caps.pagesz & 1u))
        return XHCI_RANGE;
    /* MaxSlotsEn: bounded, never above hardware MaxSlots. */
    h->max_slots_en = h->caps.slots < XHCI_MAX_SLOTS_EN ?
        h->caps.slots : XHCI_MAX_SLOTS_EN;
    if (xh_op_r(h, XHCI_OP_CONFIG, &cfg) != XHCI_OK)
        return XHCI_HW;
    cfg = (cfg & 0xFFFFFF00u) | h->max_slots_en;
    if (xh_op_w(h, XHCI_OP_CONFIG, cfg) != XHCI_OK)
        return XHCI_HW;
    if (xh_op_r(h, XHCI_OP_CONFIG, &back32) != XHCI_OK ||
        (back32 & 0xFFu) != h->max_slots_en)
        return XHCI_HW;
    /* DCBAA: (MaxSlotsEn+1) 8-byte entries, 64B aligned, zeroed. */
    n = ((cpu_u64)h->max_slots_en + 1u) * 8u;
    if (xhci_alloc(h, n, 64, &h->dcb) != XHCI_OK)
        return XHCI_NOMEM;
    if (h->dcb.bus & 0x3Fu)
        return XHCI_RANGE;
    if (xh_w64(h, h->op_base + XHCI_OP_DCBAA, h->dcb.bus) != XHCI_OK)
        return XHCI_HW;
    if (xh_r64(h, h->op_base + XHCI_OP_DCBAA, &back) != XHCI_OK)
        return XHCI_HW;
    if ((back & ~0x3Fu) != (h->dcb.bus & ~0x3Fu))
        return XHCI_HW;
    if (xhci_inject == XHCI_FAIL_DCBAA)
        return XHCI_REFUSED;
    /* Scratchpads. */
    if (h->caps.spads > XHCI_MAX_SPADS)
        return XHCI_RANGE;
    if (h->caps.spads == 0) {
        if (*(volatile cpu_u64 *)h->dcb.virt != 0)
            return XHCI_HW;
    } else {
        cpu_u64 *arr;
        n = (cpu_u64)h->caps.spads * 8u;
        if (xhci_alloc(h, n, 64, &h->spad_arr) != XHCI_OK)
            return XHCI_NOMEM;
        for (i = 0; i < h->caps.spads; ++i) {
            if (xhci_alloc(h, 4096, 4096, &h->spads[i]) != XHCI_OK)
                return XHCI_NOMEM;
        }
        arr = (cpu_u64 *)h->spad_arr.virt;
        for (i = 0; i < h->caps.spads; ++i)
            arr[i] = h->spads[i].bus;
        dma_sync_for_device(&h->spad_arr);
        *(volatile cpu_u64 *)h->dcb.virt = h->spad_arr.bus;
    }
    if (xhci_inject == XHCI_FAIL_SCRATCH)
        return XHCI_REFUSED;
    dma_sync_for_device(&h->dcb);
    /* Command ring: 32 TRBs, 64B aligned (CRCR 63:6). */
    if (xhci_alloc(h, XHCI_CMD_TRBS * XHCI_TRB_SIZE, 64, &h->cmd.dma)
        != XHCI_OK)
        return XHCI_NOMEM;
    if (h->cmd.dma.bus & 0x3Fu)
        return XHCI_RANGE;
    h->cmd.count = XHCI_CMD_TRBS;
    h->cmd.enq = 0;
    h->cmd.pcs = 1;
    h->cmd.wraps = 0;
    cmd = (struct xhci_trb *)h->cmd.dma.virt;
    xhci_encode_link(&cmd[XHCI_CMD_TRBS - 1], h->cmd.dma.bus, 1);
    dma_sync_for_device(&h->cmd.dma);
    if (xh_w64(h, h->op_base + XHCI_OP_CRCR,
               h->cmd.dma.bus | XHCI_CRCR_RCS) != XHCI_OK)
        return XHCI_HW;
    if (xh_r64(h, h->op_base + XHCI_OP_CRCR, &back) != XHCI_OK)
        return XHCI_HW;
    if ((back & ~0x3Fu) != (h->cmd.dma.bus & ~0x3Fu) || !(back & XHCI_CRCR_RCS))
        return XHCI_HW;
    if (xhci_inject == XHCI_FAIL_CMDRING)
        return XHCI_REFUSED;
    /* Event ring: 32 TRBs, consumer cycle 1. */
    if (xhci_alloc(h, XHCI_EVT_TRBS * XHCI_TRB_SIZE, 64, &h->evt.dma)
        != XHCI_OK)
        return XHCI_NOMEM;
    h->evt.count = XHCI_EVT_TRBS;
    h->evt.deq = 0;
    h->evt.ccs = 1;
    h->evt.wraps = 0;
    h->evt.erdp_shadow = h->evt.dma.bus;
    if (xhci_inject == XHCI_FAIL_EVTRING)
        return XHCI_REFUSED;
    /* ERST: one 16-byte entry. */
    if (xhci_alloc(h, 16, 64, &h->erst) != XHCI_OK)
        return XHCI_NOMEM;
    erst = (cpu_u64 *)h->erst.virt;
    erst[0] = h->evt.dma.bus;
    erst[1] = (cpu_u64)XHCI_EVT_TRBS;
    dma_sync_for_device(&h->erst);
    if (xhci_inject == XHCI_FAIL_ERST)
        return XHCI_REFUSED;
    return XHCI_OK;
}

/* --- Interrupter 0 ---------------------------------------------------------------- */

enum xhci_result xhci_setup_interrupter(struct xhci_hcd *h)
{
    cpu_u64 ib = XHCI_RT_INTR;
    cpu_u32 back;
    if (!h || !h->mmio || !h->caps_valid || !h->erst.virt ||
        !h->evt.dma.virt)
        return XHCI_INVALID;
    /* Conservative: no moderation. */
    if (xh_rt_w(h, ib + 0x04, 0) != XHCI_OK)
        return XHCI_HW;
    if (xh_rt_w(h, ib + 0x08, 1) != XHCI_OK)
        return XHCI_HW;
    if (xh_rt_r(h, ib + 0x08, &back) != XHCI_OK || (back & 0xFFFFu) != 1)
        return XHCI_HW;
    if (xh_w64(h, h->rt_base + ib + 0x10, h->erst.bus) != XHCI_OK)
        return XHCI_HW;
    /* ERSTBA-high write commits the table (QEMU validates erstsz==1
       and seg bounds here); the abort on violation is the fence. */
    if (xh_w64(h, h->rt_base + ib + 0x18, h->evt.dma.bus) != XHCI_OK)
        return XHCI_HW;
    h->evt.erdp_shadow = h->evt.dma.bus;
    if (xh_rt_w(h, ib + 0x00, XHCI_IMAN_IE) != XHCI_OK)
        return XHCI_OK;
    return XHCI_OK;
}

/* --- IRQ handler (either CR3; IRQ-context rules) --------------------------------- */

static unsigned int xhci_consume(struct xhci_hcd *h)
{
    struct xhci_trb *ev = (struct xhci_trb *)h->evt.dma.virt;
    unsigned int guard, n = 0;
    for (guard = 0; guard < h->evt.count; ++guard) {
        struct xhci_trb *t = &ev[h->evt.deq];
        cpu_u8 type, cc, slot;
        cpu_u64 ptr;
        unsigned int i;
        dma_rmb();
        if (!xhci_decode_event(t, h->evt.ccs, &type, &cc, &ptr, &slot))
            break;
        if (type == XHCI_TRB_EV_CMPL) {
            h->cmpl_count++;
            for (i = 0; i < XHCI_TOKENS; ++i) {
                if (h->tokens[i].state == XHCI_TOK_SUBMITTED &&
                    h->tokens[i].trb_bus == ptr) {
                    h->tokens[i].state = (cc == XHCI_CC_SUCCESS) ?
                        XHCI_TOK_COMPLETED : XHCI_TOK_ERROR;
                    h->tokens[i].ccode = cc;
                    break;
                }
            }
            if (i == XHCI_TOKENS)
                h->failed = 1;
        } else if (type == XHCI_TRB_EV_PORT) {
            h->port_events++;
        } else {
            h->other_events++;
        }
        if (h->hook)
            h->hook(h, type, cc, ptr, slot, h->evt.ccs, h->hook_ctx);
        h->evt.deq++;
        if (h->evt.deq == h->evt.count) {
            h->evt.deq = 0;
            h->evt.ccs = h->evt.ccs ? 0 : 1;
            h->evt.wraps++;
        }
        ++n;
    }
    return n;
}

static void xhci_isr(cpu_u32 vector, void *opaque)
{
    struct xhci_hcd *h = (struct xhci_hcd *)opaque;
    cpu_u64 saved;
    cpu_u64 deq;
    (void)vector;
    if (!h || !h->evt.dma.virt)
        return;
    h->irq_count++;
    h->last_user = irq_last_frame_user();
    h->last_rip = irq_last_frame_rip();
    h->last_isr = apic_lapic_isr_set(vector) ? 1 : 0;
    saved = apic_mmio_enter();
    /* Storm quarantine: IP with nothing consumable twice running means
       ring desync (a healthy controller always leaves ≥1 event). Mask
       the interrupter and flag it; the waiter fails instead of the
       kernel livelocking under a re-firing MSI-X. */
    if (xhci_consume(h) == 0) {
        if (++h->noprogress >= 2)
            h->desync = 1;
    } else {
        h->noprogress = 0;
    }
    /* Advance ERDP to the dequeue with EHB re-arm, then clear IP
       (keeping IE unless quarantined) and W1C the USBSTS event bit. */
    deq = h->evt.dma.bus + (cpu_u64)h->evt.deq * XHCI_TRB_SIZE;
    h->evt.erdp_shadow = deq;
    xh_w64(h, h->rt_base + XHCI_RT_INTR + 0x18, deq | XHCI_ERDP_EHB);
    xh_rt_w(h, XHCI_RT_INTR + 0x00,
            XHCI_IMAN_IP | (h->desync ? 0u : (cpu_u32)XHCI_IMAN_IE));
    xh_op_w(h, XHCI_OP_USBSTS, XHCI_STS_EINT);
    apic_mmio_exit(saved);
}

enum xhci_result xhci_arm_msix(struct xhci_hcd *h)
{
    cpu_u32 handle = 0xFFFFFFFFu;
    unsigned int vectors[MSI_MAX_VECTORS];
    enum msi_result mr;
    struct msi_handle_info info;
    if (!h || !h->claimed || !h->evt.dma.virt)
        return XHCI_INVALID;
    if (h->irq_handle != 0xFFFFFFFFu)
        return XHCI_STATE;
    /* Interrupter 0, MSI-X entry 0, exactly one vector. */
    mr = pci_irq_enable_msix(h->bus, h->dev, h->fn, 1, xhci_isr, h,
                             &handle, vectors);
    if (mr != MSI_OK)
        return (mr == MSI_REFUSED) ? XHCI_REFUSED : XHCI_HW;
    if (pci_irq_info(handle, &info) != MSI_OK) {
        pci_irq_disable(handle);
        return XHCI_HW;
    }
    if (info.kind != MSI_KIND_MSIX || info.nvec != 1 ||
        info.vector[0] >= 256) {
        pci_irq_disable(handle);
        return XHCI_HW;
    }
    h->irq_handle = handle;
    h->irq_vec = (cpu_u8)info.vector[0];
    return XHCI_OK;
}

/* --- Start ----------------------------------------------------------------------- */

enum xhci_result xhci_start(struct xhci_hcd *h)
{
    cpu_u32 cmd;
    if (!h || !h->mmio || !h->caps_valid || h->irq_handle == 0xFFFFFFFFu)
        return XHCI_INVALID;
    if (xh_op_r(h, XHCI_OP_USBCMD, &cmd) != XHCI_OK)
        return XHCI_HW;
    if (!(cmd & XHCI_CMD_INTE)) {
        if (xh_op_w(h, XHCI_OP_USBCMD, cmd | XHCI_CMD_INTE) != XHCI_OK)
            return XHCI_HW;
    }
    if (xh_op_r(h, XHCI_OP_USBCMD, &cmd) != XHCI_OK)
        return XHCI_HW;
    if (xh_op_w(h, XHCI_OP_USBCMD, cmd | XHCI_CMD_RS) != XHCI_OK)
        return XHCI_HW;
    if (xh_wait_op(h, XHCI_OP_USBSTS, XHCI_STS_HCH, 0) != XHCI_OK)
        return XHCI_TIMEOUT;
    h->started = 1;
    return XHCI_OK;
}

/* --- NO-OP submit / wait ------------------------------------------------------------- */

enum xhci_result xhci_submit_noop(struct xhci_hcd *h, cpu_u8 *tok_out)
{
    unsigned int i;
    cpu_u8 tok = 0xFFu;
    struct xhci_trb *cmd;
    cpu_u64 bus;
    if (!h || !h->started || !h->cmd.dma.virt || !tok_out)
        return XHCI_INVALID;
    for (i = 0; i < XHCI_TOKENS; ++i) {
        if (h->tokens[i].state == XHCI_TOK_FREE ||
            h->tokens[i].state == XHCI_TOK_COMPLETED ||
            h->tokens[i].state == XHCI_TOK_ERROR) {
            tok = (cpu_u8)i;
            break;
        }
    }
    if (tok == 0xFFu)
        return XHCI_BUSY;
    cmd = (struct xhci_trb *)h->cmd.dma.virt;
    /* Ring-full is impossible by construction: 8 tokens < 31 usable
       TRBs, so any slot reuse implies >= 23 completions, which (in the
       in-order NOOP stream) include that slot's own command — hardware
       has consumed past it. Transfer rings (A2+) need explicit
       slot-ownership; the token bound suffices for A1. */
    bus = h->cmd.dma.bus + (cpu_u64)h->cmd.enq * XHCI_TRB_SIZE;
    if (!xhci_encode_noop(&cmd[h->cmd.enq], h->cmd.pcs, 0))
        return XHCI_INVALID;
    h->tokens[tok].state = XHCI_TOK_SUBMITTED;
    h->tokens[tok].ccode = 0;
    h->tokens[tok].trb_bus = bus;
    dma_sync_for_device(&h->cmd.dma);
    dma_wmb();
    h->cmd.enq++;
    if (h->cmd.enq == h->cmd.count - 1) {
        /* Latch the Link cycle to the lap just completed, then wrap. */
        struct xhci_trb *link = &cmd[h->cmd.count - 1];
        if (h->cmd.pcs)
            link->control |= XHCI_TRB_C;
        else
            link->control &= ~XHCI_TRB_C;
        dma_wmb();
        h->cmd.enq = 0;
        h->cmd.pcs = h->cmd.pcs ? 0 : 1;
        h->cmd.wraps++;
    }
    if (xh_db_w(h, 0, 0) != XHCI_OK)
        return XHCI_HW;
    *tok_out = tok;
    return XHCI_OK;
}

enum xhci_token_state xhci_token_state(struct xhci_hcd *h, cpu_u8 tok)
{
    if (!h || tok >= XHCI_TOKENS)
        return XHCI_TOK_FREE;
    return (enum xhci_token_state)h->tokens[tok].state;
}

enum xhci_result xhci_wait_token(struct xhci_hcd *h, cpu_u8 tok)
{
    cpu_u64 spin;
    if (!h || tok >= XHCI_TOKENS)
        return XHCI_INVALID;
    if (h->tokens[tok].state != XHCI_TOK_SUBMITTED)
        return XHCI_STATE;
    __asm__ volatile ("sti" : : : "memory");
    for (spin = 0; spin < XHCI_SPIN_BUDGET; ++spin) {
        cpu_u8 st = *(volatile cpu_u8 *)&h->tokens[tok].state;
        if (*(volatile cpu_u8 *)&h->desync) {
            __asm__ volatile ("cli" : : : "memory");
            return XHCI_HW;
        }
        if (st == XHCI_TOK_COMPLETED)
            break;
        if (st == XHCI_TOK_ERROR) {
            __asm__ volatile ("cli" : : : "memory");
            return XHCI_HW;
        }
    }
    __asm__ volatile ("cli" : : : "memory");
    if (*(volatile cpu_u8 *)&h->tokens[tok].state != XHCI_TOK_COMPLETED)
        return XHCI_TIMEOUT;
    return XHCI_OK;
}

/* --- Teardown (ordered unwind; fail-safe on unquiesced hardware) ------------------- */

enum xhci_result xhci_teardown(struct xhci_hcd *h)
{
    enum xhci_result first = XHCI_OK, r;
    unsigned int i;
    cpu_u32 sts;
    if (!h || !h->claimed)
        return XHCI_INVALID;
    /* Mask the interrupter first so no new MSI-X can fire mid-teardown. */
    if (h->mmio && h->caps_valid)
        (void)xh_rt_w(h, XHCI_RT_INTR + 0x00, 0);
    if (h->irq_handle != 0xFFFFFFFFu) {
        if (pci_irq_disable(h->irq_handle) != MSI_OK)
            first = XHCI_HW;
        h->irq_handle = 0xFFFFFFFFu;
    }
    h->started = 0;
    /* Quiesce: halt, else reset. If neither stops the controller, the
       DMA stays pinned (safe leak) and teardown reports failure. */
    if (h->mmio && h->caps_valid) {
        r = xhci_halt(h);
        if (r != XHCI_OK)
            r = xhci_reset(h);
        if (r != XHCI_OK)
            return (first == XHCI_OK) ? r : first;
        if (xh_op_r(h, XHCI_OP_USBSTS, &sts) != XHCI_OK ||
            !(sts & XHCI_STS_HCH))
            return (first == XHCI_OK) ? XHCI_HW : first;
    }
    /* Hardware quiescent: restore COMMAND, free DMA, unmap, unclaim.
       COMMAND is restored only when map_bar saved it. */
    if (h->bar_ord != 0xFFu) {
        pci_cfg_write16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND, h->cmd0);
        if (pci_cfg_read16(h->bus, h->dev, h->fn, PCI_CFG_COMMAND) != h->cmd0 &&
            first == XHCI_OK)
            first = XHCI_HW;
    }
    if (h->cmd.dma.virt && dma_free(&h->cmd.dma) != DMA_OK && first == XHCI_OK)
        first = XHCI_HW;
    if (h->evt.dma.virt && dma_free(&h->evt.dma) != DMA_OK && first == XHCI_OK)
        first = XHCI_HW;
    if (h->erst.virt && dma_free(&h->erst) != DMA_OK && first == XHCI_OK)
        first = XHCI_HW;
    for (i = 0; i < h->caps.spads && i < XHCI_MAX_SPADS; ++i) {
        if (h->spads[i].virt && dma_free(&h->spads[i]) != DMA_OK &&
            first == XHCI_OK)
            first = XHCI_HW;
    }
    if (h->spad_arr.virt && dma_free(&h->spad_arr) != DMA_OK &&
        first == XHCI_OK)
        first = XHCI_HW;
    if (h->dcb.virt && dma_free(&h->dcb) != DMA_OK && first == XHCI_OK)
        first = XHCI_HW;
    if (h->mmio) {
        if (pci_unmap_bar(h->bus, h->dev, h->fn, h->bar_ord) != PCI_OK &&
            first == XHCI_OK)
            first = XHCI_HW;
        h->mmio = 0;
    }
    h->claimed = 0;
    xhci_claimed = 0;
    return first;
}

/* --- Staged init ---------------------------------------------------------------------- */

enum xhci_result xhci_init(struct xhci_hcd *h, enum xhci_fail_at fail_at)
{
    enum xhci_result r;
    if (!h)
        return XHCI_INVALID;
    xhci_inject = fail_at;
    r = xhci_claim(h);
    if (r != XHCI_OK)
        goto out;
    if (fail_at == XHCI_FAIL_BAR) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_map_bar(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_CAPS) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_read_caps(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_LEGACY) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_legacy_handoff(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_HALT) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_halt(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_RESET) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_reset(h);
    if (r != XHCI_OK)
        goto unwind;
    r = xhci_setup_dma(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_INTR) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_setup_interrupter(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_BME) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_enable_bme(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_MSIX) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_arm_msix(h);
    if (r != XHCI_OK)
        goto unwind;
    if (fail_at == XHCI_FAIL_START) {
        r = XHCI_REFUSED;
        goto unwind;
    }
    r = xhci_start(h);
    if (r != XHCI_OK)
        goto unwind;
    h->started = 1;
    goto out;
unwind:
    {
        enum xhci_result tr = xhci_teardown(h);
        (void)tr;
    }
out:
    xhci_inject = XHCI_FAIL_NONE;
    return r;
}
