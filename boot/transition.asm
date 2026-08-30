; Minimal original real -> protected -> long-mode transition.
; Firmware map is collected before BIOS services become unavailable.
section .boot progbits alloc exec nowrite align=16
bits 16
global boot_transition
extern rynorkernel_entry
extern __boot_map_start
extern __boot_stack_end
extern __page_tables_start
extern __page_tables_end

boot_transition:
    cli
    cld
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, __boot_stack_end
    call acquire_e820
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

; Fixed 4 KiB handoff page, header 32 bytes, up to 64 slots of 32 bytes.
; Each slot: firmware's 20/24 bytes, returned size, zero reserved word.
; No partial/truncated map is marked complete. Bounded continuation loop.
acquire_e820:
    mov di, __boot_map_start
    xor ax, ax
    mov cx, 4096 / 2
    rep stosw
    mov dword [__boot_map_start], 0x50414d52 ; 'RMAP'
    mov dword [__boot_map_start + 4], 1
    mov dword [__boot_map_start + 12], 64
    mov dword [__boot_map_start + 16], 32
    mov dword [__boot_map_start + 24], 4096
    xor ebx, ebx
    mov di, __boot_map_start + 32
.next:
    mov dword [es:di + 20], 1  ; Default enabled attributes for 20-byte BIOSes.
    mov eax, 0xe820
    mov edx, 0x534d4150        ; 'SMAP'
    mov ecx, 24
    push ds
    push es
    push di
    sti
    int 0x15
    cli                      ; CLI/POP preserve carry from BIOS.
    pop di
    pop es
    pop ds
    jc .carry
    cmp eax, 0x534d4150
    jne .failed
    cmp ecx, 20
    je .size_ok
    cmp ecx, 24
    jne .failed
.size_ok:
    mov [es:di + 24], ecx
    inc dword [__boot_map_start + 8]
    test ebx, ebx
    jz .complete
    cmp dword [__boot_map_start + 8], 64
    jae .failed
    add di, 32
    jmp .next
.carry:
    ; E820 permits CF to signal end after at least one successful record.
    cmp dword [__boot_map_start + 8], 0
    je .failed
.complete:
    mov dword [__boot_map_start + 20], 1
    cld
    ret
.failed:
    mov dword [__boot_map_start + 20], 2
    cld
    ret                      ; Kernel emits a diagnostic and refuses PMM init.

bits 32
protected_entry:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov fs, ax
    mov gs, ax
    mov esp, __boot_stack_end
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
    mov edi, __page_tables_start
    mov ecx, __page_tables_end
    sub ecx, edi
    shr ecx, 2
    rep stosd
    mov dword [__page_tables_start], __page_tables_start + 4096 + 3
    mov dword [__page_tables_start + 4096], __page_tables_start + 8192 + 3
    mov dword [__page_tables_start + 8192], 0x0083 ; 0..2 MiB, one large page.
    mov eax, cr4
    or eax, 1 << 5             ; PAE is required for long mode.
    mov cr4, eax
    mov eax, __page_tables_start
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
