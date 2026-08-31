# Independent Stage 9 audit and repair

## Scope and incoming state

HEAD/main: `061238e48d9594f75aeb5a53deb91a660025e269`. Incoming tree:
26 modified tracked files and 18 untracked files, including the audited,
uncommitted Stage 8. During the audit, commit/push was not authorized and Git
history was left untouched. The user subsequently authorized documentation
verification and publication; the Git states below are audit-time snapshots,
not assertions about the repository after publication.
The incoming source is preserved in ignored `build/stage9-incoming.zip`.

The scope is validated framebuffer/text output, not windowing, compositing,
mouse support, a console, or a desktop. Stage 7 is not being redesigned.

### Final review inventory

31 modified tracked files and 23 untracked files; zero staged. These include
the original Stage 8/9 changes, not 54 files newly authored by this audit.
No unrelated personal asset was added or deleted. The original icon is unchanged.

Modified tracked files:

```text
ARCHITECTURE.md
README.md
ROADMAP.md
assets/README.md
boot/README.md
boot/transition.asm
docs/design/bootstrap-dependencies.md
docs/design/irq-timer.md
docs/design/virtual-memory.md
kernel/README.md
kernel/arch/x86_64/linker.ld
kernel/core/main.c
kernel/include/boot_memory.h
kernel/include/io.h
kernel/include/vm.h
kernel/mm/pmm.c
kernel/mm/vm.c
project.json
tests/README.md
tests/integration/test_boot.py
tests/integration/test_heap.py
tests/integration/test_pmm.py
tests/integration/test_scheduler.py
tests/integration/test_vm.py
tests/repository/test_pmm_output.py
tests/repository/test_timer_output.py
tools/README.md
tools/host/boot_output.py
tools/host/image.py
tools/host/qemu.py
tools/host/repository.py
```

Untracked files retained for review:

```text
docs/design/framebuffer.md
docs/design/keyboard.md
docs/reports/stage8-audit.md
docs/reports/stage8.md
docs/reports/stage9-audit.md
docs/reports/stage9.md
kernel/drivers/display-font.h
kernel/drivers/display-internal.h
kernel/drivers/display-surface-test.c
kernel/drivers/display-surface.c
kernel/drivers/display-test.c
kernel/drivers/display.c
kernel/drivers/keyboard-internal.h
kernel/drivers/keyboard-test.c
kernel/drivers/keyboard.c
kernel/include/display.h
kernel/include/kbd.h
tests/integration/test_display.py
tests/integration/test_keyboard.py
tests/repository/test_fb_output.py
tests/repository/test_kbd_output.py
tools/host/display_output.py
tools/host/kbd_output.py
```

## Confirmed incoming findings

| Severity | Location | Evidence / impact |
| --- | --- | --- |
| HIGH | vm.c physical_allowed | `state != FREE` accepts ALLOCATED RAM, including table frames, as foreign MMIO. An independent temporary-image probe successfully mapped and unmapped a PMM allocation with vm_map_device. |
| HIGH | vm.c range_valid | Ordinary map/unmap/protect permits PML4 509 despite the exclusive-MMIO contract. Ordinary protect can change device permissions; ordinary unmap can invalidate the driver's retained pointer. |
| HIGH | display.c initialization | Reserved/unavailable does not identify a framebuffer: no aperture-length proof or independent device match. A corrupted base can target unrelated reserved/kernel memory. |
| HIGH | transition.asm acquire_vbe | Assumes BDF 00:02.0 without checking device ID/class, BAR type/size, decode enable, BGA identity/enable, virtual stride or display offsets. BAR masking is 16-byte, not the claimed 4-KiB validation. |
| HIGH | display.c text | 32-bit x/character and y/row addition wraps; invalid large coordinates can land in valid pixels. Text length is unbounded; later invalid bytes can leave partial output. |
| HIGH | display.c font | Most accepted characters have blank glyphs; digit/letter indexing is shifted. An accepted character is not evidence of implemented text output. |
| HIGH | vm.c device leaves | Device mappings inherit ordinary RAM cache bits without an explicit validated MMIO memory type. |
| MEDIUM | display.c pixel/read/rect | Casts discard volatile before framebuffer accesses, contrary to its documented device-access contract. |
| TEST-QUALITY | display_output.py | Only four non-text samples checked. Removing valid text rendering passed the original full boot verifier in an independent copied-image probe. Physical memory dumping alone does not prove the display scans out that memory. |
| TEST-QUALITY | display-test.c | Claims clipping/overflow verification without testing large/clipped rectangles, padded stride, text wrap or complete changed/unchanged pixels. |
| TEST-QUALITY | test_display.py | Seven alleged validation mutations invert predicates against valid metadata. They prove rejection can execute, not that actual bad metadata fails closed. No device mapping OOM/rollback, ownership or cache tests. |
| TEST-QUALITY | test_fb_output.py | The complete-boot test never calls the imported complete-boot validator. |
| MEDIUM | qemu.py evidence | Unquoted HMP dump path breaks paths with spaces; capture adds a separate deadline; accepts oversized dumps and trusts the guest-provided physical address without checking actual scanout. |
| MEDIUM | Stage 8 trace / mutation deadlines | Cross-byte trace ordering rejected legal queued release events; a 3-second negative-probe whole-boot deadline later expired in the scheduler before keyboard testing. Neither is a valid keyboard-failure result. |
| DOCUMENTATION | reports/design/README | Incorrect VBE 4F01 signature rationale; wrong reported physical base (decimal 4244635648 is FD000000h); stale no-display/no-MMIO claims; full character-range and overflow claims exceed evidence. |

No production mock allocator or empty drawing function was found. The defect is
not that the existing rectangle clipping algorithm is intrinsically wrong:
its widened end arithmetic is sensible. The wider contracts and evidence are
insufficient, and text/device ownership contain actual defects.

## Baseline evidence

`build/stage9-original-probes.log` records original-verifier acceptance of
missing text rendering and PMM-allocated-as-MMIO mapping. The first full check
and an overlapping third probe encountered host commit-memory pressure (QEMU's
default 1-GiB TCG cache); that incomplete run is not a passing baseline.
Unrelated host training processes were not modified or terminated. Subsequent
runs use one QEMU at a time and a documented 32-MiB TCG cache.

The bounded baseline check actually produced **83 repository passes, 92
integration cases with 91 passes and one error**, not the claimed full pass.
`build/stage9-baseline-bounded.log` records the error: Stage 8's max-CPU trace
validator wrongly required a make data-port read to precede the next device
event. Real PS/2 release may queue first. The repair retains all 16 ordered
bytes, 16 IRQ1 acknowledgments, and each device->IRQ->read chain, but removes
only the invalid cross-byte non-overlap requirement. A positive queued-event
fixture and negative read-before-IRQ fixture were added. This is a concrete
regression-verifier correction, not a scheduler/keyboard redesign.

The first repaired full integration run produced **107/108 passes**. Its sole
failure was the keyboard ring-content mutation's 3-second deadline expiring
before `[KBD] self-test started` (`build/stage9-final-integration.log`). The
existing assertion correctly rejected this early failure. Negative keyboard
boots now have a bounded 12-second budget, still requiring their intended
keyboard failure. Complete CRLF-terminated `[KBD] failure=` / `[FB] failure=`
records stop the harness promptly as failures; missing/incomplete records
still time out. No success criterion, stage requirement or mutation assertion
was weakened. Final results below supersede this intermediate run.

`build/stage9-text-wrap-probe.log` additionally reproduces acceptance of
`display_draw_text(0xfffffffcU,0," A",...)`: the incoming cursor wraps and the
old complete boot verifier passes. These probes run copied images; they are
not switches in production code.

## Repairs and invariant enforcement

- Boot now proves PCI identity/class/decode/BAR type/aperture, restores probed
  configuration, checks BGA state/VRAM and obtains actual stride. Version-2
  handoff is 64 bytes, retained reserved/read-only/NX. Kernel independently
  matches current hardware before using it; malformed/mismatched metadata
  cannot publish a framebuffer pointer.
- MMIO rejects allocated/free RAM and infrastructure/ACPI/bad regions. Ordinary
  VM mutations cannot touch slot 509. Leaves use explicit UC, supervisor RW/NX
  with PAT3 checked when supported. Every actual display leaf is queried.
  Existing PMM table allocation, invalidation and ownership mechanisms remain;
  no second allocator or static table pool was introduced.
- Volatile surface operations retain full byte-extent/pitch checks, widened
  clipping and bounded text preflight. Explicit keyed glyphs replace shifted
  ASCII indexing/blank placeholders. Unsupported text fails before writes;
  right/bottom clipping and NL/CR behavior are documented, not terminal claims.
- Synthetic guarded storage runs the same production drawing algorithms and
  compares every word, including unchanged canaries/padding. It is explicitly
  not hardware evidence or a framebuffer substitute.
- Actual PMM exhaustion tests MMIO mapping with 0/1/2/3 available frames; the
  513-page request tests both hierarchy failure and installed-leaf rollback.
  Counts, absent mappings and table totals are checked before returning frames.
- Host evidence checks every BGRX byte and every RGB scanout pixel, including
  text/atlas/control sequences/edge clipping; it does not parse expected glyphs
  from guest source or memory. Padded stride and spaced paths are positive
  cases. Evidence files have exact lengths; QEMU gets one bounded boot deadline.
- Cleanup invariant failure has a named serial diagnostic before halt. The
  initial repaired bypass-map probe exposed a silent halt; that audit-created
  diagnostic gap was fixed and the same probe rerun successfully.

## Original 14 display tests: what they actually proved

| Incoming test group | Classification / weakness | Repair |
| --- | --- | --- |
| Normal framebuffer | Emulator physical-memory evidence, only four non-text samples | Full bytes plus independently observed scanout and font atlas |
| Bpp/model/masks/resolution/pitch/base-alignment/usable-region (7) | Predicate inversion on valid input, not malformed metadata or ownership | Actual boot handoff corruption and a RAM-alias mutation |
| Wrong mapping VA | Useful implementation negative | Retained; failure must reach display stage |
| Wrong border / wrong square (2) | Useful guest-readback behavioral negatives | Retained |
| Host color swap | Useful independent-byte negative | Retained with full-byte comparison |
| Guest readback skipped | Useful positive proof of host independence | Retained with scanout gate |
| Canned completion | Useful rejection of blank VRAM, not evidence text works | Retained; full pattern/text/scanout required |

The repository's complete-boot display test was effectively a concatenation
assertion. It now invokes the actual full validator and rejects misplaced,
missing, duplicated and trailing display sections. New parser/oracle cases
reject missing text, arbitrary changed pixels, pitch-padding corruption,
oversized dumps and malformed/wrong-color scanout. Fixture tests remain
explicitly synthetic, not part of the count of QEMU boots.

## Mutation and positive QEMU cases

There are 30 display integration test methods after repair, compared with the
original 14. Every negative checks that the guest reached the display stage,
the intended failure reason occurred, and QEMU exited/reaped normally. They
never pass merely because compilation, early boot or another subsystem failed.

Positive cases: normal complete framebuffer/scanout (path includes spaces),
guest readback disabled while host evidence still passes, and actual BGA
virtual width 1040 producing padded pitch 4160.

The 27 negatives include malformed bpp/model/masks/geometry/pitch/base,
wrong but plausible physical aperture, bad mapping VA, bypassed mapping,
skipped initialization, PMM allocation accepted as MMIO, ordinary slot access,
missing UC, missing NX, partial-range rollback leak, removed one/all metadata
checks, removed pixel bounds, wrong stride, wrong rectangle clipping, corrupted
glyph, no-op text, skipped drawing/readback, wrong border/square, guest-and-
oracle color swap, and canned serial completion. Missing text, corrupt glyph,
color-swap and canned completion must reach a guest success transcript but fail
the host image oracle. Local guarded tests catch algorithmic out-of-bounds
mutations before unsafe real framebuffer use.

## Assessment and remaining scope

The original approach (BGA, a bounded renderer and the existing VM/PMM) was
appropriate for Stage 9; the ownership/text defects and weak claims were not.
The fixed handoff, tables, actual writes and serial are real implementations,
not host functionality disguised as guest code. No scheduler redesign or next
milestone was introduced. No dead TODO/stub scheduler was found in Stage 9;
exception "stubs" are real assembly entry code, not placeholders.

This remains a single-CPU QEMU development kernel. Only the pinned standard-VGA
device and 32-bpp 1024x768 scanout are supported. No physical hardware test,
generic PCI/video discovery, runtime resize/hotplug, display teardown, write
combining, terminal/GUI, Unicode, lowercase or icon decoding/rendering exists.
Callers must serialize drawing and provide stable readable kernel strings.
No proof of general OS isolation or resilience to arbitrary kernel corruption
is implied. Mutation evidence is regression sensitivity, not cryptographic
attestation or exhaustive proof over every possible hardware state.

## Final verification

Final verdict: **VERIFIED within the documented Stage 9 QEMU contract**. The
incoming implementation was not defensible unchanged; the repaired code and
strengthened verification satisfy validated metadata, safe access/drawing/text
and serial retention. No confirmed Stage 9 correctness defect remains open.
This does not certify a usable OS, physical hardware, a GUI, or general GPU support.

The final sequential matrix used the dependency paths documented in
`docs/design/bootstrap-dependencies.md`; no tool was installed. Each command
below returned exit code 0. Logs are ignored build artifacts prefixed
`build/stage9-verified-` (command name plus `.log`).

| Command | Actual final result |
| --- | --- |
| `python tools/build/build.py build` | PASS: 34 host Python sources checked; kernel assembled/compiled/linked; 1 MiB image, 201 payload sectors |
| `python tools/build/build.py boot-test --timeout 30` | PASS: complete serial, device/IRQ/I/O trace, full physical framebuffer and full scanout evidence; normal QEMU exit/reap |
| `python tools/build/build.py test` | 86/86 PASS (18.319 s) |
| `python tools/build/build.py integration-test` | 108/108 PASS (666.244 s) |
| `python tools/build/build.py validate` | PASS: structure, project metadata and original icon |
| `python tools/build/build.py check` | PASS: fresh build, 86/86 repository tests (17.146 s), 108/108 integration tests (616.955 s) |
| `python -B -m unittest discover -s tests/repository -p 'test_*.py' -v` | 86/86 PASS (18.740 s); `stage9-verified-direct-repository.log` |

Each final integration run includes **30/30 display cases: 3 positive and 27
negative**. The targeted run initially had 25/26 passes because a bypassed-map
cleanup halted without its expected diagnostic; the repaired case plus four
additional negatives passed 5/5 on rerun. The later three-case deadline/normal/
cleanup rerun passed 3/3. These intermediate runs are retained, not substituted
for the final full suites.

The preserved matrix includes 8/16/64/128/256/512 MiB RAM, `qemu64` and `max`,
expected NX-disabled failure, real firmware RAM above 4 GiB with 32 MiB below
4 GiB, repeated boot/keyboard challenges, CPU exceptions, actual preemption,
heap/PMM accounting and VM fault/CR3/TLB/zeroing failures. Standard machine:
`pc-i440fx-10.0`, TCG (`tb-size=32`), one CPU, SeaBIOS, standard VGA, snapshot
IDE disk, serial file and HMP monitor, no network/parallel. Logs include every
owned process's exit/reap record. Final process inspection found **zero QEMU
processes**. No failure required killing an unrelated process.

The final diff and untracked source inventory were reviewed; `git diff --check`
passed. HEAD remains `061238e48d9594f75aeb5a53deb91a660025e269`, branch `main`,
remote `https://github.com/entrenchedosx/RynorOS`. Working tree: 31 modified
tracked files, 23 untracked files, zero staged. **No commit, push, amend,
rebase, reset, clean, or history rewrite was performed.**

### Observed normal 64-MiB framebuffer transcript

```text
[SYSTEM] RynorOS 0.1.0 | Rynorkernel | stage9 frame buffer
[FB] self-test started
[FB] metadata and guarded drawing/text tests passed
[FB] MMIO ownership, UC/NX permissions and OOM rollback verified
[FB] pattern 0 painted and read back
[FB] handoff magic=1145586246 version=2 status=1
[FB] mode=45253 width=1024 height=768 pitch=4096 bpp=32 memory_model=6
[FB] pixel maps red=16711680 green=65280 blue=255
[FB] lfb=4244635648 fb_bytes=3145728 pages=768
[FB] lfb_end=4247781376
[FB] mapped va=18446742424442109952
[FB] final allocated_bytes=122880 free_bytes=65802240 table_pages=14
[TEST] framebuffer api verified
[TEST] PMM post-IRQ accounting verified
```

This is the actual guest serial record, not the proof by itself. The normal
physical dump SHA-256 is
`5f7ac921c5ae5b7d75c560f09c7243da513e020a5be1f24a64c7385c8b670725`;
the actual PPM scanout SHA-256 is
`b5bd1bfdfcfa977fd50fed898abc535c79ea1445367058f5d7824d2bc77cc177`.
Both full contents passed the independent oracle. Evidence lives beside serial
and run.json under `build/boot-test/` and `build/fb-tests/normal with spaces/logs/`.

### Reproducibility

Two builds of the final guest sources compared byte-identically for boot.bin,
rynorkernel.bin, rynorkernel.elf, rynoros.img, rynoros-resources.zip and
build-manifest.json. The existing separate-output-directory comparison also
passed. Normal disk image SHA-256:
`24c1081171f544220f634f79000f15add9dc26c9c93cb558bcc823e3047ebb05`.
The separate resource ZIP remains
`8b4ae90b11c4912c29c14a2679e6a44bf2a87ae6e39577d1cc4deceb9b7fbb30`;
the canonical icon bytes remain unchanged. No package, kernel or image timestamp
was introduced. Same inputs/tool versions are required for this byte-identity
claim; no cross-toolchain reproducibility claim is made.

### Publication verification

After the user authorized publication, documentation was checked against the
repaired contracts and code. Stale future-display wording was corrected and
the uncommitted Git states above were explicitly labeled historical snapshots.
A fresh `python tools/build/build.py check` passed: 86 repository tests in
20.203 seconds and 108 integration tests in 679.240 seconds. Validation and
`git diff --check` passed; no QEMU processes remained. The fresh check log is
`build/publication-check.log` (ignored local evidence). GitHub authentication
was independently confirmed as `entrenchedosx`; publication uses that identity.
