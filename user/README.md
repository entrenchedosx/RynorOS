# Native environment

## Purpose

Reserve `shell/` for the native shell, `lib/` for shared native APIs/runtime
bindings, and `apps/` for RynorOS applications.

## Public interfaces

None. Shell commands, library APIs, and syscalls are planned, not host API aliases.

## Invariants

Expose only real OS services. Clearly label trusted kernel-mode programs versus
protected user processes. Use `.rl` for RynorLang source.

## Implementation status

Planned only. An early kernel monitor may precede a userspace shell; its source
will live in the kernel until isolation and syscall contracts exist.

## Tests

Directory-presence checks only. Future launch, I/O, failure, and isolation checks
belong in `../tests/integration/`.

## Known limitations

No shell, standard library, application loader, syscall ABI, or runnable apps.
