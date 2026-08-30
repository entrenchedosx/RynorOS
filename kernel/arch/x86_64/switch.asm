bits 64
default rel
section .text

; struct exception_frame layout (offsets): r15=0,r14=8,r13=16,r12=24,r11=32,
;   r10=40,r9=48,r8=56,rdi=64,rsi=72,rbp=80,rdx=88,rcx=96,rbx=104,rax=112,
;   vector=120,error=128,rip=136,cs=144,rflags=152,rsp=160,ss=168 (176 bytes).

; void sched_resume(struct exception_frame *next) -- noreturn
; Set RSP to the given frame, restore GPRs, skip the software vector/error slot
; and IRETQ to the frame's RIP/RSP. Used for one-way transfers (thread exit and
; one-shot dispatch); the caller never continues.
global sched_resume
sched_resume:
    mov rsp, rdi
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
    add rsp, 16
    iretq

; void thread_switch(struct exception_frame *out, struct exception_frame *next)
; IF=0 throughout entry, capture and restore. C has validated next. This is a
; SysV call boundary: caller-saved GPRs/flags need not retain their pre-call values.
; Preserve the actual IF=0 flags here; thread_yield restores its caller's IF after
; .resume returns. IRQ preemption instead saves all GPRs/flags at instruction scope.
global thread_switch
thread_switch:
    mov [rdi + 0], r15
    mov [rdi + 8], r14
    mov [rdi + 16], r13
    mov [rdi + 24], r12
    mov [rdi + 32], r11
    mov [rdi + 40], r10
    mov [rdi + 48], r9
    mov [rdi + 56], r8
    mov [rdi + 64], rdi
    mov [rdi + 72], rsi
    mov [rdi + 80], rbp
    mov [rdi + 88], rdx
    mov [rdi + 96], rcx
    mov [rdi + 104], rbx
    mov [rdi + 112], rax
    mov qword [rdi + 120], 0
    mov qword [rdi + 128], 0
    lea rax, [rel .resume]
    mov [rdi + 136], rax
    mov qword [rdi + 144], 0x08
    pushfq
    pop rax
    mov [rdi + 152], rax
    mov [rdi + 160], rsp
    mov qword [rdi + 168], 0x10
    mov rdi, rsi
    jmp sched_resume
.resume:
    ret

section .note.GNU-stack noalloc noexec nowrite progbits
