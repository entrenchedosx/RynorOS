bits 64
default rel
section .text
global cpu_test_trigger
global cpu_test_fault
global cpu_test_after
extern cpu_test_rsp
extern cpu_test_return_flags

; Each image selects ONE deterministic architectural exception. Default: INT3.
; Preserve C callee-saved registers while giving the interrupted frame known
; distinctive values. No C undefined behavior is used to manufacture faults.
cpu_test_trigger:
    push rbp
    push rbx
    push r12
    push r13
    push r14
    push r15
    mov [rel cpu_test_rsp], rsp
    mov eax, 0x101
    mov ebx, 0x102
    mov ecx, 0x103
    mov edx, 0x104
    mov ebp, 0x105
    mov esi, 0x106
    mov edi, 0x107
    mov r8d, 0x108
    mov r9d, 0x109
    mov r10d, 0x10a
    mov r11d, 0x10b
    mov r12d, 0x10c
    mov r13d, 0x10d
    mov r14d, 0x10e
    mov r15d, 0x10f
%if RYNOR_TEST_VECTOR == 0
    mov eax, 1
    xor ecx, ecx
    xor edx, edx
%elif RYNOR_TEST_VECTOR == 13
    mov eax, 0x18              ; Index 3 is beyond the kernel's three-entry GDT.
%endif
%if RYNOR_TEST_VECTOR == 3
    push qword 0x402           ; DF deliberately set; handler must clear for C.
%elif RYNOR_TEST_VECTOR == 1
    push qword 0x102           ; TF causes #DB after exactly one NOP.
%else
    push qword 0x2             ; Known status flags, IF=0.
%endif
    popfq
cpu_test_fault:
%if RYNOR_TEST_VECTOR == 3
    int3
%elif RYNOR_TEST_VECTOR == 0
    div rcx
%elif RYNOR_TEST_VECTOR == 1
    nop
%elif RYNOR_TEST_VECTOR == 6
    ud2
%elif RYNOR_TEST_VECTOR == 13
    mov ds, ax
%elif RYNOR_TEST_VECTOR == 14
    mov rax, [abs 0x200000]    ; First byte beyond existing boot identity map.
%else
    %error "unsupported test vector"
%endif
cpu_test_after:
    ; Only the armed breakpoint is allowed to return. Capture restored flags
    ; before any CMP/CLD changes them, and verify all fifteen GPRs plus RSP.
    pushfq
    pop qword [rel cpu_test_return_flags]
    cld
    cmp rsp, [rel cpu_test_rsp]
    jne .failed
    cmp qword [rel cpu_test_return_flags], 0x402
    jne .failed
    cmp rax, 0x101
    jne .failed
    cmp rbx, 0x102
    jne .failed
    cmp rcx, 0x103
    jne .failed
    cmp rdx, 0x104
    jne .failed
    cmp rbp, 0x105
    jne .failed
    cmp rsi, 0x106
    jne .failed
    cmp rdi, 0x107
    jne .failed
%assign n 8
%rep 8
    cmp r%+n, 0x100 + n
    jne .failed
%assign n n + 1
%endrep
    mov eax, 1
    jmp .return
.failed:
    xor eax, eax
.return:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    pop rbp
    ret

section .note.GNU-stack noalloc noexec nowrite progbits
