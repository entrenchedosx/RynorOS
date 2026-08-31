# Stage 8: audited PS/2 keyboard input

The incoming uncommitted OpenCode implementation reproduced 71 repository and
58 integration passes, but those results did not establish its broader claims.
The independent audit found controller, decoding, API and test-quality defects.
The original source snapshot and baseline logs are retained under ignored build/.

Current contracts are in [keyboard design](../design/keyboard.md); findings and
final verification are in [Stage 8 audit](stage8-audit.md). Neither this report
nor the guest's success marker alone proves physical-hardware correctness.

Implemented: bounded explicit i8042/keyboard setup, IRQ1 status/data handling,
31-sample drop-newest ring with loss reporting, a documented Set-1 subset,
serialized foreground event consumption, and QEMU input/IRQ/I/O trace verification.
The guest reports actual events for host-selected keys, while IRQ0 schedules a
worker. Local queue tests never feed the hardware queue or its counters.

Not implemented: complete Set-1 repertoire, text/modifier interpretation,
LED control, runtime command arbitration, reconnect/recovery, USB, SMP, shell
or graphics. All changes remain uncommitted for review; no Stage 9 work was added.
