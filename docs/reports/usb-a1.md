# RYNOROS USB-A1 close-out — first real USB device descriptor over EP0

RYNOROS USB-A1 COMPLETE — REAL USB DEVICE DESCRIPTOR THROUGH DMA + MSI-X
PROOFS — slot 1 / port 5 / addr 1, BSR=0 (xHC owns SET_ADDRESS), 18-byte descriptor `120100020000004027060100000001020901` (VID 0627 / PID 0001 / bcdUSB 0200), 5/5 repeat reads identical, EP0 wrap x2, 11-stage rollback, XHCI/USB event separation, CPL3 preemption regression, 5/5 mutants, 42/42 validator tampers, zero net allocation
NEXT — USB configuration + HID boot keyboard (USB-B1); no more foundation slices

## Substrate (inherited, reused verbatim)

xHCI-A1 command/event engine (single command ring, single
interrupter, one MSI-X vector, ISR + storm quarantine,
ordered teardown, 14-stage rollback) with a minimal
extension surface: `xhci_ring_emit` (shared producer for
command + transfer rings), 8 transfer records matched by
exact TRB pointer (`xfer_submit/wait/release`), a
port-change bitmap (`port_change_claim`), and explicit
PORTSC helpers (`op_write`, no RMW). The USB layer adds no
second controller path, no second ISR, no second hcd.

## New architecture

- Supported Protocol parse into bounded structs (<=4
  protos, <=8 PSI each): overlap/range/conflict refused;
  speed via PSI rate math else xHCI default IDs.
- Port scan: explicit PORTSC snapshots, state machine
  DISCONNECTED..DESCDONE/FAILED, reset = PR-alone write
  with bounded PED wait, change-bit ack = W1C-alone.
- Slot lifecycle: Enable Slot, one bounded dev slot,
  Disable Slot + DCBAA clear after quiescence, ordered
  free (ring, then contexts).
- Contexts (CSZ=0, 32B, dword arrays): slot dword0 =
  route 0 + speed + entries 1, dword1 = root port;
  EP0 = Control/MPS/DCS/dequeue; Input Add 0x3 for
  Address, EP0-only for Evaluate.
- Address Device BSR=0: the xHC performs SET_ADDRESS.
  Output validation refuses anything but slot state
  Addressed + nonzero address + echoed port/speed +
  EP0 Running + intact dequeue/DCS.
- EP0 ring: 10 TRBs (9 usable = 3 TDs, TD-atomic by
  arithmetic, Link never straddled, PCS latched like
  the command ring).
- Control TD per the Linux recipe: Setup IDT+TRT
  chained, Data DIR/ISP chained, Status opposite-DIR
  IOC unchained; doorbell = slot register, value 1;
  Transfer Event on the IOC TRB with CC + resid + epid
  checked.
- Descriptor: 8-byte first read (MPS0 fixed for HS/SS,
  evaluate-if-needed for FS/LS), live Evaluate proof,
  18-byte read, LE byte-array parse, canaried DMA,
  raw bytes logged for independent host decode.

## Live proofs (all host-pinned)

```text
proto    2.0 ports 5..8 + 3.0 ports 1..4, disjoint, PSI 0
scan     8 ports; port 5 conn/ccs=1/pls=7/spd=3 HS, rest RxDetect
rollback 11 stages (PROTO..DESC18), each fails loud + DCBAA clear + post-failure NOOP
slot     id=1 port=5 addr=1
addr     ctl=1002c00 bit-exact (type 11, slot 1, BSR=0)
out      st=2 (Addressed) ep0=1 (Running) mps=64
eval     64->64 Evaluate Context accepted
portev   reset-completion Port Change claimed through the ISR
desc     18 bytes DMA'd; independent host re-parse matches parsed row
repeat   5 reads, all identical, ptrs cycle 80/20/50/80/20 (Link x2)
separate [XHCI] completions + [USB] xfers + [USB] portev in one transcript
cpl3     SCHED workers preempted 6x each in the same boot
absent   XHCI_ABSENT path: synth + absent + zero cost, OS continues
nodev    controller live, zero devices: inventory + no-device + zero cost
tablet   usb-tablet serves different bytes (...01030a01): still green
```

QEMU fidelity notes: monitor `sendkey` routes to an
attached `usb-kbd`, starving the i8042 `[KBD]` stage, so
the proof device is `usb-mouse` (production is
device-agnostic); Setup TRT mismatches are ignored by
the emulator (stayed-green mutant, same gap class as
the xHCI-A1 RCS precedent) — direction is proven by the
descriptor payload. Bring-up bug found live: slot
speed/entries were built into dword 1 (colliding with
the port field); the synth context matrix now guards
the layout (`u-ctx-s0`).

## Synthetic matrices (101 cases, in-guest pure)

Setup/TRB encode (Setup/Data/Status/Link across PCS,
TRT/DIR/ISP/IOC/chain bits), transfer-event decode
(CC/SHORT/endpoint/slot/pointer), slot + EP0 context
images incl. dword split and DCS masking, descriptor
parser accept/reject, EP0 MPS by speed + update matrix.

## Mutants (5/5 kernel + 42/42 validator)

M-SCTX (dword swap → synth `u-ctx-s0`), M-BSR (BSR=1 →
output validation refuses), M-PCS (PCS 0 → xfer
timeout), M-DB (doorbell 0 → xfer timeout), M-LEN (18→8
read → parser refuses truncation): each rebuilt +
rebooted RED, then restored. 42 tampered transcripts
(every row and field class incl. BSR flip, TRB-ptr
skew, wrap count, cost leak) all raise `USBError`;
7 golden/shape cases pin the three section shapes.

## Cost (measured)

`alloc0=20000 alloc1=20000`, net **0**, across synth +
11 rollbacks + enumerate + 5 reads + NOOP + cleanup +
teardown. Per-enumeration DMA (device/input contexts,
EP0 ring, descriptor buffer) is page-granular and fully
freed; no vectors, no statics growth beyond the bounded
bus/dev records.

## Docs

New `docs/design/usb.md` (frozen authority table,
U1-U16 architecture, exact transcript grammar,
topology + sendkey note, emulator gaps) and this
report. Refreshed: `docs/design/xhci.md` (shared
transfer-engine surface), ROADMAP (USB-A1 row), README
(current state + inventory 1210/524), `tools/build`
inventory (`test_usb_output` 49, `test_usb` 11),
`tools/host/repository.py` (10 new files registered).

## Honest gaps (carried, not hidden)

FS/LS evaluate-with-change is structural (live device
is HS); USB3 warm reset is structural
(`XUSB_UNSUPPORTED`); CSZ=1 refused; hot-unplug during
transfer is impractical on this harness (rollback
matrix + post-failure NOOP is the liveness proof);
`usb-kbd` cannot be the proof device (sendkey
routing, above).

## Regression inventory

`test_usb` 6/6 (live, separation, CPL3, absent,
no-device, SET_ADDRESS pin) + 5/5 mutants;
`test_usb_output` 49/49; `test_xhci` 8/8 standalone on
this branch; `test_xhci_output` 34/34; `test_commands`
13/13 (fixture build caught the missing file-registry
entries, then green); default image boots with zero
USB/XHCI rows (706 vs 778 sectors); inventory counts
re-verified (49/11). The multi-hour full repository
suite was not re-run; the repo-surface change is
strictly additive (one validator, one suite, inventory
+ registry entries), and every suite touching it is
green above.

RYNOROS USB-A1 COMPLETE — REAL USB DEVICE DESCRIPTOR THROUGH DMA + MSI-X
PROOFS — slot 1 / port 5 / addr 1, BSR=0 (xHC owns SET_ADDRESS), 18-byte descriptor `120100020000004027060100000001020901` (VID 0627 / PID 0001 / bcdUSB 0200), 5/5 repeat reads identical, EP0 wrap x2, 11-stage rollback, XHCI/USB event separation, 5/5 mutants, 42/42 validator tampers, zero net allocation
NEXT — USB configuration + HID boot keyboard (USB-B1); no more foundation slices
