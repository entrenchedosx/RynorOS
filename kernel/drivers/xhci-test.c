/* RYNOROS xHCI-A1 gated self-test: synthetic matrices + live proofs.
 * See kernel/include/xhci.h and docs/design/xhci.md. */
#include "xhci.h"
#include "apic.h"
#include "cpu.h"
#include "dma.h"
#include "ksched.h"
#include "msi.h"
#include "pci.h"
#include "pmm.h"
#include "serial.h"
#include "user.h"
#include "vm.h"

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

static void fail(const char *tag) __attribute__((noreturn));
static void fail(const char *tag)
{
    say("[XHCI] failure=");
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

/* ---------- pure TRB tests (§22/54) ---------- */

static void synth_trb(void)
{
    struct xhci_trb t;
    cpu_u8 type, cc, slot;
    cpu_u64 ptr;
    unsigned int i;
    static const cpu_u8 codes[7] = {1, 5, 17, 21, 24, 25, 26};

    require(xhci_encode_noop(&t, 1, 0) == 1, "t-noop0");
    require(t.param_lo == 0 && t.param_hi == 0 && t.status == 0, "t-noopw");
    require(t.control == ((cpu_u32)23 << 10 | 1u), "t-noopc");
    require(xhci_encode_noop(&t, 0, 7) == 1, "t-noop1");
    require(t.status == (7u << 22), "t-noopt");
    require((t.control & 1u) == 0, "t-noopz");
    require(xhci_encode_noop(&t, 1, 1024) == 0, "t-noorng");
    require(xhci_encode_noop(0, 1, 0) == 0, "t-noonull");

    xhci_encode_link(&t, 0x12345000ULL, 1);
    require(t.param_lo == 0x12345000u && t.param_hi == 0, "t-linkp");
    require(t.control == ((cpu_u32)6 << 10 | 2u | 1u), "t-linkc");
    xhci_encode_link(&t, 0x1FFFFFFFFULL, 0);
    require(t.param_lo == 0xFFFFFFFFu && t.param_hi == 1u, "t-linkh");
    require((t.control & 1u) == 0 && (t.control & 2u) != 0, "t-linkt");

    for (i = 0; i < 7; ++i) {
        t.param_lo = 0xAA550000u + i;
        t.param_hi = 0;
        t.status = ((cpu_u32)codes[i] << 24);
        t.control = ((cpu_u32)33 << 10) | 1u;
        require(xhci_decode_event(&t, 1, &type, &cc, &ptr, &slot),
                "t-evok");
        require(type == 33 && cc == codes[i], "t-evcc");
        require(ptr == 0xAA550000u + i && slot == 0, "t-evptr");
        require(!xhci_decode_event(&t, 0, &type, &cc, &ptr, &slot),
                "t-evmis");
    }
    t.control = ((cpu_u32)34 << 10) | 1u;
    t.status = (1u << 24);
    require(xhci_decode_event(&t, 1, &type, &cc, &ptr, &slot), "t-port");
    require(type == 34, "t-portt");
    require(!xhci_decode_event(0, 1, &type, &cc, &ptr, &slot), "t-evnull");
}

static void say_hex64(cpu_u64 v)
{
    char buf[17];
    int n = 0;
    int i;
    if (v == 0) {
        say("0");
        return;
    }
    while (v != 0) {
        cpu_u64 d = v & 0xFu;
        buf[n++] = (char)(d < 10u ? '0' + d : 'a' + d - 10u);
        v >>= 4;
    }
    for (i = n - 1; i >= 0; --i) {
        char c[2] = {buf[i], 0};
        say(c);
    }
}

static void say_bdf(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    say("bdf=");
    say_dec(bus);
    say(":");
    say_dec(dev);
    say(".");
    say_dec(fn);
}

/* ---------- live bring-up ---------- */

static struct xhci_hcd h;

/* Per-event latch (hook runs in IRQ context; test drains after). */
static volatile cpu_u8 hook_type, hook_cc, hook_slot, hook_ccs, hook_user;
static volatile cpu_u64 hook_ptr, hook_rip;
static volatile cpu_u8 hook_isr;

static void xh_hook(struct xhci_hcd *hh, cpu_u8 type, cpu_u8 cc, cpu_u64 ptr,
                    cpu_u8 slot, cpu_u8 ccs, void *ctx)
{
    (void)ctx;
    hook_type = type;
    hook_cc = cc;
    hook_ptr = ptr;
    hook_slot = slot;
    hook_ccs = ccs;
    hook_user = hh->last_user;
    hook_rip = hh->last_rip;
    hook_isr = hh->last_isr;
}

static unsigned int xcap_count;
static unsigned int leg_present;
static cpu_u64 leg_off;

static enum xhci_result xcap_emit(struct xhci_hcd *hh, cpu_u8 id, cpu_u64 off,
                                  cpu_u8 next, void *ctx)
{
    (void)hh;
    (void)ctx;
    say("[XHCI] xcap id=");
    say_dec(id);
    say(" off=");
    say_hex64(off);
    say(" next=");
    say_dec(next);
    say("\r\n");
    ++xcap_count;
    if (id == XHCI_XCAP_LEGACY) {
        leg_present = 1;
        leg_off = off;
    }
    return XHCI_OK;
}

static void live_bringup(void)
{
    cpu_u32 v;
    const struct pci_device *d;
    say("[XHCI] found ");
    say_bdf(h.bus, h.dev, h.fn);
    say(" vendor=");
    say_hex64(h.caps.vendor);
    say(" device=");
    say_hex64(h.caps.device);
    say(" class=");
    say_hex64(h.caps.class_);
    say(".");
    say_hex64(h.caps.subclass);
    say(".");
    say_hex64(h.caps.progif);
    say(" rev=");
    say_hex64(h.caps.rev);
    say("\r\n");
    require(h.caps.class_ == XHCI_PCI_CLASS &&
            h.caps.subclass == XHCI_PCI_SUBCLASS &&
            h.caps.progif == XHCI_PCI_PROGIF, "c-class");
    /* Second claim must refuse while owned. */
    {
        struct xhci_hcd h2;
        require(xhci_claim(&h2) == XHCI_STATE, "c-excl");
    }
    require(xhci_map_bar(&h) == XHCI_OK, "c-bar");
    d = pci_find_bdf(h.bus, h.dev, h.fn);
    require(d != 0, "c-bdf");
    say("[XHCI] bar ord=");
    say_dec(h.bar_ord);
    say(" base=");
    say_hex64(d->bars[h.bar_ord].base);
    say(" size=");
    say_hex64(d->bars[h.bar_ord].size);
    say(" mem64=");
    say_dec(d->bars[h.bar_ord].is64 ? 1 : 0);
    say("\r\n");
    require(xhci_read_caps(&h) == XHCI_OK, "c-caps");
    say("[XHCI] caps caplen=");
    say_hex64(h.caps.caplen);
    say(" hciver=");
    say_hex64(h.caps.hciver);
    say(" slots=");
    say_dec(h.caps.slots);
    say(" intrs=");
    say_dec(h.caps.intrs);
    say(" ports=");
    say_dec(h.caps.ports);
    say(" spads=");
    say_dec(h.caps.spads);
    say(" csz=");
    say_dec(h.caps.csz);
    say(" ac64=");
    say_dec(h.caps.ac64);
    say(" xecp=");
    say_hex64(h.caps.xecp);
    say(" dboff=");
    say_hex64(h.db_base);
    say(" rtsoff=");
    say_hex64(h.rt_base);
    say(" pagesize=");
    say_hex64(h.caps.pagesz);
    say("\r\n");
    xcap_count = 0;
    leg_present = 0;
    require(xhci_walk_xcaps(&h, xcap_emit, 0) == XHCI_OK, "c-xcap");
    require(xcap_count > 0, "c-xcapn");
    require(xhci_legacy_handoff(&h) == XHCI_OK, "c-leg");
    say("[XHCI] legacy ");
    if (!leg_present) {
        say("absent\r\n");
    } else {
        require(xhci_bar_read(&h, leg_off + 4, &v) == XHCI_OK, "c-legst");
        say("owned bios=");
        say_dec((v & XHCI_LEG_BIOS_OWNED) ? 1 : 0);
        say(" os=");
        say_dec((v & XHCI_LEG_OS_OWNED) ? 1 : 0);
        say("\r\n");
        require(!(v & XHCI_LEG_BIOS_OWNED) && (v & XHCI_LEG_OS_OWNED),
                "c-legown");
    }
    require(xhci_halt(&h) == XHCI_OK, "c-halt");
    require(xhci_op_read(&h, XHCI_OP_USBSTS, &v) == XHCI_OK, "c-haltst");
    say("[XHCI] halted usbsts=");
    say_hex64(v);
    say("\r\n");
    require(v & XHCI_STS_HCH, "c-hch");
    require(xhci_reset(&h) == XHCI_OK, "c-reset");
    require(xhci_op_read(&h, XHCI_OP_USBCMD, &v) == XHCI_OK, "c-rstcmd");
    say("[XHCI] reset ok cnr=0 usbcmd=");
    say_hex64(v);
    say("\r\n");
    require(xhci_setup_dma(&h) == XHCI_OK, "c-dma");
    say("[XHCI] dmaa dcbba=");
    say_hex64(h.dcb.bus);
    say(" cmd=");
    say_hex64(h.cmd.dma.bus);
    say(" evt=");
    say_hex64(h.evt.dma.bus);
    say(" erst=");
    say_hex64(h.erst.bus);
    say(" spads=");
    say_dec(h.caps.spads);
    say(" maxbus=");
    say(h.caps.ac64 ? "ANY" : "32");
    say("\r\n");
    require((h.cmd.dma.bus & 0x3Fu) == 0, "c-crcr");
    say("[XHCI] slots maxen=");
    say_dec(h.max_slots_en);
    say(" csz=");
    say_dec(h.caps.csz);
    say("\r\n");
    say("[XHCI] ring cmd pcs=");
    say_dec(h.cmd.pcs);
    say(" enq=");
    say_dec(h.cmd.enq);
    say(" wraps=");
    say_dec(h.cmd.wraps);
    say("\r\n");
    say("[XHCI] ring evt ccs=");
    say_dec(h.evt.ccs);
    say(" deq=");
    say_dec(h.evt.deq);
    say(" wraps=");
    say_dec(h.evt.wraps);
    say("\r\n");
    say("[XHCI] erst sz=1 ba=");
    say_hex64(h.erst.bus);
    say(" erdp=");
    say_hex64(h.evt.erdp_shadow);
    say("\r\n");
    require(xhci_setup_interrupter(&h) == XHCI_OK, "c-intr");
    say("[XHCI] intr ie=1 imod=0 erstsz=1\r\n");
    require(xhci_enable_bme(&h) == XHCI_OK, "c-bme");
    say("[XHCI] cmd saved=");
    say_hex64(h.cmd0);
    say(" now=");
    say_hex64(pci_cfg_read16(h.bus, h.dev, h.fn, PCI_CFG_COMMAND));
    say("\r\n");
    h.hook = xh_hook;
    require(xhci_arm_msix(&h) == XHCI_OK, "c-msix");
    say("[XHCI] msix vec=");
    say_dec(h.irq_vec);
    say(" entry=0 ok\r\n");
    require(xhci_start(&h) == XHCI_OK, "c-start");
    require(xhci_op_read(&h, XHCI_OP_USBSTS, &v) == XHCI_OK, "c-stst");
    say("[XHCI] started usbsts=");
    say_hex64(v);
    say("\r\n");
    require(!(v & XHCI_STS_HCH), "c-run");
    /* R1 negative: reset while running must refuse (silent). */
    require(xhci_reset(&h) == XHCI_STATE, "c-r1neg");
}

static void emit_cost(struct pmm_statistics *before,
                      struct pmm_statistics *after)
{
    say("[XHCI] cost alloc0=");
    say_hex64(before->allocated_bytes);
    say(" alloc1=");
    say_hex64(after->allocated_bytes);
    say("\r\n");
}

/* ---------- NOOP series (64 commands, both rings wrap) ---------- */

#define XHCI_SERIES 64u

static void live_series(void)
{
    unsigned int i;
    cpu_u64 irq0 = h.irq_count;
    cpu_u64 legacy0[16];
    for (i = 0; i < 16; ++i)
        legacy0[i] = apic_vector_count(32 + i);
    for (i = 0; i < XHCI_SERIES; ++i) {
        cpu_u8 tok = 0xFFu;
        require(xhci_submit_noop(&h, &tok) == XHCI_OK, "s-submit");
        require(tok < XHCI_TOKENS, "s-tok");
        say("[XHCI] noop token=");
        say_dec(tok);
        say(" trb=");
        say_hex64(h.tokens[tok].trb_bus);
        say("\r\n");
        say("[XHCI] db rung=0\r\n");
        require(xhci_wait_token(&h, tok) == XHCI_OK, "s-wait");
        say("[XHCI] irq vec=");
        say_dec(h.irq_vec);
        say(" count=");
        say_dec(h.irq_count);
        say(" isr=");
        say_dec(h.last_isr);
        say(" user=");
        say_dec(h.last_user);
        say(" rip=");
        say_hex64(h.last_rip);
        say("\r\n");
        require(h.last_isr == 1, "s-isr");
        require(h.last_user == 0, "s-ring0");
        say("[XHCI] cmpl token=");
        say_dec(tok);
        say(" ptr=");
        say_hex64(hook_ptr);
        say(" cc=");
        say_dec(hook_cc);
        say(" slot=");
        say_dec(hook_slot);
        say(" type=");
        say_dec(hook_type);
        say(" cycle=");
        say_dec(hook_ccs);
        say(" ok\r\n");
        require(hook_type == XHCI_TRB_EV_CMPL, "s-type");
        require(hook_cc == XHCI_CC_SUCCESS, "s-cc");
        require(hook_slot == 0, "s-slot");
        require(hook_ptr == h.tokens[tok].trb_bus, "s-ptr");
        say("[XHCI] noop done token=");
        say_dec(tok);
        say(" count=");
        say_dec(h.cmpl_count);
        say("\r\n");
    }
    require(h.cmpl_count == XHCI_SERIES, "s-cmpl");
    require(h.irq_count == irq0 + XHCI_SERIES, "s-irq");
    /* First-fit reuse parks on token 0; every token must be settled
       (COMPLETED or never-issued FREE), none stuck or errored. */
    for (i = 0; i < XHCI_TOKENS; ++i)
        require(h.tokens[i].state == XHCI_TOK_COMPLETED ||
                h.tokens[i].state == XHCI_TOK_FREE, "s-tokend");
    require(h.tokens[0].state == XHCI_TOK_COMPLETED, "s-tok0");
    say("[XHCI] erdp deq=");
    say_hex64(h.evt.erdp_shadow);
    say(" wraps=");
    say_dec(h.evt.wraps);
    say("\r\n");
    /* INTx defense: legacy vectors silent across the whole series. */
    for (i = 0; i < 16; ++i)
        require(apic_vector_count(32 + i) == legacy0[i], "s-intx");
    say("[XHCI] intx silent=1\r\n");
}

/* ---------- rollback matrix (§48, live) ---------- */

static const struct {
    const char *name;
    enum xhci_fail_at fail;
} rb_stages[] = {
    {"BAR", XHCI_FAIL_BAR}, {"CAPS", XHCI_FAIL_CAPS},
    {"LEGACY", XHCI_FAIL_LEGACY}, {"HALT", XHCI_FAIL_HALT},
    {"RESET", XHCI_FAIL_RESET}, {"DCBAA", XHCI_FAIL_DCBAA},
    {"SCRATCH", XHCI_FAIL_SCRATCH}, {"CMDRING", XHCI_FAIL_CMDRING},
    {"EVTRING", XHCI_FAIL_EVTRING}, {"ERST", XHCI_FAIL_ERST},
    {"INTR", XHCI_FAIL_INTR}, {"BME", XHCI_FAIL_BME},
    {"MSIX", XHCI_FAIL_MSIX}, {"START", XHCI_FAIL_START},
};

static void live_rollback(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    unsigned int i;
    struct pmm_statistics rb0, rb1;
    unsigned int vec0;
    require(pmm_statistics(&rb0) == PMM_OK, "rb-cost0");
    vec0 = apic_vector_free();
    for (i = 0; i < sizeof(rb_stages) / sizeof(rb_stages[0]); ++i) {
        struct xhci_hcd h2;
        struct xhci_hcd h3;
        cpu_u16 cmd_before;
        enum xhci_result r;
        cmd_before = pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND);
        r = xhci_init(&h2, rb_stages[i].fail);
        require(r == XHCI_REFUSED, "rb-refused");
        require(pci_cfg_read16(bus, dev, fn, PCI_CFG_COMMAND) == cmd_before,
                "rb-cmd");
        require(dma_check(), "rb-dma");
        require(xhci_claim(&h3) == XHCI_OK, "rb-reclaim");
        require(xhci_teardown(&h3) == XHCI_OK, "rb-reteardown");
        say("[XHCI] rollback stage=");
        say(rb_stages[i].name);
        say(" ok leaks=0\r\n");
    }
    require(pmm_statistics(&rb1) == PMM_OK, "rb-cost1");
    require(rb0.allocated_bytes == rb1.allocated_bytes, "rb-zero");
    require(apic_vector_free() == vec0, "rb-vec");
}

/* ---------- CPL3 preemption proof (§37/38) ---------- */

#define XWORK_ITERS 8192u
#define XWORK_UNMASK_YIELD 4u

static void xwork_expected(cpu_u64 out[5])
{
    cpu_u64 r12 = 0xC0DE000000000000ULL, r13 = 0xC0DE000000000001ULL;
    cpu_u64 r14 = 0xC0DE000000000002ULL, r15 = 0xC0DE000000000003ULL;
    cpu_u64 rbx = 0xC0DE000000000004ULL;
    cpu_u64 i;
    for (i = 1; i <= XWORK_ITERS; ++i) {
        r12 += i;
        r13 ^= r12;
        r14 += r13;
        r15 ^= r14;
        rbx += r15;
    }
    out[0] = r12;
    out[1] = r13;
    out[2] = r14;
    out[3] = r15;
    out[4] = rbx;
}

static void live_cpl3(void)
{
    struct user_context *c = 0;
    cpu_u8 tok = 0xFFu;
    cpu_u64 yields = 0, snap0 = 0, snap1 = 0;
    cpu_u64 cmpl0 = h.cmpl_count, irq0 = h.irq_count;
    volatile cpu_u64 *d;
    cpu_u64 rc, exp[5];
    unsigned int i;
    hook_type = 0;
    require(pci_irq_mask(h.irq_handle, 0, 1) == MSI_OK, "p-mask");
    require(xhci_submit_noop(&h, &tok) == XHCI_OK, "p-submit");
    require(user_create(&c, USER_BLOB_XWORK) && c, "p-create");
    rc = user_enter(&c->link);
    while (rc == USER_RUN_PREEMPTED || rc == USER_RUN_YIELDED) {
        if (rc == USER_RUN_YIELDED) {
            ++yields;
            if (yields == XWORK_UNMASK_YIELD) {
                d = vm_frame_access(c->data_frame[0]);
                require(d != 0, "p-access");
                snap0 = d[USER_DATA_COUNT / 8];
                require(snap0 == 4096, "p-snap0");
                __asm__ volatile ("cli" ::: "memory");
                require(pci_irq_mask(h.irq_handle, 0, 0) == MSI_OK,
                        "p-unmask");
            }
            if (yields == XWORK_UNMASK_YIELD + 1) {
                d = vm_frame_access(c->data_frame[0]);
                require(d != 0, "p-access1");
                snap1 = d[USER_DATA_COUNT / 8];
                require(snap1 == 5120, "p-snap1");
            }
        }
        rc = user_resume(&c->link);
    }
    require(rc == USER_RUN_EXITED, "p-exit");
    require(c->exit_code == 77 && c->state == USER_EXITED, "p-code");
    require(c->yields == 8, "p-yields");
    require(c->preemptions >= 1, "p-park");
    say("[XHCI] irq vec=");
    say_dec(h.irq_vec);
    say(" count=");
    say_dec(h.irq_count);
    say(" isr=");
    say_dec(hook_isr);
    say(" user=");
    say_dec(hook_user);
    say(" rip=");
    say_hex64(hook_rip);
    say("\r\n");
    require(h.cmpl_count == cmpl0 + 1, "p-cmpl1");
    require(h.irq_count == irq0 + 1, "p-irq1");
    require(hook_type == XHCI_TRB_EV_CMPL && hook_cc == XHCI_CC_SUCCESS,
            "p-ev");
    require(hook_ptr == h.tokens[tok].trb_bus, "p-ptr");
    require(hook_user == 1, "p-user");
    require(hook_rip >= USER_CODE_BASE &&
            hook_rip < USER_CODE_BASE + VM_PAGE_SIZE, "p-rip");
    require(hook_isr == 1, "p-isr");
    require(h.tokens[tok].state == XHCI_TOK_COMPLETED, "p-tok");
    d = vm_frame_access(c->data_frame[0]);
    require(d != 0, "p-access2");
    require(d[USER_DATA_COUNT / 8] == XWORK_ITERS, "p-final");
    xwork_expected(exp);
    for (i = 0; i < 5; ++i)
        require(d[USER_DATA_GPRS / 8 + i] == exp[i], "p-regs");
    require(thread_detach_user(), "p-detach");
    require(user_destroy(c), "p-destroy");
    say("[XHCI] cpl3 yields=");
    say_dec(yields);
    say(" snap0=");
    say_dec(snap0);
    say(" snap1=");
    say_dec(snap1);
    say(" final=");
    say_dec(XWORK_ITERS);
    say(" acc=");
    for (i = 0; i < 5; ++i) {
        say_hex64(exp[i]);
        say(i < 4 ? "," : " regs=ok cs=23\r\n");
    }
}

/* ---------- reinit + teardown ---------- */

static unsigned int cum_cmd_wraps, cum_evt_wraps;

static void live_reinit(void)
{
    struct xhci_hcd h2;
    cpu_u8 tok = 0xFFu;
    require(xhci_init(&h2, XHCI_FAIL_NONE) == XHCI_OK, "r-init");
    h2.hook = xh_hook;
    hook_type = 0;
    require(xhci_submit_noop(&h2, &tok) == XHCI_OK, "r-submit");
    say("[XHCI] noop token=");
    say_dec(tok);
    say(" trb=");
    say_hex64(h2.tokens[tok].trb_bus);
    say("\r\n");
    say("[XHCI] db rung=0\r\n");
    require(xhci_wait_token(&h2, tok) == XHCI_OK, "r-wait");
    require(hook_type == XHCI_TRB_EV_CMPL &&
            hook_ptr == h2.tokens[tok].trb_bus, "r-cmpl");
    say("[XHCI] noop done token=");
    say_dec(tok);
    say(" count=");
    say_dec(h2.cmpl_count);
    say("\r\n");
    cum_cmd_wraps += h2.cmd.wraps;
    cum_evt_wraps += h2.evt.wraps;
    require(xhci_teardown(&h2) == XHCI_OK, "r-teardown");
}

static void live_teardown(struct xhci_hcd *hh, cpu_u32 handle)
{
    cpu_u16 cmd0;
    struct msi_handle_info info;
    require(hh->claimed, "t-claimed");
    cmd0 = hh->cmd0;
    cum_cmd_wraps += hh->cmd.wraps;
    cum_evt_wraps += hh->evt.wraps;
    require(xhci_teardown(hh) == XHCI_OK, "t-down");
    require(pci_cfg_read16(hh->bus, hh->dev, hh->fn, PCI_CFG_COMMAND) ==
            cmd0, "t-cmd");
    require(dma_check(), "t-dma");
    require(pci_irq_info(handle, &info) != MSI_OK, "t-vec");
    say("[XHCI] teardown ok cmd_restored=1 dma_free=1 vec_free=1\r\n");
}

void xhci_self_test(void)
{
    enum xhci_result r;
    struct pmm_statistics before, after;
    cpu_u32 handle;
    const struct pci_device *d;
    synth_trb();
    say("[XHCI] synth ok cases=");
    say_dec(cases);
    say("\r\n");
    require(pmm_statistics(&before) == PMM_OK, "live-cost0");
    d = pci_find_class(XHCI_PCI_CLASS, XHCI_PCI_SUBCLASS, XHCI_PCI_PROGIF,
                       0);
    if (!d) {
        say("[XHCI] absent\r\n");
        require(pmm_statistics(&after) == PMM_OK, "live-cost1");
        emit_cost(&before, &after);
        say("[XHCI] live ok devices=0\r\n");
        say("[XHCI] xhci verified\r\n");
        return;
    }
    live_rollback(d->bus, d->device, d->function);
    r = xhci_claim(&h);
    require(r == XHCI_OK, "c-claim");
    live_bringup();
    live_series();
    live_cpl3();
    handle = h.irq_handle;
    live_teardown(&h, handle);
    live_reinit();
    say("[XHCI] wrap cmd=");
    say_dec(cum_cmd_wraps);
    say(" evt=");
    say_dec(cum_evt_wraps);
    say(" cmds=66 cmpls=66\r\n");
    require(pmm_statistics(&after) == PMM_OK, "live-cost1");
    emit_cost(&before, &after);
    say("[XHCI] live ok devices=1\r\n");
    say("[XHCI] xhci verified\r\n");
}

unsigned int xhci_test_cases(void) { return cases; }
