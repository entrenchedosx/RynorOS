# INT-A2: PCI capability walker + MSI/MSI-X + modern device IRQ delivery

Status: **complete (verified).** Last generic interrupt-foundation slice: a
capability-list walker, MSI and MSI-X transports, an x86 message
builder, and a generic `pci_irq_*` API over the INT-A1 vector/route
substrate — proven by live MSI (edu), single-vector MSI-X (xHCI),
and multi-vector MSI-X (e1000e) delivery. Internal kernel
infrastructure only: no CPL3 surface, no
driver bring-up (the xHCI proof uses test-owned minimal programming),
no SMP, no IOMMU/remapping, no x2APIC.

## 1. Authority table (no remembered bit positions)

Every layout below was verified against these sources (fetched
2026-09-28; QEMU sources are the implementation under test):

| Fact | Authority |
|---|---|
| Cap IDs MSI=0x05, MSI-X=0x11; CAP_LIST=0x34; STATUS_CAP=0x10; INTX_DISABLE=0x400 | Linux `include/uapi/linux/pci_regs.h` |
| MSI flags ENABLE/QMASK/QSIZE/64BIT/MASKBIT + all MSI offsets | Linux `pci_regs.h` |
| MSI-X flags QSIZE/MASKALL/ENABLE; TABLE/PBA BIR+OFFSET; 16 B entries | Linux `pci_regs.h` |
| MSI ADDR/DATA bit positions (dest[19:12], DM[2], RH[3], vec[7:0], deliv[10:8], level[14], trig[15]) | QEMU `include/hw/i386/apic-msidef.h` |
| MSI compose values (base, physical, fixed, edge, level=0) | Linux `__irq_msi_compose_msg` (`arch/x86/kernel/apic/apic.c`) |
| MSI multi-vector DATA rule (`data &= ~(N-1); data \|= i`) | QEMU `hw/pci/msi.c` `msi_prepare_message` |
| MSI MMC/QSIZE init, wmask (QSIZE+ENABLE), ADDR_LO mask ~0x3 | QEMU `hw/pci/msi.c` `msi_init` |
| MSI delivery decode (dest, vector, dest_mode, trigger, delivery) | QEMU `hw/i386/x86.c`+`apic.c` `apic_send_msi` (ignores DATA[14]) |
| MSI window = APIC MMIO page, offset >0xFFF routes to `apic_send_msi` | QEMU `hw/i386/apic.c` `apic_mem_write` |
| MSI-X cap length 12; table/PBA/message APIs | QEMU `include/hw/pci/msix.h` |
| edu MSI 1vec/64-bit/nomask (auto offset); ID/FACT_IRQ/raise/ack regs | QEMU `hw/misc/edu.c` + edu spec (`docs/system/devices/edu.rst`) |
| xhci MSI 16vec @0x70 + MSI-X 16vec @0x90, BAR0+0x3000/+0x3800 | QEMU `hw/usb/hcd-xhci-pci.c`, `XHCI_MAXINTRS=16` (`include/hw/usb/xhci.h`) |
| e1000e MSI 1vec @0xD0 + MSI-X 5vec, BAR3+0x0/+0x2000 | QEMU `hw/net/e1000e.c`, `E1000E_MSIX_VEC_NUM=5` (`e1000e_core.h`) |
| ich9-ahci MSI 1vec @0x80, no MSI-X | QEMU `hw/ide/ich.c` |
| virtio-net MSI-X 4vec (2q+2) on exclusive BAR, no MSI | QEMU `hw/virtio/virtio-{pci,net-pci}.c` |
| nvme MSI-X 65vec default (`msix_qsize`), BAR0+computed, no MSI | QEMU `hw/nvme/ctrl.c` |
| e1000 (classic) and pci-testdev: no MSI/MSI-X at all | QEMU `hw/net/e1000.c`, `hw/misc/pci-testdev.c` (zero refs) |
| Stock + candidate topology, IDs, BAR kinds, IRQ pins | Live HMP `info pci`, QEMU 11.1.0, `-machine pc` (see §9) |

One deliberate deviation from folklore: the MSI DATA level bit is
**0**, not 1 — Linux composes MSI with `memset 0` + vector + fixed,
and QEMU ignores DATA[14] on delivery. The freeze follows Linux.

## 2. Inherited substrate (exact INT-A1/PCI-A1 surface)

Config + BARs (`kernel/include/pci.h`, `kernel/drivers/pci.c`):

- `pci_cfg_read8/16/32`, `pci_cfg_write8/16/32`: validated BDF/offset;
  reads fail closed to all-ones, bad writes ignored. Backend is the
  dword-granular `pci_cfg_ops`, replaceable via `pci_cfg_install_ops`
  (test mock; never production). CF8/CFC serialized, IRQ-safe.
- `pci_map_bar(bus,dev,fn, entry, *va)` / `pci_unmap_bar`: MMIO BARs
  through `vm_map_device` (supervisor UC, slot 509). `entry` is the
  **decoded-entry ordinal**, not the raw config slot; `bars[].index`
  records the raw slot (a 64-bit BAR spans two slots, one entry).
  Only 4 entries decoded per device (documented truncation).
- `pci_find_bdf`, `pci_device_at`: borrowed registry pointers.
  `command`/`status` in the registry are enumeration-time snapshots;
  MSI-X must re-read live COMMAND (MEM decode) at enable time.

VM (`kernel/include/vm.h`): `vm_map_device` (slot 509, UC, refuses
RAM; permissions exactly `VM_WRITE`), `vm_unmap_device`, `vm_query`
for VA validation. MSI-X reuses BAR mappings; no new mapper.

APIC/vectors/routes (`kernel/include/apic.h`): `apic_bsp_id()`,
`apic_lapic_base()`, `apic_vector_alloc/claim/release/owner/state`
(first-fit over 48-127), `apic_route_gsi_register/unregister`,
`apic_route_for_vector`, `apic_lapic_isr_set`, `apic_note_vector`,
`apic_lapic_eoi`. The dynamic pool + route slots are shared with GSI
routes (32 slots); MSI/MSI-X consume the same budget.

Dispatch (`kernel/interrupts/irq.c`, frozen): route lookup by vector;
unexpected vector → `write_vector` diagnostic + mask-quiet + park;
gated by `apic_lapic_isr_set(vector)` **before** the handler (proves
hardware delivery — software INT never sets ISR) and ISR-clear
**after** EOI (`cpu_halt()` on violation); handler convention
`void (*)(cpu_u32 vector, void *opaque)`; then `apic_note_vector` +
LAPIC EOI. MSI/MSI-X reuse this path verbatim; only the mask-quiet
arm must learn non-IOAPIC kinds (decision D7).

Pre-existing refusals kept: x2APIC refused at init; `IRQ2` cascade
reserved; PIC fallback retained (`backend=pic` when APIC fails).

## 3. Frozen register layouts

### 3.1 Capability walk

List head at config `0x34`, valid only if STATUS bit 4
(`PCI_STATUS_CAP_LIST`). Each entry: byte0 = ID, byte1 = next
pointer (0 = end). Entries live at `0x40..0xFC`, dword-aligned.
IDs of interest: `0x05` MSI, `0x11` MSI-X; all other IDs are
skipped, never parsed. Malformed lists (pointer `< 0x40`, `> 0xFC`,
misaligned, cycle, next==self) fail closed with a sticky reason;
the walker never follows more entries than can exist (48-iteration
TTL plus a visited set).

### 3.2 MSI capability (ID 0x05)

```text
+0x00: cap ID (0x05) | +0x01: next
+0x02: MSG_CTRL: bit0 ENABLE, bits3:1 MMC (QMASK, max log2 N, RO),
                 bits6:4 MME (QSIZE, requested log2 N, RW),
                 bit7 64BIT, bit8 MASKBIT
+0x04: MSG_ADDR (low 32; bits1:0 unusable, QEMU wmask ~0x3)
+0x08: if 64BIT: MSG_ADDR_HI | else: MSG_DATA (16 bit)
+0x0C: if 64BIT: MSG_DATA (16 bit) [+0x10 MASK, +0x14 PENDING if MASKBIT]
+0x0C: if !64BIT and MASKBIT: MASK | +0x10 PENDING
```

N (vectors) is a power of two, `1..32`. MMC is RO; the OS writes
MME <= MMC. Only MME+ENABLE bits of MSG_CTRL are guest-writable
(QEMU `msi_init` wmask). Mask/pending exist only when MASKBIT=1.

### 3.3 MSI-X capability (ID 0x11)

```text
+0x00: cap ID (0x11) | +0x01: next
+0x02: MSG_CTRL: bits10:0 TABLE_SIZE (N-1, N = 1..2048),
                 bit14 FUNCTION_MASK (mask-all), bit15 ENABLE
+0x04: TABLE: bits2:0 BIR (BAR index 0..5), bits31:3 offset (8-aligned)
+0x08: PBA:   bits2:0 BIR, bits31:3 offset (8-aligned)
```

Each of the N table entries is 16 bytes at TABLE+16*i:

```text
+0x0: MSG_ADDR (low 32) | +0x4: MSG_ADDR_HI | +0x8: MSG_DATA (low 16 used)
+0xC: VECTOR_CTRL: bit0 MASK (1 = masked, reset state)
```

PBA is `ceil(N/8)` bytes at PBA+0, one bit per vector (1 =
pending). Table and PBA may share a BAR but must not overlap
(fail closed; conservative).

### 3.4 x86 message format (xAPIC, physical, fixed, edge)

```text
ADDR = 0xFEE00000 | (apic_id << 12)   // bits19:12 dest, bit3 RH=0, bit2 DM=0
ADDR_HI = 0                            // always, even on 64-bit caps
DATA = vector | (0 << 8) | (0 << 14) | (0 << 15)   // fixed, level=0, edge
```

- The `0xFEE` prefix is unconditional (Linux parity — Linux never
  checks the APIC base); the live phase emits a `[MSI] lapic base=`
  diagnostic row and trips loudly if firmware ever moves the LAPIC
  window, so the assumption cannot rot silently.
- `apic_id > 0xFF` refuses (`MSI_REFUSED`) — no truncation, no
  x2APIC extension (Linux `WARN_ON_ONCE`s here; we fail).
- Multi-vector MSI: device sends vector `i` with
  `DATA_i = (DATA & ~(N-1)) | i`, same address (QEMU
  `msi_prepare_message`). The OS must therefore allocate N CPU
  vectors **contiguous and N-aligned**, and program DATA with low
  `log2(N)` bits zero (decision D3).

## 4. Component design (decisions D1-D14)

One new file: `kernel/interrupts/msi.c` + `kernel/include/msi.h`.
No new mapper, no new IDT, no CPL3 surface.

- **D1 walker.** `msi_walk_caps(bdf, out)` over `pci_cfg_*` (mockable
  backend, so the synthetic matrix in §6 runs the real walker).
  Visited set + 48 TTL; returns offsets of MSI/MSI-X caps plus a
  malformed-list reason. Never parses unknown IDs.
- **D2 MSI parse.** `msi_parse(bdf, cap, *desc)`: MMC, is64, maskbit,
  current MME/ENABLE (refuse if already enabled by firmware with a
  live message? No — adopt: read addr/data, verify sane, keep; only
  refuse double-*enable* by us). Computes cap length from flags
  (10/14/20/24 bytes) and bounds-checks against 256.
- **D3 MSI enable (single + multi).** `msi_enable(bdf, nvec, handler,
  opaque, *vec)`: validate N pow2 <= MMC and <= 32; allocate N
  contiguous N-aligned CPU vectors (new aligned scan over 48-127;
  refuse + rollback when unavailable); build messages (§3.4);
  order: snapshot COMMAND (+BME check) → mask-all if MASKBIT →
  write ADDR(/HI=0) → write DATA (low bits 0) → write MME → install
  routes → COMMAND |= INTX_DISABLE → FLAGS |= ENABLE →
  readback-verify every write → unmask (if MASKBIT). Routes precede
  ENABLE so nothing can fire into an unrouted vector (critical for
  nomask devices). Any failure rolls back in reverse (ENABLE never
  left set on a dead route).
- **D4 MSI-X validate.** `msi_parse_msix(bdf, cap, *geo)`: live-read
  COMMAND.MEM — refuse `MSI_REFUSED` (`mem-disabled`) when clear
  (enabling decoding is the *driver's* job; the IRQ layer only
  validates); BIR <= 5; offsets 8-aligned; N = QSIZE+1 <= 2048;
  translate BIR raw slot → decoded entry via `bars[].index` (a
  64-bit BAR is one entry spanning two slots; BIR must name its
  start slot); refuse I/O, unimplemented, or truncated (beyond 4
  decoded entries) BARs; table_end and pba_end within BAR size, no
  64-bit wrap, table/PBA non-overlapping.
- **D4b bus master.** Both enable paths refuse `MSI_REFUSED`
  (`no-busmaster`) when live COMMAND.BME is clear: MSI/MSI-X are
  bus-mastered memory writes, and QEMU drops them without BME
  (`pci_msi_trigger` posts through `bus_master_as`, whose enable
  region tracks the BME bit — verified after a silent-delivery
  debug). The IRQ layer never sets BME itself (DMA enable is a
  device-global privilege decision, like MEM); the driver sets it
  first. SeaBIOS leaves BME clear, so the live proofs show the
  refusal row before the driver-role enable.
- **D5 MSI-X map.** Reuse `pci_map_bar` on the table/PBA BARs (whole
  BAR, UC, slot 509) with a per-function refcount: table and PBA
  may share one BAR; `pci_unmap_bar` runs only when the last vector
  of the function releases. `vm_query` validates every entry VA
  before the first write (§24).
- **D6 MSI-X program.** Per vector, masked-first: VECTOR_CTRL=1 →
  ADDR → ADDR_HI=0 → DATA → readback → VECTOR_CTRL=0. Bulk order:
  FUNCTION_MASK=1 → INTX_DISABLE → program requested vectors →
  ENABLE → readback → FUNCTION_MASK=0 (unrequested vectors stay
  per-vector masked). MSI-X needs no alignment (each entry has its
  own address/data); any free CPU vectors do.
- **D7 route kinds.** `struct apic_route` gains `kind`
  (legacy/gsi/msi/msix) + `msi_bdf` + `msi_index`. Dispatch and the
  ISR pre/post proofs are vector-based and unchanged. The
  unexpected-vector quiet arm masks by kind: IOAPIC pin for
  legacy/gsi, MSI mask bit (or nothing when nomask — then park
  only), MSI-X VECTOR_CTRL. Registration refuses double-use of a
  (bdf, index); vectors release back to the pool.
- **D8 teardown.** `pci_irq_disable(handle)`: mask vector(s) →
  MSI: clear ENABLE (MME/addr/data left programmed, documented);
  MSI-X: set per-vector MASK (+FUNCTION_MASK when the last vector
  of a function goes); clear INTX_DISABLE only if we set it;
  unregister routes, release vectors, unmap BARs at refcount zero.
  Double-disable and foreign-handle disable refuse (`MSI_STATE`).
- **D9 mutual exclusion + arbiter.** MSI and MSI-X ENABLE on one
  function refuse with `MSI_STATE` (PCI forbids both). When both
  caps exist, `pci_irq_enable_auto` prefers MSI-X (more vectors,
  per-vector masking); the choice is transcripted and host-pinned
  (e1000e live row).
- **D10 API.** `pci_irq_enable_msi/msix/auto(bdf, nvec, handler,
  opaque, *handle)`, `pci_irq_disable(handle)`,
  `pci_irq_mask/unmask(handle, index)` (MSI nomask devices refuse
  per-vector ops; MSI-X always supports them),
  `pci_irq_info(handle, *info)` (kind, vectors, messages). Handles
  are small indices into a static table (bounded, no alloc).
- **D11 errors.** `enum msi_result`: MSI_OK, MSI_INVALID (bad arg),
  MSI_ABSENT (no such cap), MSI_REFUSED (policy: alignment,
  MEM-disabled, apic-id, overlap), MSI_NOMEM (vectors/routes/table
  exhausted), MSI_HW (readback mismatch), MSI_RANGE (bounds/wrap),
  MSI_STATE (double enable/disable, both-caps). Sticky
  `msi_error()` string; every refusal transcripted.
- **D12 message builder.** Pure function
  `msi_build_message(apic_id, vector, *addr, *hi, *data)` (§3.4);
  the host validator reimplements it independently (§8) and diffs
  every live message. `apic_id > 0xFF` refuses.
- **D13 mask-then-configure.** No enable path programs an address
  or sets ENABLE while any vector that could fire is unmasked:
  MSI masks first when MASKBIT exists (nomask devices: INTX_DISABLE
  + ENABLE are adjacent with IF=0); MSI-X always masked-first per
  vector + FUNCTION_MASK across bulk program. COMMAND bit
  preservation: only INTX_DISABLE is touched, restored per D8.
- **D14 no firmware trust.** Enumeration-time `command`/`status`
  snapshots are never used for live decisions; ENABLE/MEM/mask
  state is re-read at every entry. A device the firmware left
  MSI-enabled is adopted only after its message verifies
  (FEE prefix + owned vector), else disabled + refused.

## 5. QEMU device inventory (§39; QEMU 11.1.0, `-machine pc`)

Live `info pci` plus the source table in §1. BDFs are SeaBIOS
order for the exact `-device` list in §7 (stock 00:00.0–00:02.0,
edu=00:03.0, xhci=00:04.0, e1000e=00:05.0, testdev=00:06.0); the
kernel never hardcodes BDFs — tests discover by vendor:device.

| Device (`-device`) | vendor:dev | MSI | MSI-X | BARs (live) | INTx | Role |
|---|---|---|---|---|---|---|
| stock: 1237/7000/7010/7113/1234:1111/8086:100e | — | none | none | — | 7113+100e pin A | Regression: stock boot programs no MSI anywhere |
| `edu` | 1234:11e8 | 1vec 64-bit nomask, QEMU-allocated offset | — | BAR0 mem32 1 MiB | pin A | **MSI proof** (raise 0x60/ack 0x64/status 0x24; ID 0x010000ED, FACT_IRQ 0x1) |
| `ich9-ahci` | 8086:2922 | 1vec 64-bit nomask @0x80 | — | BAR4 I/O, BAR5 mem32 | pin A | Spare MSI single-vector |
| `e1000e` | 8086:10d3 | 1vec 64-bit nomask @0xD0 | 5vec, BAR3+0x0/+0x2000 | BAR0/1 mem32, BAR2 I/O, BAR3 mem32 16 KiB | pin A | **Dual-cap arbiter + multi-vector MSI-X proof** (IVAR-routed ICS triggers → 3 CPU vectors) |
| `nec-usb-xhci` | 1033:0194 | 16vec @0x70 (MMC=4) | 16vec @0x90, BAR0+0x3000/+0x3800 | BAR0 mem64 | pin A (forced intr0 w/o MSI) | **MSI-X single-vector + mask/PBA proof** (NOOP→intr0; QEMU hardcodes command completions to intr0, so vectors 1+ are unprovable here); live MSI-parse + MME row |
| `nvme` (+subsys+ns) | 1b36:0010 | — | 65vec def, BAR0+computed | BAR0 mem64 | pin A | Spare (needs drive image) |
| `virtio-net-pci` | 1af4:1000 | — | 4vec def, exclusive BAR4 (mem64 pref) | BAR0 I/O, BAR1 mem32, BAR4 mem64 | pin A | Spare (exclusive-bar geometry) |
| `pci-testdev` | 1b36:0005 | — | — | BAR0 mem32, BAR1 I/O | none | **Negative control** (walker finds nothing) |

Proof-device rationale: edu is the only device with a one-MMIO-write
MSI trigger. xHCI proves functional-device MSI-X delivery (a real
NOOP executes, completes, and raises interrupter 0 → vector 0) but
QEMU ignores the TRB Interrupter Target for command completions
(`xhci_event(xhci, &event, 0)` — verified after a misrouted-vector
debug), so multi-vector MSI-X moved to e1000e: IVAR routes three
causes (TXQ0/RXQ0/OTHER) to three distinct MSI-X vectors, each raised
by a guest ICS write through the full device path (IMS gating →
IVAR → table → LAPIC) — the same legitimacy class as edu's raise
register. The xHCI bring-up (~DCBAA + cmd ring + ERST + interrupter
+ doorbell) lives in *test* code and directly prefigures the §90
driver audit; e1000e needs no link, DMA rings, or net backend for
the ICS triggers. nvme needs admin queues; virtio-net needs
virtqueues; pci-testdev has no MSI-X at all (verified: zero `msi`
refs in its model).

QEMU fidelity notes (all verified in source, all guest-visible):
- xHCI command completions ignore Interrupter Target (intr 0 only).
- MSI-X PBA MMIO requires dword access (`min_access_size = 4`; byte
  reads return 0) — the test reads u32.
- MSI/MSI-X posts travel `bus_master_as`: no BME, no delivery (the
  D4b rule). MSI/MSI-X ENABLE bits alone do nothing observable.
- e1000e IVAR (single u32 @ BAR0+0xE4): RXQ0@0, RXQ1@4, TXQ0@8,
  TXQ1@12, OTHER@16; entry = VALID(0x8)|VEC; causes raised via ICS
  writes honor IMS gating and IVAR routing exactly.

## 6. Evidence + transcript grammar

`[MSI]` rows (gated self-test images only; silent builds print
nothing). Every row is host-pinned (`tools/host/msi_output.py`):

```text
[MSI] walk ok cases=18
[MSI] parse ok cases=35
[MSI] msg ok cases=16
[MSI] msix ok cases=16
[MSI] alloc ok cases=20
[MSI] synth ok
[MSI] synthetic ok cases=193
[MSI] lapic base=fee00000
[MSI] walk bdf=0:3.0 msi=1@40 msix=0 malformed=0
[MSI] map bdf=0:3.0 entry=0 va=fffffe8010000000 pages=256
[MSI] msi bdf=0:3.0 mmc=0 mme=0 is64=1 mask=0 enabled=0 len=14
[MSI] msix bdf=0:4.0 n=16 tbl=0:3000 pba=0:3800
[MSI] mme bdf=0:4.0 mmc=4 wrote=4 read=4 restored=1
[MSI] msg vec=48 addr=fee00000 hi=0 data=30 apic=0
[MSI] entry bdf=0:4.0 i=0 addr=fee00000 data=30 ctrl=0
[MSI] enable bdf=0:3.0 kind=msi nvec=1 vec=48 ok
[MSI] irq vec=48 count=1 isr=1
[MSI] fact value=1
[MSI] pba bdf=0:4.0 vec=0 pending=1
[MSI] intx bdf=0:3.0 intxoff=1 silent=1
[MSI] disable handle=0 kind=msi ok
[MSI] refuse op=msi bdf=0:3.0 reason=no-busmaster
[MSI] cost alloc0=<hex> alloc1=<hex>   (pinned per topology; §9)
[MSI] live ok devices=9
[MSI] msi verified
```

Parity rule (§8): the synthetic matrices (§7) print byte-identical
`walk`/`msi`/`msix`/`msg` rows through the same emitters; the host
asserts synthetic == live-parse for the frozen geometries.

Stock devices (and pci-testdev) report `malformed=1`: the benign
no-list code (STATUS capability-list bit clear), not corruption.
Anything found after a truly malformed pointer still fails closed
with a sticky reason; the live phase treats all of these rows as
no-MSI either way.

INTx defense (every live proof): (a) COMMAND.INTX_DISABLE readback
row; (b) host asserts no IOAPIC route programmed for the proof
device's GSI; (c) host asserts zero deliveries on legacy vectors
32-47 during the proof window. MSI/MSI-X deliveries carry
`isr=1` from the INT-A1 pre-handler check.

## 7. Test plan

Synthetic (mock `pci_cfg_ops` + scripted config bytes; noguest):

- Walker matrix: no-caps, MSI-only, MSI-X-only, both, unknown-ID
  skip, chain of 5, truncated (next=0xFF), misaligned, cycle,
  self-next, next<0x40, 48-entry max chain, all-ones (absent).
- MSI parse matrix: 32/64-bit × mask/nomask × MMC 0..5; cap-length
  bounds; MME>MMC refuse; double-enable refuse; adopt-verify
  (good message / bad prefix / foreign vector).
- Message matrix: (apic 0/1/255, vectors 32/48/127/255) golden
  addr/data; apic 256/0xFFFFFFFF refuse; DATA low-bit-zero rule
  for N in 1/2/4/8/16/32.
- MSI-X geometry matrix: shared/exclusive BAR, 32/64-bit BAR, I/O
  BIR refuse, BIR>5 refuse, unaligned offset refuse, table/PBA
  overlap refuse, wrap refuse, N=1/16/64/2048, N=0 (QSIZE raw 0
  means 1 — never 0), MEM-clear refuse, truncated-BAR refuse.
- Allocator matrix: aligned N=1/2/4/8/16 over fragmented pools;
  exhaustion refuse + rollback (pool bit-identical after).

Live (boot-test images, `-device` per §5):

- edu MSI: BME refusal row → set BME → enable (vec 48) → raise
  0xAA → exactly 1 delivery (`isr=1`) → ack → status 0 →
  factorial path (row prints status 1; 6! = 720 read back and
  verified guest-side) → INTx-defense triple → disable → silent
  reuse (re-enable same vec 48, second raise 0x55, guest-asserted,
  no extra rows) → COMMAND restore → unmap.
- xhci MSI-X (single vector): test-owned bring-up (DCBAA + cmd
  ring + ERST0 + interrupter 0) → MMC=4 parse + MME program row
  (no MSI ENABLE) → enable nvec=1 (vec 48) → entry==msg rows →
  NOOP → 1 delivery → mask → NOOP (PBA=1, silent) → unmask →
  delivery (PBA=0) → INTx triple → disable → stop → COMMAND
  restore → dma_free all → unmap.
- e1000e: walk finds both caps (MSI @0xD0, MSI-X @0xA0) → BME
  refusal → auto prefers MSI-X (pinned) → MSI-while-X refuses
  (`fn-busy`) → disable → delivery: enable nvec=3 (vec 48-50),
  IVAR (TXQ0→1, RXQ0→2, OTHER→0), IMS arm → ICS TXQ0 →
  vec 49 → ICS RXQ0 → vec 50 → ICS OTHER → vec 48 → mask/unmask
  on index 2 → INTx triple → disable → MSI-alone
  (program+readback) → disable → COMMAND restore → unmap.
- pci-testdev: walk finds nothing; auto refuses (`no-msi-x-cap`).
- Stock boot: zero `enable` rows; suite-green unchanged.
- Combined (`edu+xhci+e1000e+testdev`): all sections green, 9
  devices, shared-pool reuse proven (every proof starts at vec 48).

Mutants (§78-80): C (walker misses MSI), M (MSI DATA low bits set
for N>1), X (table/PBA overlap accepted), B (bus-master check
neutered) — each must turn the stock boot RED with its own tag,
then restore green (mutants run in temp tree copies).

## 8. Host validators

New `tools/host/msi_output.py`: parses `msi:` rows; independently
reimplements `msi_build_message` (§3.4) and diffs every live
message; checks INTx-defense triple inputs; pins row counts and
the arbiter choice. Wired into `tests/integration/test_msi.py`
(live) and `tests/repository/test_msi_output.py` (validator +
golden matrices). Production code stays generic; all QEMU device
knowledge (edu offsets, xHCI bring-up, `-device` lists) lives in
test code and this doc.

## 9. Cost + budget interaction

MSI consumes the INT-A1 dynamic pool (vectors 48-127, 32 route
slots shared with GSI) — no new IDT. MSI-X additionally maps whole
BARs (xhci BAR0 16 KiB = 4 pages; edu BAR0 1 MiB = 256 pages;
e1000e BAR0 128 KiB = 32 pages + BAR3 16 KiB = 4 pages — all
unmapped at teardown). The `[MSI] cost` row reports
`pmm allocated_bytes` before/after the live proofs (pinned per
topology; drift detector for VM policy changes). Measured
(QEMU 11.1.0, all six topologies):

| Topology | alloc0 | alloc1 | Net |
|---|---|---|---|
| stock / edu / xhci / e1000e / testdev / all | `0x20000` | `0x20000` | **0** |

Zero net frame growth in every topology, including the BAR-mapping
ones: the MSI-X teardown path releases everything it took.
Leak-freedom is still proven by hygiene checks, not the counter:
vectors freed (80), double-disable refused (`MSI_STATE`), proof
BARs unmapped, DMA buffers released. Page-table frame reclamation
on unmap is VM-layer policy (out of INT-A2 scope); the cost row
observes it, the hygiene rows enforce ours.

## 10. Risks + non-goals

- No IOMMU/interrupt remapping: MSI devices are fully privileged
  (a malicious/buggy device can target any APIC). Documented, not
  solved — matches the stage (no DMA protection either).
- APIC IDs >255 impossible on xAPIC by construction, but the
  builder still refuses them (§63) rather than truncating.
- x2APIC stays refused; remapped/VF/nested cases out of scope.
- MSI-X tables in undecoded-BAR regions (>4 entries) refuse; a
  future PCI slice can raise the entry bound.
- The level-bit freeze (0) follows Linux+QEMU agreement; if a
  future Intel SDM reading contradicts it, only `msi_build_message`
  and its golden matrix change (single point).
- Scheduler `frame_valid` (`thread.c`) widened to vectors < 128
  (was < 48): dynamic-vector IRQs must preempt ring-0 self-test
  code. The user-exit validator (`user.c`, CPL3 tick frames) kept
  the legacy range in INT-A2 — MSI preempting live CPL3 threads
  was the §90 audit item. xHCI-A1 closed it: `user_save_state`
  now parks the full dispatch range 32–127, and an MSI-X
  completion preempting a live XWORK thread is proven
  (`user=1`, park-recorded, exact register state; see
  `xhci.md` D27).
