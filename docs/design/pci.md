# PCI-A1: PCI discovery + BAR resource mapping

Status: **complete (verified).** Internal kernel infrastructure only:
no CPL3 surface, no ACPI. DMA buffers landed as DMA-A1
(see `dma.md`); MSI/MSI-X delivery landed as INT-A2 (see `msi.md`);
the first driver (xHCI command engine) landed as xHCI-A1
(see `xhci.md`). Future USB enumeration, storage,
NIC, audio, and GPU drivers bind through the registry and mapping
API defined here.

## Transport

`pci_cfg_read/write8/16/32` (`kernel/drivers/pci.c`) is the only
configuration-space path. A `pci_cfg_ops` backend (dword read/write)
defaults to legacy Configuration Mechanism #1 (`0xCF8/0xCFC`), which
is the correct bootstrap on the `pc-i440fx` target: no ACPI/MCFG
exists here yet, and `display.c` already proves the ports. The CF8/CFC
address/data pair is serialized with an IRQ save/cli/restore critical
section (single-CPU correct from any IRQ state; shared with the
display's local reader).

Validation fails closed: out-of-range bus/device/function/offset
reads return all-ones (absent-device semantics), invalid writes are
ignored. Word accesses require natural alignment and must fit inside
the 256-byte space; dword accesses require dword alignment.

`pci_cfg_install_ops` swaps the backend (test mock only; `NULL`
restores legacy). A future PCIe ECAM/MCFG transport implements the
same two function pointers; enumeration, BAR, and mapping code does
not change.

## Enumeration

Iterative bridge-aware walk, deterministic bus → device → function
order:

```text
queue = [bus 0]; visited = {bus 0}
while queue:
    bus = pop
    for dev in 0..31:
        if vendor(fn0) == 0xFFFF: continue
        maxfn = 7 if header(fn0).MF else 0
        for fn in 0..maxfn:
            if vendor(fn) == 0xFFFF: continue
            record identity + BARs (+ bridge windows)
            if bridge and sec valid and sec not visited: enqueue sec
```

Rules: absent = `vendor_id == 0xFFFF` (checked at device level, then
per function for multifunction devices only); single-function
devices scan fn 0 alone. Bridge windows are validated before use:
`sec == 0`, `sec > sub`, `sec == bus`, and revisits are ignored
(registered, never followed). Bounds: 32-entry bus queue, 256-bit
visited set, 16-device registry (`PCI_FULL` stops the scan but still
boots with a partial registry).

Only bridge-claimed buses are visited: a device on an unclaimed bus
is invisible by design (pinned by the mock bus-3 fixture). CF8 could
address all 256 buses directly, but claimed-only traversal keeps
scans deterministic, fast, and bridge-semantics-honest; a rescan-all
fallback can be added later if firmware proves untrustworthy.

## Device representation

`struct pci_device`: BDF, vendor/device IDs, class/subclass/prog-if/
revision, header type (MF reported separately), enumeration-time
command/status, IRQ line/pin bytes (metadata only, no routing),
bridge secondary/subordinate, and up to 4 decoded BAR entries.
No pointers to transient buffers; no kernel pointers leave the
kernel (no userspace API in this slice).

Registry queries: `pci_device_at` (ordinal), `pci_find_bdf`,
`pci_find_vendor_device` (with start ordinal for iteration),
`pci_find_class` (each field exact or `PCI_MATCH_ANY`). Returned
pointers borrow the registry and are invalidated by the next
`pci_initialize` (boot calls it once; the gated test re-scans only
before any mapping exists).

Bounds (flat-BIOS-window driven, all fail-safe): 16 devices, 4 BAR
entries per device (6 config slots scanned; a 64-bit BAR consumes
two slots but stores one entry), 32 buses. Verified adequate for
QEMU (10 functions worst case here); raise when the window allows.

## BAR decoding

Endpoints decode slots 0-5, bridges slots 0-1; other header layouts
register identity-only. ROM BARs (`0x30`) and bridge windows are
never size-probed. Per slot:

- raw 0 → unimplemented, skipped.
- bit 0 set → I/O: base = raw & `0xFFFFFFFC`, 16-bit space enforced
  (base ≤ `0xFFFF`, size ≤ `0x10000`).
- else memory: type bits 2:1 (`10` = 64-bit, else 32-bit; legacy
  `01` decodes as 32-bit), prefetchable bit 3, base = raw &
  `0xFFFFFFF0` (+ high half << 32 for 64-bit, consuming the next
  slot; a 64-bit flag on the last slot is malformed and skipped).

## BAR sizing (safe sequence)

For each candidate BAR, exactly:

```text
save BAR low (+ high), save COMMAND
COMMAND &= ~(IO | MEM)          # decode off, nothing else touched
write 0xFFFFFFFF to BAR (+ high)
read size mask(s)
restore BAR (+ high)            # data path first ...
restore COMMAND exactly         # ... then re-enable decode
re-read BAR (+ high) + COMMAND: mismatch drops the BAR
derive + validate size
```

Size math: `size = (~(mask & addr_mask) + 1)` in the BAR width;
combined 64-bit mask for 64-bit BARs. Reject: zero mask/size,
non-power-of-two, `base % size != 0`, `base + size` wrap (32-bit
range must fit in 4G; I/O inside 64K). Restored values are re-read
and compared before the entry is stored, so a broken probe (or a
mutant that drops a restore) loses the BAR instead of corrupting
device state. The gated test additionally re-reads every BAR and
COMMAND after enumeration and requires bit equality (`restore` rows).

PCI-A1 never sets `BUS_MASTER`, never enables decoding globally,
never writes device MMIO, and never touches interrupt modes: sizing
is the only config-space write path, and it is fully restored.
MSI/MSI-X programming (INTX_DISABLE, message registers) lives in
INT-A2's `msi.c` on top of this transport (see `msi.md`).

## MMIO mapping

`pci_map_bar(bus, dev, fn, entry, &va)` maps one decoded MMIO entry
through `vm_map_device` (supervisor RW/NX, uncached PCD|PWT):

- refuses I/O BARs, unimplemented entries, bad ordinals/BDF;
- overflow-checked page span (`offset + size`, no `+4095` wrap);
- VA from a bump cursor inside slot 509 above the display carve-up
  (`PCI_MMIO_VA_BASE = VM_MMIO_BASE + 256MiB`; the display owns at
  most 256MiB from the base, so regions cannot collide);
- `vm_map_device` re-validates (slot range, non-RAM physical,
  no double-map) and rolls back partial maps;
- records `mapped_va` (page-offset-adjusted); repeat maps return it;
- `pci_unmap_bar` tears down via `vm_unmap_device` (double-unmap is
  an error; the VA cursor never rewinds — documented lifetime).

Cache policy: unconditionally uncacheable (the only policy
`vm_map_device` offers; PAT-verified by the VMM). Correct and slow:
a framebuffer or DMA ring eventually wants write-combining, which
needs PAT/PAT-management work outside this slice. No
physical==virtual assumption anywhere: every BAR goes through this
mapping, including ones the display already mapped elsewhere.

## Class names

`pci_class_name` maps common classes to short diagnostic names
(`storage`/`ide`/`sata`/`nvme`, `network`, `display`,
`multimedia`, `bridge`/`host-bridge`/`isa-bridge`/`pci-bridge`,
`serial-bus`/`usb`); everything else is `"unknown"` and still a
fully valid device. No device-name database.

## Verification

- `pci_self_test` (gated `RYNOR_PCI_TEST` images; silent builds
  print nothing): mock-transport unit rows, 14-device synthetic
  topology (multifunction, nested bridges, malformed ranges/loops,
  visited-bus cycle guard, unknown class, 32/64/IO/zero/malformed
  BARs, restoration, re-scan determinism), then live QEMU evidence
  rows, per-device
  restoration re-checks, map/query/unmap proofs for every MMIO BAR
  (only the VGA framebuffer is ever read, cross-checked against
  the display path), stats, and `[PCI] pci verified`.
- Host: `tools/host/pci_output.py` grammar + `boot_output.py`
  strip/validate; `tests/integration/test_pci.py` pins both QEMU
  topologies against independent HMP `info pci` captures, exact
  mapping VAs, stats, and 14 check-removal mutants (M12 dropped:
  unobservable — all firmware BAR bases are page-aligned, so a lost
  page offset changes nothing on this hardware). M1/M2/M4/M5/M7-M11
  fire in the mock phase (guest failure tags); M3/M6/M13-M15 fire on
  live hardware (evidence mismatch or gate tags).
- `tests/repository/test_pci_abi.py` pins offsets, masks, bounds,
  enum order, API surface, and the no-userspace-surface rule.

## Non-goals (explicit)

USB enumeration, AHCI, NVMe, NIC/GPU/audio drivers,
IOMMU, hotplug, power management, ACPI (including MCFG
discovery for a future ECAM transport), PCIe extended
capabilities, SR-IOV, interrupt routing. BAR entry/device bounds
(4/16) are window-driven and documented raisable.

## Window pressure (measured)

The flat BIOS window (`0x8000-0x70000`) held only 3,687 bytes of
slack at P1-A3. PCI-A1 (~20KB text/rodata/BSS) fits only because
test-only TUs now compile `-Os` (freed ~37KB; every self-test
re-verified under `-Os` by the full QEMU suites). BOOT-A1 removed
this window: the kernel loads high with an 8 MiB file / 16 MiB memory
budget (see `boot.md`), so later slices size against that instead.
