# Stage 14 report — RynorLang stable AST and semantics

## Outcome

Stage 14 is a host-side Python 3.10+ semantic-analysis bootstrap. It lowers the Stage 13 temporary syntax tree into a stable, JSON-compatible AST, resolves names, and checks the frozen `int`/`bool`/`str`/`unit` rules. It does not interpret, compile, generate code, or execute RynorLang, and it changes no boot or kernel source.

## Front-end alignment

The committed Stage 12/13 front end was rechecked:

- `!` is lexed as `BANG`; maximal munch selects `!=` as `BANG_EQ` first.
- Function return annotations use `:`. The lexer tokenizes `->`, but the parser rejects it in a function signature.
- `let` and parameter annotations require `:`.
- Parameter and argument lists reject trailing commas.
- Binary precedence is `||`, `&&`, equality, relational, additive, multiplicative; binary operators associate left and unary operators associate right.
- `else` binds to the nearest unmatched `if`.

The authoritative grammar remains [the Stage 13 parser design](../design/rynorlang-parser.md).

## Implementation

`tools/rynorlang/analyze.py` provides `analyze`, `analyze_bytes`, `analyze_file`, and `analyze_tokens`. Its AST has 16 node kinds documented in [the AST design](../design/rynorlang-ast.md). The schema is stable and deterministic; the returned dictionaries and lists are caller-owned mutable JSON structures rather than immutable Python nodes.

The analyzer builds the complete global function table before validating parameters, making the no-shadowing rule independent of function declaration order. Locals are block scoped and visible only after declaration. Forward function references and self-recursion resolve; local forward references do not.

Semantic failures use exactly five codes: `SEM_UNDECLARED`, `SEM_DUPLICATE`, `SEM_TYPE_MISMATCH`, `SEM_ARITY_MISMATCH`, and `SEM_UNKNOWN_FUNCTION`.

Each semantic diagnostic includes a real source span and structured `expected` and `got` detail, with applicable name, callee, context, or operator fields. Analysis stops at the first diagnostic and returns no partial AST.

Multiline AST end positions are calculated from source text (or the final covered token for `analyze_tokens`); byte length is not incorrectly treated as a same-line column count.

## Tests and audit repairs

`tests/repository/test_rynorlang_semantics.py` contains 42 test methods. The fixture inventory is exactly 12 valid and 20 invalid programs. Every invalid fixture is mapped by filename to its exact frozen semantic code and must include span plus expected/got detail.

The audit repaired four candidate defects:

1. Parameter names were checked against only functions already seen, so a parameter could shadow a later function. Validation now occurs after the complete global table is built.
2. Semantic diagnostics documented structured expected/got fields but returned neither. The implementation and fixture tests now enforce them.
3. Multiline `end` line/column values were fabricated using `start.column + byte_length`. End positions are now source-accurate.
4. Candidate mutation tests renamed diagnostic constants and treated surviving mutations as skips. Seven temporary-copy mutations now remove actual enforcement for scope lookup, duplicate functions, arithmetic typing, arity, unknown functions, no-shadowing, and unit-as-value. Each mutant must exhibit the invalid behavior targeted by the corresponding normal rule test.

Additional robustness probes cover a 300-deep expression (`PAR_DEPTH_EXCEEDED`), input over 1 MiB (`PAR_FILE_TOO_LARGE`), 20 deterministic random garbage inputs (one clean diagnostic, no traceback), invalid file API input, missing-file CLI exit 2, and three identical CLI dumps.

The repository method count is 253: 110 pre-language tests, 49 lexer tests, 52 parser tests, and 42 semantic tests. The language suites have zero skips. The unchanged kernel integration suite contains 155 test methods; it is a regression gate, not evidence that the host analyzer executes in QEMU.

## Verification

The targeted Stage 14 command is:

```text
python -B -m unittest discover -s tests/repository -p 'test_rynorlang_semantics.py' -v
```

The full project gate is:

```text
python tools/build/build.py check
```

Reference-host verification on 2026-09-03 used Clang/LLD 23.1.0, NASM 3.02, QEMU 11.1.0 TCG, and Python 3.14. The complete `check` command exited 0: metadata validation and host compilation passed, 253/253 repository tests passed, and 155/155 integration tests passed in 817.476 seconds. Those integrations exercised the established nine-configuration matrix (8, 16, 64, 128, 256, 512, and 4096 MiB; `max` CPU; and RAM above 4 GiB through a 32 MiB below-4-GiB hole). Every QEMU process was reaped.

Two subsequent normal builds produced byte-identical `boot.bin`, `rynorkernel.bin`, `rynorkernel.elf`, `rynoros.img`, and `rynoros-resources.zip`. Their SHA-256 values are recorded in `build/build-manifest.json`; the image hash for this source state was `517bdf615037ab26dd5f48dd0ef280bffb287b24c4be949b6c1cdd26448d09a6`.

## Limitations

- No type inference or implicit conversions.
- No all-paths-return check: a function declared `: int` can currently fall through and still analyze successfully.
- No shadowing, by frozen language design.
- No builtins, modules, imports, composite types, generics, constant folding, or multi-error recovery.
- No interpretation, code generation, compiler, self-hosting, kernel integration, or RynorLang execution.
- QEMU and physical hardware do not execute this host-side analyzer; kernel QEMU tests are regression-only.
