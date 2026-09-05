/* Stage 17a IDE block driver: PIIX3 compatibility-mode PIO, LBA28, polling.
   No DMA, no interrupts, one outstanding command: completion identity is
   structural, timeouts are bounded loop caps, and every bound is checked
   before hardware is touched. See kernel/include/blk.h and
   docs/design/block-storage.md. */
#include "blk.h"
#include "cpu.h"
#include "io.h"
#include "irq.h"

/* Compatibility ports (used only after PCI provenance, see below). */
#define IDE0_CMD 0x1f0u
#define IDE0_CTL 0x3f6u
#define IDE1_CMD 0x170u
#define IDE1_CTL 0x376u
#define IDE_REG_COUNT 2u
#define IDE_REG_LBA0 3u
#define IDE_REG_LBA1 4u
#define IDE_REG_LBA2 5u
#define IDE_REG_DRIVE 6u
#define IDE_REG_STATUS 7u
#define IDE_CMD_IDENTIFY 0xecu
#define IDE_CMD_READ 0x20u
#define IDE_CMD_WRITE 0x30u
#define IDE_SR_BSY 0x80u
#define IDE_SR_DRDY 0x40u
#define IDE_SR_DF 0x20u
#define IDE_SR_DRQ 0x08u
#define IDE_SR_ERR 0x01u
/* PIIX3 IDE at 00:01.1 on pc-i440fx (ISA bridge is 00:01.0). */
#define PCI_IDE_ADDR(reg) (0x80000000u | (1u << 11) | (1u << 8) | ((reg) & 0xfcu))
#define PCI_IDE_ID 0x70108086u
#define PCI_CLASS_IDE 0x0101u
#define BLK_LBA28_MAX 0x10000000ull
#define BLK_MAGIC "RLBLK1\0\0"
#define BLK_MAGIC_LEN 8u

struct ide_slot {
    cpu_u16 cmd;
    cpu_u16 ctl;
    cpu_u8 slave;
};

static const struct ide_slot SLOTS[BLK_MAX_DEVICES] = {
    { IDE0_CMD, IDE0_CTL, 0 }, { IDE0_CMD, IDE0_CTL, 1 },
    { IDE1_CMD, IDE1_CTL, 0 }, { IDE1_CMD, IDE1_CTL, 1 },
};

struct blk_state {
    int present;
    int test_device;
    cpu_u64 block_count;
};

static struct blk_state devices[BLK_MAX_DEVICES];

/* Failing discovery stage for diagnostics (static text, no allocation). */
static const char *blk_stage = "none";

static cpu_u32 pci_read(cpu_u32 reg)
{ io_out32(0xcf8, PCI_IDE_ADDR(reg)); return io_in32(0xcfc); }

static void delay400ns(cpu_u16 ctl)
{
    io_in8(ctl); io_in8(ctl); io_in8(ctl); io_in8(ctl);
}

static int poll_clear_bsy(cpu_u16 status)
{
    for (cpu_u32 i = 0; i < BLK_POLL_LIMIT; ++i)
        if (!(io_in8(status) & IDE_SR_BSY)) return 1;
    return 0;
}

static int poll_set_drq(cpu_u16 status)
{
    for (cpu_u32 i = 0; i < BLK_POLL_LIMIT; ++i) {
        cpu_u8 s = io_in8(status);
        if (s & IDE_SR_BSY) continue;
        if (s & IDE_SR_ERR) return -1;
        if (s & IDE_SR_DRQ) return 1;
    }
    return 0;
}

/* BSY clear and DRDY set: the device accepts a command. Bounded; absent or
   wedged hardware yields TIMEOUT rather than an issued-into-busy command. */
static int poll_ready(cpu_u16 status)
{
    for (cpu_u32 i = 0; i < BLK_POLL_LIMIT; ++i) {
        cpu_u8 s = io_in8(status);
        if (!(s & IDE_SR_BSY) && (s & IDE_SR_DRDY)) return 1;
    }
    return 0;
}

static void select_drive(const struct ide_slot *slot)
{
    io_out8(slot->cmd + IDE_REG_DRIVE, (cpu_u8)(0xe0u | (slot->slave << 4)));
    delay400ns(slot->ctl);
}

/* IDENTIFY one slot into words[256]. Returns 1 when a working ATA disk
   answers, 0 when the slot is empty or non-ATA. An ERR abort here means
   "no ATA disk" (SeaBIOS practice: empty QEMU slots abort IDENTIFY), never
   a fatal error: fatal I/O failures surface in real transfers instead.
   ATAPI signatures are skipped the same way (no ATAPI support claimed). */
static int identify(const struct ide_slot *slot, cpu_u16 *words)
{
    select_drive(slot);
    cpu_u8 status = io_in8(slot->cmd + IDE_REG_STATUS);
    if (status == 0x00u || status == 0xffu) return 0;
    if (!poll_ready(slot->cmd + IDE_REG_STATUS)) return 0;
    io_out8(slot->cmd + IDE_REG_COUNT, 0);
    io_out8(slot->cmd + IDE_REG_LBA0, 0);
    io_out8(slot->cmd + IDE_REG_LBA1, 0);
    io_out8(slot->cmd + IDE_REG_LBA2, 0);
    io_out8(slot->cmd + IDE_REG_STATUS, IDE_CMD_IDENTIFY);
    if (!poll_clear_bsy(slot->cmd + IDE_REG_STATUS)) return 0;
    status = io_in8(slot->cmd + IDE_REG_STATUS);
    /* Any abort (including ATAPI signatures, which share this path) means
       no usable ATA disk here. */
    if (status & IDE_SR_ERR) return 0;
    if (!(status & IDE_SR_DRQ)) return 0;
    io_insw(slot->cmd, words, 256);
    return 1;
}

static cpu_u32 words_u32(const cpu_u16 *w, unsigned index)
{ return (cpu_u32)w[index] | ((cpu_u32)w[index + 1] << 16); }

static int check_geometry(const cpu_u16 *words, cpu_u64 *count_out)
{
    /* LBA support is mandatory; CHS-only devices are refused, not guessed. */
    if (!(words[49] & 0x0200u)) return BLK_UNSUPPORTED;
    cpu_u64 count = (cpu_u64)words_u32(words, 60);
    if (!count) return BLK_NODEV;
    /* Word 106: bit15=0 + bit14=1 marks the field valid; bit12 set means a
       logical sector longer than 256 words, which LBA28 PIO cannot move. */
    cpu_u16 w106 = words[106];
    if ((w106 & 0xc000u) == 0x4000u && (w106 & 0x1000u)) return BLK_UNSUPPORTED;
    /* LBA28 addresses 2^28 sectors; larger disks are capped, not wrapped. */
    *count_out = count > BLK_LBA28_MAX ? BLK_LBA28_MAX : count;
    return BLK_OK;
}

static cpu_u64 read_le64(const cpu_u8 *p)
{
    cpu_u64 v = 0;
    for (unsigned i = 0; i < 8; ++i) v |= (cpu_u64)p[i] << (i * 8);
    return v;
}

static cpu_u32 read_le32(const cpu_u8 *p)
{
    return (cpu_u32)p[0] | ((cpu_u32)p[1] << 8) | ((cpu_u32)p[2] << 16) | ((cpu_u32)p[3] << 24);
}

/* Transfer one addressed sector. dir 0 = read, 1 = write. Buffer must hold
   512 bytes at an even address (checked by callers, asserted never). */
static int one_sector(const struct ide_slot *slot, cpu_u64 lba, cpu_u16 *buf, int dir)
{
    select_drive(slot);
    if (!poll_ready(slot->cmd + IDE_REG_STATUS)) return BLK_TIMEOUT;
    io_out8(slot->cmd + IDE_REG_COUNT, 1);
    io_out8(slot->cmd + IDE_REG_LBA0, (cpu_u8)lba);
    io_out8(slot->cmd + IDE_REG_LBA1, (cpu_u8)(lba >> 8));
    io_out8(slot->cmd + IDE_REG_LBA2, (cpu_u8)(lba >> 16));
    io_out8(slot->cmd + IDE_REG_DRIVE,
            (cpu_u8)(0xe0u | (slot->slave << 4) | ((lba >> 24) & 0x0fu)));
    io_out8(slot->cmd + IDE_REG_STATUS, dir ? IDE_CMD_WRITE : IDE_CMD_READ);
    if (!poll_clear_bsy(slot->cmd + IDE_REG_STATUS)) return BLK_TIMEOUT;
    int drq = poll_set_drq(slot->cmd + IDE_REG_STATUS);
    if (drq < 0) return BLK_IOERR;
    if (!drq) return BLK_TIMEOUT;
    if (dir) io_outsw(slot->cmd, buf, 256);
    else io_insw(slot->cmd, buf, 256);
    /* Post-transfer status: cached writes complete here; errors surface now. */
    if (!poll_clear_bsy(slot->cmd + IDE_REG_STATUS)) return BLK_TIMEOUT;
    cpu_u8 end = io_in8(slot->cmd + IDE_REG_STATUS);
    if (end & (IDE_SR_ERR | IDE_SR_DF)) return BLK_IOERR;
    return BLK_OK;
}

static int valid_range(const struct blk_state *dev, cpu_u64 start, cpu_u32 count, cpu_u64 len)
{
    /* Every term ordered so no addition can wrap before its check runs. */
    if (!count || count > BLK_MAX_BLOCKS) return BLK_INVALID;
    if (len != (cpu_u64)count * BLK_SECTOR_SIZE) return BLK_INVALID;
    if (start >= dev->block_count) return BLK_RANGE;
    if (count > dev->block_count - start) return BLK_RANGE;
    return BLK_OK;
}

int blk_discover(void)
{
    if (!cpu_interrupts_disabled() || irq_in_context()) { blk_stage = "interrupts"; return BLK_INVALID; }
    /* Provenance first: PIIX3 IDE function with IDE class/subclass in
       compatibility mode. Fixed ports are used only on this match; native
       modes (unprogrammed BARs would lie) fail closed. */
    if (pci_read(0) != PCI_IDE_ID) { blk_stage = "pci-id"; return BLK_INIT_FAIL; }
    if ((pci_read(8) >> 16) != PCI_CLASS_IDE) { blk_stage = "pci-class"; return BLK_INIT_FAIL; }
    if ((pci_read(8) >> 8) & 0x05u) { blk_stage = "pci-mode"; return BLK_INIT_FAIL; }
    int found = 0;
    for (cpu_u32 i = 0; i < BLK_MAX_DEVICES; ++i) {
        devices[i].present = 0;
        devices[i].test_device = 0;
        devices[i].block_count = 0;
        cpu_u16 words[256];
        int present = identify(&SLOTS[i], words);
        if (!present) continue;
        cpu_u64 count = 0;
        int ok = check_geometry(words, &count);
        if (ok == BLK_NODEV) continue;
        if (ok) { blk_stage = "geometry"; return ok; }
        /* Sector-0 probe: both IDENTIFY and a readable first sector are
           required before any address on this device is trusted. */
        static cpu_u16 sector0[256];
        int rd = one_sector(&SLOTS[i], 0, sector0, 0);
        if (rd) { blk_stage = "sector0"; return rd; }
        devices[i].present = 1;
        devices[i].block_count = count;
        ++found;
        const cpu_u8 *b = (const cpu_u8 *)sector0;
        int magic = 1;
        for (unsigned k = 0; k < BLK_MAGIC_LEN; ++k)
            if (b[k] != (cpu_u8)BLK_MAGIC[k]) { magic = 0; break; }
        /* Cross-check the header against IDENTIFY geometry; a mismatch
           means this is not our test device (never trust one source). */
        if (magic && read_le32(b + 8) == BLK_SECTOR_SIZE && read_le64(b + 12) == count)
            devices[i].test_device = 1;
    }
    if (!found) { blk_stage = "nodisk"; return BLK_INIT_FAIL; }
    blk_stage = "none";
    return found;
}

const char *blk_stage_detail(void) { return blk_stage; }

cpu_u32 blk_count(void)
{
    cpu_u32 n = 0;
    for (cpu_u32 i = 0; i < BLK_MAX_DEVICES; ++i) n += (cpu_u32)devices[i].present;
    return n;
}

const struct blk_device *blk_device(cpu_u32 id)
{
    static struct blk_device view;
    if (id >= BLK_MAX_DEVICES || !devices[id].present) return 0;
    view.id = id;
    view.block_size = BLK_SECTOR_SIZE;
    view.block_count = devices[id].block_count;
    view.test_device = devices[id].test_device;
    view.present = 1;
    return &view;
}

int blk_find_test(void)
{
    for (cpu_u32 i = 0; i < BLK_MAX_DEVICES; ++i)
        if (devices[i].present && devices[i].test_device) return (int)i;
    return BLK_NODEV;
}

static int transfer(cpu_u32 id, cpu_u64 start, cpu_u32 count, void *buf, cpu_u64 len, int dir, int need_test)
{
    if (id >= BLK_MAX_DEVICES || !devices[id].present) return BLK_NODEV;
    if (!buf || ((cpu_u64)buf & 1u)) return BLK_INVALID;
    struct blk_state snapshot = devices[id];
    int range = valid_range(&snapshot, start, count, len);
    if (range) return range;
    if (snapshot.block_count > BLK_LBA28_MAX) return BLK_RANGE;
    if (need_test && !snapshot.test_device) return BLK_DENIED;
    cpu_u8 *bytes = (cpu_u8 *)buf;
    for (cpu_u32 i = 0; i < count; ++i) {
        int rc = one_sector(&SLOTS[id], start + i, (cpu_u16 *)(bytes + (cpu_u64)i * BLK_SECTOR_SIZE), dir);
        if (rc) return rc;
    }
    return BLK_OK;
}

int blk_read(cpu_u32 id, cpu_u64 start, cpu_u32 count, void *buf, cpu_u64 len)
{ return transfer(id, start, count, buf, len, 0, 0); }

int blk_write(cpu_u32 id, cpu_u64 start, cpu_u32 count, void *buf, cpu_u64 len)
{ return transfer(id, start, count, buf, len, 1, 1); }

const char *blk_error_str(int code)
{
    switch (code) {
    case BLK_OK: return "ok";
    case BLK_INVALID: return "invalid";
    case BLK_RANGE: return "range";
    case BLK_UNSUPPORTED: return "unsupported";
    case BLK_NODEV: return "nodev";
    case BLK_INIT_FAIL: return "init-fail";
    case BLK_IOERR: return "ioerr";
    case BLK_TIMEOUT: return "timeout";
    case BLK_DENIED: return "denied";
    default: return "unknown";
    }
}
