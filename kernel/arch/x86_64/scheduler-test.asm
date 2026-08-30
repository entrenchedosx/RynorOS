bits 64
default rel
section .text
global sched_test_loop, sched_test_loop_begin, sched_test_loop_end
; void sched_test_loop(stop*, probe*). probe={iterations,actual_rsp,error}.
; NO calls/yields/HLT inside the busy loop: only hardware IRQ preemption can
; let another thread progress. Check non-argument GPRs and DF/CF restoration.
sched_test_loop:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov [rsi+8], rsp
    mov rbx, 0x102
    mov rcx, 0x103
    mov rdx, 0x104
    mov rbp, 0x105
    mov r8, 0x108
    mov r9, 0x109
    mov r10, 0x10a
    mov r11, 0x10b
    mov r12, 0x10c
    mov r13, 0x10d
    mov r14, 0x10e
    mov r15, 0x10f
    mov rax, 0x101
sched_test_loop_begin:
    cmp rax, 0x101
    jne .bad
    cmp rbx, 0x102
    jne .bad
    cmp rcx, 0x103
    jne .bad
    cmp rdx, 0x104
    jne .bad
    cmp rbp, 0x105
    jne .bad
    cmp r8, 0x108
    jne .bad
    cmp r9, 0x109
    jne .bad
    cmp r10, 0x10a
    jne .bad
    cmp r11, 0x10b
    jne .bad
    cmp r12, 0x10c
    jne .bad
    cmp r13, 0x10d
    jne .bad
    cmp r14, 0x10e
    jne .bad
    cmp r15, 0x10f
    jne .bad
    cmp rsp, [rsi+8]
    jne .bad
    std
    stc
    pause
    pushfq
    pop rax
    cld
    and eax, 0x401
    cmp eax, 0x401
    jne .bad
    inc qword [rsi]
    cmp qword [rdi], 0
    jne .done
    mov rax, 0x101
    jmp sched_test_loop_begin
.bad:
    mov qword [rsi+16], 1
.done:
    cld
sched_test_loop_end:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
section .note.GNU-stack noalloc noexec nowrite progbits
