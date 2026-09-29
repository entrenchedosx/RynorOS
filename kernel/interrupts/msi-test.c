/* INT-A2 gated self-test (RYNOR_MSI_TEST images only).
 *
 * Phases: (A) walker matrix over a scripted mock backend (absent,
 * no-caps, MSI/MSI-X/both, unknown-ID skip, chains, malformed lists);
 * (B) MSI parse + message-builder + MSI-X geometry matrices (a scratch
 * fixture's cap bytes are scribbled per case; parse paths are
 * read-only); (C) aligned-allocator matrix on a snapshotted live pool;
 * (D) synthetic MSI enable/disable/mask/info/quiet/auto over the mock
 * (config-only; MSI-X enable needs table MMIO, so it is live-only);
 * (E) live QEMU hardware: edu MSI proof, xHCI MSI-X proof (test-owned
 * minimal bring-up), e1000e dual-cap arbiter, pci-testdev negative
 * control. Terminates with "[MSI] msi verified". Silent builds never
 * call this. Production code stays generic: every QEMU device fact
 * (edu offsets, xHCI bring-up) lives here and in docs/design/msi.md.
 */
#include "msi.h"
#include "cpu.h"
#include "dma.h"
#include "io.h"
#include "pmm.h"
#include "serial.h"
#include "vm.h"

static unsigned int cases;

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
    say("[MSI] failure=");
    say(tag);
    say(" detail=");
    say(msi_error());
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

unsigned int msi_test_cases(void) { return cases; }

/* ---------- mock config-space backend ---------- */

#define MOCK_FNS 15u
#define MOCK_SCRATCH 12u
#define CAP_IMG 192u /* bytes covering config 0x40..0xFF */

struct mock_bar_desc {
    cpu_u32 raw;
    cpu_u32 mask;
    cpu_u32 raw_hi;
    cpu_u32 mask_hi;
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
    cpu_u8 head;     /* capability list head (0x34) */
    cpu_u8 msi_cap;  /* MSI cap offset, 0 when none (wmask) */
    cpu_u8 msix_cap; /* MSI-X cap offset, 0 when none (wmask) */
    struct mock_bar_desc bar[6];
};

static const struct mock_fn mock_fixtures[MOCK_FNS] = {
    /* 0: no capability list at all. */
    { 0, 1, 0, 0xAAAAu, 0x0001u, 0x0007u, 0x0000u, 0x00u, 0, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 1: MSI alone, edu-shaped (64-bit, nomask, MMC=0). */
    { 0, 2, 0, 0xAAAAu, 0x0002u, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 2: MSI-X alone, xhci-shaped (N=16, BAR0 shared table/PBA). */
    { 0, 3, 0, 0xAAAAu, 0x0003u, 0x0007u, 0x0010u, 0x60u, 0, 0x60u,
      { { 0xF0000000u, 0xFFFF0000u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 3: both caps, e1000e-shaped (MSI 32-bit + MSI-X N=5 on BAR3). */
    { 0, 4, 0, 0xAAAAu, 0x0004u, 0x0007u, 0x0010u, 0x50u, 0x50u, 0x70u,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0xF0010000u, 0xFFFFC000u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 4: unknown IDs skipped (PM -> MSI -> VNDR). */
    { 0, 5, 0, 0xAAAAu, 0x0005u, 0x0007u, 0x0010u, 0x40u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 5: five-entry chain (PM, MSI, VNDR, MSI-X, PM). */
    { 0, 6, 0, 0xAAAAu, 0x0006u, 0x0007u, 0x0010u, 0x40u, 0x50u, 0x70u,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 6: truncated (next=0xFF past the MSI cap). */
    { 0, 7, 0, 0xAAAAu, 0x0007u, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 7: misaligned head (0x51). */
    { 0, 8, 0, 0xAAAAu, 0x0008u, 0x0007u, 0x0010u, 0x51u, 0, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 8: two-entry cycle (0x50 <-> 0x60). */
    { 0, 9, 0, 0xAAAAu, 0x0009u, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 9: self-next (0x50 -> 0x50). */
    { 0, 10, 0, 0xAAAAu, 0x000Au, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 10: low pointer (0x50 -> 0x30). */
    { 0, 11, 0, 0xAAAAu, 0x000Bu, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 11: multi-vector masked MSI (64-bit, MMC=3, 8 vectors). */
    { 0, 12, 0, 0xAAAAu, 0x000Cu, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 12: scratch (phase B scribbles cap bytes per case). */
    { 0, 13, 0, 0xAAAAu, 0x000Du, 0x0007u, 0x0010u, 0x50u, 0, 0,
      { { 0xF0020000u, 0xFFFF0000u, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 13: firmware left MSI enabled (adopt-refuse + cleanup). */
    { 0, 14, 0, 0xAAAAu, 0x000Eu, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
    /* 14: insane MME (MME=3 > MMC=1): parse refuses MSI_HW. */
    { 0, 15, 0, 0xAAAAu, 0x000Fu, 0x0007u, 0x0010u, 0x50u, 0x50u, 0,
      { { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 }, { 0, 0, 0, 0, 0 } } },
};

/* Mutable runtime: cap images, COMMAND words, BAR sizing latch. */
static cpu_u8 mock_img[MOCK_FNS][CAP_IMG];
static cpu_u16 mock_cmd[MOCK_FNS];
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

static void img_poke8(cpu_u32 fi, cpu_u32 off, cpu_u8 v)
{
    mock_img[fi][off - 0x40u] = v;
}

static void img_poke16(cpu_u32 fi, cpu_u32 off, cpu_u16 v)
{
    mock_img[fi][off - 0x40u] = (cpu_u8)(v & 0xFFu);
    mock_img[fi][off - 0x40u + 1u] = (cpu_u8)(v >> 8);
}

static void img_poke32(cpu_u32 fi, cpu_u32 off, cpu_u32 v)
{
    mock_img[fi][off - 0x40u] = (cpu_u8)(v & 0xFFu);
    mock_img[fi][off - 0x40u + 1u] = (cpu_u8)((v >> 8) & 0xFFu);
    mock_img[fi][off - 0x40u + 2u] = (cpu_u8)((v >> 16) & 0xFFu);
    mock_img[fi][off - 0x40u + 3u] = (cpu_u8)(v >> 24);
}

/* Build the frozen cap images (scratch stays zeroed for scribbling). */
static void mock_build_images(void)
{
    cpu_u32 i;
    cpu_u32 j;
    for (i = 0; i < MOCK_FNS; ++i)
        for (j = 0; j < CAP_IMG; ++j) mock_img[i][j] = 0;
    /* 1: MSI @0x50, 64-bit nomask MMC=0. */
    img_poke8(1, 0x50, MSI_CAP_ID_MSI);
    img_poke8(1, 0x51, 0);
    img_poke16(1, 0x52, MSI_CTRL_64BIT);
    /* 2: MSI-X @0x60, N=16, BAR0 tbl@0x3000 pba@0x3800. */
    img_poke8(2, 0x60, MSI_CAP_ID_MSIX);
    img_poke8(2, 0x61, 0);
    img_poke16(2, 0x62, 15);
    img_poke32(2, 0x64, 0x3000u);
    img_poke32(2, 0x68, 0x3800u);
    /* 3: MSI @0x50 (32-bit nomask MMC=0) -> MSI-X @0x70 (N=5 BAR3). */
    img_poke8(3, 0x50, MSI_CAP_ID_MSI);
    img_poke8(3, 0x51, 0x70);
    img_poke16(3, 0x52, 0);
    img_poke8(3, 0x70, MSI_CAP_ID_MSIX);
    img_poke8(3, 0x71, 0);
    img_poke16(3, 0x72, 4);
    img_poke32(3, 0x74, 3u);
    img_poke32(3, 0x78, 0x2000u | 3u);
    /* 4: PM @0x40 -> MSI @0x50 -> VNDR @0x60. */
    img_poke8(4, 0x40, 0x01);
    img_poke8(4, 0x41, 0x50);
    img_poke8(4, 0x50, MSI_CAP_ID_MSI);
    img_poke8(4, 0x51, 0x60);
    img_poke16(4, 0x52, 0);
    img_poke8(4, 0x60, 0x09);
    img_poke8(4, 0x61, 0);
    /* 5: PM -> MSI -> VNDR -> MSI-X -> PM. */
    img_poke8(5, 0x40, 0x01);
    img_poke8(5, 0x41, 0x50);
    img_poke8(5, 0x50, MSI_CAP_ID_MSI);
    img_poke8(5, 0x51, 0x60);
    img_poke16(5, 0x52, MSI_CTRL_64BIT);
    img_poke8(5, 0x60, 0x09);
    img_poke8(5, 0x61, 0x70);
    img_poke8(5, 0x70, MSI_CAP_ID_MSIX);
    img_poke8(5, 0x71, 0x80);
    img_poke16(5, 0x72, 0);
    img_poke32(5, 0x74, 0);
    img_poke32(5, 0x78, 0x1000u);
    img_poke8(5, 0x80, 0x01);
    img_poke8(5, 0x81, 0);
    /* 6: MSI @0x50 with next=0xFF. */
    img_poke8(6, 0x50, MSI_CAP_ID_MSI);
    img_poke8(6, 0x51, 0xFF);
    img_poke16(6, 0x52, 0);
    /* 8: 0x50 <-> 0x60 cycle (PM at 0x60). */
    img_poke8(8, 0x50, MSI_CAP_ID_MSI);
    img_poke8(8, 0x51, 0x60);
    img_poke16(8, 0x52, 0);
    img_poke8(8, 0x60, 0x01);
    img_poke8(8, 0x61, 0x50);
    /* 9: 0x50 self-next. */
    img_poke8(9, 0x50, MSI_CAP_ID_MSI);
    img_poke8(9, 0x51, 0x50);
    img_poke16(9, 0x52, 0);
    /* 10: 0x50 -> 0x30 (below the cap floor). */
    img_poke8(10, 0x50, MSI_CAP_ID_MSI);
    img_poke8(10, 0x51, 0x30);
    img_poke16(10, 0x52, 0);
    /* 11: masked 64-bit MSI, MMC=3. */
    img_poke8(11, 0x50, MSI_CAP_ID_MSI);
    img_poke8(11, 0x51, 0);
    img_poke16(11, 0x52, MSI_CTRL_64BIT | MSI_CTRL_MASKBIT | (3u << 1));
    /* 13: ENABLE preset (firmware-live). */
    img_poke8(13, 0x50, MSI_CAP_ID_MSI);
    img_poke8(13, 0x51, 0);
    img_poke16(13, 0x52, MSI_CTRL_64BIT | MSI_CTRL_ENABLE);
    img_poke32(13, 0x54, 0xFEE00000u);
    /* 14: MME=3 > MMC=1. */
    img_poke8(14, 0x50, MSI_CAP_ID_MSI);
    img_poke8(14, 0x51, 0);
    img_poke16(14, 0x52, (1u << 1) | (3u << 4));
}

static void mock_reset(void)
{
    cpu_u32 i;
    mock_build_images();
    mock_last_valid = 0;
    mock_armed = 0;
    mock_hi_valid = 0;
    for (i = 0; i < MOCK_FNS; ++i) mock_cmd[i] = mock_fixtures[i].command;
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

static cpu_u32 mock_img32(cpu_u32 fi, cpu_u32 off)
{
    return (cpu_u32)mock_img[fi][off - 0x40u] |
        ((cpu_u32)mock_img[fi][off - 0x40u + 1u] << 8) |
        ((cpu_u32)mock_img[fi][off - 0x40u + 2u] << 16) |
        ((cpu_u32)mock_img[fi][off - 0x40u + 3u] << 24);
}

static cpu_u32 mock_read(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off)
{
    const struct mock_fn *f;
    int i = mock_find(bus, dev, fn);
    cpu_u32 slot;
    if (i < 0)
        return 0xFFFFFFFFu;
    f = &mock_fixtures[(cpu_u32)i];
    switch (off) {
    case 0x00u:
        return (cpu_u32)f->vendor | ((cpu_u32)f->device << 16);
    case 0x04u:
        return (cpu_u32)mock_cmd[i] | ((cpu_u32)f->status << 16);
    case 0x08u:
        return 0x02000001u; /* rev 1, network class */
    case 0x0Cu:
        return 0;
    case 0x34u:
        return f->head;
    case 0x3Cu:
        return 0x0109u; /* irq line 9, pin A */
    default:
        break;
    }
    if (off >= 0x40u && off <= 0xFCu) return mock_img32((cpu_u32)i, off);
    if (off >= PCI_CFG_BAR0 && off < PCI_CFG_BAR0 + 24u) {
        const struct mock_bar_desc *b;
        slot = (off - PCI_CFG_BAR0) / 4u;
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

/* QEMU-faithful write masks: MSI ctrl QSIZE+ENABLE, MSI mask by MMC,
   MSI-X ctrl ENABLE+MASKALL, MSI-X table/PBA read-only. */
static void mock_write(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn, cpu_u32 off,
                       cpu_u32 val)
{
    int i = mock_find(bus, dev, fn);
    cpu_u32 fi;
    cpu_u32 slot;
    const struct mock_fn *f;
    if (i < 0)
        return;
    fi = (cpu_u32)i;
    f = &mock_fixtures[fi];
    if (off == 0x04u) {
        mock_cmd[fi] = (cpu_u16)(val & 0xFFFFu);
        return;
    }
    if (off >= PCI_CFG_BAR0 && off < PCI_CFG_BAR0 + 24u) {
        slot = (off - PCI_CFG_BAR0) / 4u;
        if (slot > 0 && f->bar[slot - 1u].is64) {
            if (val == 0xFFFFFFFFu) {
                mock_armed = 1;
                mock_armed_fi = (cpu_u8)fi;
                mock_armed_slot = (cpu_u8)(slot - 1u);
            } else {
                mock_hi_valid = 1;
                mock_hi_fi = (cpu_u8)fi;
                mock_hi_slot = (cpu_u8)(slot - 1u);
                mock_hi_val = val;
                mock_armed = 0;
            }
            return;
        }
        if (val == 0xFFFFFFFFu) {
            mock_armed = 1;
            mock_armed_fi = (cpu_u8)fi;
            mock_armed_slot = (cpu_u8)slot;
        } else {
            mock_last_valid = 1;
            mock_last_fi = (cpu_u8)fi;
            mock_last_slot = (cpu_u8)slot;
            mock_last_val = val;
            mock_armed = 0;
        }
        return;
    }
    if (off < 0x40u || off > 0xFCu)
        return;
    /* MSI ctrl dword: ID+next read-only, QSIZE+ENABLE writable. */
    if (f->msi_cap && off == (cpu_u32)f->msi_cap) {
        cpu_u32 old = mock_img32(fi, off);
        cpu_u32 wmask = ((cpu_u32)(MSI_CTRL_QSIZE | MSI_CTRL_ENABLE)) << 16;
        img_poke32(fi, off, (old & ~wmask) | (val & wmask));
        return;
    }
    /* MSI mask dword: only implemented-vector bits writable. */
    if (f->msi_cap) {
        cpu_u32 ctrl = mock_img32(fi, (cpu_u32)f->msi_cap);
        cpu_u32 mmc = (ctrl >> 17) & 7u;
        cpu_u32 is64 = (ctrl >> 23) & 1u;
        cpu_u32 maskbit = (ctrl >> 24) & 1u;
        cpu_u32 mask_off = (cpu_u32)f->msi_cap +
            (is64 ? MSI_MASK_64 : MSI_MASK_32);
        if (maskbit && off == mask_off) {
            cpu_u32 old = mock_img32(fi, off);
            cpu_u32 n = mmc > 5u ? 32u : (1u << mmc);
            cpu_u32 mm =
                n >= 32u ? 0xFFFFFFFFu : (0xFFFFFFFFu >> (32u - n));
            img_poke32(fi, off, (old & ~mm) | (val & mm));
            return;
        }
    }
    /* MSI-X ctrl dword: ID+next+QSIZE read-only, ENABLE+MASKALL open. */
    if (f->msix_cap && off == (cpu_u32)f->msix_cap) {
        cpu_u32 old = mock_img32(fi, off);
        cpu_u32 wmask =
            ((cpu_u32)(MSIX_CTRL_ENABLE | MSIX_CTRL_MASKALL)) << 16;
        img_poke32(fi, off, (old & ~wmask) | (val & wmask));
        return;
    }
    /* MSI-X table/PBA dwords: read-only. */
    if (f->msix_cap &&
        (off == (cpu_u32)f->msix_cap + MSIX_TABLE_REG ||
         off == (cpu_u32)f->msix_cap + MSIX_PBA_REG))
        return;
    img_poke32(fi, off, val);
}

static const struct pci_cfg_ops mock_ops = { mock_read, mock_write };

/* ---------- phase A: walker matrix ---------- */

static void phase_walk(void)
{
    struct msi_walk w;
    mock_reset();
    pci_cfg_install_ops(&mock_ops);
    require(pci_initialize() == PCI_OK, "w-init");
    require(pci_device_count() == MOCK_FNS, "w-count");
    /* Absent function: clean, no caps. */
    require(msi_walk_caps(0, 31, 7, &w) == MSI_OK && !w.has_msi &&
            !w.has_msix && !w.malformed,
            "w-absent");
    /* Bad arguments. */
    require(msi_walk_caps(0, 1, 0, 0) == MSI_INVALID, "w-null");
    require(msi_walk_caps(256, 0, 0, &w) == MSI_INVALID, "w-badbus");
    require(msi_walk_caps(0, 32, 0, &w) == MSI_INVALID, "w-baddev");
    require(msi_walk_caps(0, 0, 8, &w) == MSI_INVALID, "w-badfn");
    /* 0: no list bit. */
    require(msi_walk_caps(0, 1, 0, &w) == MSI_OK && !w.has_msi &&
            !w.has_msix && w.malformed == 1,
            "w-nolist");
    /* 1: MSI alone. */
    require(msi_walk_caps(0, 2, 0, &w) == MSI_OK && w.has_msi &&
            !w.has_msix && w.msi_off == 0x50 && !w.malformed,
            "w-msi");
    /* 2: MSI-X alone. */
    require(msi_walk_caps(0, 3, 0, &w) == MSI_OK && !w.has_msi &&
            w.has_msix && w.msix_off == 0x60 && !w.malformed,
            "w-msix");
    /* 3: both. */
    require(msi_walk_caps(0, 4, 0, &w) == MSI_OK && w.has_msi &&
            w.has_msix && w.msi_off == 0x50 && w.msix_off == 0x70 &&
            !w.malformed,
            "w-both");
    /* 4: unknown IDs skipped. */
    require(msi_walk_caps(0, 5, 0, &w) == MSI_OK && w.has_msi &&
            !w.has_msix && w.msi_off == 0x50 && !w.malformed,
            "w-skip");
    /* 5: five-entry chain finds both. */
    require(msi_walk_caps(0, 6, 0, &w) == MSI_OK && w.has_msi &&
            w.has_msix && w.msi_off == 0x50 && w.msix_off == 0x70 &&
            !w.malformed,
            "w-chain5");
    /* 6: truncated: MSI found before the defect. */
    require(msi_walk_caps(0, 7, 0, &w) == MSI_OK && w.has_msi &&
            w.malformed == 3,
            "w-trunc");
    /* 7: misaligned head. */
    require(msi_walk_caps(0, 8, 0, &w) == MSI_OK && !w.has_msi &&
            w.malformed == 4,
            "w-misalign");
    /* 8: cycle. */
    require(msi_walk_caps(0, 9, 0, &w) == MSI_OK && w.has_msi &&
            w.malformed == 5,
            "w-cycle");
    /* 9: self-next. */
    require(msi_walk_caps(0, 10, 0, &w) == MSI_OK && w.has_msi &&
            w.malformed == 5,
            "w-self");
    /* 10: low pointer. */
    require(msi_walk_caps(0, 11, 0, &w) == MSI_OK && w.has_msi &&
            w.malformed == 2,
            "w-low");
    say("[MSI] walk ok cases=18\r\n");
}

/* ---------- phase B: parse + message + geometry matrices ---------- */

static void scratch_zero(void)
{
    cpu_u32 j;
    for (j = 0; j < CAP_IMG; ++j) mock_img[MOCK_SCRATCH][j] = 0;
    mock_cmd[MOCK_SCRATCH] = mock_fixtures[MOCK_SCRATCH].command;
}

static void phase_msi_parse(void)
{
    struct msi_desc d;
    cpu_u32 mmc;
    /* Bad arguments. */
    require(msi_parse_msi(0, 2, 0, 0x50, 0) == MSI_INVALID, "p-null");
    require(msi_parse_msi(0, 32, 0, 0x50, &d) == MSI_INVALID, "p-bdf");
    require(msi_parse_msi(0, 2, 0, 0x30, &d) == MSI_INVALID, "p-low");
    require(msi_parse_msi(0, 2, 0, 0x51, &d) == MSI_INVALID, "p-mis");
    /* Wrong ID at the offset (fixture 2 has MSI-X at 0x60). */
    require(msi_parse_msi(0, 3, 0, 0x60, &d) == MSI_ABSENT, "p-id");
    /* Fixture 1: 64-bit nomask MMC=0, len 14. */
    require(msi_parse_msi(0, 2, 0, 0x50, &d) == MSI_OK && d.mmc == 0 &&
            d.mme == 0 && d.is64 && !d.maskbit && !d.enabled && d.len == 14,
            "p-f1");
    /* Fixture 11: 64-bit mask MMC=3, len 24. */
    require(msi_parse_msi(0, 12, 0, 0x50, &d) == MSI_OK && d.mmc == 3 &&
            d.is64 && d.maskbit && d.len == 24,
            "p-f11");
    /* Fixture 13: enabled preset parses (enable refuses later). */
    require(msi_parse_msi(0, 14, 0, 0x50, &d) == MSI_OK && d.enabled,
            "p-f13on");
    /* Fixture 14: MME > MMC refuses. */
    require(msi_parse_msi(0, 15, 0, 0x50, &d) == MSI_HW, "p-f14mme");
    /* 32/64 x mask/nomask x MMC 0..5 lengths on the scratch fixture. */
    for (mmc = 0; mmc <= 5u; ++mmc) {
        cpu_u32 variant;
        for (variant = 0; variant < 4u; ++variant) {
            cpu_u16 ctrl = (cpu_u16)(mmc << 1);
            cpu_u8 want_len;
            if (variant & 1u) ctrl |= MSI_CTRL_64BIT;
            if (variant & 2u) ctrl |= MSI_CTRL_MASKBIT;
            want_len = (cpu_u8)(10u + ((variant & 1u) ? 4u : 0u) +
                                ((variant & 2u) ? 10u : 0u));
            scratch_zero();
            img_poke8(MOCK_SCRATCH, 0x50, MSI_CAP_ID_MSI);
            img_poke8(MOCK_SCRATCH, 0x51, 0);
            img_poke16(MOCK_SCRATCH, 0x52, ctrl);
            require(msi_parse_msi(0, 13, 0, 0x50, &d) == MSI_OK &&
                    d.mmc == (cpu_u8)mmc &&
                    d.is64 == (cpu_u8)(variant & 1u) &&
                    d.maskbit == (cpu_u8)((variant >> 1) & 1u) &&
                    d.len == want_len,
                    "p-matrix");
        }
    }
    /* Cap overruns config space (cap at 0xFC with len 14). */
    scratch_zero();
    img_poke8(MOCK_SCRATCH, 0xFC, MSI_CAP_ID_MSI);
    img_poke8(MOCK_SCRATCH, 0xFD, 0);
    img_poke16(MOCK_SCRATCH, 0xFE, MSI_CTRL_64BIT);
    require(msi_parse_msi(0, 13, 0, 0xFC, &d) == MSI_RANGE, "p-overrun");
    /* MMC=7 (reserved) refuses. */
    scratch_zero();
    img_poke8(MOCK_SCRATCH, 0x50, MSI_CAP_ID_MSI);
    img_poke8(MOCK_SCRATCH, 0x51, 0);
    img_poke16(MOCK_SCRATCH, 0x52, (cpu_u16)(7u << 1));
    require(msi_parse_msi(0, 13, 0, 0x50, &d) == MSI_HW, "p-mmc7");
    say("[MSI] parse ok cases=35\r\n");
}

static void phase_msg(void)
{
    struct msi_msg m;
    static const unsigned int vecs[4] = { 32, 48, 127, 255 };
    static const cpu_u32 apics[3] = { 0, 1, 255 };
    cpu_u32 a;
    cpu_u32 v;
    require(msi_build_message(0, 48, 0) == MSI_INVALID, "m-null");
    require(msi_build_message(0, 256, &m) == MSI_INVALID, "m-vec");
    require(msi_build_message(256, 48, &m) == MSI_REFUSED, "m-apic256");
    require(msi_build_message(0xFFFFFFFFu, 48, &m) == MSI_REFUSED,
            "m-apicmax");
    for (a = 0; a < 3u; ++a)
        for (v = 0; v < 4u; ++v) {
            cpu_u32 want_addr = 0xFEE00000u | (apics[a] << 12);
            require(msi_build_message(apics[a], vecs[v], &m) == MSI_OK &&
                    m.addr == want_addr && m.hi == 0 && m.data == vecs[v],
                    "m-golden");
        }
    say("[MSI] msg ok cases=16\r\n");
}

static void msix_scribble(cpu_u16 qsize, cpu_u32 tbl, cpu_u32 pba,
                          cpu_u16 cmd)
{
    scratch_zero();
    img_poke8(MOCK_SCRATCH, 0x50, MSI_CAP_ID_MSIX);
    img_poke8(MOCK_SCRATCH, 0x51, 0);
    img_poke16(MOCK_SCRATCH, 0x52, qsize);
    img_poke32(MOCK_SCRATCH, 0x54, tbl);
    img_poke32(MOCK_SCRATCH, 0x58, pba);
    mock_cmd[MOCK_SCRATCH] = cmd;
}

static void phase_msix_geo(void)
{
    struct msix_geo g;
    /* Bad arguments. */
    require(msi_parse_msix(0, 3, 0, 0x60, 0) == MSI_INVALID, "x-null");
    require(msi_parse_msix(0, 32, 0, 0x60, &g) == MSI_INVALID, "x-bdf");
    require(msi_parse_msix(0, 3, 0, 0x30, &g) == MSI_INVALID, "x-low");
    require(msi_parse_msix(0, 3, 0, 0xF8, &g) == MSI_RANGE, "x-overrun");
    require(msi_parse_msix(0, 2, 0, 0x50, &g) == MSI_ABSENT, "x-id");
    /* Fixture 2: N=16 shared BAR0. */
    require(msi_parse_msix(0, 3, 0, 0x60, &g) == MSI_OK && g.nvec == 16 &&
            g.tbl_bir == 0 && g.pba_bir == 0 && g.tbl_off == 0x3000u &&
            g.pba_off == 0x3800u && !g.enabled && g.tbl_entry == 0 &&
            g.pba_entry == 0,
            "x-f2");
    /* Fixture 3: N=5 on BAR3. */
    require(msi_parse_msix(0, 4, 0, 0x70, &g) == MSI_OK && g.nvec == 5 &&
            g.tbl_bir == 3 && g.pba_bir == 3 && g.tbl_off == 0 &&
            g.pba_off == 0x2000u && g.tbl_entry == 0,
            "x-f3");
    /* MEM clear refuses (driver must enable decoding). */
    msix_scribble(15, 0x3000u, 0x3800u, 0x0005u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_REFUSED, "x-nomem");
    /* BIR 6 refuses (only BARs 0..5 exist). */
    msix_scribble(0, 0x0006u, 0x1000u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_RANGE, "x-bir6");
    /* BIR with no decoded BAR refuses (scratch has BAR0 only). */
    msix_scribble(0, 0x0001u, 0x1000u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_RANGE, "x-nobar");
    /* Table past BAR end refuses (BAR0 is 64 KiB). */
    msix_scribble(15, 0x10000u, 0x3800u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_RANGE, "x-tblend");
    /* PBA past BAR end refuses. */
    msix_scribble(15, 0x3000u, 0x10000u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_RANGE, "x-pbaend");
    /* Table/PBA overlap refuses. */
    msix_scribble(15, 0x3000u, 0x3050u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_REFUSED, "x-overlap");
    /* 64-bit wrap refuses (offset near 4G with a big table). */
    msix_scribble(2047, 0xFFFFFFF8u, 0x1000u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_RANGE, "x-wrap");
    /* N=2048 geometry validates against a big BAR (bounds pass: the
       scratch BAR is 64 KiB, so shrink the table to fit: N=2048 needs
       32 KiB at offset 0, PBA 256 B at 0x8000 — still inside). */
    msix_scribble(2047, 0x0u, 0x8000u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_OK && g.nvec == 2048,
            "x-max");
    /* Same-BAR adjacent (no overlap) validates. */
    msix_scribble(15, 0x3000u, 0x3100u, 0x0007u);
    require(msi_parse_msix(0, 13, 0, 0x50, &g) == MSI_OK, "x-adjacent");
    say("[MSI] msix ok cases=16\r\n");
}

/* ---------- phase C: aligned-allocator matrix ---------- */

static void phase_alloc(void)
{
    cpu_u8 saved_state[256];
    unsigned int saved_owner[256];
    unsigned int v;
    int b;
    for (v = 0; v < 256; ++v) {
        saved_state[v] = (cpu_u8)apic_vector_state(v);
        saved_owner[v] = apic_vector_owner(v);
    }
    apic_vector_init();
    /* Bad sizes refuse without touching the pool. */
    require(apic_vector_alloc_aligned(0) == -1, "a-zero");
    require(apic_vector_alloc_aligned(3) == -1, "a-nonpow2");
    require(apic_vector_alloc_aligned(64) == -1, "a-huge");
    require(apic_vector_free() == 80, "a-pristine");
    /* First-fit alignment on a pristine pool. */
    b = apic_vector_alloc_aligned(1);
    require(b == 48, "a-1");
    require(apic_vector_alloc_aligned(2) == 50, "a-2");
    require(apic_vector_alloc_aligned(4) == 52, "a-4");
    require(apic_vector_alloc_aligned(8) == 56, "a-8");
    require(apic_vector_alloc_aligned(16) == 64, "a-16");
    require(apic_vector_alloc_aligned(32) == 96, "a-32");
    /* Fragment: free 50-51, alloc 4 must skip to 80 (48/52/56/64
       used, 60-63 used, 80 aligned+free). */
    require(apic_vector_release(50) == APIC_OK, "a-rel50");
    require(apic_vector_release(51) == APIC_OK, "a-rel51");
    require(apic_vector_alloc_aligned(4) == 80, "a-frag4");
    /* Exhaustion: fill everything, aligned alloc fails, pool intact. */
    {
        unsigned int before = apic_vector_free();
        unsigned int got = 0;
        int w;
        while ((w = apic_vector_alloc()) >= 0) ++got;
        require(got == before && apic_vector_free() == 0, "a-fill");
        require(apic_vector_alloc_aligned(2) == -1, "a-exhaust");
        /* Free one aligned pair slot and reclaim exactly it. */
        require(apic_vector_release(100) == APIC_OK, "a-rel100");
        require(apic_vector_release(101) == APIC_OK, "a-rel101");
        require(apic_vector_alloc_aligned(2) == 100, "a-reclaim");
    }
    apic_vector_init();
    require(apic_vector_free() == 80, "a-reset");
    apic_vector_restore(saved_state, saved_owner);
    {
        int same = 1;
        for (v = 0; v < 256; ++v)
            same &= apic_vector_state(v) == saved_state[v] &&
                    apic_vector_owner(v) == saved_owner[v];
        require(same, "a-restored");
    }
    say("[MSI] alloc ok cases=20\r\n");
}

/* ---------- phase D: synthetic MSI lifecycle (mock, config-only) ---------- */

static volatile cpu_u64 synth_count;

static void synth_handler(cpu_u32 vector, void *opaque)
{
    (void)vector;
    (void)opaque;
    ++synth_count;
}

static void phase_synth_msi(void)
{
    cpu_u32 handle = 0xFFFFFFFFu;
    cpu_u32 info_handle = 0xFFFFFFFFu;
    unsigned int vectors[MSI_MAX_VECTORS];
    struct msi_handle_info info;
    cpu_u32 i;
    require(apic_active(), "d-apic");
    require(apic_vector_free() == 80, "d-poolfree");
    /* Bad arguments. */
    require(pci_irq_enable_msi(0, 2, 0, 1, 0, 0, &handle, vectors) ==
            MSI_INVALID,
            "d-nohandler");
    require(pci_irq_enable_msi(0, 2, 0, 1, synth_handler, 0, 0, vectors) ==
            MSI_INVALID,
            "d-nohandle");
    require(pci_irq_enable_msi(0, 2, 0, 1, synth_handler, 0, &handle, 0) ==
            MSI_INVALID,
            "d-novectors");
    require(pci_irq_enable_msi(0, 2, 0, 0, synth_handler, 0, &handle,
                               vectors) == MSI_INVALID,
            "d-nvec0");
    require(pci_irq_enable_msi(0, 2, 0, 3, synth_handler, 0, &handle,
                               vectors) == MSI_INVALID,
            "d-nvec3");
    require(pci_irq_enable_msi(0, 2, 0, 33, synth_handler, 0, &handle,
                               vectors) == MSI_INVALID,
            "d-nvec33");
    require(pci_irq_enable_msi(0, 32, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_INVALID,
            "d-bdf");
    /* Over MMC (fixture 1 has MMC=0). */
    require(pci_irq_enable_msi(0, 2, 0, 2, synth_handler, 0, &handle,
                               vectors) == MSI_REFUSED,
            "d-overmmc");
    /* Bus master clear refuses (driver must set it). */
    mock_cmd[1] = 0x0003u;
    require(pci_irq_enable_msi(0, 2, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_REFUSED,
            "d-nobm");
    mock_cmd[1] = 0x0007u;
    /* Insane MME fixture propagates parse failure. */
    require(pci_irq_enable_msi(0, 15, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_HW,
            "d-mmebad");
    /* Firmware-live fixture: refuse + cleanup, then retry works. */
    require(pci_irq_enable_msi(0, 14, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_STATE,
            "d-already");
    require(!(pci_cfg_read16(0, 14, 0, 0x50 + MSI_MSG_CTRL) &
              MSI_CTRL_ENABLE),
            "d-alreadyoff");
    require(pci_irq_enable_msi(0, 14, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_OK && vectors[0] == 48,
            "d-retry");
    require(pci_irq_disable(handle) == MSI_OK, "d-retrydis");
    /* Single-vector enable on fixture 1. */
    synth_count = 0;
    require(pci_irq_enable_msi(0, 2, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_OK && vectors[0] == 48,
            "d-en1");
    require(pci_cfg_read32(0, 2, 0, 0x50 + MSI_ADDR_LO) == 0xFEE00000u,
            "d-addr");
    require(pci_cfg_read32(0, 2, 0, 0x50 + MSI_ADDR_HI) == 0, "d-hi");
    require(pci_cfg_read16(0, 2, 0, 0x50 + MSI_DATA_64) == 48, "d-data");
    require((pci_cfg_read16(0, 2, 0, 0x50 + MSI_MSG_CTRL) &
             (MSI_CTRL_ENABLE | MSI_CTRL_QSIZE)) == MSI_CTRL_ENABLE,
            "d-mme0");
    require(pci_cfg_read16(0, 2, 0, PCI_CFG_COMMAND) ==
            (0x0007u | PCI_COMMAND_INTX_DISABLE),
            "d-intx");
    require(apic_route_for_vector(48) != 0, "d-route");
    /* Nomask device refuses per-vector ops; info works. */
    require(pci_irq_mask(handle, 0, 1) == MSI_STATE, "d-nomask");
    require(pci_irq_info(handle, &info) == MSI_OK &&
            info.kind == MSI_KIND_MSI && info.bus == 0 && info.dev == 2 &&
            info.fn == 0 && info.nvec == 1 && info.vector[0] == 48,
            "d-info");
    require(pci_irq_info(MSI_MAX_HANDLES, &info) == MSI_STATE, "d-infobad");
    /* Double enable refuses; the first handle is undisturbed. */
    require(pci_irq_enable_msi(0, 2, 0, 1, synth_handler, 0, &info_handle,
                               vectors) == MSI_STATE,
            "d-double");
    require(pci_irq_info(handle, &info) == MSI_OK && info.nvec == 1,
            "d-doubleinfo");
    /* Disable restores everything. */
    require(pci_irq_disable(handle) == MSI_OK, "d-dis");
    require(!(pci_cfg_read16(0, 2, 0, 0x50 + MSI_MSG_CTRL) &
              MSI_CTRL_ENABLE),
            "d-disoff");
    require(pci_cfg_read16(0, 2, 0, PCI_CFG_COMMAND) == 0x0007u,
            "d-disintx");
    require(apic_route_for_vector(48) == 0, "d-disroute");
    require(apic_vector_free() == 80, "d-disfree");
    require(pci_irq_disable(handle) == MSI_STATE, "d-disdouble");
    require(pci_irq_disable(MSI_MAX_HANDLES) == MSI_STATE, "d-disbad");
    /* Reuse: same vector comes back. */
    require(pci_irq_enable_msi(0, 2, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_OK && vectors[0] == 48,
            "d-reuse");
    require(pci_irq_disable(handle) == MSI_OK, "d-reusedis");
    /* Multi-vector masked MSI (fixture 11, MMC=3, nvec=8). */
    require(pci_irq_enable_msi(0, 12, 0, 8, synth_handler, 0, &handle,
                               vectors) == MSI_OK,
            "d-en8");
    require(vectors[0] % 8 == 0, "d-align");
    for (i = 1; i < 8u; ++i)
        require(vectors[i] == vectors[0] + i, "d-contig");
    require((pci_cfg_read16(0, 12, 0, 0x50 + MSI_DATA_64) & 7u) == 0,
            "d-datazero");
    require(((pci_cfg_read16(0, 12, 0, 0x50 + MSI_MSG_CTRL) &
              MSI_CTRL_QSIZE) >> 4) == 3,
            "d-mme3");
    require(pci_cfg_read32(0, 12, 0, 0x50 + MSI_MASK_64) == 0,
            "d-unmasked");
    for (i = 0; i < 8u; ++i) {
        require(pci_irq_mask(handle, i, 1) == MSI_OK, "d-mask");
        require(pci_cfg_read32(0, 12, 0, 0x50 + MSI_MASK_64) ==
                (1u << i),
                "d-maskbit");
        require(pci_irq_mask(handle, i, 0) == MSI_OK, "d-unmask");
    }
    require(pci_irq_mask(handle, 8, 1) == MSI_INVALID, "d-maskidx");
    /* Quiet-by-kind masks without failing (direct call). */
    msi_quiet_route(MSI_KIND_MSI, (12u << 8), 3);
    require(pci_cfg_read32(0, 12, 0, 0x50 + MSI_MASK_64) == (1u << 3),
            "d-quiet");
    msi_quiet_route(MSI_KIND_MSI, (12u << 8), 30);
    msi_quiet_route(MSI_KIND_MSI, (9u << 8), 0);
    msi_quiet_route(MSI_KIND_MSIX, (12u << 8), 0);
    require(pci_cfg_read32(0, 12, 0, 0x50 + MSI_MASK_64) == (1u << 3),
            "d-quietnoop");
    require(pci_irq_mask(handle, 3, 0) == MSI_OK, "d-quietclear");
    require(pci_irq_disable(handle) == MSI_OK, "d-dis8");
    require(apic_vector_free() == 80, "d-dis8free");
    /* Both-caps fixture: MSI enable blocks MSI-X (live MSI-X is
       proven on silicon; the mock has no table MMIO). */
    require(pci_irq_enable_msi(0, 4, 0, 1, synth_handler, 0, &handle,
                               vectors) == MSI_OK,
            "d-bothmsi");
    require(pci_irq_enable_msix(0, 4, 0, 1, synth_handler, 0, &info_handle,
                                vectors) == MSI_STATE,
            "d-bothblock");
    require(pci_irq_disable(handle) == MSI_OK, "d-bothdis");
    /* Auto on an MSI-only function picks MSI. */
    require(pci_irq_enable_auto(0, 2, 0, 1, synth_handler, 0, &handle,
                                vectors) == MSI_OK,
            "d-auto");
    require(pci_irq_info(handle, &info) == MSI_OK &&
            info.kind == MSI_KIND_MSI,
            "d-autokind");
    require(pci_irq_disable(handle) == MSI_OK, "d-autodis");
    /* Auto with no caps refuses. */
    require(pci_irq_enable_auto(0, 1, 0, 1, synth_handler, 0, &handle,
                                vectors) == MSI_ABSENT,
            "d-autonone");
    /* The mock never delivers. */
    require(synth_count == 0, "d-silent");
    require(apic_vector_free() == 80, "d-finalfree");
    say("[MSI] synth ok\r\n");
}

/* ---------- phase E: live hardware ---------- */

static void say_bdf(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    say("bdf=");
    say_dec(bus);
    say(":");
    say_dec(dev);
    say(".");
    say_dec(fn);
}

static void emit_walk(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                      const struct msi_walk *w)
{
    say("[MSI] walk ");
    say_bdf(bus, dev, fn);
    say(" msi=");
    say(w->has_msi ? "1" : "0");
    if (w->has_msi) {
        say("@");
        say_hex64(w->msi_off);
    }
    say(" msix=");
    say(w->has_msix ? "1" : "0");
    if (w->has_msix) {
        say("@");
        say_hex64(w->msix_off);
    }
    say(" malformed=");
    say_dec(w->malformed);
    say("\r\n");
}

static void emit_msi(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                     const struct msi_desc *d)
{
    say("[MSI] msi ");
    say_bdf(bus, dev, fn);
    say(" mmc=");
    say_dec(d->mmc);
    say(" mme=");
    say_dec(d->mme);
    say(" is64=");
    say_dec(d->is64);
    say(" mask=");
    say_dec(d->maskbit);
    say(" enabled=");
    say_dec(d->enabled);
    say(" len=");
    say_dec(d->len);
    say("\r\n");
}

static void emit_msix(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                      const struct msix_geo *g)
{
    say("[MSI] msix ");
    say_bdf(bus, dev, fn);
    say(" n=");
    say_dec(g->nvec);
    say(" tbl=");
    say_dec(g->tbl_bir);
    say(":");
    say_hex64(g->tbl_off);
    say(" pba=");
    say_dec(g->pba_bir);
    say(":");
    say_hex64(g->pba_off);
    say("\r\n");
}

static void emit_msg(unsigned int vector, const struct msi_msg *m,
                     cpu_u32 apic)
{
    say("[MSI] msg vec=");
    say_dec(vector);
    say(" addr=");
    say_hex64(m->addr);
    say(" hi=");
    say_hex64(m->hi);
    say(" data=");
    say_hex64(m->data);
    say(" apic=");
    say_dec(apic);
    say("\r\n");
}

static void emit_enable(cpu_u32 bus, cpu_u32 dev, cpu_u32 fn,
                        const char *kind, unsigned int nvec,
                        const unsigned int *vectors)
{
    cpu_u32 i;
    say("[MSI] enable ");
    say_bdf(bus, dev, fn);
    say(" kind=");
    say(kind);
    say(" nvec=");
    say_dec(nvec);
    say(" vec=");
    for (i = 0; i < nvec; ++i) {
        if (i) say(",");
        say_dec(vectors[i]);
    }
    say(" ok\r\n");
}

static void emit_irq(unsigned int vector, cpu_u64 count, int isr)
{
    say("[MSI] irq vec=");
    say_dec(vector);
    say(" count=");
    say_dec(count);
    say(" isr=");
    say_dec(isr ? 1u : 0u);
    say("\r\n");
}

static void emit_disable(cpu_u32 handle, const char *kind)
{
    say("[MSI] disable handle=");
    say_dec(handle);
    say(" kind=");
    say(kind);
    say(" ok\r\n");
}

static void emit_refuse(const char *op, cpu_u32 bus, cpu_u32 dev, cpu_u32 fn)
{
    say("[MSI] refuse op=");
    say(op);
    say(" ");
    say_bdf(bus, dev, fn);
    say(" reason=");
    say(msi_error());
    say("\r\n");
}

/* IF=1 bounded wait: pending IRQs deliver between iterations; the
   bound fails loudly instead of hanging on a dead line. */
static void wait_count(volatile cpu_u64 *slot, cpu_u64 want,
                       const char *tag)
{
    __asm__ volatile ("sti" : : : "memory");
    for (cpu_u64 spin = 0; spin < 20000000ULL && *slot < want; ++spin) {
    }
    __asm__ volatile ("cli" : : : "memory");
    require(*slot == want, tag);
}

/* Legacy silence window: no non-timer legacy deliveries. Timer (vec
   32) always ticks; everything else must hold still. */
static void legacy_snapshot(cpu_u64 *out)
{
    for (unsigned int v = 33; v <= 47; ++v) out[v] = apic_vector_count(v);
}

static void legacy_require_quiet(const cpu_u64 *before, const char *tag)
{
    for (unsigned int v = 33; v <= 47; ++v)
        if (apic_vector_count(v) != before[v]) fail(tag);
    ++cases;
}

/* ----- edu MSI proof ----- */

#define EDU_ID 0x00u
#define EDU_LIVE 0x04u
#define EDU_FACT 0x08u
#define EDU_STATUS 0x20u
#define EDU_IRQSTAT 0x24u
#define EDU_RAISE 0x60u
#define EDU_ACK 0x64u

static volatile cpu_u64 edu_count;
static volatile cpu_u32 edu_vec;
static volatile cpu_u32 edu_status;
static volatile int edu_isr;
static cpu_u64 edu_bar_va;

static void edu_handler(cpu_u32 vector, void *opaque)
{
    volatile cpu_u32 *mmio;
    (void)opaque;
    ++edu_count;
    edu_vec = vector;
    edu_isr = apic_lapic_isr_set(vector);
    mmio = (volatile cpu_u32 *)edu_bar_va;
    edu_status = mmio[EDU_IRQSTAT / 4];
    mmio[EDU_ACK / 4] = edu_status;
}

static void live_edu(void)
{
    const struct pci_device *d =
        pci_find_vendor_device(0x1234u, 0x11E8u, 0);
    cpu_u32 handle;
    unsigned int vectors[1];
    cpu_u64 va = 0;
    cpu_u64 legacy[48];
    cpu_u16 cmd0;
    struct msi_walk w;
    struct msi_desc md;
    struct msi_msg msg;
    if (!d) {
        say("[MSI] edu none\r\n");
        return;
    }
    require(d->bar_count >= 1 && d->bars[0].index == 0 &&
            d->bars[0].kind == PCI_BAR_MMIO32,
            "e-bar");
    cmd0 = pci_cfg_read16(d->bus, d->device, d->function, PCI_CFG_COMMAND);
    if (!(cmd0 & PCI_COMMAND_MEM)) {
        pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND,
                        (cpu_u16)(cmd0 | PCI_COMMAND_MEM));
        cmd0 |= PCI_COMMAND_MEM;
        require(pci_cfg_read16(d->bus, d->device, d->function,
                               PCI_CFG_COMMAND) &
                    PCI_COMMAND_MEM,
                "e-mem");
    }
    require(pci_map_bar(d->bus, d->device, d->function, 0, &va) == PCI_OK &&
            va != 0,
            "e-map");
    edu_bar_va = va;
    say("[MSI] map ");
    say_bdf(d->bus, d->device, d->function);
    say(" entry=0 va=");
    say_hex64(va);
    say(" pages=");
    say_dec(d->bars[0].size >> 12);
    say("\r\n");
    /* Liveness + identity through the mapping. */
    {
        volatile cpu_u32 *mmio = (volatile cpu_u32 *)va;
        require(mmio[EDU_ID / 4] == 0x010000EDu, "e-id");
        mmio[EDU_LIVE / 4] = ~mmio[EDU_LIVE / 4];
    }
    /* edu's MSI offset is QEMU-allocated (msi_init offset 0): the walk
       row carries it for host pinning; only shape is asserted here. */
    require(msi_walk_caps(d->bus, d->device, d->function, &w) == MSI_OK &&
            w.has_msi && !w.has_msix && !w.malformed,
            "e-walk");
    require(msi_parse_msi(d->bus, d->device, d->function, w.msi_off, &md) ==
                MSI_OK &&
            md.mmc == 0 && md.mme == 0 && md.is64 && !md.maskbit &&
            !md.enabled,
            "e-parse");
    emit_msi(d->bus, d->device, d->function, &md);
    /* INTx leg: disabled by enable; the line was never routed. */
    if (d->irq_line < 16)
        require(apic_route_for_irq(d->irq_line) == 0, "e-unrouted");
    legacy_snapshot(legacy);
    /* Bus master is the driver's call: force it clear, watch enable
       refuse, then set it (deterministic whatever firmware did). */
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND,
                    (cpu_u16)(cmd0 & ~PCI_COMMAND_BUS_MASTER));
    require(!(pci_cfg_read16(d->bus, d->device, d->function,
                             PCI_CFG_COMMAND) &
              PCI_COMMAND_BUS_MASTER),
            "e-bmclear");
    require(pci_irq_enable_msi(d->bus, d->device, d->function, 1,
                               edu_handler, 0, &handle,
                               vectors) == MSI_REFUSED,
            "e-nobm");
    emit_refuse("msi", d->bus, d->device, d->function);
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND,
                    (cpu_u16)(cmd0 | PCI_COMMAND_BUS_MASTER));
    require((pci_cfg_read16(d->bus, d->device, d->function,
                            PCI_CFG_COMMAND) &
             PCI_COMMAND_BUS_MASTER) != 0,
            "e-bm");
    edu_count = 0;
    require(pci_irq_enable_msi(d->bus, d->device, d->function, 1,
                               edu_handler, 0, &handle,
                               vectors) == MSI_OK && vectors[0] == 48,
            "e-enable");
    require(msi_build_message(apic_bsp_id(), vectors[0], &msg) == MSI_OK,
            "e-msg");
    emit_msg(vectors[0], &msg, apic_bsp_id());
    emit_enable(d->bus, d->device, d->function, "msi", 1, vectors);
    require(pci_cfg_read16(d->bus, d->device, d->function,
                           PCI_CFG_COMMAND) &
                PCI_COMMAND_INTX_DISABLE,
            "e-intxoff");
    /* Raise 0xAA: exactly one MSI, ISR-proven, acked to zero. */
    {
        volatile cpu_u32 *mmio = (volatile cpu_u32 *)va;
        mmio[EDU_RAISE / 4] = 0xAAu;
    }
    wait_count(&edu_count, 1, "e-count");
    require(edu_vec == vectors[0] && edu_isr, "e-vecisr");
    require(edu_status == 0xAAu, "e-status");
    {
        volatile cpu_u32 *mmio = (volatile cpu_u32 *)va;
        require(mmio[EDU_IRQSTAT / 4] == 0, "e-ack");
    }
    emit_irq(vectors[0], edu_count, edu_isr);
    /* Factorial path: value-agnostic (nonzero in, zero after ack). */
    {
        volatile cpu_u32 *mmio = (volatile cpu_u32 *)va;
        mmio[EDU_STATUS / 4] = 0x80u;
        mmio[EDU_FACT / 4] = 6;
    }
    wait_count(&edu_count, 2, "e-fcount");
    require(edu_status == 0x1u, "e-fstatus");
    {
        volatile cpu_u32 *mmio = (volatile cpu_u32 *)va;
        require(mmio[EDU_IRQSTAT / 4] == 0, "e-fack");
        require(mmio[EDU_FACT / 4] == 720, "e-fact");
    }
    say("[MSI] fact value=");
    say_hex64(edu_status);
    say("\r\n");
    legacy_require_quiet(legacy, "e-silence");
    say("[MSI] intx ");
    say_bdf(d->bus, d->device, d->function);
    say(" intxoff=1 silent=1\r\n");
    require(pci_irq_disable(handle) == MSI_OK, "e-disable");
    emit_disable(handle, "msi");
    /* Reuse: same vector, second raise (bus master still set). */
    edu_count = 0;
    require(pci_irq_enable_msi(d->bus, d->device, d->function, 1,
                               edu_handler, 0, &handle,
                               vectors) == MSI_OK && vectors[0] == 48,
            "e-reuse");
    {
        volatile cpu_u32 *mmio = (volatile cpu_u32 *)va;
        mmio[EDU_RAISE / 4] = 0x55u;
    }
    wait_count(&edu_count, 1, "e-rcount");
    require(edu_status == 0x55u && edu_vec == 48 && edu_isr, "e-rcheck");
    require(pci_irq_disable(handle) == MSI_OK, "e-redis");
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND, cmd0);
    require(pci_cfg_read16(d->bus, d->device, d->function,
                           PCI_CFG_COMMAND) == cmd0,
            "e-cmdrestore");
    require(pci_unmap_bar(d->bus, d->device, d->function, 0) == PCI_OK,
            "e-unmap");
    d = pci_find_vendor_device(0x1234u, 0x11E8u, 0);
    require(d != 0 && d->bars[0].mapped_va == 0, "e-gone");
    edu_bar_va = 0;
}

/* ----- xHCI MSI-X proof (NOOP TRB -> interrupter N -> vector N) -----
 *
 * Test-owned minimal bring-up (verified against QEMU hcd-xhci.c):
 * DCBAA + command ring + one ERST/event-ring per tested interrupter.
 * TRB type 23 (No Op Command), interrupter target in STATUS bits
 * 31:22, cycle 1. The handler clears IP (IMAN write 1) and advances
 * ERDP past consumed events (write dequeue|EHB); without the ERDP
 * advance QEMU suppresses the next notify (EHB sticky). Buffers are
 * dma_alloc 32-bit (bus == phys, DMA-A1).
 */

/* Single-vector MSI-X: QEMU routes every xHCI command completion to
   interrupter 0 (hcd-xhci.c: xhci_event(xhci, &event, 0)), ignoring the
   TRB's Interrupter Target field, so NOOPs cannot address vectors 1+.
   Multi-vector MSI-X is proven on e1000e (IVAR-routed ICS triggers);
   xHCI proves functional-device delivery plus mask/PBA mechanics. */
#define XHCI_NVEC 1u
#define XHCI_TRB_NOOP 23u
#define XHCI_ER_TRBS 256u

static volatile cpu_u64 xhc_count[XHCI_NVEC];
static volatile cpu_u32 xhc_vec[XHCI_NVEC];
static volatile int xhc_isr[XHCI_NVEC];
static unsigned int xhc_vectors[XHCI_NVEC];
static cpu_u64 xhc_seg_bus[XHCI_NVEC];
static cpu_u64 xhc_seen[XHCI_NVEC];
static cpu_u64 xhc_rt;
static struct dma_buffer xhc_dcbaa;
static struct dma_buffer xhc_cmd;
static struct dma_buffer xhc_erst[XHCI_NVEC];
static struct dma_buffer xhc_seg[XHCI_NVEC];
static unsigned int xhc_ring_idx;

static void xhc_handler(cpu_u32 vector, void *opaque)
{
    cpu_u32 n = XHCI_NVEC;
    volatile cpu_u32 *rt;
    cpu_u32 i;
    (void)opaque;
    for (i = 0; i < XHCI_NVEC; ++i)
        if (xhc_vectors[i] == vector) {
            n = i;
            break;
        }
    if (n >= XHCI_NVEC) return;
    ++xhc_count[n];
    xhc_vec[n] = vector;
    xhc_isr[n] = apic_lapic_isr_set(vector);
    ++xhc_seen[n];
    rt = (volatile cpu_u32 *)xhc_rt;
    /* ERDP low: new dequeue + EHB to re-arm; IMAN: clear IP, keep IE. */
    rt[(0x20u + n * 0x20u + 0x18u) / 4] =
        (cpu_u32)(xhc_seg_bus[n] + xhc_seen[n] * 16u) | 0x8u;
    rt[(0x20u + n * 0x20u + 0x00u) / 4] = 0x3u;
}

static cpu_u32 xhc_reg32(cpu_u64 base, cpu_u32 off)
{
    return *(volatile cpu_u32 *)(base + off);
}

static void xhc_wreg32(cpu_u64 base, cpu_u32 off, cpu_u32 v)
{
    *(volatile cpu_u32 *)(base + off) = v;
}

static void xhc_noop(cpu_u64 bar0, cpu_u32 dboff, cpu_u32 n)
{
    volatile cpu_u32 *trb =
        (volatile cpu_u32 *)xhc_cmd.virt + xhc_ring_idx * 4u;
    trb[0] = 0;
    trb[1] = 0;
    trb[2] = n << 22;
    trb[3] = (XHCI_TRB_NOOP << 10) | 1u;
    dma_wmb();
    ++xhc_ring_idx;
    xhc_wreg32(bar0, dboff, 0);
}

static void live_xhci(void)
{
    const struct pci_device *d =
        pci_find_vendor_device(0x1033u, 0x0194u, 0);
    cpu_u32 handle;
    unsigned int vectors[XHCI_NVEC];
    cpu_u64 bar0 = 0;
    cpu_u64 legacy[48];
    cpu_u16 cmd0;
    cpu_u16 cmd;
    struct msi_walk w;
    struct msi_desc md;
    struct msix_geo g;
    cpu_u32 n;
    cpu_u32 caplen;
    cpu_u32 dboff;
    cpu_u32 rtsoff;
    cpu_u64 op;
    cpu_u64 rt;
    cpu_u32 slots;
    cpu_u32 spin;
    if (!d) {
        say("[MSI] xhci none\r\n");
        return;
    }
    require(d->bar_count >= 1 && d->bars[0].index == 0 &&
            d->bars[0].kind == PCI_BAR_MMIO64,
            "x-bar");
    cmd0 = pci_cfg_read16(d->bus, d->device, d->function, PCI_CFG_COMMAND);
    cmd = (cpu_u16)(cmd0 | PCI_COMMAND_MEM | PCI_COMMAND_BUS_MASTER);
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND, cmd);
    require((pci_cfg_read16(d->bus, d->device, d->function,
                            PCI_CFG_COMMAND) &
             (PCI_COMMAND_MEM | PCI_COMMAND_BUS_MASTER)) ==
                (PCI_COMMAND_MEM | PCI_COMMAND_BUS_MASTER),
            "x-cmd");
    require(pci_map_bar(d->bus, d->device, d->function, 0, &bar0) == PCI_OK &&
            bar0 != 0,
            "x-map");
    say("[MSI] map ");
    say_bdf(d->bus, d->device, d->function);
    say(" entry=0 va=");
    say_hex64(bar0);
    say(" pages=");
    say_dec(d->bars[0].size >> 12);
    say("\r\n");
    caplen = *(volatile cpu_u8 *)bar0;
    dboff = xhc_reg32(bar0, 0x14u);
    rtsoff = xhc_reg32(bar0, 0x18u);
    say("[MSI] xhci caplen=");
    say_hex64(caplen);
    say(" dboff=");
    say_hex64(dboff);
    say(" rtsoff=");
    say_hex64(rtsoff);
    say("\r\n");
    op = bar0 + caplen;
    rt = bar0 + rtsoff;
    xhc_rt = rt;
    require(xhc_reg32(op, 0x04u) & 0x1u, "x-halted");
    /* Identity DMA buffers (32-bit bus addresses). */
    require(dma_alloc(4096, 64, DMA_ADDR_32BIT, &xhc_dcbaa) == DMA_OK,
            "x-dcbaa");
    require(dma_alloc(4096, 64, DMA_ADDR_32BIT, &xhc_cmd) == DMA_OK,
            "x-cmdbuf");
    for (n = 0; n < XHCI_NVEC; ++n) {
        volatile cpu_u32 *erst;
        volatile cpu_u8 *seg;
        cpu_u32 i;
        require(dma_alloc(4096, 64, DMA_ADDR_32BIT, &xhc_erst[n]) ==
                DMA_OK,
                "x-erst");
        require(dma_alloc(4096, 64, DMA_ADDR_32BIT, &xhc_seg[n]) == DMA_OK,
                "x-seg");
        xhc_seg_bus[n] = xhc_seg[n].bus;
        xhc_seen[n] = 0;
        xhc_count[n] = 0;
        erst = (volatile cpu_u32 *)xhc_erst[n].virt;
        erst[0] = (cpu_u32)xhc_seg[n].bus;
        erst[1] = (cpu_u32)(xhc_seg[n].bus >> 32);
        erst[2] = XHCI_ER_TRBS;
        erst[3] = 0;
        seg = (volatile cpu_u8 *)xhc_seg[n].virt;
        for (i = 0; i < 4096u; ++i) seg[i] = 0;
    }
    {
        volatile cpu_u8 *cmdring = (volatile cpu_u8 *)xhc_cmd.virt;
        volatile cpu_u8 *dcbaa = (volatile cpu_u8 *)xhc_dcbaa.virt;
        cpu_u32 i;
        for (i = 0; i < 4096u; ++i) {
            cmdring[i] = 0;
            dcbaa[i] = 0;
        }
    }
    xhc_ring_idx = 0;
    slots = xhc_reg32(bar0, 0x04u) & 0xFFu;
    xhc_wreg32(op, 0x38u, slots);
    require((xhc_reg32(op, 0x38u) & 0xFFu) == slots, "x-config");
    xhc_wreg32(op, 0x30u, (cpu_u32)xhc_dcbaa.bus);
    xhc_wreg32(op, 0x34u, (cpu_u32)(xhc_dcbaa.bus >> 32));
    xhc_wreg32(op, 0x18u, (cpu_u32)xhc_cmd.bus | 0x1u);
    xhc_wreg32(op, 0x1Cu, (cpu_u32)(xhc_cmd.bus >> 32));
    for (n = 0; n < XHCI_NVEC; ++n) {
        cpu_u64 ir = rt + 0x20u + (cpu_u64)n * 0x20u;
        xhc_wreg32(ir, 0x08u, 1);
        xhc_wreg32(ir, 0x10u, (cpu_u32)xhc_erst[n].bus);
        xhc_wreg32(ir, 0x14u, (cpu_u32)(xhc_erst[n].bus >> 32));
        xhc_wreg32(ir, 0x18u, (cpu_u32)xhc_seg[n].bus);
        xhc_wreg32(ir, 0x1Cu, (cpu_u32)(xhc_seg[n].bus >> 32));
        xhc_wreg32(ir, 0x04u, 0);
        xhc_wreg32(ir, 0x00u, 0x2u);
    }
    /* Both caps live here (explicit QEMU offsets): MSI MMC=4 parses;
       MSI-X N=16 validates. MME programs without ENABLE. */
    require(msi_walk_caps(d->bus, d->device, d->function, &w) == MSI_OK &&
            w.has_msi && w.has_msix && w.msi_off == 0x70 &&
            w.msix_off == 0x90 && !w.malformed,
            "x-walk");
    require(msi_parse_msi(d->bus, d->device, d->function, w.msi_off, &md) ==
                MSI_OK &&
            md.mmc == 4 && md.mme == 0 && md.is64 && !md.maskbit &&
            !md.enabled,
            "x-msiparse");
    emit_msi(d->bus, d->device, d->function, &md);
    {
        cpu_u16 ctrl = pci_cfg_read16(d->bus, d->device, d->function,
                                      (cpu_u32)w.msi_off + MSI_MSG_CTRL);
        pci_cfg_write16(d->bus, d->device, d->function,
                        (cpu_u32)w.msi_off + MSI_MSG_CTRL,
                        (cpu_u16)((ctrl & ~MSI_CTRL_QSIZE) | (4u << 4)));
        require(((pci_cfg_read16(d->bus, d->device, d->function,
                                 (cpu_u32)w.msi_off + MSI_MSG_CTRL) &
                  MSI_CTRL_QSIZE) >> 4) == 4,
                "x-mme");
        pci_cfg_write16(d->bus, d->device, d->function,
                        (cpu_u32)w.msi_off + MSI_MSG_CTRL,
                        (cpu_u16)(ctrl & ~MSI_CTRL_QSIZE));
        say("[MSI] mme ");
        say_bdf(d->bus, d->device, d->function);
        say(" mmc=4 wrote=4 read=4 restored=1\r\n");
    }
    require(msi_parse_msix(d->bus, d->device, d->function, w.msix_off,
                           &g) == MSI_OK &&
            g.nvec == 16 && g.tbl_bir == 0 && g.tbl_off == 0x3000u &&
            g.pba_bir == 0 && g.pba_off == 0x3800u && !g.enabled,
            "x-msixparse");
    emit_msix(d->bus, d->device, d->function, &g);
    if (d->irq_line < 16)
        require(apic_route_for_irq(d->irq_line) == 0, "x-unrouted");
    legacy_snapshot(legacy);
    require(pci_irq_enable_msix(d->bus, d->device, d->function, XHCI_NVEC,
                                xhc_handler, 0, &handle,
                                vectors) == MSI_OK && vectors[0] == 48,
            "x-enable");
    for (n = 0; n < XHCI_NVEC; ++n) xhc_vectors[n] = vectors[n];
    emit_enable(d->bus, d->device, d->function, "msix", XHCI_NVEC,
                vectors);
    /* Entry readback: host diffs these against the built messages. */
    for (n = 0; n < XHCI_NVEC; ++n) {
        volatile cpu_u32 *e =
            (volatile cpu_u32 *)(bar0 + 0x3000u + (cpu_u64)n * 16u);
        struct msi_msg msg;
        require(msi_build_message(apic_bsp_id(), vectors[n], &msg) ==
                MSI_OK,
                "x-msg");
        emit_msg(vectors[n], &msg, apic_bsp_id());
        say("[MSI] entry ");
        say_bdf(d->bus, d->device, d->function);
        say(" i=");
        say_dec(n);
        say(" addr=");
        say_hex64(e[0]);
        say(" data=");
        say_hex64(e[2]);
        say(" ctrl=");
        say_hex64(e[3]);
        say("\r\n");
        require(e[0] == msg.addr && e[1] == 0 && e[2] == msg.data &&
                e[3] == 0,
                "x-entry");
    }
    /* Run, then one NOOP per interrupter. */
    xhc_wreg32(op, 0x00u, xhc_reg32(op, 0x00u) | 0x5u);
    for (spin = 0; spin < 1000000u; ++spin)
        if (!(xhc_reg32(op, 0x04u) & 0x1u)) break;
    require(!(xhc_reg32(op, 0x04u) & 0x1u), "x-running");
    for (n = 0; n < XHCI_NVEC; ++n) {
        xhc_noop(bar0, dboff, n);
        wait_count(&xhc_count[n], 1, "x-count");
        require(xhc_vec[n] == vectors[n] && xhc_isr[n], "x-vecisr");
        emit_irq(vectors[n], xhc_count[n], xhc_isr[n]);
    }
    /* Mask vector 0: NOOP lands in the PBA, silent; unmask delivers. */
    require(pci_irq_mask(handle, 0, 1) == MSI_OK, "x-mask");
    xhc_noop(bar0, dboff, 0);
    {
        cpu_u64 before = xhc_count[0];
        __asm__ volatile ("sti" : : : "memory");
        for (spin = 0; spin < 5000000u; ++spin) {
        }
        __asm__ volatile ("cli" : : : "memory");
        require(xhc_count[0] == before, "x-masksilent");
    }
    {
        /* PBA MMIO requires dword access (min_access_size 4). */
        volatile cpu_u32 *pba = (volatile cpu_u32 *)(bar0 + 0x3800u);
        require((pba[0] & 0x1u) != 0, "x-pbapending");
        say("[MSI] pba ");
        say_bdf(d->bus, d->device, d->function);
        say(" vec=0 pending=1\r\n");
    }
    require(pci_irq_mask(handle, 0, 0) == MSI_OK, "x-unmask");
    wait_count(&xhc_count[0], 2, "x-uncount");
    {
        volatile cpu_u32 *pba = (volatile cpu_u32 *)(bar0 + 0x3800u);
        require((pba[0] & 0x1u) == 0, "x-pbaclear");
        say("[MSI] pba ");
        say_bdf(d->bus, d->device, d->function);
        say(" vec=0 pending=0\r\n");
    }
    legacy_require_quiet(legacy, "x-silence");
    say("[MSI] intx ");
    say_bdf(d->bus, d->device, d->function);
    say(" intxoff=1 silent=1\r\n");
    require(pci_irq_disable(handle) == MSI_OK, "x-disable");
    emit_disable(handle, "msix");
    for (n = 0; n < XHCI_NVEC; ++n) {
        volatile cpu_u32 *e =
            (volatile cpu_u32 *)(bar0 + 0x3000u + (cpu_u64)n * 16u);
        require(e[3] == MSIX_ENTRY_MASK, "x-remasked");
    }
    require(!(pci_cfg_read16(d->bus, d->device, d->function,
                             (cpu_u32)w.msix_off + MSIX_MSG_CTRL) &
              MSIX_CTRL_ENABLE),
            "x-disoff");
    /* Stop the controller, restore firmware COMMAND, free everything. */
    xhc_wreg32(op, 0x00u, xhc_reg32(op, 0x00u) & ~0x1u);
    for (spin = 0; spin < 1000000u; ++spin)
        if (xhc_reg32(op, 0x04u) & 0x1u) break;
    require(xhc_reg32(op, 0x04u) & 0x1u, "x-halted");
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND, cmd0);
    require(pci_cfg_read16(d->bus, d->device, d->function,
                           PCI_CFG_COMMAND) == cmd0,
            "x-cmdrestore");
    require(dma_free(&xhc_dcbaa) == DMA_OK, "x-freedcbaa");
    require(dma_free(&xhc_cmd) == DMA_OK, "x-freecmd");
    for (n = 0; n < XHCI_NVEC; ++n) {
        require(dma_free(&xhc_erst[n]) == DMA_OK, "x-freerst");
        require(dma_free(&xhc_seg[n]) == DMA_OK, "x-freeseg");
    }
    require(pci_unmap_bar(d->bus, d->device, d->function, 0) == PCI_OK,
            "x-unmap");
    d = pci_find_vendor_device(0x1033u, 0x0194u, 0);
    require(d != 0 && d->bars[0].mapped_va == 0, "x-gone");
    xhc_rt = 0;
}

/* ----- e1000e: dual-cap arbiter + multi-vector MSI-X delivery -----
 *
 * Delivery proof via the device's own cause logic: IVAR routes TXQ0,
 * RXQ0 and OTHER to three distinct MSI-X vectors, and guest writes to
 * the ICS (cause-set) register raise each cause through the full
 * device path (IMS gating -> IVAR -> MSI-X table -> LAPIC), the same
 * legitimacy class as edu's raise register. Verified against QEMU
 * e1000e_core.c (e1000e_set_ics -> set_interrupt_cause ->
 * raise_interrupts -> e1000e_msix_notify) and e1000_regs.h. */

#define E1000_NVEC 3u
#define E1000_ICR 0xC0u
#define E1000_ICS 0xC8u
#define E1000_IMS 0xD0u
#define E1000_IMC 0xD8u
#define E1000_IVAR 0xE4u
#define E1000_CAUSE_RXQ0 0x00100000u
#define E1000_CAUSE_TXQ0 0x00400000u
#define E1000_CAUSE_OTHER 0x01000000u
/* IVAR routes: TXQ0 -> MSI-X 1, RXQ0 -> MSI-X 2, OTHER -> MSI-X 0. */
#define E1000_IVAR_VAL \
    (((0x8u | 2u) << 0) | ((0x8u | 1u) << 8) | ((0x8u | 0u) << 16))
#define E1000_IMS_VAL \
    (E1000_CAUSE_RXQ0 | E1000_CAUSE_TXQ0 | E1000_CAUSE_OTHER)

static volatile cpu_u64 e1000_count[E1000_NVEC];
static volatile cpu_u32 e1000_vec[E1000_NVEC];
static volatile int e1000_isr[E1000_NVEC];
static unsigned int e1000_vectors[E1000_NVEC];
static cpu_u64 e1000_bar0;

static void e1000_handler(cpu_u32 vector, void *opaque)
{
    cpu_u32 i;
    (void)opaque;
    for (i = 0; i < E1000_NVEC; ++i)
        if (e1000_vectors[i] == vector) break;
    if (i >= E1000_NVEC) return;
    ++e1000_count[i];
    e1000_vec[i] = vector;
    e1000_isr[i] = apic_lapic_isr_set(vector);
    /* Device ack: ICR read clears the cause (R/clr). */
    (void)*(volatile cpu_u32 *)(e1000_bar0 + E1000_ICR);
}

static void live_e1000e(void)
{
    const struct pci_device *d =
        pci_find_vendor_device(0x8086u, 0x10D3u, 0);
    cpu_u32 handle;
    cpu_u32 other = 0xFFFFFFFFu;
    unsigned int vectors[E1000_NVEC];
    struct msi_handle_info info;
    struct msi_walk w;
    struct msi_desc md;
    struct msix_geo g;
    struct msi_msg msg;
    cpu_u16 cmd0;
    cpu_u32 e;
    cpu_u32 i;
    cpu_u64 bar0 = 0;
    cpu_u64 legacy[48];
    if (!d) {
        say("[MSI] e1000e none\r\n");
        return;
    }
    cmd0 = pci_cfg_read16(d->bus, d->device, d->function, PCI_CFG_COMMAND);
    require(msi_walk_caps(d->bus, d->device, d->function, &w) == MSI_OK &&
            w.has_msi && w.has_msix && w.msi_off == 0xD0 &&
            w.msix_off == 0xA0 && !w.malformed,
            "n-walk");
    require(msi_parse_msi(d->bus, d->device, d->function, w.msi_off, &md) ==
                MSI_OK &&
            md.mmc == 0 && md.is64 && !md.maskbit && !md.enabled,
            "n-msiparse");
    emit_msi(d->bus, d->device, d->function, &md);
    require(msi_parse_msix(d->bus, d->device, d->function, w.msix_off,
                           &g) == MSI_OK &&
            g.nvec == 5 && g.tbl_bir == 3 && g.tbl_off == 0 &&
            g.pba_bir == 3 && g.pba_off == 0x2000u && !g.enabled,
            "n-msixparse");
    emit_msix(d->bus, d->device, d->function, &g);
    /* Bus master off: MSI-X refuses first (forced clear, then set). */
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND,
                    (cpu_u16)(cmd0 & ~PCI_COMMAND_BUS_MASTER));
    require(!(pci_cfg_read16(d->bus, d->device, d->function,
                             PCI_CFG_COMMAND) &
              PCI_COMMAND_BUS_MASTER),
            "n-bmclear");
    require(pci_irq_enable_auto(d->bus, d->device, d->function, 1,
                                e1000_handler, 0, &handle,
                                vectors) == MSI_REFUSED,
            "n-nobm");
    emit_refuse("auto", d->bus, d->device, d->function);
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND,
                    (cpu_u16)(cmd0 | PCI_COMMAND_BUS_MASTER));
    require((pci_cfg_read16(d->bus, d->device, d->function,
                            PCI_CFG_COMMAND) &
             PCI_COMMAND_BUS_MASTER) != 0,
            "n-bm");
    /* Auto prefers MSI-X; the MSI cap stays untouched. */
    for (i = 0; i < E1000_NVEC; ++i) {
        e1000_count[i] = 0;
        e1000_vectors[i] = 0xFFFFFFFFu;
    }
    require(pci_irq_enable_auto(d->bus, d->device, d->function, 1,
                                e1000_handler, 0, &handle,
                                vectors) == MSI_OK && vectors[0] == 48,
            "n-auto");
    e1000_vectors[0] = vectors[0];
    require(pci_irq_info(handle, &info) == MSI_OK &&
            info.kind == MSI_KIND_MSIX,
            "n-autokind");
    emit_enable(d->bus, d->device, d->function, "msix", 1, vectors);
    require(!(pci_cfg_read16(d->bus, d->device, d->function,
                             (cpu_u32)w.msi_off + MSI_MSG_CTRL) &
              MSI_CTRL_ENABLE),
            "n-msiclean");
    /* MSI while MSI-X lives refuses. */
    require(pci_irq_enable_msi(d->bus, d->device, d->function, 1,
                               e1000_handler, 0, &other,
                               vectors) == MSI_STATE,
            "n-blocked");
    emit_refuse("msi-while-x", d->bus, d->device, d->function);
    require(pci_irq_disable(handle) == MSI_OK, "n-disx");
    emit_disable(handle, "msix");
    d = pci_find_vendor_device(0x8086u, 0x10D3u, 0);
    require(d != 0, "n-refind");
    for (e = 0; e < d->bar_count; ++e)
        if (d->bars[e].index == 3)
            require(d->bars[e].mapped_va == 0, "n-unmapped");
    require(e1000_count[0] == 0, "n-silent");
    /* Multi-vector delivery: map BAR0, route IVAR, enable 3 vectors. */
    for (e = 0; e < d->bar_count; ++e)
        if (d->bars[e].index == 0)
            require(d->bars[e].kind == PCI_BAR_MMIO32, "n-bar0kind");
    require(pci_map_bar(d->bus, d->device, d->function, 0, &bar0) == PCI_OK &&
            bar0 != 0,
            "n-map");
    e1000_bar0 = bar0;
    say("[MSI] map ");
    say_bdf(d->bus, d->device, d->function);
    say(" entry=0 va=");
    say_hex64(bar0);
    say(" pages=");
    for (e = 0; e < d->bar_count; ++e)
        if (d->bars[e].index == 0) say_dec(d->bars[e].size >> 12);
    say("\r\n");
    if (d->irq_line < 16)
        require(apic_route_for_irq(d->irq_line) == 0, "n-unrouted");
    legacy_snapshot(legacy);
    for (i = 0; i < E1000_NVEC; ++i) {
        e1000_count[i] = 0;
        e1000_vectors[i] = 0xFFFFFFFFu;
    }
    require(pci_irq_enable_msix(d->bus, d->device, d->function, E1000_NVEC,
                                e1000_handler, 0, &handle,
                                vectors) == MSI_OK &&
            vectors[0] == 48 && vectors[1] == 49 && vectors[2] == 50,
            "n-enable3");
    for (i = 0; i < E1000_NVEC; ++i) e1000_vectors[i] = vectors[i];
    emit_enable(d->bus, d->device, d->function, "msix", E1000_NVEC,
                vectors);
    /* Route + arm: IVAR entries, then the IMS causes. */
    *(volatile cpu_u32 *)(bar0 + E1000_IVAR) = E1000_IVAR_VAL;
    require(*(volatile cpu_u32 *)(bar0 + E1000_IVAR) == E1000_IVAR_VAL,
            "n-ivar");
    *(volatile cpu_u32 *)(bar0 + E1000_IMS) = E1000_IMS_VAL;
    /* TXQ0 -> MSI-X 1 -> CPU 49. */
    *(volatile cpu_u32 *)(bar0 + E1000_ICS) = E1000_CAUSE_TXQ0;
    wait_count(&e1000_count[1], 1, "n-txq0");
    require(e1000_vec[1] == 49 && e1000_isr[1], "n-txvec");
    emit_irq(49, e1000_count[1], e1000_isr[1]);
    /* RXQ0 -> MSI-X 2 -> CPU 50. */
    *(volatile cpu_u32 *)(bar0 + E1000_ICS) = E1000_CAUSE_RXQ0;
    wait_count(&e1000_count[2], 1, "n-rxq0");
    require(e1000_vec[2] == 50 && e1000_isr[2], "n-rxvec");
    emit_irq(50, e1000_count[2], e1000_isr[2]);
    /* OTHER -> MSI-X 0 -> CPU 48. */
    *(volatile cpu_u32 *)(bar0 + E1000_ICS) = E1000_CAUSE_OTHER;
    wait_count(&e1000_count[0], 1, "n-other");
    require(e1000_vec[0] == 48 && e1000_isr[0], "n-ovec");
    emit_irq(48, e1000_count[0], e1000_isr[0]);
    /* Mask MSI-X 2: ICS lands in the PBA, silent; unmask delivers. */
    require(pci_irq_mask(handle, 2, 1) == MSI_OK, "n-mask");
    *(volatile cpu_u32 *)(bar0 + E1000_ICS) = E1000_CAUSE_RXQ0;
    {
        cpu_u64 before = e1000_count[2];
        __asm__ volatile ("sti" : : : "memory");
        for (e = 0; e < 5000000u; ++e) {
        }
        __asm__ volatile ("cli" : : : "memory");
        require(e1000_count[2] == before, "n-masksilent");
    }
    require(pci_irq_mask(handle, 2, 0) == MSI_OK, "n-unmask");
    wait_count(&e1000_count[2], 2, "n-uncount");
    legacy_require_quiet(legacy, "n-silence");
    say("[MSI] intx ");
    say_bdf(d->bus, d->device, d->function);
    say(" intxoff=1 silent=1\r\n");
    require(pci_irq_disable(handle) == MSI_OK, "n-dis3");
    emit_disable(handle, "msix");
    *(volatile cpu_u32 *)(bar0 + E1000_IMC) = 0xFFFFFFFFu;
    /* MSI proves alone (program + readback, no trigger). */
    for (i = 0; i < E1000_NVEC; ++i) {
        e1000_count[i] = 0;
        e1000_vectors[i] = 0xFFFFFFFFu;
    }
    require(pci_irq_enable_msi(d->bus, d->device, d->function, 1,
                               e1000_handler, 0, &handle,
                               vectors) == MSI_OK && vectors[0] == 48,
            "n-msi");
    e1000_vectors[0] = vectors[0];
    require(msi_build_message(apic_bsp_id(), vectors[0], &msg) == MSI_OK,
            "n-msg");
    emit_msg(vectors[0], &msg, apic_bsp_id());
    require(pci_cfg_read32(d->bus, d->device, d->function,
                           (cpu_u32)w.msi_off + MSI_ADDR_LO) == msg.addr,
            "n-msiaddr");
    emit_enable(d->bus, d->device, d->function, "msi", 1, vectors);
    require(pci_irq_disable(handle) == MSI_OK, "n-dismsi");
    emit_disable(handle, "msi");
    pci_cfg_write16(d->bus, d->device, d->function, PCI_CFG_COMMAND, cmd0);
    require(pci_cfg_read16(d->bus, d->device, d->function,
                           PCI_CFG_COMMAND) == cmd0,
            "n-cmdrestore");
    require(pci_unmap_bar(d->bus, d->device, d->function, 0) == PCI_OK,
            "n-unmap");
    d = pci_find_vendor_device(0x8086u, 0x10D3u, 0);
    require(d != 0, "n-refind2");
    for (e = 0; e < d->bar_count; ++e)
        require(d->bars[e].mapped_va == 0, "n-gone");
    e1000_bar0 = 0;
}

/* ----- pci-testdev negative control ----- */

static void live_testdev(void)
{
    const struct pci_device *d =
        pci_find_vendor_device(0x1B36u, 0x0005u, 0);
    struct msi_walk w;
    cpu_u32 handle = 0xFFFFFFFFu;
    unsigned int vectors[1];
    if (!d) {
        say("[MSI] testdev none\r\n");
        return;
    }
    require(msi_walk_caps(d->bus, d->device, d->function, &w) == MSI_OK &&
            !w.has_msi && !w.has_msix && w.malformed == 1,
            "t-walk");
    require(pci_irq_enable_auto(d->bus, d->device, d->function, 1,
                                e1000_handler, 0, &handle,
                                vectors) == MSI_ABSENT,
            "t-absent");
    emit_refuse("auto", d->bus, d->device, d->function);
}

/* ----- live driver ----- */

static void phase_live(void)
{
    cpu_u32 i;
    cpu_u32 n;
    cpu_u32 h;
    pci_cfg_install_ops(0); /* legacy CF8/CFC backend */
    require(pci_initialize() == PCI_OK, "live-init");
    require(apic_active(), "live-noapic");
    n = pci_device_count();
    require(n > 0, "live-nonempty");
    /* The message builder assumes the stock LAPIC window (Linux
       parity): trip loudly if firmware ever moves it. */
    say("[MSI] lapic base=");
    say_hex64(apic_lapic_base());
    say("\r\n");
    require(apic_lapic_base() == MSI_ADDR_BASE, "live-lapicbase");
    for (i = 0; i < n; ++i) {
        const struct pci_device *d = pci_device_at(i);
        struct msi_walk w;
        require(d != 0, "live-at");
        require(msi_walk_caps(d->bus, d->device, d->function, &w) ==
                MSI_OK,
                "live-walk");
        emit_walk(d->bus, d->device, d->function, &w);
    }
    {
        struct pmm_statistics before;
        struct pmm_statistics after;
        require(pmm_statistics(&before) == PMM_OK, "live-cost0");
        live_edu();
        live_xhci();
        live_e1000e();
        live_testdev();
        require(pmm_statistics(&after) == PMM_OK, "live-cost1");
        say("[MSI] cost alloc0=");
        say_hex64(before.allocated_bytes);
        say(" alloc1=");
        say_hex64(after.allocated_bytes);
        say("\r\n");
    }
    /* Leak checks: pool pristine, no live handles. */
    require(apic_vector_free() == 80, "live-vecfree");
    for (h = 0; h < MSI_MAX_HANDLES; ++h) {
        struct msi_handle_info info;
        require(pci_irq_info(h, &info) == MSI_STATE, "live-noleak");
    }
    say("[MSI] live ok devices=");
    say_dec(n);
    say("\r\n");
}

void msi_self_test(void)
{
    __asm__ volatile ("cli" : : : "memory");
    phase_walk();
    phase_msi_parse();
    phase_msg();
    phase_msix_geo();
    phase_alloc();
    phase_synth_msi();
    say("[MSI] synthetic ok cases=");
    say_dec(cases);
    say("\r\n");
    phase_live();
    say("[MSI] msi verified\r\n");
    (void)serial_flush();
}
