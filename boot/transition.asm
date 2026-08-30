; Minimal original real -> protected -> long-mode transition.
; Fixed RAM ownership is documented in boot/README.md; there is no allocator.
section .boot progbits alloc exec nowrite align=16
bits 16
global boot_transition
extern rynorkernel_entry

boot_transition:
    cli
    cld
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7c00
    ; Mask legacy IRQs and NMI while no kernel exception/interrupt system exists.
    mov al, 0xff
    out 0x21, al
    out 0xa1, al
    mov al, 0x80
    out 0x70, al
    ; Fast A20 gate on the supported QEMU PC machine; keep reset bit clear.
    in al, 0x92
    or al, 2
    and al, 0xfe
    out 0x92, al
    lgdt [gdt_pointer]
    mov eax, cr0
    or eax, 1
    mov cr0, eax
    jmp 0x08:protected_entry

bits 32
protected_entry:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov fs, ax
    mov gs, ax
    mov esp, 0x7c00
    ; x86-64 is the hardware contract; require extended CPUID long-mode support.
    mov eax, 0x80000000
    cpuid
    cmp eax, 0x80000001
    jb unsupported_cpu
    mov eax, 0x80000001
    cpuid
    test edx, 1 << 29
    jz unsupported_cpu

    xor eax, eax
    mov edi, 0x1000
    mov ecx, (3 * 4096) / 4
    rep stosd
    mov dword [0x1000], 0x2003  ; PML4[0] -> PDPT, present/write.
    mov dword [0x2000], 0x3003  ; PDPT[0] -> page directory.
    mov dword [0x3000], 0x0083  ; Identity map 0..2 MiB, one large page.
    mov eax, cr4
    or eax, 1 << 5             ; PAE is required for long mode.
    mov cr4, eax
    mov eax, 0x1000
    mov cr3, eax
    mov ecx, 0xc0000080
    rdmsr
    or eax, 1 << 8             ; IA32_EFER.LME.
    wrmsr
    mov eax, cr0
    or eax, (1 << 31) | (1 << 16) ; Paging and supervisor write protection.
    mov cr0, eax
    jmp 0x18:long_entry

unsupported_cpu:
    ; Unsupported hardware stops silently; the bounded host boot test fails.
    hlt
    jmp unsupported_cpu

bits 64
long_entry:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    xor eax, eax
    mov fs, ax
    mov gs, ax
    jmp rynorkernel_entry

align 8
gdt:
    dq 0
    dq 0x00cf9a000000ffff       ; 0x08: flat 32-bit executable code.
    dq 0x00cf92000000ffff       ; 0x10: flat writable data.
    dq 0x00af9a000000ffff       ; 0x18: 64-bit code, L=1, D=0.
gdt_end:
gdt_pointer:
    dw gdt_end - gdt - 1
    dd gdt

section .note.GNU-stack noalloc noexec nowrite progbits
