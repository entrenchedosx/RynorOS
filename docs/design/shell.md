# Stage 11: kernel shell/monitor

## Purpose and scope

Stage 11 adds a verified **ring-0 kernel monitor** that exercises the existing kernel subsystems through real keyboard input. It is a trusted, single-CPU, `IF=0`-at-entry monitor that runs in the bootstrap thread after all Stage 0–10 self-tests, before the final `pmm/vm/heap/scheduler` integrity gate. It exposes **only implemented services** (Stage 10 `KRST_SVC_UPPER` / `COUNT_DIGITS` / `DIGEST` plus honest built-ins `help/version/echo/clear`) and rejects everything else. There is no userspace, no filesystem, no RynorLang execution, no `Ring 3`.

## Invariants

* **Bounded line** `SHELL_LINE_MAX 64` → `data[65]` with `len`/`NUL` invariant; `line_insert` rejects `len>=64` without overflow.
* **Bounded tokenizer** `SHELL_ARG_MAX 12`, `kstr_nlen(line,cap)!=cap` validates NUL within `cap`; `12` tokens → `SHELL_OK`, `13` → `SHELL_TOO_MANY=-3` (negative, never aliases `0..12`). `SHELL_INVALID=-1` for unterminated. Tokens alias into `line`; no escape.
* **Strict dispatch** `help/version/clear` `argc==1`, `echo/upper/count/digest` `argc==2` (extra → `SHELL_ARGS=-5`), unknown → `SHELL_UNKNOWN=-4`.
* **Runtime safety** `text_len = kstr_nlen(tokens[1], cap-offset)` with `offset<cap` guard; `krst_call` re-entrantly validates `IRQ`/`overlap`/`MAX_BYTES`; `upper` checks `n<48` before `NUL`; `count` decodes full 64-bit LE, `digest` checks `nlen==8`.
* **Keyboard** real `IRQ1` via `kbd_poll` with `sti;hlt;cli`; `E0`/`E1` prefix isolation preserved (driver contract), `AUX`/`ERROR`/`00`/`FF` counted as `epoch` loss, `UNKNOWN` makes are stored then ignored, `LOST` halts fail-closed, one `sendkey` (make+break) → one `waiting` marker, releases drained by `scan&0x7f`.
* **Scheduler/IRQ** `shell_run` requires `IF=0`, enables only `IRQ1`, never touches `IRQ0` handler, preserves `IF`, no heap/PMM/VM allocation, balanced `pmm/vm/heap/table_pages` before/after.

## Public interfaces

`kernel/include/shell.h`:

* `int shell_tokenize(char *line, cpu_u64 cap, char *tokens[12])` — in-place `' '`→`NUL`, returns `0..12` or `SHELL_TOO_MANY=-3`/`SHELL_INVALID=-1`
* `int shell_execute(char *line, cpu_u64 cap)` — dispatch, emits `[SHELL] exec=…` + result/error, returns `shell_result` (`SHELL_SERVICE=-6` on runtime failure, `SHELL_ARGS=-5` on arity mismatch)
* `void shell_run(cpu_u64 key_budget)` — budget loop `irq_set_enabled(1,1)` (checked, halt on failure) → `waiting` → `wait_key` → `translate` → `insert/backspace/Enter` → `shell_execute` → `irq_set_enabled(1,0)` (checked); `key_budget==0` performs zero iterations
* `void shell_self_test(void)` — synthetic parser/dispatch + optional interactive session (`RYNOR_SHELL_INTERACTIVE`)

## Implementation status

**Implemented:** `kernel/shell/shell.c` (bounded table `a–z`/`0–9`/`space`, `Enter 0x1c`, `Backspace 0x0e`, `upper` `n<48` guard, `count` full 64-bit LE), `kernel/shell/shell-test.c` (synthetic tokenizer/dispatch + 39-key interactive), `tools/host/shell_output.py` host validator, `tools/host/qemu.py` dual-stream injection. Shell is **integrated into the normal `rynoros.img`** (always built; `shell_self_test` runs synthetic tests in every boot, interactive `39`-key session only with `RYNOR_SHELL_INTERACTIVE`); the `shell_interactive` flag selects the interactive test image.

Synthetic evidence (always): `tokenizer, bounds and empty line` → `exec version/help/echo/upper/count/digest/clear/bogus/empty` → extra-arg rejections (`too many`, `invalid`, `echo/upper/count/digest/version/help` extra) → `count` `30` (`1`*30, shell line) + `300` (`1`*300 via direct `krst_call` proving full 64-bit decode) → recovery `bogus→version` → `dispatch verified`. Interactive session: 4 commands `upper hello`→`HELLO`, `count a1b2`→`2`, `digest ab`→`0x6A9845B507449C08`, `bogus`→`error: unknown command`, each key with `scan/ ascii/line` echo, `keys=39 received_scan_bytes=78`.

**Not implemented:** filesystem, pipes, globbing, scripting, `user/shell`, RynorLang evaluation — `user/shell` remains empty.

## Tests

* `python tools/build/build.py test` — `tests/repository/test_shell_output.py` (7 tests): valid fixture, every-line-required, structural damage, accounting, script well-formed, `-O` fails closed, stage10 early rejected.
* `python -m unittest discover -s tests/integration -p test_shell.py` — runs 8 test methods: two positive 39-key QEMU sessions (default and alternate host-selected scripts) and six mutation-negative guests. The validator requires `SHELL_END`, `keys=39 received=78`, all host inputs, exact per-key/per-command output, and the keyboard device/PIC trace. Mutations cover canned output, dispatch bypass, runtime-service bypass, tokenizer bypass, low-byte-only count decoding, and a realistic keyboard-draining canned transcript. Kernel synthetic tests separately cover tokenizer/result-code/error boundaries and service-result widths.
* The complete suite currently contains 159 repository and 155 integration test methods (147 non-shell + 8 shell). The positive QEMU configuration matrix has 9 distinct configurations: 8/16/64/128/256/512/4096 MiB, `max` CPU, and the low-32/high-RAM layout.
* `python tools/build/build.py boot-test` / `check` / QEMU matrix (`8…4096` MiB, `max`, `low-32`) now include shell synthetic output in every normal boot.

## Known limitations

* `64`-byte line, `12`-token, `a–z`/`0–9`/`space` only (no `~!@#$%^&*()_+-=[]{}|;':",./<>?` except `.-:/?` via text subset), lower-case only, no tab, no history, no cursor, no UTF-8.
* `clear` is an honest stub (`display redraw requested`), not a framebuffer clear via `display_fill_rect`; the framebuffer pattern from Stage 9 remains intact for `pmemsave`/`screendump` evidence.
* `echo`/`upper`/`count`/`digest` strictly `argc==2`; extra args rejected, not joined.
* `32`-byte `HEAP`/`PMM`/`VM` window unchanged; shell allocates nothing (verified via `balanced`).
* Single-CPU `IF=0` entry, `IRQ1` only; `E0` extended keys suppressed (4 bytes) without marker, `E1` Pause `5` bytes unconditional — well-formed `E1 1d 45 e1 9d c5` works, malformed loses up to 4 keys (acceptable, never sent by harness).
* QEMU-only `TCG` `pc-i440fx-10.0` `qemu64`; no bare-metal `USB`/`APIC`/`SMP`.

See `docs/reports/stage11.md` for final verification, mutation gauntlet, and bare-metal horizons.
