# Windows Compatibility Architecture

Planned, no implementation claimed. This document defines the architectural contract for hosting a Windows-compatible execution environment under Rynorkernel without replacing it.

## Purpose

RynorOS aims to be a native operating system powered by **Rynorkernel** that can run:

```text
native RynorOS software
+
RynorLang software
+
Windows applications
+
Windows games
```

The long-term objective includes serious Windows game compatibility, including large 3D and multiplayer titles, **without** defeating or spoofing vendor security/anti-cheat. The Windows layer is hosted by and constrained by Rynorkernel, which remains the sole trusted computing base (TCB) and owner of the machine (`CR3`, `IDT`, `GDT`/`TSS`, `PMM`/`VM`, devices, `IOMMU` when present).

Inspirations are conceptual only; no NT kernel, driver, or userspace is imported. `Rynorkernel` is not “a ring above ring 0.”

## Non-goals

* Anti-cheat bypass, hiding virtualization, defeating code integrity or PatchGuard, tampering with game/anti-cheat checks.
* Claiming DirectX, driver, or game support before an executable demonstrates it.
* Replacing Rynorkernel with a Windows kernel; the Windows environment is deprivileged.
* POSIX compatibility, Wine re-hosting without attribution, or shipping proprietary Windows binaries.

## Architectural overview

```text
                         APPLICATIONS
                              │
             ┌────────────────┴────────────────┐
             │                                 │
       Native RynorOS                     Windows software
             │                                 │
             │                      ┌──────────┴──────────┐
             │                      │ Windows compatibility│
             │                      │ runtime / ABI / APIs │
             │                      │ drivers / devices   │
             │                      └──────────┬──────────┘
             │                                 │
             └────────────────┬────────────────┘
                              ▼
                        RYNORKERNEL
                              │
                    ┌─────────┼─────────┐
                    │         │         │
                   CPU       VM       devices
                    │         │         │
                    └─────────┴─────────┘
                              │
                           HARDWARE
```

```text
Hardware
   │
   ▼
Rynorkernel privileged layer  (Ring 0, owns CR3/IDT/GDT/TSS/PMM/VM/devices/IOMMU)
   │
   ├── isolation / virtualization boundary
   │
   ▼
Windows-compatible execution environment
   │
   ├── Windows user-mode compatibility (Ring 3, U/S=1, per-process PML4)
   ├── Windows kernel/driver compatibility (contained, mediated)
   └── virtual device model (virtio-GPU/virgl, Vulkan translation, or passthrough)
```

## CPU privilege vs. architectural trust boundary

Explicit distinction required in all documentation:

* **CPU privilege level** — x86-64 `CPL0` (kernel) vs `CPL3` (user). `CPL0` is maximal; no software runs “above” it. `RynorOS` today is `CPL0`-only (`GDT 0x08/0x10`, `IDT 0x8e` DPL0, no TSS/IST, no user descriptors).
* **RynorOS architectural trust boundary** — ownership of `CR3`, `IDT`, `GDT`/`TSS`, `PMM` frame allocation, `VM` table lifecycle, interrupt dispatch, device `MMIO`/`I/O` ports, and `IOMMU` programming. Code inside the boundary is the TCB; code outside cannot acquire these capabilities, regardless of ring.
* **Windows compatibility privilege model** — Windows apps/drivers expect NT semantics: `ntdll` syscall thunks → `ntoskrnl` (`SSDT`, `IRQL`, `DPC`/`APC`, `Object Manager`, `I/O Manager` with `IRP`/`MDL`). On RynorOS this is reproduced **observably**, not by changing rings: user-mode Win32 runs `CPL3` behind a `syscall`/`sysret` or `int 0x80` gate; a driver environment is either (a) a Ring-3 NT-emulation subsystem that validates handles, or (b) a whole Windows kernel in `VMX Non-Root Ring 0` with `EPT`/`NPT`.

No documentation may claim Rynorkernel is “Ring -1” unless `VMX Root` is actually used. Honest phrasing: *Rynorkernel is the sole Ring-0 TCB; the Windows environment is deprivileged to Ring 3 behind a syscall/address-space boundary (and optionally VMX Non-Root if a hypervisor is chosen).*

### Implementation options without false claims

| Pattern | Rynorkernel | Windows env | HW needed | Boundary |
|---|---|---|---|---|
| **A. Native isolated subsystem** (preferred) | Sole Ring 0, `CR3` owner | Ring 3 per-process `PML4`, `U/S=1`, `TSS.RSP0`, `syscall` gate | Rings + paging only | Syscall table + `vm_create` activation + `INVLPG`/`PCID` |
| **B. Type-1 hypervisor** | `VMX Root Ring 0` | Unmodified Windows in `VMX Non-Root Ring 0` via `EPT` | `VT-x`+`EPT`+`VPID`+`IOMMU` | `VMEXIT` hypercalls, EPT violations, vAPIC/vPIC |

Both satisfy *trust boundary ≠ CPU privilege*. `Rynorkernel` remains privileged because it exclusively owns `CR3`/`IDT`/`PMM`, never because it changes privilege semantics.

## Public interfaces

None implemented. Planned interfaces will be declared with caller obligations, ownership, error behavior, and stability per `docs/design/subsystem-template.md`.

Future surface sketch (all `research-only` until specified):

* **Loader** — `pe_validate(image, size) → PE_OK | PE_INVALID | PE_OVERFLOW | PE_CORRUPT` with tested bounds; `pe_load` creates a `vm_space` mapping.
* **Syscall gate** — `int 0x80` or `syscall` with `MSR_LSTAR`/`STAR`/`SFMASK`, `TSS.RSP0` per thread. All user pointers are `ProbeForRead/Write` validated, copied in/out, `SMAP`/`SMEP` enforced when enabled.
* **Handle/object manager** — `HANDLE` → `OBJECT_TYPE` with refcount, per-process tables, pseudo-handles.
* **Sync** — `NtCreateEvent`, `NtWaitForSingleObject`, `NtWaitForMultipleObjects` with blocking `THREAD_WAITING` queues and PIT/APIC timeout.
* **Graphics** — `IDXGIFactory`/`IDXGISwapChain` → native `virtio-gpu`/`Vulkan`/`KMS`.

Say “none implemented” until tests exist.

## Invariants

* `Rynorkernel` never exposes `PMM` frames, `VM` tables, or `PML4 509/510/511` (MMIO/window) to Windows userspace; no `W+X` leaves.
* Every Windows user `VA` is `vm_canonical` and `U/S=1`; kernel `VA` is `U/S=0`, `W^X`, `NX` enforced.
* `IF`, `CR3`, `IDT`, and `TSS` are kernel-owned; Windows code cannot modify them except via the published gate.
* `IOMMU` `DMA` is remapped; no guest DMA aliases host DRAM.
* `SMEP`/`SMAP`/`PKE` (when enabled) block `U→K` access; `handle` translation is never a raw pointer leak.
* Evidence for worker/scheduler/runtime is *host-recomputed* plus independently collected `pmemsave`/`screendump`/`-d int` traces; serial lines alone are insufficient (Stage 10 model).

Violations halt or return `STATUS_ACCESS_VIOLATION`/`STATUS_INVALID_HANDLE`/`STATUS_NOT_SUPPORTED`; never silently alias.

## Implementation status

**Implemented:** none for Windows. Foundation available: `PMM` (E820 + bitmap, 4 KiB frames, `IF=0`), `VM` (4-level 4K, `vm_create` inactive scaffolding, `VM_USER` leaves rejected for `kernel_space`), `heap` (64 KiB fixed arena), `kstack` (guard page + generation ownership), single-CPU `PIC`/`PIT ~100Hz` + round-robin `thread.c` (no wait queues, no `CR3` switch), polled `COM1`, `i8042` keyboard, `BGA 1024x768x32` LFB at `VM_MMIO_BASE` (slot 509) with `UC` `PCD|PWT` (`PAT3` `EAX[31:24]=UC` verified, not programmed).

**Planned:** Stages 21a–21m (see `ROADMAP.md` and `docs/windows-compatibility-program.md`).

**Experimental:** Exact `syscall` ABI, `GDT` user selectors (`0x1B`/`0x23`), `FSBASE`/`GSBASE` per thread, `API-set` versioning, graphics translation technology (software `llvmpipe`/`WARP` vs `virtio-gpu virgl/venus` vs `DXVK`/`vkd3d-proton` → Vulkan vs VFIO passthrough), `hive` backing.

## Dependencies

Hard gate: **Stage 18a–18b Protected userspace and loader/syscalls** (`user-mode processes, loader validation, syscalls, address-space isolation, clean exit`) and **Stage 17a/b** (block driver + native filesystem read for file-backed images). `21b/c` additionally need blocking scheduler waits (`THREAD_WAITING` + wait queues) and `CR3` switching with `TSS.RSP0`/`IST`. `21d/e` need the Stage 20c native graphics stack (native or paravirt display/GPU; Stage 9 LFB alone insufficient). `21f` needs the Stage 20e device/audio stack; `21j` needs the Stage 20d networking stack; `21g` needs the 18b native loader. `21k` (driver containment) needs `VT-x`/`AMD-V` + `EPT`/`NPT` + `IOMMU` (`VT-d`/`AMD-Vi`, `DMAR`, `ATS`), `APIC`/`MSI-X`, PCIe enumeration (`capabilities`, `Resizable BAR`) via the 20e device manager. Label all pre-18a Windows milestones `research-only`.

## Tests

Per-milestone contract (mirroring Stages 0–10 philosophy):

* **Scope** — narrow, with `dependency`, `observable behavior`, `failure semantics`.
* **Positive tests** — parse/load `PE32+` `x64` fixtures; `VirtualAlloc` reserve/commit/`PAGE_GUARD`; handle lifecycle; `WaitForSingleObject` with timeout; `CreateFile` on a disposable image; swapchain `Present` with scanout evidence.
* **Negative tests** — malformed `MZ`/`e_lfanew`/`PE`/`section`/`reloc`/`import`/`TLS`/`pdata` rejections (checked arithmetic, wrap, overlap, `W+X`); invalid `Machine!=0x8664`, `NumberOfSections>96`, `SizeOfImage` overflow; undersized/aliased `out_len` (`KRST`-style), `NULL` `out` even with zero capacity, overlap among `in`/`out`/`len`; missing `PAT3` `UC` → `VM_UNSUPPORTED` (already tested).
* **Mutation tests** — remove bounds checks, allow `W+X`, skip reloc, skip `IAT`, skip `IAT` unwind validation, remove blocking wait; each must fail at the documented stage.
* **Real executable tests** — where legally permitted, run real Windows binaries built from source or redistributables; do not ship proprietary binaries.
* **QEMU verification** — TCG `pc-i440fx-10.0` `qemu64`, `32 MiB` `tb-size`, owned-process `quit`/`reap`, byte-for-byte artifacts, `pmemsave`/`screendump`/`-d int` evidence as in Stage 10. Bare-metal validation later.

Never claim “hello.exe launched” as completeness. Never weaken a test to hide a failure.

## Known limitations / bare-metal horizons

* **Single CPU, PIC/PIT only** — no `APIC`/`HPET`, no `SMP`, no `IOMMU`, no PCIe enumeration beyond hardcoded `00:02.0`, no `DMA` API, no `PCID` shootdown.
* **No user isolation today** — no `TSS`, no `DPL3`, no `syscall` gate, no `SMEP`/`SMAP`, no `FSBASE`/`GSBASE`, no per-process `CR3` activation.
* **No filesystem** — only BIOS LBA `0x8000..0x70000`; Stage 17 `ramfs` or host-forwarded `9p` is the earliest file source for `PE` images.
* **Graphics** — only `UC` LFB; `WC`/`WT` `PAT` reprogramming, `GPU` `VRAM`/`GTT`/`PPGTT`, `KMS` atomic modeset, `virtio-gpu`/`Vulkan`/`VFIO` are all future.
* **Security** — no `TPM`/`Secure Boot`/`VBS`/`HVCI`/`PatchGuard` — see certification program for `A–E` classification.

## Remaining architectural questions

Listed in `docs/windows-compatibility-program.md` §8; not invented here. Key open choices: `syscall` vs `int 0x80` vs `SYSENTER`, `PCID` management, `FS/GS` per-thread storage, `API-set` versioning, graphics translation (software rasterizer vs Vulkan passthrough vs native `KMD`), and hypervisor vs isolated subsystem for `21k`. All are recorded as decisions to be made, not assumed.
