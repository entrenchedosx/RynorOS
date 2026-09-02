# Stage 12 report — RynorLang lexer

## Outcome

Stage 12 freezes and implements the first RynorLang lexical subset. The result
is host-side bootstrap tooling only. It does not parse, build an AST, compile,
link, load, or execute `.rl` programs, and it changes no kernel or boot code.

The initial candidate failed independent review because it duplicated a large
implementation, mixed two incompatible language designs, accepted non-frozen
syntax, continued after errors, and used equality/caller-inspection tricks that
could make incorrect results satisfy permissive tests. Those mechanisms were
removed rather than patched around.

## Implementation

- Canonical implementation: `tools/rynorlang/lex.py`.
- Bootstrap requirements: Python 3.10+ standard library only.
- Source: ASCII, at most 1,048,576 bytes.
- File reads are bounded to 1,048,577 bytes so rejection does not first load an
  arbitrarily large file into host memory.
- Comments: `//` only.
- Keywords: `fn let if else while return true false int bool str`.
- Identifiers: `[A-Za-z_][A-Za-z0-9_]*`.
- Integers: decimal magnitude through `9223372036854775807`.
- Strings: double-quoted with `\\`, `\"`, `\n`, and `\t` escapes only.
- Operators: maximal munch for `== != <= >= && || ->`, followed by the frozen
  single-character tokens.
- Locations: one-based line/column and zero-based byte offset/length.
- Errors: one exact diagnostic; scanning stops at the first error.

The public result is an immutable `LexResult(tokens, diagnostic)`. Successful
results contain a final `EOF`. Diagnostics are not tokens and diagnostic codes
do not alias one another. Oversized input is rejected before tokenization.

`rynorlang/lexer/` remains a reserved project directory with `.gitkeep`; it does
not contain a duplicate Python lexer.

## Tests

`tests/repository/test_rynorlang_lexer.py` has 49 strict test methods. The
fixture inventory is exactly 16 valid and 19 invalid `.rl` files. Tests cover:

- exact keyword and identifier classification;
- integer boundaries and overflow;
- raw and decoded strings;
- all frozen escapes and rejection of `\r`/unknown escapes;
- maximal-munch operators;
- exact token and diagnostic spans;
- non-ASCII input inside and outside strings;
- first-error termination;
- exact 1 MiB acceptance and pre-scan rejection above the limit;
- stable library types, deterministic CLI output, streams, and exit statuses;
- absence of a duplicate implementation or compatibility/hidden-test logic.

Temporary mutation copies are used to disable each required diagnostic path.
Each corresponding targeted test must fail before the correct source is
restored. Temporary copies are removed after the check.

## Verification

The repaired candidate passed the following gates on Windows with Python 3.14,
Clang/LLD 23.1.0, NASM 3.02, and QEMU 11.1.0:

- `python tools/build/build.py check`: 159 repository tests and 155 integration
  tests passed. The final integration/QEMU phase completed in 796.640 seconds.
- `python tools/build/build.py boot-test --timeout 30`: the normal kernel image
  completed and QEMU exited normally and was reaped.
- Stage 12-specific discovery: 49/49 tests passed.
- Four temporary diagnostic-removal mutations were each rejected by their
  targeted test: invalid character, integer overflow, unterminated string, and
  invalid escape. The temporary copies were removed afterward.
- Two consecutive builds produced identical SHA-256 values for all declared
  artifacts: `boot.bin`, `rynorkernel.elf`, `rynorkernel.bin`, `rynoros.img`,
  `rynoros-resources.zip`, and `build-manifest.json`.

The final image SHA-256 was
`517BDF615037AB26DD5F48DD0EF280BFFB287B24C4BE949B6C1CDD26448D09A6`;
the resource package SHA-256 was
`8B4AE90B11C4912C29C14A2679E6A44BF2A87AE6E39577D1CC4DECEB9B7FBB30`.
These QEMU runs are regression evidence for Stages 1–11, not evidence that the
host lexer executes inside RynorOS.

## Known limitations

- The lexer is not self-hosted and is not part of the RynorOS boot image.
- Unicode, block comments, numeric prefixes/separators, floating point,
  character literals, and interpolation are unsupported.
- The signed minimum value cannot yet be expressed as one integer token because
  signs are separate tokens and Stage 13 has not defined unary-minus semantics.
- There is no parser, AST, semantic analysis, compiler, runtime binding, module
  system, native program, or execution claim.
- QEMU regression tests protect the existing OS, but QEMU and physical hardware
  are not evidence for or dependencies of this host lexer.
