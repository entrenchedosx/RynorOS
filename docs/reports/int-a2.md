# RYNOROS INT-A2 close-out — PCI MSI/MSI-X delivery proven on live devices

RYNOROS INT-A2 COMPLETE — PCI MSI/MSI-X DELIVERY PROVEN ON LIVE DEVICES
PROOFS — edu MSI vec48 isr=1 + factorial; xHCI MSI-X NOOP + PBA mask; e1000e 3-vector IVAR/ICS; testdev negative; 6/6 topologies, 4/4 mutants, zero net frame growth
NEXT — first genuinely useful modern device driver (xHCI default); no more generic foundation slices

## Substrate (inherited, unchanged in contract)

INT-A1's vector pool (48-127 first-fit), route slots (32, shared
with GSI), ISR pre/post proofs, and mask-quiet dispatch are reused
verbatim. PCI-A1's config path (`pci_cfg_*` over a mockable dword
backend), decoded-BAR registry (4 entries/device), and `pci_map_bar`
(whole-BAR UC, slot 509) are reused verbatim. DMA-A1's contiguous
buffers serve the xHCI proof's rings. INT-A2 adds one production
file (`kernel/interrupts/msi.c` + `kernel/include/msi.h`), two
APIC helpers (aligned alloc, MSI route register), a route-kind
field, and a widened ring-0 preemption check (`frame_valid` < 128;
CPL3 tick frames keep the legacy range).

## New architecture

- Capability walker over `pci_cfg_*`: head at `0x34` gated on
  STATUS bit 4, `0x40..0xFC` dword-aligned entries, visited set +
  48-iteration TTL; unknown IDs skipped, malformed lists fail
  closed with a sticky reason.
- MSI parse + enable: MMC/MME discipline, 32/64-bit and mask/nomask
  geometries, cap-length bounds (10/14/20/24). Multi-vector MSI
  allocates N contiguous N-aligned CPU vectors and programs DATA
  with low `log2(N)` bits zero (QEMU `msi_prepare_message` rule).
- x86 message builder (pure): `0xFEE00000 | apic<<12`, HI=0,
  fixed/edge/level-0 (Linux parity — level is 0, not folklore 1);
  APIC ID >255 refuses.
- MSI-X validate + map + program: live COMMAND.MEM check, BIR raw
  slot → decoded entry translation, 8-alignment, table/PBA bounds
  without 64-bit wrap, non-overlap; whole-BAR reuse with a
  per-function refcount; masked-first per-vector programming under
  FUNCTION_MASK.
- Bus-master refusal (both paths): COMMAND.BME clear refuses
  `no-busmaster`. MSI/MSI-X are bus-mastered writes; QEMU drops
  them without BME (`bus_master_as`). The IRQ layer never sets
  BME/MEM — that is the driver's privilege decision.
- Generic `pci_irq_*` API: `enable_msi/msix/auto`, `disable`,
  `mask/unmask`, `info`. Handles are static-table indices (8 max);
  MSI/MSI-X mutual exclusion; auto prefers MSI-X; routes precede
  ENABLE; every failure rolls back in reverse.
- Route-before-ENABLE order, mask-then-configure, INTX_DISABLE
  touched-and-restored, readback-verify on every program step.

## Live proofs (all host-pinned, all `isr=1`)

```text
edu      MSI 1vec      00:03.0  BME refuse → enable vec48 → raise 0xAA → 1 delivery
                                   → fact path (status 1, 6! = 720) → silent reuse (0x55)
xhci     MSI-X 1vec    00:04.0  bring-up (DCBAA/rings/ERST/intr0) → NOOP → delivery
                                   → mask → NOOP silent (PBA=1) → unmask → delivery (PBA=0)
e1000e   MSI-X 3vec    00:05.0  auto→MSI-X; MSI-while-X fn-busy; IVAR+ICS → vec 49/50/48
                                   (TXQ0/RXQ0/OTHER) → mask/unmask → MSI-alone program row
testdev  negative      00:06.0  walk finds nothing; auto refuses no-msi-x-cap
```

Every proof carries the INTx-defense triple (INTX_DISABLE
readback, no IOAPIC route for the proof GSI, zero legacy-vector
deliveries in the window). Stock boot programs no MSI anywhere.
Combined topology (9 devices) reuses vec 48 for every proof.
QEMU fidelity notes (verified in source): xHCI completions ignore
Interrupter Target (intr0 only — multi-vector moved to e1000e);
PBA needs dword access; IVAR is one u32 at BAR0+0xE4.

## Synthetic matrices (193 cases, mock config backend)

Walker 18 (both caps, unknown skip, chains, truncation,
misalignment, cycle, self-next, low pointer, max chain,
all-ones); MSI parse 35 (32/64 × mask/nomask × MMC 0..5,
length bounds, MME>MMC, double-enable, adopt-verify);
message 16 (golden addr/data, APIC-ID refusal, DATA low-bit
rule N=1..32); MSI-X 16 (shared/exclusive BAR, I/O + BIR>5 +
unaligned + overlap + wrap + MEM-clear + truncation refuses,
N=1/16/64/2048); allocator 20 (aligned N=1..16 over
fragmented pools, exhaustion rollback bit-identical).

## Mutants (4/4)

C (walker misses MSI → `w-msi`), M (DATA low bits set →
`d-datazero`), X (overlap accepted → `x-overlap`), B
(bus-master check neutered → `d-nobm`): each turns the stock
boot RED with its own tag in a temp tree copy, then restores.

## Cost (measured, all six topologies)

`alloc0=0x20000 alloc1=0x20000`, net **0**, everywhere — the
MSI-X teardown releases everything it took (vectors, routes,
handles, BAR maps, DMA). Leak-freedom is enforced by hygiene
checks (80 vectors freed, double-disable refused, BARs
unmapped); the counter is a drift detector for VM policy.

## Docs

New `docs/design/msi.md` (authority table with fetched Linux +
QEMU sources, frozen layouts D1-D14, device inventory,
transcript grammar, test plan, cost, risks). Refreshed:
README (INT-A2 current state, inventory 1127/505),
ARCHITECTURE (MSI-owned pool, inventory), ROADMAP (inventory),
`acpi-apic.md` / `irq-timer.md` / `pci.md` (MSI landed, pool
ownership), `shell.md` (inventory).

## Driver audit (§90): what xHCI needs next

INT-A2 leaves the driver slice exactly: `pci_irq_enable_auto`
(route + message + masking), `pci_map_bar` (BAR0 for caps +
runtime + doorbells), DMA buffers (DCBAA, rings, ERST, TRBs —
already exercised by the proof bring-up), and the MSI-X
single-vector pattern (intr0/MSI-X-0, since QEMU targets
completions at intr0). Still driver-owned: BME+MEM enable,
slot/endpoint state machines, transfer-ring production,
event-ring consumption with EHB/ERDP discipline, and port
reset/enumeration. No further generic interrupt work is
expected; CPL3 preemption by MSI-X is the one open audit item
(no user threads lived during these proofs).

## Regression inventory

`integration-test`: 505/505. The full run (6161s) surfaced two
INT-A2 mutant-anchor drifts, both fixed and re-verified: the
`i8-release` anchor needed 2-line context (MSI route register
repeats the release guard), and the scheduler `timer-only-handoff`
anchor moved to the widened `< 128` bound. A third in-run item,
`test_gated_input_matrix`, was a CPU-contention flake (QEMU IRQ1-ack
counting under parallel suites) — retry-green immediately, and green
in the quiet re-run. Post-fix module re-run: test_apic (21) +
test_scheduler + test_input, 51/51 OK.

`test`: 1118/1127 (14232s). The 9 failures are all pre-existing
RynorLang backend/differential issues in files INT-A2 never touches
(git-disjoint, and `tools/rynorlang` imports nothing INT-A2 changed):
2 aggregate backend-mutation ERRORs (installed NASM emits a new
`.note.GNU-stack` warning-as-error), 1 control backend-mutation
ERROR (missing Linux linker on this Windows box), 3 selfhost
native/tail-call FAILs, 3 emit mutant-anchor FAILs. No kernel, host
tool, ABI, validator, or doc-count test regressed; `test_msi_output`
17/17 and `test_msi` 10/10 are green inside these runs.

RYNOROS INT-A2 COMPLETE — PCI MSI/MSI-X DELIVERY PROVEN ON LIVE DEVICES
PROOFS — edu MSI vec48 isr=1 + factorial; xHCI MSI-X NOOP + PBA mask; e1000e 3-vector IVAR/ICS; testdev negative; 6/6 topologies, 4/4 mutants, zero net frame growth
NEXT — first genuinely useful modern device driver (xHCI default); no more generic foundation slices
