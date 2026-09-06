# Stage 17c report — overwrite-in-extent filesystem writes

## Outcome

Stage 17c adds a small deterministic bounded write layer to RYNORFS v1:
`fs_write()` overwrites bytes inside existing file extents only. No
create/extend/truncate/delete/rename/mkdir, no allocation, no metadata
changes, no journaling. Lengths and extents never change, so open handles
stay valid across writes; partial-write behavior is explicit (completed
prefix reported); torn data bytes are possible across sectors while torn
metadata is impossible by construction (nothing writes metadata).

## Implementation

- `kernel/include/fs.h`: `FS_MAX_WRITE_BYTES 16384`, `fs_write()`
  contract, armed-only `fs_inject_fault_at()` declaration (link error if
  misused outside armed builds).
- `kernel/storage/fs.c`: overwrite-only `fs_write` (range must lie
  inside the file or `FS_RANGE`; over-cap `FS_INVALID`; shared handle
  decoding so stale/closed handles stay `FS_BADHANDLE`); edge blocks via
  read-modify-write through one static scratch block, aligned middles
  straight into the caller buffer in ≤32-block chunks; every block
  transfer funnels through a `data_write` choke point that also hosts
  the one-shot armed fault hook (absent from unarmed builds).
- `kernel/storage/fs-test.c`: Phase W (fixed 7-write set with hex
  payloads: beginning/end/cross-block/full/multi-block/exact-capacity;
  invalid offset/length/handle/buffer/stale cases), unmount/remount
  readback of all written files, armed fault block (fail-1st/partial/
  after with exact prefix counts), all silent except `[FS] write/fault`
  lines.
- `tools/host/fs_output.py`: ordered-event validation (file/part lines
  checked against content patched by previously printed writes);
  `[FS] write/fault` line formats; snapshot-off host-file corroboration
  path in tests.
- `tools/host/qemu.py`: `extra_drives` accepts `(path, snapshot_on)`
  tuples (plain paths keep overlay-on behavior).

## Write model (atomicity bounds, honestly stated)

- Atomic unit: one 512-byte sector (single PIO command).
- Multi-block overwrite: sectors commit in order; on block-N failure,
  sectors <N hold new bytes, >=N hold old bytes, `*nwritten` reports the
  completed prefix, code is `FS_IOERR`.
- Metadata: never written by `fs_write` (lengths/extents/handles
  unchanged) — always mountable, always consistent.
- Interrupted operation (IRQ0 preemption): memory-safe (static state,
  no partial handle updates); on-disk effect identical to the completed
  prefix rule above. No power-loss atomicity beyond single sectors;
  no durability beyond the tested QEMU device.

## Failure model

Block error/timeout → prior prefix complete, `FS_IOERR`; bad
handle/offset/length/buffer → classified codes before any I/O;
interrupted multi-block → torn data possible, metadata intact;
failed mount → fail-closed entry leaves prior state dead (inherited
17b rule). No impossible states: every enumerated failure maps to a
tested outcome.

## Verification

```text
python tools/build/build.py build
python tools/build/build.py test      # 510/510 repository
python tools/build/build.py integration-test  # 191/191 integration
python tools/build/build.py validate
python tools/build/build.py check
```

Reference-host result: full `check` green (build + 510 repository +
191 integration). `tests/repository/test_fs_output.py` (20 tests):
patched-expectation, malformed/ordered write lines, fault parsing.
`tests/integration/test_filesystem.py` (19 tests): write/readback
evidence, exact fault lines, snapshot-off host-file corroboration
(decoded disk bytes equal patched expectations), 4 write-path
mutations (plus the 4 inherited read-path mutations). Inventory sums equal collected counts.

## Mutation matrix (write path)

- wrong-block (+1 LBA in fs_write): readback digest mismatch — DETECTED
- ignore-error (continue after failed block): armed fault block halts
  differently / readback mismatch — DETECTED
- always-success (early OK): readback shows old data — DETECTED
- range check dropped (allow over-length): invalid-case require halts
  the boot — DETECTED

## Limitations

- Overwrite-only; no create/extend/truncate/delete/rename/mkdir.
- No journaling, no atomic multi-block, no durability framework.
- QEMU `pc-i440fx` PIIX3 IDE only; no physical-hardware verification.
- Fault injection covers data-block transfers (the only writes that
  exist); no metadata-write faults possible by construction.
