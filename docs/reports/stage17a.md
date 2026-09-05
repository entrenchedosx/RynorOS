# Stage 17a report -- block storage + controller discovery

## Outcome

Stage 17a is a kernel-side IDE PIO block driver (PIIX3 compatibility mode,
LBA28, polled, no DMA, no interrupts) with PCI provenance, geometry
validation, per-request bounds, a controller-independent `BlockDevice` API,
deterministic patterned test images, host-recomputed digest evidence, and
write/readback proofs. No filesystem is implemented or claimed.

## Implementation

- `kernel/include/blk.h`: `BLK_SECTOR_SIZE 512`, `BLK_MAX_BLOCKS 32`,
  `BLK_MAX_DEVICES 4`, `BLK_POLL_LIMIT 4000000`; 8-code error set
  (`OK/INVALID/RANGE/UNSUPPORTED/NODEV/INIT_FAIL/IOERR/TIMEOUT/DENIED`);
  descriptor carries `id/block_size/block_count/test_device` only.
- `kernel/storage/blk.c`: PCI 00:07.1 VID/DID + class + compat-mode check
  (IF=0, not in IRQ context); per-slot IDENTIFY with instant absent
  detection (`0x00`/`0xFF`) and bounded BSY/DRQ waits; LBA-bit, nonzero
  count, word-106 sector checks; 2^28 cap (never wrap); sector-0 probe +
  RLBLK1 header cross-check; single-sector LBA28 commands with post-transfer
  status; all bounds before hardware; writes test-device-only (`BLK_DENIED`
  otherwise, pre-hardware).
- `kernel/storage/blk-test.c`: silent rejected-argument matrix every boot
  (19 cases, zero hardware I/O on failure paths); discovery/boot-sector/
  writeback/neighbor evidence only with a test image attached.
- `kernel/shell/shell-internal.h` pattern reused implicitly: no new shared
  headers beyond `blk.h` (driver state stays in `blk.c`).
- `tools/host/blk_image.py`: deterministic patterned/zeroed image tool
  (block N formula, header magic + geometry).
- `tools/host/blk_output.py`: serial parser + file-recomputed validator.
- `tools/host/qemu.py`: `extra_drives` appends snapshot-overlay raw IDE
  disks (boot disk stays primary master).

## Controller decision

IDE PIO, not virtio-blk: the boot disk is already `if=ide` (SeaBIOS proves
the path), PIO removes the DMA/IOMMU hazard class entirely, LBA28 covers
test scope, and polling removes async completion identity. Full rationale
in `docs/design/block-storage.md`.

## Fixtures and tests

No checked-in binaries: images are generated deterministically at test
time (1 MiB + 8 MiB patterned, zeroed, corrupt-magic).

`tests/repository/test_blk_output.py` (12 tests): tool determinism,
header layout, pattern uniqueness, zeroed handling, OOB read, CLI codes
without tracebacks, writeback-tag separation, serial parse fields,
genuine-evidence acceptance, tamper/missing-line/failure-image/wrong-image
rejection, error-code distinctness.

`tests/integration/test_storage.py` (9 tests): 1 MiB + 8 MiB full evidence
(host-recomputed), zeroed/corrupt/missing-drive negative boots, and
mutations (range inversion and completion-error inversion halt with named
reasons; LBA+1 and fake capacity caught by digest/capacity comparison).

## Verification

```text
python tools/build/build.py build
python tools/build/build.py test      # 490/490 repository
python tools/build/build.py integration-test  # 172/172 integration
python tools/build/build.py validate
python tools/build/build.py check
```

Reference-host result: full `check` green (build + 490 repository +
172 integration). Inventory sums equal collected counts. Docs counts in
`README.md`, `ARCHITECTURE.md`, and `docs/design/shell.md` match.

## Forensic notes

- New serial lines appear only with a test image attached (or on
  failure); normal-boot transcripts are byte-identical, so no existing
  exact-transcript validator needed changes (proven by the unchanged
  shell/display/integration suites).
- `snapshot=on` overlays keep host images pristine: reruns deterministic,
  writeback verified in-guest with host recomputing the written pattern.

## Limitations

- QEMU `pc-i440fx` PIIX3 IDE only; no AHCI/NVMe/USB/ATAPI/CHS/LBA48/DMA/IRQs.
- Timeout path is bounded-wait (4M polls); forced-timeout staging needs
  fault-injecting hardware and is deferred with rationale (every real
  wait is exercised; the bound exceeds observed polls by orders).
- Block-level readback only: no durability, crash consistency, journaling,
  or atomic multi-block claims. 17b/17c own the filesystem.
