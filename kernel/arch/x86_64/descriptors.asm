bits 64
default rel
section .text
global cpu_load_gdt

; RDI points to a packed 10-byte GDTR. All descriptors are kernel DPL 0.
cpu_load_gdt:
    lgdt [rdi]
    push qword 0x08
    lea rax, [rel .reload_cs]
    push rax
    retfq
.reload_cs:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    xor eax, eax
    mov fs, ax
    mov gs, ax
    ret

section .note.GNU-stack noalloc noexec nowrite progbits
