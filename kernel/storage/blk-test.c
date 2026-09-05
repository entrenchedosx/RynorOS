/* Stage 17a block-storage self-test. Validation unit checks run on every
   boot through real API entry points with rejected arguments (no hardware
   I/O on any failure path: every check precedes the sector loop). Real
   device evidence runs only when the RLBLK1 test device is attached;
   normal boots stay silent on success so existing exact transcripts are
   unaffected. Any require() failure halts with [BLK] failure=. */
#include "blk.h"
#include "serial.h"
#include "cpu.h"
#include "io.h"

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[BLK] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}

static void say(const char *s) { require(serial_write(s), "serial"); }

static void say_u64(cpu_u64 n)
{
    char b[21];
    unsigned i = 20;
    b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    say(b + i);
}

static cpu_u32 byte_sum(const cpu_u8 *b, cpu_u64 n)
{
    cpu_u32 s = 0;
    for (cpu_u64 i = 0; i < n; ++i) s += b[i];
    return s;
}

/* Position-weighted sum: plain sums are rotation-blind (every patterned
   data block sums to the same total), so a wrong-block read needs this to
   be caught. Both numbers are host-recomputed from the image file. */
static cpu_u64 byte_wsum(const cpu_u8 *b, cpu_u64 n)
{
    cpu_u64 s = 0;
    for (cpu_u64 i = 0; i < n; ++i) s += i * b[i];
    return s;
}

static cpu_u8 io_area[BLK_MAX_BLOCKS * 512u];
static cpu_u8 blk_scratch[512u];

static void bounds_tests(cpu_u32 boot_id)
{
    /* Every case must fail before touching hardware (boot_id is present,
       so INVALID/RANGE verdicts prove validation, not absence). */
    require(blk_read(BLK_MAX_DEVICES, 0, 1, io_area, 512) == BLK_NODEV, "id-range");
    require(blk_read(BLK_MAX_DEVICES + 40u, 0, 1, io_area, 512) == BLK_NODEV, "id-huge");
    require(blk_read(boot_id, 0, 0, io_area, 0) == BLK_INVALID, "count-zero");
    require(blk_read(boot_id, 0, BLK_MAX_BLOCKS + 1u, io_area,
                     ((cpu_u64)BLK_MAX_BLOCKS + 1u) * 512u) == BLK_INVALID, "count-huge");
    require(blk_read(boot_id, 0, 1, 0, 512) == BLK_INVALID, "null-buf");
    require(blk_read(boot_id, 0, 1, io_area + 1, 512) == BLK_INVALID, "odd-buf");
    require(blk_read(boot_id, 0, 1, io_area, 511) == BLK_INVALID, "short-len");
    require(blk_read(boot_id, 0, 1, io_area, 513) == BLK_INVALID, "long-len");
    require(blk_read(boot_id, 0, 2, io_area, 512) == BLK_INVALID, "len-mismatch");
    {
        const struct blk_device *dev = blk_device(boot_id);
        require(dev != 0, "boot-desc");
        require(blk_read(boot_id, dev->block_count, 1, io_area, 512) == BLK_RANGE, "at-end");
        require(blk_read(boot_id, dev->block_count - 1u, 2, io_area, 1024) == BLK_RANGE,
                "past-end");
        require(blk_read(boot_id, (cpu_u64)-1, 1, io_area, 512) == BLK_RANGE, "start-max");
        require(blk_read(boot_id, (cpu_u64)-512, 2, io_area, 1024) == BLK_RANGE,
                "start-wrap");
        /* Boot disk is never writable in 17a: policy denial, no I/O. */
        require(blk_write(boot_id, 0, 1, io_area, 512) == BLK_DENIED, "boot-readonly");
    }
    require(blk_device(BLK_MAX_DEVICES) == 0, "desc-range");
}

static void read_evidence(cpu_u32 id, cpu_u64 blk)
{
    require(blk_read(id, blk, 1, blk_scratch, sizeof blk_scratch) == BLK_OK, "evict-read");
    say("[BLK] read blk=");
    say_u64(blk);
    say(" sum=");
    say_u64(byte_sum(blk_scratch, sizeof blk_scratch));
    say(" wsum=");
    say_u64(byte_wsum(blk_scratch, sizeof blk_scratch));
    say("\r\n");
}

void blk_self_test(void)
{
    require(cpu_interrupts_disabled(), "if0");
    int found = blk_discover();
    if (found <= 0) {
        say("[BLK] failure=discovery detail=");
        say(blk_stage_detail());
        say(" code=");
        say(found < 0 ? blk_error_str(found) : "nodisk");
        say("\r\n");
        require(0, "discovery");
    }
    /* Boot disk is discovery slot 0 in our QEMU (primary master). The
       bounds below only assume >=1 present device, never a fixed slot. */
    cpu_u32 boot_id = 0;
    for (cpu_u32 i = 0; i < BLK_MAX_DEVICES; ++i)
        if (blk_device(i)) { boot_id = i; break; }
    bounds_tests(boot_id);
    int test_id = blk_find_test();
    if (test_id < 0) return; /* normal boot: silent success */
    const struct blk_device *dev = blk_device((cpu_u32)test_id);
    require(dev != 0 && dev->test_device, "test-desc");
    say("[BLK] devices=");
    say_u64(blk_count());
    say(" test=");
    say_u64((cpu_u64)test_id);
    say(" blocks=");
    say_u64(dev->block_count);
    say("\r\n");
    /* Fixed evidence set: first blocks plus the last one. */
    read_evidence((cpu_u32)test_id, 0);
    read_evidence((cpu_u32)test_id, 1);
    read_evidence((cpu_u32)test_id, 2);
    read_evidence((cpu_u32)test_id, dev->block_count - 1u);
    /* Boot-disk sector 0 proves reads on a second, unselected device. */
    require(blk_read(boot_id, 0, 1, blk_scratch, sizeof blk_scratch) == BLK_OK, "bootsec");
    require(blk_scratch[510] == 0x55 && blk_scratch[511] == 0xaa, "boot-sig");
    say("[BLK] bootsec aa55=1\r\n");
    /* Write/readback on a mid-disk block plus neighbor integrity. */
    cpu_u64 wblk = dev->block_count / 2u;
    for (cpu_u64 i = 0; i < sizeof blk_scratch; ++i)
        blk_scratch[i] = (cpu_u8)((wblk * 131u + i * 17u + 0x5au) & 0xffu);
    require(blk_write((cpu_u32)test_id, wblk, 1, blk_scratch, sizeof blk_scratch) == BLK_OK,
            "wb-write");
    for (cpu_u64 i = 0; i < sizeof blk_scratch; ++i) blk_scratch[i] = 0;
    require(blk_read((cpu_u32)test_id, wblk, 1, blk_scratch, sizeof blk_scratch) == BLK_OK,
            "wb-read");
    say("[BLK] writeback blk=");
    say_u64(wblk);
    say(" sum=");
    say_u64(byte_sum(blk_scratch, sizeof blk_scratch));
    say(" wsum=");
    say_u64(byte_wsum(blk_scratch, sizeof blk_scratch));
    say("\r\n");
    /* Neighbors must still hold file patterns (no over/under-write). */
    require(blk_read((cpu_u32)test_id, wblk - 1u, 1, blk_scratch, sizeof blk_scratch) == BLK_OK,
            "nb-lo");
    say("[BLK] neighbor blk=");
    say_u64(wblk - 1u);
    say(" sum=");
    say_u64(byte_sum(blk_scratch, sizeof blk_scratch));
    say(" wsum=");
    say_u64(byte_wsum(blk_scratch, sizeof blk_scratch));
    say("\r\n");
    require(blk_read((cpu_u32)test_id, wblk + 1u, 1, blk_scratch, sizeof blk_scratch) == BLK_OK,
            "nb-hi");
    say("[BLK] neighbor blk=");
    say_u64(wblk + 1u);
    say(" sum=");
    say_u64(byte_sum(blk_scratch, sizeof blk_scratch));
    say(" wsum=");
    say_u64(byte_wsum(blk_scratch, sizeof blk_scratch));
    say("\r\n");
    /* Multi-block path: one 4-block call must equal four single reads.
       Silent (no new serial format): byte-compare in guest. */
    static cpu_u8 multi[4u * 512u];
    static cpu_u8 single[512u];
    require(blk_read((cpu_u32)test_id, 10, 4, multi, sizeof multi) == BLK_OK,
            "multi-read");
    for (cpu_u64 b = 0; b < 4u; ++b) {
        require(blk_read((cpu_u32)test_id, 10 + b, 1, single, sizeof single) == BLK_OK,
                "multi-single");
        for (cpu_u64 i = 0; i < sizeof single; ++i)
            require(multi[b * 512u + i] == single[i], "multi-match");
    }
    say("[BLK] storage verified\r\n");
}
