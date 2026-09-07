#ifndef RYNOR_BLK_H
#define RYNOR_BLK_H
#include "cpu.h"

/* Stage 17a block storage: a minimal controller-independent block-device
   API over discovered IDE (PIIX3, PIO, LBA28) devices. No filesystem, no
   directories, no files, no mounts, no journaling: raw addressed blocks
   only. Single outstanding request (polling, no interrupts, no DMA), so
   request identity is structural, never tracked. See
   docs/design/block-storage.md. */

#define BLK_SECTOR_SIZE 512u   /* only sector size discovery accepts */
#define BLK_MAX_BLOCKS 32u     /* max blocks per read/write call (16 KiB) */
#define BLK_MAX_DEVICES 4u     /* primary/secondary x master/slave */
#define BLK_POLL_LIMIT 4000000u /* status-poll iterations before TIMEOUT */

enum blk_result {
    BLK_OK = 0,
    BLK_INVALID = -1,     /* null buffer, misaligned buffer, bad id/count/len */
    BLK_RANGE = -2,       /* start/count outside the device capacity */
    BLK_UNSUPPORTED = -3, /* non-512 sector layout or beyond LBA28 */
    BLK_NODEV = -4,       /* no usable device (or no test device selected) */
    BLK_INIT_FAIL = -5,   /* discovery found no working device at all */
    BLK_IOERR = -6,       /* controller reported an error */
    BLK_TIMEOUT = -7,     /* status wait exhausted BLK_POLL_LIMIT */
    BLK_DENIED = -8,      /* 17a write policy: test device only */
};

/* Controller-independent device description for filesystem handoff (17b).
   No register files, ports, or controller structs leak through here. */
struct blk_device {
    cpu_u32 id;            /* 0..BLK_MAX_DEVICES-1 discovery slot */
    cpu_u32 block_size;    /* always BLK_SECTOR_SIZE for 17a devices */
    cpu_u64 block_count;   /* addressable 512-byte blocks (<= 2^28) */
    int test_device;       /* RLBLK1 magic + geometry cross-check passed */
    int present;           /* IDENTIFY + sector-0 probe both succeeded */
};

/* Discover IDE devices (PCI provenance, IDENTIFY, sector-0 probe).
   Returns the number of present devices, or a negative blk_result.
   Safe to call on every boot: silent unless a test device is found or
   discovery itself fails. */
int blk_discover(void);
/* Number of present devices after blk_discover (0 if never run). */
cpu_u32 blk_count(void);
/* Device descriptor by discovery id, or NULL for a bad id/absent device.
   Points at shared driver state: copy what you need, do not hold it
   across blk_discover. */
const struct blk_device *blk_device(cpu_u32 id);
/* Id of the RLBLK1 test device, or a negative blk_result (none found). */
int blk_find_test(void);
/* Read/write count blocks at start into/out of buf (len must equal
   count*512 exactly; buf must be non-null and 2-byte aligned).
   Single-sector commands internally; prior blocks of a multi-block call
   complete before any error aborts (no cross-block atomicity claimed).
   Writes are block-level readback primitives only: no durability, no
   crash consistency, no journaling. Writes require an authorized device
   (see blk_set_writable): the block self-test authorizes the RLBLK1 test
   device, and fs_mount authorizes validated filesystems. Nothing else
   can be written, so the boot disk is never at risk. */
int blk_read(cpu_u32 id, cpu_u64 start, cpu_u32 count, void *buf, cpu_u64 len);
int blk_write(cpu_u32 id, cpu_u64 start, cpu_u32 count, void *buf, cpu_u64 len);
/* Authorize writes on a present device (idempotent). Used by test code
   for the RLBLK1 device and by fs_mount after full validation. Returns
   BLK_OK or BLK_NODEV. */
int blk_set_writable(cpu_u32 id);
/* Revoke write authorization (idempotent). fs_unmount calls this so a
   torn-down filesystem leaves no writable device behind. Returns
   BLK_OK or BLK_NODEV. */
int blk_clear_writable(cpu_u32 id);
/* Static lowercase name for a blk_result code (serial diagnostics). */
const char *blk_error_str(int code);
/* Failing discovery stage after a negative blk_discover ("none" on success). */
const char *blk_stage_detail(void);
/* Boot self-test: silent bounds checks every boot; device evidence only
   with a test device attached. Halts with [BLK] failure= on violation. */
void blk_self_test(void);
#endif
