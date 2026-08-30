# RynorOS

RynorOS is a new hobby operating-system project. Its intended kernel is
**Rynorkernel**, and its intended native programming language is **RynorLang**
(source extension **`.rl`**).

The long-term direction is a small, self-contained, integrated system inspired
by TempleOS's simplicity and immediacy, not by reusing its implementation.
RynorOS will not be based on Linux, BSD, another kernel, or an existing OS userspace.

## Current status: repository foundation only

Implemented: repository layout, project metadata, documentation, host-side
validation, and repository tests. There is **no bootable kernel, compiler,
shell, filesystem, userspace, or native application execution** yet. Language
examples describe proposed syntax and cannot currently run.

Status labels throughout the project:

- **Implemented**: present and verified by the stated checks.
- **Planned**: intended work, not executable functionality.
- **Experimental**: unresolved design proposals, not compatibility promises.

## Host commands

Requires Python 3.10 or newer (standard library only). Run from the repository:

```text
python tools/build/build.py validate
python tools/build/build.py build
python tools/build/build.py test
python tools/build/build.py check
```

`validate` checks required paths and metadata. `build` validates and compiles
the available host Python sources to bytecode in a temporary directory, then
removes those temporary files; it produces no OS image. `test` runs repository
tests. `check` runs build (including validation) and tests, stopping on failure.
All commands return nonzero on failure and work from other working directories
when invoked using the script's absolute path.

## Layout

| Path | Responsibility | Status |
| --- | --- | --- |
| `boot/` | Firmware/loader handoff | Planned |
| `kernel/` | Rynorkernel hardware and resource management | Planned |
| `rynorlang/` | Language design and future toolchain | Experimental design |
| `user/` | Future shell, libraries, applications | Planned |
| `tools/` | Host validation and build entry point | Implemented foundation |
| `tests/repository/` | Repository validation tests | Implemented |
| `tests/kernel/`, `tests/rynorlang/`, `tests/integration/` | Future system tests | Planned |
| `docs/design/`, `docs/reports/` | Decisions, subsystem template, verification reports | Foundation docs |
| `build/` | Ignored future generated output; `.gitkeep` retained | Reserved |

Start with [architecture](ARCHITECTURE.md), [roadmap](ROADMAP.md),
[RynorLang design](rynorlang/README.md), and
[bootstrap dependencies](docs/design/bootstrap-dependencies.md).
[project.json](project.json) is machine-readable foundation metadata.

## License and contributions

The initial repository uses the [MIT license](LICENSE).
See [CONTRIBUTING.md](CONTRIBUTING.md) for scope, status, test, and commit rules.
