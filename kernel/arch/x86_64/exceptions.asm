bits 64
default rel
section .text
extern exception_dispatch
extern irq_dispatch

; No-error vectors get a synthetic zero. Hardware-error vectors already have
; a qword error slot. Do not use INT n to test a hardware-error vector.
%assign vector 0
%rep 32
exception_stub_%+vector:
%if vector != 8 && vector != 10 && vector != 11 && vector != 12 && vector != 13 && vector != 14 && vector != 17 && vector != 21 && vector != 29 && vector != 30
    push qword 0
%endif
    push qword vector
    jmp exception_common
%assign vector vector + 1
%endrep

; PIC IRQs never push CPU error codes. Share the proven full-register entry
; and IRETQ return, but dispatch to a separate C hardware-IRQ layer.
%assign vector 32
%rep 16
irq_stub_%+vector:
    push qword 0
    push qword vector
    jmp exception_common
%assign vector vector + 1
%endrep

exception_common:
    push rax
    push rbx
    push rcx
    push rdx
    push rbp
    push rsi
    push rdi
    push r8
    push r9
    push r10
    push r11
    push r12
    push r13
    push r14
    push r15
    ; Preserve every GPR before using argument registers. CR2 is sampled before
    ; C/serial work; it is meaningful here only for a page fault.
    mov rdi, rsp
    mov rsi, cr2
    mov rbx, rsp
    cld                       ; SysV C requires DF=0, interrupted flags stay saved.
    and rsp, -16              ; Align before CALL without moving the saved frame.
    cmp qword [rdi + 120], 32
    jb .exception
    call irq_dispatch
    jmp .restore
.exception:
    call exception_dispatch
.restore:
    mov rsp, rbx              ; RBX is callee-saved by the C ABI.
    pop r15
    pop r14
    pop r13
    pop r12
    pop r11
    pop r10
    pop r9
    pop r8
    pop rdi
    pop rsi
    pop rbp
    pop rdx
    pop rcx
    pop rbx
    pop rax
    add rsp, 16               ; Remove software vector and normalized error slot.
    iretq                     ; CPU restores RIP, CS, RFLAGS, RSP and SS.

section .rodata align=8
global exception_stub_table
exception_stub_table:
%assign vector 0
%rep 32
    dq exception_stub_%+vector
%assign vector vector + 1
%endrep

global irq_stub_table
irq_stub_table:
%assign vector 32
%rep 16
    dq irq_stub_%+vector
%assign vector vector + 1
%endrep

section .note.GNU-stack noalloc noexec nowrite progbits
