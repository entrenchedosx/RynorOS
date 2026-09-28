# P1-A3: CPL3 file discovery + lifecycle ABI (stat/readdir/unlink)

Status: **complete (verified).** This RFC exposes discovery and
deletion to normal CPL3 userspace: stat, directory enumeration, and
persistent unlink. It allocates syscall numbers 11-13 and `sys_err`
code 13 per `docs/design/abi-growth.md` G1/G4. P1-A1/P1-A2
semantics and the on-disk format are unchanged; there is no
rename/mkdir/rmdir, truncate, permissions, timestamps, links, or
journaling in this slice.

Precedence: below `docs/design/stage18d-abi.md` §§A-F (syscalls 0-8),
below `docs/design/p1a2-cpl3-abi.md` (syscalls 9-10, errors 11-12),
above future stage RFCs. Conflicts resolve in favor of the older
freeze.

## Namespace audit (verified from headers, not assumed)

`SYS_*` names live in SEPARATE namespaces; identical numbers across
namespaces are coincidence, never aliasing:

```text
symbol            namespace        value  meaning / frozen?
SYS_FCREATE       syscall number   9      create call; frozen (P1-A2)
SYS_FWRITE        syscall number   10     write call; frozen (P1-A2)
SYS_EXISTS        sys_err value    11     create collision; frozen (P1-A2)
SYS_NOSPC         sys_err value    12     storage exhaustion; frozen (P1-A2)
SYS_INVAL         sys_err value    2      bad argument class; frozen (18d)
SYS_IOERR         sys_err value    10     device/image failure; frozen (18d)
SYS_NOTFOUND      sys_err value    3      resolves to nothing; frozen (18d)
SYS_MALFORMED     sys_err value    4      type mismatch; frozen (18d)
SYS_BADARG        sys_err value    8      pointer/length/shape; frozen (18d)
SYS_FSTAT         syscall number   11     stat call; NEW, frozen here
SYS_READDIR       syscall number   12     enumerate call; NEW, frozen here
SYS_UNLINK        syscall number   13     delete call; NEW, frozen here
SYS_END           sys_err value    13     end of directory; NEW, frozen here
FS_EXISTS         fs_result value  -11    kernel fs collision (P1-A)
FS_NOSPC          fs_result value  -12    kernel fs exhaustion (P1-A)
FS_END            fs_result value  -13    past last live entry; NEW here
USER_RUN_FSTAT    resume code      14     kernel-internal, not UAPI
USER_RUN_READDIR  resume code      15     kernel-internal, not UAPI
USER_RUN_UNLINK   resume code      16     kernel-internal, not UAPI
```

Next free syscall number is 14; next free `sys_err` value is 14.
`SYS_END = 13` (error) and `SYS_UNLINK = 13` (call) share a number
across namespaces exactly like the pre-existing `SYS_FREAD = 7` /
`SYS_NOMEM = 7` collision: legal, documented, and pinned by test.

## Numbers (G1)

```text
SYS_FSTAT   = 11
SYS_READDIR = 12
SYS_UNLINK  = 13
```

Syscalls 0-10 keep their numbers and semantics.

## Register files (G2)

`SYS_FSTAT` (stateless stat over an absolute path):

```text
EAX  11
EBX  path_ptr      (user bytes, no NUL required)
ECX  path_len      (1..32)
EDX  out_ptr       (32-byte struct user_stat destination)
ESI  0             (reserved)
EDI  0             (reserved)
EBP  0             (reserved)
RAX  sys_err
```

`SYS_READDIR` (dense-ordinal enumeration):

```text
EAX  12
EBX  ordinal       (0-based dense index over live entries)
ECX  out_ptr       (64-byte struct user_dirent destination)
EDX  0             (reserved)
ESI  0             (reserved)
EDI  0             (reserved)
EBP  0             (reserved)
RAX  sys_err
```

`SYS_UNLINK` (stateless persistent delete over an absolute path):

```text
EAX  13
EBX  path_ptr      (user bytes, no NUL required)
ECX  path_len      (1..32)
EDX  0             (reserved)
ESI  0             (reserved)
EDI  0             (reserved)
EBP  0             (reserved)
RAX  sys_err
```

Nonzero reserved words are INVAL returns (never kills), checked by
the dispatcher like fcreate/spawn. G2 framing (EAX high-32 rule,
full-64-bit args, RAX return, GPR preservation) inherited unchanged.

## Error codes (G4)

One code is appended:

```text
SYS_END = 13
```

Justification (G4a: semantic gap): end-of-directory is neither a
missing path (NOTFOUND), a shape failure (BADARG), nor a device
fault (IOERR). Overloading any of those would force `ls` to treat
real errors as success. Frozen value above (G4b). Per-syscall
mapping tests (G4c): `tests/integration/test_p1a3.py`. Removal
mutants (G4d): see Mutants below.

`rt_err` mapping: the thin wrappers collapse like `rt_fread`
(OK -> RT_OK, everything else -> RT_INVAL); exact `sys_err` codes go
through `rt_gate6` (shells use the raw gate for exact codes).

## Structures (frozen UAPI)

Only truthful fields. The filesystem stores no timestamps,
permissions, owners, or links, so the ABI reports none. Every
reserved/padding byte is zeroed before publication; no kernel
pointers cross; compile-time layout assertions pin both structs.

```text
struct user_stat   (32 bytes, align 8):
  u64 type        (1 = file, 2 = dir; UAPI_FTYPE_* mirrors)
  u64 size        (file bytes; 0 for directories)
  u64 reserved[2] (zero; room for future blocks/flags)

struct user_dirent (64 bytes, align 8):
  u8  name[40]    (absolute path, NUL-terminated, zero-padded;
                   longest legal path is 32 chars + NUL)
  u64 type        (1 = file, 2 = dir)
  u64 size        (file bytes; 0 for directories)
  u64 reserved    (zero)
```

`UAPI_FTYPE_FILE = 1` / `UAPI_FTYPE_DIR = 2` mirror
`FS_TYPE_FILE`/`FS_TYPE_DIR` (pinned equal by test, never aliased).

## Enumeration design (ADR)

Option A (index-based) wins: `readdir(ordinal)` returns the
ordinal-th LIVE entry in directory-slot order, skipping free slots.
No raw slot numbers, no kernel pointers, no handles, no new
process-resource machinery (Option C overkill for a flat fs with
<=512 entries). Deterministic order = slot order (the natural
existing order; creation order is observable through it).
End = `SYS_END` when ordinal >= live count. Directories are
returned (type 2, size 0); root itself is not an entry and is never
enumerated (`stat /` still works). Callers MUST restart enumeration
after any mutation (ordinals shift); `ls` reads the whole directory
in one pass and never mutates, so this is safe.

## Kernel cores

`kern_fstat(kpath, kstat)` / `kern_readdir(ordinal, kdirent)` /
`kern_unlink(kpath)` run on staged kernel memory and own the
`fs_result` -> `sys_err` mapping; the `sys_*` layer adds staging
(G3: scalars -> wrap checks -> copy-once staging -> staged
validation -> admission -> commit -> publish last). Outputs are
published last and left untouched on every error.

```text
fstat:   OK->OK  NOTFOUND->NOTFOUND  NOTDIR->MALFORMED
         INVALID->IOERR (staged path pre-validated: unmounted)
         else->IOERR           (stat on a directory is VALID)
readdir: OK->OK  END->END
         INVALID->IOERR (only reachable unmounted: no path arg)
         else->IOERR
unlink:  OK->OK  NOTFOUND->NOTFOUND  NOTDIR->MALFORMED
         NOTFILE->MALFORMED (directories and root never unlink)
         INVALID->IOERR (staged path pre-validated: unmounted)
         else->IOERR
```

`fs_unlink` persists via the existing `dir_write_slot` (single
64-byte zero entry, disk-before-RAM, same discipline as create);
the cleared slot is immediately reusable by create and the
forgotten extent immediately reusable by the gap allocator. Open
handles on the unlinked slot are invalidated (no aliasing on slot
reuse). Data bytes are NOT erased: deleted payload stays on disk
until overwritten (future security consideration, stated here).

Crash honesty: unlink persistence is one directory-sector RMW; the
atomic unit is whatever the block layer gives one sector write
(identical exposure to create). No journaling, no transactions.
Fault injection on the directory write leaves the old entry intact
(RAM still shows it; disk keeps it) with IOERR propagated.

## Userspace wrappers

`user/lib/rt/rt_fs.h` grows (same file domain; `rt.h` frozen):

```text
rt_fstat(path, path_len, out) -> rt_err
rt_readdir(ordinal, out) -> rt_err
rt_unlink(path, path_len) -> rt_err
```

Struct mirrors + gate numbers + caps pinned by
`tests/repository/test_p1a3_abi.py`. Collapse rule identical to
`rt_fread`.

## Shell

`ls` (one absolute path per line, slot order, bounded loop),
`stat /path` (`path:`/`type:`/`size:` lines, raw `sys_err` exit on
failure), `rm /path` (exit 0 / raw `sys_err`) are `/bin` helpers
through the PUBLIC ABI; the shell spawns them like cat (no parser
changes, no kernel-table inspection). `cat` becomes stat-driven:
fstat size first, then exact-length chunked fread (no new ceiling).

## Tests

`tests/integration/test_p1a3.py`: kern battery (marker-gated
`/p1a3-go`, kern-unreachable shapes only: buffer shapes, unlink
fault leg, unmounted mappings -- under the old 0x70000 link budget,
nearly spent at the time, so all content/enumerate/reuse/reboot rows
run in CPL3),
CPL3 probe matrix (stat/readdir/unlink + hostile pointers + alive
checks + reboot-verify prelude), lifecycle e2e (create/list/stat/
delete/reuse across two boots), dir-cycle + disk-cycle legs,
enumeration/mutation interaction, mutants M1-Mn.
`tests/repository/test_p1a3_abi.py`: numbers 11/12/13, `SYS_END`,
struct sizes/offsets, mirrors, resume codes, 0-10/0-12 frozen.

## Limitations (frozen for this slice)

```text
no rename / mkdir / rmdir / truncate
no permissions / ownership  (any CPL3 process can stat/list/unlink
                             anything: MAJOR future security blocker)
no journaling / transactions
no secure erase on unlink
enumeration restarts after mutation (no snapshots)
```

## Mutants (G4d)

Each asserts its exact RED vector on a tree copy (working tree
never mutated). Shipped set (`tests/integration/test_p1a3.py`,
all observed RED):

```text
M1  readdir END->OK:        ls dies 65, probe walk dies 220, halt 65
M2  unlink NOTFILE->OK:     probe dir-unlink row dies 231, halt 0
                            (/docs survives: the lie is the bug)
M3  fstat size always 0:    probe content row dies 201, halt 0
M4  readdir counts free:    ls dies 10, probe walk dies 220, halt 10
M5  readdir END->NOTFOUND:  ls dies 3, probe walk dies 220, halt 3
M6  fcreate NOSPC->IOERR:   dircycle dies 201 (never sees 12)
M7  fwrite NOSPC->IOERR:    diskcycle dies 210 (never sees 12)
```

M1/M5 are the END-removal pair (G4d for `SYS_END`): without a
dedicated end-of-directory code, `ls` cannot distinguish success
from error. Considered and rejected (no observable RED witness):
unlink RAM-only (shared `dir_write_slot` with create: mutating it
breaks creates first, so the mutant is not unlink-specific),
`FS_END` renumber (both sides use the symbol; still matches).

## Verification (observed)

```text
kern battery:   4/4  (evidence, marker gate, persist, reboot-idempotent
                      rows + masked disk identity across reboots)
shell e2e:      1/1  (17-line script x 2 boots: exact dones vectors,
                      exact slot-1 bytes both boots, exact slot+extent
                      reuse shape, transient-byte pins, masked identity)
cycles:         2/2  (dir-exact-full + slot reuse; data-exact-full +
                      first-fit block reuse at /one's old extent)
mutants:        7/7  (M1-M7 RED vectors above)
repo pins:      green (test_p1a3_abi + neighbors)
regressions:    69/69 (test_p1a2 + test_cplshell + test_p1a +
                      test_filesystem, incl. P1-A2 mutants under the
                      stat-driven cat and the -Os fs-test.c)
```

Reuse shape pinned from the raw directory (N0 = pristine entry
count, F0 = lowest free data block): `banner`@(N0,F0),
`sb`@(N0+1,F0+1) retaking `/gas`'s freed slot+block, `w/u3`@(N0+2,
F0+2..F0+4) retaking `/w/sa`'s. Deleted payload is NOT erased
(free-space leftovers pinned, never in live extents).
