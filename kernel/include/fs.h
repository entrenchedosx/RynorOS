#ifndef RYNOR_FS_H
#define RYNOR_FS_H
#include "cpu.h"

/* Stage 17b read-only native filesystem (RYNORFS v1) over the Stage 17a
   BlockDevice API, plus Stage 17c overwrite-in-extent writes. Flat entry
   storage with hierarchical path resolution; contiguous file extents; no
   allocation, no heap, no interrupts touched. Single-threaded boot-time
   context (consistent with the block layer).
   See docs/design/filesystem.md. */

#define FS_MAX_PATH 32u        /* '/' + up to 31 name bytes */
#define FS_MAX_NAME 31u        /* entry name bytes, NUL-terminated in 32 */
#define FS_MAX_OPEN 8u         /* concurrent handles */
#define FS_MAX_DIR_BLOCKS 64u  /* directory extent cap (512 entries) */
#define FS_MAX_READ_BYTES 16384u /* per-read cap (32 blocks, the blk cap) */
#define FS_MAX_WRITE_BYTES 16384u /* per-write cap (overwrite within extent) */
#define FS_DIR_ENTRIES_PER_BLOCK 8u

enum fs_result {
    FS_OK = 0,
    FS_INVALID = -1,     /* malformed path, bad argument, no fs mounted,
                            not a RYNORFS image, over-cap request */
    FS_NOTFOUND = -2,    /* no such path */
    FS_NOTFILE = -3,     /* directory opened/read as a file */
    FS_NOTDIR = -4,      /* traversal through a file entry */
    FS_BADHANDLE = -5,   /* unknown, closed, or stale handle */
    FS_RANGE = -6,       /* offset past end of file */
    FS_IOERR = -7,       /* block-layer failure (translated) */
    FS_CORRUPT = -8,     /* mounted image violates the format */
    FS_UNSUPPORTED = -9, /* well-formed but beyond 17b limits */
    FS_BUSY = -10,       /* handle table full */
};

enum fs_entry_type {
    FS_TYPE_FILE = 1,
    FS_TYPE_DIR = 2,
};

struct fs_stat {
    int type;              /* FS_TYPE_FILE or FS_TYPE_DIR */
    cpu_u64 size;          /* file bytes (0 for directories) */
    cpu_u64 blocks;        /* extent blocks (0 for directories) */
};

/* Validate and mount the filesystem on a discovered block device. Reads
   block 0 + the directory extent through blk_read only. Re-mounting
   resets state and invalidates all open handles. Returns FS_OK or a
   classified error; never mounts a corrupt image. */
int fs_mount(cpu_u32 dev);
/* Release the mount; all handles become stale. Safe when unmounted. */
void fs_unmount(void);
/* Nonzero while a filesystem is mounted. */
int fs_mounted(void);
/* Mounted geometry (0 when unmounted): total blocks and data blocks. */
cpu_u64 fs_total_blocks(void);
/* Open a file for reading. Directories (including root) report
   FS_NOTFILE; traversal through a file reports FS_NOTDIR. */
int fs_open(const char *path, cpu_u32 *handle);
/* Metadata for any existing path (files and directories). */
int fs_stat(const char *path, struct fs_stat *st);
/* Read up to len bytes at offset into buf (2-byte aligned, like blk).
   Sets *nread (short at EOF, 0 exactly at EOF, never past file_size;
   on FS_IOERR *nread reports the completed prefix, mirroring fs_write;
   other errors leave *nread untouched, so callers must check rc first).
   offset past end is FS_RANGE; len beyond FS_MAX_READ_BYTES is FS_INVALID. */
int fs_read(cpu_u32 handle, cpu_u64 offset, void *buf, cpu_u64 len, cpu_u64 *nread);
/* Overwrite len bytes at offset from buf (2-byte aligned, like blk) into
   an open file. Stage 17c supports overwrite within the existing extent
   only: offset+len past file_size is FS_RANGE (no extension, no partial
   extension); len beyond FS_MAX_WRITE_BYTES is FS_INVALID. Sets *nwritten
   (== len on FS_OK); on a block-write failure earlier blocks stay written
   and *nwritten reports the completed prefix. Lengths, handles, and
   extents are unchanged by writes, so open handles stay valid. */
int fs_write(cpu_u32 handle, cpu_u64 offset, const void *buf, cpu_u64 len, cpu_u64 *nwritten);
/* Close a handle. Double close and unknown handles are FS_BADHANDLE. */
int fs_close(cpu_u32 handle);
/* Static lowercase name for an fs_result code (serial diagnostics). */
const char *fs_error_str(int code);
/* Failing mount/lookup stage after an error ("none" when clean). */
const char *fs_stage_detail(void);
/* Boot self-test: silent invalid-path/handle cases every boot; device
   evidence only with a filesystem image attached. Halts with
   [FS] failure= on violation. */
void fs_self_test(void);
#if RYNOR_TEST_ARMED
/* TEST-ONLY fault hook (absent from unarmed builds): fail the Nth
   fs_write block transfer with a simulated I/O error (n >= 1 arms a
   one-shot fault; n <= 0 disarms). Production code paths never call it. */
void fs_inject_fault_at(int n);
#endif
#endif
