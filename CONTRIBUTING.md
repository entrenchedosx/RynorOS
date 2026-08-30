# Contributing

## Scope and honesty

The current scope is Stage 0. Do not present a design, empty function, hardcoded
demo, or TODO as working functionality. Scaffolding must say it is incomplete.
Do not import and rename another kernel or userspace. Record the provenance,
license, version, and purpose of any introduced bootstrap dependency.

## Changes

- Keep changes small and explain their milestone and acceptance criteria.
- Preserve unrelated work. Do not commit generated output or credentials.
- Use `.rl` for RynorLang sources; examples are not executable until tooling exists.
- Update `project.json` and validator/schema tests deliberately when metadata evolves.
- Follow `docs/design/subsystem-template.md`: purpose, public interfaces,
  invariants, implementation status, tests, and known limitations are required.
- Document unresolved ABI, storage, and ownership decisions before relying on them.

## Checks

Use Python 3.10+ with no third-party packages:

```text
python tools/build/build.py check
```

New host tooling needs positive and negative tests. Future subsystem changes
need real behavior tests at the appropriate layer. Empty future test directories
are reservations, not coverage. Report exact commands and distinguish repository
validation from runtime verification. Never weaken a test merely to hide a failure.

## Git

Use logical commits such as `init: create RynorOS repository structure`,
`build: add foundation validation and tests`, or `docs: record bootstrap decision`.
Configure your own author identity before committing. Review staged changes and
run checks first. Stage only files relevant to your change; avoid meaningless
commits and unrelated formatting churn. All initial code is MIT-licensed.
