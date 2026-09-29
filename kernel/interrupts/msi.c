/* INT-A2: PCI capability walker + MSI/MSI-X transports + generic PCI IRQ API.
 *
 * Foreground-only (IF=0, never in IRQ context) except msi_quiet_route.
 * Static bounded state: 8 handles x 32 vectors. Fail closed everywhere:
 * malformed lists, insane geometry, and readback mismatches refuse with
 * a sticky reason; partial progress always rolls back. Layout authority:
 * docs/design/msi.md §1.
 */
#include "msi.h"
#include "io.h"

static const char *error = "none";

const char *msi_error(void) { return error; }

static enum msi_result fail(enum msi_result code, const char *reason)
{
    error = reason;
    return code;
}

static int bdf_ok(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    return bus <= PCI_MAX_BUS && dev <= PCI_MAX_DEVICE && fn <= PCI_MAX_FUNCTION;
}

static cpu_u32 bdf_pack(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    return (bus << 16) | (dev << 8) | fn;
}

/* ---------- capability walker (§4) ---------- */

enum msi_result msi_walk_caps(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                              struct msi_walk *out)
{
    cpu_u16 status;
    cpu_u32 off;
    cpu_u32 iter;
    cpu_u32 seen_lo = 0; /* visited bits for offsets 0x40..0xBC */
    cpu_u32 seen_hi = 0; /* visited bits for offsets 0xC0..0xFC */
    if (!out) return fail(MSI_INVALID, "walk-null");
    out->has_msi = 0;
    out->has_msix = 0;
    out->msi_off = 0;
    out->msix_off = 0;
    out->malformed = 0;
    if (!bdf_ok(bus, dev, fn)) return fail(MSI_INVALID, "walk-bdf");
    /* Absent functions read all-ones; that is no caps, not an error. */
    if (pci_cfg_read16(bus, dev, fn, PCI_CFG_VENDOR_ID) == PCI_VENDOR_ABSENT)
        return MSI_OK;
    status = pci_cfg_read16(bus, dev, fn, PCI_CFG_STATUS);
    if (!(status & PCI_STATUS_CAP_LIST)) {
        out->malformed = 1; /* benign: firmware says no list */
        return MSI_OK;
    }
    off = pci_cfg_read8(bus, dev, fn, PCI_CFG_CAPABILITY_LIST);
    for (iter = 0; iter < 64u; ++iter) {
        cpu_u32 id;
        cpu_u32 next;
        cpu_u32 bit;
        if (off == 0) return MSI_OK; /* clean termination */
        if (off < 0x40u) {
            out->malformed = 2;
            return MSI_OK;
        }
        if (off > 0xFCu) {
            out->malformed = 3;
            return MSI_OK;
        }
        if (off & 3u) {
            out->malformed = 4;
            return MSI_OK;
        }
        /* Cycle detection over the 48 visitable offsets. */
        bit = (off - 0x40u) / 4u;
        if (bit < 32u) {
            if (seen_lo & (1u << bit)) {
                out->malformed = 5;
                return MSI_OK;
            }
            seen_lo |= 1u << bit;
        } else {
            if (seen_hi & (1u << (bit - 32u))) {
                out->malformed = 5;
                return MSI_OK;
            }
            seen_hi |= 1u << (bit - 32u);
        }
        id = pci_cfg_read8(bus, dev, fn, off);
        next = pci_cfg_read8(bus, dev, fn, off + 1u);
        /* First capability of each kind wins (Linux pci_find_capability);
           duplicates are ignored, unknown IDs skipped, never parsed. */
        if (id == MSI_CAP_ID_MSI && !out->has_msi) {
            out->has_msi = 1;
            out->msi_off = (cpu_u8)off;
        } else if (id == MSI_CAP_ID_MSIX && !out->has_msix) {
            out->has_msix = 1;
            out->msix_off = (cpu_u8)off;
        }
        off = next;
    }
    out->malformed = 6; /* backstop: unreachable with cycle detection */
    return MSI_OK;
}

/* ---------- MSI parse (§8) ---------- */

enum msi_result msi_parse_msi(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                              cpu_u8 cap, struct msi_desc *out)
{
    cpu_u16 ctrl;
    if (!out) return fail(MSI_INVALID, "msi-null");
    if (!bdf_ok(bus, dev, fn)) return fail(MSI_INVALID, "msi-bdf");
    if (cap < 0x40u || cap > 0xFCu || (cap & 3u))
        return fail(MSI_INVALID, "msi-cap");
    if (pci_cfg_read8(bus, dev, fn, cap) != MSI_CAP_ID_MSI)
        return fail(MSI_ABSENT, "msi-id");
    ctrl = pci_cfg_read16(bus, dev, fn, cap + MSI_MSG_CTRL);
    out->cap = cap;
    out->mmc = (cpu_u8)((ctrl & MSI_CTRL_QMASK) >> 1);
    out->mme = (cpu_u8)((ctrl & MSI_CTRL_QSIZE) >> 4);
    out->is64 = (ctrl & MSI_CTRL_64BIT) ? 1 : 0;
    out->maskbit = (ctrl & MSI_CTRL_MASKBIT) ? 1 : 0;
    out->enabled = (ctrl & MSI_CTRL_ENABLE) ? 1 : 0;
    /* Lengths: 32-bit 10/20, 64-bit 14/24 (mask appends 10 bytes:
       32-bit mask+pending at +0x0C, 64-bit data shifts to +0x0C). */
    out->len = (cpu_u8)(10u + (out->is64 ? 4u : 0u) +
                        (out->maskbit ? 10u : 0u));
    if ((cpu_u32)cap + out->len > PCI_CFG_SPACE_BYTES)
        return fail(MSI_RANGE, "msi-len");
    if (out->mmc > 5u) return fail(MSI_HW, "msi-mmc");
    if (out->mme > out->mmc) return fail(MSI_HW, "msi-mme");
    return MSI_OK;
}

/* ---------- MSI-X parse (§10) ---------- */

static int msix_entry_for_bir(const struct pci_device *d, cpu_u32 bir,
                              cpu_u8 *entry)
{
    cpu_u8 e;
    /* BIR names the BAR's start slot exactly; pointing into the upper
       half of a 64-bit BAR is refused (no QEMU device does it). */
    for (e = 0; e < d->bar_count; ++e)
        if (d->bars[e].index == bir) {
            *entry = e;
            return 1;
        }
    return 0;
}

enum msi_result msi_parse_msix(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                               cpu_u8 cap, struct msix_geo *out)
{
    cpu_u16 ctrl;
    cpu_u16 cmd;
    cpu_u32 tbl;
    cpu_u32 pba;
    cpu_u64 tbl_bytes;
    cpu_u64 pba_bytes;
    cpu_u64 tbl_end;
    cpu_u64 pba_end;
    const struct pci_device *d;
    if (!out) return fail(MSI_INVALID, "msix-null");
    if (!bdf_ok(bus, dev, fn)) return fail(MSI_INVALID, "msix-bdf");
    if (cap < 0x40u || cap > 0xFCu || (cap & 3u))
        return fail(MSI_INVALID, "msix-cap");
    if ((cpu_u32)cap + 12u > PCI_CFG_SPACE_BYTES)
        return fail(MSI_RANGE, "msix-len");
    if (pci_cfg_read8(bus, dev, fn, cap) != MSI_CAP_ID_MSIX)
        return fail(MSI_ABSENT, "msix-id");
    /* Enabling address decoding is the driver's job; the IRQ layer
       only validates it (live COMMAND, never the enum snapshot). */
    cmd = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
    if (!(cmd & PCI_COMMAND_MEM)) return fail(MSI_REFUSED, "mem-disabled");
    ctrl = pci_cfg_read16(bus, dev, fn, cap + MSIX_MSG_CTRL);
    tbl = pci_cfg_read32(bus, dev, fn, cap + MSIX_TABLE_REG);
    pba = pci_cfg_read32(bus, dev, fn, cap + MSIX_PBA_REG);
    out->cap = cap;
    out->nvec = (cpu_u16)((ctrl & MSIX_CTRL_QSIZE) + 1u);
    out->tbl_bir = (cpu_u8)(tbl & MSIX_REG_BIR);
    out->pba_bir = (cpu_u8)(pba & MSIX_REG_BIR);
    out->tbl_off = tbl & MSIX_REG_OFFSET;
    out->pba_off = pba & MSIX_REG_OFFSET;
    out->enabled = (ctrl & MSIX_CTRL_ENABLE) ? 1 : 0;
    out->masked_all = (ctrl & MSIX_CTRL_MASKALL) ? 1 : 0;
    if (out->tbl_bir > 5u || out->pba_bir > 5u)
        return fail(MSI_RANGE, "msix-bir");
    d = pci_find_bdf(bus, dev, fn);
    if (!d) return fail(MSI_ABSENT, "not-enumerated");
    if (!msix_entry_for_bir(d, out->tbl_bir, &out->tbl_entry))
        return fail(MSI_RANGE, "msix-tbl-bar");
    if (!msix_entry_for_bir(d, out->pba_bir, &out->pba_entry))
        return fail(MSI_RANGE, "msix-pba-bar");
    if (d->bars[out->tbl_entry].kind == PCI_BAR_IO ||
        d->bars[out->pba_entry].kind == PCI_BAR_IO)
        return fail(MSI_RANGE, "msix-io-bar");
    if (d->bars[out->tbl_entry].size == 0 || d->bars[out->pba_entry].size == 0)
        return fail(MSI_RANGE, "msix-zero-bar");
    tbl_bytes = (cpu_u64)out->nvec * MSIX_ENTRY_SIZE;
    pba_bytes = ((cpu_u64)out->nvec + 7u) / 8u;
    tbl_end = (cpu_u64)out->tbl_off + tbl_bytes;
    pba_end = (cpu_u64)out->pba_off + pba_bytes;
    if (tbl_end > d->bars[out->tbl_entry].size)
        return fail(MSI_RANGE, "msix-tbl-bounds");
    if (pba_end > d->bars[out->pba_entry].size)
        return fail(MSI_RANGE, "msix-pba-bounds");
    /* Same BAR: table and PBA must not overlap (conservative). */
    if (out->tbl_entry == out->pba_entry &&
        (cpu_u64)out->tbl_off < pba_end && (cpu_u64)out->pba_off < tbl_end)
        return fail(MSI_REFUSED, "msix-overlap");
    return MSI_OK;
}

/* ---------- message builder (§18) ---------- */

enum msi_result msi_build_message(cpu_u32 apic_id, unsigned int vector,
                                  struct msi_msg *out)
{
    if (!out) return fail(MSI_INVALID, "msg-null");
    if (vector > 255u) return fail(MSI_INVALID, "msg-vector");
    /* xAPIC physical: IDs above 255 refuse, never truncate. */
    if (apic_id > 0xFFu) return fail(MSI_REFUSED, "msg-apic-id");
    out->addr = MSI_ADDR_BASE | (apic_id << MSI_ADDR_DEST_SHIFT);
    out->hi = 0;
    /* Fixed delivery, level 0, edge trigger: all zero (Linux parity). */
    out->data = (cpu_u32)(vector & MSI_DATA_VECTOR_MASK);
    return MSI_OK;
}

/* ---------- handle table + enable/disable (§31-33) ---------- */

struct msi_handle {
    int used;
    cpu_u32 kind; /* enum msi_kind */
    cpu_u32 bus;
    cpu_u32 dev;
    cpu_u32 fn;
    cpu_u8 cap;
    cpu_u32 nvec;
    unsigned int vector[MSI_MAX_VECTORS];
    int did_intx;  /* we set INTX_DISABLE; teardown restores it */
    cpu_u8 is64;   /* MSI only */
    cpu_u8 maskbit; /* MSI only */
    cpu_u8 mmc;    /* MSI only: log2(max vectors), for mask readback */
    cpu_u8 tbl_entry; /* MSI-X only: decoded-entry ordinals */
    cpu_u8 pba_entry;
    cpu_u32 tbl_off; /* MSI-X only: table byte offset in its BAR */
    cpu_u64 tbl_va; /* MSI-X only: mapped table BAR base (0 unmapped) */
    cpu_u64 pba_va; /* MSI-X only: mapped PBA BAR base (0 unmapped) */
    int tbl_mapped_pre; /* BAR already mapped: borrowed, never unmapped */
    int pba_mapped_pre;
};

static struct msi_handle handles[MSI_MAX_HANDLES];

static unsigned int log2_pow2(unsigned int n)
{
    unsigned int l = 0;
    while (n > 1u) {
        n >>= 1;
        ++l;
    }
    return l;
}

static int handle_live(cpu_u32 handle)
{
    return handle < MSI_MAX_HANDLES && handles[handle].used;
}

static int function_busy(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    cpu_u32 h;
    for (h = 0; h < MSI_MAX_HANDLES; ++h)
        if (handles[h].used && handles[h].bus == bus &&
            handles[h].dev == dev && handles[h].fn == fn)
            return 1;
    return 0;
}

/* The other capability must not be live on this function (PCI forbids
   MSI and MSI-X ENABLE together; we refuse even if hardware allowed).
   Raw ENABLE-bit reads: no geometry validation, so a MEM-disabled or
   otherwise insane sibling still blocks (fail closed). */
static int other_enabled(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                         const struct msi_walk *w, int want_msix)
{
    if (want_msix && w->has_msi) {
        cpu_u16 ctrl = pci_cfg_read16(bus, dev, fn,
                                      (cpu_u32)w->msi_off + MSI_MSG_CTRL);
        if (ctrl & MSI_CTRL_ENABLE) return 1;
    }
    if (!want_msix && w->has_msix) {
        cpu_u16 ctrl = pci_cfg_read16(bus, dev, fn,
                                      (cpu_u32)w->msix_off + MSIX_MSG_CTRL);
        if (ctrl & MSIX_CTRL_ENABLE) return 1;
    }
    return 0;
}

static cpu_u32 msi_mask_off(const struct msi_desc *d)
{
    return (cpu_u32)d->cap + (d->is64 ? MSI_MASK_64 : MSI_MASK_32);
}

static cpu_u32 msi_data_off(const struct msi_desc *d)
{
    return (cpu_u32)d->cap + (d->is64 ? MSI_DATA_64 : MSI_DATA_32);
}

/* Roll back a half-built MSI registration: device first (ENABLE off,
   INTx restored when we set it), then routes, vectors, handle.
   Best-effort device writes: we are already failing, and clearing a
   clear bit is harmless. */
static void msi_unwind(cpu_u32 h, unsigned int routes_done, int enabled)
{
    unsigned int i;
    struct msi_handle *hh = &handles[h];
    if (enabled) {
        cpu_u16 ctrl = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                      (cpu_u32)hh->cap + MSI_MSG_CTRL);
        pci_cfg_write16(hh->bus, hh->dev, hh->fn,
                        (cpu_u32)hh->cap + MSI_MSG_CTRL,
                        (cpu_u16)(ctrl & ~MSI_CTRL_ENABLE));
    }
    if (hh->did_intx) {
        cpu_u16 cmd = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                     PCI_CFG_COMMAND);
        pci_cfg_write16(hh->bus, hh->dev, hh->fn, PCI_CFG_COMMAND,
                        (cpu_u16)(cmd & ~PCI_COMMAND_INTX_DISABLE));
        hh->did_intx = 0;
    }
    for (i = 0; i < routes_done; ++i)
        (void)apic_route_msi_unregister(hh->vector[i]);
    for (i = 0; i < hh->nvec; ++i)
        (void)apic_vector_release(hh->vector[i]);
    hh->used = 0;
}

enum msi_result pci_irq_enable_msi(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                                   unsigned int nvec, irq_handler handler,
                                   void *opaque, cpu_u32 *handle,
                                   unsigned int *vectors)
{
    struct msi_walk w;
    struct msi_desc d;
    struct msi_msg msg;
    cpu_u16 cmd;
    cpu_u16 ctrl;
    cpu_u32 h = MSI_MAX_HANDLES;
    cpu_u32 i;
    int base;
    unsigned int order;
    unsigned int routes_done = 0;
    cpu_u32 data_off;
    if (!handler || !handle || !vectors)
        return fail(MSI_INVALID, "msi-arg");
    if (nvec == 0 || nvec > MSI_MAX_VECTORS || (nvec & (nvec - 1)) != 0)
        return fail(MSI_INVALID, "msi-nvec");
    if (!cpu_interrupts_disabled() || irq_in_context())
        return fail(MSI_STATE, "context");
    if (!apic_active()) return fail(MSI_STATE, "no-apic");
    if (!bdf_ok(bus, dev, fn)) return fail(MSI_INVALID, "msi-bdf");
    if (function_busy(bus, dev, fn)) return fail(MSI_STATE, "fn-busy");
    {
        enum msi_result rc = msi_walk_caps(bus, dev, fn, &w);
        if (rc != MSI_OK) return rc;
    }
    if (!w.has_msi) return fail(MSI_ABSENT, "no-msi-cap");
    {
        enum msi_result rc = msi_parse_msi(bus, dev, fn, w.msi_off, &d);
        if (rc != MSI_OK) return rc;
    }
    if (d.enabled) {
        /* Firmware left it live: shut it off, refuse, let the caller
           retry into a known-quiet device (D14: never adopt). */
        ctrl = pci_cfg_read16(bus, dev, fn, (cpu_u32)d.cap + MSI_MSG_CTRL);
        pci_cfg_write16(bus, dev, fn, (cpu_u32)d.cap + MSI_MSG_CTRL,
                        (cpu_u16)(ctrl & ~MSI_CTRL_ENABLE));
        return fail(MSI_STATE, "already-enabled");
    }
    if (other_enabled(bus, dev, fn, &w, 0))
        return fail(MSI_STATE, "msix-live");
    if (nvec > (1u << d.mmc)) return fail(MSI_REFUSED, "over-mmc");
    /* MSI is a bus-mastered memory write (QEMU drops it via
       bus_master_as without BME, per the PCI spec). Enabling
       delivery the device cannot perform would be a lie; the driver
       sets Bus Master first (we never flip it: DMA enable is a
       device-global privilege decision, like MEM in D4). */
    cmd = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
    if (!(cmd & PCI_COMMAND_BUS_MASTER))
        return fail(MSI_REFUSED, "no-busmaster");
    if (apic_bsp_id() > 0xFFu) return fail(MSI_REFUSED, "dest-id");
    for (i = 0; i < MSI_MAX_HANDLES; ++i)
        if (!handles[i].used) {
            h = i;
            break;
        }
    if (h == MSI_MAX_HANDLES) return fail(MSI_NOMEM, "no-handle");
    base = apic_vector_alloc_aligned(nvec);
    if (base < 0) return fail(MSI_NOMEM, "no-vectors");
    if (msi_build_message(apic_bsp_id(), (unsigned int)base, &msg) != MSI_OK) {
        for (i = 0; i < nvec; ++i)
            (void)apic_vector_release((unsigned int)base + i);
        return MSI_REFUSED;
    }
    order = log2_pow2(nvec);
    data_off = msi_data_off(&d);
    handles[h].used = 1;
    handles[h].kind = MSI_KIND_MSI;
    handles[h].bus = bus;
    handles[h].dev = dev;
    handles[h].fn = fn;
    handles[h].cap = d.cap;
    handles[h].nvec = nvec;
    handles[h].did_intx = 0;
    handles[h].is64 = d.is64;
    handles[h].maskbit = d.maskbit;
    handles[h].mmc = d.mmc;
    for (i = 0; i < nvec; ++i) handles[h].vector[i] = (unsigned int)base + i;
    /* Mask-then-configure: all vectors masked before ENABLE can fire. */
    if (d.maskbit) {
        pci_cfg_write32(bus, dev, fn, msi_mask_off(&d), 0xFFFFFFFFu);
        if (pci_cfg_read32(bus, dev, fn, msi_mask_off(&d)) !=
            (0xFFFFFFFFu >> (MSI_VECTORS_MAX - (1u << d.mmc)))) {
            msi_unwind(h, 0, 0);
            return fail(MSI_HW, "msi-mask-w");
        }
    }
    /* Address, data (low order bits zero: the device ORs the index). */
    pci_cfg_write32(bus, dev, fn, (cpu_u32)d.cap + MSI_ADDR_LO, msg.addr);
    if (pci_cfg_read32(bus, dev, fn, (cpu_u32)d.cap + MSI_ADDR_LO) != msg.addr) {
        msi_unwind(h, 0, 0);
        return fail(MSI_HW, "msi-addr-w");
    }
    if (d.is64) {
        pci_cfg_write32(bus, dev, fn, (cpu_u32)d.cap + MSI_ADDR_HI, msg.hi);
        if (pci_cfg_read32(bus, dev, fn, (cpu_u32)d.cap + MSI_ADDR_HI) !=
            msg.hi) {
            msi_unwind(h, 0, 0);
            return fail(MSI_HW, "msi-hi-w");
        }
    }
    pci_cfg_write16(bus, dev, fn, data_off,
                    (cpu_u16)(msg.data & ~((1u << order) - 1u)));
    if (pci_cfg_read16(bus, dev, fn, data_off) !=
        (cpu_u16)(msg.data & ~((1u << order) - 1u))) {
        msi_unwind(h, 0, 0);
        return fail(MSI_HW, "msi-data-w");
    }
    /* MME, then routes, then INTx off, then ENABLE: nothing can fire
       into an unrouted vector, and INTx dies before MSI lives. */
    ctrl = pci_cfg_read16(bus, dev, fn, (cpu_u32)d.cap + MSI_MSG_CTRL);
    pci_cfg_write16(bus, dev, fn, (cpu_u32)d.cap + MSI_MSG_CTRL,
                    (cpu_u16)((ctrl & ~MSI_CTRL_QSIZE) | (order << 4)));
    ctrl = pci_cfg_read16(bus, dev, fn, (cpu_u32)d.cap + MSI_MSG_CTRL);
    if (((ctrl & MSI_CTRL_QSIZE) >> 4) != order) {
        msi_unwind(h, 0, 0);
        return fail(MSI_HW, "msi-mme-w");
    }
    for (i = 0; i < nvec; ++i) {
        if (apic_route_msi_register(APIC_ROUTE_MSI, bdf_pack(bus, dev, fn),
                                    i, handles[h].vector[i], handler,
                                    opaque) != APIC_OK) {
            msi_unwind(h, routes_done, 0);
            return fail(MSI_NOMEM, "msi-route");
        }
        ++routes_done;
    }
    cmd = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
    if (!(cmd & PCI_COMMAND_INTX_DISABLE)) {
        pci_cfg_write16(bus, dev, fn, PCI_CFG_COMMAND,
                        (cpu_u16)(cmd | PCI_COMMAND_INTX_DISABLE));
        if (!(pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND) &
              PCI_COMMAND_INTX_DISABLE)) {
            msi_unwind(h, routes_done, 0);
            return fail(MSI_HW, "msi-intx-w");
        }
        handles[h].did_intx = 1;
    }
    pci_cfg_write16(bus, dev, fn, (cpu_u32)d.cap + MSI_MSG_CTRL,
                    (cpu_u16)(ctrl | MSI_CTRL_ENABLE));
    if (!(pci_cfg_read16(bus, dev, fn, (cpu_u32)d.cap + MSI_MSG_CTRL) &
          MSI_CTRL_ENABLE)) {
        msi_unwind(h, routes_done, 1);
        return fail(MSI_HW, "msi-enable-w");
    }
    if (d.maskbit) {
        pci_cfg_write32(bus, dev, fn, msi_mask_off(&d), 0);
        if (pci_cfg_read32(bus, dev, fn, msi_mask_off(&d)) != 0) {
            msi_unwind(h, routes_done, 1);
            return fail(MSI_HW, "msi-unmask-w");
        }
    }
    *handle = h;
    for (i = 0; i < nvec; ++i) vectors[i] = handles[h].vector[i];
    return MSI_OK;
}

/* ---------- MSI-X enable ---------- */

static void msix_unwind(cpu_u32 h, unsigned int routes_done, int enabled)
{
    unsigned int i;
    struct msi_handle *hh = &handles[h];
    if (enabled) {
        cpu_u16 ctrl = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                      (cpu_u32)hh->cap + MSIX_MSG_CTRL);
        pci_cfg_write16(hh->bus, hh->dev, hh->fn,
                        (cpu_u32)hh->cap + MSIX_MSG_CTRL,
                        (cpu_u16)(ctrl & ~MSIX_CTRL_ENABLE));
    } else {
        /* Never went live: drop the bulk mask we raised. */
        cpu_u16 ctrl = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                      (cpu_u32)hh->cap + MSIX_MSG_CTRL);
        pci_cfg_write16(hh->bus, hh->dev, hh->fn,
                        (cpu_u32)hh->cap + MSIX_MSG_CTRL,
                        (cpu_u16)(ctrl & ~MSIX_CTRL_MASKALL));
    }
    if (hh->did_intx) {
        cpu_u16 cmd = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                     PCI_CFG_COMMAND);
        pci_cfg_write16(hh->bus, hh->dev, hh->fn, PCI_CFG_COMMAND,
                        (cpu_u16)(cmd & ~PCI_COMMAND_INTX_DISABLE));
        hh->did_intx = 0;
    }
    for (i = 0; i < routes_done; ++i)
        (void)apic_route_msi_unregister(hh->vector[i]);
    if (hh->tbl_va && !hh->tbl_mapped_pre)
        (void)pci_unmap_bar(hh->bus, hh->dev, hh->fn, hh->tbl_entry);
    if (hh->pba_va && hh->pba_entry != hh->tbl_entry && !hh->pba_mapped_pre)
        (void)pci_unmap_bar(hh->bus, hh->dev, hh->fn, hh->pba_entry);
    for (i = 0; i < hh->nvec; ++i)
        (void)apic_vector_release(hh->vector[i]);
    hh->used = 0;
}

static volatile cpu_u32 *msix_entry_ptr(cpu_u64 tbl_va, cpu_u32 tbl_off,
                                        unsigned int index)
{
    return (volatile cpu_u32 *)(tbl_va + (cpu_u64)tbl_off +
                                (cpu_u64)index * MSIX_ENTRY_SIZE);
}

enum msi_result pci_irq_enable_msix(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                                    unsigned int nvec, irq_handler handler,
                                    void *opaque, cpu_u32 *handle,
                                    unsigned int *vectors)
{
    struct msi_walk w;
    struct msix_geo g;
    const struct pci_device *d;
    cpu_u16 cmd;
    cpu_u16 ctrl;
    cpu_u32 h = MSI_MAX_HANDLES;
    cpu_u32 i;
    unsigned int routes_done = 0;
    if (!handler || !handle || !vectors)
        return fail(MSI_INVALID, "msix-arg");
    if (nvec == 0 || nvec > MSI_MAX_VECTORS)
        return fail(MSI_INVALID, "msix-nvec");
    if (!cpu_interrupts_disabled() || irq_in_context())
        return fail(MSI_STATE, "context");
    if (!apic_active()) return fail(MSI_STATE, "no-apic");
    if (!bdf_ok(bus, dev, fn)) return fail(MSI_INVALID, "msix-bdf");
    if (function_busy(bus, dev, fn)) return fail(MSI_STATE, "fn-busy");
    {
        enum msi_result rc = msi_walk_caps(bus, dev, fn, &w);
        if (rc != MSI_OK) return rc;
    }
    if (!w.has_msix) return fail(MSI_ABSENT, "no-msix-cap");
    {
        enum msi_result rc = msi_parse_msix(bus, dev, fn, w.msix_off, &g);
        if (rc != MSI_OK) return rc;
    }
    if (g.enabled) {
        ctrl = pci_cfg_read16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL);
        pci_cfg_write16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL,
                        (cpu_u16)(ctrl & ~MSIX_CTRL_ENABLE));
        return fail(MSI_STATE, "already-enabled");
    }
    if (other_enabled(bus, dev, fn, &w, 1))
        return fail(MSI_STATE, "msi-live");
    if (nvec > g.nvec) return fail(MSI_REFUSED, "over-table");
    /* Same bus-master rule as MSI (MSI-X posts through
       bus_master_as too). */
    cmd = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
    if (!(cmd & PCI_COMMAND_BUS_MASTER))
        return fail(MSI_REFUSED, "no-busmaster");
    if (apic_bsp_id() > 0xFFu) return fail(MSI_REFUSED, "dest-id");
    for (i = 0; i < MSI_MAX_HANDLES; ++i)
        if (!handles[i].used) {
            h = i;
            break;
        }
    if (h == MSI_MAX_HANDLES) return fail(MSI_NOMEM, "no-handle");
    for (i = 0; i < nvec; ++i) {
        int v = apic_vector_alloc();
        if (v < 0) {
            unsigned int j;
            for (j = 0; j < i; ++j)
                (void)apic_vector_release(handles[h].vector[j]);
            return fail(MSI_NOMEM, "no-vectors");
        }
        handles[h].vector[i] = (unsigned int)v;
    }
    handles[h].used = 1;
    handles[h].kind = MSI_KIND_MSIX;
    handles[h].bus = bus;
    handles[h].dev = dev;
    handles[h].fn = fn;
    handles[h].cap = g.cap;
    handles[h].nvec = nvec;
    handles[h].did_intx = 0;
    handles[h].tbl_entry = g.tbl_entry;
    handles[h].pba_entry = g.pba_entry;
    handles[h].tbl_off = g.tbl_off;
    handles[h].tbl_va = 0;
    handles[h].pba_va = 0;
    /* A BAR the driver already mapped is borrowed, never unmapped by us. */
    d = pci_find_bdf(bus, dev, fn);
    if (!d) {
        msix_unwind(h, 0, 0);
        return fail(MSI_ABSENT, "not-enumerated");
    }
    handles[h].tbl_mapped_pre = d->bars[g.tbl_entry].mapped_va != 0;
    handles[h].pba_mapped_pre = d->bars[g.pba_entry].mapped_va != 0;
    {
        enum pci_result pr =
            pci_map_bar(bus, dev, fn, g.tbl_entry, &handles[h].tbl_va);
        if (pr != PCI_OK) {
            msix_unwind(h, 0, 0);
            return fail(pr == PCI_FULL ? MSI_NOMEM : MSI_HW,
                        "msix-map-tbl");
        }
    }
    if (g.pba_entry != g.tbl_entry) {
        enum pci_result pr =
            pci_map_bar(bus, dev, fn, g.pba_entry, &handles[h].pba_va);
        if (pr != PCI_OK) {
            msix_unwind(h, 0, 0);
            return fail(pr == PCI_FULL ? MSI_NOMEM : MSI_HW,
                        "msix-map-pba");
        }
    }
    if (g.pba_entry == g.tbl_entry) handles[h].pba_va = handles[h].tbl_va;
    /* Every programmed entry VA must resolve UC before first write. */
    for (i = 0; i < nvec; ++i) {
        struct vm_mapping m;
        cpu_u64 va = handles[h].tbl_va + (cpu_u64)g.tbl_off +
            (cpu_u64)i * MSIX_ENTRY_SIZE;
        if (vm_query(vm_kernel_space(), va, &m) != VM_OK || !m.uncached ||
            m.permissions != VM_WRITE) {
            msix_unwind(h, 0, 0);
            return fail(MSI_HW, "msix-va");
        }
    }
    /* Bulk mask, then INTx off: the table is quiet while programmed. */
    ctrl = pci_cfg_read16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL);
    pci_cfg_write16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL,
                    (cpu_u16)(ctrl | MSIX_CTRL_MASKALL));
    if (!(pci_cfg_read16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL) &
          MSIX_CTRL_MASKALL)) {
        msix_unwind(h, 0, 0);
        return fail(MSI_HW, "msix-maskall-w");
    }
    cmd = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
    if (!(cmd & PCI_COMMAND_INTX_DISABLE)) {
        pci_cfg_write16(bus, dev, fn, PCI_CFG_COMMAND,
                        (cpu_u16)(cmd | PCI_COMMAND_INTX_DISABLE));
        if (!(pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND) &
              PCI_COMMAND_INTX_DISABLE)) {
            msix_unwind(h, 0, 0);
            return fail(MSI_HW, "msix-intx-w");
        }
        handles[h].did_intx = 1;
    }
    /* Program masked-first, one entry per vector, readback-verified. */
    for (i = 0; i < nvec; ++i) {
        struct msi_msg msg;
        volatile cpu_u32 *e;
        if (msi_build_message(apic_bsp_id(), handles[h].vector[i], &msg) !=
            MSI_OK) {
            msix_unwind(h, 0, 0);
            return fail(MSI_REFUSED, "dest-id");
        }
        e = msix_entry_ptr(handles[h].tbl_va, g.tbl_off, i);
        e[MSIX_ENTRY_CTRL / 4] = MSIX_ENTRY_MASK;
        e[MSIX_ENTRY_ADDR / 4] = msg.addr;
        e[MSIX_ENTRY_ADDR_HI / 4] = msg.hi;
        e[MSIX_ENTRY_DATA / 4] = msg.data;
        if (e[MSIX_ENTRY_CTRL / 4] != MSIX_ENTRY_MASK ||
            e[MSIX_ENTRY_ADDR / 4] != msg.addr ||
            e[MSIX_ENTRY_ADDR_HI / 4] != msg.hi ||
            e[MSIX_ENTRY_DATA / 4] != msg.data) {
            msix_unwind(h, 0, 0);
            return fail(MSI_HW, "msix-entry-w");
        }
    }
    for (i = 0; i < nvec; ++i) {
        if (apic_route_msi_register(APIC_ROUTE_MSIX, bdf_pack(bus, dev, fn),
                                    i, handles[h].vector[i], handler,
                                    opaque) != APIC_OK) {
            msix_unwind(h, routes_done, 0);
            return fail(MSI_NOMEM, "msix-route");
        }
        ++routes_done;
    }
    ctrl = pci_cfg_read16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL);
    pci_cfg_write16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL,
                    (cpu_u16)(ctrl | MSIX_CTRL_ENABLE));
    if (!(pci_cfg_read16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL) &
          MSIX_CTRL_ENABLE)) {
        msix_unwind(h, routes_done, 1);
        return fail(MSI_HW, "msix-enable-w");
    }
    pci_cfg_write16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL,
                    (cpu_u16)((ctrl | MSIX_CTRL_ENABLE) & ~MSIX_CTRL_MASKALL));
    if (pci_cfg_read16(bus, dev, fn, (cpu_u32)g.cap + MSIX_MSG_CTRL) &
        MSIX_CTRL_MASKALL) {
        msix_unwind(h, routes_done, 1);
        return fail(MSI_HW, "msix-unmaskall-w");
    }
    for (i = 0; i < nvec; ++i) {
        volatile cpu_u32 *e =
            msix_entry_ptr(handles[h].tbl_va, g.tbl_off, i);
        e[MSIX_ENTRY_CTRL / 4] = 0;
        if (e[MSIX_ENTRY_CTRL / 4] != 0) {
            msix_unwind(h, routes_done, 1);
            return fail(MSI_HW, "msix-unmask-w");
        }
    }
    *handle = h;
    for (i = 0; i < nvec; ++i) vectors[i] = handles[h].vector[i];
    return MSI_OK;
}

enum msi_result pci_irq_enable_auto(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                                    unsigned int nvec, irq_handler handler,
                                    void *opaque, cpu_u32 *handle,
                                    unsigned int *vectors)
{
    struct msi_walk w;
    if (!handler || !handle || !vectors)
        return fail(MSI_INVALID, "auto-arg");
    if (!bdf_ok(bus, dev, fn)) return fail(MSI_INVALID, "auto-bdf");
    {
        enum msi_result rc = msi_walk_caps(bus, dev, fn, &w);
        if (rc != MSI_OK) return rc;
    }
    /* MSI-X wins when present (more vectors, per-vector masking). */
    if (w.has_msix)
        return pci_irq_enable_msix(bus, dev, fn, nvec, handler, opaque,
                                   handle, vectors);
    if (w.has_msi)
        return pci_irq_enable_msi(bus, dev, fn, nvec, handler, opaque,
                                  handle, vectors);
    return fail(MSI_ABSENT, "no-msi-x-cap");
}

/* ---------- teardown (idempotent: retries converge) ---------- */

static enum msi_result disable_msi(struct msi_handle *hh)
{
    cpu_u32 i;
    cpu_u32 mask_off =
        (cpu_u32)hh->cap + (hh->is64 ? MSI_MASK_64 : MSI_MASK_32);
    if (hh->maskbit) {
        cpu_u32 want =
            0xFFFFFFFFu >> (MSI_VECTORS_MAX - (1u << hh->mmc));
        pci_cfg_write32(hh->bus, hh->dev, hh->fn, mask_off, 0xFFFFFFFFu);
        if (pci_cfg_read32(hh->bus, hh->dev, hh->fn, mask_off) != want)
            return fail(MSI_HW, "dis-msi-mask");
    }
    {
        cpu_u16 ctrl = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                      (cpu_u32)hh->cap + MSI_MSG_CTRL);
        pci_cfg_write16(hh->bus, hh->dev, hh->fn,
                        (cpu_u32)hh->cap + MSI_MSG_CTRL,
                        (cpu_u16)(ctrl & ~MSI_CTRL_ENABLE));
        if (pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                           (cpu_u32)hh->cap + MSI_MSG_CTRL) &
            MSI_CTRL_ENABLE)
            return fail(MSI_HW, "dis-msi-enable");
    }
    /* MME/address/data stay programmed (documented); only ENABLE dies. */
    if (hh->did_intx) {
        cpu_u16 cmd = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                     PCI_CFG_COMMAND);
        if (cmd & PCI_COMMAND_INTX_DISABLE)
            pci_cfg_write16(hh->bus, hh->dev, hh->fn, PCI_CFG_COMMAND,
                            (cpu_u16)(cmd & ~PCI_COMMAND_INTX_DISABLE));
        /* INTx restore is best-effort: disabled INTx only keeps the
           device quiet, so a mismatch here must not block teardown. */
        hh->did_intx = 0;
    }
    for (i = 0; i < hh->nvec; ++i) {
        enum apic_result ar =
            apic_route_msi_unregister(hh->vector[i]);
        if (ar == APIC_NOT_FOUND) continue; /* already torn down */
        if (ar != APIC_OK) return fail(MSI_HW, "dis-msi-route");
        ar = apic_vector_release(hh->vector[i]);
        if (ar == APIC_STATE) continue; /* already free */
        if (ar != APIC_OK) return fail(MSI_HW, "dis-msi-vector");
    }
    return MSI_OK;
}

static enum msi_result disable_msix(struct msi_handle *hh)
{
    cpu_u32 i;
    for (i = 0; i < hh->nvec; ++i) {
        volatile cpu_u32 *e =
            msix_entry_ptr(hh->tbl_va, hh->tbl_off, i);
        e[MSIX_ENTRY_CTRL / 4] = MSIX_ENTRY_MASK;
        if (e[MSIX_ENTRY_CTRL / 4] != MSIX_ENTRY_MASK)
            return fail(MSI_HW, "dis-msix-mask");
    }
    {
        cpu_u16 ctrl = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                      (cpu_u32)hh->cap + MSIX_MSG_CTRL);
        pci_cfg_write16(hh->bus, hh->dev, hh->fn,
                        (cpu_u32)hh->cap + MSIX_MSG_CTRL,
                        (cpu_u16)((ctrl & ~MSIX_CTRL_ENABLE) |
                                  MSIX_CTRL_MASKALL));
        if (pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                           (cpu_u32)hh->cap + MSIX_MSG_CTRL) &
            MSIX_CTRL_ENABLE)
            return fail(MSI_HW, "dis-msix-enable");
    }
    if (hh->did_intx) {
        cpu_u16 cmd = pci_cfg_read16(hh->bus, hh->dev, hh->fn,
                                     PCI_CFG_COMMAND);
        if (cmd & PCI_COMMAND_INTX_DISABLE)
            pci_cfg_write16(hh->bus, hh->dev, hh->fn, PCI_CFG_COMMAND,
                            (cpu_u16)(cmd & ~PCI_COMMAND_INTX_DISABLE));
        hh->did_intx = 0;
    }
    /* Unmap only BARs we mapped; a gone mapping is the desired end
       state either way, so errors converge instead of failing. */
    if (hh->tbl_va && !hh->tbl_mapped_pre) {
        (void)pci_unmap_bar(hh->bus, hh->dev, hh->fn, hh->tbl_entry);
        hh->tbl_va = 0;
    }
    if (hh->pba_va && hh->pba_entry != hh->tbl_entry &&
        !hh->pba_mapped_pre) {
        (void)pci_unmap_bar(hh->bus, hh->dev, hh->fn, hh->pba_entry);
        hh->pba_va = 0;
    }
    for (i = 0; i < hh->nvec; ++i) {
        enum apic_result ar =
            apic_route_msi_unregister(hh->vector[i]);
        if (ar == APIC_NOT_FOUND) continue;
        if (ar != APIC_OK) return fail(MSI_HW, "dis-msix-route");
        ar = apic_vector_release(hh->vector[i]);
        if (ar == APIC_STATE) continue;
        if (ar != APIC_OK) return fail(MSI_HW, "dis-msix-vector");
    }
    return MSI_OK;
}

enum msi_result pci_irq_disable(cpu_u32 handle)
{
    enum msi_result rc;
    if (!handle_live(handle)) return fail(MSI_STATE, "dis-handle");
    if (!cpu_interrupts_disabled() || irq_in_context())
        return fail(MSI_STATE, "context");
    if (handles[handle].kind == MSI_KIND_MSIX)
        rc = disable_msix(&handles[handle]);
    else
        rc = disable_msi(&handles[handle]);
    if (rc != MSI_OK) return rc;
    handles[handle].used = 0;
    return MSI_OK;
}

/* ---------- per-vector mask + info ---------- */

enum msi_result pci_irq_mask(cpu_u32 handle, unsigned int index, int mask)
{
    struct msi_handle *hh;
    if (!handle_live(handle)) return fail(MSI_STATE, "mask-handle");
    hh = &handles[handle];
    if (index >= hh->nvec || (mask & ~1))
        return fail(MSI_INVALID, "mask-arg");
    if (!cpu_interrupts_disabled() || irq_in_context())
        return fail(MSI_STATE, "context");
    if (hh->kind == MSI_KIND_MSIX) {
        volatile cpu_u32 *e = msix_entry_ptr(hh->tbl_va, hh->tbl_off, index);
        cpu_u32 want = mask ? MSIX_ENTRY_MASK : 0u;
        e[MSIX_ENTRY_CTRL / 4] = want;
        if (e[MSIX_ENTRY_CTRL / 4] != want)
            return fail(MSI_HW, "mask-x-w");
        return MSI_OK;
    }
    if (!hh->maskbit) return fail(MSI_STATE, "mask-nomask");
    {
        cpu_u32 off =
            (cpu_u32)hh->cap + (hh->is64 ? MSI_MASK_64 : MSI_MASK_32);
        cpu_u32 m = pci_cfg_read32(hh->bus, hh->dev, hh->fn, off);
        if (mask)
            m |= 1u << index;
        else
            m &= ~(1u << index);
        pci_cfg_write32(hh->bus, hh->dev, hh->fn, off, m);
        if (pci_cfg_read32(hh->bus, hh->dev, hh->fn, off) != m)
            return fail(MSI_HW, "mask-msi-w");
        return MSI_OK;
    }
}

enum msi_result pci_irq_info(cpu_u32 handle, struct msi_handle_info *out)
{
    cpu_u32 i;
    if (!out) return fail(MSI_INVALID, "info-null");
    if (!handle_live(handle)) return fail(MSI_STATE, "info-handle");
    out->kind = handles[handle].kind;
    out->bus = handles[handle].bus;
    out->dev = handles[handle].dev;
    out->fn = handles[handle].fn;
    out->nvec = handles[handle].nvec;
    for (i = 0; i < handles[handle].nvec; ++i)
        out->vector[i] = handles[handle].vector[i];
    return MSI_OK;
}

/* ---------- IRQ-context quiet (never maps, prints, or fails) ---------- */

void msi_quiet_route(unsigned int kind, cpu_u32 bdf, unsigned int index)
{
    cpu_u32 h;
    for (h = 0; h < MSI_MAX_HANDLES; ++h) {
        struct msi_handle *hh = &handles[h];
        if (!hh->used || hh->kind != kind ||
            bdf_pack(hh->bus, hh->dev, hh->fn) != bdf ||
            index >= hh->nvec)
            continue;
        if (kind == MSI_KIND_MSIX) {
            if (hh->tbl_va) {
                volatile cpu_u32 *e =
                    msix_entry_ptr(hh->tbl_va, hh->tbl_off, index);
                e[MSIX_ENTRY_CTRL / 4] = MSIX_ENTRY_MASK;
            }
            return;
        }
        if (hh->maskbit) {
            cpu_u32 off =
                (cpu_u32)hh->cap + (hh->is64 ? MSI_MASK_64 : MSI_MASK_32);
            cpu_u32 m = pci_cfg_read32(hh->bus, hh->dev, hh->fn, off);
            pci_cfg_write32(hh->bus, hh->dev, hh->fn, off,
                            m | (1u << index));
        }
        return;
    }
}
