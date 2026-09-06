bits 64
default rel
section .text
global cpu_load_gdt
global cpu_load_task

; RDI points to a packed 10-byte GDTR. Entries 0-2 are kernel DPL 0;
; entries 3-4 are the Stage 18a DPL-3 user descriptors (see cpu.c).
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
    ; No LDT exists: LLDT-null marks it explicitly invalid, so a TI-bit
    ; selector faults canonically as #GP(index/TI) before any memory
    ; access (observed: CPL3 TI load raises #GP(0x1C)), independent of
    ; reset LDTR details.
    lldt ax
    ret

; DI carries the TSS selector. The TSS must be fully initialized first.
cpu_load_task:
    ltr di
    ret

section .note.GNU-stack noalloc noexec nowrite progbits
