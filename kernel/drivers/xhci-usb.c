#include "xhci-usb.h"
#include "dma.h"

/* USB-A1 enumeration engine (docs/design/usb.md). Uses the xHCI-A1
   command/event engine; owns protocols, ports, slots, contexts, EP0
   rings, and control transfers. */

static enum xusb_fail_at xusb_inject = XUSB_FAIL_NONE;

#define PORTSC_REG(n) (0x400u + ((cpu_u32)(n)-1u) * 0x10u)

static enum xusb_result map_xhci(enum xhci_result r)
{
    switch (r) {
    case XHCI_OK: return XUSB_OK;
    case XHCI_TIMEOUT: return XUSB_TIMEOUT;
    case XHCI_NOMEM: return XUSB_NOMEM;
    case XHCI_RANGE: return XUSB_RANGE;
    case XHCI_STATE: return XUSB_STATE;
    case XHCI_INVALID: return XUSB_INVALID;
    default: return XUSB_HW;
    }
}

cpu_u32 xusb_last_addr_control(const struct xusb_dev *dev)
{
    return dev ? dev->addr_control : 0;
}

void xusb_inject_force(enum xusb_fail_at f)
{
    xusb_inject = f;
}

/* ---------- Supported Protocols ---------- */

struct proto_walk {
    struct xusb_bus *bus;
    enum xusb_result err;
};

static enum xhci_result proto_visit(struct xhci_hcd *h, cpu_u8 id,
                                    cpu_u64 off, cpu_u8 next, void *ctx)
{
    struct proto_walk *w = (struct proto_walk *)ctx;
    struct xusb_bus *bus = w->bus;
    struct xusb_proto *p;
    cpu_u32 dw0, dw1, dw2, i;
    cpu_u8 psic;
    (void)next;
    if (id != XHCI_XCAP_PROTO)
        return XHCI_OK;
    if (w->err != XUSB_OK)
        return XHCI_OK;
    if (bus->nprotos >= XHCI_PROTOS_MAX) {
        w->err = XUSB_RANGE;
        return XHCI_OK;
    }
    if (xhci_bar_read(h, off, &dw0) != XHCI_OK ||
        xhci_bar_read(h, off + 4, &dw1) != XHCI_OK ||
        xhci_bar_read(h, off + 8, &dw2) != XHCI_OK) {
        w->err = XUSB_HW;
        return XHCI_OK;
    }
    if (dw1 != XHCI_PROTO_NAME_USB)
        return XHCI_OK;
    p = &bus->protos[bus->nprotos];
    p->major = (cpu_u8)((dw0 >> 24) & 0xFFu);
    p->minor = (cpu_u8)((dw0 >> 16) & 0xFFu);
    p->port_off = (cpu_u8)(dw2 & 0xFFu);
    p->port_count = (cpu_u8)((dw2 >> 8) & 0xFFu);
    psic = (cpu_u8)((dw2 >> 28) & 0xFu);
    p->cap_off = off;
    if (!p->port_off || !p->port_count ||
        (cpu_u32)p->port_off + (cpu_u32)p->port_count - 1u >
        (cpu_u32)h->caps.ports) {
        w->err = XUSB_RANGE;
        return XHCI_OK;
    }
    for (i = 0; i < bus->nprotos; ++i) {
        cpu_u32 a0 = bus->protos[i].port_off;
        cpu_u32 a1 = a0 + bus->protos[i].port_count - 1u;
        cpu_u32 b0 = p->port_off;
        cpu_u32 b1 = b0 + p->port_count - 1u;
        if (!(b1 < a0 || b0 > a1)) {
            w->err = XUSB_RANGE;
            return XHCI_OK;
        }
    }
    if (psic > XHCI_PSI_MAX) {
        w->err = XUSB_RANGE;
        return XHCI_OK;
    }
    p->psi_count = psic;
    for (i = 0; i < psic; ++i) {
        if (xhci_bar_read(h, off + 16 + (cpu_u64)i * 4u, &p->psi[i])
            != XHCI_OK) {
            w->err = XUSB_HW;
            return XHCI_OK;
        }
    }
    bus->nprotos++;
    return XHCI_OK;
}

enum xusb_result xusb_parse_protos(struct xusb_bus *bus)
{
    struct proto_walk w;
    if (!bus || !bus->hcd)
        return XUSB_INVALID;
    if (xusb_inject == XUSB_FAIL_PROTO)
        return XUSB_FAILED_DEV;
    bus->nprotos = 0;
    w.bus = bus;
    w.err = XUSB_OK;
    if (xhci_walk_xcaps(bus->hcd, proto_visit, &w) != XHCI_OK)
        return XUSB_HW;
    return w.err;
}

static int proto_of(const struct xusb_bus *bus, cpu_u32 num)
{
    cpu_u32 i;
    for (i = 0; i < bus->nprotos; ++i) {
        cpu_u32 off = bus->protos[i].port_off;
        if (num >= off && num < off + bus->protos[i].port_count)
            return (int)i;
    }
    return -1;
}

/* ---------- Port scan ---------- */

enum xusb_result xusb_scan_ports(struct xusb_bus *bus)
{
    struct xhci_hcd *h;
    cpu_u32 n, i;
    if (!bus || !bus->hcd)
        return XUSB_INVALID;
    if (xusb_inject == XUSB_FAIL_SCAN)
        return XUSB_FAILED_DEV;
    h = bus->hcd;
    if (!h->caps_valid || h->caps.ports > XHCI_PORTS_MAX)
        return XUSB_RANGE;
    n = h->caps.ports;
    bus->nports = n;
    for (i = 0; i < n; ++i) {
        struct xusb_port *p = &bus->ports[i];
        cpu_u32 ps;
        int pi;
        p->num = i + 1;
        pi = proto_of(bus, p->num);
        if (pi < 0) {
            p->proto = 0xFFu;
            p->state = XUSB_DISCONNECTED;
            continue;
        }
        p->proto = (cpu_u8)pi;
        if (xhci_op_read(h, PORTSC_REG(p->num), &ps) != XHCI_OK)
            return XUSB_HW;
        p->last_ps = ps;
        p->speed_id = (cpu_u8)((ps >> XHCI_PS_SPD_SHIFT) & XHCI_PS_SPD_MASK);
        p->speed = (cpu_u8)xusb_port_speed(bus, i);
        if (ps & XHCI_PS_CCS) {
            if (p->state == XUSB_DISCONNECTED)
                p->state = XUSB_CONNECTED;
        } else if (p->state != XUSB_DISCONNECTED) {
            p->state = XUSB_FAILED;
        }
        /* Acknowledge a latched connect-status change once seen. */
        if (ps & XHCI_PS_CSC) {
            if (xhci_op_write(h, PORTSC_REG(p->num), XHCI_PS_CSC)
                != XHCI_OK)
                return XUSB_HW;
        }
    }
    /* PORTSC is ground truth; ISR port-change claims are owned by
       the caller (event proof), never drained implicitly here. */
    return XUSB_OK;
}

enum usb_speed xusb_port_speed(const struct xusb_bus *bus, cpu_u32 idx)
{
    const struct xusb_proto *p;
    cpu_u8 sid;
    cpu_u32 i;
    if (!bus || idx >= bus->nports)
        return USB_SPEED_UNKNOWN;
    if (bus->ports[idx].proto >= bus->nprotos)
        return USB_SPEED_UNKNOWN;
    p = &bus->protos[bus->ports[idx].proto];
    sid = bus->ports[idx].speed_id;
    for (i = 0; i < p->psi_count; ++i) {
        cpu_u32 psiv = p->psi[i] & 0xFu;
        if (psiv == sid) {
            cpu_u32 psie = (p->psi[i] >> 4) & 0x3u;
            cpu_u32 psim = (p->psi[i] >> 16) & 0xFFFFu;
            cpu_u64 rate = (cpu_u64)psim;
            if (psie == 1) rate *= 1000ull;
            else if (psie == 2) rate *= 1000000ull;
            else if (psie == 3) rate *= 1000000000ull;
            if (rate == 1500000ull) return USB_SPEED_LOW;
            if (rate == 12000000ull) return USB_SPEED_FULL;
            if (rate == 480000000ull) return USB_SPEED_HIGH;
            if (rate == 5000000000ull) return USB_SPEED_SUPER;
            if (rate == 10000000000ull) return USB_SPEED_SUPERPLUS;
            return USB_SPEED_UNKNOWN;
        }
    }
    if (p->psi_count)
        return USB_SPEED_UNKNOWN;
    /* No PSI rows: xHCI default encodings per protocol major. */
    if (p->major == 2) {
        if (sid == XHCI_SPD_USB2_FS) return USB_SPEED_FULL;
        if (sid == XHCI_SPD_USB2_LS) return USB_SPEED_LOW;
        if (sid == XHCI_SPD_USB2_HS) return USB_SPEED_HIGH;
    } else if (p->major == 3) {
        if (sid == XHCI_SPD_USB3_SS) return USB_SPEED_SUPER;
        if (sid == 5) return USB_SPEED_SUPERPLUS;
    }
    return USB_SPEED_UNKNOWN;
}

/* ---------- PORTSC actions ---------- */

enum xusb_result xusb_port_ack(struct xusb_bus *bus, cpu_u32 idx,
                               cpu_u32 bits)
{
    if (!bus || !bus->hcd || idx >= bus->nports)
        return XUSB_INVALID;
    if (!bits || (bits & ~XHCI_PS_W1C))
        return XUSB_INVALID;
    return map_xhci(xhci_op_write(bus->hcd, PORTSC_REG(idx + 1), bits));
}

enum xusb_result xusb_port_reset(struct xusb_bus *bus, cpu_u32 idx)
{
    struct xhci_hcd *h;
    struct xusb_port *p;
    cpu_u32 ps;
    cpu_u64 spin;
    if (!bus || !bus->hcd || idx >= bus->nports)
        return XUSB_INVALID;
    if (xusb_inject == XUSB_FAIL_RESET)
        return XUSB_FAILED_DEV;
    h = bus->hcd;
    p = &bus->ports[idx];
    if (p->proto >= bus->nprotos)
        return XUSB_STATE;
    if (bus->protos[p->proto].major == 3)
        return XUSB_UNSUPPORTED;
    if (p->state != XUSB_CONNECTED)
        return XUSB_STATE;
    if (xhci_op_read(h, PORTSC_REG(p->num), &ps) != XHCI_OK)
        return XUSB_HW;
    if (!(ps & XHCI_PS_CCS))
        return XUSB_STATE;
    p->state = XUSB_RESETTING;
    if (!(ps & XHCI_PS_PP)) {
        cpu_u32 w = (ps & XHCI_PS_RW) | XHCI_PS_PP;
        if (xhci_op_write(h, PORTSC_REG(p->num), w) != XHCI_OK) {
            p->state = XUSB_FAILED;
            return XUSB_HW;
        }
    }
    /* Reset = write PR alone (write-1-to-start ignores the rest). */
    if (xhci_op_write(h, PORTSC_REG(p->num), XHCI_PS_PR) != XHCI_OK) {
        p->state = XUSB_FAILED;
        return XUSB_HW;
    }
    for (spin = 0; spin < XHCI_SPIN_BUDGET; ++spin) {
        if (xhci_op_read(h, PORTSC_REG(p->num), &ps) != XHCI_OK) {
            p->state = XUSB_FAILED;
            return XUSB_HW;
        }
        if (!(ps & XHCI_PS_PR))
            break;
    }
    if (ps & XHCI_PS_PR) {
        p->state = XUSB_FAILED;
        return XUSB_TIMEOUT;
    }
    p->last_ps = ps;
    if (!(ps & XHCI_PS_PED) ||
        ((ps >> XHCI_PS_PLS_SHIFT) & XHCI_PS_PLS_MASK) != XHCI_PLS_U0 ||
        !(ps & XHCI_PS_PRC)) {
        p->state = XUSB_FAILED;
        return XUSB_FAILED_DEV;
    }
    if (xhci_op_write(h, PORTSC_REG(p->num), XHCI_PS_PRC) != XHCI_OK) {
        p->state = XUSB_FAILED;
        return XUSB_HW;
    }
    p->speed_id = (cpu_u8)((ps >> XHCI_PS_SPD_SHIFT) & XHCI_PS_SPD_MASK);
    p->speed = (cpu_u8)xusb_port_speed(bus, idx);
    if (p->speed == USB_SPEED_UNKNOWN) {
        p->state = XUSB_FAILED;
        return XUSB_FAILED_DEV;
    }
    p->state = XUSB_ENABLED;
    return XUSB_OK;
}

/* ---------- Slots ---------- */

enum xusb_result xusb_enable_slot(struct xusb_dev *dev)
{
    struct xhci_hcd *h;
    struct xusb_port *p;
    cpu_u8 tok = 0xFFu;
    cpu_u8 slot;
    if (!dev || !dev->bus || !dev->bus->hcd ||
        dev->port_idx >= dev->bus->nports)
        return XUSB_INVALID;
    if (xusb_inject == XUSB_FAIL_ENSLOT)
        return XUSB_FAILED_DEV;
    h = dev->bus->hcd;
    p = &dev->bus->ports[dev->port_idx];
    if (p->state != XUSB_ENABLED || dev->slot)
        return XUSB_STATE;
    if (xhci_submit_cmd(h, 0, 0, 0,
            (cpu_u32)XHCI_TRB_EN_SLOT << XHCI_TRB_TYPE_SHIFT,
            &tok) != XHCI_OK)
        return XUSB_HW;
    if (xhci_wait_token(h, tok) != XHCI_OK)
        return XUSB_HW;
    if (h->tokens[tok].ccode != XHCI_CC_SUCCESS)
        return XUSB_FAILED_DEV;
    slot = h->last_slot;
    if (!slot || slot > h->max_slots_en)
        return XUSB_FAILED_DEV;
    dev->slot = slot;
    p->slot = slot;
    p->state = XUSB_SLOT;
    return XUSB_OK;
}

enum xusb_result xusb_disable_slot(struct xusb_dev *dev)
{
    struct xhci_hcd *h;
    cpu_u8 tok = 0xFFu;
    volatile cpu_u64 *dcb;
    if (!dev || !dev->bus || !dev->bus->hcd)
        return XUSB_INVALID;
    if (!dev->slot)
        return XUSB_OK;
    h = dev->bus->hcd;
    if (xhci_submit_cmd(h, 0, 0, 0,
            ((cpu_u32)XHCI_TRB_DIS_SLOT << XHCI_TRB_TYPE_SHIFT) |
            ((cpu_u32)dev->slot << XHCI_TRB_SLOT_SHIFT),
            &tok) != XHCI_OK)
        return XUSB_HW;
    if (xhci_wait_token(h, tok) != XHCI_OK)
        return XUSB_HW;
    if (h->tokens[tok].ccode != XHCI_CC_SUCCESS)
        return XUSB_FAILED_DEV;
    /* Quiesced: clear the controller-visible reference first. */
    dcb = (volatile cpu_u64 *)h->dcb.virt;
    dcb[dev->slot] = 0;
    dma_sync_for_device(&h->dcb);
    if (dev->port_idx < dev->bus->nports &&
        dev->bus->ports[dev->port_idx].slot == dev->slot)
        dev->bus->ports[dev->port_idx].slot = 0;
    dev->slot = 0;
    return XUSB_OK;
}

/* ---------- Contexts + EP0 ring ---------- */

enum xusb_result xusb_alloc_contexts(struct xusb_dev *dev)
{
    struct xhci_hcd *h;
    cpu_u64 max_bus;
    if (!dev || !dev->bus || !dev->bus->hcd)
        return XUSB_INVALID;
    h = dev->bus->hcd;
    if (h->caps.csz)
        return XUSB_UNSUPPORTED;
    if (dev->dctx.virt || dev->ictx.virt)
        return XUSB_STATE;
    max_bus = h->caps.ac64 ? DMA_ADDR_ANY : DMA_ADDR_32BIT;
    if (xusb_inject == XUSB_FAIL_DCTX)
        return XUSB_FAILED_DEV;
    if (dma_alloc(64, 64, max_bus, &dev->dctx) != DMA_OK)
        return XUSB_NOMEM;
    if (xusb_inject == XUSB_FAIL_ICTX) {
        dma_free(&dev->dctx);
        dev->dctx.virt = 0;
        dev->dctx.magic = 0;
        return XUSB_FAILED_DEV;
    }
    if (dma_alloc(96, 64, max_bus, &dev->ictx) != DMA_OK) {
        dma_free(&dev->dctx);
        dev->dctx.virt = 0;
        dev->dctx.magic = 0;
        return XUSB_NOMEM;
    }
    return XUSB_OK;
}

void xusb_free_contexts(struct xusb_dev *dev)
{
    if (!dev)
        return;
    if (dev->dctx.virt) {
        dma_free(&dev->dctx);
        dev->dctx.virt = 0;
        dev->dctx.magic = 0;
    }
    if (dev->ictx.virt) {
        dma_free(&dev->ictx);
        dev->ictx.virt = 0;
        dev->ictx.magic = 0;
    }
}

enum xusb_result xusb_ep0_ring_alloc(struct xusb_dev *dev)
{
    struct xhci_hcd *h;
    struct xhci_trb *ring;
    cpu_u64 max_bus;
    if (!dev || !dev->bus || !dev->bus->hcd)
        return XUSB_INVALID;
    if (xusb_inject == XUSB_FAIL_EP0RING)
        return XUSB_FAILED_DEV;
    h = dev->bus->hcd;
    if (dev->ep0.dma.virt)
        return XUSB_STATE;
    max_bus = h->caps.ac64 ? DMA_ADDR_ANY : DMA_ADDR_32BIT;
    if (dma_alloc(XUSB_EP0_TRBS * XHCI_TRB_SIZE, 64, max_bus, &dev->ep0.dma)
        != DMA_OK)
        return XUSB_NOMEM;
    dev->ep0.count = XUSB_EP0_TRBS;
    dev->ep0.enq = 0;
    dev->ep0.pcs = 1;
    dev->ep0.wraps = 0;
    ring = (struct xhci_trb *)dev->ep0.dma.virt;
    xhci_encode_link(&ring[XUSB_EP0_TRBS - 1], dev->ep0.dma.bus, 1);
    dma_sync_for_device(&dev->ep0.dma);
    return XUSB_OK;
}

void xusb_ep0_ring_free(struct xusb_dev *dev)
{
    if (!dev)
        return;
    if (dev->ep0.dma.virt) {
        dma_free(&dev->ep0.dma);
        dev->ep0.dma.virt = 0;
        dev->ep0.dma.magic = 0;
        dev->ep0.count = 0;
        dev->ep0.enq = 0;
    }
}

/* ---------- Address Device ---------- */

void xusb_slot_ctx_init(cpu_u32 *s, cpu_u8 speed_id, cpu_u32 port)
{
    /* Dword 0: route string (0: root-port device), speed, context
       entries = 1 (slot + EP0 valid). Dword 1: root hub port number. */
    s[0] = ((cpu_u32)speed_id << XHCI_SCTX_SPD_SHIFT) |
           ((cpu_u32)1u << XHCI_SCTX_ENT_SHIFT);
    s[1] = (cpu_u32)port << XHCI_SCTX_PORT_SHIFT;
    s[2] = 0;
    s[3] = 0;
    s[4] = 0;
    s[5] = 0;
    s[6] = 0;
    s[7] = 0;
}

void xusb_ep0_ctx_init(cpu_u32 *ep, cpu_u64 ring_bus, cpu_u16 mps)
{
    ep[0] = 0;
    ep[1] = ((cpu_u32)XHCI_EP_TYPE_CTRL << XHCI_ECTX_TYPE_SHIFT) |
            ((cpu_u32)3u << XHCI_ECTX_ERR_SHIFT) |
            ((cpu_u32)mps << XHCI_ECTX_MPS_SHIFT);
    ep[2] = (cpu_u32)(ring_bus & ~0xFu) | XHCI_ECTX_DCS;
    ep[3] = (cpu_u32)(ring_bus >> 32);
    ep[4] = 8u;
    ep[5] = 0;
    ep[6] = 0;
    ep[7] = 0;
}

enum xusb_result xusb_address_device(struct xusb_dev *dev, int bsr)
{
    struct xhci_hcd *h;
    struct xusb_port *p;
    volatile cpu_u32 *ictl;
    volatile cpu_u32 *is;
    volatile cpu_u32 *ie;
    volatile cpu_u64 *dcb;
    volatile cpu_u32 *os;
    volatile cpu_u32 *oe;
    cpu_u32 control;
    cpu_u8 tok = 0xFFu;
    if (!dev || !dev->bus || !dev->bus->hcd ||
        dev->port_idx >= dev->bus->nports)
        return XUSB_INVALID;
    if (xusb_inject == XUSB_FAIL_ADDR)
        return XUSB_FAILED_DEV;
    h = dev->bus->hcd;
    p = &dev->bus->ports[dev->port_idx];
    if (!dev->slot || !dev->dctx.virt || !dev->ictx.virt ||
        !dev->ep0.dma.virt || !dev->ep0_mps)
        return XUSB_STATE;
    if (p->state != XUSB_SLOT)
        return XUSB_STATE;
    p->state = XUSB_ADDRESSING;
    ictl = (volatile cpu_u32 *)dev->ictx.virt;
    ictl[0] = 0;
    ictl[1] = XHCI_ICTX_ADD_SLOT | XHCI_ICTX_ADD_EP0;
    is = ictl + 8;
    {
        cpu_u32 s[8];
        cpu_u32 e[8];
        cpu_u32 i;
        xusb_slot_ctx_init(s, p->speed_id, p->num);
        for (i = 0; i < 8; ++i)
            is[i] = s[i];
        xusb_ep0_ctx_init(e, dev->ep0.dma.bus, dev->ep0_mps);
        ie = ictl + 16;
        for (i = 0; i < 8; ++i)
            ie[i] = e[i];
    }
    dma_sync_for_device(&dev->ictx);
    dcb = (volatile cpu_u64 *)h->dcb.virt;
    dcb[dev->slot] = dev->dctx.bus;
    dma_sync_for_device(&h->dcb);
    control = ((cpu_u32)XHCI_TRB_ADDR_DEV << XHCI_TRB_TYPE_SHIFT) |
              ((cpu_u32)dev->slot << XHCI_TRB_SLOT_SHIFT) |
              (bsr ? XHCI_TRB_BSR : 0u);
    dev->addr_control = control;
    dma_sync_for_device(&dev->dctx);
    if (xhci_submit_cmd(h, (cpu_u32)dev->ictx.bus,
            (cpu_u32)(dev->ictx.bus >> 32), 0, control, &tok) != XHCI_OK) {
        p->state = XUSB_FAILED;
        return XUSB_HW;
    }
    if (xhci_wait_token(h, tok) != XHCI_OK ||
        h->tokens[tok].ccode != XHCI_CC_SUCCESS ||
        h->last_slot != dev->slot) {
        p->state = XUSB_FAILED;
        return XUSB_FAILED_DEV;
    }
    /* Validate the output Device Context; never trust success alone. */
    dma_sync_for_device(&dev->dctx);
    os = (volatile cpu_u32 *)dev->dctx.virt;
    oe = os + 8;
    if (((os[3] >> XHCI_SCTX_ST_SHIFT) & XHCI_SCTX_ST_MASK) !=
            XHCI_SLOTST_ADDRESSED ||
        !(os[3] & XHCI_SCTX_ADDR_MASK) ||
        ((os[1] >> XHCI_SCTX_PORT_SHIFT) & XHCI_SCTX_PORT_MASK) != p->num ||
        ((os[0] >> XHCI_SCTX_SPD_SHIFT) & XHCI_SCTX_SPD_MASK) != p->speed_id ||
        ((os[0] >> XHCI_SCTX_ENT_SHIFT) & XHCI_SCTX_ENT_MASK) != 1u ||
        (oe[0] & XHCI_ECTX_ST_MASK) != XHCI_EPST_RUNNING ||
        ((oe[2] & ~0xFu) != (cpu_u32)(dev->ep0.dma.bus & ~0xFu)) ||
        (oe[3] != (cpu_u32)(dev->ep0.dma.bus >> 32)) ||
        !(oe[2] & XHCI_ECTX_DCS)) {
        p->state = XUSB_FAILED;
        return XUSB_FAILED_DEV;
    }
    dev->addr = (cpu_u8)(os[3] & XHCI_SCTX_ADDR_MASK);
    p->state = XUSB_ADDRESSED;
    return XUSB_OK;
}

/* ---------- Evaluate Context (EP0 MPS update) ---------- */

enum xusb_result xusb_evaluate_ep0(struct xusb_dev *dev, cpu_u16 mps)
{
    struct xhci_hcd *h;
    volatile cpu_u32 *ictl;
    volatile cpu_u32 *ie;
    volatile cpu_u32 *os;
    volatile cpu_u32 *oe;
    cpu_u8 tok = 0xFFu;
    cpu_u32 i;
    if (!dev || !dev->bus || !dev->bus->hcd)
        return XUSB_INVALID;
    if (xusb_inject == XUSB_FAIL_EVAL)
        return XUSB_FAILED_DEV;
    h = dev->bus->hcd;
    if (!dev->slot || !dev->dctx.virt || !dev->ictx.virt || !mps)
        return XUSB_STATE;
    /* Input: Drop 0, Add EP0 only; EP0 ctx copied from the output
       with the new MPS. */
    ictl = (volatile cpu_u32 *)dev->ictx.virt;
    ictl[0] = 0;
    ictl[1] = XHCI_ICTX_ADD_EP0;
    dma_sync_for_device(&dev->dctx);
    os = (volatile cpu_u32 *)dev->dctx.virt;
    oe = os + 8;
    ie = ictl + 16;
    for (i = 0; i < 8; ++i)
        ie[i] = oe[i];
    ie[1] = (ie[1] & ~((cpu_u32)XHCI_ECTX_MPS_MASK << XHCI_ECTX_MPS_SHIFT)) |
            ((cpu_u32)mps << XHCI_ECTX_MPS_SHIFT);
    dma_sync_for_device(&dev->ictx);
    if (xhci_submit_cmd(h, (cpu_u32)dev->ictx.bus,
            (cpu_u32)(dev->ictx.bus >> 32), 0,
            ((cpu_u32)XHCI_TRB_EVAL_CTX << XHCI_TRB_TYPE_SHIFT) |
            ((cpu_u32)dev->slot << XHCI_TRB_SLOT_SHIFT),
            &tok) != XHCI_OK)
        return XUSB_HW;
    if (xhci_wait_token(h, tok) != XHCI_OK ||
        h->tokens[tok].ccode != XHCI_CC_SUCCESS ||
        h->last_slot != dev->slot)
        return XUSB_FAILED_DEV;
    dma_sync_for_device(&dev->dctx);
    if ((((volatile cpu_u32 *)dev->dctx.virt)[9] >> XHCI_ECTX_MPS_SHIFT) !=
        (mps & XHCI_ECTX_MPS_MASK))
        return XUSB_FAILED_DEV;
    dev->ep0_mps = mps;
    return XUSB_OK;
}

/* ---------- EP0 control transfers ---------- */

enum xusb_result xusb_control(struct xusb_dev *dev,
                              const struct usb_setup *setup,
                              cpu_u64 data_bus, cpu_u32 data_len, int dir_in)
{
    struct xhci_hcd *h;
    struct xhci_trb t;
    cpu_u64 status_bus = 0;
    cpu_u8 x = 0xFFu;
    cpu_u8 trt;
    if (!dev || !dev->bus || !dev->bus->hcd || !setup)
        return XUSB_INVALID;
    h = dev->bus->hcd;
    if (!dev->slot || !dev->ep0.dma.virt)
        return XUSB_STATE;
    if (!data_bus || !data_len || data_len > XHCI_TRB_LEN_MASK)
        return XUSB_INVALID;
    /* TD-atomicity invariant: 9 usable TRBs hold whole TDs of 3, so a
       TD never straddles the Link by arithmetic. Fail closed if the
       ring ever desynchronizes. */
    if (dev->ep0.enq % 3 != 0)
        return XUSB_STATE;
    trt = dir_in ? XHCI_TRB_TRT_IN : XHCI_TRB_TRT_OUT;
    if (!xhci_encode_setup(&t, setup->bytes, dev->ep0.pcs, trt))
        return XUSB_INVALID;
    if (xhci_ring_emit(&dev->ep0, &t, &status_bus) != XHCI_OK)
        return XUSB_STATE;
    if (!xhci_encode_data(&t, data_bus, data_len, dev->ep0.pcs, dir_in))
        return XUSB_INVALID;
    if (xhci_ring_emit(&dev->ep0, &t, &status_bus) != XHCI_OK)
        return XUSB_STATE;
    /* Status direction is opposite the data direction (IN data → OUT
       status per USB control semantics). */
    if (!xhci_encode_status(&t, dev->ep0.pcs, !dir_in))
        return XUSB_INVALID;
    if (xhci_ring_emit(&dev->ep0, &t, &status_bus) != XHCI_OK)
        return XUSB_STATE;
    dma_sync_for_device(&dev->ep0.dma);
    dma_wmb();
    if (xhci_xfer_submit(h, dev->slot, XHCI_DCI_EP0, status_bus, &x)
        != XHCI_OK)
        return XUSB_HW;
    if (xhci_doorbell(h, dev->slot, XHCI_DCI_EP0) != XHCI_OK) {
        xhci_xfer_release(h, x);
        return XUSB_HW;
    }
    if (xhci_xfer_wait(h, x) != XHCI_OK) {
        xhci_xfer_release(h, x);
        return XUSB_HW;
    }
    if (h->xfers[x].ccode != XHCI_CC_SUCCESS || h->xfers[x].resid != 0) {
        xhci_xfer_release(h, x);
        return XUSB_FAILED_DEV;
    }
    xhci_xfer_release(h, x);
    return XUSB_OK;
}

enum xusb_result xusb_get_descriptor(struct xusb_dev *dev, cpu_u8 type,
                                     cpu_u8 index, cpu_u64 data_bus,
                                     cpu_u16 wlen)
{
    struct usb_setup s;
    if (!dev || !wlen)
        return XUSB_INVALID;
    usb_setup_get_desc(&s, type, index, wlen);
    return xusb_control(dev, &s, data_bus, wlen, 1);
}

/* ---------- Full enumeration with rollback ---------- */

void xusb_dev_cleanup(struct xusb_dev *dev)
{
    if (!dev)
        return;
    /* Ordered: quiesce slot → clear refs → free ring → free contexts. */
    xusb_disable_slot(dev);
    xusb_ep0_ring_free(dev);
    xusb_free_contexts(dev);
    dev->ep0_mps = 0;
    dev->addr = 0;
}

#define DESC_DMA_LEN 64u
#define DESC_CANARY 0xC0DECAFEu

enum xusb_result xusb_enumerate(struct xusb_dev *dev, cpu_u32 port_idx,
                                enum xusb_fail_at fail_at,
                                cpu_u8 desc_out[USB_DT_DEVICE_SIZE])
{
    struct xusb_bus *bus;
    struct xhci_hcd *h;
    struct xusb_port *p;
    struct dma_buffer db;
    volatile cpu_u8 *dp;
    volatile cpu_u32 *pre, *post;
    struct usb_device_desc parsed;
    enum xusb_result r;
    cpu_u32 i;
    cpu_u64 max_bus;
    cpu_u16 mps0;
    if (!dev || !desc_out)
        return XUSB_INVALID;
    bus = dev->bus;
    if (!bus || !bus->hcd || port_idx >= bus->nports)
        return XUSB_INVALID;
    h = bus->hcd;
    p = &bus->ports[port_idx];
    dev->port_idx = port_idx;
    dev->slot = 0;
    dev->addr = 0;
    dev->dctx.virt = 0;
    dev->dctx.magic = 0;
    dev->ictx.virt = 0;
    dev->ictx.magic = 0;
    dev->ep0.dma.virt = 0;
    dev->ep0.dma.magic = 0;
    dev->ep0_mps = 0;
    dev->addr_control = 0;
    db.virt = 0;
    db.magic = 0;
    xusb_inject = fail_at;
    max_bus = h->caps.ac64 ? DMA_ADDR_ANY : DMA_ADDR_32BIT;

    r = xusb_port_reset(bus, port_idx);
    if (r != XUSB_OK)
        goto fail;
    r = xusb_enable_slot(dev);
    if (r != XUSB_OK)
        goto fail;
    r = xusb_alloc_contexts(dev);
    if (r != XUSB_OK)
        goto fail;
    r = xusb_ep0_ring_alloc(dev);
    if (r != XUSB_OK)
        goto fail;
    mps0 = usb_ep0_initial_mps((enum usb_speed)p->speed);
    if (!mps0) {
        r = XUSB_UNSUPPORTED;
        goto fail;
    }
    dev->ep0_mps = mps0;
    /* The xHC performs SET_ADDRESS itself (BSR=0); software must
       never issue a second SET_ADDRESS after this. */
    r = xusb_address_device(dev, 0);
    if (r != XUSB_OK)
        goto fail;
    p->state = XUSB_EP0READY;
    if (dma_alloc(DESC_DMA_LEN + 8, 64, max_bus, &db) != DMA_OK) {
        r = XUSB_NOMEM;
        goto fail;
    }
    dp = (volatile cpu_u8 *)db.virt;
    pre = (volatile cpu_u32 *)db.virt;
    post = (volatile cpu_u32 *)((volatile cpu_u8 *)db.virt + DESC_DMA_LEN + 4);
    *pre = DESC_CANARY;
    *post = DESC_CANARY;
    dp += 4;
    if (xusb_inject == XUSB_FAIL_DESC8) {
        r = XUSB_FAILED_DEV;
        goto fail;
    }
    r = xusb_get_descriptor(dev, USB_DT_DEVICE, 0, db.bus + 4, 8);
    if (r != XUSB_OK)
        goto fail;
    if (*pre != DESC_CANARY || *post != DESC_CANARY) {
        r = XUSB_FAILED_DEV;
        goto fail;
    }
    /* First 8 bytes are enough to learn bMaxPacketSize0. */
    if (dp[0] < 8 || dp[1] != USB_DT_DEVICE) {
        r = XUSB_FAILED_DEV;
        goto fail;
    }
    if (p->speed == USB_SPEED_HIGH || p->speed == USB_SPEED_SUPER ||
        p->speed == USB_SPEED_SUPERPLUS) {
        /* Fixed-MPS speeds: the descriptor must agree. */
        if (dp[7] != (cpu_u8)mps0) {
            r = XUSB_FAILED_DEV;
            goto fail;
        }
        /* Live Evaluate-Context proof with identical values. */
        r = xusb_evaluate_ep0(dev, mps0);
        if (r != XUSB_OK)
            goto fail;
    } else if (usb_ep0_mps_needs_update((enum usb_speed)p->speed,
                                        (cpu_u8)mps0, dp[7])) {
        r = xusb_evaluate_ep0(dev, dp[7]);
        if (r != XUSB_OK)
            goto fail;
    }
    p->state = XUSB_DESC8;
    if (xusb_inject == XUSB_FAIL_DESC18) {
        r = XUSB_FAILED_DEV;
        goto fail;
    }
    r = xusb_get_descriptor(dev, USB_DT_DEVICE, 0, db.bus + 4,
                            USB_DT_DEVICE_SIZE);
    if (r != XUSB_OK)
        goto fail;
    if (*pre != DESC_CANARY || *post != DESC_CANARY) {
        r = XUSB_FAILED_DEV;
        goto fail;
    }
    {
        cpu_u8 raw[USB_DT_DEVICE_SIZE];
        for (i = 0; i < USB_DT_DEVICE_SIZE; ++i)
            raw[i] = dp[i];
        if (usb_parse_device_desc(raw, USB_DT_DEVICE_SIZE, &parsed)
            != USB_OK) {
            r = XUSB_FAILED_DEV;
            goto fail;
        }
        for (i = 0; i < USB_DT_DEVICE_SIZE; ++i)
            desc_out[i] = raw[i];
    }
    p->state = XUSB_DESCDONE;
    dma_free(&db);
    xusb_inject = XUSB_FAIL_NONE;
    return XUSB_OK;

fail:
    if (db.virt) {
        dma_free(&db);
        db.virt = 0;
        db.magic = 0;
    }
    xusb_dev_cleanup(dev);
    if (p->state != XUSB_FAILED)
        p->state = XUSB_FAILED;
    xusb_inject = XUSB_FAIL_NONE;
    return r == XUSB_OK ? XUSB_FAILED_DEV : r;
}
