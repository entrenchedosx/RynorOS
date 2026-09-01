# Stage 9: validated framebuffer and bounded text

## Purpose and scope

One QEMU standard-VGA device on `pc-i440fx-10.0`, BDF 00:02.0, PCI 1234:1111.
This is framebuffer/text output, not a GUI, terminal, desktop or generic GPU
driver. No physical-hardware certification is claimed. Serial stays primary.

## Boot handoff and device ownership

`boot/transition.asm` acquires display information in real mode, before paging.
It checks PCI identity, display class, I/O and memory decoding, and a 32-bit
prefetchable BAR0. It temporarily disables memory decoding, probes the BAR mask,
and restores BAR/command before accepting or rejecting the aperture. Size must
be a power of two from 4 KiB through 256 MiB, base size-aligned above 1 MiB, and
the whole aperture below or ending at 4 GiB. This sequence is specific to the
documented device; it is not a general PCI enumeration library.

BGA ports 0x1ce/0x1cf are used to check supported ID, disable extensions, select
ID 0xb0c5, set 1024x768x32, then enable 0x41. Readbacks verify actual width,
height, bpp, enable, virtual width, zero x/y offsets and reported VRAM capacity.
Pitch comes from virtual-width readback, not an invented width-times-four.
The positive padded-stride test sets virtual width 1040 (4160-byte pitch).

The fixed linker-owned page 0x5000..0x6000 contains `boot_fb_info`, 64 bytes,
version 2. Fields: magic FBHD, version, complete/failed status, BGA ID, width,
height, pitch, bpp, direct-color model, 32-bit physical base, RGB masks,
reserved word, aperture size and PCI ID. Status is published last. The page
is reserved by PMM, identity-mapped read-only/NX by VM, and retained. It does
not contain pointers to mutable BIOS buffers. Kernel initialization repeats
metadata validation and independently matches PCI/BGA state before mapping.
Failure returns a named error; the current mandatory display self-test prints
that error and halts. There is no silent fallback or claim of successful boot.

This is **not** VBE BIOS INT 10h/4F01. The earlier report incorrectly expected
that mode-info structure to contain a VESA signature; only controller info
from 4F00 has that signature. No firmware defect was established by that test.

## Validation, VM/PMM and invariants

Only 32-bit little-endian BGRX is accepted (R=ff0000, G=ff00, B=ff).
Dimensions must be 1..4096, pitch four-byte aligned and at least width*4.
Widened height*pitch must be nonzero and at most 16 MiB; base must be page
aligned and the complete byte extent inside the verified aperture. All sums
use checked/widened arithmetic. CPU physical limits and the canonical MMIO
window are also checked by VM. Failed initialization never publishes a pointer.

PML4 slot 509 (`VM_MMIO_BASE=0xfffffe8000000000`) belongs exclusively to the
device API. Ordinary map/unmap/protect reject it. `vm_map_device` requires
kernel space, IF=0, exact WRITE permission and a range wholly inside that slot.
It rejects usable RAM (free **or allocated**), low boot memory, PMM metadata,
ACPI/NVS/persistent/bad ranges. Only undescribed holes or firmware-reserved
regions above 1 MiB are eligible; eligibility alone does not establish device
ownership, so the driver must prove an actual aperture.

Leaves are supervisor RW/NX and UC: PCD=PWT=1, PAT bit=0. These page-entry bits
select IA32_PAT index 3, the fourth byte of the MSR (`EAX[31:24]`). If CPUID
reports PAT, `vm_map_device` requires that complete byte to be UC (0) and fails
unsupported otherwise. The kernel does not modify the global PAT, negotiate a
different index, or enable write combining.
Every installed leaf is queried for correct PA/permissions/cache mode. Mapping
failure rolls back before publishing the surface. VM owns and allocates tables
through PMM; device pages are foreign, never allocated/freed through PMM.
Unmap detaches/invalidate-before-release as in the existing VM contract.

## Drawing/text API and ownership

`display.h` exposes init/error/geometry, pixel read/write, clipped rectangles,
and transparent text. Return 0 means rejection; valid operations return 1.
There is one serialized kernel caller, no drawing from IRQs, and no SMP safety
claim. No allocation occurs during drawing. Volatile 32-bit accesses preserve
device reads/writes at `y*pitch+x*4`. Public colors are RGB, stored B,G,R,zero.

The production algorithms in `display-surface.c` share a borrowed extent
descriptor with explicitly synthetic guarded-buffer tests. It is not an
allocator or emulated device. Surface extent/canonical pointers, dimensions,
pitch, origin and widened clipping calculations are validated before writes.
Pixels reject one-past-end. Rectangles reject zero size/outside origins and
clip right/bottom, including UINT32_MAX sizes; row padding is never painted.

The original font is explicitly keyed: A-Z, 0-9, space, `. - : / ?`, 5x7 ink
in transparent 8x8 cells. There are no accepted blank placeholder glyphs.
Newline resets x and advances y by eight; carriage return resets x. Right and
bottom clipping are safe, without automatic wrapping or scrolling. A whole
string is validated before drawing; unsupported characters or more than 128
characters fail without writes. Caller must supply readable kernel storage
through NUL or 129 bytes. Wide cursors cannot wrap from large coordinates.
No lowercase, Unicode, input-to-console integration or PNG renderer exists.

## Tests and evidence

Guest tests exercise actual metadata validation with invalid field fixtures;
guarded arrays check every changed/unchanged word and padding for corners,
extreme/clipped rectangles, invalid text, text limits, controls and glyph edges.
MMIO tests reject RAM aliases and ordinary edits, check real leaf permissions,
mapping conflicts, atomic unmap, and exact PMM/table accounting. Real PMM
exhaustion leaving 0/1/2/3 frames forces 513-page mapping failure at distinct
hierarchy/partial-range stages and verifies rollback and full restoration.

Actual VGA memory receives a colored pattern, all supported glyphs, control
sequences and clipped text. Guest readback checks samples; independent host
verification checks **every byte** of HMP `pmemsave`, including stride padding,
and every RGB pixel of HMP `screendump`. The latter checks actual scanout rather
than merely a guest-selected physical address. Both are mandatory, bounded by
the boot deadline, with quoted paths and owned-process shutdown/reaping.
Mutation tests remove drawing/text, break metadata/stride/clipping/glyphs/VM
ownership/cache policy, supply real corrupted boot handoffs, or print canned
success. These are reviewed-code regression probes, not attestation against an
adversary modifying both kernel and verifier. See the independent audit report
for exact observed commands, counts, failures and limitations.

## References and limitations

[QEMU standard VGA](https://www.qemu.org/docs/master/specs/standard-vga.html)
documents the device and BAR roles. The
[Intel SDM Volume 3A](https://cdrdv2-public.intel.com/835754/253668-sdm-vol-3a.pdf)
PAT memory-type rules govern the UC encoding. The
[VESA VBE 3 specification](https://www.cs.utexas.edu/~dahlin/Classes/439/ref/hardware/vbe3.pdf)
distinguishes 4F00 controller information from 4F01 mode information.
No external font library or graphics dependency was added.
The harness now requires standard VGA, its packaged firmware, HMP pmemsave and
screendump, in addition to existing keyboard trace support. TCG uses a bounded
32 MiB translation cache to avoid the host's default 1 GiB per-emulator cache.
Only this emulator/device/platform, 32-bpp mode and single-CPU ownership model
are supported. Future mode changes, device teardown, hotplug, generic GPU/PCI
drivers, write combining, terminal services and physical hardware need their
own contracts and verification. They are not Stage 9 claims.
