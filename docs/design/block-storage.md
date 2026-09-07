# Block storage design (Stage 17a)

Status: **implemented for Stage 17a (IDE PIO, host QEMU only)**. This
document specifies the controller choice, the block-device contract,
discovery, limits, transfer, timeout, error, and test model. No filesystem:
directories, files, paths, inodes, mounts, journaling, permissions, and
allocation are explicitly future (17b/17c). See `ROADMAP.md` Stage 17a and
`docs/reports/stage17a.md`.

## 1. Controller selection: IDE PIO, LBA28, polling

The first controller is the PIIX3 IDE compatibility interface
(`pc-i440fx` QEMU machine), programmed I/O, 28-bit LBA, status polling.
Rationale, decided against virtio-blk:

- The boot disk is already attached as `if=ide`; SeaBIOS boots from it,
  which proves the controller path works before our driver runs. No QEMU
  configuration change is needed for the device itself.
- PIO needs no DMA: no physical-address translation, no buffer lifetime
  hazards, no IOMMU claims. The whole DMA/IOMMU problem class is absent
  by construction at this stage (not merely handled).
- LBA28 covers 137 GiB; test images are MiBs. 48-bit LBA is deferred.
- Polling needs no interrupts: no completion identity, no spurious/
  duplicate/late completion states can exist (one outstanding command,
  observed directly). IRQs stay as the firmware left them.
- virtio-blk would require descriptor chains, avail/used rings, and DMA
  addressing for the same test coverage: more code, more bug surface, no
  filesystem-visible benefit at 17a.

What this is not: a universal storage driver. No AHCI/NVMe/USB, no ATAPI,
no CHS, no >512-byte sectors, no LBA48, no DMA, no IRQs, no multisector
auto-DMA. Each refusal is an explicit error, never silent translation.

## 2. Block-device API (`kernel/include/blk.h`)

```c
blk_discover(void) -> device count or negative blk_result
blk_count(void)
blk_device(id) -> descriptor or NULL
blk_find_test(void) -> test-device id or negative
blk_read(id, start, count, buf, len)
blk_write(id, start, count, buf, len)
blk_error_str(code)
```

Ownership and rules:

- Callers own all buffers; the driver never allocates (two static sector
  buffers exist inside the self-test only, never in the driver).
- `buf` non-null, 2-byte aligned (word string ops), `len` exactly
  `count * 512`; `1 <= count <= 32` (16 KiB per call).
- Single-sector commands internally (`count` byte = 1); a multi-block call
  completes prior blocks before any error aborts — no cross-block
  atomicity is claimed.
- Reads accept any present device; **writes require an explicitly
  authorized device** (`blk_set_writable`, `BLK_DENIED` otherwise), so no
  code path can corrupt the boot disk. The block self-test authorizes the
  RLBLK1 test device; `fs_mount` authorizes a device only after full
  filesystem validation (Stage 17c). Nothing else can enable writes.
- Descriptors point at shared driver state: copy fields out, do not hold
  them across `blk_discover`.
- No filesystem types cross this API: `id`, `block_size`, `block_count`
  only. No ports, registers, or controller structs leak (verified by
  inspection: `blk.h` includes only `cpu.h`).

Error set (all negative except `BLK_OK = 0`, names via `blk_error_str`):
`INVALID` (bad id/count/len/buffer), `RANGE` (outside capacity),
`UNSUPPORTED` (non-LBA or >512-byte layout), `NODEV` (absent/unknown),
`INIT_FAIL` (no working device / bad controller), `IOERR` (ERR/DF bits),
`TIMEOUT` (status wait exhausted), `DENIED` (unauthorized write).

## 3. Discovery and provenance

`blk_discover` (requires IF=0, not in IRQ context, like the display
provenance path):

1. PCI config read at 00:01.1 must be VID/DID `8086:7010`, class/subclass
   `01/01`, and both channels in compatibility mode. Anything else fails
   closed (`BLK_INIT_FAIL`): fixed ports are used only on this match.
   (00:01.1 is the PIIX3 IDE function on pc-i440fx; 00:01.0 is the ISA
   bridge.)
2. Per slot (primary/secondary master/slave): select, check status
   `0x00`/`0xFF` for absence (instant, no wait), else IDENTIFY with
   bounded BSY/DRQ waits.
3. Geometry: LBA bit (word 49.9) mandatory; 28-bit count from words 60–61
   must be nonzero; word-106 validation respected (>512-byte logical
   sectors refused); capacity capped at 2^28 (never wrapped).
4. Sector-0 probe must read cleanly; the RLBLK1 header (magic 8 bytes,
   `u32le` 512, `u64le` count) must cross-match IDENTIFY geometry, or the
   device is present but not the test device.

Zero present devices is `BLK_INIT_FAIL` (our QEMU always has the boot
disk); a present-but-unmarked disk is simply never selected for writes.
A present device that fails geometry or the sector-0 probe is likewise
skipped without vetoing the remaining devices (only a wholly bad set
fails discovery).

## 4. Limits and arithmetic

- `block_size` is `u32`, `block_count` and `start` are `u64`, `count` is
  `u32`: `count * 512` cannot overflow; range checks run as
  `start >= count_total -> RANGE` then `count > total - start -> RANGE`,
  so no addition wraps before its guard. LBA28 fit is enforced by the
  discovery cap, re-checked per transfer.
- Request cap 32 blocks; sector count byte is always exactly 1.

## 5. Transfer, timeouts, interrupts, DMA

- One command at a time; ready gate (BSY-clear and DRDY-set) before issue,
  BSY-clear then DRQ/ERR poll, each bounded by
  `BLK_POLL_LIMIT` (4M iterations); post-transfer status re-checked
  (cached writes complete here). No HLT wait loops; IRQ0 keeps ticking.
- No interrupts consumed or required; no DMA engine exists in this path
  (CPU `rep insw/outsw` on mapped kernel buffers only).
- Buffer lifetime is the call: completion is observed before return, so
  no use-after-free or stale-completion path exists by construction.

## 6. Test model

- Synthetic (every boot, silent): rejected-argument matrix through the
  real entry points (bad id, zero/huge count, null/odd/short/long
  buffers, len mismatch, at-end/past-end/max-start/wrap, boot-write
  denial, descriptor range) — all fail before hardware I/O.
- Device evidence (test image attached): discovery line, fixed block
  reads with byte-sums, boot-sector `AA55` cross-device read, patterned
  write/readback with neighbor integrity, `storage verified` marker.
- Host images (`tools/host/blk_image.py`): deterministic patterned
  images (block N formula), zeroed images (must be ignored), corrupt
  images (must be ignored); `snapshot=on` overlays keep host files
  pristine across reruns.
- Host validator (`tools/host/blk_output.py`): recomputes every guest
  number from the image file; written-block sums from the shared
  deterministic formula.
- QEMU: `boot_image(..., extra_drives=...)` appends raw `if=ide` disks;
  the boot disk stays primary master. Matrix: 1 MiB + 8 MiB patterned,
  zeroed, corrupt-magic, and missing-drive boots.
- Mutations (integration): range-check inversion, completion-error
  inversion (both halt with named reasons), LBA+1 and fake capacity
  (caught by host digest/capacity comparison).

## 7. Handoff to 17b and honesty bounds

Filesystem code will use `blk_read/blk_write/capacity/block_size`
without knowing IDE exists. Verified on QEMU `pc-i440fx` IDE only;
arbitrary physical hardware is explicitly not verified. No durability,
crash-consistency, journaling, or atomic multi-block claims.
