bits 64
default rel
section .text.entry progbits alloc exec nowrite align=16
global rynorkernel_entry
extern kernel_main
extern __bss_start
extern __bss_end

rynorkernel_entry:
    cli
    cld
    mov rsp, 0x80000           ; Fixed 16 KiB stack below this address.
    xor ebp, ebp
    lea rdi, [__bss_start]
    lea rcx, [__bss_end]
    sub rcx, rdi
    xor eax, eax
    rep stosb
    ; SysV x86-64: stack aligned to 16 before CALL, no red zone or SIMD usage.
    call kernel_main
.halt:
    cli
    hlt
    jmp .halt

section .note.GNU-stack noalloc noexec nowrite progbits
