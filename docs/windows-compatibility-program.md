# Windows Compatibility Program — Design and Test Plan (Stages 21a–21m)

Planned program. No Windows compatibility is implemented; this document defines scope, dependencies, observable behavior, tests, and bare-metal implications for each stage. Numbering `21a`–`21m` is a working sequence after the native foundation (Stages 0–20); historical numbering is unchanged.

## Program placement

```text
Stages 0–10  implemented (boot, CPU, PIC/PIT, PMM, VM, heap, scheduler,
             keyboard, framebuffer, basic runtime)
Stage 11     Shell/monitor (ring-0; interactive input is opt-in in test images)
Stages 12–14 RynorLang lexer/parser/semantics (host)
Stage 15     Compiler ABI
Stage 16     Native .rl programs (trusted, kernel-mode bundle)
Stage 17a/b  Block storage + native filesystem (read, then write)
Stage 18a/b  Protected userspace (syscalls, isolation, native shell/lib)
Stages 19–20 Language readiness, self-hosting compiler, native tools, self-hosting OS
──────────────────────────────────────────────────────────────────────────────
Stage 21a–21m Windows Compatibility Program (this document)
```

No Windows stage is placed before `18a`+`17a/b` without an explicit `research-only` label.

## Dependency graph (text)

```text
Rynorkernel (PMM/VM/heap/kstack/scheduler/PIC/PIT/keyboard/BGA LFB)
   │
   ├── 17a Block driver (PCIe enumeration, virtio-blk or IDE, DMA/IOMMU later)
   ├── 17b Native filesystem (versioned image tool, corruption rejection)
   │
   ▼
18a Protected userspace
   ├── GDT user selectors (0x1B/0x23), TSS.RSP0/IST, IDT DPL3
   ├── syscall gate (MSR_LSTAR/STAR/SFMASK or int 0x80)
   ├── per-process vm_space (U/S=1, CR3 switch, INVLPG/PCID, SMEP/SMAP)
   ├── loader validation (ELF/PE bounds, W^X, fault-arm #PF)
   └── blocking scheduler (THREAD_WAITING, wait queues, timeout via IRQ0/APIC)
   │
   ▼
21a PE/COFF foundation  ──────────────────────────────────┐
   │  parse/validate headers, sections, relocs, imports,   │
   │  exports, TLS, pdata/xdata; malformed rejection      │
   ▼                                                      │
21b User-mode ABI foundation                               │
   │  handles, VirtualAlloc, sync primitives, env, DLL     │
   │  model, TLS, SEH substrate, blocking waits            │
   ▼                                                      │
21c Win32 API compatibility  ◄──────────────────────────────┤
   │  kernel32/NT semantics, file/memory/thread/sync       │
   ▼                                                      │
21d GUI compatibility                                      │
   │  window/input/message loop/GDI basics                 │
   ▼                                                      │
21e Graphics compatibility  ───────────┐                    │
   │  DXGI/D3D boundary, shaders,     │                    │
   │  resources, presentation        │                    │
   ▼                                │                    │
21f Audio/input/device  ──────────────┤                    │
   ▼                                │                    │
21g PE loader + DLL ecosystem  ◄─────┘                    │
   ▼                                                      │
21h Advanced runtime (SEH/VEH, fibers, TLS, overlapped)   │
   ▼                                                      │
21i Game compatibility foundation (harness)                │
   ▼                                                      │
21j Multiplayer/network (Winsock/DNS/UDP/TCP)             │
   ▼                                                      │
21k Driver compatibility environment (contained NT driver) │
   │  requires VT-x/EPT/IOMMU or Ring-3 NT emulation     │
   ▼                                                      │
21l Advanced game compatibility (matrix  app→3D→offline→online→high-perf)
   ▼                                                      │
21m Certification framework  ──────────────────────────────┘
      binary/env/API/driver/graphics/input/network/security/observed/limitations
```

`21e`/`21g` are partially independent of `21d`; `21j` can proceed after `21c`; `21k` may be implemented as a hypervisor-hosted Windows kernel rather than a re-implemented NT executive — both require `18a` and `IOMMU`.

## Stages 21a–21m — scope, observable behavior, tests, failure semantics, bare-metal

For each stage, the template is `scope | dependency | observable behavior | positive tests | negative tests | failure semantics | QEMU coverage | bare-metal requirement`. Every stage must have host and QEMU tests; serial/pmem evidence is required as in Stage 10; `build` must remain byte-identical for deterministic inputs.

### 21a — Windows executable format foundation

*Scope:* Safe PE/COFF understanding and loader abstraction, no execution.

*Dependency:* `11 Shell` (for test facilitation) + `17a/b` for file bytes; otherwise host-only parsing of in-memory buffers.

*Observable behavior:* `pe_validate(image,size)` returns `PE_OK` only for well-formed `PE32+` `x64` (`Machine 0x8664`, `Magic 0x20b`), with all header `RVA`/`Size`/`Alignment`/`Characteristics` checks; `pe_sections`, `pe_directory`, `pe_reloc`, `pe_import`, `pe_export`, `pe_tls`, `pe_unwind` accessors expose validated extents.

*Positive tests:* Well-formed minimal `PE32+` with 1–3 sections, reloc block, import of `KERNEL32.dll!ExitProcess`, export directory, TLS callback array, `pdata`/`xdata` with one `RUNTIME_FUNCTION`.

*Negative tests:* Truncated DOS/NT headers, `e_magic!=MZ`, `e_lfanew` out of bounds, `Signature!=PE`, `Machine!=0x8664`, `SizeOfHeaders` overflow, `NumberOfSections 0/>96`, section `VirtualAddress` misalignment, `RVA+Size` wrap, `W+X` section, unknown `Characteristics`, directory `RVA` outside image, reloc `Type!=10` (x64 `DIR64`) at unaligned `VA`, import `Name` not NUL-terminated, TLS `AddressOfIndex==0` but `SizeOfZeroFill` overflow, `pdata` `Begin>=End`.

*Failure semantics:* `PE_INVALID` / `PE_OVERFLOW` / `PE_CORRUPT` — caller learns no internal state; image not mapped.

*QEMU coverage:* Host parser tests only; no guest boot required (like `heap` host tests).

*Bare-metal:* None; pure arithmetic.

### 21b — Windows user-mode ABI foundation

*Scope:* Minimal NT-compatible primitives with explicit contracts: process/thread handles, virtual memory, synchronization, timers, environment.

*Dependency:* `18a` (user `CR3` + `TSS` + `syscall` + blocking waits) — otherwise `research-only`.

*Observable behavior:* `NtAllocateVirtualMemory(reserve/commit, PAGE_GUARD/NOACCESS/RW/RX/RO)`, `NtProtectVirtualMemory`, `NtFreeVirtualMemory`; handle table with `DuplicateHandle`/`CloseHandle`; `NtCreateEvent`/`Mutex`/`Semaphore`/`WaitForSingleObject`/`WaitForMultipleObjects` with `LARGE_INTEGER` timeout; `CreateThread` with `TEB`/`FSBASE`/`GSBASE` and `TLS` array.

*Positive tests:* `VirtualAlloc` reserve 64 KiB, commit 4 KiB `RW`, protect `RO`, guard-page stack growth; create/join 3 threads sharing an `Event`; `WaitForSingleObject` with `0`/`INFINITE`/`50ms` timeout.

*Negative tests:* `Reserve` with `Size % PageSize !=0`; `Commit` outside reservation; `Protect` on unmapped `VA`; `DuplicateHandle` on invalid handle; `Wait` on closed handle; `CloseHandle` on pseudo-handle; user pointer probe failure (`ProbeForRead`).

*Failure semantics:* `STATUS_ACCESS_VIOLATION`, `STATUS_INVALID_HANDLE`, `STATUS_INVALID_PARAMETER` — documented `NTSTATUS`.

*QEMU coverage:* One boot with 3 user threads, `-d int` trace shows `U/S=1` `#PF` for guard vs `STATUS_GUARD_PAGE_VIOLATION`, host `pmemsave` validates per-process `PML4`.

*Bare-metal:* `SMEP`/`SMAP` enabled when available; `FSBASE`/`GSBASE` `WRFSBASE`/`MSR_KERNEL_GS_BASE` per thread.

### 21c — Win32 API compatibility

*Scope:* First practical `kernel32`-like layer: file/memory/thread/sync/timer/console/process semantics atop `21b`.

*Dependency:* `21a`+`21b` + `17b` filesystem for `CreateFile`.

*Observable behavior:* `CreateFile`/`ReadFile`/`WriteFile` on a disposable image, `HeapCreate`/`HeapAlloc` on `VirtualAlloc`, `Sleep`/`CreateWaitableTimer`, `GetSystemInfo`.

*Positive tests:* Real `PE` built from source (`mingw`/`clang-cl` where legally permitted) that opens a file, writes, memory-maps, creates threads, waits on an event, and exits with `0`.

*Negative tests:* `CreateFile` on directory with `FILE_FLAG_BACKUP_SEMANTICS` missing; `ReadFile` on closed handle; `HeapAlloc` with `HEAP_ZERO_MEMORY` vs uninitialized.

*QEMU coverage:* Disposable `qcow2`/`raw` image test (Stage 17) with corruption rejection.

*Bare-metal:* `HPET`/`TSC` for high-res timers.

### 21d — Windows GUI compatibility

*Scope:* Separate from native RynorOS GUI: `CreateWindowEx`, `RegisterClass`, `GetMessage`/`DispatchMessage`, `GDI` `BitBlt`/`TextOut` basics, `clipboard`.

*Dependency:* `21c` + native or paravirt display (Stage 9 LFB insufficient for window manager).

*Observable behavior:* Message-loop window that paints `WM_PAINT` and handles `WM_KEYDOWN` from emulated `i8042`/`virtio-input`.

*Tests:* Host `pmemsave` of window backbuffer + `screendump` clip vs expected, as in Stage 9 but per-window.

*Bare-metal:* `KMS` atomic modeset, cursor plane.

### 21e — Windows graphics compatibility

*Scope:* Boundary for `Direct3D`/`DXGI`, shaders (`HLSL→DXBC/DXIL→SPIR-V`), GPU resources, `ExecuteCommandLists`, `Present`, fences. No `DirectX` claim before a demo.

*Dependency:* `21d` + userspace + `PCIe`/`IOMMU` + display `KMS`.

*Implementation technology (decide later):* (1) `llvmpipe`/`WARP` software rasterizer into Stage 9 LFB, (2) `virtio-gpu` `virgl`/`venus` paravirt queue, (3) `DXVK`/`vkd3d-proton` → Vulkan `vkQueueSubmit`, (4) `VFIO` passthrough/`SR-IOV` with native vendor `KMD`. See design doc.

*Observable behavior:* `D3D12CreateDevice` → `CreateCommittedResource` → `PSO` with `vs`/`ps` → `DrawInstanced` → `Present` flips to scanout with `vsync` fence.

*Tests:* `Triangle` software vs reference `ppm`; `DXGI` mode enumeration; fence signal/wait.

*Bare-metal:* Real GPU `BAR` `256MiB–32GiB`, `GTT`/`VRAM`, `PPGTT` per process, `TTM` eviction, `MSI-X`, `Resizable BAR`.

### 21f — Windows audio/input/device compatibility

*Scope:* `XInput`, `DirectInput`, `WASAPI`/`DirectSound`, `HID`, `WM_DEVICECHANGE`.

*Dependency:* `21c` + native audio (`HDA`/`AC97` or `virtio-snd`) + `virtio-input`.

*Tests:* Controller `rumble` + audio `sine` capture via `HMP` audio dump.

*Bare-metal:* `USB` `xHCI`, `HD Audio` `verbs`, `I2S`.

### 21g — PE loader + DLL ecosystem

*Scope:* In-image loader: map sections, apply `DIR64` relocs, resolve `IAT`, process `TLS` directory + callbacks under `Loader Lock`, handle `DelayLoad`, `API-sets` (`api-ms-win-*`), versioned `WinSxS` shims.

*Dependency:* `21a`+`21c` + `VirtualAlloc`.

*Tests:* `EXE` with 2 `DLL`s, `TLS` callback that increments `__tls_index`, `DelayLoad` on first call, forwarding export.

*Bare-metal:* Same as `21a`.

### 21h — Advanced Windows runtime

*Scope:* `SEH`/`VEH`/`UEF` (`KiUserExceptionDispatcher`, `RtlUnwindEx`, `x64` `pdata`), fibers, `Fls`, `overlapped` `ReadFile` + `IOCP`, named `Event`/`Mutex` (`\BaseNamedObjects\`), registry (`HKLM` hive), services `SCM` interface stubs, `QueryPerformanceCounter`.

*Dependency:* `21g` + exception dispatch (`#GP`/`#PF` → `EXCEPTION_RECORD`/`CONTEXT`).

*Tests:* `__try/__except` filter that catches `EXCEPTION_ACCESS_VIOLATION` from `*(volatile int*)0`; `IOCP` with 4 overlapped reads.

*Bare-metal:* `HPET`/`TSC` invariant.

### 21i — Windows game compatibility foundation

*Scope:* Harness measuring `startup → DLL load → graphics init → shader compile → input/audio/fs/threads/timers/sockets/controllers`. Reproducible fixtures, not “game launches”.

*Dependency:* `21a`–`21h`.

*Tests:* Fixtures: `simple PE` (no imports), `minimal D3D triangle` (software rasterizer), `offline game loop` (60 Hz present). Score is per-subsystem.

*Bare-metal:* Storage `NVMe` `4K` `I/O`, network `virtio-net` vs `e1000`.

### 21j — Multiplayer/network compatibility

*Scope:* `Winsock` `WSAStartup`/`socket`/`bind`/`connect`/`send`/`recv`/`select`/`WSAIoctl`, `getaddrinfo`, `UDP`/`TCP`, overlapped `WSASend`/`WSARecv`, `IOCP` for sockets, timers for retransmit.

*Dependency:* `21c` + `virtio-net` or `e1000` driver + `TCP/IP` stack (host-forwarded `9p`/`slirp` first, native stack later).

*Tests:* `UDP` echo, `TCP` echo with `SO_REUSEADDR`, `getaddrinfo` for `localhost`, `WSAEventSelect`.

*Bare-metal:* `PCIe` `virtio-net` vs `Intel I225` `e1000`, `IOMMU` for `DMA`, `APIC` for `MSI-X`.

### 21k — Windows driver compatibility environment

*Scope:* Major milestone. Contained NT driver semantics without exposing `Rynorkernel`.

```text
Rynorkernel
   ↓  isolation / virtualization boundary (EPT/IOMMU/handle tables)
Windows compatibility kernel/driver environment
   ↓
Windows-compatible driver interfaces (WDM/WDF IRP/MDL/DMA/PnP/NDIS)
```

*Dependency:* `18a` + `VT-x`/`AMD-V` + `EPT`/`NPT` + `IOMMU` + `APIC` + `PCIe` enumeration.

*Observable behavior:* Signed `.sys` (`WDK` test driver) `DriverEntry` → `IoCreateDevice` → `IRP_MJ_CREATE`/`READ` → `MmMapLockedPages` with `MDL` → `DMA` via `IOMMU` → `IoCompleteRequest`; `PsSetCreateProcessNotifyRoutine` receives process creation.

*Failure semantics:* Unsigned driver → `STATUS_IMAGE_CERT_REVOKED`; `MMIO` outside `BAR` → `STATUS_ACCESS_VIOLATION`; `DMA` without `IOMMU` mapping → `STATUS_INSUFFICIENT_RESOURCES`. Driver panic never corrupts Rynorkernel `PML4`.

*QEMU coverage:* `VFIO` `virtio` device passthrough test with `DMAR` table, `IOMMU` fault injection.

*Bare-metal:* `Intel VT-d`/`AMD-Vi` `DMAR`/`IVRS`, `Interrupt Remapping`, `ATS`/`PASID`, `SR-IOV`.

### 21l — Advanced game compatibility

*Scope:* Progressive workload matrix; failures tracked per subsystem.

```text
simple Windows app (21c)
      ↓
3D application (21e software rasterizer)
      ↓
offline game (21i harness, no network)
      ↓
online game (21j Winsock)
      ↓
high-performance multiplayer (21k driver + 21e native GPU)
```

*Tests:* Certification fixtures per tier; `perf` counters (`present` latency, `draw` calls/sec) with `TSC`.

*Bare-metal:* `GPU` `VRAM` pressure, `NVMe` queue depth, `10 GbE`.

### 21m — Compatibility certification framework

*Scope:* Standard test format, not a claim. Entry:

```
Application:    name + version + SHA-256
Environment:    QEMU args | bare-metal board + BIOS version
API coverage:   kernel32/ntdll/dxgi/d3d11/d3d12/xinput/winsock per import table
Driver reqs:    none | redist | .sys + signing + HVCI
Graphics reqs:  llvmpipe | virtio-gpu | Vulkan | passthrough + VRAM
Input reqs:     keyboard/mouse/gamepad + trace
Network reqs:   none | UDP | TCP | D + vendor approval
Security reqs:  A/B/C/D/E per below
Observed:       startup/DLL/graphics/shader/input/audio/fs/threads/timers/sockets
Result:         PASS/FAIL per subsystem, not binary works/doesn't
Known issues:   subsystem + NTSTATUS + trace
Performance:    present latency p50/p95, CPU/GPU %
```

A game is *supported* only when it passes the declared criteria. Never claim `Fortnite works` before D/E certification.

## Security / anti-cheat boundary — mandatory

Not a bypass project. Document honesty prevents false promises:

**Classification:**

* **A — no kernel components** — pure user-mode; only Win32 API + files/registry. Feasible with Wine-like NT emulation in Ring 3.
* **B — supported runtime deps** — A + `VC++`/`.NET`/`DirectX` redist; still user-mode.
* **C — requires kernel-driver semantics** — needs `DriverEntry`, `Ps*`, `Ob*`, `Mm` section, `IRP`, `EPT` isolation. Legitimate path: run **genuine Windows kernel inside a VM** under RynorOS hypervisor; driver loads into Windows, not into Rynorkernel. Do not re-implement NT exports as lying stubs.
* **D — requires vendor approval** — `Vanguard`/`EAC+EOS` with `EAC` kernel + attestation, `BattlEye`+`HVCI`+`TPM` need whitelisting + remote `TPM2_Quote` chain. Even with C hosting, vendor must explicitly support `RynorOS-Hv`.
* **E — unsupported** — would require hiding hypervisor (`CPUID` bit, `0x40000000` `MSR`), spoofing `TPM` `PCR`s, patching `PatchGuard`, disabling `DSE`/`HVCI`, forging `EK` cert. Document as `UNSUPPORTED — do not implement`.

**Legitimate compatibility:** Host a genuine Windows execution environment and forward its attestations. Leave `CPUID hypervisor=1`, expose `KVMKVMKVM`/`Microsoft Hv` honestly, do not accelerate `RDTSC` to hide `VMEXIT` timing. If a driver fails due to missing `HVCI`, report `STATUS_NOT_SUPPORTED` honestly.

**Isolation:** Host `RynorOS` ↔ Guest `Windows` via `EPT` + `IOMMU` + no shared writable mapping (Stage 5 `0xffffff0000000000` window is per-guest transient, never host alias) + `fTPM`/`swtpm` or `HW TPM` passthrough (`PCR` extend-only, `EK` never exportable).

**Hardest dependency:** `D` — honest `fTPM` + `measured boot` + `Secure Boot` + attestation CA (fused `EK` cert + `Microsoft DB`) and getting the vendor to trust `RynorOS-Hv`.

## Bare-metal requirements (eventual)

* **CPU virtualization** — `VT-x` (`VMXON`/`VMCS`/`EPT`/`VPID`/`Unrestricted Guest`) or `AMD-V` (`VMCB`/`NPT`), `CPUID.1:ECX[5]`, `CR4.VMXE`, `VMREAD`/`VMWRITE`/`VMLAUNCH`.
* **IOMMU** — `Intel VT-d` (`DMAR` ACPI, `4-level IOMMU page tables`, `Interrupt Remapping`) or `AMD-Vi` (`IVRS`), `ATS`/`PASID`, `IOMMU groups`, `4K`/`2M` `IOVA`.
* **APIC** — `xAPIC`/`x2APIC`, `MSI`/`MSI-X` (replaces `PIC` `IRQ0/1`), `HPET`/`TSC` invariant for `QueryPerformanceCounter`.
* **PCIe** — full enumeration (capability walk `MSI-X`/`Extended Caps`), `64-bit` prefetchable `BAR`s, `Resizable BAR`, `ACS` isolation.
* **GPU** — discrete/integrated (`Intel Xe`, `AMD RDNA`, `NVIDIA`): `VRAM` `256MiB–32GiB`, `GTT`/`VRAM` + `PPGTT`/`GGTT` + `TTM`/`GEM` eviction, `KMS` atomic (`CRTC`/`plane`/`connector`), `D3D→Vulkan` shader chain (`DXC` `HLSL→DXIL→SPIR-V`).
* **Storage** — `AHCI`/`NVMe` (`4K` sectors, `queue depth` `32–64K`), `virtio-blk` paravirt first.
* **Network/audio/USB** — `virtio-net`/`e1000`, `virtio-snd`/`HDA`, `xHCI` (`TRB` rings, `DMA`).
* **Memory** — per-process `PML4` `U/S` + `NX` + `SMEP`/`SMAP`/`PKE`/`CET`, `PCID` shootdown, `TSS` `RSP0`/`IST`.

All are `research-only` until `18a`/`17a/b` are verified; no bare-metal claim is made.

## Performance goal

Minimal `syscall`/`API` translation overhead, efficient graphics path (`vkQueueSubmit` batching), efficient `CoW`/`mmap` sharing, low `WaitFor*` overhead via blocking queues (not spin + `yield`). Benchmark only when the subsystem exists: `present` `p50`/`p95`, `draw` throughput, `fence` latency, `handle` ops/sec.

## Testing model

Mirrors Stages 0–10: per-stage `scope | dependency | observable behavior | positive tests | negative tests | failure semantics | QEMU coverage | bare-metal`. Serial + `pmemsave` + `screendump` + `-d int` `RIP`/`RSP` traces are required where applicable (Stage 10 model); `hello.exe` launching is never sufficient. Mutation tests remove each new check. Disposable images for filesystem. Byte-identical `rynoros.img`/`rynorkernel.elf` for `build` with fixed inputs.

## Performance / compatibility tracking

Framework `docs/reports/windows-compatibility-matrix.md` (future):

```
Application:    name version SHA-256
Environment:    QEMU args / bare-metal board + BIOS
API coverage:   kernel32/ntdll/dxgi/d3d11/d3d12/xinput/winsock per import
Driver reqs:    none/redist/.sys+signing+HVCI
Graphics reqs:  llvmpipe/virtio-gpu/Vulkan/passthrough+VRAM
Input reqs:     keyboard/mouse/gamepad
Network reqs:   none/UDP/TCP/D
Security reqs:  A/B/C/D/E
Observed:       startup/DLL/graphics/shader/input/audio/fs/threads
Result:         PASS/FAIL per subsystem
Known issues:   NTSTATUS + trace
Performance:    present latency, CPU/GPU %
```

No fake results.

## Remaining architectural questions

* `syscall` ABI: `int 0x80` vs `syscall`/`sysret` vs `SYSENTER`/`SYSEXIT` and `STAR`/`LSTAR`/`SFMASK` layout.
* `PCID`/`ASID` management and `TLB` shootdown strategy for per-process `CR3`.
* `FSBASE`/`GSBASE` per thread (`WRFSBASE` vs `MSR_KERNEL_GS_BASE` + `swapgs` for `KPCR`).
* `API-set` (`api-ms-win-*`) versioning and `WinSxS` (`SxS` manifest) strategy.
* Graphics translation technology: `llvmpipe` vs `virtio-gpu` `virgl`/`venus` vs `DXVK`/`vkd3d-proton` vs native `KMD` vs `VFIO` passthrough.
* Hypervisor vs native subsystem for `21k` (re-implement `WDM`/`IRP` vs host Windows kernel).
* `IOMMU` page-table sharing vs separate `IOVA` domain.
* `Registry` backing: host-backed `9p` vs in-guest `hive` vs `Wine`-style text regfiles.
* `TPM`/`Secure Boot`/`VBS` attestation chain and vendor trust for `D`.

All are recorded as open decisions, not assumed.

## Documentation truth

Use only `planned`/`research`/`prototype`/`experimentally supported`/`QEMU-verified`/`bare-metal-verified`/`vendor-supported`. Never claim a commercial game, `DirectX` feature, or kernel anti-cheat works before certification. Valid image provenance (e.g., `mingw`-built `PE`) must be disclosed; no proprietary Windows binaries are shipped.
