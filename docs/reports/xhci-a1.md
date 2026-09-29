# RYNOROS xHCI-A1 close-out — first real modern driver: xHCI command + event engine

RYNOROS xHCI-A1 COMPLETE — REAL xHCI COMMAND + EVENT ENGINE VERIFIED THROUGH DMA + MSI-X
PROOFS — 66 NOOPs (64 series + CPL3 + reinit), both rings wrap ×2, MSI-X preempts live CPL3 (user=1, exact regs), 14-stage rollback, MSI/XHCI separation, 5/5 mutants, 30/30 validator tampers, zero net allocation
NEXT — first USB device descriptor (xHCI-A2/USB-A1); no more foundation slices

## Substrate (inherited, reused verbatim)

PCI-A1 discovery + BAR registry + whole-BAR UC maps; DMA-A1
zero-on-alloc contiguous buffers with `bus` addresses and
sync barriers; INT-A1 vectors/routes/ISR proofs/dispatch;
INT-A2 `pci_irq_enable_msix/mask/disable/info` (BME/MEM
refusals, table programming, vector 48–127 pool); the
scheduler tick/park path; userspace contexts + gates. New
minimal surface: `apic_mmio_enter/exit` (IRQ-context
kernel-CR3 guard shared with the xHCI handler),
`irq_last_frame_user/rip` (dispatch origin latch), the
XWORK CPL3 workload blob, and one generic-layer fix xHCI
proved missing — `user_save_state` parked only vectors
32–47, so MSI/MSI-X could never preempt CPL3; it now
mirrors `irq_dispatch`'s exact 32–127 range.

## New architecture

- One-owner claim on PCI class `0x0C03/0x30` (any
  vendor/device), second claim refuses; BAR0 required
  64/32-bit MEM, whole-BAR UC map; caps decode with
  fail-closed bounds (CAPLENGTH, DBOFF/RTSOFF, xECP,
  slots/intrs, pagesize bit0, AC64 addr width).
- Extended-cap walker (visited set + TTL, BAR-bounded,
  unknown IDs skipped) and BIOS→OS legacy handoff with
  bounded waits and SMI-disable mask (absent on QEMU —
  the present path is an honest A2 gap).
- Halt (RS=0, wait HCH) → reset refused unless halted
  (R1 negative pinned) → HCRST + self-clear + CNR waits.
- DMA: DCBAA (`MaxSlotsEn=min(hw,8)`), scratchpad array
  + pages (count 0 on QEMU; >8 refused), 32-TRB command
  ring (31 usable + Link TC=1 with old-PCS latch),
  flat 32-TRB event ring (ERST size governs the HW
  wrap, no Link), single-entry ERST.
- Interrupter 0 (IMOD=0, ERSTSZ=1, ERSTBA, ERDP,
  IMAN IE), BME only after DMA-ready, single-vector
  MSI-X via the `pci_irq` API (no manual table
  programming), then RS=1 start.
- IRQ-context handler (either CR3): ISR proof + frame
  latch, cycle-gated drain, completion validation
  (type/CC/ptr/cycle/slot), 8 static tokens matched by
  exact TRB pointer, ERDP+EHB, IMAN IP clear, USBSTS
  EINT W1C — plus storm quarantine (D31): two
  consecutive no-progress deliveries mask the
  interrupter and fail waiters instead of livelocking.
- Ordered teardown (mask → disable → halt/reset →
  COMMAND restore → free → unmap → unclaim) and a
  14-stage `fail_at` rollback matrix with
  leak/vector/COMMAND accounting per stage.

## Live proofs (all host-pinned, all `isr=1`)

```text
series   64 NOOPs   cmd ring 31+31+2 (Link ×2, PCS latch) → 64 MSI-X
                        → evt ring 32+32 (CCS gate) → erdp shadow exact
intx     defense    legacy vectors 32–47 silent across the series
cpl3     1 NOOP     mask → submit → XWORK (8192, yields/1024) → snap 4096
                        → unmask → MSI-X preempts CPL3 (user=1, rip=400066,
                        park-recorded) → snap 5120 → exit 77, exact accs
reinit   1 NOOP     teardown → full re-bring-up → NOOP → teardown
absent   stock      XHCI_ABSENT, OS continues, 5-row section
combined dual-flag  MSI xhci proof quiesces, driver re-arms; both validate
```

`wrap cmd=2 evt=2 cmds=66 cmpls=66`; every address, cycle,
token, and wrap re-derived by the host model. QEMU
fidelity notes (all verified): completions forced to
intr0; CRCR.RCS ignored on fetch (driver enforces via
readback); unconsumed events re-fire MSI-X (quarantine);
event producer wraps flat at ERST size; ERSTSZ≠1 aborts.

## Synthetic matrices (43 cases, in-guest pure)

NOOP/Link encode across PCS, event decode across
CCS/type/CC/slot, C-mismatch refusal. Ring/event wrap
math is proven on live DMA + host re-derivation, not
duplicated synthetically.

## Mutants (5/5 kernel + 30/30 validator)

M-CCS (ccs 0 → quarantine → `s-wait`), M-ERST (size 31 →
`s-wait`), M-RCS (no RCS → readback trips rollback →
`rb-refused`), M-IE (no delivery → `s-wait`), M-DB
(target 1 → `failure=`): each rebuilt + rebooted RED,
then restored. A double-fault RCS mutant stayed green,
proving the QEMU RCS gap (D33). 30 tampered transcripts
(each field class) all raise `XHCIError`.

## Cost (measured)

`alloc0=20000 alloc1=20000`, net **0**, across rollback
matrix + 66 commands + CPL3 + teardown + reinit. Static:
`xhci_hcd` ~900B; DMA 4×4K pages + DCBAA 4K per
bring-up, all freed; 1 MSI-X vector, freed.

## Docs

New `docs/design/xhci.md` (authority table, frozen D1–D33
layouts, QEMU inventory, exact transcript grammar, test
plan as built, validator, cost, risks) and this report.
Refreshed: README (current state, inventory 1161/513),
ARCHITECTURE (driver section, CPL3 preemption, inventory),
ROADMAP (xHCI-A1 row), `msi.md` (§90 audit item closed),
`pci.md` (first driver landed), `shell.md` (inventory).

## Driver audit: what USB needs next

Slot contexts, endpoint/transfer rings, port reset +
device-slot assignment, SETUP/DATA/STATUS transactions,
and the first device descriptor (xHCI-A2/USB-A1). The
command/event engine, MSI-X path, CPL3 preemption, and
teardown/rollback discipline carry over unchanged. Open
A2 gaps: nonzero scratchpads, legacy-present handoff,
non-Success completion codes, port-change events.

## Regression inventory

`integration-test`: 510/513 in the full run (6770s), then
three focused fixes, each re-verified. Two VM stale-TLB
negatives failed deterministically: always-compiled xHCI
objects perturbed the default kernel layout past a
layout-sensitive soft-TLB window. Fixed by compiling
`xhci.o`/`xhci-test.o` only into xHCI test images
(`main.c` compile-gated): default images keep the exact
INT-A2 layout. The third, `test_msi.test_combined_topology`,
was a CPU-contention flake (all three e1000e IRQs
delivered, then a mask-window wait budget expired while
the repo suite burned 70% CPU) — retry-green immediately,
same class as INT-A2's `test_gated_input_matrix` flake.
Post-fix module re-runs: test_boot (14) + test_vm (8) +
test_msi combined (1), 23/23 OK; test_xhci 8/8 OK;
test_apic (21), test_pci + test_dma (34), test_userspace
(18), test_scheduler (23) OK. Mandate sweep post-fix:
test_pmm, test_keyboard, test_shell, test_proc,
test_input, test_pipe, test_p1a, test_p1a2, test_p1a3 —
112/112 OK.

`test`: 1152/1161 (13802s). The 9 failures are exactly
INT-A2's pre-existing RynorLang backend/differential set
in files xHCI-A1 never touches (2 aggregate
backend-mutation ERRORs, 1 control backend-mutation
ERROR, 3 selfhost native/tail-call FAILs, 3 emit
mutant-anchor FAILs). No kernel, host tool, ABI,
validator, inventory, or doc-count test regressed;
`test_xhci_output` 34/34 green inside the run. (An
earlier `test` attempt hung idle deep in the emit
natives while the full `integration-test` contended for
CPU; the quiet rerun completed normally.)

RYNOROS xHCI-A1 COMPLETE — REAL xHCI COMMAND + EVENT ENGINE VERIFIED THROUGH DMA + MSI-X
PROOFS — 66 NOOPs (64 series + CPL3 + reinit), both rings wrap ×2, MSI-X preempts live CPL3 (user=1, exact regs), 14-stage rollback, MSI/XHCI separation, 5/5 mutants, 30/30 validator tampers, zero net allocation
NEXT — first USB device descriptor (xHCI-A2/USB-A1); no more foundation slices
