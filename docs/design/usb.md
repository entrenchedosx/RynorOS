# USB-A1: root-port discovery → Address Device → EP0 Device Descriptor

Status: **complete (usb-a1).** One real USB device on an xHCI
root port, addressed by the xHC, descriptor retrieved through
EP0 control transfers. Builds on xHCI-A1 (command/event engine,
MSI-X, teardown); USB enumeration never duplicates that
machinery.

## 1. Authority (frozen)

| # | Fact | Source |
|---|------|--------|
| 1 | xHCI programming interface | xHCI Rev 1.2b (frozen by xHCI-A1; no silent upgrade) |
| 2 | USB device states, requests, descriptors, EP0, control semantics | USB 2.0 base spec 2000-04-27 + ECNs/errata (`usb_20_20250603.zip`, USB-IF doc library) |
| 3 | Chapter-9 machine-readable truth (descriptor layout, request codes, speeds) | Linux `include/uapi/linux/usb/ch9.h` |
| 4 | Transfer/Slot/EP context + TRB bit positions | Linux `drivers/usb/host/xhci.h`, `xhci-ring.c` (`xhci_queue_ctrl_tx`, `xhci_ring_ep_doorbell`) |
| 5 | PORTSC/doorbell/reset/slot/address/transfer emulation truth | QEMU `hw/usb/hcd-xhci.c`, `hcd-xhci.h` |
| 6 | Virtual device descriptors + speed selection | QEMU `hw/usb/dev-hid.c`, `hw/usb/desc.c` |
| 7 | Control-transfer recipe (Setup IDT+TRT / Data DIR+ISP / Status opposite-DIR+IOC, chaining) | Linux `xhci_queue_ctrl_tx` + xHCI §6.4.1.2 |

No invented bit positions. QEMU specifics stay in tests;
production is generic.

## 2. Live topology (test-owned)

```text
-device nec-usb-xhci -device usb-mouse   (usb_version=2 default)
```

QEMU wires the mouse to xHCI USB2 bus port 1 at High
Speed (`addr 0.0, port 1, speed 480`). xHCI root-port
number, protocol, and speed ID are DERIVED live from
Supported Protocol caps + PORTSC (observed: root port 5,
USB2 protocol, speed ID 3 = High Speed) — never assumed.

`usb-kbd` was the original choice but breaks the harness:
QEMU routes monitor `sendkey` to the USB keyboard, so the
i8042-based `[KBD]` stage never receives its key and the
boot stalls before any USB code runs. The mouse (and the
tablet in the genericity probe) avoids the PS/2 path
entirely. No production code depends on the device choice.

Observed served descriptor (`.high`, speed-selected):

```text
12 01 00 02 00 00 00 40 27 06 01 00 00 00 01 02 09 01
```

bcdUSB 0x0200, MPS0 64, VID 0x0627, PID 0x0001,
iMfr/iProduct/iSerial 1/2/9, 1 configuration. The
`usb-tablet` genericity probe serves `... 01 03 0a 01`
instead — the host validator accepts whatever the live
device serves (structure + cross-row consistency, never
frozen VID/PID/strings), and nothing is ever injected
into the guest.

## 3. xHCI facts (Linux xhci.h + QEMU)

TRB types: Setup 2, Data 3, Status 4, Link 6, Enable Slot
9, Disable Slot 10, Address Device 11, Evaluate Context
13, Transfer-NOOP 8, NOOP cmd 23, Transfer Event 32,
Command Completion 33, Port Status Change 34.

Completion codes: Success 1, DataBuf 2, Babble 3, USB-Tx 4,
TRB 5, Stall 6, NoSlots 9, SlotNotEn 11, EpNotEn 12,
Short 13, Param 17, CtxState 19.

Slot ctx (dwords): [0] route string; [1] speed[23:20] +
entries[31:27] + root port[23:16]; [2] intr target;
[3] state[31:27] + address[7:0]. States: Enabled 0,
Default 1, Addressed 2, Configured 3.

EP ctx: [0] state[2:0] + interval[23:16]; [1] type[5:3] +
burst/mult + MPS[31:16]; [2] dequeue[63:4] + DCS[0];
[3] dequeue-hi; [4] avg len. EP0 = DCI 1, type Control 4,
state Running 1.

Input Control ctx: Drop[31:0]=0, Add[1:0]=0x3 (slot+EP0)
for Address Device (QEMU requires exactly this).

Setup TRB: IDT + type + cycle + TRT (0 none / 2 OUT / 3
IN, xHCI ≥ 1.0); parameter = 8 setup bytes LE; LEN 8;
chained. Data TRB: type + cycle + DIR-IN iff IN + ISP iff
IN; LEN n + TD_SIZE 0 (single packet) + intr 0; DMA bus
pointer; chained. Status TRB: type + cycle + DIR opposite
the data direction (IN data → OUT status); LEN 0; IOC;
unchained (last). TD_SIZE counts remaining packets; all
USB-A1 transfers are single-packet data → 0.

Doorbell: register index = slot ID; value low byte =
endpoint ID (EP0 → 1). Never Doorbell 0 for transfers.

Transfer Event on the IOC TRB (Status): ptr = status TRB
bus, slot, epid, residual length 0, CC Success (Short
Packet 13 where legal with ISP).

QEMU Address Device: reads DCBAA[slot] (8B), requires
Add=0x3/Drop=0, looks up the port from slot ctx
(port[23:16] + route string), requires attached +
unassigned, BSR=0 assigns address = slot ID and performs
the USB SET_ADDRESS itself, enables EP1, forces EP state
RUNNING, writes output contexts. Enable Slot returns
first-free (expect 1; driver never assumes).

QEMU PORTSC write: PR/WPR are write-1-to-start (rest of
the write ignored); CSC/PEC/WRC/OCC/PRC/PLC/CEC are W1C;
PLS overwrites only with LWS=1; PP/WCE/WDE/WOE are RW.
Reset of a USB2 port: PR clears, PLS→U0, PED set, PRC
raised.

QEMU Supported Protocol caps: USB2 rev 2.0 @0x20 (ports
numports_3+1 .. +numports_2, default 5-8), USB3 rev 3.0
@0x30 (ports 1..numports_3, default 1-4). NO PSI Dword
rows — speed IDs fall back to xHCI defaults: USB2 FS=1,
LS=2, HS=3; USB3 SS=4.

## 4. Architecture decisions

- U1 reuse: one command ring, one event ring, one ISR,
  one hcd. USB adds transfer records + port-change
  bitmap + helpers; no second engine.
- U2 protocols: parse Supported Protocol caps into
  bounded structs (rev, port off/count, ≤8 PSI rows);
  reject overlapping/out-of-range/conflicting maps.
- U3 ports: PORTSC read via `xhci_op_read`; writes only
  through explicit helpers (reset = write PR alone; ack
  = write change bits alone). Never RMW a snapshot.
- U4 state machine per port (explicit enum; no boolean
  soup): DISCONNECTED → CONNECTED → RESETTING →
  ENABLED → SLOT → ADDRESSING → ADDRESSED → EP0READY →
  DESC8 → DESCDONE / FAILED (terminal).
- U5 speed: PORTSC speed ID interpreted through the
  port's protocol cap (PSI rows when present, xHCI
  defaults when absent) into LOW/FULL/HIGH/SUPER/
  SUPERPLUS/UNKNOWN. Unsupported → honest refuse.
- U6 reset: USB2 PR sequence with bounded PR-clear +
  PED/PRC validation; timeout → port FAILED, controller
  live. USB3 warm-reset path structural (unproven).
- U7 events: ISR routes Transfer (ptr→xfer record,
  resid/cc/epid/slot latched) and Port-Change (port ID
  → pending bitmap) with zero parsing; enumeration runs
  in foreground.
- U8 initial scan + event scan share one idempotent
  port-service routine (no double enumeration).
- U9 slots: bounded device array (2), slot↔port↔device
  ownership; Disable Slot + DCBAA clear + quiesced DMA
  free on every failure path (order: disable → prove
  completion → clear refs → free ring → free contexts).
- U10 contexts: 32B iff CSZ=0 (else refuse — CSZ=1 is
  an honest gap); dword arrays + masks, never
  bitfields; Input and Device never overlay.
- U11 EP0 ring: 10 TRBs (9 usable = 3 TDs exactly, so a
  TD never straddles the Link by arithmetic); PCS=1,
  Link TC=1 with old-PCS latch (same helper as the
  command ring); wrap forced by repeated reads.
- U12 EP0 init: MPS from speed (HS 64 fixed; FS/LS 8
  initial + evaluate-if-needed; SS 512); type Control;
  interval 0; errors 3; DCS 1; dequeue = ring bus.
- U13 first-descriptor: HS reads 8 (MPS0 must already
  be 64 — evaluate path proven live with a 64→64
  Evaluate Context + synthetic MPS-change encoding);
  then reads 18. FS/LS would read 8 → evaluate →
  read 18 (structural; unproved live).
- U14 transfers: one outstanding TD per EP0 ring at a
  time (USB-A1 bound); TD = setup+data+status with
  exact Linux-recipe fields; TD_SIZE 0; doorbell =
  slot register, value 1.
- U15 descriptor: byte-array parse (LE explicit),
  validate type/length/consistency; VID/PID/class
  generic (class 0, vendor-specific valid); raw bytes
  logged for independent host decode; canaries around
  DMA.
- U16 liveness: any device failure → port FAILED, slot
  disabled, controller proven alive with post-failure
  NOOP; xHCI-A1 CPL3 proof stays green.

## 5. Transcript grammar ([USB])

After `[XHCI] xhci verified` (usb_test images only):

```text
[USB] synth ok cases=101
[USB] proto rev=MM.mm ports=LO..HI psi=K        (xN, xcap order)
[USB] port N proto=P s=STATE ccs=C ped=E pls=L spd=I SPEED  (xM, 1..M)
[USB] rollback stage=X ok                       (x11, fixed order)
[USB] slot id=S port=N addr=A
[USB] ctx slot=S dctx=BUS ictx=BUS ep0ring=BUS mps=M
[USB] addr slot=S ctl=CCCCCCCC ok               (bit-exact: type 11, BSR=0)
[USB] out slot=S st=2 ep0=1 mps=M               (output-context witness)
[USB] eval slot=S mps=M ok
[USB] portev port=N ok                          (reset-completion event)
[USB] desc slot=S len=18 raw=HH..               (36 hex chars)
[USB] parsed vid=VVVV pid=PPPP bcd=BBBB cls=C mps0=M cfgs=G
[USB] xferdone slot=S ep=E cc=CC resid=R ptr=BUS ok   (x5, ring model)
[USB] wrap ep0=W reads=5 identical=1
[USB] noopalive ok
[USB] cleanup slot=0 ok
[USB] cost alloc0=HHHH alloc1=HHHH              (equal: zero net)
[USB] live ok devices=1
[USB] usb verified
```

Absent shape: `synth`, `absent`, `cost`, `live ok devices=0`,
`usb verified`. No-device shape: `synth`, `proto`×N, `port`×M,
`no-device`, `cost`, `live ok devices=0`, `usb verified`.

Reset is proven downstream (slot/address/descriptor success) plus
the RESET rollback stage and the `portev` claim of the
reset-completion Port Status Change Event; there is no separate
`reset` row. Transfer direction is proven by the descriptor bytes
themselves (an IN GET_DESCRIPTOR result), so `xferdone` carries
the completion witness, not the setup packet.

Exact rows pinned by `tools/host/usb_output.py`.
