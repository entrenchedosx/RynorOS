# CPU initialization and exception diagnostics

## Purpose and implementation status

Implemented Stage 2: explicit kernel GDT/IDT setup and verification, a shared
x86-64 exception diagnostic path, and controlled self-tests under QEMU. Six
required exception types are execution-tested. Other vector slots are wired
for best-effort diagnostics, not claimed as tested or enabled CPU features.

Stage 4 adds real-mode E820 collection without changing the CPU-mode transition.
After the original boot prefix,
`kernel_main` calls `cpu_initialize`, then `cpu_exception_self_test`, then
runs Stage 4 PMM initialization/self-tests, Stage 3 `timer_self_test`, rechecks
PMM integrity, then returns to the existing halt loop. See
`irq-timer.md` for the separate hardware IRQ layer; the exception contract below
is preserved.

## Public interfaces

All interfaces are kernel-internal, declared in `kernel/include/cpu.h`:

- `cpu_initialize()` loads/verifies kernel descriptor state; returns zero on
  verification or serial-output failure. Caller halts on failure.
- `cpu_exception_self_test()` triggers exactly one selected test exception;
  default breakpoint returns only after successful frame/restoration checks.
- `exception_dispatch(frame, cr2)` receives the normalized saved state from
  assembly. Only a matching armed breakpoint may return to assembly/IRETQ.
- `cpu_halt()` never returns; it loops on CLI/HLT.

There is no userspace ABI, scheduler context, general recovery callback, or
interrupt-controller API in this CPU header. Stage 3 uses the separate `irq.h`
interface. The existing serial API is unchanged.

## GDT design and verification

`kernel/arch/x86_64/cpu.c` owns an aligned, constant three-entry table:

| Selector | Descriptor | Purpose |
| --- | --- | --- |
| 0x00 | Null, all zero | Architecturally invalid null selector |
| 0x08 | `0x00af9b000000ffff` | Present DPL-0 executable/readable code, L=1, D=0 |
| 0x10 | `0x00cf93000000ffff` | Present DPL-0 writable data/stack, L=0 |

Both non-null descriptors have the accessed bit preset. All execution is CPL 0;
no user descriptors, TSS, LDT, or call gates are installed. Long mode ignores
most data-segment base/limit semantics; this table does not provide isolation.

`descriptors.asm` executes LGDT, performs a far return to reload CS=0x08, reloads
DS/ES/SS=0x10, and sets FS/GS selectors to null. C then uses SGDT and reads all
six selectors, checking base, limit, and selector values before printing
`[CPU] GDT initialized`. This replaces the transition GDT's 0x18 code selector;
it does not re-enter protected/long mode or modify paging.

## IDT design and vector mapping

A static 256-entry array occupies 4096 BSS bytes. Each installed gate is a
16-byte long-mode interrupt gate: selector 0x08, type/attributes 0x8e
(present, DPL 0), IST=0, reserved fields zero. Entries 0..31 target distinct
small stubs, all sharing `exception_common`. Stage 3 installs PIC IRQ stubs at
32..47 using the same saved frame but separate `irq_dispatch`; 48..255 are non-present.
LIDT is followed by SIDT base/limit verification and gate address/attribute checks
before `[CPU] IDT initialized` is emitted. Exception delivery works with IF=0;
PIC lines are masked during this initialization/exception test. Stage 3 subsequently
enables IRQ0 for its bounded timer test; NMI/UART interrupts remain disabled.

| Vector (decimal) | Name | CPU error slot? | Verification status |
| --- | --- | --- | --- |
| 0 | Divide Error (#DE) | No | Real DIV by zero, controlled halt |
| 1 | Debug (#DB) | No | Real TF single-step after NOP, controlled halt |
| 3 | Breakpoint (#BP) | No | Real INT3; frame checked and IRETQ return verified |
| 6 | Invalid Opcode (#UD) | No | Real UD2, controlled halt |
| 13 | General Protection (#GP) | Yes | Real invalid selector load, error=0x18 |
| 14 | Page Fault (#PF) | Yes | Real unmapped read, error=0 and CR2=0x200000 |
| 2, 4, 5, 7 | NMI, Overflow, Bound Range, Device Unavailable | No | Wired, unexercised |
| 8, 10, 11, 12 | Double Fault, Invalid TSS, Segment Not Present, Stack Fault | Yes | Wired, unexercised; no emergency stack |
| 16, 18, 19, 20 | x87, Machine Check, SIMD, Virtualization | No | Wired; feature handling unsupported |
| 17, 21, 29, 30 | Alignment Check, Control Protection, VMM Communication, Security | Yes | Wired; feature handling unsupported |
| 28 | Hypervisor Injection | No | Wired; unsupported |
| 9, 15, 22–27, 31 | Reserved | No synthetic hardware error assumed | Wired defensively; unsupported |
| 32–47 | PIC IRQ0..15 | Synthetic zero | Stage 3 gates; real IRQ0 tested, other lines masked |
| 48–255 | Unassigned external/application vectors | No gates | Unsupported |

Architectural vector/error distinctions follow the
[AMD64 System Programming manual, exception chapter](https://www.amd.com/content/dam/amd/en/documents/processor-tech-docs/programmer-references/24593.pdf).
No `INT n` is used to simulate a hardware-error-code exception: software INT does
not supply its hardware error slot. New CPU features require a separate review.

## Interrupt frame design and invariants

The long-mode CPU saves RIP, CS, RFLAGS, RSP, and SS even for same-CPL entry.
Hardware-error vectors additionally push an error code. All slots are eight
bytes. Entry can realign the handler stack; saved RSP is the actual interrupted
pointer, not a computed approximation. IRETQ restores the saved stack pair.
These mode-specific rules are described in
[Intel SDM Volume 3A, sections 5.14.2–5.14.3](https://www.intel.com/content/dam/support/us/en/documents/processors/pentium4/sb/25366821.pdf).

Stubs add a synthetic zero only for no-error vectors, then push the vector.
The common entry saves all 15 non-RSP GPRs before clobbering any, samples CR2,
passes the frame pointer and CR2 to exception C (IRQ vectors select separate C), clears DF for the C ABI, and aligns the
call stack to 16 bytes. RBX retains the original frame pointer across the call.
Compiler flags remain freestanding, general-registers-only, and no red zone.

Exact layout at the common entry's frame pointer (176 bytes total):

| Byte offset | Saved field(s), each eight bytes |
| --- | --- |
| 0, 8, 16, 24, 32, 40, 48, 56 | R15, R14, R13, R12, R11, R10, R9, R8 |
| 64, 72, 80, 88, 96, 104, 112 | RDI, RSI, RBP, RDX, RCX, RBX, RAX |
| 120 | Software vector |
| 128 | Hardware error or explicit synthetic zero |
| 136 | CPU-saved RIP |
| 144 | CPU-saved CS |
| 152 | CPU-saved RFLAGS |
| 160 | CPU-saved RSP |
| 168 | CPU-saved SS |

C static assertions check the structure size and critical offsets. CR2 is a
separate sample, not a CPU-stack-frame field, and is printed only for #PF.
No SIMD/x87 state, debug registers, FS/GS base, or other unrecorded state is claimed.
On return, assembly restores GPRs, discards vector/error slots, and uses IRETQ.

## Diagnostics and controlled test design

One shared C path prints a stable vector/name/error-source line, CPU-frame line,
four GPR lines, optional CR2 line, and action. Values come from the captured frame;
hex fields use 16 lowercase digits. A synthetic error zero is explicitly labeled
`error_source=synthetic`, not described as CPU-provided state.

The default assembly self-test seeds RAX through R15 with distinctive known
values, saves its actual RSP, and sets RFLAGS=0x402 (IF=0, DF=1) before INT3.
Those values are test inputs, not hardcoded diagnostic output. The C handler
checks the received vector, error, RIP against the linked post-INT3 label,
RSP against the saved value, selectors, flags, and all GPRs. A one-shot arm and
counter must also match. It then reports `action=resume` and returns. Assembly
checks all restored GPRs, RSP, and RFLAGS after IRETQ, clears DF before returning
to C, and only then can `[TEST] exception handling verified` be printed.

Separate integration images select one other required vector each. DIV/UD2,
invalid DS selector 0x18, and an assembly read at the unmapped address 0x200000
cause real faults without C undefined behavior; TF/NOP causes a real debug trap.
Their expected frame is checked, then the handler prints `action=halt` and the
verified marker and halts without retrying the instruction. #PF uses only the
existing Stage 1 static mapping; no mapping allocator or paging API is added.

Unarmed, repeated, wrong-vector, or mismatched-state exceptions print
`action=halt reason=unexpected` and never a verified marker. A best-effort nested
diagnostic guard halts; it does not make a broken stack safe.

## Tests

`python tools/build/build.py check` runs repository and integration tests through
Stage 4. The five Stage 1 regression cases remain; the normal boot requires the
unchanged prefix plus the complete Stage 2, PMM and timer transcripts.
QEMU reads actual serial output with a 10-second deadline, not a fixed boot delay.
Tests compare each saved RIP with the appropriate actual ELF symbol; require one
exception/verified marker, exact register/error/flag data, and controlled action;
verify missing/altered records cannot pass; and assert normal QEMU quit/reaping.
An unarmed breakpoint test confirms the fatal path cannot report test success.
Native artifacts remain byte-identical across independent rebuild directories.

## Known limitations / unsupported

No privilege changes, TSS/IST/emergency stack, stack guard, general recovery,
new virtual-memory facility, memory protection policy,
process isolation, FPU/SIMD
context handling, SMP, or hardware-platform coverage beyond the tested QEMU PC.
Faults before IDT loading, invalid stacks, or exceptions during diagnostics can
still double/triple-fault. Wired non-test vectors and nesting are not execution
coverage claims. Masked NMI is not an NMI-handling guarantee. Stage 3 adds only
the separate PIC/PIT IRQ path described in `irq-timer.md`; bootstrap dependencies
are unchanged. Stage 4 adds physical allocation separately in `kernel/mm/`;
it does not alter exception recovery, privilege or paging policy.
