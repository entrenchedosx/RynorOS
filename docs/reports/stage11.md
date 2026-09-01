# Stage 11: kernel shell/monitor — implementation and verification

Stage 11 adds a verified **ring-0 kernel monitor** (`kernel/shell/`) that exercises the existing Stage 8 keyboard and Stage 10 runtime services through real `IRQ1` input. The `clear` command is currently an honest serial-only redraw request; it does not modify the Stage 9 framebuffer. The monitor is a trusted kernel component, not protected userspace (`Stage 18a`), and runs in the bootstrap thread after `runtime_self_test` and the final `pmm/vm/heap/scheduler` gate. See [design](../design/shell.md).

## What was built

* **`kernel/include/shell.h`** — `SHELL_LINE_MAX 64`, `SHELL_ARG_MAX 12`, `SHELL_CMD_MAX 16`; `enum shell_result { SHELL_OK=0, SHELL_INVALID=-1, SHELL_TOO_LONG=-2, SHELL_TOO_MANY=-3, SHELL_UNKNOWN=-4, SHELL_ARGS=-5, SHELL_SERVICE=-6 }` (negative, never aliases `0..12`).
* **`kernel/shell/shell.c`** — Bounded Set-1 → ASCII table (`a–z`, `0–9`, `space` `0x39`, `Enter 0x1c`, `Backspace 0x0e`); `line_insert` `len>=64` rejection; `shell_tokenize` validates `cap`, `kstr_nlen!=cap`, `12`→`ok`/`13`→`SHELL_TOO_MANY` (no silent drop); `shell_execute` uses strict `argc` (`help/version/clear` `==1`, `echo/upper/count/digest` `==2` → `SHELL_ARGS` on mismatch), an `offset<cap` guard before `kstr_nlen`, an `upper` `n<48` guard, full 64-bit little-endian count decoding with `nlen==8`, and digest `nlen==8`. `clear` reports a redraw request but deliberately performs no framebuffer operation. `wait_key` drains `KBD_EMPTY` with `sti;hlt;cli`, handles `KBD_LOST`→halt, isolates `E0`/`E1`, and drains the matching break event. Both `irq_set_enabled` calls are checked.
* **`kernel/shell/shell-test.c`** — Synthetic `parser_tests`: empty/unterminated/`12`/`13`-token, `SHELL_TOO_MANY`/`INVALID` negative distinctness, `result_collision`, `exec_too_many`/`exec_unterminated`, extra-arg rejections (`echo/upper/count/digest/version/help` → `SHELL_ARGS`), `COUNT` `30` via `shell_execute` + `300` via direct `krst_call` (proves `44` low-byte vs `300`), `bogus→version` recovery, plus accounting `balanced`. Interactive `39`-key `SCRIPT` (`upper hello`, `count a1b2`, `digest ab`, `bogus`) when `RYNOR_SHELL_INTERACTIVE`.
* **Host** `tools/host/shell_output.py` — exact synthetic and interactive transcripts (`waiting`/`key`/`line`/`exec`/`result` per key), key-to-scan validation (`spc→0x39`, `ret→0x1c`), independently computed FNV-1a digest output, and script-parameterized interactive expectations. `pmemsave` is not required for the serial shell.
* **QEMU harness** `tools/host/qemu.py` dual-stream `sendkey` per `waiting` marker (`[KBD]` and `[SHELL]`), `tools/host/boot_output.py` shell as final section after `POST_IRQ`, `tools/host/image.py` shell always built (flag selects interactive).

## Evidence and gates

* **Synthetic** `shell_output.py` validates exact `20`-line synthetic transcript plus `keys=39` accounting; every line required (mutation removes any line → `validate_shell_output` fails).
* **Interactive** `tests/integration/test_shell.py` builds `shell_interactive` image, QEMU injects 39 `sendkey` per marker, asserts `SHELL_END`, `keys=39 received_scan_bytes=78`, `shell_inputs_sent==39`, `reaped`/`monitor-quit`. The 39 `waiting` markers + per-key `scan=0x%02x ascii` `line` echo + per-command `exec`/`result` (`HELLO`/`2`/`0x6A98…`/`error: unknown`) are compared exactly; `validate_keyboard_trace` is not shell-specific but `qemu.py` also checks `KBD` trace for the same run.
* **Accounting** `shell-test.c` `balanced` checks `pmm/vm/heap/table_pages` before/after; shell allocates nothing.

## Positive and negative tests

**Repository** `tests/repository/test_shell_output.py` (7 tests): valid synthetic, every-line-required, structural damage (empty/garbage/extra/`RynorOS 9.9.9`/`free_bytes=1`/missing `started`/`×2`/`\xff`), accounting mismatch, script well-formed (`KEY_BUDGET==39`, `digest_hex`), `-O` fails closed, stage10 early rejected.

**Integration** `test_shell.py` (8 test methods): two positive real-key sessions (the default script and a different host-selected 39-key script) plus six negative guest mutations covering canned output, dispatch bypass, runtime-service bypass, tokenizer bypass, low-byte-only count decoding, and a realistic keyboard-draining canned transcript.

**Mutation sensitivity** (post-repair, manual copied-fixture probes):
* `SHELL_TOO_MANY` alias (`3` positive) → now `-3`, 13-token `exec_too_many` fails old `argc==3` path.
* Remove `kstr_nlen==cap` → unterminated accepted → `exec_unterminated` fails.
* Remove `offset>=cap` → wrap (`cap - offset` huge) → `KSTR_TERMINATION` path still `SHELL_INVALID` but synthetic `exec_unterminated` distinguishes.
* Remove `n<48` → `out[48]` OOB with `n==48` (synthetic `UPPER` `40` clamp hides, but direct `krst` with `48` would corrupt).
* `COUNT` low-byte (`n[0]`) → `300` test fails (`44` vs `300`).
* Remove `nlen==8` → short digest accepted.
* Extra args `echo hi extra` → `SHELL_ARGS` (`extra-args` 6 cases) — old `argc>=2` would have returned `SHELL_OK`.
* `E0` ignored → keypad Enter `0xe0 0x1c` would become `0x1c` `Enter` (now suppressed).
* `shell_execute` no-op → `canned [SHELL] interactive verified` fails exact `20`+`93`-line match.
* `bypass krst_call` for `upper` → the dedicated shell mutation is rejected by the exact synthetic transcript.
* A canned implementation that drains two real keyboard events per host key still fails the exact interactive transcript, proving byte counts alone are insufficient.
* The alternate host-selected 39-key script is passed into both QEMU injection and transcript generation; the default fixed transcript cannot satisfy it.

## Verification (post-repair, on `55b6c86` + shell hardening)

An independent final review rejected the initial completion report before this
verification. Discovery found `155`, not `152`, integration test methods. The
purported host-variable test was still injecting the default script and matched
`echo hi` from the synthetic section; the harness is now script-parameterized
and the assertion is restricted to the interactive section. The realistic
canned mutation initially failed incidentally at `kb_counts`; it now drains both
make and break bytes and is rejected for the intended interactive transcript
mismatch. Finally, the 4096 MiB PMM run inherited a 10-second smoke deadline and
timed out after reaching Stage 10; the documented matrix-wide 30-second bound is
now explicit, and the matrix method plus the final combined check pass.

All commands use Python 3.14.3, Clang/LLD 23.1.0, NASM 3.02, QEMU 11.1.0 (`pc-i440fx-10.0` `qemu64` `TCG tb-size=32`, `SeaBIOS` `bios-256k.bin`). `RYNOR_CLANG`/`RYNOR_LLD`/`RYNOR_QEMU` as in `bootstrap-dependencies.md`.

| Command | Observed result |
|---|---|
| `python tools/build/build.py build` | PASS, 1 MiB image, payload sectors, `rynoros-resources.zip` |
| `python tools/build/build.py boot-test --timeout 30` | PASS, full serial through shell synthetic (non-interactive) + `PMM post-IRQ accounting verified`, `monitor-quit` `reaped` |
| `python tools/build/build.py test` | 110 tests, OK (includes 7 shell repository) |
| `python -m unittest discover -s tests/integration -p test_shell.py` | 8 test methods: 2 positive real-key sessions + 6 mutation-negative guests |
| `python tools/build/build.py integration-test` | 155 test methods (147 non-shell + 8 shell) |
| `python tools/build/build.py validate` | PASS, `schema 11` `stage 11` `kernel-shell` |
| `python tools/build/build.py check` | PASS: 110 repository + 155 integration test methods in one combined run (`867.959s` wall time) |
| `git diff --check` | PASS |
| `git status --short` | Intentionally dirty Stage 11 implementation, test, harness, and documentation changes before milestone commit |

QEMU matrix (normal image, now includes shell synthetic, `30s` deadline): `8/16/64/128/256/512/4096` MiB, `max` CPU, `low-32` (32 MiB below 4G) — all PASS with `monitor-quit` `reaped`. No `qemu-system` process remained.

Reproducibility: two consecutive `build` produced byte-identical `boot.bin`, `rynorkernel.elf`, `rynorkernel.bin`, `rynoros.img`, `rynoros-resources.zip`, `build-manifest.json`.

## Limits

* Single CPU, `PIC`/`PIT`, `PS/2` only; no `APIC`/`HPET`/`USB`/`SMP`.
* `64`-byte line, `12`-token, `a–z0–9`+`space` only, no tab/history/cursor/UTF-8.
* `clear` is a serial-only redraw-request stub; it is not a framebuffer clear or a console redraw.
* `E1` Pause well-formed `6`-byte works, malformed loses ≤4 keys (never sent by harness).
* `KBD_LOST` halts fail-closed (not resync).
* QEMU TCG only; no bare-metal USB/APIC/SMP validation.
