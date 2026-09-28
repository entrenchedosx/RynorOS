/* PCI-A1 gated self-test (RYNOR_PCI_TEST images only).
 *
 * Three phases: (1) transport unit rows over a scripted mock backend
 * (lane extraction, RMW writes, fail-closed validation); (2) a
 * synthetic topology through the same mock (multifunction, nested
 * bridges, malformed ranges/loops, unknown classes, 32/64/IO/zero/
 * malformed BARs, restoration, re-scan determinism); (3) live QEMU
 * hardware (evidence rows for host pinning, restoration re-check,
 * MMIO map/query/unmap proof incl. a display cross-read). Terminates
 * with "[PCI] pci verified". Production builds never call this.
 */
#include "pci.h"
#include "cpu.h"
#include "display.h"
#include "io.h"
#include "serial.h"
#include "vm.h"

static void say(const char *s)
{
    (void)serial_write(s);
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
        char c[2];
        c[0] = buf[i];
        c[1] = 0;
        say(c);
    }
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
        buf[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    }
    for (i = n - 1; i >= 0; --i) {
        char c[2];
        c[0] = buf[i];
        c[1] = 0;
        say(c);
    }
}

static void fail(const char *tag) __attribute__((noreturn));
static void fail(const char *tag)
{
    say("[PCI] failure=");
    say(tag);
    say("\r\n");
    (void)serial_flush();
    cpu_halt();
}

static void require(int ok, const char *tag)
{
    if (!ok)
        fail(tag);
}

/* ---------- mock config-space backend ---------- */

#define MOCK_FNS 15u

struct mock_bar_desc {
    cpu_u32 raw;     /* firmware BAR value (0 = unimplemented) */
    cpu_u32 mask;    /* size mask returned while sizing is armed */
    cpu_u32 raw_hi;  /* 64-bit high half value */
    cpu_u32 mask_hi; /* 64-bit high size mask */
    cpu_u8 is64;
};

struct mock_fn {
    cpu_u8 bus;
    cpu_u8 dev;
    cpu_u8 fn;
    cpu_u16 vendor;
    cpu_u16 device;
    cpu_u16 command;
    cpu_u16 status;
    cpu_u8 rev;
    cpu_u8 prog;
    cpu_u8 sub;
    cpu_u8 cls;
    cpu_u8 header; /* includes MF bit */
    cpu_u8 irq_line;
    cpu_u8 irq_pin;
    cpu_u8 sec;
    cpu_u8 sub_bus;
    cpu_u8 flaky; /* vendor vanishes once mock_flake_armed is set */
    struct mock_bar_desc bar[6];
};

static const struct mock_fn mock_fixtures[MOCK_FNS] = {
    /* 0: full endpoint: mem32 + IO + mem64 + min-size mem32. */
    { 0, 1, 0, 0xABCDu, 0xEF01u, 0x0007u, 0x0210u, 1, 0, 0, 2, 0x00u,
      0xFFu, 1, 0, 0, 0,
      { { 0xF0000000u, 0xFFFFF000u, 0, 0, 0 },
        { 0x0000C001u, 0xFFFFFFC0u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 },
        { 0x0000000Cu, 0xFFE00000u, 0x00000001u, 0xFFFFFFFFu, 1 },
        { 0, 0, 0, 0, 0 },
        { 0xF0100000u, 0xFFFFFFF0u, 0, 0, 0 } } },
    /* 1: bridge to bus 1..3 with a small MMIO BAR (MF: fn1 lives
       on the same device, so fn0 must advertise multifunction). */
    { 0, 2, 0, 0x11AAu, 0x22BBu, 0x0007u, 0x0210u, 0, 0, 4, 6, 0x81u,
      0xFFu, 1, 1, 3, 0,
      { { 0xF0200000u, 0xFFFFFF00u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 } } },
    /* 2: unknown-class endpoint, decode already off, empty BARs. */
    { 0, 2, 1, 0x1234u, 0x5678u, 0x0000u, 0x0000u, 0, 0xFFu, 0xFFu,
      0xFFu, 0x00u, 0, 0, 0, 0, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 3/4: multifunction pair; fn1 is flaky for re-scan tests. */
    { 0, 3, 0, 0xDEADu, 0xBEEFu, 0x0001u, 0x0200u, 2, 0, 1, 1, 0x80u,
      5, 1, 0, 0, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    { 0, 3, 1, 0xDEADu, 0xBEEFu, 0x0001u, 0x0200u, 2, 0, 1, 1, 0x00u,
      5, 2, 0, 0, 1,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 5: invalid header layout: identity survives, no BARs. */
    { 0, 4, 0, 0xAAAAu, 0xBBBBu, 0x0007u, 0x0210u, 0, 0, 0, 3, 0x02u,
      0xFFu, 1, 0, 0, 0,
      { { 0xF0400000u, 0xFFFFF000u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 6: bus-1 endpoint behind the bridge. */
    { 1, 0, 0, 0xBBBBu, 0xCCCCu, 0x0007u, 0x0210u, 0, 1, 4, 4, 0x00u,
      9, 1, 0, 0, 0,
      { { 0xF0300000u, 0xFFFFE000u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 7: malformed BARs: zero-mask, overflow, misaligned, orphan-64. */
    { 1, 1, 0, 0xEEEFu, 0x1111u, 0x0007u, 0x0210u, 0, 0, 0, 2, 0x00u,
      9, 1, 0, 0, 0,
      { { 0xF0400000u, 0x00000000u, 0, 0, 0 },
        { 0xFFFFFFF0u, 0xFFFFFFE0u, 0, 0, 0 },
        { 0xF0000100u, 0xFFFFF000u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0x00000004u, 0xFFFFF000u, 0, 0, 1 } } },
    /* 8: nested bridge bus1 -> bus2. */
    { 1, 2, 0, 0x11AAu, 0x33CCu, 0x0007u, 0x0210u, 0, 0, 4, 6, 0x01u,
      9, 1, 2, 2, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 9: malformed bridge range (sec > sub): ignored. */
    { 1, 5, 0, 0x11AAu, 0x44DDu, 0x0007u, 0x0210u, 0, 0, 4, 6, 0x01u,
      9, 1, 5, 2, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 10: self-loop bridge (sec == own bus): ignored. */
    { 1, 6, 0, 0x11AAu, 0x55EEu, 0x0007u, 0x0210u, 0, 0, 4, 6, 0x01u,
      9, 1, 1, 1, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 11: bridge to an already-visited bus: ignored. */
    { 1, 7, 0, 0x11AAu, 0x66FFu, 0x0007u, 0x0210u, 0, 0, 4, 6, 0x01u,
      9, 1, 0, 0, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 12: endpoint behind the nested bridge. */
    { 2, 0, 0, 0xCCCCu, 0xDDDDu, 0x0003u, 0x0210u, 0, 3, 3, 0x0Cu,
      0x00u, 10, 1, 0, 0, 0,
      { { 0x0000C101u, 0xFFFFFFE0u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 13: unclaimed bus-3 device: never visited, never registered. */
    { 3, 0, 0, 0xDDDDu, 0xEEEEu, 0x0007u, 0x0210u, 0, 0, 0, 2, 0x00u,
      11, 1, 0, 0, 0,
      { { 0xF0500000u, 0xFFFFF000u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 14: bridge to an already-visited bus (1, nonzero, not our own
       bus 2): ONLY the visited guard ignores it. Removing the guard
       re-enqueues bus 1 until the registry overflows. */
    { 2, 1, 0, 0x11AAu, 0x77AAu, 0x0007u, 0x0210u, 0, 0, 4, 6, 0x01u,
      10, 1, 1, 1, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
};

/* Mutable mock state. Probing is strictly sequential (each BAR is
   restored before the next is touched) and the transport tests
   read back the slot they just wrote, so one last-write latch, one
   sizing-armed flag, and one high-half latch are exact (BSS budget:
   the old flat BIOS window left no room for per-slot arrays). COMMAND
   words are genuinely concurrent across fixtures and stay per-fixture.
   Reset before each mock phase. */
static cpu_u8 mock_last_valid;
static cpu_u8 mock_last_fi;
static cpu_u8 mock_last_slot;
static cpu_u32 mock_last_val;
static cpu_u8 mock_armed;
static cpu_u8 mock_armed_fi;
static cpu_u8 mock_armed_slot;
static cpu_u8 mock_hi_valid;
static cpu_u8 mock_hi_fi;
static cpu_u8 mock_hi_slot;
static cpu_u32 mock_hi_val;
static cpu_u16 mock_cmd[MOCK_FNS];
static int mock_flake_armed;

static void mock_reset(void)
{
    cpu_u32 i;
    mock_last_valid = 0;
    mock_last_fi = 0;
    mock_last_slot = 0;
    mock_last_val = 0;
    mock_armed = 0;
    mock_armed_fi = 0;
    mock_armed_slot = 0;
    mock_hi_valid = 0;
    mock_hi_fi = 0;
    mock_hi_slot = 0;
    mock_hi_val = 0;
    for (i = 0; i < MOCK_FNS; ++i)
        mock_cmd[i] = mock_fixtures[i].command;
    mock_flake_armed = 0;
}

static int mock_find(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    cpu_u32 i;
    for (i = 0; i < MOCK_FNS; ++i)
        if (mock_fixtures[i].bus == bus && mock_fixtures[i].dev == dev &&
            mock_fixtures[i].fn == fn)
            return (int)i;
    return -1;
}

static cpu_u32 mock_read(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off)
{
    const struct mock_fn *f;
    int i = mock_find(bus, dev, fn);
    int is_bridge;
    cpu_u32 slot;
    if (i < 0)
        return 0xFFFFFFFFu;
    f = &mock_fixtures[(cpu_u32)i];
    is_bridge = (f->header & 0x7Fu) == 1;
    if (off == PCI_CFG_VENDOR_ID && f->flaky && mock_flake_armed)
        return 0xFFFFFFFFu; /* vendor vanishes on re-scan */
    switch (off) {
    case 0x00u:
        return (cpu_u32)f->vendor | ((cpu_u32)f->device << 16);
    case 0x04u:
        return (cpu_u32)mock_cmd[i] | ((cpu_u32)f->status << 16);
    case 0x08u:
        return (cpu_u32)f->rev | ((cpu_u32)f->prog << 8) |
            ((cpu_u32)f->sub << 16) | ((cpu_u32)f->cls << 24);
    case 0x0Cu:
        /* Header type (with MF bit) lives in byte 2 of this dword. */
        return (cpu_u32)f->header << 16;
    case 0x28u:
    case 0x2Cu:
    case 0x30u:
    case 0x34u:
    case 0x38u:
        return 0;
    case 0x18u:
        /* Bridge bus numbers, or endpoint BAR2: same addresses,
           different layouts. */
        if (!is_bridge)
            break;
        return (cpu_u32)f->bus | ((cpu_u32)f->sec << 8) |
            ((cpu_u32)f->sub_bus << 16);
    case 0x1Cu:
    case 0x20u:
    case 0x24u:
        /* Bridge windows, or endpoint BAR3/4/5. */
        if (!is_bridge)
            break;
        return 0;
    case 0x3Cu:
        return (cpu_u32)f->irq_line | ((cpu_u32)f->irq_pin << 8);
    default:
        break;
    }
    if (off >= PCI_CFG_BAR0 && off < PCI_CFG_BAR0 + 24u &&
        ((off - PCI_CFG_BAR0) % 4u) == 0) {
        const struct mock_bar_desc *b;
        slot = (off - PCI_CFG_BAR0) / 4u;
        /* A consumed neighbor slot reads the 64-bit high half. */
        if (slot > 0 && f->bar[slot - 1u].is64) {
            b = &f->bar[slot - 1u];
            if (mock_armed && mock_armed_fi == (cpu_u8)i &&
                mock_armed_slot == (cpu_u8)(slot - 1u))
                return b->mask_hi;
            if (mock_hi_valid && mock_hi_fi == (cpu_u8)i &&
                mock_hi_slot == (cpu_u8)(slot - 1u))
                return mock_hi_val;
            return b->raw_hi;
        }
        b = &f->bar[slot];
        if (mock_armed && mock_armed_fi == (cpu_u8)i &&
            mock_armed_slot == (cpu_u8)slot) {
            /* Sizing read: size mask with the BAR's flag bits. */
            if (b->raw & 1u)
                return (b->mask & 0xFFFFFFFCu) | 1u;
            return (b->mask & 0xFFFFFFF0u) | (b->raw & 0xFu);
        }
        if (mock_last_valid && mock_last_fi == (cpu_u8)i &&
            mock_last_slot == (cpu_u8)slot)
            return mock_last_val;
        return b->raw;
    }
    return 0;
}

static void mock_write(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                       cpu_u32 val)
{
    int i = mock_find(bus, dev, fn);
    cpu_u32 slot;
    if (i < 0)
        return;
    if (off == 0x04u) {
        mock_cmd[i] = (cpu_u16)(val & 0xFFFFu);
        return;
    }
    if (off < PCI_CFG_BAR0 || off >= PCI_CFG_BAR0 + 24u ||
        ((off - PCI_CFG_BAR0) % 4u) != 0)
        return; /* only BAR slots + COMMAND are writable here */
    slot = (off - PCI_CFG_BAR0) / 4u;
    if (slot > 0 && mock_fixtures[i].bar[slot - 1u].is64) {
        /* High half of the previous slot's 64-bit BAR. */
        if (val == 0xFFFFFFFFu) {
            mock_armed = 1;
            mock_armed_fi = (cpu_u8)i;
            mock_armed_slot = (cpu_u8)(slot - 1u);
        } else {
            mock_hi_valid = 1;
            mock_hi_fi = (cpu_u8)i;
            mock_hi_slot = (cpu_u8)(slot - 1u);
            mock_hi_val = val;
            mock_armed = 0;
        }
        return;
    }
    if (val == 0xFFFFFFFFu) {
        mock_armed = 1;
        mock_armed_fi = (cpu_u8)i;
        mock_armed_slot = (cpu_u8)slot;
    } else {
        mock_last_valid = 1;
        mock_last_fi = (cpu_u8)i;
        mock_last_slot = (cpu_u8)slot;
        mock_last_val = val;
        mock_armed = 0;
    }
}

static const struct pci_cfg_ops mock_ops = { mock_read, mock_write };

/* ---------- phase 1: transport unit rows ---------- */

static void phase_transport(void)
{
    /* Fixture 0 header dword 0: vendor ABCD, device EF01. */
    mock_reset();
    pci_cfg_install_ops(&mock_ops);
    require(pci_cfg_read32(0, 1, 0, 0) == 0xEF01ABCDu, "t-dword");
    require(pci_cfg_read8(0, 1, 0, 0) == 0xCDu, "t-lane0");
    require(pci_cfg_read8(0, 1, 0, 1) == 0xABu, "t-lane1");
    require(pci_cfg_read8(0, 1, 0, 2) == 0x01u, "t-lane2");
    require(pci_cfg_read8(0, 1, 0, 3) == 0xEFu, "t-lane3");
    require(pci_cfg_read16(0, 1, 0, 0) == 0xABCDu, "t-word0");
    require(pci_cfg_read16(0, 1, 0, 2) == 0xEF01u, "t-word2");
    /* Class dword 0x08: rev 01 prog 00 sub 00 class 02. */
    require(pci_cfg_read32(0, 1, 0, 8) == 0x02000001u, "t-class");
    require(pci_cfg_read8(0, 1, 0, 0x0B) == 2, "t-class-lane");
    /* RMW writes land in the right lane, neighbors preserved. */
    pci_cfg_write8(0, 1, 0, 0x10, 0x5Au);
    require(pci_cfg_read32(0, 1, 0, 0x10) == 0xF000005Au, "t-w8");
    pci_cfg_write16(0, 1, 0, 0x10, 0x1234u);
    require(pci_cfg_read32(0, 1, 0, 0x10) == 0xF0001234u, "t-w16lo");
    pci_cfg_write16(0, 1, 0, 0x12, 0x5678u);
    require(pci_cfg_read32(0, 1, 0, 0x10) == 0x56781234u, "t-w16hi");
    pci_cfg_write32(0, 1, 0, 0x10, 0xF0000000u);
    require(pci_cfg_read32(0, 1, 0, 0x10) == 0xF0000000u, "t-w32");
    require(pci_cfg_read16(0, 1, 0, 4) == 7, "t-cmd");
    pci_cfg_write16(0, 1, 0, 4, 0x0042u);
    require(pci_cfg_read16(0, 1, 0, 4) == 0x42u, "t-cmdw");
    /* Fail-closed validation: bad BDF/offset reads all-ones. */
    require(pci_cfg_read8(256, 0, 0, 0) == 0xFFu, "t-badbus8");
    require(pci_cfg_read16(0, 32, 0, 0) == 0xFFFFu, "t-baddev16");
    require(pci_cfg_read32(0, 0, 8, 0) == 0xFFFFFFFFu, "t-badfn32");
    require(pci_cfg_read8(0, 1, 0, 256) == 0xFFu, "t-badoff8");
    require(pci_cfg_read16(0, 1, 0, 1) == 0xFFFFu, "t-odd16");
    require(pci_cfg_read16(0, 1, 0, 255) == 0xFFFFu, "t-edge16");
    require(pci_cfg_read16(0, 1, 0, 254) == 0, "t-edge16ok");
    require(pci_cfg_read32(0, 1, 0, 1) == 0xFFFFFFFFu, "t-unal32");
    require(pci_cfg_read32(0, 9, 9, 9) == 0xFFFFFFFFu, "t-absent");
    /* Invalid writes are ignored (BAR0 keeps its value). */
    pci_cfg_write32(0, 1, 0, 1, 0x12345678u);
    pci_cfg_write32(9, 9, 9, 0x10, 0x12345678u);
    require(pci_cfg_read32(0, 1, 0, 0x10) == 0xF0000000u, "t-wign");
    say("[PCI] transport ok\r\n");
}

/* ---------- phase 2: synthetic topology ---------- */

static const struct pci_device *synth_dev(cpu_u32 bus, cpu_u32 dev,
                                          cpu_u32 fn, const char *tag)
{
    const struct pci_device *d = pci_find_bdf(bus, dev, fn);
    require(d != 0, tag);
    return d;
}

static void phase_synthetic(void)
{
    const struct pci_device *d;
    struct pci_stats st;
    cpu_u32 i;
    cpu_u32 s;
    mock_reset();
    pci_cfg_install_ops(&mock_ops);
    require(pci_initialize() == PCI_OK, "s-init");
    require(pci_device_count() == 14u, "s-count");
    /* Deterministic bus -> device -> function order. */
    for (i = 1; i < 14u; ++i) {
        const struct pci_device *a = pci_device_at(i - 1u);
        const struct pci_device *b = pci_device_at(i);
        cpu_u32 ka = (a->bus << 16) | (a->device << 8) | a->function;
        cpu_u32 kb = (b->bus << 16) | (b->device << 8) | b->function;
        require(a != 0 && b != 0 && ka < kb, "s-order");
    }
    require(pci_device_at(14) == 0, "s-oob");
    /* Fixture 0: identity + all four BAR kinds. */
    d = synth_dev(0, 1, 0, "s-f0");
    require(d->vendor_id == 0xABCDu && d->device_id == 0xEF01u, "s-f0id");
    require(d->class_code == 2 && d->subclass == 0 && d->prog_if == 0 &&
            d->revision == 1, "s-f0cls");
    require(d->header_type == 0 && d->multifunction == 0, "s-f0hdr");
    require(d->command == 7 && d->status == 0x210u, "s-f0cmd");
    require(d->bar_count == 4u, "s-f0bars");
    require(d->bars[0].kind == PCI_BAR_MMIO32 && d->bars[0].index == 0 &&
            d->bars[0].base == 0xF0000000u && d->bars[0].size == 0x1000u,
            "s-f0b0");
    require(d->bars[1].kind == PCI_BAR_IO && d->bars[1].index == 1 &&
            d->bars[1].base == 0xC000u && d->bars[1].size == 0x40u,
            "s-f0b1");
    require(d->bars[2].kind == PCI_BAR_MMIO64 && d->bars[2].index == 3 &&
            d->bars[2].is64 && d->bars[2].prefetchable &&
            d->bars[2].base == 0x100000000u &&
            d->bars[2].size == 0x200000u, "s-f0b64");
    require(d->bars[3].kind == PCI_BAR_MMIO32 && d->bars[3].index == 5 &&
            d->bars[3].size == 0x10u, "s-f0b5");
    /* Bridge windows recorded, traversal follows only valid ones. */
    d = synth_dev(0, 2, 0, "s-br0");
    require(d->header_layout == 1 && d->sec_bus == 1 && d->sub_bus == 3,
            "s-br0win");
    require(d->bar_count == 1u && d->bars[0].size == 0x100u, "s-br0bar");
    d = synth_dev(0, 2, 1, "s-unk");
    require(d->class_code == 0xFFu && d->bar_count == 0, "s-unkcls");
    require(pci_class_name(0xFFu, 0xFFu)[0] == 'u', "s-unkname");
    require(pci_class_name(1, 1)[0] == 'i', "s-clside");
    require(pci_class_name(2, 0)[0] == 'n', "s-clsnet");
    require(pci_class_name(6, 4)[0] == 'p', "s-clsbr");
    d = synth_dev(0, 3, 0, "s-mf0");
    require(d->multifunction == 1, "s-mfbit");
    d = synth_dev(0, 3, 1, "s-mf1");
    require(d->vendor_id == 0xDEADu, "s-mffn1");
    d = synth_dev(0, 4, 0, "s-badhdr");
    require(d->header_layout == 2 && d->bar_count == 0, "s-badhdrbars");
    d = synth_dev(1, 0, 0, "s-b1");
    require(d->bars[0].size == 0x2000u, "s-b1bar");
    d = synth_dev(1, 1, 0, "s-mal");
    require(d->bar_count == 0, "s-malbars");
    d = synth_dev(1, 2, 0, "s-nest");
    require(d->sec_bus == 2 && d->sub_bus == 2, "s-nestwin");
    d = synth_dev(2, 0, 0, "s-b2");
    require(d->bars[0].kind == PCI_BAR_IO && d->bars[0].size == 0x20u,
            "s-b2bar");
    /* Malformed bridges are registered but never followed. */
    require(synth_dev(1, 5, 0, "s-malbr") != 0, "s-malbr");
    require(synth_dev(1, 6, 0, "s-selfbr") != 0, "s-selfbr");
    require(synth_dev(1, 7, 0, "s-visbr") != 0, "s-visbr");
    d = synth_dev(2, 1, 0, "s-visbr2");
    require(d->sec_bus == 1 && d->sub_bus == 1, "s-visbr2win");
    require(pci_find_bdf(3, 0, 0) == 0, "s-unclaimed");
    require(pci_find_bdf(0, 9, 0) == 0, "s-nobdf");
    /* Query API. */
    require(pci_find_vendor_device(0xDEADu, 0xBEEFu, 0)->function == 0,
            "s-qvd0");
    require(pci_find_vendor_device(0xDEADu, 0xBEEFu, 4)->function == 1,
            "s-qvd1");
    require(pci_find_vendor_device(0xDEADu, 0xBEEFu, 5) == 0, "s-qvdend");
    require(pci_find_class(6, 4, PCI_MATCH_ANY, 0)->bus == 0, "s-qcls");
    require(pci_find_class(PCI_MATCH_ANY, PCI_MATCH_ANY, PCI_MATCH_ANY,
                           13)->bus == 2, "s-qany");
    require(pci_find_class(9, 9, 9, 0) == 0, "s-qnone");
    require(pci_find_class(0x100u, 0, 0, 0) == 0, "s-qbad");
    pci_stats_read(&st);
    require(st.buses_scanned == 3u && st.functions_seen == 14u, "s-stats");
    /* Restoration: every fixture BAR + COMMAND reads back original
       (unvisited fixtures were never probed: pristine too). No probe
       left the sizing latch armed. */
    require(mock_armed == 0, "s-bararmed");
    for (i = 0; i < MOCK_FNS; ++i) {
        const struct mock_fn *f = &mock_fixtures[i];
        cpu_u16 cmd;
        for (s = 0; s < 6u; ++s) {
            cpu_u32 cur;
            cpu_u32 want;
            /* Bridges have no BAR slots past slot 1: those offsets
               are bus-number/window registers, never probed. */
            if ((f->header & 0x7Fu) == 1 && s >= 2u)
                continue;
            cur = pci_cfg_read32(f->bus, f->dev, f->fn,
                                 PCI_CFG_BAR0 + s * 4u);
            if (s > 0 && f->bar[s - 1u].is64)
                want = f->bar[s - 1u].raw_hi;
            else
                want = f->bar[s].raw;
            require(cur == want, "s-barcur");
        }
        cmd = pci_cfg_read16(f->bus, f->dev, f->fn, PCI_CFG_COMMAND);
        require(cmd == f->command, "s-cmdcur");
    }
    say("[PCI] synthetic ok devices=14 buses=3\r\n");
    /* Re-scan determinism under change: flaky fn1 vanishes cleanly. */
    mock_flake_armed = 1;
    require(pci_initialize() == PCI_OK, "s-flakeinit");
    require(pci_device_count() == 13u, "s-flakecount");
    require(pci_find_bdf(0, 3, 1) == 0, "s-flakegone");
    require(pci_find_bdf(0, 3, 0) != 0, "s-flakefn0");
    mock_flake_armed = 0;
    require(pci_initialize() == PCI_OK, "s-flakereinit");
    require(pci_device_count() == 14u, "s-flakeback");
    say("[PCI] rescan ok\r\n");
}

/* ---------- phase 3: live hardware ---------- */

static void say_bdf(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    say("bus=");
    say_dec(bus);
    say(" dev=");
    say_dec(dev);
    say(" fn=");
    say_dec(fn);
}

static void emit_dev(const struct pci_device *d)
{
    say("[PCI] dev ");
    say_bdf(d->bus, d->device, d->function);
    say(" vendor=");
    say_hex64(d->vendor_id);
    say(" device=");
    say_hex64(d->device_id);
    say(" class=");
    say_hex64(d->class_code);
    say(" sub=");
    say_hex64(d->subclass);
    say(" prog=");
    say_hex64(d->prog_if);
    say(" rev=");
    say_hex64(d->revision);
    say(" hdr=");
    say_hex64(d->header_type);
    say(" mf=");
    say_dec(d->multifunction);
    say(" cmd=");
    say_hex64(d->command);
    say(" status=");
    say_hex64(d->status);
    say(" irql=");
    say_hex64(d->irq_line);
    say(" irqp=");
    say_hex64(d->irq_pin);
    say(" nbar=");
    say_dec(d->bar_count);
    say("\r\n");
}

static void emit_bar(const struct pci_device *d, cpu_u32 entry)
{
    const struct pci_bar *b = &d->bars[entry];
    say("[PCI] bar ");
    say_bdf(d->bus, d->device, d->function);
    say(" index=");
    say_dec(b->index);
    say(" kind=");
    say(b->kind == PCI_BAR_IO ? "io" :
        b->kind == PCI_BAR_MMIO64 ? "mmio64" : "mmio32");
    say(" base=");
    say_hex64(b->base);
    say(" size=");
    say_hex64(b->size);
    say(" prefetch=");
    say_dec(b->prefetchable);
    say(" rawhi=");
    say_hex64(b->raw_high);
    say("\r\n");
}

/* Live restoration re-check: enumeration-time BAR/COMMAND values must
   still read back (probing left nothing mutated). */
static void live_restore(const struct pci_device *d)
{
    cpu_u32 e;
    for (e = 0; e < d->bar_count; ++e) {
        const struct pci_bar *b = &d->bars[e];
        cpu_u32 off = PCI_CFG_BAR0 + (cpu_u32)b->index * 4u;
        require(pci_cfg_read32(d->bus, d->device, d->function, off) ==
                b->raw_low, "live-barlow");
        if (b->is64)
            require(pci_cfg_read32(d->bus, d->device, d->function,
                                   off + 4u) == b->raw_high,
                    "live-barhigh");
    }
    require(pci_cfg_read16(d->bus, d->device, d->function,
                           PCI_CFG_COMMAND) == d->command,
            "live-cmd");
    say("[PCI] restore ");
    say_bdf(d->bus, d->device, d->function);
    say(" ok=1\r\n");
}

/* Map one BAR, verify every page structurally, cross-read the
   framebuffer candidate through the display path, unmap, and prove
   the pages are gone. Returns 1 when the display cross-read ran. */
static int live_map(const struct pci_device *d, cpu_u32 entry,
                    int *fb_done)
{
    struct pci_bar *b;
    cpu_u64 va = 0;
    cpu_u64 pa_page;
    cpu_u64 pages;
    cpu_u64 total;
    cpu_u64 i;
    cpu_u64 xcheck = 0;
    /* Re-borrow mutable: map records into the registry. */
    const struct pci_device *re = pci_find_bdf(d->bus, d->device,
                                               d->function);
    require(re != 0, "live-find");
    b = (struct pci_bar *)&re->bars[entry];
    require(pci_map_bar(d->bus, d->device, d->function, entry, &va) ==
            PCI_OK, "live-map");
    require(va == b->mapped_va && va != 0, "live-mappedva");
    /* Repeat map returns the stored address (no double allocation). */
    {
        cpu_u64 va2 = 0;
        require(pci_map_bar(d->bus, d->device, d->function, entry,
                            &va2) == PCI_OK && va2 == va,
                "live-mapagain");
    }
    pa_page = b->base & ~(cpu_u64)0xFFFu;
    total = (b->base & 0xFFFu) + b->size;
    pages = total / VM_PAGE_SIZE + (total % VM_PAGE_SIZE != 0);
    for (i = 0; i < pages; ++i) {
        struct vm_mapping m;
        require(vm_query(vm_kernel_space(), va - (b->base & 0xFFFu) +
                         i * VM_PAGE_SIZE, &m) == VM_OK,
                "live-query");
        require(m.physical == pa_page + i * VM_PAGE_SIZE, "live-pa");
        require(m.permissions == VM_WRITE && m.uncached, "live-uc");
    }
    /* Benign cross-read: the first large prefetchable aperture is the
       VGA framebuffer; pixel (0,0) must match the display path. Only
       this BAR is ever read; all others prove out structurally. */
    if (!*fb_done && b->kind == PCI_BAR_MMIO32 && b->prefetchable &&
        b->size >= 0x100000u) {
        cpu_u8 r = 0;
        cpu_u8 g = 0;
        cpu_u8 bl = 0;
        const volatile cpu_u8 *pix = (const volatile cpu_u8 *)va;
        require(display_read_pixel(0, 0, &r, &g, &bl) == 1, "live-disp");
        require(pix[0] == bl && pix[1] == g && pix[2] == r, "live-xread");
        *fb_done = 1;
        xcheck = 1;
    }
    require(pci_unmap_bar(d->bus, d->device, d->function, entry) ==
            PCI_OK, "live-unmap");
    {
        struct vm_mapping probe;
        probe.physical = 0;
        probe.permissions = 0;
        probe.uncached = 0;
        require(vm_query(vm_kernel_space(), va - (b->base & 0xFFFu),
                         &probe) == VM_NOT_MAPPED,
                "live-gone");
    }
    require(pci_unmap_bar(d->bus, d->device, d->function, entry) ==
            PCI_INVALID, "live-doubleunmap");
    say("[PCI] map ");
    say_bdf(d->bus, d->device, d->function);
    say(" entry=");
    say_dec(entry);
    say(" va=");
    say_hex64(va);
    say(" pages=");
    say_dec(pages);
    say(" xcheck=");
    say_dec(xcheck);
    say("\r\n");
    return (int)xcheck;
}

static void phase_live(void)
{
    cpu_u32 n;
    cpu_u32 i;
    cpu_u32 e;
    int fb_done = 0;
    int saw_io_refuse = 0;
    pci_cfg_install_ops(0); /* legacy CF8/CFC backend */
    require(pci_initialize() == PCI_OK, "live-init");
    n = pci_device_count();
    require(n > 0, "live-nonempty");
    for (i = 0; i < n; ++i) {
        const struct pci_device *d = pci_device_at(i);
        require(d != 0, "live-at");
        emit_dev(d);
        for (e = 0; e < d->bar_count; ++e)
            emit_bar(d, e);
        live_restore(d);
    }
    for (i = 0; i < n; ++i) {
        const struct pci_device *d = pci_device_at(i);
        for (e = 0; e < d->bar_count; ++e) {
            if (d->bars[e].kind == PCI_BAR_IO) {
                cpu_u64 va = 0;
                require(pci_map_bar(d->bus, d->device, d->function,
                                    e, &va) == PCI_UNSUPPORTED,
                        "live-iorefuse");
                saw_io_refuse = 1;
                continue;
            }
            if (live_map(d, e, &fb_done))
                require(fb_done == 1, "live-fbflag");
        }
        /* Bad entry ordinals are refused, never mapped. */
        {
            cpu_u64 va = 0;
            require(pci_map_bar(d->bus, d->device, d->function,
                                d->bar_count, &va) == PCI_INVALID,
                    "live-badentry");
        }
    }
    require(fb_done == 1, "live-fbxcheck");
    require(saw_io_refuse == 1, "live-iosaw");
    {
        cpu_u64 bad_va = 0;
        require(pci_map_bar(0, 31, 7, 0, &bad_va) == PCI_INVALID,
                "live-badbdf");
    }
    say("[PCI] live ok devices=");
    say_dec(n);
    say("\r\n");
}

void pci_self_test(void)
{
    struct pci_stats st;
    phase_transport();
    phase_synthetic();
    phase_live();
    pci_stats_read(&st);
    say("[PCI] stats reads=");
    say_dec(st.cfg_reads);
    say(" writes=");
    say_dec(st.cfg_writes);
    say(" buses=");
    say_dec(st.buses_scanned);
    say(" functions=");
    say_dec(st.functions_seen);
    say(" bars=");
    say_dec(st.bars_sized);
    say(" registry=");
    say_dec(st.registry_bytes);
    say("\r\n");
    say("[PCI] pci verified\r\n");
    (void)serial_flush();
}
