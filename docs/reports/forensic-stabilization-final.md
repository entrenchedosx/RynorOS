# Forensic stabilization audit (continuation)

Date: 2026-09-04. Baseline HEAD: `12abd9aefd3cb9602bf5ac1170467583f9df0826`
on `main`. All work described here remains uncommitted.

This report supersedes the conclusions, but does not erase the historical record,
in `kernel-hardening-sweep.md`. It distinguishes observed results from expectations.

## Corrected Muse conclusions

* The PMM search-cursor A10 claim was a false positive. The pre-change fallback
  could not dereference beyond `frame_count`: `cursor < frame_count` bounded the
  fallback; `cursor == frame_count` searched the complete bitmap; and a greater
  corrupted cursor skipped the loop before the final short-circuit. The clamp is
  retained as defensive invariant hardening, not represented as a repaired OOB.
* The lexer UTF-8-byte-span change was a regression. The public API accepts Python
  strings but the language accepts ASCII only; encoding first caused lone UTF-16
  surrogates to escape as `UnicodeEncodeError`. It now rejects them as
  `LEX_INVALID_CHAR`, and the one-MiB limit is measured directly on accepted ASCII.
* PMM/VM/heap IRQ-context rejection was valid hardening, but source-string tests
  did not establish behavior. A real PIT IRQ0 callback now calls all three APIs,
  verifies exact rejection and unchanged outputs, and three independent mutations
  prove removing each guard is detected.
* Reported counts and hypothetical reference-host results were not evidence. The
  build now enforces an exact per-module inventory: 269 repository and 162
  integration methods. Results below are only added after commands actually run.

## Confirmed defects repaired

| Subsystem | Defect | Repair | Regression/evidence |
|---|---|---|---|
| VM | Late `vm_initialize` validation failure left the new CR3, EFER, published kernel space, and allocated tables active. | Restore bootstrap CR3 and EFER, unpublish state, release all owned tables. | Forced late failure must return `VM_CORRUPT`, expose no kernel space, and restore PMM accounting. |
| Runtime | Worker indexed `W_INPUT[slot]` before validating `slot < WORKERS`. | Validate context and slot before all indexing. | Invalid worker slot mutation must stop at `worker_context`. |
| Shell | Exactly 12 tokens followed by spaces left the last token unterminated. | Replace all trailing separators with NUL after the final token. | Guest test checks the exact 12-token/trailing-space boundary. |
| Shell input | `wait_key` blindly swallowed five bytes after E1, diverging from the keyboard decoder after malformed Pause sequences. | Stateful Pause-tail validation with immediate mismatch reset. | Guest self-test exercises `E1 00 1E 9E`, a valid Pause tail, and E0 isolation. |
| Parser | Call argument nesting bypassed the parser's 256-level budget. | Enter/leave the depth budget around every call argument list. | 254 nested calls at the enclosing function/block boundary pass; 255 fail with `PAR_DEPTH_EXCEEDED`. |
| Test loader | Removing an entire required test module could leave discovery green. | Enforce reviewed method counts for every required module before execution. | An empty `test_vm.py` fixture is rejected; an inventory-shaped deliberate failing suite still propagates failure. |
| QEMU evidence | `run.json` named a command but did not identify the emulator or firmware bytes. | Use an absolute pinned firmware path and record SHA-256 plus QEMU version/path/hash. | Boot test checks QEMU provenance shape and the documented SeaBIOS hash. |
| Reproducibility | The rebuild test compared artifacts and parsed artifact metadata, not the raw manifest. | Compare `build-manifest.json` byte-for-byte too. | `test_rebuild_is_byte_identical`. |
| Test timing | The positive 8/512 MiB audit matrix inherited a 10-second default while the equivalent PMM matrix used 30 seconds; under combined-suite load the 8 MiB guest timed out while making forward progress in scheduler tests. | Give positive full-guest matrix boots the documented 30-second budget; retain short deadlines for intentional failure cases. | First final `check` reproduced the false negative at 10.144 seconds; targeted rerun and second combined check verify the repair. |

## Verification ledger

* Baseline full integration run after the first repair set: 157/157 passed in
  907.647 seconds.
* PMM/VM/heap guard-removal mutations: 3/3 produced the intended real-IRQ guest
  failure and were rejected by their integration tests.
* Repaired normal image: build passed; 30-second boot test passed with normal
  monitor quit, exit zero, and owned-process reap.
* Final repository suite: 269/269 passed in 23.019 seconds.
* Final integration/QEMU suite: 162/162 passed in 949.625 seconds. It exercised
  8/16/64/128/256/512/4096 MiB, the MAX CPU, and the low-32/high-RAM layout.
* Two independent fresh output directories produced byte-identical `boot.bin`,
  kernel ELF/binary, disk image, resource ZIP, and raw build manifest. Their
  hashes before this report's final evidence edit were recorded by the audit;
  the combined final check rebuild below is authoritative for the final tree.
* Final standalone `validate`, `build`, and 30-second `boot-test` all passed.
  The final combined `check` result is recorded after it completes.

## Continuation addendum (second pass, same worktree)

Fresh-context review of the first-pass repairs confirmed the VM rollback,
shell prefix machine, parser call-depth budget, inventory gate, and QEMU
provenance as correct, and identified seven further items, all repaired with
regression evidence:

| Item | Defect | Repair | Evidence |
|---|---|---|---|
| Parser CLI | Legal-but-wide flat trees (600 chained calls / 600-term `&&`) crashed `parse.py --json` with an unhandled `RecursionError` traceback, violating the CLI contract. | Serialize under the parser's own recursion lock/headroom; `RecursionError` maps to a located `PAR_DEPTH_EXCEEDED` instead of a traceback. | `test_54_cli_serializes_wide_flat_trees_without_traceback` (both `-O` modes, chained + chain shapes). |
| Lexer oversize bound | The 1 MiB bound is bytes, but the `str` path counted characters; non-ASCII input could reach the scanner before the size question (diagnostic-code asymmetry vs `lex_bytes`). | ASCII strings keep the exact char==byte count; only non-ASCII input skips the size check and reaches the scanner's own first-error `LEX_INVALID_CHAR` (lone surrogates included, never an encoding exception). | Existing 49 lexer tests plus forensic non-ASCII probes; behavior re-derived for oversize/exact-max/non-ASCII. |
| Test discovery dead zones | `test_x.y.py` filenames and test files placed under `tests/kernel`, `tests/rynorlang`, etc. were silently never run and no gate noticed. | `loader_errors` now rejects dotted test filenames and any `test_*.py` outside `tests/repository` / `tests/integration` before the suites run. | Mutation: adding `test_vm.extra.py` / misfiled modules must fail `test`/`integration-test`. |
| QEMU firmware pin | `share/bios-256k.bin` beside the resolved binary breaks under shim-indirected QEMU (scoop shims are not resolved by `Path.resolve`). | `_locate_firmware` resolves sibling `*.shim` redirect files and honors an explicit `RYNOR_QEMU_BIOS` override; provenance timeout raised to 15 s with `CalledProcessError`/`TimeoutExpired` mapped to `RuntimeError`. | Boot test passes with pinned hash `ae6f6aa9…` on the reference host; override path checked. |
| Shell `upper` truncation | 41+ character `upper` arguments were silently truncated to 40, contradicting the honest-rejection contract. | Reject with `SHELL_SERVICE` and `error: upper accepts at most 40 characters`; never truncate. | Guest synthetic test + host validator line + real-boot serial evidence. |
| Shell/runtime array drift | Nothing tied `W_INPUT` to the worker pool or the prefix-probe tables to their expected-decision arrays. | `_Static_assert`s added for all three pairings. | Compilation fails on drift (guest build is part of every boot). |
| Tokenizer coverage gap | The 12-token + trailing-spaces + 13th-token variant was only hand-proven. | Guest `token_full_trailing_too_many` require added. | Synthetic self-test in every normal boot. |

The repository inventory is now 270 methods (`test_rynorlang_parser` 54).
Second-pass full-suite results are recorded below after the combined `check`.

## Second-pass verification (observed on the reference host)

* `python tools/build/build.py check` → **build + 270/270 repository + 162/162
  integration passed** (combined run 900.610 s; second confirmation run
  908.679 s). The 9-configuration QEMU matrix (8/16/64/128/256/512/4096 MiB,
  MAX CPU, low-32/high-RAM hole) ran within it, every emulator reaped.
* `python tools/build/build.py test` → 270/270 in 23.926 s.
* `python tools/build/build.py boot-test --timeout 30` → normal monitor-quit,
  exit 0, reaped; serial contains the honest `error: upper accepts at most 40
  characters` rejection line.
* Byte-identical rebuild: `rynoros.img` SHA-256
  `5BB59E484F4638A08C73693EB37F929E89035F1722602ECD522AA463755DE3AE`
  reproduced across two consecutive builds.
* One transient suite failure during the second pass (the shell
  dispatch-bypass mutation test now hits the earlier long-upper rejection
  witness `r_upper_long` before `r_bogus`) was reconciled with the test's
  documented first-witness and re-run green; no test was weakened.

## Explicit verification boundaries

QEMU TCG with `pc-i440fx-10.0`, QEMU64/MAX CPUs, SeaBIOS, legacy PIC/PIT,
i8042, and standard VGA is emulator evidence, not physical-hardware evidence.
No physical machine was tested. Fixed low-memory bootstrap structures are used
before the E820 ownership check and therefore remain a potential firmware-layout
compatibility blocker. Slave-PIC EOI, production keyboard-ring overflow, alternate
display hardware, and preservation of every arithmetic RFLAGS bit lack direct
hardware-path mutation evidence. These are not silently promoted to verified.
