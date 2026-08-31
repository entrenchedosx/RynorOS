# Stage 9: framebuffer/text output

The incoming Stage 9 implementation required repair. Its reported success was
not sufficient: text indexing/wrap, MMIO ownership/cache policy, device metadata
provenance and verification had defects. See the [independent audit](stage9-audit.md)
for findings, reproductions and final verification; that report supersedes the
original completion claim. The [design contract](../design/framebuffer.md)
describes the repaired implementation and deliberately limited hardware scope.

The direct BGA path targets QEMU standard VGA 1234:1111 at 00:02.0. It is a
deliberate narrow bootstrap choice, **not** evidence that BIOS VBE is broken.
The original attempt incorrectly expected a VESA signature from INT 10h/4F01
mode information; that signature belongs to 4F00 controller information.

Observed normal configuration: physical BAR0 `0xfd000000` (discovered, not
hardcoded), aperture 16 MiB, 1024x768x32 BGRX, pitch 4096, visible extent
3,145,728 bytes, 768 pages at `0xfffffe8000000000`. Mapping retains four PMM
table pages; final allocated bytes are 122880 (14 tables plus 16 heap pages).
A positive variant uses virtual width 1040, pitch 4160. Scanout remains 1024x768.

Version-2 handoff has 64 bytes in a reserved, read-only/NX kernel page at 0x5000.
Boot checks PCI BAR size/type/decode and BGA state; kernel independently matches
device state before mapping. MMIO uses uncached supervisor RW/NX leaves, never
PMM-owned RAM. Rendering uses volatile accesses and bounded/clipped arithmetic.
The implemented 42-glyph subset is uppercase letters, digits and documented
punctuation/space; text supports newline/carriage return, not terminal semantics.

Guest tests include guarded-buffer drawing and real PMM exhaustion/rollback.
Host tests compare complete physical framebuffer bytes and actual QEMU scanout,
including glyphs. Serial diagnostics and Stage 1–8 regression gates remain.
No GUI, desktop, mouse, mode arbitration, lowercase/Unicode, PNG rendering,
generic GPU support, physical-hardware verification or interactive console is
claimed. The icon remains a separately packaged canonical OS resource.

Stage 8/9 and audit changes were left uncommitted at audit completion, as
requested. The user subsequently authorized documentation verification and
publication. The audit report preserves the original review-state evidence;
publication does not rewrite the baseline history.
