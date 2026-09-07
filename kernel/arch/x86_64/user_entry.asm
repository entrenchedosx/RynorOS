bits 64
default rel
section .text
global user_enter_asm
global user_kern_resume
extern user_kernel_cr3

; cpu_u64 user_enter_asm(struct exception_frame *frame,
;                        struct exception_frame *kern_save, cpu_u64 user_cr3)
; Record a frame_valid-compatible kernel resume, load the user address
; space, and IRETQ to CPL3. IF=0 on entry (enforced with CLI); the user
; frame must carry IF=1 so the timer keeps preempting. Never returns
; directly: a later scheduler resume lands on user_kern_resume, which
; reloads the kernel CR3 (defense in depth; resumes always arrive on it)
; and RETs into the entry caller with the scheduled code in RAX.
user_enter_asm:
    cli
    mov qword [rsi + 0], 0
    mov qword [rsi + 8], 0
    mov qword [rsi + 16], 0
    mov qword [rsi + 24], 0
    mov qword [rsi + 32], 0
    mov qword [rsi + 40], 0
    mov qword [rsi + 48], 0
    mov qword [rsi + 56], 0
    mov qword [rsi + 64], 0
    mov qword [rsi + 72], 0
    mov [rsi + 80], rbp
    mov qword [rsi + 88], 0
    mov qword [rsi + 96], 0
    mov [rsi + 104], rbx
    mov qword [rsi + 112], 0
    mov [rsi + 0], r15
    mov [rsi + 8], r14
    mov [rsi + 16], r13
    mov [rsi + 24], r12
    mov qword [rsi + 120], 0
    mov qword [rsi + 128], 0
    lea rax, [rel user_kern_resume]
    mov [rsi + 136], rax
    mov qword [rsi + 144], 0x08
    pushfq
    pop rax
    and rax, 0x10ed7
    mov [rsi + 152], rax
    mov [rsi + 160], rsp
    mov qword [rsi + 168], 0x10
    mov cr3, rdx
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
user_kern_resume:
    ; RAX (the scheduled return code) must survive; use RCX.
    mov rcx, [rel user_kernel_cr3]
    mov cr3, rcx
    ret

; Test blobs. Absolute addressing only: every memory operand goes through
; a base register loaded with movabs, short jumps are position
; independent, and no RIP-relative access appears (the blob runs at the
; user base, not the kernel link address). Each blob ends with UD2 so a
; gate that ever returned would trap loudly instead of running on.
section .rodata
global user_blob_exit
global user_blob_exit_end
user_blob_exit:
    mov eax, 0
    mov ebx, 42
    int 0x80
    ud2
user_blob_exit_end:

global user_blob_spin
global user_blob_spin_end
user_blob_spin:
    mov r11, 0x600000
    mov rax, 0xF00D000000000000
    mov rbx, 0xF00D000000000001
    mov rcx, 0xF00D000000000002
    mov rdx, 0xF00D000000000003
    mov rsi, 0xF00D000000000004
    mov rdi, 0xF00D000000000005
    mov rbp, 0xF00D000000000006
    mov r8, 0xF00D000000000007
    mov r9, 0xF00D000000000008
    mov r10, 0xF00D000000000009
    mov r12, 0xF00D00000000000b
    mov r13, 0xF00D00000000000c
    mov r14, 0xF00D00000000000d
    mov r15, 0xF00D00000000000e
.spin:
    mov r11, 0x600000
    mov [r11 + 0x10], rax
    mov [r11 + 0x18], rbx
    mov [r11 + 0x20], rcx
    mov [r11 + 0x28], rdx
    mov [r11 + 0x30], rsi
    mov [r11 + 0x38], rdi
    mov [r11 + 0x40], rbp
    mov [r11 + 0x48], r8
    mov [r11 + 0x50], r9
    mov [r11 + 0x58], r10
    mov [r11 + 0x60], r11
    mov [r11 + 0x68], r12
    mov [r11 + 0x70], r13
    mov [r11 + 0x78], r14
    mov [r11 + 0x80], r15
    inc qword [r11 + 0x08]
    ; Stop flag set by the kernel from tick 25 on; the 1e9 backstop only
    ; trips if flags never arrive (anti-hang: exact-count asserts then
    ; fail loudly instead of spinning forever). No GPR is clobbered, so
    ; the spill row stays stable across every preemption.
    cmp qword [r11 + 0x00], 0
    jne .spindone
    cmp qword [r11 + 0x08], 1000000000
    jb .spin
.spindone:
    mov eax, 0
    mov ebx, 7
    int 0x80
    ud2
user_blob_spin_end:

global user_blob_yield
global user_blob_yield_end
user_blob_yield:
    mov r11, 0x600000
    mov qword [r11 + 0x08], 0
.yloop:
    inc qword [r11 + 0x08]
    mov rax, [r11 + 0x08]
    cmp rax, 2
    jge .ydone
    mov eax, 1
    int 0x80
    mov r11, 0x600000
    jmp .yloop
.ydone:
    mov eax, 0
    mov ebx, 9
    int 0x80
    ud2
user_blob_yield_end:

global user_blob_ud2
global user_blob_ud2_end
user_blob_ud2:
    ud2
user_blob_ud2_end:

global user_blob_readkern_lo
global user_blob_readkern_lo_end
user_blob_readkern_lo:
    mov rax, 0x8000
    mov rax, [rax]
    ud2
user_blob_readkern_lo_end:

global user_blob_readkern_hi
global user_blob_readkern_hi_end
user_blob_readkern_hi:
    mov rax, 0xFFFFFFFF80000000
    mov rax, [rax]
    ud2
user_blob_readkern_hi_end:

global user_blob_write_rx
global user_blob_write_rx_end
user_blob_write_rx:
    mov rax, 0x400000
    mov qword [rax], 0x1234
    ud2
user_blob_write_rx_end:

global user_blob_exec_data
global user_blob_exec_data_end
user_blob_exec_data:
    mov rax, 0x600000
    jmp rax
    ud2
user_blob_exec_data_end:

global user_blob_cli
global user_blob_cli_end
user_blob_cli:
    cli
    ud2
user_blob_cli_end:

global user_blob_null
global user_blob_null_end
user_blob_null:
    mov rax, 0
    mov rax, [rax]
    ud2
user_blob_null_end:

global user_blob_badcall
global user_blob_badcall_end
user_blob_badcall:
    mov eax, 0x99
    int 0x80
    ud2
user_blob_badcall_end:

; Adversarial CPL3 payloads. Each must fault in exactly its pinned way
; (vector/error/CR2 asserted by user_self_test); none may ever complete.
; Kernel addresses come from the data page (prefilled by user_create);
; everything else is an immediate. Absolute addressing only, as above.
; Selector immediates use RPL=0 throughout: the enforced property is
; privilege/type rejection, and RPL-zero keeps the expected #GP error
; identical whether the CPU reports the full selector or masks RPL.

global user_blob_readkern_text
global user_blob_readkern_text_end
user_blob_readkern_text:
    mov r11, 0x600000
    mov rax, [r11 + 0x90]
    mov rax, [rax]
    ud2
user_blob_readkern_text_end:

global user_blob_writekern_data
global user_blob_writekern_data_end
user_blob_writekern_data:
    mov r11, 0x600000
    mov rax, [r11 + 0x98]
    mov qword [rax], 0x1234
    ud2
user_blob_writekern_data_end:

global user_blob_execkern_text
global user_blob_execkern_text_end
user_blob_execkern_text:
    mov r11, 0x600000
    mov rax, [r11 + 0x90]
    jmp rax
    ud2
user_blob_execkern_text_end:

global user_blob_execstack
global user_blob_execstack_end
user_blob_execstack:
    mov rax, 0x7FF000
    jmp rax
    ud2
user_blob_execstack_end:

global user_blob_readcr3
global user_blob_readcr3_end
user_blob_readcr3:
    mov rax, cr3
    ud2
user_blob_readcr3_end:

global user_blob_kernsel
global user_blob_kernsel_end
user_blob_kernsel:
    mov ax, 0x10
    mov ds, ax
    ud2
user_blob_kernsel_end:

global user_blob_badsel
global user_blob_badsel_end
user_blob_badsel:
    mov ax, 0x40
    mov es, ax
    ud2
user_blob_badsel_end:

global user_blob_tibit
global user_blob_tibit_end
user_blob_tibit:
    mov ax, 0x1C
    mov ds, ax
    ud2
user_blob_tibit_end:

; Far absolute jump through a 10-byte m16:64 pointer staged in the
; data page scratch area (past the prefill slots). Crisp selector
; faults: non-conforming kernel code from CPL3, and data-as-CS.

global user_blob_farjmp_kcs
global user_blob_farjmp_kcs_end
user_blob_farjmp_kcs:
    mov r11, 0x600000
    mov rax, 0x400000
    mov [r11 + 0xa0], rax
    mov word [r11 + 0xa8], 0x08
    jmp far [r11 + 0xa0]
    ud2
user_blob_farjmp_kcs_end:

global user_blob_farjmp_udata
global user_blob_farjmp_udata_end
user_blob_farjmp_udata:
    mov r11, 0x600000
    mov rax, 0x400000
    mov [r11 + 0xa0], rax
    mov word [r11 + 0xa8], 0x18
    jmp far [r11 + 0xa0]
    ud2
user_blob_farjmp_udata_end:

global user_blob_movss
global user_blob_movss_end
user_blob_movss:
    mov ax, 0x20
    mov ss, ax
    ud2
user_blob_movss_end:

global user_blob_divzero
global user_blob_divzero_end
user_blob_divzero:
    mov eax, 1
    xor ecx, ecx
    div ecx
    ud2
user_blob_divzero_end:

global user_blob_syscall
global user_blob_syscall_end
user_blob_syscall:
    syscall
    ud2
user_blob_syscall_end:

; Stack-pointer corruption: the fault must deliver on the TSS exit
; stack and record a kill, never touch a user-controlled stack.

global user_blob_ss_rsp
global user_blob_ss_rsp_end
user_blob_ss_rsp:
    ; 0x800000000008 is noncanonical AND stays noncanonical after the
    ; 8-byte push (0x800000000000): a bottom-edge value like
    ; 0x800000000000 would wrap to canonical and merely #PF instead.
    mov rsp, 0x0000800000000008
    push rax
    ud2
user_blob_ss_rsp_end:

global user_blob_kern_rsp
global user_blob_kern_rsp_end
user_blob_kern_rsp:
    mov r11, 0x600000
    mov rsp, [r11 + 0x98]
    push rax
    ud2
user_blob_kern_rsp_end:

; Forged return frames to kernel CS. CPL3 cannot return inward: both
; must fault (#GP on the kernel selector) with no handler, scheduler,
; or validator ever running. A silent CPL0 entry here would be a full
; escape, so these rows are the most load-bearing in the matrix.

global user_blob_iretq_kcs
global user_blob_iretq_kcs_end
user_blob_iretq_kcs:
    mov r11, 0x600000
    mov rax, [r11 + 0x90]
    push 0x1B
    push 0x7FF000
    push 0x202
    push 0x08
    push rax
    iretq
    ud2
user_blob_iretq_kcs_end:

global user_blob_retfq_kcs
global user_blob_retfq_kcs_end
user_blob_retfq_kcs:
    mov r11, 0x600000
    mov rax, [r11 + 0x90]
    push 0x08
    push rax
    retfq
    ud2
user_blob_retfq_kcs_end:

; MSR access is CPL0-only; complements the EFER/SYSENTER_CS mediation
; checks with a live privileged-instruction fault.

global user_blob_rdmsr
global user_blob_rdmsr_end
user_blob_rdmsr:
    mov ecx, 0x10
    rdmsr
    ud2
user_blob_rdmsr_end:

section .note.GNU-stack noalloc noexec nowrite progbits
