# P1-A2: CPL3 file create/write ABI (Slice P1-A userspace exposure)

Status: **complete.** This RFC exposes the proven P1-A1 mutation
core (`fs_create`, gap allocator, `fs_write` extension, relocate on
grow, data-before-directory ordering, hole rejection) to normal CPL3
userspace. It allocates syscall numbers 9-10 and `sys_err` codes
11-12 per `docs/design/abi-growth.md` G1/G4. P1-A1 (`fs_create` /
`fs_write` semantics, on-disk format) is unchanged.

Precedence: below `docs/design/stage18d-abi.md` §§A-F (normative for
syscalls 0-8, untouched), below the frozen per-stage UAPI headers,
above future stage RFCs. Conflicts resolve in favor of the older
freeze.

## Numbers (G1)

```text
SYS_FCREATE = 9
SYS_FWRITE  = 10
```

Syscalls 0-8 keep their numbers and semantics. Next free number is 11.

## Register files (G2)

`SYS_FCREATE` (stateless create of a zero-length file):

```text
EAX  9
EBX  path_ptr      (user bytes, no NUL required)
ECX  path_len      (1..32)
EDX  0             (reserved)
ESI  0             (reserved)
EDI  0             (reserved)
EBP  0             (reserved)
RAX  sys_err
```

Nonzero reserved words are INVAL returns (never kills), checked by the
dispatcher like spawn/wait/terminate.

`SYS_FWRITE` (stateless write of exactly `len` bytes at `offset`):

```text
EAX  10
EBX  path_ptr      (user bytes, no NUL required)
ECX  path_len      (1..32)
EDX  offset        (full 64-bit; past end-of-file is rejected)
ESI  buf           (user source bytes)
EDI  len           (0..16384)
EBP  nwritten_out  (u64 destination, published last)
RAX  sys_err
```

All six argument registers are used (no reserved word exists), like
fread. G2 framing (EAX high-32 rule, full-64-bit args, RAX return,
GPR preservation) is inherited unchanged.

## Error codes (G4)

`sys_err` keeps values 0-10 forever. Two codes are appended:

```text
SYS_EXISTS = 11
SYS_NOSPC  = 12
```

Justification (G4a: semantic gap): no existing code covers these.

- EXISTS (create target already present, any type, including `/` and
  directories) is a *state* collision, not a malformed argument:
  BADARG covers pointer/length/shape failures, NOTFOUND covers
  names that resolve to nothing, MALFORMED covers type mismatches.
  Collapsing "already exists" into any of those would lie to the
  shell about why a create failed (and the shell must report the
  target-must-not-exist failure honestly: there is no truncate in
  this slice).
- NOSPC (directory full, or no allocatable data extent for growth)
  is *storage* exhaustion: NOMEM is kernel-memory exhaustion,
  IOERR is device/transfer failure. A full disk is neither.

Frozen values above (G4b). Per-syscall mapping tests (G4c):
`tests/integration/test_p1a2.py` (kern-driver rows and the CPL3 probe
matrix hit every mapping). Check-removal mutants (G4d): the P1-A2
mutant set (`P1A2MutantTests`, each asserting its exact RED vector)
removes the EXISTS/NOSPC classifications, the staged-path check,
the dispatcher reserved-word check, and the untouched-on-error
discipline:

```text
M1  shell pipes echo into cat, not fput   -> no file; cats fail
M2  fput swallows EXISTS                  -> silent victim overwrite
M3  dispatcher ignores reserved words     -> probe dies at 159
M4  fwrite publishes a count on error     -> probe dies at 166
M5  rt_fcreate always reports OK          -> probe dies at 192
M6a fcreate NOSPC collapsed to IOERR      -> fcfull dies at 210
M6b fwrite NOSPC collapsed to IOERR       -> fcfull dies at 223
M7  kern_fcreate staged-path check gone   -> probe dies at 147
M8  fcreate EXISTS misreported as OK      -> probe dies at 142
```

Buffer-shape CPL3 rows (null/odd_buf/toolen) are pinned by probe
rows plus kern-driver requires, without a dedicated red-proof
mutant.

`rt_err` mapping (G4, documented per RFC, never remapped silently):
the thin `rt_fcreate` / `rt_fwrite` wrappers collapse like `rt_fread`
(OK -> RT_OK, everything else -> RT_INVAL). Exact `sys_err` codes are
observed in-guest through `rt_gate6`; shells use the raw gate for
exact codes (the established Slice E convention).

## Kernel cores

`kern_fcreate(kpath)` / `kern_fwrite(kpath, offset, kbuf, len,
*nwritten_out)` run on staged kernel memory (never user pointers)
and own the `fs_result` -> `sys_err` mapping; `sys_fcreate` /
`sys_fwrite` add the userspace staging/publication shell (G3:
scalars -> wrap checks -> copy-once staging -> staged validation ->
admission -> commit -> publish last).

`kern_fcreate` mapping:

```text
FS_OK       -> SYS_OK
FS_EXISTS   -> SYS_EXISTS
FS_NOSPC    -> SYS_NOSPC
FS_NOTFOUND -> SYS_NOTFOUND   (missing parent names nothing)
FS_NOTDIR   -> SYS_MALFORMED  (file on the parent chain)
FS_INVALID  -> SYS_IOERR       (staged path pre-validated: unmounted)
else        -> SYS_IOERR
```

`kern_fwrite` mapping (after stat/open pre-checks: missing ->
NOTFOUND, non-file -> MALFORMED, offset > size -> BADARG for every
length including 0):

```text
FS_OK                -> SYS_OK (*nwritten_out == len)
FS_NOSPC             -> SYS_NOSPC
FS_RANGE/FS_INVALID  -> SYS_BADARG (defensive; pre-checked)
FS_NOTFILE           -> SYS_MALFORMED (defensive; pre-checked)
FS_BADHANDLE         -> SYS_BADHANDLE (defensive; fresh handle)
else                 -> SYS_IOERR
```

`fwrite` return contract (frozen): OK means **all** `len` bytes
landed (`*nwritten_out == len`); **every** error leaves both user
outputs untouched. A multi-chunk syscall whose later chunk fails
returns the error with the count untouched; earlier landed chunks
are coherent file bytes (documented short-write behavior, like
POSIX), and only SYS_OK publishes a count. There are no partial
success counts at the syscall boundary.

Hole rejection (P1-A1, unchanged): any write with offset past
end-of-file fails with BADARG, including zero-length writes. Growth
without a free extent fails with NOSPC; a failed relocation leaves
the old entry intact with `*nwritten == 0` (data-before-directory).

## Userspace wrappers

`user/lib/rt/rt_fs.h` (header-only companion; `rt.h` stays frozen):

```text
rt_fcreate(path, path_len) -> rt_err
rt_fwrite(path, path_len, offset, buf, len, nwritten) -> rt_err
```

Mirrors `RT_SYS_FCREATE` / `RT_SYS_FWRITE` / `RT_FWRITE_MAX` /
`RT_FCREATE_PATH_MAX`, pinned equal to the kernel headers by
`tests/repository/test_p1a2_abi.py`. Collapse rule identical to
`rt_fread`.

## Shell

`echo TEXT > /path` only (general `command > file` is out of scope:
any other command with `>` fails as `[SH] error redirect`, status 2,
with no spawn). The shell spawns echo with stdout piped into a
synthesized `/bin/fput` consumer stage (`argv`: `fput`, target) via
the proven `exec_pipe` path; the shell itself never touches file
bytes (pipe capture, not parser-direct writes). A consumer exit code
N surfaces as `[SH] error redirect N` with pipeline status N (the
raw `sys_err`: 11 EXISTS, 12 NOSPC, ...). There is no truncate:
existing targets fail honestly.

`user/proc-tests/sh_fput.c` (redirect consumer): reads stdin (pipe)
to EOF (cap 2048, exit 65 past it), strips exactly one trailing
`\n` (the echo terminator; the file holds TEXT verbatim otherwise),
creates `argv[1]` through fcreate, writes the captured bytes through
fwrite (a zero-length write still runs for empty input), and exits 0
on success or the RAW fcreate/fwrite `sys_err` otherwise (exit 66 on
bad argv). Only the shell's `echo TEXT > /path` is specified; manual
`|>` composition works mechanically but is not a supported
interface.

`cat /path` (existing helper) reads files back through fread and is
the shell-visible readback leg of every redirect test.

## Tests

`tests/integration/test_p1a2.py`:

- `P1A2KernTests` (4): marker-gated (`/p1a2-go`) kern-driver battery
  covering what CPL3 cannot reach (fault injection, unmounted
  mappings, kernel-buffer shapes, the kern length cap), kern-written
  content, and a reboot leg. Absence of `p1a2` rows on unmarked
  images is the documented gate.
- `P1A2ShellTests` (1): the full e2e. One script exercises
  redirects (proof text, the EXISTS collision row, multiword, empty,
  padded whitespace, nospace `>`), cats, and the `fcwprobe` matrix;
  both boots' done vectors and the child-output slot bytes are
  pinned exactly, and the drive is decoded host-side after each
  boot.
- `P1A2FullTests` (2): directory-full and disk-full through the real
  CPL3 gate (`fcfull`): NOSPC arrival + stickiness, priors intact,
  exact counts (12 zero-length creates; one 4 KiB chunk).
- `P1A2MutantTests` (9): M1-M8 above, each on a tree copy (the
  working tree is never mutated).

`tests/repository/test_p1a2_abi.py` pins the numbers: 9/10,
RT mirrors, caps, and the 0-8 freeze.

Reboot discipline (mirrors P1-A1): the probe opens with a
fresh-vs-reboot prelude (absent `/w/a` means first boot); on reboot
it takes a verify-only path (byte-exact readback, no writes) and
exits 0, so any number of reboots is stable by construction. The
drive check patches the 17c overwrites into the baseline from the
boot's printed `[FS] write` rows, carves out `b1500[0,512)` (fault
leftovers, pinned boot-vs-boot only), and the cross-boot comparison
masks that one block: boot 2 provably writes nothing new (every
redirect collides, the probe only reads).

## Limitations (frozen for this slice)

```text
no truncate        (existing redirect targets fail honestly)
no unlink
no rename
no append mode
no mkdir
no permissions / ownership
no journaling      (multi-chunk syscalls are not atomic across
                    chunks; each chunk is one fs_write)
```

Create-then-write partial failure: if `fcreate` succeeds and the
following `fwrite` fails, the entry keeps its pre-write state (the
data-before-directory rule: a failed growing write never publishes
a count and never moves the entry). Pinned shapes: the diskfull leg
leaves `w/big` at its pre-NOSPC length with priors intact, and the
dirfull leg leaves its 12 create-only files zero-length. The pure
first-write-NOSPC-on-empty shape (zero-length remains) follows from
the same path but has no dedicated pin.
