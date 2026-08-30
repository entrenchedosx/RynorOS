; Original RynorOS BIOS hard-disk bootstrap. NASM flat binary, exactly 512 bytes.
bits 16
org 0x7c00

%ifndef PAYLOAD_SECTORS
    %error "PAYLOAD_SECTORS must be supplied by the image builder"
%endif
%if PAYLOAD_SECTORS < 1 || PAYLOAD_SECTORS > 832
    %error "payload must fit physical 0x8000..0x70000"
%endif

    jmp 0:start
start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7c00
    cld
    mov [boot_drive], dl
    sti                         ; BIOS disk routines may need interrupts.

    mov ah, 0x41                ; Require INT 13h extended LBA reads.
    mov bx, 0x55aa
    int 0x13
    jc disk_error
    cmp bx, 0xaa55
    jne disk_error
    test cx, 1
    jz disk_error
.read_sector:
    mov word [packet + 2], 1   ; One sector: never cross a BIOS 64 KiB boundary.
    mov dl, [boot_drive]
    mov si, packet
    mov ah, 0x42
    push ds
    int 0x13
    pop ds
    jc disk_error
    add word [packet + 6], 0x20
    inc dword [packet + 8]
    dec word [remaining]
    jnz .read_sector
    cli
    jmp 0:0x8000                ; Linked boot transition, not an ELF loader.

disk_error:
    cli
    ; Error-only polled COM1 diagnostics, independent of kernel serial setup.
    mov dx, 0x3fb
    mov al, 0x80
    out dx, al
    mov dx, 0x3f8
    mov al, 1
    out dx, al
    inc dx
    xor al, al
    out dx, al
    mov dx, 0x3fb
    mov al, 3
    out dx, al
    mov si, error_text
.next:
    lodsb
    test al, al
    jz .halt
    mov bl, al
    mov cx, 0xffff
.wait:
    mov dx, 0x3fd
    in al, dx
    test al, 0x20
    jnz .send
    loop .wait
    jmp .halt
.send:
    mov dx, 0x3f8
    mov al, bl
    out dx, al
    jmp .next
.halt:
    hlt
    jmp .halt

align 4
packet:
    db 16, 0
    dw 1
    dw 0, 0x0800                ; Destination 0800:0000 = physical 0x8000.
    dq 1                        ; Payload starts at disk sector 1.
boot_drive: db 0
remaining: dw PAYLOAD_SECTORS
error_text: db 'Rynor boot: BIOS disk read failed.', 13, 10, 0
times 510 - ($ - $$) db 0
dw 0xaa55
