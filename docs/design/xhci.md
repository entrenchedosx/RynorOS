# xHCI-A1: first real modern driver (command + event engine)

Status: **complete (verified).** One xHCI host controller through the
production stack (PCI claim, BAR map, legacy handoff, halt/reset,
DMA rings, MSI-X) completing genuine NO-OP commands, including an
MSI-X completion that preempts a running CPL3 thread. Internal
kernel driver + gated self-test: no USB enumeration, no slots, no
endpoints, no transfers (xHCI-A2/USB-A1). No CPL3 driver surface.

Spec identity: Intel eXtensible Host Controller Interface
Requirements Specification **Revision 1.2b** (doc 625472,
`625472_xHCI_Rev1_2b.pdf`, fetched 5.4 MB; binary — no text
extraction in this environment). All bit positions below are
cross-verified against the Linux spec-derived headers and the QEMU
implementation under test (§1). QEMU serves HCIVERSION 1.0; the
driver targets 1.x command-engine semantics both share.

## 1. Authority table

| Fact | Source |
|---|---|
| Spec revision 1.2b (doc 625472) | Intel PDF URL + landing search, §2 table |
| HCIVERSION/CAPLENGTH/HCSPARAMS/HCCPARAMS/DBOFF/RTSOFF layout | Linux `xhci-caps.h` (cites spec §5.3) |
| Scratchpad count `(HI<<5)\|LO`, CSZ, AC64, xECP dword units | Linux `xhci-caps.h` |
| USBCMD/USBSTS/CRCR/DCBAAP/CONFIG/PAGESIZE bits | QEMU `xhci_oper_read/write` + Linux `xhci.h` |
| IMAN/IMOD/ERSTSZ/ERSTBA/ERDP layout | Linux `xhci.h` + QEMU `xhci_runtime_read/write` |
| TRB size 16, type bits 15:10, cycle bit 0 | Linux `xhci.h` + QEMU `TRB_*` |
| TRB types: Link 6, NoOp-cmd 23, Evt-completion 33, Evt-port 34 | Linux `xhci.h` + QEMU `hcd-xhci.h` enums |
| Link TC bit 1, intr-target bits 31:22 | Linux `xhci.h` + QEMU fetch path |
| Completion codes: Success 1, TRBErr 5, Param 17, EvRingFull 21, RingStopped 24, Aborted 25 | Linux `xhci.h` + QEMU `hcd-xhci.h` |
| Event TRB: ptr u64, status len\|cc<<24, ctrl slot<<24\|ep<<16\|type<<10\|C | QEMU `xhci_event` + Linux `xhci.h` |
| Ext-cap header ID/next (dword units), USB-Legacy ID 1 | Linux `xhci-ext-caps.h` (cites spec §7) |
| Legacy BIOS_OWNED bit16, OS_OWNED bit24, SMI-disable mask | Linux `xhci-ext-caps.h` |
| Supported-Protocol ID 2, ports/speeds | Linux `xhci-ext-caps.h` + QEMU `usb_xhci_supports_protocol` |
| PORTSC bits (CCS/PED/PLS/Speed/PP/change/W1C) | Linux `xhci-port.h` + QEMU `PORTSC_*` |
| Reset while running warns; HCRST full reset; no CNR | QEMU `xhci_reset` + `xhci_oper_write` |
| ERSTSZ must be 1; seg 16..4096 TRBs; er_pcs init 1 | QEMU `xhci_er_reset` (`hw_error` otherwise) |
| EHB sticky re-arm; IP auto-clear on MSI-X notify | QEMU `xhci_intr_raise` |
| Doorbell 0 value 0 only; ignored while stopped | QEMU `xhci_doorbell_write` |
| Command completions forced to intr0 | QEMU `xhci_process_commands` (`xhci_event(...,0)`) |
| PCI class 0x0C03 prog-if 0x30; MSI@0x70, MSI-X@0x90 | QEMU `hcd-xhci-pci.c` |
| PCI INTx pin 1; MSI-X table+0x3000/PBA+0x3800 in BAR0 | QEMU `hcd-xhci-pci.c` |
| BAR0 64-bit mem 16 KiB (`XHCI_LEN_REGS` 0x4000) | QEMU `hcd-xhci.h` |
| nec_quirks (ERSTBA 16B ok); ring fetch cycle/Link rules | QEMU `hcd-xhci.c` `xhci_ring_fetch` |
| NOOP→Success/slot0; port events only when running | QEMU `xhci_process_commands`/`xhci_port_notify` |
| MMIO offsets used by INT-A2 proof (op/rt/db/interrupter) | `kernel/interrupts/msi-test.c` `live_xhci` |

## 2. Substrate reuse (no reinvention)

`pci_find_class` (class match + wildcards), `pci_bar_info` /
`pci_map_bar` / `pci_unmap_bar` (canonical BAR metadata, whole-BAR
UC map), `pci_cfg_read/write` (COMMAND RMW, INTX), `dma_alloc/free`
(virt/phys/bus, align, max_bus, zero, sync), `dma_sync_for_device` /
`dma_sync_for_cpu`, `pci_irq_enable_msix/auto`, `pci_irq_mask`,
`pci_irq_disable`, `pci_irq_info` (MSI-X transport, BME/MEM
refusals), `irq_register` path inside pci_irq, LAPIC EOI inside
dispatch, `user_create/enter/resume` + `vm_frame_access` (CPL3
workload + heartbeat reads), `wait`-style bounded spins. New
minimal primitives: `apic_mmio_enter/exit` exposed for
IRQ-context xHCI MMIO (same CR3 round-trip LAPIC/IOAPIC use),
`irq_last_frame_user/rip` dispatch observability, the XWORK
CPL3 workload blob (`USER_BLOB_XWORK`), and one generic-layer
fix xHCI proved missing: `user_save_state` used to park only
vectors 32-47, so no MSI/MSI-X (dynamic pool 48-127) could ever
preempt a CPL3 thread; it now mirrors `irq_dispatch`'s exact
range (`APIC_VECTOR_IRQ_BASE..APIC_VECTOR_DYNAMIC_END`).

## 3. Frozen layouts (D-register)

Conventions: all offsets bytes from BAR0 unless noted; `u32`
little-endian; reserved bits preserved on RMW; W1C written exactly.

- PCI identity: class `0x0C`, subclass `0x03`, prog-if `0x30`
  (xHCI). Any vendor/device. Revision recorded, never matched.
- CAPLENGTH `u8@0x00` (op base), HCIVERSION `u16@0x02`.
  HCSPARAMS1@0x04: slots[7:0], intrs[18:8], ports[31:24].
  HCSPARAMS2@0x08: IST[3:0], ERSTmax[7:4], SP_HI[25:21],
  SP_LO[31:27]; scratchpads `(HI<<5)|LO`. HCSPARAMS3@0x0C
  (U1/U2 exit latency, A1 diagnostic only).
  HCCPARAMS1@0x10: AC64 bit0, BNC 1, CSZ 2, PPC 3, PIND 4,
  LHRC 5, LTC 6, NSS 7, PAE 8, SPC 9, SEC 10, CFC 11,
  MaxPSA[15:12], xECP[31:16] in DWORDS from cap base (0 =
  none). DBOFF@0x14 mask `31:2` (byte offset). RTSOFF@0x18
  mask `31:5` (byte offset).
- Op regs (base = BAR0+CAPLENGTH): USBCMD@0x00 (RS 0, HCRST 1,
  INTE 2, HSEE 3, LHCRST 7, CSS 8, CRS 9, EWE 10, EU3S 11),
  USBSTS@0x04 (HCH 0, HSE 2, EINT 3, PCD 4, SSS 8, RSS 9,
  SRE 10, CNR 11, HCE 12; HSE/EINT/PCD/SRE are W1C),
  PAGESIZE@0x08 (bit n = 4K<<n supported), DNCTRL@0x14,
  CRCR@0x18 (RCS 0, CS 1, CA 2, CRR 3, addr 63:6),
  DCBAAP@0x30/0x34 (addr 63:6), CONFIG@0x38 (MaxSlotsEn 7:0).
- Runtime (base = BAR0+RTSOFF): MFINDEX@0x00 (RO). Interrupter n
  @0x20+n*0x20: IMAN@0x00 (IP 0 W1C, IE 1 RW), IMOD@0x04
  (IMODI 15:0 in 250ns, IMODC 31:16; 0 = unmoderated),
  ERSTSZ@0x08 (low 16 = entries), ERSTBA@0x10/0x14 (63:6),
  ERDP@0x18/0x1C (DESI 1:0, EHB 3, dequeue 63:4).
- Doorbell array (base = BAR0+DBOFF): DB0 u32, write 0 = ring
  host-controller doorbell. Stride 4 (A1 uses DB0 only).
- Ext caps (base = BAR0+xECP*4): header u32 ID[7:0],
  next[15:8] in DWORDS from this header (0 = end), followed
  by cap body. USB-Legacy ID 1: USBLEGCTL@+4 (BIOS_OWNED 16,
  OS_OWNED 24, SMI enables 29:16 mask region per header).
  Supported-Protocol ID 2 (diagnostic only in A1).
- TRB: 16 bytes `(param_lo, param_hi, status, control)`.
  control: cycle 0, type[15:10], EPID[20:16] (cmd),
  slot[31:24] (cmd). status: intr-target[31:22] (cmd).
  Link: parameter = next-base (16B aligned), TC bit 1.
  Event: parameter = u64 (cmd TRB pointer for completions),
  status = len[23:0] | CC[31:24], control =
  slot[31:24] | epid[20:16] | type[15:10] | C.
- Completion codes pinned: 1 Success, 5 TRB Error, 17
  Command Parameter Error, 21 Event Ring Full, 24 Command
  Ring Stopped, 25 Command Aborted, 26 Stopped.
- PORTSC (port n @op+0x400+n*0x10): CCS 0, PED 1, PLS[8:5],
  Speed[13:10], PP 9, change bits CSC/PEC/WRC/OCC/PRC 17..22
  (W1C), CEC 23. A1 reads only (diagnostics + event
  correlation); never writes PORTSC.

## 4. Decisions

- D1 ownership: one static `xhci_hcd` + `claimed` flag; second
  claim refuses `XHCI_STATE`. No generic driver framework.
- D2 BAR: use `pci_bar_info` ordinals; require 64/32-bit MEM,
  nonzero size, size ≥ CAPLENGTH+op-span at map time; map via
  `pci_map_bar` (whole BAR UC). MEM enabled at claim (needed
  for any MMIO); BME strictly after DMA ready (§14/D9).
- D3 caps: decode all of §3; refuse zero slots/intrs, bad
  CAPLENGTH (<0x20 or ≥ BAR), DBOFF/RTSOFF outside BAR,
  xECP outside BAR. Pin values in transcript.
- D4 accessors: `xhc_r32/w32(block, off)` with per-block base +
  BAR-size bounds; no raw volatile arithmetic in logic.
- D5 ext-cap walker: byte offset from xECP*4; header ID/next;
  next==0 ends; unknown IDs skipped; visited-set + 64-entry
  TTL; every read BAR-bounded; malformed fails closed.
- D6 legacy: if ID-1 present: if BIOS_OWNED: set OS_OWNED,
  bounded wait for BIOS_OWNED clear; on success disable legacy
  SMIs (write documented mask region only); verify OS_OWNED
  set + BIOS clear. Timeout/absent-bits fail `XHCI_TIMEOUT`/
  `XHCI_HW` with rollback. Absent cap: continue.
- D7 halt/reset: halt (RS=0, wait HCH) → refuse reset unless
  HCH (R1 negative: `XHCI_STATE`) → HCRST=1 → wait self-clear
  → wait CNR clear → verify quiesced. All waits bounded with
  distinct tags (§12). No ring programming before CNR clears.
- D8 timeouts: iteration budgets (1M foreground spins for
  token waits — NOOP round-trips in microseconds, so 1M still
  clears a ~100ms worst case by 10x while failing loud in
  seconds inside the boot harness window); every poll names
  condition + bound + tag.
- D9 COMMAND: save cmd0 at claim. MEM on at BAR map. BME on
  only after DCBAA+scratch+cmd+event+ERST programmed AND
  before `pci_irq_enable` (INT-A2 BME refusal). Restore cmd0
  after halt on teardown/failure. Never touch other bits.
- D10 addr width: `max_bus = AC64 ? DMA_ADDR_ANY :
  DMA_ADDR_32BIT` for every xHCI buffer; assert returned
  `bus` fits (refuse `XHCI_RANGE` otherwise).
- D11 pagesize: require bit0 (4 KiB); refuse otherwise. Frozen
  as current driver support (§16).
- D12 DCBAA: `(MaxSlotsEn+1)*8` bytes, 64B aligned, zeroed;
  MaxSlotsEn = min(hw MaxSlots, 8). Entry 0 per D13.
- D13 scratchpad: count from HCSPARAMS2; 0 → DCBAA[0] = 0;
  else array (count*8, 64B) + count PAGESIZE pages via DMA;
  count > 8 refuses `XHCI_RANGE` (documented raisable).
  QEMU serves 0, so the nonzero path is unexecuted
  (A2 gap, fails closed).
- D14 CSZ: recorded from HCCPARAMS1; context-size helpers take
  it (32 vs 64); DCBAA entries always 8 bytes. A1 allocates
  no device contexts.
- D15 rings: single-segment cmd ring 32 TRBs (31 usable +
  Link TC=1); single-segment event ring 32 TRBs (no Link;
  QEMU erstsz must equal 1). PCS/CCS init 1.
- D16 Link rule: Link.C = PCS of the lap just completed (set
  at wrap before toggling); initial Link.C = 1. Full
  derivation in §10 notes.
- D17 producer ring struct: dma buf, TRB count, enqueue idx,
  PCS; `enqueue` handles Link slot + wrap + toggle; full =
  next slot is Link-consumed-uncompleted (A1: completions
  drain; submit refuses `XHCI_BUSY` when no free entry).
- D18 TRB build: explicit u32 words + masks/shifts; 16-byte
  static assert; no C bitfields.
- D19 CRCR = cmd ring `bus` | RCS(PCS); pin equality in tests.
- D20 event consume: while C==CCS: validate + decode; ERDP =
  (dequeue & ~0xF) | EHB each batch; wrap toggles CCS.
  Interrupter state: IMOD=0, ERSTSZ=1, ERSTBA, ERDP init,
  IMAN IE.
- D21 MSI-X: `pci_irq_enable_msix(bus, dev, fn, 1, handler,
  opaque, &handle, vectors)` only (no manual table
  programming); interrupter 0 ↔ entry 0 (QEMU
  forces completions to intr0; driver binds 0 by design).
  MSI fallback refused for the driver (INTx never accepted);
  `auto` not used — determinism.
- D22 handler (IRQ context, either CR3): CR3-guard MMIO via
  `apic_mmio_enter/exit`; snapshot IP/frame-user/RIP/counter;
  drain new events (cycle-gated); validate completion
  (type/CC/ptr/cycle/slot); record token COMPLETED; ERDP+EHB;
  clear IMAN IP (write IP|IE, IP-only when quarantined per
  D31); W1C USBSTS EINT (write 0x8 only); no waits, no prints
  on success path.
- D23 tokens: 8 static slots FREE/SUBMITTED/COMPLETED(+ERROR);
  submit binds TRB bus addr; completion matches exact ptr
  (else `XHCI_HW` + controller marked failed). No
  single-command globals.
- D24 submit: token→TRB(C=PCS)→wmb→enqueue(+Link wrap)→
  doorbell 0. Completion wait: STI + bounded token poll
  (§60); timeout dumps state, masks, marks failed.
- D25 teardown order: stop submits → IMAN IE=0 →
  `pci_irq_disable` → RS=0 + wait HCH → restore cmd0 →
  dma_free (rings, ERST, scratchpads, DCBAA) → unmap →
  unclaim. No DMA after free (halt proven first).
- D26 rollback: staged `xhci_init(fail_at)`; each stage
  unwinds in reverse via one teardown path; fault-injection
  hook is test-only (`fail_at` enum, production passes
  `XHCI_FAIL_NONE`). Leaks asserted via dma/vector/cmd
  accounting.
- D27 CPL3 proof: MSI-X masked → submit NOOP → enter XWORK
  blob (counter + 5 accumulators, yields every 1024 of 8192)
  → at yield 4 read counter (must be 4096) → unmask
  (foreground, IF=0) → resume → pending MSI-X fires at the
  first CPL3 boundary → handler latches (user=1, RIP∈user
  code, ISR set) and the scheduler parks the frame → at
  yield 5 read counter (must be 5120, work continued) →
  user exits 77 with exact final counter + accumulators.
  Pending-ness is proven by delivery-on-unmask (exactly one
  IRQ, exactly one completion, CPL3 origin) — the `pci_irq`
  API exposes no PBA read and the driver does no manual
  table access, so no PBA row exists. No timer-hook
  changes; yields are the kernel re-entry.
- D28 limits: 1 controller, MaxSlotsEn 8, cmd/evt 32 TRBs,
  ERST 1 seg, 8 tokens, 8 scratchpads. Honest §77 bounds.
- D29: normal boot never fails on xHCI absence/failure
  (diagnosable `XHCI_ABSENT`/reason; OS continues).
- D30: second controller structurally possible (state in
  `xhci_hcd`, not file globals except the claim flag);
  no two-instance test in A1 (no dual-xHCI topology run).
- D31 storm quarantine: the ISR counts consecutive
  no-progress deliveries (IP set, zero events consumable).
  After 2 it masks the interrupter (IMAN write drops IE),
  sets `desync`, and every waiter fails `XHCI_HW` instead
  of livelocking — QEMU re-fires MSI-X while an event
  stays unconsumed, which starves iteration budgets
  (proven by the M-CCS mutant: 2.2 GB of `INT=0x30`
  before the fix, one loud `failure=s-wait` after). A
  healthy controller always leaves ≥1 event per
  delivery, so the healthy path never trips it.
- D32 event ring: flat 32 TRBs, no Link TRB (legal
  single-segment ring: the ERST segment size governs
  the producer wrap, observed toggling at the segment
  boundary). The command ring keeps its Link TC=1 at
  slot 31 with the D16 old-PCS latch (spec-mandated:
  the command ring has no size register).
- D33 RCS: QEMU ignores CRCR.RCS on fetch (double-fault
  mutant completed all 66 NOOPs with RCS=0). The driver
  enforces RCS itself via CRCR readback; dropping the
  write trips the rollback matrix (`rb-refused`). Real
  hardware gates fetch on RCS; the driver is correct
  for both.

## 5. QEMU device inventory (nec-usb-xhci, QEMU 11.1.0)

PCI 1033:0194 class 0x0C03 prog-if 0x30, INTx pin 1, BAR0
64-bit MEM 16 KiB @BAR0+0x0; MSI@0x70 MMC=4 (16 vec);
MSI-X@0x90 N=16, table BAR0+0x3000, PBA BAR0+0x3800.
CAPLENGTH 0x40, HCIVERSION 0x0100; HCSPARAMS1 slots=64
intrs=16 ports=8; HCSPARAMS2 0x0F (0 scratchpads);
HCCPARAMS1 0x80001 (AC64=1, CSZ=0, xECP=8→0x20);
PAGESIZE 1 (4K); DBOFF 0x2000; RTSOFF 0x1000. Ext caps:
ID2 USB2 @0x20 (next→0x30), ID2 USB3 @0x30 (end); NO
legacy-support cap. PORTSC: PP|RxDetect, no devices, no
boot-time port events (notify gated on running).

## 6. Transcript grammar (`[XHCI]`, host-pinned)

Exact row order (hex values lowercase, no `0x`, no padding;
decimals plain). The host validator pins every field.

```text
[XHCI] synth ok cases=43
[XHCI] rollback stage=BAR|CAPS|LEGACY|HALT|RESET|DCBAA|SCRATCH|CMDRING|EVTRING|ERST|INTR|BME|MSIX|START ok leaks=0  (×14, in order)
[XHCI] found bdf=B:D.F vendor=VVVV device=DDDD class=CC.SC.PI rev=RR
[XHCI] bar ord=O base=BBBB size=SSSS mem64=M
[XHCI] caps caplen=LL hciver=V slots=S intrs=I ports=P spads=N csz=C ac64=A xecp=X dboff=D rtsoff=R pagesize=PS
[XHCI] xcap id=II off=OOO next=NN  (one row per walked cap, in order)
[XHCI] legacy absent|owned bios=0 os=1
[XHCI] halted usbsts=SSSS
[XHCI] reset ok cnr=0 usbcmd=CCCC
[XHCI] dmaa dcbba=BUS cmd=BUS evt=BUS erst=BUS spads=N maxbus=ANY|32
[XHCI] slots maxen=N csz=C
[XHCI] ring cmd pcs=1 enq=0 wraps=0
[XHCI] ring evt ccs=1 deq=0 wraps=0
[XHCI] erst sz=1 ba=BUS erdp=BUS
[XHCI] intr ie=1 imod=0 erstsz=1
[XHCI] cmd saved=CCCC0 now=CCCC1
[XHCI] msix vec=V entry=0 ok
[XHCI] started usbsts=SSSS
[XHCI] noop token=T trb=BUS      \
[XHCI] db rung=0                  \
[XHCI] irq vec=V count=C isr=1 user=0 rip=RRRR   > ×64 (series)
[XHCI] cmpl token=T ptr=BUS cc=1 slot=0 type=33 cycle=C ok  /
[XHCI] noop done token=T count=C /
[XHCI] erdp deq=BUS wraps=W
[XHCI] intx silent=1
[XHCI] irq vec=V count=65 isr=1 user=1 rip=RRRR   (CPL3 preemption)
[XHCI] cpl3 yields=8 snap0=4096 snap1=5120 final=8192 acc=H0,H1,H2,H3,H4 regs=ok cs=23
[XHCI] teardown ok cmd_restored=1 dma_free=1 vec_free=1
[XHCI] noop token=T trb=BUS      (reinit leg: fresh rings)
[XHCI] db rung=0
[XHCI] noop done token=T count=1
[XHCI] wrap cmd=W evt=W cmds=66 cmpls=66
[XHCI] cost alloc0=AAAA alloc1=AAAA
[XHCI] live ok devices=1
[XHCI] failure=<tag>   (only on failure; aborts the section)
[XHCI] xhci verified
```

Absent path: `synth`, `absent`, `cost`, `live ok devices=0`,
`verified` — nothing else.

## 7. Test plan (as built)

- In-guest synthetic: 43-case TRB matrix (`synth_trb`:
  NOOP/Link encode across PCS, event decode across
  CCS/type/CC/slot, C-mismatch refusal). Ring/event
  wrap math is NOT duplicated synthetically: the live
  proof forces both wraps on real DMA and the host
  validator re-derives every address, cycle, and wrap
  from first principles instead of trusting the guest.
- Live (QEMU nec-usb-xhci, no USB devices): rollback
  matrix (14 injected stages, each leak-free) →
  claim→reset→rings→MSI-X→NOOP series (64 cmds, both
  rings wrap twice)→CPL3 proof→teardown→reinit.
  Topologies: xhci-only (primary), stock (absent path:
  `XHCI_ABSENT`, OS continues), combined dual-flag
  image (`RYNOR_MSI_TEST` + `RYNOR_XHCI_TEST` in one
  boot: INT-A2's xhci MSI-X proof quiesces the function,
  then the driver re-arms it — both sections validate).
- Negatives in-guest: second-claim refusal (`c-excl`),
  reset-while-running refusal (`c-r1neg`), INTx defense
  (legacy vectors 32-47 silent across the series).
- Kernel mutants (each rebuilt + rebooted, must RED):
  M-CCS (ccs init 0 → quarantine → `s-wait`), M-ERST
  (seg size 31 → desync → `s-wait`), M-RCS (CRCR
  without RCS → readback trips rollback,
  `rb-refused`), M-IE (interrupter IE dropped → no
  delivery → `s-wait`), M-DB (doorbell target 1 →
  `failure=`). A double-fault RCS mutant (write +
  readback neutered) stayed green, proving QEMU
  ignores CRCR.RCS on fetch (D33).
- Validator mutants: 30 tampered transcripts (every
  field class: counts, ptrs, cycles, wraps, accs,
  costs, order, terminator) all raise `XHCIError`.
- False-green audit: host-injection impossible
  (harness only runs QEMU); software-INT excluded
  (`isr=1` per delivery + INTx-silence row); wrap
  proven twice (guest counters + host model);
  `cmds==cmpls==66` with per-token ptr equality;
  prior-state excluded (HCRST+CNR per boot, fresh
  QEMU per proof); CPL3-never-running excluded
  (snap0/snap1 mid-loop bounds + `user=1` +
  park-recorded `preemptions≥1`).
- Honest gaps (A2 follow-ups, not executed in A1):
  nonzero-scratchpad path, legacy-present handoff,
  non-Success completion codes, and port-change
  events — QEMU serves none of these; the code
  paths exist and fail closed but are untriggered
  live. No scripted-MMIO-backend matrices were
  built; the `xhci_mmio_install` seam remains for
  A2.

## 8. Host validator (`tools/host/xhci_output.py`)

`verify_xhci_section(text)` raises `XHCIError` on any
deviation, returns the parsed rows on success. It walks
independent command-ring (31 usable/lap, Link skip, PCS
latch) and event-ring (flat 32, CCS gate) models over all
66 commands, checking every submitted address, every
event cycle, every token reuse, wrap counts, ERDP
advance, the XWORK accumulators (recomputed in Python),
rollback order, teardown evidence, zero net cost, and
exact row order. `verify_absent_section` pins the
5-row absent path. Unit suite
`tests/repository/test_xhci_output.py` (generated golden
+ 30 tampered transcripts, all RED).

## 9. Cost budget

Static: `xhci_hcd` (~900B incl. 8 tokens) + claim flag;
no .rodata tables. DMA per bring-up: DCBAA 4K + cmd 4K
(512B in a page) + evt 4K + ERST 4K + spads 0 (QEMU) —
all freed at teardown. MMIO: 16 KiB UC window. Vectors:
1 MSI-X (freed at teardown). Measured live: `cost
alloc0=20000 alloc1=20000` — zero net allocation across
rollback matrix + 66 commands + CPL3 + teardown +
reinit.

## 10. Risks + QEMU fidelity notes

- Completions ignore TRB intr-target (intr0 forced): driver
  binds interrupter 0 by design (§D21); multi-interrupter
  scheduling explicitly deferred.
- ERSTSZ≠1 or seg∉[16,4096] TRBs = QEMU `hw_error` (guest
  abort): driver programs ERSTSZ=1 / seg 32 always.
- CRCR.RCS ignored on fetch (D33): driver enforces via
  readback; real hardware gates fetch on RCS.
- Unconsumed events re-fire MSI-X (observed storm): D31
  quarantine masks the interrupter and fails waiters.
- Event-ring producer wraps flat at the ERST segment size
  and toggles CCS there (D32); no Link needed or used.
- No CNR served: wait loop still implemented per spec
  (immediately clear on QEMU); real-hardware ready.
- No legacy cap on QEMU: present-path handoff code is
  unexecuted (A2 gap, fails closed).
- Scratchpad count 0 on QEMU: nonzero path unexecuted
  (A2 gap, fails closed past 8).
- Link rule D16 derivation (producer must latch OLD pcs):
  proven live — the series crosses the Link twice (wraps
  at commands 32 and 63) with the correct PCS latched
  each time (host model agrees).
- MSI-X deliverability requires BME (INT-A2 D4b): D9 orders
  BME after DMA-ready, before `pci_irq_enable_msix`.
- Controller DMA uses PCI bus addresses: every programmed
  address is `dma.bus` (never virt/phys).
- PORTSC never written (A1); port-change events would be
  consumed as record+skip (none observed: no devices).
- Second controller: claim flag refuses; structs allow it.

