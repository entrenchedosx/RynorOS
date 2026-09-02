# Stage 13 report — RynorLang parser

## Result

Stage 13 implements one host-side Python 3.10+ standard-library parser at
`tools/rynorlang/parse.py`. It consumes the Stage 12 lexer result, enforces the
frozen syntax in `docs/design/rynorlang-parser.md`, and returns an immutable
temporary syntax tree or one located diagnostic. No kernel, boot, architecture,
compiler, or runtime code was added.

The audited implementation uses `:` for optional function return types and
rejects `->`. It rejects trailing commas in parameter and call argument lists.
The Stage 12 lexer was corrected to emit `BANG` for standalone `!`, allowing the
documented right-associative unary operator to be parsed and tested.

## Implementation

- Recursive descent handles declarations/statements; precedence climbing handles
  binary expressions.
- Precedence is `||`, `&&`, equality, relational, additive, multiplicative,
  unary. Binary operators associate left and unary `-`/`!` associates right.
- The maximum documented grammar nesting is 256. Depth increments and decrements
  are paired by `finally`; interpreter recursion failure is fail-closed.
- `ParseNode`, `ParseDiagnostic`, and `ParseResult` are frozen dataclasses.
- Spans retain the lexer filename, one-based line/column, byte offset, and byte
  length. Supplied token streams require ordered, non-overlapping spans and one
  final EOF.
- Lexer faults map to `PAR_LEX_ERROR` or `PAR_FILE_TOO_LARGE`. Parser/API faults
  use five distinct `PAR_*` codes and stop at the first diagnostic.
- The CLI emits deterministic sorted JSON on stdout or one located diagnostic on
  stderr. It never claims to run the parsed program.

## Test evidence

Stage 13 adds 52 strict repository test methods and 35 parser fixtures: 14 valid
and 21 invalid. The invalid inventory includes explicit `->` return syntax,
single and double trailing commas, incomplete expressions/signatures, missing
punctuation/blocks, and trailing top-level input.

Five tests compile temporary mutated parser copies and demonstrate sensitivity
to removal or corruption of:

1. the parameter trailing-comma rule;
2. the colon return-type rule;
3. the depth guard;
4. additive/multiplicative precedence;
5. the full-program consumption check.

Targeted verification observed:

```text
python -B -m unittest discover -s tests/repository -p 'test_rynorlang_lexer.py'
49 tests — PASS

python -B -m unittest discover -s tests/repository -p 'test_rynorlang_parser.py'
52 tests — PASS
```

The reference Windows host ran `python tools/build/build.py check` end to end
with the documented Clang/LLD/QEMU paths. It passed repository validation, host
Python compilation, the native kernel build and boot test, all 211 repository
tests, and all 155 QEMU integration tests. The integration suite took 847.888
seconds and included the nine-configuration RAM/CPU matrix. Its rebuild test
confirmed byte-identical boot, kernel, image, resource package, and manifest
artifacts. QEMU exited normally and no QEMU process remained.

## Audit repairs

The submitted candidate was not accepted unchanged. It used `->` contrary to
the frozen grammar, omitted lexer/parser support for unary `!`, used mutable tree
dictionaries, leaked parser depth on successful paths, installed a permanent
process-wide recursion limit, and contained tests that skipped on import or
mutation failure and accepted broad outcomes. Those areas were replaced with
the strict implementation and tests described above. Candidate documentation
and fixtures were corrected to match the actual grammar.

## Limitations

Stage 13 is syntax-only bootstrap tooling. It provides no stable semantic AST,
name resolution, type checking, constant evaluation, control-flow validation,
code generation, linking, loading, kernel integration, self-hosting, or `.rl`
execution. Its QEMU regression gate proves the existing OS still boots; it does
not put Python or the parser inside RynorOS. Physical hardware was not involved.
