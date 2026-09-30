/* RYNOROS USB-A1 gated self-test: synthetic matrices + live proofs.
 * See kernel/include/xhci-usb.h and docs/design/usb.md. */
#include "xhci-usb.h"
#include "cpu.h"
#include "dma.h"
#include "pci.h"
#include "pmm.h"
#include "serial.h"
#include "xhci.h"

static unsigned int cases;

static void say(const char *s)
{
    (void)serial_write(s);
}

static void say_dec(cpu_u64 v)
{
    char buf[21];
    int n = 0;
    int i;
    if (v == 0) {
        say("0");
        return;
    }
    while (v != 0) {
        buf[n++] = (char)('0' + v % 10u);
        v /= 10u;
    }
    for (i = n - 1; i >= 0; --i) {
        char c[2] = {buf[i], 0};
        say(c);
    }
}

static void say_hexn(cpu_u64 v, int digits)
{
    int i;
    for (i = digits - 1; i >= 0; --i) {
        unsigned int d = (unsigned int)((v >> (i * 4)) & 0xFu);
        char c[2] = {(char)(d < 10 ? '0' + d : 'a' + d - 10), 0};
        say(c);
    }
}

static void say_hex64(cpu_u64 v)
{
    int i;
    int started = 0;
    for (i = 15; i >= 0; --i) {
        unsigned int d = (unsigned int)((v >> (i * 4)) & 0xFu);
        if (d)
            started = 1;
        if (started || i == 0) {
            char c[2] = {(char)(d < 10 ? '0' + d : 'a' + d - 10), 0};
            say(c);
        }
    }
}

static void fail(const char *tag) __attribute__((noreturn));
static void fail(const char *tag)
{
    say("[USB] failure=");
    say(tag);
    say("\r\n");
    (void)serial_flush();
    cpu_halt();
}

static void require(int ok, const char *tag)
{
    if (!ok)
        fail(tag);
    ++cases;
}

/* ---------- synthetic: setup packets ---------- */

static void synth_setup(void)
{
    struct usb_setup s;
    usb_setup_get_desc(&s, USB_DT_DEVICE, 0, 18);
    require(s.bytes[0] == 0x80, "u-setup0");
    require(s.bytes[1] == USB_REQ_GET_DESCRIPTOR, "u-setup1");
    require(s.bytes[2] == 0 && s.bytes[3] == USB_DT_DEVICE, "u-setup2");
    require(s.bytes[4] == 0 && s.bytes[5] == 0, "u-setup3");
    require(s.bytes[6] == 18 && s.bytes[7] == 0, "u-setup4");
    usb_setup_get_desc(&s, USB_DT_DEVICE, 0, 8);
    require(s.bytes[6] == 8 && s.bytes[7] == 0, "u-setup5");
    usb_setup_get_desc(&s, USB_DT_CONFIG, 0, 0x1234);
    require(s.bytes[3] == USB_DT_CONFIG, "u-setup6");
    require(s.bytes[6] == 0x34 && s.bytes[7] == 0x12, "u-setup7");
}

/* ---------- synthetic: transfer TRB encoders ---------- */

static void synth_trb(void)
{
    struct xhci_trb t;
    cpu_u8 s[8] = {0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00};
    cpu_u8 pcs;
    for (pcs = 0; pcs < 2; ++pcs) {
        cpu_u32 c = pcs ? XHCI_TRB_C : 0u;
        require(xhci_encode_setup(&t, s, pcs, XHCI_TRB_TRT_IN),
                "u-trb-s0");
        require(t.param_lo == 0x01000680u, "u-trb-s1");
        require(t.param_hi == 0x00120000u, "u-trb-s2");
        require(t.status == 8u, "u-trb-s3");
        require(t.control == (((cpu_u32)XHCI_TRB_SETUP <<
                               XHCI_TRB_TYPE_SHIFT) | XHCI_TRB_CH |
                              XHCI_TRB_IDT |
                              ((cpu_u32)XHCI_TRB_TRT_IN <<
                               XHCI_TRB_TRT_SHIFT) | c), "u-trb-s4");
        require(xhci_encode_setup(&t, s, pcs, XHCI_TRB_TRT_OUT),
                "u-trb-s5");
        require((t.control & (0x3u << XHCI_TRB_TRT_SHIFT)) ==
                ((cpu_u32)XHCI_TRB_TRT_OUT << XHCI_TRB_TRT_SHIFT),
                "u-trb-s6");
        require(!xhci_encode_setup(&t, s, pcs, 1), "u-trb-s7");
        require(xhci_encode_data(&t, 0x120000ull, 18, pcs, 1),
                "u-trb-d0");
        require(t.param_lo == 0x120000u && t.param_hi == 0, "u-trb-d1");
        require(t.status == 18u, "u-trb-d2");
        require(t.control == (((cpu_u32)XHCI_TRB_DATA <<
                               XHCI_TRB_TYPE_SHIFT) | XHCI_TRB_CH |
                              XHCI_TRB_DIR_IN | XHCI_TRB_ISP | c),
                "u-trb-d3");
        require(xhci_encode_data(&t, 0x120000ull, 18, pcs, 0),
                "u-trb-d4");
        require(!(t.control & (XHCI_TRB_DIR_IN | XHCI_TRB_ISP)),
                "u-trb-d5");
        require(!xhci_encode_data(&t, 0, 18, pcs, 1), "u-trb-d6");
        require(!xhci_encode_data(&t, 0x120000ull, 0, pcs, 1),
                "u-trb-d7");
        require(!xhci_encode_data(&t, 0x120000ull, 0x20000u, pcs, 1),
                "u-trb-d8");
        require(xhci_encode_status(&t, pcs, 0), "u-trb-t0");
        require(t.param_lo == 0 && t.param_hi == 0 && t.status == 0,
                "u-trb-t1");
        require(t.control == (((cpu_u32)XHCI_TRB_STATUS <<
                               XHCI_TRB_TYPE_SHIFT) | XHCI_TRB_IOC | c),
                "u-trb-t2");
        require(xhci_encode_status(&t, pcs, 1), "u-trb-t3");
        require(t.control & XHCI_TRB_DIR_IN, "u-trb-t4");
        require(!(t.control & XHCI_TRB_CH), "u-trb-t5");
    }
}

/* ---------- synthetic: transfer event decode ---------- */

static void synth_decode_xfer(void)
{
    struct xhci_trb t;
    cpu_u8 cc, slot, epid;
    cpu_u64 ptr;
    cpu_u32 len;
    t.param_lo = 0x127000u;
    t.param_hi = 0;
    t.status = (1u << 24) | 0u;
    t.control = ((cpu_u32)3u << 24) | ((cpu_u32)1u << 16) |
                ((cpu_u32)XHCI_TRB_EV_XFER << XHCI_TRB_TYPE_SHIFT) |
                XHCI_TRB_C;
    require(xhci_decode_xfer(&t, 1, &cc, &ptr, &slot, &epid, &len),
            "u-dec0");
    require(cc == 1 && ptr == 0x127000ull, "u-dec1");
    require(slot == 3 && epid == 1 && len == 0, "u-dec2");
    require(!xhci_decode_xfer(&t, 0, &cc, &ptr, &slot, &epid, &len),
            "u-dec3");
    t.status = (13u << 24) | 5u;
    require(xhci_decode_xfer(&t, 1, &cc, &ptr, &slot, &epid, &len),
            "u-dec4");
    require(cc == XHCI_CC_SHORT && len == 5, "u-dec5");
    t.control = ((cpu_u32)2u << 24) |
                ((cpu_u32)XHCI_TRB_EV_PORT << XHCI_TRB_TYPE_SHIFT) |
                XHCI_TRB_C;
    require(xhci_decode_xfer(&t, 1, &cc, &ptr, &slot, &epid, &len),
            "u-dec6");
    require(slot == 2, "u-dec7");
}

/* ---------- synthetic: context builders ---------- */

static void synth_ctx(void)
{
    cpu_u32 s[8], e[8];
    xusb_slot_ctx_init(s, 3, 5);
    require(s[0] == ((3u << XHCI_SCTX_SPD_SHIFT) |
                     (1u << XHCI_SCTX_ENT_SHIFT)), "u-ctx-s0");
    require(s[1] == (5u << XHCI_SCTX_PORT_SHIFT), "u-ctx-s1");
    require(s[2] == 0 && s[3] == 0, "u-ctx-s2");
    xusb_ep0_ctx_init(e, 0x12a000ull, 64);
    require(e[0] == 0, "u-ctx-e0");
    require(e[1] == (((cpu_u32)XHCI_EP_TYPE_CTRL << XHCI_ECTX_TYPE_SHIFT) |
                     (3u << XHCI_ECTX_ERR_SHIFT) |
                     (64u << XHCI_ECTX_MPS_SHIFT)), "u-ctx-e1");
    require(e[2] == (0x12a000u | XHCI_ECTX_DCS), "u-ctx-e2");
    require(e[3] == 0 && e[4] == 8u, "u-ctx-e3");
    xusb_ep0_ctx_init(e, 0x12a005ull, 8);
    require((e[2] & ~0xFu) == 0x12a000u && (e[2] & XHCI_ECTX_DCS),
            "u-ctx-e4");
}

/* ---------- synthetic: descriptor parser ---------- */

static const cpu_u8 desc_good[18] = {
    18, 1, 0x00, 0x02, 0x00, 0x00, 0x00, 64,
    0x27, 0x06, 0x01, 0x00, 0x00, 0x00, 1, 4, 11, 1
};

static void synth_parse(void)
{
    struct usb_device_desc d;
    cpu_u8 b[24];
    cpu_u32 i;
    for (i = 0; i < 18; ++i)
        b[i] = desc_good[i];
    require(usb_parse_device_desc(b, 18, &d) == USB_OK, "u-par0");
    require(d.bLength == 18 && d.bDescriptorType == 1, "u-par1");
    require(d.bcdUSB == 0x0200, "u-par2");
    require(d.bMaxPacketSize0 == 64, "u-par3");
    require(d.idVendor == 0x0627, "u-par4");
    require(d.idProduct == 0x0001, "u-par5");
    require(d.bcdDevice == 0, "u-par6");
    require(d.iManufacturer == 1 && d.iProduct == 4, "u-par7");
    require(d.iSerialNumber == 11 && d.bNumConfigurations == 1, "u-par8");
    b[0] = 0;
    require(usb_parse_device_desc(b, 18, &d) == USB_MISMATCH, "u-par9");
    b[0] = 8;
    require(usb_parse_device_desc(b, 18, &d) == USB_MISMATCH, "u-par10");
    b[0] = 18;
    b[1] = 2;
    require(usb_parse_device_desc(b, 18, &d) == USB_MISMATCH, "u-par11");
    b[1] = 1;
    require(usb_parse_device_desc(b, 7, &d) == USB_SHORT, "u-par12");
    b[17] = 0;
    require(usb_parse_device_desc(b, 18, &d) == USB_MISMATCH, "u-par13");
    b[17] = 1;
    b[4] = 9;
    require(usb_parse_device_desc(b, 18, &d) == USB_OK, "u-par14");
    require(d.bDeviceClass == 9, "u-par15");
    b[4] = 0xFF;
    require(usb_parse_device_desc(b, 18, &d) == USB_OK, "u-par16");
    b[4] = 0;
    for (i = 0; i < 18; ++i)
        b[i] = 0xFFu;
    b[0] = 18;
    b[1] = 1;
    require(usb_parse_device_desc(b, 18, &d) == USB_OK, "u-par17");
    require(d.idVendor == 0xFFFFu && d.bcdUSB == 0xFFFFu, "u-par18");
    require(usb_parse_device_desc(0, 18, &d) == USB_INVALID, "u-par19");
    require(usb_parse_device_desc(b, 18, 0) == USB_INVALID, "u-par20");
}

/* ---------- synthetic: EP0 MPS policy ---------- */

static void synth_mps(void)
{
    require(usb_ep0_initial_mps(USB_SPEED_HIGH) == 64, "u-mps0");
    require(usb_ep0_initial_mps(USB_SPEED_FULL) == 8, "u-mps1");
    require(usb_ep0_initial_mps(USB_SPEED_LOW) == 8, "u-mps2");
    require(usb_ep0_initial_mps(USB_SPEED_SUPER) == 512, "u-mps3");
    require(usb_ep0_initial_mps(USB_SPEED_UNKNOWN) == 0, "u-mps4");
    require(!usb_ep0_mps_needs_update(USB_SPEED_HIGH, 64, 64), "u-mps5");
    require(!usb_ep0_mps_needs_update(USB_SPEED_HIGH, 64, 8), "u-mps6");
    require(!usb_ep0_mps_needs_update(USB_SPEED_FULL, 8, 8), "u-mps7");
    require(usb_ep0_mps_needs_update(USB_SPEED_FULL, 8, 64), "u-mps8");
    require(usb_ep0_mps_needs_update(USB_SPEED_LOW, 8, 16), "u-mps9");
}

static void synth_all(void)
{
    synth_setup();
    synth_trb();
    synth_decode_xfer();
    synth_ctx();
    synth_parse();
    synth_mps();
}

/* ---------- live ---------- */

static struct xhci_hcd h;
static struct xusb_bus bus;
static struct xusb_dev dev;

static volatile cpu_u8 hook_type, hook_cc;
static volatile cpu_u64 hook_ptr;
static volatile cpu_u8 hook_slot;

static void u_hook(struct xhci_hcd *hh, cpu_u8 type, cpu_u8 cc, cpu_u64 ptr,
                   cpu_u8 slot, cpu_u8 ccs, void *ctx)
{
    (void)hh;
    (void)ccs;
    (void)ctx;
    hook_type = type;
    hook_cc = cc;
    hook_ptr = ptr;
    hook_slot = slot;
}

static const char *state_name(cpu_u8 s)
{
    switch (s) {
    case XUSB_DISCONNECTED: return "disconn";
    case XUSB_CONNECTED: return "conn";
    case XUSB_RESETTING: return "reset";
    case XUSB_ENABLED: return "enabled";
    case XUSB_SLOT: return "slot";
    case XUSB_ADDRESSING: return "addr";
    case XUSB_ADDRESSED: return "addressed";
    case XUSB_EP0READY: return "ep0";
    case XUSB_DESC8: return "desc8";
    case XUSB_DESCDONE: return "done";
    default: return "failed";
    }
}

static const char *speed_name(cpu_u8 s)
{
    switch (s) {
    case USB_SPEED_LOW: return "LS";
    case USB_SPEED_FULL: return "FS";
    case USB_SPEED_HIGH: return "HS";
    case USB_SPEED_SUPER: return "SS";
    case USB_SPEED_SUPERPLUS: return "SSP";
    default: return "??";
    }
}

static void emit_cost(struct pmm_statistics *a, struct pmm_statistics *b)
{
    say("[USB] cost alloc0=");
    say_hex64(a->allocated_bytes);
    say(" alloc1=");
    say_hex64(b->allocated_bytes);
    say("\r\n");
}

static void live_protos(void)
{
    cpu_u32 i;
    require(xusb_parse_protos(&bus) == XUSB_OK, "u-proto");
    require(bus.nprotos >= 1, "u-proto1");
    for (i = 0; i < bus.nprotos; ++i) {
        struct xusb_proto *p = &bus.protos[i];
        say("[USB] proto rev=");
        say_dec(p->major);
        say(".");
        say_dec(p->minor);
        say(" ports=");
        say_dec(p->port_off);
        say("..");
        say_dec((cpu_u64)p->port_off + p->port_count - 1u);
        say(" psi=");
        say_dec(p->psi_count);
        say("\r\n");
    }
}

static void live_scan(void)
{
    cpu_u32 i;
    require(xusb_scan_ports(&bus) == XUSB_OK, "u-scan");
    for (i = 0; i < bus.nports; ++i) {
        struct xusb_port *p = &bus.ports[i];
        say("[USB] port ");
        say_dec(p->num);
        say(" proto=");
        if (p->proto == 0xFFu)
            say("none");
        else
            say_dec(p->proto);
        say(" s=");
        say(state_name(p->state));
        say(" ccs=");
        say_dec((p->last_ps & XHCI_PS_CCS) ? 1 : 0);
        say(" ped=");
        say_dec((p->last_ps & XHCI_PS_PED) ? 1 : 0);
        say(" pls=");
        say_dec((p->last_ps >> XHCI_PS_PLS_SHIFT) & XHCI_PS_PLS_MASK);
        say(" spd=");
        say_dec(p->speed_id);
        say(" ");
        say(speed_name(p->speed));
        say("\r\n");
    }
}

static int find_connected(cpu_u32 *idx_out)
{
    cpu_u32 i;
    for (i = 0; i < bus.nports; ++i) {
        if (bus.ports[i].state == XUSB_CONNECTED) {
            *idx_out = i;
            return 1;
        }
    }
    return 0;
}

static void live_noop(void)
{
    cpu_u8 tok = 0xFFu;
    require(xhci_submit_noop(&h, &tok) == XHCI_OK, "u-noop0");
    require(xhci_wait_token(&h, tok) == XHCI_OK, "u-noop1");
    require(h.tokens[tok].ccode == XHCI_CC_SUCCESS, "u-noop2");
}

static const struct {
    const char *name;
    enum xusb_fail_at fail;
} rb_stages[] = {
    {"PROTO", XUSB_FAIL_PROTO}, {"SCAN", XUSB_FAIL_SCAN},
    {"RESET", XUSB_FAIL_RESET}, {"ENSLOT", XUSB_FAIL_ENSLOT},
    {"DCTX", XUSB_FAIL_DCTX}, {"ICTX", XUSB_FAIL_ICTX},
    {"EP0RING", XUSB_FAIL_EP0RING}, {"ADDR", XUSB_FAIL_ADDR},
    {"DESC8", XUSB_FAIL_DESC8}, {"EVAL", XUSB_FAIL_EVAL},
    {"DESC18", XUSB_FAIL_DESC18},
};

static void live_rollback(cpu_u32 idx)
{
    unsigned int i;
    struct pmm_statistics rb0, rb1;
    cpu_u8 desc[USB_DT_DEVICE_SIZE];
    require(pmm_statistics(&rb0) == PMM_OK, "u-rb-cost0");
    for (i = 0; i < sizeof(rb_stages) / sizeof(rb_stages[0]); ++i) {
        cpu_u32 s;
        volatile cpu_u64 *dcb;
        for (s = 0; s < USB_DT_DEVICE_SIZE; ++s)
            desc[s] = 0;
        if (rb_stages[i].fail == XUSB_FAIL_PROTO) {
            require(xusb_parse_protos(&bus) == XUSB_OK, "u-rb-p-ok");
            xusb_inject_force(XUSB_FAIL_PROTO);
            require(xusb_parse_protos(&bus) == XUSB_FAILED_DEV,
                    "u-rb-p-fail");
            xusb_inject_force(XUSB_FAIL_NONE);
            require(xusb_parse_protos(&bus) == XUSB_OK, "u-rb-p-re");
        } else if (rb_stages[i].fail == XUSB_FAIL_SCAN) {
            xusb_inject_force(XUSB_FAIL_SCAN);
            require(xusb_scan_ports(&bus) == XUSB_FAILED_DEV,
                    "u-rb-s-fail");
            xusb_inject_force(XUSB_FAIL_NONE);
            require(xusb_scan_ports(&bus) == XUSB_OK, "u-rb-s-re");
        } else {
            require(xusb_enumerate(&dev, idx, rb_stages[i].fail, desc) !=
                    XUSB_OK, "u-rb-enum");
            require(dev.slot == 0, "u-rb-slot");
            require(bus.ports[idx].state == XUSB_FAILED, "u-rb-state");
            require(bus.ports[idx].slot == 0, "u-rb-pslot");
            dcb = (volatile cpu_u64 *)h.dcb.virt;
            for (s = 1; s <= h.max_slots_en; ++s)
                require(dcb[s] == 0, "u-rb-dcbaa");
            require(dma_check(), "u-rb-dma");
            live_noop();
            bus.ports[idx].state = XUSB_CONNECTED;
        }
        say("[USB] rollback stage=");
        say(rb_stages[i].name);
        say(" ok\r\n");
    }
    require(pmm_statistics(&rb1) == PMM_OK, "u-rb-cost1");
    require(rb0.allocated_bytes == rb1.allocated_bytes, "u-rb-zero");
}

static void live_enumerate(cpu_u32 idx)
{
    cpu_u8 desc[USB_DT_DEVICE_SIZE];
    struct usb_device_desc parsed;
    cpu_u32 i;
    volatile cpu_u32 *os;
    volatile cpu_u32 *oe;
    require(xusb_enumerate(&dev, idx, XUSB_FAIL_NONE, desc) == XUSB_OK,
            "u-enum");
    require(bus.ports[idx].state == XUSB_DESCDONE, "u-enum-state");
    say("[USB] slot id=");
    say_dec(dev.slot);
    say(" port=");
    say_dec(bus.ports[idx].num);
    say(" addr=");
    say_dec(dev.addr);
    say("\r\n");
    say("[USB] ctx slot=");
    say_dec(dev.slot);
    say(" dctx=");
    say_hex64(dev.dctx.bus);
    say(" ictx=");
    say_hex64(dev.ictx.bus);
    say(" ep0ring=");
    say_hex64(dev.ep0.dma.bus);
    say(" mps=");
    say_dec(dev.ep0_mps);
    say("\r\n");
    say("[USB] addr slot=");
    say_dec(dev.slot);
    say(" ctl=");
    say_hex64(dev.addr_control);
    say(" ok\r\n");
    os = (volatile cpu_u32 *)dev.dctx.virt;
    oe = os + 8;
    say("[USB] out slot=");
    say_dec(dev.slot);
    say(" st=");
    say_dec((os[3] >> XHCI_SCTX_ST_SHIFT) & XHCI_SCTX_ST_MASK);
    say(" ep0=");
    say_dec(oe[0] & XHCI_ECTX_ST_MASK);
    say(" mps=");
    say_dec((oe[1] >> XHCI_ECTX_MPS_SHIFT) & XHCI_ECTX_MPS_MASK);
    say("\r\n");
    say("[USB] eval slot=");
    say_dec(dev.slot);
    say(" mps=");
    say_dec(dev.ep0_mps);
    say(" ok\r\n");
    require(xhci_port_change_claim(&h, bus.ports[idx].num) == 1,
            "u-portev");
    say("[USB] portev port=");
    say_dec(bus.ports[idx].num);
    say(" ok\r\n");
    say("[USB] desc slot=");
    say_dec(dev.slot);
    say(" len=18 raw=");
    for (i = 0; i < USB_DT_DEVICE_SIZE; ++i)
        say_hexn(desc[i], 2);
    say("\r\n");
    require(usb_parse_device_desc(desc, USB_DT_DEVICE_SIZE, &parsed) ==
            USB_OK, "u-parse");
    say("[USB] parsed vid=");
    say_hexn(parsed.idVendor, 4);
    say(" pid=");
    say_hexn(parsed.idProduct, 4);
    say(" bcd=");
    say_hexn(parsed.bcdUSB, 4);
    say(" cls=");
    say_dec(parsed.bDeviceClass);
    say(" mps0=");
    say_dec(parsed.bMaxPacketSize0);
    say(" cfgs=");
    say_dec(parsed.bNumConfigurations);
    say("\r\n");
}

static void live_repeat(void)
{
    struct dma_buffer db;
    cpu_u64 max_bus = h.caps.ac64 ? DMA_ADDR_ANY : DMA_ADDR_32BIT;
    cpu_u8 first[USB_DT_DEVICE_SIZE];
    cpu_u32 r, i;
    unsigned int wraps0 = dev.ep0.wraps;
    require(dma_alloc(USB_DT_DEVICE_SIZE, 64, max_bus, &db) == DMA_OK,
            "u-rep-alloc");
    for (r = 0; r < 5; ++r) {
        volatile cpu_u8 *dp = (volatile cpu_u8 *)db.virt;
        for (i = 0; i < USB_DT_DEVICE_SIZE; ++i)
            dp[i] = 0;
        hook_type = 0;
        require(xusb_get_descriptor(&dev, USB_DT_DEVICE, 0, db.bus,
                                    USB_DT_DEVICE_SIZE) == XUSB_OK,
                "u-rep-xfer");
        require(hook_type == XHCI_TRB_EV_XFER, "u-rep-ev");
        require(hook_cc == XHCI_CC_SUCCESS, "u-rep-cc");
        require(hook_slot == dev.slot, "u-rep-slot");
        require(h.last_xepid == XHCI_DCI_EP0, "u-rep-ep");
        require(h.last_xlen == 0, "u-rep-len");
        require(hook_ptr >= dev.ep0.dma.bus &&
                hook_ptr < dev.ep0.dma.bus +
                (cpu_u64)XUSB_EP0_TRBS * XHCI_TRB_SIZE &&
                ((hook_ptr - dev.ep0.dma.bus) % XHCI_TRB_SIZE) == 0,
                "u-rep-ptr");
        say("[USB] xferdone slot=");
        say_dec(hook_slot);
        say(" ep=");
        say_dec(h.last_xepid);
        say(" cc=");
        say_dec(hook_cc);
        say(" resid=");
        say_dec(h.last_xlen);
        say(" ptr=");
        say_hex64(hook_ptr);
        say(" ok\r\n");
        for (i = 0; i < USB_DT_DEVICE_SIZE; ++i) {
            if (r == 0)
                first[i] = dp[i];
            else
                require(dp[i] == first[i], "u-rep-ident");
        }
    }
    dma_free(&db);
    require(dev.ep0.wraps > wraps0, "u-rep-wrap");
    say("[USB] wrap ep0=");
    say_dec(dev.ep0.wraps - wraps0);
    say(" reads=5 identical=1\r\n");
}

void usb_self_test(void)
{
    struct pmm_statistics before, after;
    const struct pci_device *d;
    cpu_u32 idx = 0;
    synth_all();
    say("[USB] synth ok cases=");
    say_dec(cases);
    say("\r\n");
    require(pmm_statistics(&before) == PMM_OK, "u-cost0");
    d = pci_find_class(XHCI_PCI_CLASS, XHCI_PCI_SUBCLASS, XHCI_PCI_PROGIF,
                       0);
    if (!d) {
        say("[USB] absent\r\n");
        require(pmm_statistics(&after) == PMM_OK, "u-cost1");
        emit_cost(&before, &after);
        say("[USB] live ok devices=0\r\n");
        say("[USB] usb verified\r\n");
        return;
    }
    /* xHCI-A1 suite stays green inside every USB run (§73). */
    xhci_self_test();
    require(xhci_init(&h, XHCI_FAIL_NONE) == XHCI_OK, "u-init");
    h.hook = u_hook;
    {
        cpu_u32 i;
        bus.hcd = &h;
        bus.nprotos = 0;
        bus.nports = 0;
        for (i = 0; i < XHCI_PORTS_MAX; ++i) {
            bus.ports[i].num = 0;
            bus.ports[i].proto = 0xFFu;
            bus.ports[i].state = XUSB_DISCONNECTED;
            bus.ports[i].slot = 0;
        }
    }
    dev.bus = &bus;
    live_protos();
    live_scan();
    if (!find_connected(&idx)) {
        say("[USB] no-device\r\n");
        live_noop();
        require(xhci_teardown(&h) == XHCI_OK, "u-down0");
        require(pmm_statistics(&after) == PMM_OK, "u-cost1");
        emit_cost(&before, &after);
        say("[USB] live ok devices=0\r\n");
        say("[USB] usb verified\r\n");
        return;
    }
    live_rollback(idx);
    live_enumerate(idx);
    live_repeat();
    live_noop();
    say("[USB] noopalive ok\r\n");
    xusb_dev_cleanup(&dev);
    require(dev.slot == 0, "u-clean");
    say("[USB] cleanup slot=0 ok\r\n");
    require(xhci_teardown(&h) == XHCI_OK, "u-down");
    require(dma_check(), "u-dma");
    require(pmm_statistics(&after) == PMM_OK, "u-cost1");
    emit_cost(&before, &after);
    say("[USB] live ok devices=1\r\n");
    say("[USB] usb verified\r\n");
}
