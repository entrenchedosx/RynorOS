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
#define IDE_CHANNELS 2u

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
    int writable;
    cpu_u64 block_count;
};

static struct blk_state devices[BLK_MAX_DEVICES];

/* A timed-out command leaves the entire shared taskfile channel in an
   unknown host/device state. There is deliberately no in-place recovery:
   without reset-and-revalidation, only a platform restart clears this. */
static cpu_u8 channel_quarantined[IDE_CHANNELS];

#if defined(RYNOR_BLK_SCRIPT_TEST) && RYNOR_BLK_SCRIPT_TEST
/* Scripted port backend used only by the gated timeout regression. It drives
   the same block API path as hardware while recording every taskfile access. */
struct blk_script_io {
    int active;
    int primary_late;
    int secondary_drq_stuck;
    int preselect_phase;
    int ordering_test;
    int order_select_armed;
    cpu_u32 order_preselect_reads[IDE_CHANNELS];
    cpu_u32 order_command_reads[IDE_CHANNELS];
    cpu_u8 order_command[IDE_CHANNELS];
    cpu_u8 order_trace[32];
    cpu_u32 order_trace_count;
    cpu_u32 status_reads[IDE_CHANNELS];
    cpu_u8 last_status[IDE_CHANNELS];
    cpu_u32 port_reads;
    cpu_u32 port_writes;
    cpu_u32 taskfile_writes;
    cpu_u32 command_writes[IDE_CHANNELS];
    cpu_u32 command_while_drq[IDE_CHANNELS];
    cpu_u32 pio_words[IDE_CHANNELS];
};
static struct blk_script_io blk_script;

static void blk_order_trace(cpu_u8 event)
{
    if (blk_script.order_trace_count < sizeof blk_script.order_trace)
        blk_script.order_trace[blk_script.order_trace_count++] = event;
}

static cpu_u8 blk_order_status_class(cpu_u8 status)
{
    if (status & IDE_SR_BSY) return 1u;
    if (status & IDE_SR_DRQ) return 2u;
    if (status & IDE_SR_DRDY) return 3u;
    return 4u;
}

#if defined(RYNOR_BLK_SCRIPT_TEST) && RYNOR_BLK_SCRIPT_TEST
static int blk_order_trace_is(const cpu_u8 *expected, cpu_u32 count)
{
    if (blk_script.order_trace_count != count) return 0;
    for (cpu_u32 i = 0; i < count; ++i)
        if (blk_script.order_trace[i] != expected[i]) return 0;
    return 1;
}
#endif

static cpu_u8 blk_in8(cpu_u16 port)
{
    if (!blk_script.active) return io_in8(port);
    ++blk_script.port_reads;
    if (port == IDE0_CMD + IDE_REG_STATUS) {
        if (blk_script.preselect_phase) {
            cpu_u32 n = blk_script.order_preselect_reads[0]++;
            cpu_u8 status = IDE_SR_DRDY;
            if (blk_script.ordering_test) {
                if (n == 0u) status = IDE_SR_BSY;
                else if (n == 1u) status = IDE_SR_DRDY | IDE_SR_DRQ;
                if (n < 3u)
                    blk_order_trace(blk_order_status_class(status));
            }
            blk_script.last_status[0] = status;
            return status;
        }
        if (blk_script.ordering_test) {
            cpu_u8 status;
            if (!blk_script.order_command[0]) {
                status = IDE_SR_DRDY;
                blk_order_trace((cpu_u8)(0x20u | blk_order_status_class(status)));
            } else {
                cpu_u32 n = blk_script.order_command_reads[0]++;
                if (blk_script.order_command[0] == IDE_CMD_IDENTIFY)
                    status = n == 1u ? (IDE_SR_DRDY | IDE_SR_DRQ) : IDE_SR_DRDY;
                else
                    status = n == 1u ? (IDE_SR_DRDY | IDE_SR_DRQ) : IDE_SR_DRDY;
                blk_order_trace((cpu_u8)(0x40u | blk_order_status_class(status)));
            }
            blk_script.last_status[0] = status;
            return status;
        }
        cpu_u32 n = blk_script.status_reads[0]++;
        cpu_u8 status;
        if (!n) status = IDE_SR_DRDY;
        else if (n == 1u) status = IDE_SR_DRDY;
        else if (n == 2u) status = IDE_SR_DRDY | IDE_SR_DRQ;
        else status = blk_script.primary_late ? IDE_SR_DRDY : IDE_SR_BSY;
        blk_script.last_status[0] = status;
        return status;
    }
    if (port == IDE1_CMD + IDE_REG_STATUS) {
        if (blk_script.preselect_phase) {
            cpu_u32 n = blk_script.order_preselect_reads[1]++;
            cpu_u8 status = IDE_SR_DRDY;
            if (blk_script.ordering_test) {
                if (n == 0u) status = IDE_SR_BSY;
                else if (n == 1u) status = IDE_SR_DRDY | IDE_SR_DRQ;
                if (n < 3u)
                    blk_order_trace(blk_order_status_class(status));
            }
            blk_script.last_status[1] = status;
            return status;
        }
        cpu_u32 n = blk_script.status_reads[1]++;
        cpu_u8 status;
        if (blk_script.secondary_drq_stuck) status = IDE_SR_DRDY | IDE_SR_DRQ;
        else if (n == 2u) status = IDE_SR_DRDY | IDE_SR_DRQ;
        else status = IDE_SR_DRDY;
        blk_script.last_status[1] = status;
        return status;
    }
    return 0;
}

static void blk_out8(cpu_u16 port, cpu_u8 value)
{
    if (!blk_script.active) { io_out8(port, value); return; }
    ++blk_script.port_writes;
    if (blk_script.ordering_test && blk_script.order_select_armed &&
        (port == IDE0_CMD + IDE_REG_DRIVE || port == IDE1_CMD + IDE_REG_DRIVE)) {
        cpu_u32 channel = port == IDE1_CMD + IDE_REG_DRIVE ? 1u : 0u;
        blk_order_trace((cpu_u8)(0x10u | ((value >> 4) & 1u)));
        blk_script.order_command[channel] = 0;
        blk_script.order_command_reads[channel] = 0;
        blk_script.order_select_armed = 0;
    }
    if (port >= IDE0_CMD + IDE_REG_COUNT && port <= IDE0_CMD + IDE_REG_STATUS) {
        ++blk_script.taskfile_writes;
        if (port == IDE0_CMD + IDE_REG_STATUS) {
            ++blk_script.command_writes[0];
            if (blk_script.last_status[0] & IDE_SR_DRQ)
                ++blk_script.command_while_drq[0];
            if (blk_script.ordering_test) {
                blk_script.order_command[0] = value;
                blk_script.order_command_reads[0] = 0;
                blk_order_trace((cpu_u8)(0x30u |
                    (value == IDE_CMD_IDENTIFY ? 1u : 2u)));
            }
        }
    } else if (port >= IDE1_CMD + IDE_REG_COUNT && port <= IDE1_CMD + IDE_REG_STATUS) {
        ++blk_script.taskfile_writes;
        if (port == IDE1_CMD + IDE_REG_STATUS) {
            ++blk_script.command_writes[1];
            if (blk_script.last_status[1] & IDE_SR_DRQ)
                ++blk_script.command_while_drq[1];
        }
    }
    (void)value;
}

static void blk_insw(cpu_u16 port, cpu_u16 *buf, cpu_u32 words)
{
    if (!blk_script.active) { io_insw(port, buf, words); return; }
    for (cpu_u32 i = 0; i < words; ++i) buf[i] = 0;
}

static void blk_outsw(cpu_u16 port, const cpu_u16 *buf, cpu_u32 words)
{
    if (!blk_script.active) io_outsw(port, buf, words);
    else if (port == IDE0_CMD) blk_script.pio_words[0] += words;
    else if (port == IDE1_CMD) blk_script.pio_words[1] += words;
    (void)port; (void)buf; (void)words;
}

static cpu_u32 blk_in32(cpu_u16 port)
{
    return blk_script.active ? 0u : io_in32(port);
}
static void blk_out32(cpu_u16 port, cpu_u32 value)
{
    if (!blk_script.active) io_out32(port, value); (void)port; (void)value;
}
#else
#define blk_in8 io_in8
#define blk_out8 io_out8
#define blk_insw io_insw
#define blk_outsw io_outsw
#define blk_in32 io_in32
#define blk_out32 io_out32
#endif

static cpu_u32 channel_index(const struct ide_slot *slot)
{ return slot->cmd == IDE1_CMD ? 1u : 0u; }

static int channel_blocked(const struct ide_slot *slot)
{ return channel_quarantined[channel_index(slot)] != 0; }

static void quarantine_channel(const struct ide_slot *slot)
{ channel_quarantined[channel_index(slot)] = 1; }

/* Failing discovery stage for diagnostics (static text, no allocation). */
static const char *blk_stage = "none";

static cpu_u32 pci_read(cpu_u32 reg)
{ blk_out32(0xcf8, PCI_IDE_ADDR(reg)); return blk_in32(0xcfc); }

static void delay400ns(cpu_u16 ctl)
{
    blk_in8(ctl); blk_in8(ctl); blk_in8(ctl); blk_in8(ctl);
}

static int poll_clear_bsy(cpu_u16 status)
{
    for (cpu_u32 i = 0; i < BLK_POLL_LIMIT; ++i)
        if (!(blk_in8(status) & IDE_SR_BSY)) return 1;
    return 0;
}

static int poll_set_drq(const struct ide_slot *slot)
{
    for (cpu_u32 i = 0; i < BLK_POLL_LIMIT; ++i) {
        cpu_u8 s = blk_in8(slot->cmd + IDE_REG_STATUS);
        if (s & IDE_SR_BSY) continue;
        if (s & (IDE_SR_ERR | IDE_SR_DF)) {
            if (s & IDE_SR_DRQ) quarantine_channel(slot);
            return -1;
        }
        if (s & IDE_SR_DRQ) return 1;
    }
    return 0;
}

/* The selected device accepts a command only in !BSY, DRDY, !DRQ state.
   Bounded; absent or wedged hardware yields TIMEOUT. */
static int poll_ready(cpu_u16 status)
{
    for (cpu_u32 i = 0; i < BLK_POLL_LIMIT; ++i) {
        cpu_u8 s = blk_in8(status);
        if (!(s & (IDE_SR_BSY | IDE_SR_DRQ)) && (s & IDE_SR_DRDY)) return 1;
    }
    return 0;
}

/* ATA host-idle ordering: the Device/Head register must not be changed while
   the currently selected device is BSY or still owns a DRQ data phase. The
   selected target's DRDY state is checked separately after selection. */
static int poll_channel_idle(const struct ide_slot *slot)
{
    int idle = 0;
#if defined(RYNOR_BLK_SCRIPT_TEST) && RYNOR_BLK_SCRIPT_TEST
    int scripted = blk_script.active;
    if (scripted) {
        blk_script.preselect_phase = 1;
        if (blk_script.ordering_test) {
            blk_script.order_preselect_reads[channel_index(slot)] = 0;
            blk_script.order_select_armed = 1;
        }
    }
#endif
    for (cpu_u32 i = 0; i < BLK_POLL_LIMIT; ++i) {
        cpu_u8 s = blk_in8(slot->cmd + IDE_REG_STATUS);
        if (!(s & (IDE_SR_BSY | IDE_SR_DRQ))) {
            idle = 1;
            break;
        }
    }
#if defined(RYNOR_BLK_SCRIPT_TEST) && RYNOR_BLK_SCRIPT_TEST
    if (scripted) blk_script.preselect_phase = 0;
    if (!idle && scripted) blk_script.order_select_armed = 0;
#endif
    return idle;
}

static void select_drive(const struct ide_slot *slot)
{
    blk_out8(slot->cmd + IDE_REG_DRIVE, (cpu_u8)(0xe0u | (slot->slave << 4)));
    delay400ns(slot->ctl);
}

static int select_idle_drive(const struct ide_slot *slot)
{
    if (!poll_channel_idle(slot)) {
        quarantine_channel(slot);
        return 0;
    }
    select_drive(slot);
    return 1;
}

/* IDENTIFY one slot into words[256]. Returns 1 when a working ATA disk
   answers, 0 when the slot is empty or non-ATA. An ERR abort here means
   "no ATA disk" (SeaBIOS practice: empty QEMU slots abort IDENTIFY), never
   a fatal error: fatal I/O failures surface in real transfers instead.
   ATAPI signatures are skipped the same way (no ATAPI support claimed). */
static int identify(const struct ide_slot *slot, cpu_u16 *words)
{
    if (channel_blocked(slot)) return 0;
    if (!select_idle_drive(slot)) return 0;
    cpu_u8 status = blk_in8(slot->cmd + IDE_REG_STATUS);
    if (status == 0x00u || status == 0xffu) return 0;
    if (!poll_ready(slot->cmd + IDE_REG_STATUS)) {
        quarantine_channel(slot);
        return 0;
    }
    blk_out8(slot->cmd + IDE_REG_COUNT, 0);
    blk_out8(slot->cmd + IDE_REG_LBA0, 0);
    blk_out8(slot->cmd + IDE_REG_LBA1, 0);
    blk_out8(slot->cmd + IDE_REG_LBA2, 0);
    blk_out8(slot->cmd + IDE_REG_STATUS, IDE_CMD_IDENTIFY);
    if (!poll_clear_bsy(slot->cmd + IDE_REG_STATUS)) {
        quarantine_channel(slot);
        return 0;
    }
    /* ATAPI devices share this path: reject the 0x14/0xEB LBA1/LBA2
       signature explicitly instead of trusting abort behavior alone,
       so a phantom IDENTIFY can never fabricate geometry. */
    if (blk_in8(slot->cmd + IDE_REG_LBA1) == 0x14u &&
        blk_in8(slot->cmd + IDE_REG_LBA2) == 0xebu)
        return 0;
    status = blk_in8(slot->cmd + IDE_REG_STATUS);
    /* Any abort (including ATAPI signatures, which share this path) means
       no usable ATA disk here. */
    if (status & IDE_SR_ERR) {
        if (status & IDE_SR_DRQ) quarantine_channel(slot);
        return 0;
    }
    if (!(status & IDE_SR_DRQ)) return 0;
    blk_insw(slot->cmd, words, 256);
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
    if (channel_blocked(slot)) return BLK_TIMEOUT;
    if (!select_idle_drive(slot)) return BLK_TIMEOUT;
    if (!poll_ready(slot->cmd + IDE_REG_STATUS)) {
        quarantine_channel(slot);
        return BLK_TIMEOUT;
    }
    blk_out8(slot->cmd + IDE_REG_COUNT, 1);
    blk_out8(slot->cmd + IDE_REG_LBA0, (cpu_u8)lba);
    blk_out8(slot->cmd + IDE_REG_LBA1, (cpu_u8)(lba >> 8));
    blk_out8(slot->cmd + IDE_REG_LBA2, (cpu_u8)(lba >> 16));
    blk_out8(slot->cmd + IDE_REG_DRIVE,
            (cpu_u8)(0xe0u | (slot->slave << 4) | ((lba >> 24) & 0x0fu)));
    blk_out8(slot->cmd + IDE_REG_STATUS, dir ? IDE_CMD_WRITE : IDE_CMD_READ);
    if (!poll_clear_bsy(slot->cmd + IDE_REG_STATUS)) {
        quarantine_channel(slot);
        return BLK_TIMEOUT;
    }
    int drq = poll_set_drq(slot);
    if (drq < 0) return BLK_IOERR;
    if (!drq) {
        quarantine_channel(slot);
        return BLK_TIMEOUT;
    }
    if (dir) blk_outsw(slot->cmd, buf, 256);
    else blk_insw(slot->cmd, buf, 256);
    /* Post-transfer status: cached writes complete here; errors surface now. */
    if (!poll_clear_bsy(slot->cmd + IDE_REG_STATUS)) {
        quarantine_channel(slot);
        return BLK_TIMEOUT;
    }
    cpu_u8 end = blk_in8(slot->cmd + IDE_REG_STATUS);
    if (end & IDE_SR_DRQ) quarantine_channel(slot);
    if (end & (IDE_SR_ERR | IDE_SR_DF)) return BLK_IOERR;
    if (end & IDE_SR_DRQ) return BLK_IOERR;
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
        devices[i].writable = 0;
        devices[i].block_count = 0;
        cpu_u16 words[256];
        int present = identify(&SLOTS[i], words);
        if (!present) continue;
        cpu_u64 count = 0;
        int ok = check_geometry(words, &count);
        if (ok == BLK_NODEV) continue;
        /* A present-but-unusable secondary must not veto the boot disk:
           leave it unmarked and keep probing (a wholly bad set still
           fails below with found == 0). */
        if (ok) { blk_stage = "geometry"; continue; }
        /* Sector-0 probe: both IDENTIFY and a readable first sector are
           required before any address on this device is trusted. */
        static cpu_u16 sector0[256];
        int rd = one_sector(&SLOTS[i], 0, sector0, 0);
        if (rd) { blk_stage = "sector0"; continue; }
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

int blk_set_writable(cpu_u32 id)
{
    if (id >= BLK_MAX_DEVICES || !devices[id].present) return BLK_NODEV;
    devices[id].writable = 1;
    return BLK_OK;
}

/* Revoke write authorization (fs_unmount calls this so a torn-down
   filesystem leaves no writable device behind). Idempotent. */
int blk_clear_writable(cpu_u32 id)
{
    if (id >= BLK_MAX_DEVICES || !devices[id].present) return BLK_NODEV;
    devices[id].writable = 0;
    return BLK_OK;
}

static int transfer(cpu_u32 id, cpu_u64 start, cpu_u32 count, void *buf, cpu_u64 len, int dir, int need_write)
{
    if (id >= BLK_MAX_DEVICES || !devices[id].present) return BLK_NODEV;
    if (!buf || ((cpu_u64)buf & 1u)) return BLK_INVALID;
    struct blk_state snapshot = devices[id];
    int range = valid_range(&snapshot, start, count, len);
    if (range) return range;
    if (snapshot.block_count > BLK_LBA28_MAX) return BLK_RANGE;
    if (need_write && !snapshot.writable) return BLK_DENIED;
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

#if defined(RYNOR_BLK_SCRIPT_TEST) && RYNOR_BLK_SCRIPT_TEST
int blk_scripted_timeout_test(void)
{
    struct blk_state saved_devices[BLK_MAX_DEVICES];
    struct blk_script_io saved_script = blk_script;
    cpu_u8 saved_quarantine[IDE_CHANNELS];
    _Alignas(2) cpu_u16 buffer[BLK_SECTOR_SIZE / sizeof(cpu_u16)];
    cpu_u16 identify_words[256];
    cpu_u32 writes_before;
    cpu_u32 reads_before;
    cpu_u32 port_writes_before;
    cpu_u8 late_idle;
    int first_rc;
    int mate_rc;
    int other_rc;
    int ready_rc;
    int identify_rc;
    int order_rc;
    int failures = 0;
    static const cpu_u8 identify_order[] = {
        1u, 2u, 3u, 0x11u, 0x23u, 0x23u, 0x31u, 0x43u, 0x42u,
    };
    static const cpu_u8 transfer_order[] = {
        1u, 2u, 3u, 0x11u, 0x23u, 0x32u, 0x43u, 0x42u, 0x43u, 0x43u,
    };

    for (cpu_u32 i = 0; i < BLK_MAX_DEVICES; ++i) saved_devices[i] = devices[i];
    for (cpu_u32 i = 0; i < IDE_CHANNELS; ++i) {
        saved_quarantine[i] = channel_quarantined[i];
        channel_quarantined[i] = 0;
    }
    for (cpu_u32 i = 0; i < BLK_MAX_DEVICES; ++i) {
        devices[i].present = i < 3u;
        devices[i].test_device = 0;
        devices[i].writable = i == 0u;
        devices[i].block_count = 16u;
    }
    blk_script.active = 1;
    blk_script.primary_late = 0;
    blk_script.secondary_drq_stuck = 0;
    blk_script.preselect_phase = 0;
    blk_script.ordering_test = 0;
    blk_script.order_select_armed = 0;
    blk_script.order_trace_count = 0;
    blk_script.status_reads[0] = 0;
    blk_script.status_reads[1] = 0;
    blk_script.last_status[0] = 0;
    blk_script.last_status[1] = 0;
    blk_script.port_reads = 0;
    blk_script.port_writes = 0;
    blk_script.taskfile_writes = 0;
    blk_script.command_writes[0] = 0;
    blk_script.command_writes[1] = 0;
    blk_script.command_while_drq[0] = 0;
    blk_script.command_while_drq[1] = 0;
    blk_script.pio_words[0] = 0;
    blk_script.pio_words[1] = 0;

    /* Primary-master PIO WRITE transfers its data, then stays BSY through
       the bounded post-data completion wait. This matches QEMU's asynchronous
       backing write: the completion may arrive after this call times out. */
    first_rc = blk_write(0, 0, 1, buffer, sizeof buffer);
    if (first_rc != BLK_TIMEOUT || !channel_quarantined[0] ||
        blk_script.command_writes[0] != 1u || blk_script.pio_words[0] != 256u)
        failures |= 1u;

    /* The backing write completes late and the device becomes ready. The
       shared channel still requires reset/revalidation, so its master/slave
       mate must touch no taskfile or status register. */
    blk_script.primary_late = 1;
    late_idle = blk_in8(IDE0_CMD + IDE_REG_STATUS);
    if (late_idle != IDE_SR_DRDY) failures |= 1u;
    writes_before = blk_script.taskfile_writes;
    reads_before = blk_script.port_reads;
    port_writes_before = blk_script.port_writes;
    mate_rc = blk_read(1, 0, 1, buffer, sizeof buffer);
    if (mate_rc != BLK_TIMEOUT || blk_script.taskfile_writes != writes_before ||
        blk_script.port_reads != reads_before ||
        blk_script.port_writes != port_writes_before ||
        blk_script.command_writes[0] != 1u)
        failures |= 1u;

    /* The independent channel first completes a normal read successfully. */
    other_rc = blk_read(2, 0, 1, buffer, sizeof buffer);
    if (other_rc != BLK_OK || channel_quarantined[1] ||
        blk_script.command_writes[1] != 1u)
        failures |= 4u;

    /* Then inject a persistent pre-command DRQ phase. Readiness must refuse
       the command rather than accepting DRDY|DRQ as idle. */
    blk_script.secondary_drq_stuck = 1;
    ready_rc = blk_read(2, 0, 1, buffer, sizeof buffer);
    if (ready_rc != BLK_TIMEOUT || blk_script.command_writes[1] != 1u ||
        blk_script.command_while_drq[1] != 0u)
        failures |= 2u;

    /* Prove both IDENTIFY and PIO wait through BSY -> DRQ -> idle on the
       previously selected master before selecting the slave. Each trace also
       requires target DRDY observations before its command is issued. */
    channel_quarantined[0] = 0;
    blk_script.ordering_test = 1;
    blk_script.order_trace_count = 0;
    blk_script.order_preselect_reads[0] = 0;
    blk_script.order_command_reads[0] = 0;
    blk_script.order_command[0] = 0;
    blk_script.order_select_armed = 0;
    identify_rc = identify(&SLOTS[1], identify_words);
    if (identify_rc != 1 ||
        !blk_order_trace_is(identify_order,
                            (cpu_u32)(sizeof identify_order / sizeof identify_order[0])))
        failures |= 8u;

    blk_script.order_trace_count = 0;
    blk_script.order_preselect_reads[0] = 0;
    blk_script.order_command_reads[0] = 0;
    blk_script.order_command[0] = 0;
    blk_script.order_select_armed = 0;
    order_rc = one_sector(&SLOTS[1], 0, buffer, 0);
    if (order_rc != BLK_OK ||
        !blk_order_trace_is(transfer_order,
                            (cpu_u32)(sizeof transfer_order / sizeof transfer_order[0])))
        failures |= 8u;

    blk_script = saved_script;
    for (cpu_u32 i = 0; i < BLK_MAX_DEVICES; ++i) devices[i] = saved_devices[i];
    for (cpu_u32 i = 0; i < IDE_CHANNELS; ++i)
        channel_quarantined[i] = saved_quarantine[i];
    return failures;
}
#endif

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
