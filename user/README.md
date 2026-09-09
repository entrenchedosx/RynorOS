# Native environment

## Purpose

Reserve `shell/` for the native shell, `lib/` for shared native APIs/runtime
bindings, and `apps/` for RynorOS applications.

## Public interfaces

`lib/rt/` (Stage 18c, 15 frozen functions): `rt_exit`, `rt_write`,
`rt_print`/`rt_print_bytes`, `rt_fmt` (never NUL-terminates, use count),
`rt_alloc`, `rt_free` (exact live pointer only), `rt_arena_watermark`/
`rt_live_count`/`rt_ptr_off` (read-only evidence, no free authority),
`rt_nap`, `rt_set_flag`, `rt_wait_flag`, honest `rt_open`/`rt_read`
stubs. Shell commands remain planned, not host API aliases.

## Invariants

Expose only real OS services. Clearly label trusted kernel-mode programs versus
protected user processes. Use `.rl` for RynorLang source.

## Implementation status

`lib/rt/` implemented and verified (freestanding C + one asm stub over the
frozen 18b syscalls; seven CPL3 conformance programs). Shell and apps remain
planned; the kernel monitor lives in the kernel until 18d.

## Tests

`lib/rt` conformance runs in QEMU CPL3 (`tests/integration/test_rt.py`);
host-side rebind pins live in `tests/repository/test_rtlib.py`.

## Known limitations

No shell, no heap growth, no blocking calls, no file reads. The write sink
remains the 18b serial-hex evidence ABI.
