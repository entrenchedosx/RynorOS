# RYNORFS v1: native filesystem (Stages 17b read, 17c overwrite)

Status: **implemented for Stage 17b (read) and Stage 17c
(overwrite-in-extent writes)**. This document freezes
the on-disk format, path rules, read/write API, error model, and validation
discipline. No allocation, no heap, no userspace. See `block-storage.md`
(device layer), `ROADMAP.md` Stages 17b/17c, and `docs/reports/stage17b.md`
/ `stage17c.md`.

## 1. Format overview

Little-endian throughout; fixed-width integers; explicit offsets. Block
size is 512 (superblock must say 512 or the image is unsupported, never
silently translated).

```
block 0:             superblock (magic, version, geometry)
block 1..D:          directory extents (64-byte entries, 8 per block)
block D+1..:         file data extents (contiguous per file)
```

## 2. Superblock (block 0)

| Bytes | Field | Rule |
|---|---|---|
| 0..8 | magic `"RYNORFS\0"` | mismatch means "not a filesystem" (`FS_INVALID`), never `CORRUPT` |
| 8..12 | version `u32le` | must be 1, else `FS_UNSUPPORTED` |
| 12..16 | block_size `u32le` | must be 512, else `FS_UNSUPPORTED` |
| 16..24 | total_blocks `u64le` | nonzero, within the device |
| 24..32 | dir_start `u64le` | nonzero; `dir_start + dir_blocks` inside total |
| 32..40 | dir_blocks `u64le` | 1..64, inside total |
| 40..48 | data_start `u64le` | nonzero, inside total |
| 48..56 | data_blocks `u64le` | nonzero, inside total |
| 56..64 | reserved `u64le` | must be zero |
| 64..512 | padding | must be zero |

Superblock, directory, and data extents must be pairwise disjoint;
`{0}`, dir, and data ranges are checked with overflow-safe ordering
(`start > total - count` style, never `start + count` first).

## 3. Directory entries (64 bytes, 8 per block)

| Bytes | Field | Rule |
|---|---|---|
| 0..32 | name | 1..31 printable ASCII (`0x20..0x7E`), NUL-terminated, zero-padded |
| 32 | type | 0 = free (whole record must be zero), 1 = file, 2 = dir |
| 33..40 | reserved | must be zero |
| 40..48 | first_block `u64le` | file extent start (0 iff zero-length) |
| 48..56 | block_count `u64le` | extent length (0 iff zero-length) |
| 56..64 | byte_length `u64le` | file bytes (0 for dirs and empty files) |

Rules: unknown types rejected; duplicates rejected (first match must
never lie); zero-length files are canonical `(0,0,0)`; nonzero files need
`0 < byte_length <= block_count * 512` (overflow-guarded) with the extent
inside the data region (`first >= data_start`, `count <= data_end -
first` — never `data_blocks - (first - data_start)`, which wraps);
directories carry no data fields; file extents pairwise disjoint (shared
data illegal in v1).

## 4. Paths and lookup

Paths are at most 32 bytes (`/` + up to 31 name bytes): exactly one
leading `/`, no `//`, no trailing `/` (except root), printable ASCII
only, no `.`/`..` components (there is no current/parent directory).
Storage is flat; hierarchy is a resolution rule: every proper prefix of
a nested name must exist as a directory entry (checked at mount, else
`FS_CORRUPT`), and lookup walks components requiring directories for
non-final components (`FS_NOTDIR` when traversing through a file).
Comparison is exact bytes (case-sensitive); no normalization of any
kind. Root `/` is an implicit directory (stat-able, never openable).

## 5. Read API (`kernel/include/fs.h`)

`fs_mount(dev)` validates everything above (magic through overlap) into
a static 32 KiB directory copy (at most 64 blocks, read in `blk`-sized
chunks); re-mount resets state and invalidates handles. `fs_open`
(files only; directories report `FS_NOTFILE`), `fs_stat` (files and
directories), `fs_read(handle, offset, buf, len, &nread)` (short at EOF,
zero exactly at EOF, past-end is `FS_RANGE`, over-cap length is
`FS_INVALID`), `fs_close` (double close is `FS_BADHANDLE`).

Handles are 8 table slots with monotonic generations (`slot | gen << 3`):
close, remount, and unmount all invalidate; stale integers can never
alias a recycled object. Partial edge blocks stage through one static
512-byte scratch; aligned middles transfer straight into the caller
buffer (which must be 2-byte aligned, like `blk`). Per-read cap is
16384 bytes. Block errors translate to `FS_IOERR` with a stage detail
string (`fs_stage_detail`), mirroring the `blk` diagnostic pattern.

## 6. What is NOT in v1

Writes beyond overwrite-in-extent, recovery, journaling, permissions,
modification times, symlinks, fragments, multi-extent files, directories
beyond typed markers, readdir enumeration, memory-mapped files. The
format reserves nothing except the superblock `reserved` word and entry
padding (both must be zero), so v2 can extend without breaking v1
rejection of unknown shapes.

## 7. Stage 17c writes (overwrite-in-extent)

`fs_write(handle, offset, buf, len, &nwritten)`: the range must lie
entirely within the file (`offset + len > size` is `FS_RANGE`, never a
silent clamp or an extension); `len` beyond 16384 bytes is `FS_INVALID`;
buffers follow the `blk` even-address rule. Lengths and extents never
change, so open handles stay valid across writes (remount still
invalidates everything, as before).

Partial writes are reported, not hidden: `*nwritten` counts completed
bytes and the error code names the failure. The atomic unit is one
512-byte sector (single PIO command); a multi-block overwrite that fails
mid-way leaves earlier sectors new and later sectors old. Metadata is
never modified through `fs_write`, so the filesystem is always
mountable after filesystem-level writes: torn data bytes are possible,
torn metadata is impossible through this path. (Scope note: raw
`blk_write` to a mounted device is a separate, kernel-only path outside
the filesystem contract — all in-tree callers are reviewed, and
`fs_unmount` revokes device writability. A future multi-client design
must scope authorization per call.)

Failure injection for tests lives behind `RYNOR_TEST_ARMED` only
(`fs_inject_fault_at` fails the Nth block transfer once); unarmed
builds contain no hook. No locking is added: single-threaded caller,
polling works under any IF, and future 18b callers must serialize.
