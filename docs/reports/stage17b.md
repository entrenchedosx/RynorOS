# Stage 17b report -- read-only native filesystem

## Outcome

Stage 17b is a kernel-side read-only filesystem (RYNORFS v1) over the
verified Stage 17a `BlockDevice` API: versioned superblock, validated
64-byte directory entries, hierarchical path resolution over flat
storage, contiguous extents confined to the data region, strict paths,
generation-counted handles, cross-block reads with EOF semantics, and a
deterministic image builder plus an independent host decoder. Production
reads go through `blk_read` only. No writes, no heap, no userspace.

## Implementation

- `kernel/include/fs.h`: `FS_MAX_PATH 32`, `FS_MAX_OPEN 8`,
  `FS_MAX_DIR_BLOCKS 64` (512 entries), `FS_MAX_READ_BYTES 16384`;
  11-code error set (`OK/INVALID/NOTFOUND/NOTFILE/NOTDIR/BADHANDLE/
  RANGE/IOERR/CORRUPT/UNSUPPORTED/BUSY`); `fs_mount/unmount/mounted/
  open/stat/read/close` plus `fs_error_str`/`fs_stage_detail`.
- `kernel/storage/fs.c`: magic/version/blksize/reserved/padding checks
  (magic mismatch is `INVALID`, version/size beyond limits
  `UNSUPPORTED`); overflow-ordered extent math; directory read straight
  into a static 32 KiB copy; entry validation (names, types, padding,
  canonical empty extents, data-region confinement); duplicate,
  dangling-parent, and pairwise-overlap rejection; fail-closed
  (re)mount entry; handles as `slot | gen << 3` with monotonic
  generations; edge-staged reads through one static scratch block.
- `kernel/storage/fs-test.c`: silent invalid-path/handle/mount matrix
  every boot; fixed-set file evidence (full + partial sums) with edge
  cases; negatives (missing/dir-as-file/traversal/root); interleaved
  handles with stale-after-remount; mount/read/unmount accounting
  balance; corrupt-slot attempts with classified codes.
- `tools/host/fs_image.py`: canonical sorted builder (auto intermediate
  dirs, exact-fit totals, empty-filesystem support) plus 9 superblock
  and 6 entry corruptors; JSON-manifest CLI.
- `tools/host/fs_output.py`: spec-written decoder (mirrors every kernel
  rule), content derivation, serial parser, and file-recomputed
  validator.
- `tools/host/boot_output.py`: transcript grammar extended with an
  optional trailing filesystem section (normal boots byte-identical).

Design contract: `docs/design/filesystem.md` (format, rules, API,
error model, non-goals).

## Fixtures and tests

No checked-in images: deterministic generation at test time (standard
18-entry set, empty, nested, boundary-size, padded-device, 15 corrupt
variants).

`tests/repository/test_fs_output.py` (16 tests): builder determinism,
header layout, empty/nested/maxname/bigfile handling, builder
rejections, CLI codes without tracebacks, good decode, 9 superblock +
6 entry corruption codes, error-code table, genuine-evidence acceptance,
tamper/wrong-image/missing-marker rejection, part-slice recomputation.

`tests/integration/test_filesystem.py` (12 tests): shared good-image
boot (full evidence, nested/boundary spot checks, handle/accounting
markers, host pristine hashes), larger-device mount, three corrupt
boots with per-slot code tables (magic/invalid, version/unsupported,
dir/dup/extent/overlap/badname corrupt), and four mutations
(magic-gate removal wrongly mounts, extent-check removal halts,
blk-bypass and always-open caught by digest mismatch).

## Verification

```text
python tools/build/build.py build
python tools/build/build.py test      # 506/506 repository
python tools/build/build.py integration-test  # 184/184 integration
python tools/build/build.py validate
python tools/build/build.py check
```

Reference-host result: full `check` green (build + 506 repository +
184 integration). Inventory sums equal collected counts. Docs counts in
`README.md`, `ARCHITECTURE.md`, and `docs/design/shell.md` match.

## Forensic notes (found and repaired during implementation)

- Test design caught a genuine extent-range wraparound hole
  (`count > data_blocks - (first - data_start)` passes when `first`
  overshoots): kernel and decoder now use `first >= data_end ||
  count > data_end - first`, with a regression mutant proving it.
- Builder/decoder/kernel triple disagreement on canonical empty files
  (first=<offset> vs 0): fixed to absolute `(0,0,0)` in the builder.
- Host decoder padding-range and name-padding off-by-fields: fixed and
  covered (the kernel was right both times — exactly why two
  implementations cross-check).
- `fs_mounted` state/function name collision: renamed flag.
- Missing `fs_self_test` prototype caught by `-Werror` build.
- Transcript grammar: filesystem evidence trails the shell section via
  a strict optional section (existing validators untouched).

## Limitations

- Read-only: no writes/recovery/journaling (17c owns them against this
  read contract). Max 512 directory entries; 16 KiB per read; 8 open
  handles; no readdir enumeration; `.`/`..` rejected (no hierarchy
  traversal needed); IPv6-style nothing — out of scope entirely.
- QEMU `pc-i440fx` IDE only; no physical-hardware verification.
- Forced block-timeout staging still needs fault-injecting hardware
  (inherited 17a limitation); all real waits execute bounded.
