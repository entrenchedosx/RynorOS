# Contributing

## Scope and honesty

The current scope includes boot, serial, CPU descriptors/exceptions, PIC/PIT,
real E820/physical frames, four-level virtual memory, a bounded kernel heap and
single-CPU kernel threads/preemption,
the separately packaged icon and host verification. See
`docs/reports/stage7-audit.md` for current guarantees and limitations.
User isolation and processes are not implemented. Do not advance
the roadmap while an audit or correctness repair is still incomplete.
Do not present a design, empty function, hardcoded
demo, or TODO as working functionality. Scaffolding must say it is incomplete.
Do not import and rename another kernel or userspace. Record the provenance,
license, version, and purpose of any introduced bootstrap dependency.

## Changes

Commits created by the coding agent must use **r1ra** for both author and
committer names. Verify commit metadata and configured origin/main after push;
do not change global Git identity or rewrite unrelated history.

- Keep changes small and explain their milestone and acceptance criteria.
- Preserve unrelated work. Do not commit generated output or credentials.
- Use `.rl` for RynorLang sources; examples are not executable until tooling exists.
- Update `project.json` and validator/schema tests deliberately when metadata evolves.
- Follow `docs/design/subsystem-template.md`: purpose, public interfaces,
  invariants, implementation status, tests, and known limitations are required.
- Document unresolved ABI, storage, and ownership decisions before relying on them.

## Checks

Use Python 3.10+ with no third-party Python packages; native checks also require
NASM, Clang, LLD, and QEMU/SeaBIOS. Configure tools as documented in
`docs/design/bootstrap-dependencies.md`, then run:

```text
python tools/build/build.py check
```

New host tooling needs positive and negative tests. Future subsystem changes
need real behavior tests at the appropriate layer. Empty future test directories
are reservations, not coverage. Report exact commands and distinguish repository
validation from runtime verification. Never weaken a test merely to hide a failure.
The combined check must actually execute the kernel in QEMU. Do not skip the
boot suite when dependencies are missing. Output/logs belong under ignored
`build/`; failed fixtures belong in temporary directories. No physical disks
may be written by builds or tests. Preserve `.rl` as the language source suffix.
Exception changes must retain actual QEMU coverage of both hardware-error-code
and no-error-code frames. Never substitute `INT n` for a hardware-error exception;
that instruction does not synthesize the exception's CPU error-code slot.
IRQ changes must retain actual timer delivery, IRETQ and EOI coverage, including
masked-IRQ and missing-EOI negative cases. Never print or block in the timer IRQ.
Keep the canonical icon under `assets/`; derived assets need explicit provenance,
deterministic generation and no unnecessary kernel/boot-image embedding.

## Git

Use logical commits such as `init: create RynorOS repository structure`,
`build: add foundation validation and tests`, or `docs: record bootstrap decision`.
Configure your own author identity before committing. Review staged changes and
run checks first. Stage only files relevant to your change; avoid meaningless
commits and unrelated formatting churn. All initial code is MIT-licensed.

For every fully completed milestone, inspect the complete diff, run the full
verification suite one final time, commit only intentional milestone changes,
verify a clean working tree, verify branch/remote, and push to the configured
GitHub repository (`https://github.com/entrenchedosx/RynorOS`). Never push partial
or failing milestones. Report the commit SHA and actual push outcome. If
authentication/push fails, preserve the clean local commit and report the exact
failure; never claim that synchronization succeeded. Do not force-push unrelated
history or mix personal files into milestone commits.
