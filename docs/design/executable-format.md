# RynorOS executable format (RYNX v1)

Stage 18b loads native RynorLang programs linked at fixed user virtual
addresses. The kernel never parses ELF: a host converter links backend
ELF output at the fixed windows and packs this envelope, which the
kernel validates with checked arithmetic before mapping anything.

## Why not ELF in the kernel

Parsing `EHDR`/`PHDR`/`SHDR`/`RELA`/`DYNAMIC` in ring 0 for hostile
bytes would massively expand the TCB. The host is already trusted to
build filesystem images, so all ELF parsing lives in
`tools/host/rnyx.py` (unit-tested, mutated). The kernel sees only the
28-byte envelope below. A Linux ELF is never directly loadable: the
Stage 16 host binaries use Linux syscalls and a Linux layout, and the
converter rejects anything outside the subset.

## Layout (little-endian, 28 bytes, no trailing bytes)

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 4 | magic | `"RYNX"` |
| 4 | 2 | version | `1` |
| 6 | 2 | arch | `1` (x86-64) |
| 8 | 2 | header_len | `28` |
| 10 | 2 | reserved | `0` |
| 12 | 4 | entry_off | `0` (entry is defined as `USER_CODE_BASE`) |
| 16 | 4 | code_size | `1..4096` |
| 20 | 4 | data_filesz | `0..4096` |
| 24 | 4 | data_memsz | `filesz..4096` |

Followed by `code_size` code bytes, then `data_filesz` data bytes. The
total file size must equal `28 + code_size + data_filesz` exactly.

## Fixed virtual-address model

* Code loads at `USER_CODE_BASE` (`0x400000`) with `U-RX`. Exactly one
  executable segment; `W+X` and BSS-in-code are rejected by the
  converter and unimaginable to the kernel (fixed mapping).
* Data loads at `USER_DATA_BASE` (`0x600000`) with `U-RW`. File-backed
  bytes tile contiguously from the base; the tail to `data_memsz` is
  zero-filled (BSS). The mapping is the whole fixed 4 KiB page, so
  bytes past `data_memsz` to the page end are zeroed but remain
  addressable (over-mapping by fixed-window design, not per-byte
  enforcement). String immutability inside the RW page is a
  language property (no mutation syntax), not a paging property.
* Stack is the fixed 18a stack (top `0x800000`, guard below, initial
  `RSP` at top). No argv/env yet: an empty stack reads as `argc=0`;
  `argc`/`argv` remain future work and are documented as such.
* No other VA window exists in 18b: no heap, no `sbrk`, no growth. The
  legal user window is `[0x400000, 0x800000)` plus the guard semantics
  from `userspace.md`.

## Entry point

`entry_off` must be `0`: entry is defined as `USER_CODE_BASE`, which is
exactly what the existing `user_enter` establishes (it resets `RIP`).
A variable entry would need an audited enter-at-offset path first. The
linker script pins `_start` first in `.text` so `e_entry` is the base;
the converter and the kernel both enforce it.

## Validation checklist (kernel, `rnyx_validate`)

Truncation, magic, version, arch, header length, reserved-nonzero,
entry-nonzero, zero/oversize code, data `filesz`/`memsz` range and
order, every addition overflow-checked, exact total shape. Each failure
maps to a distinct `rnyx_error` code surfaced in `[LOAD] reject` rows.
Anything not on this list (overlaps, kernel VAs, guard/stack mapping,
permissions) is impossible by construction: the loader hardcodes the
three fixed mappings and never honors envelope addresses or
permissions.

## Producer contract (host converter)

Accepts standard `ELF64-LE-x86-64` `ET_EXEC` linked with
`tools/rynorlang/runtime/rynoros.ld` (`.text` at the code base with
`_start` first, rodata/data/bss in the data window). Rejects: wrong
ELF kind/machine, non-`ET_EXEC`, entry off base, unknown segment
types, `W+X`, BSS-in-code, out-of-window segments, non-contiguous
data tiling, `filesz > memsz`. Unknown `LOAD` flags rejected. `NULL`
and `GNU_STACK` ignored.

## Versioning

`version` unknown → reject. Reserved must be zero (future flags fail
closed). `header_len` must equal 28 (unknown trailing header bytes
fail closed). The namespace only extends with new versions, never by
reinterpreting v1 fields.
