# INT-A1: ACPI discovery + LAPIC/IOAPIC + unified IRQ routing

Status: **complete (verified).** Modern interrupt foundation on the
BIOS/QEMU target: firmware discovery (RSDP → RSDT/XSDT → MADT), LAPIC
and IOAPIC drivers, a unified ISA/vector IRQ layer, and a verified
PIC-to-APIC transition with PIT + keyboard proofs. Internal kernel
infrastructure only: no CPL3 surface, no SMP
startup, no AML, no power management. MSI/MSI-X landed separately
as INT-A2 (see `msi.md`).

## Discovery pipeline

`acpi_discover()` (`kernel/acpi/acpi.c`) runs once, foreground-only
(`IF=0`, never in IRQ context). Stages:

```text
RSDP scan (EBDA, then 0xE0000-0xFFFFF, 16-byte steps)
  -> RSDT (rev 0) or XSDT (rev 2+, nonzero address)
  -> root scan for the MADT ("APIC") entry (bounded count)
  -> MADT parse into struct acpi_topology (by value)
```

Every stage fails closed with a sticky `acpi_error()` reason; any
failure keeps the PIC backend (`[IRQ] backend=pic`) and the boot
continues. On the pinned test machine the firmware yields RSDP rev 0,
4 RSDT entries, and a MADT with 1 CPU, 1 IOAPIC, 5 overrides, 1 NMI.

## Mapping windows

Firmware bytes are reached through two slot-509 windows, never by
touching usable RAM:

- Transient (2 pages): RSDP candidates, table-header probes. Remaps
  only on a page change, so the ROM scan costs ~129 maps, not ~8K.
- Persistent (8 pages): the whole RSDT/XSDT + MADT for the root scan
  and parse. Held for the life of the boot (1 frame + 1 table total,
  shared with the LAPIC/IOAPIC mappings).

`vm_map_firmware()` (`kernel/mm/vm.c`) is the only mapper: same MMIO
slot as devices, but the ownership rule additionally accepts
ACPI-claimed kinds and the sub-1MB BIOS area. It always maps writable
UC: `vm_query` treats a UC leaf without `PTE_WRITE` as corrupt, so a
read-only firmware window could never be queried or torn down. The
ACPI layer only ever reads through these windows; writability
satisfies the VM layer's UC-implies-writable invariant.

## MADT parsing

`acpi_parse_madt()` is a pure bounded parser (pointer + length, no
mapping, no allocation). Record types:

- 0 (LAPIC), 9 (x2APIC): CPU slots (max 16), full 32-bit IDs.
- 1 (IOAPIC): id, MMIO base, GSI base (max 8).
- 2 (override): bus/source → GSI + flags (max 32). First override
  wins per bus/source; later duplicates are counted, never applied.
- 4 (NMI): processor/flags/LINT (max 16, recorded only).
- 5 (LAPIC override): 12-byte record, address at +4; first wins.
- 3, 6, 7, 8, 10+ and vendor records: framing-validated, skipped.

Fail-closed rules: short tables, ragged roots, entry-count overflow,
record overruns, zero-length records (never loops), under-minimum
type lengths, and capacity exhaustion all fail with a precise code.
43 synthetic cases (`kernel/acpi/acpi-test.c`) pin the valid shapes
plus every attack above; the host pins the executed count.

## LAPIC driver

`apic_lapic_init()` (`kernel/interrupts/apic.c`):

1. CPUID APIC bit required; x2APIC mode refused (INT-A1 is xAPIC MMIO).
2. `IA32_APIC_BASE` must be enabled and must equal the MADT base
   (firmware/hardware cross-check); otherwise `lapic_base_mismatch`.
3. One page mapped at `APIC_LAPIC_VA`; SVR enables the LAPIC with the
   spurious vector (255); ID/version read back.

No DFR programming: INT-A1 uses physical destination mode only, which
ignores the destination format register, and strict DFR readback is
unportable (QEMU returns the 4-bit model). The SVR enable is the
liveness proof instead. Delivery proof is the ISR bit: a software
`INT` never sets it. EOI is a plain register write; after EOI the ISR
bit must be clear or the kernel halts (stuck delivery is an attack).

## IOAPIC driver

`apic_ioapic_init()` maps each MADT IOAPIC (one page each), reads
ID/VERSION (max redirection = pin count − 1), then quiesces: every
redirection entry is parked masked (vector 0, edge, high, dest 0)
before any route exists. GSI ranges must be pairwise disjoint or
init fails closed (`gsi_overlap`).

`apic_program_route()` writes high-half first while masked, then the
low half masked, and unmasks last; every program is followed by a
readback of vector/dest/trigger/polarity/mask. Mask flips go through
`apic_set_route_mask()` (read-modify-write of bit 16 only).

## IRQ core

Vectors: 0-31 exceptions, 32-47 legacy ISA (both backends), 48-127
dynamic (APIC only; the INT-A2 MSI pool), 128 syscall, 255
spurious (bare `IRETQ`, never dispatches). The allocator owns 48-127
first-fit with release/reuse; legacy vectors are claimed at route
registration.

`irq_register/irq_set_enabled/irq_set_handler` (`kernel/interrupts/irq.c`)
keep their HEAD contracts, extended with an opaque cookie:

- Unmask requires a registered route (route and handler bind
  atomically); masking is unconditional — quiescing a never-registered
  line succeeds (userspace phase relies on this). On the APIC backend
  masking an unrouted line is a no-op success: init parked every entry
  masked and only used routes are ever unmasked.
- Masking from inside a handler works (the timer masks IRQ0 on its
  third tick). No `irq_in_context()` guard on these three calls.
- Masked-window edge loss: the IOAPIC drops edge-triggered assertions
  that arrive while the entry is masked (the PIC pends them instead).
  For a level-held source like the 8042 (output buffer full), a byte
  asserted while masked latches the line high with its edge lost, so a
  later unmask sees no edge and the line goes silent forever. The
  keyboard driver therefore re-enables only via `kbd_enable()`
  (unmask first, then queue stale bytes through the ISR's exact
  classification); the periodic PIT is self-healing and needs none.

Dispatch order (`irq_dispatch`):

1. Range check (32-127) and reentrancy halt.
2. PIC spurious 7/15 with no ISR bit park silently *before* route
   lookup (HEAD order); spurious 15 still EOIs the master cascade.
3. Route lookup: missing route/handler, or a dynamic route on the PIC
   backend, prints `[IRQ] unexpected vector=N`, masks what exists,
   parks, and continues. Absence of this line on good boots is
   host-asserted.
4. Hardware proof: LAPIC ISR bit on the APIC backend, PIC ISR bit on
   the PIC backend, plus `IF=0`, zero error, `RFLAGS.IF` in the frame.
   A proof failure halts (injection, not noise).
5. Handler (vector + opaque), vector census, EOI (LAPIC or PIC with
   slave cascade handling), scheduler tick (IRQ0) or CPL3 park.

Slot 509 is deliberately never shared into user address spaces, but
IRQ dispatch runs on the user CR3 for CPL3 frames (the handler phase
needs user mappings). Every APIC MMIO primitive therefore round-trips
through the kernel CR3 when needed (`mmio_enter/mmio_exit`); the
round-trip is a no-op on the kernel CR3 and safe because `IF=0` holds
throughout and stack/code/data are shared-high mappings.

## Activation (PIC transition)

`apic_activate()`: refuse if the PIC is in service; init LAPIC, init
IOAPIC (quiesce); program each used legacy route masked; snapshot the
logical enabled set; IMCR to APIC mode if present (no-IMCR chipsets
route ISA directly — the timer proof is ground truth either way);
mask both PICs with readback verification plus an ISR-zero check;
flip `active`; restore the enabled set on the IOAPIC. Any failure
keeps the PIC backend with a precise reason.

## Proofs (all on the APIC backend, all exact-count)

- Timer: collect 5 ticks, mask, spin a quiet window (>1 tick period),
  require still 5, unmask, collect to exactly 11. Prime counts admit
  no multiple; the masked window proves masking; 11 proves delivery.
- Keyboard quiet: no new vector-33 deliveries across the proof (delta
  against a pre-proof baseline, since PIC-era IRQ1s are counted too).
- Keyboard delivery: the 8042 ECHO command returns one `0xEE` byte,
  which must arrive as exactly one vector-33 IRQ with the byte in the
  output buffer. The driver's ISR is swapped out during the probe, so
  driver state is untouched and the byte is consumed by the probe.
- Dynamic exercise: a PCI-INTx-shaped level/low route on a dead pin
  (GSI 7): register → readback → unregister (mask-before-teardown) →
  first-fit vector reuse. Covers the INT-A2 path without hardware.
- Synthetic: 43 ACPI + 120 APIC cases, host-pinned counts.

The host additionally cross-checks emulator evidence: the `-d int`
trace must show the echo's IRQ1 delivery plus port read after the
keyboard phase, and the stock keyboard trace gate requires the echo
byte/IRQ with phase separation.

## Fallback policy

The PIC is retained forever as the working fallback. `backend=pic`
with an `unavailable reason=` is a *verified end state*, not a crash:
checksum/length/capacity/mismatch failures all land there. The test
machine must take the APIC path (host-asserted); the fallback is
covered by mutants that break discovery and assert the fallback
markers plus a completed boot.

## Cost

One PMM frame + one page-table page on the APIC path (persistent
ACPI window + LAPIC + IOAPIC share one PT; the transient window is
released). Static bounded tables only: 16 CPUs, 8 IOAPICs, 32
overrides, 16 NMIs, 8 persistent pages. Host-pinned (`cost frames=1
tables=1`).

## Threat model

Untrusted firmware: every length/count/offset is bounded before use;
parser bugs fail closed to the PIC, never to a half-programmed APIC.
Injection: software `INT` of a hardware vector fails the ISR proof
and halts on both backends. Stuck delivery (ISR set after EOI) halts.
Unknown hardware (missing APIC, x2APIC, GSI overlap, base mismatch)
falls back, never guesses.

## Beyond INT-A2 (explicitly deferred)

MSI/MSI-X landed as INT-A2 (dynamic vectors 48-127, PCI
capability walk; see `msi.md`). Still deferred: SMP
secondary startup, AML/_PRT, power management, and all device
drivers (xHCI/NVMe/NIC/HDA/GPU). xHCI supports PCI INTx for
interrupter 0, so MSI is the preferred path, not a bring-up blocker.
The dynamic-route exercise above was the seam INT-A2 built on.
