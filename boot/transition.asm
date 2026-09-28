; BOOT-A1 real -> protected -> long-mode transition with high kernel load.
; The BIOS sector loads this fixed 4 KiB boot part to 0x8000 only. This
; code reads the build-generated header sector (LBA 9), validates it,
; chunk-reads the kernel file into low staging and copies it to its
; 8 MiB link base with 32-bit moves (unreal mode), verifies the file
; checksum, then performs the original E820/display/A20/mode transition
; with page tables sized for the kernel range. Any failure prints a
; COM1 diagnostic and halts; control never reaches a partial kernel.
section .boot progbits alloc exec nowrite align=16
bits 16
global boot_transition
extern rynorkernel_entry
extern __boot_map_start
extern __fb_info_start
extern __boot_stack_end
extern __page_tables_start
extern __page_tables_end

; Frozen layout (audited; docs/design/boot.md). KERN_BASE and the caps
; are pinned against the linker script by the host ABI suite.
HDR_LBA equ 9
HDR_BASE equ 0x6000             ; header scratch, transient
STAGE_BASE equ 0x10000          ; disk staging, transient
STAGE_LEN equ 0x60000           ; 384 KiB staging window
KERN_LBA equ 10
CALL_SECTORS equ 64             ; sectors per INT 13h call (32 KiB, seg-safe)
HDR_MAGIC equ 0x4E484252        ; 'RBHN'
HDR_VERSION equ 1
KERN_BASE equ 0x800000
FILE_MAX_SECTORS equ 16384      ; 8 MiB file cap
MEM_MAX_PAGES equ 4096          ; 16 MiB mem cap
PD_2MB equ 0x200000
PD_MAX equ 12                   ; (8 MiB + 16 MiB) / 2 MiB

boot_transition:
    cli
    cld
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, __boot_stack_end
    mov [boot_drive_save], dl   ; sector passes the boot drive in DL
    lgdt [gdt_pointer]          ; unreal mode needs descriptors early
    call acquire_e820
    call enable_a20
    call unreal_on              ; 4 GiB DS/ES for high copies (re-armed
                                ; after every BIOS call, which may reload
                                ; segment descriptors).
    call a20_alias_test
    call load_kernel            ; header + chunk copy + checksum
    call acquire_display
    ; Mask legacy IRQs and NMI while no kernel exception/interrupt system exists.
    mov al, 0xff
    out 0x21, al
    out 0xa1, al
    mov al, 0x80
    out 0x70, al
    ; GDT was loaded before unreal mode; descriptors are already live.
    mov eax, cr0
    or eax, 1
    mov cr0, eax
    jmp 0x08:protected_entry

; Fast A20 gate on the supported QEMU PC machine; keep reset bit clear.
; Failure (read-back clear) halts: no high copy is attempted blind.
enable_a20:
    in al, 0x92
    or al, 2
    and al, 0xfe
    out 0x92, al
    in al, 0x92
    test al, 2
    jnz .ok
    mov si, msg_a20
    jmp boot_fail
.ok:
    ret

; Big-real-mode data segments: brief protected-mode excursion loads the
; flat 0x10 descriptor into DS/ES, then CS returns to real mode while
; DS/ES keep 4 GiB limits. SS/FS/GS are real. Interrupts stay off.
; The return is two-stage: a 32-bit far jump lands in the 16-bit
; protected descriptor first, because a far jump executed with CS.D=1
; decodes as ptr16:32 and would leave CS.D=1 in real mode (every later
; CALL/RET would pop 32 bits and go wild). Only a 16-bit far jump
; decoded with CS.D=0 restores a true real-mode CS.
unreal_on:
    mov eax, cr0
    or eax, 1
    mov cr0, eax
    jmp 0x08:unreal_pm
bits 32
unreal_pm:
    mov bx, 0x10
    mov ds, bx
    mov es, bx
    jmp 0x20:unreal_pm16
bits 16
unreal_pm16:
    mov eax, cr0
    and eax, 0xfffffffe
    mov cr0, eax
    jmp 0:unreal_rm
unreal_rm:
    xor ax, ax
    mov ss, ax
    mov fs, ax
    mov gs, ax
    ret

; Prove A20 with a wraparound alias test: distinct patterns at 0x0 and
; 0x100000 must read back distinct. Interrupts are off and both cells
; are restored, so the transient IVT write is invisible to firmware.
; All 32-bit accesses below assemble in bits 16: the CPU runs with
; CS.D=0, so every 32-bit operand/address needs its 0x66/0x67 prefix,
; which NASM emits automatically for 32-bit registers and addresses
; above 64 KiB. String ops, JECXZ and LOOP are avoided (their implicit
; address/counter size would be 16-bit); explicit loops only, no stack.
a20_alias_test:
    mov eax, [0x0000]
    mov [alias_lo], eax
    a32 mov eax, [0x100000]
    mov [alias_hi], eax
    a32 mov dword [0x100000], 0xA55A00FF
    mov dword [0x0000], 0x5AA5FF00
    a32 mov eax, [0x100000]
    cmp eax, 0xA55A00FF
    jne .aliased
    mov eax, [0x0000]
    cmp eax, 0x5AA5FF00
    jne .aliased
    mov eax, [alias_lo]
    mov [0x0000], eax
    mov eax, [alias_hi]
    a32 mov [0x100000], eax
    ret
.aliased:
    mov eax, [alias_hi]
    a32 mov [0x100000], eax
    mov eax, [alias_lo]
    mov [0x0000], eax
    mov si, msg_a20
    jmp boot_fail

; INT 13h AH=42h single request. EAX = LBA (low 32; high dword stays
; zero), BX = destination segment, CX = sector count (1..CALL_SECTORS),
; offset always zero. Carry set on BIOS error. Clobbers AX/BX/CX/DX/SI.
; The BIOS runs with canonical DS=0 (SeaBIOS forms flat pointers as
; seg<<4|off, so a flat DS would misaddress the packet); DS is NOT
; restored. Every caller re-arms unreal state before any DS/ES use, and
; the failure path reaches boot_fail with a working DS, so the
; diagnostic always prints.
bios_read:
    mov [packet_lba], eax
    mov dword [packet_lba + 4], 0
    mov [packet_seg], bx
    mov [packet_count], cx
    mov dl, [boot_drive_save]
    mov si, packet
    xor ax, ax
    mov ds, ax
    mov ah, 0x42
    int 0x13
    ret

; Boot header and kernel load. Header (LBA 9) at HDR_BASE:
;   +0  u32 magic, +4 u16 version, +6 u16 flags(0),
;   +8  u32 file_sectors, +12 u32 mem_pages, +16 u32 fnv1a checksum,
;   +20 u32 load_base, +24 u32 entry, +28 u32 reserved(0).
load_kernel:
    mov eax, HDR_LBA
    mov bx, HDR_BASE >> 4
    mov cx, 1
    call bios_read
    jc disk_fail
    call unreal_on
    cmp dword [HDR_BASE], HDR_MAGIC
    jne header_fail
    cmp word [HDR_BASE + 4], HDR_VERSION
    jne header_fail
    cmp word [HDR_BASE + 6], 0
    jne header_fail
    cmp dword [HDR_BASE + 20], KERN_BASE
    jne header_fail
    cmp dword [HDR_BASE + 24], KERN_BASE
    jne header_fail
    cmp dword [HDR_BASE + 28], 0
    jne header_fail
    mov eax, [HDR_BASE + 8]         ; file_sectors
    test eax, eax
    jz header_fail
    cmp eax, FILE_MAX_SECTORS
    ja header_fail
    mov [file_sectors], eax
    mov ebx, [HDR_BASE + 12]        ; mem_pages
    lea ecx, [eax + 7]
    shr ecx, 3                      ; file_pages = ceil(sectors/8)
    cmp ebx, ecx
    jb header_fail
    cmp ebx, MEM_MAX_PAGES
    ja header_fail
    mov [mem_pages], ebx
    ; mem_end = KERN_BASE + mem_pages*4096 (32-bit, cap-bounded).
    mov eax, ebx
    shl eax, 12
    jc header_fail
    add eax, KERN_BASE
    jc header_fail
    mov [mem_end], eax
    ; pd_count = ceil(mem_end / 2 MiB), capped to the single boot PD.
    add eax, PD_2MB - 1
    jc header_fail
    shr eax, 21
    test eax, eax
    jz header_fail
    cmp eax, PD_MAX
    ja header_fail
    mov [pd_count], ax
    call range_covered              ; E820 usability of the kernel range
    test al, al
    jz ram_fail
    call copy_chunks
    call verify_checksum
    ret

; Coverage: some E820 usable entry must span [KERN_BASE, mem_end).
; Entries with wrapped ends or insane ordering are skipped (not cover);
; exactness stays the kernel's job, so firmware-corruption tests still
; reach the kernel. Returns AL=1 covered, 0 otherwise.
range_covered:
    push ebx
    push ecx
    push edx
    push esi
    push edi
    mov ecx, [__boot_map_start + 8] ; entry count
    cmp ecx, 64
    ja .none
    mov esi, __boot_map_start + 32
.next:
    test ecx, ecx
    jz .none
    mov eax, [esi + 16]             ; type
    cmp eax, 1
    jne .skip
    mov eax, [esi]                  ; base_lo
    mov ebx, [esi + 4]              ; base_hi
    mov edx, [esi + 8]              ; len_lo
    mov edi, [esi + 12]             ; len_hi
    add edx, eax                    ; end_lo (carry = wrap/extend)
    adc edi, ebx                    ; end_hi
    jc .skip                        ; 64-bit wrap: insane, skip
    test ebx, ebx
    jnz .skip                       ; base above 4 GiB: cannot start low
    cmp eax, KERN_BASE
    ja .skip
    test edi, edi
    jnz .found                      ; end above 4 GiB: covers
    cmp edx, [mem_end]
    jb .skip
.found:
    mov al, 1
    jmp .done
.skip:
    add esi, 32
    dec ecx
    jmp .next
.none:
    xor al, al
.done:
    pop edi
    pop esi
    pop edx
    pop ecx
    pop ebx
    ret

; Chunk loop: read the kernel file (LBA KERN_LBA) into staging in
; STAGE_LEN pieces and copy each to KERN_BASE + done. All counts are
; 32-bit with carry checks; BIOS calls stay within one 64 KiB window.
copy_chunks:
    mov eax, [file_sectors]
    shl eax, 9                      ; file_bytes (cap-bounded)
    mov [file_bytes], eax
    xor eax, eax
    mov [bytes_done], eax
    mov eax, KERN_LBA
    mov [disk_lba], eax
.chunk:
    mov eax, [file_bytes]
    sub eax, [bytes_done]
    jz .done
    jc disk_fail                     ; done > total: impossible, fail shut
    cmp eax, STAGE_LEN
    jbe .sized
    mov eax, STAGE_LEN
.sized:
    mov [chunk_bytes], eax
    shr eax, 9                      ; chunk sectors (multiple of 512)
    mov [chunk_sectors], eax
    xor ebx, ebx
    mov [stage_off], ebx
.sector:
    mov eax, [chunk_sectors]
    test eax, eax
    jz .copied
    cmp eax, CALL_SECTORS
    jbe .sized_call
    mov eax, CALL_SECTORS
.sized_call:
    mov ecx, eax                    ; count this call
    mov [call_sectors], eax         ; BIOS clobbers CX: persist the count
    sub [chunk_sectors], eax
    mov eax, [disk_lba]
    add [disk_lba], ecx
    adc dword [disk_lba_hi], 0
    jc disk_fail
    mov ebx, [stage_off]
    shr ebx, 4
    add bx, STAGE_BASE >> 4         ; seg:off, off=0, len 32 KiB max
    call bios_read
    jc disk_fail
    call unreal_on
    mov eax, [call_sectors]
    shl eax, 9                      ; bytes advanced by this call
    add [stage_off], eax
    jc disk_fail
    jmp .sector
.copied:
    ; 32-bit copy: DS:ESI staging -> ES:EDI dest (both flat unreal).
    mov eax, [bytes_done]
    add eax, KERN_BASE
    jc disk_fail
    mov [copy_dst], eax
    mov eax, [chunk_bytes]
    shr eax, 2                      ; dwords (512 divides 4)
    mov [copy_len], eax
    ; Explicit dword loop (see block comment above): no string ops.
    mov esi, STAGE_BASE
    mov edi, [copy_dst]
    mov ecx, [copy_len]
    test ecx, ecx
    jz .copy_done
.copy_next:
    mov eax, [esi]
    mov [edi], eax
    add esi, 4
    add edi, 4
    dec ecx
    jnz .copy_next
.copy_done:
    mov eax, [chunk_bytes]
    add [bytes_done], eax
    jc disk_fail
    jmp .chunk
.done:
    ret

; FNV-1a over the copied file bytes must match the header checksum.
; One byte loop: simple and obviously correct; ~8M iterations worst case.
verify_checksum:
    mov dword [fnv], 0x811c9dc5
    mov eax, [file_bytes]
    mov [copy_len], eax
    mov esi, KERN_BASE
    mov ecx, [copy_len]
    mov ebx, [fnv]
    test ecx, ecx
    jz .done
.next:
    xor eax, eax
    mov al, [esi]
    inc esi
    xor ebx, eax
    imul ebx, ebx, 0x01000193
    dec ecx
    jnz .next
.done:
    mov [fnv], ebx
    mov eax, [fnv]
    cmp eax, [HDR_BASE + 16]
    jne checksum_fail
    ret

header_fail:
    mov si, msg_header
    jmp boot_fail
ram_fail:
    mov si, msg_ram
    jmp boot_fail
disk_fail:
    mov si, msg_disk
    jmp boot_fail
checksum_fail:
    mov si, msg_checksum
    jmp boot_fail

; Fatal boot diagnostic: full COM1 init (firmware state unknown), print
; SI, halt. Never returns; the kernel is never entered partially.
boot_fail:
    cli
    cld
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

; Pinned QEMU stdvga at 00:02.0, not a general PCI enumerator or BIOS VBE call.
; Validate identity, 32-bit prefetchable BAR, actual aperture and mode readback.
; PCI memory decode is disabled while sizing BAR0; restore BAR and command
; before ANY failure exit. Status W1C bits are never written back as ones.
%macro pci_read 1
    mov eax, 0x80001000 + %1
    call .pci_read
%endmacro
%macro pci_write 1
    mov eax, 0x80001000 + %1
    call .pci_write
%endmacro
%macro bga_write 2
    mov ax, %1
    mov cx, %2
    call .bga_write
%endmacro
%macro bga_read 1
    mov ax, %1
    call .bga_read
%endmacro
acquire_display:
    xor ax, ax
    mov ds, ax
    mov es, ax
    cld
    mov di, __fb_info_start
    mov cx, 4096 / 2
    rep stosw
    mov dword [__fb_info_start], 0x44484246 ; 'FBHD'
    mov dword [__fb_info_start + 4], 2      ; version
    mov dword [__fb_info_start + 8], 2      ; pending failure
    pci_read 0
    cmp eax, 0x11111234
    jne .fail
    mov [__fb_info_start + 60], eax
    pci_read 8
    shr eax, 16
    cmp ax, 0x0300
    jne .fail
    pci_read 4
    movzx ebp, ax
    and ax, 3
    cmp ax, 3
    jne .fail
    pci_read 0x10
    mov ebx, eax
    and eax, 15
    cmp eax, 8                 ; memory, 32-bit, prefetchable
    jne .fail
    mov ecx, ebp
    and ecx, 0xfffffffd
    pci_write 4
    mov ecx, 0xffffffff
    pci_write 0x10
    pci_read 0x10
    and eax, 0xfffffff0
    neg eax
    mov esi, eax               ; actual BAR aperture size
    mov ecx, ebx
    pci_write 0x10
    mov ecx, ebp
    pci_write 4
    pci_read 0x10
    cmp eax, ebx
    jne .fail
    pci_read 4
    movzx eax, ax
    cmp eax, ebp
    jne .fail
    and ebx, 0xfffffff0
    cmp ebx, 0x100000
    jb .fail
    cmp esi, 4096
    jb .fail
    cmp esi, 0x10000000
    ja .fail
    mov eax, esi
    dec eax
    test eax, esi
    jnz .fail
    test ebx, eax
    jnz .fail
    mov eax, ebx
    add eax, esi
    jnc .extent_ok
    test eax, eax              ; exclusive end of exactly 4 GiB is valid
    jnz .fail
.extent_ok:
    mov [__fb_info_start + 36], ebx
    mov [__fb_info_start + 56], esi
    bga_read 0
    cmp ax, 0xb0c0
    jb .fail
    cmp ax, 0xb0c5
    ja .fail
    bga_write 4, 0
    bga_write 0, 0xb0c5
    bga_read 0
    cmp ax, 0xb0c5
    jne .fail
    mov [__fb_info_start + 12], ax
    bga_read 0x0a
    movzx eax, ax
    shl eax, 16
    cmp eax, esi
    jne .fail
    bga_write 1, 1024
    bga_write 2, 768
    bga_write 3, 32
    bga_write 4, 0x41
    bga_write 6, 1024           ; virtual width: pitch comes from readback
    bga_write 8, 0
    bga_write 9, 0
    bga_read 4
    cmp ax, 0x41
    jne .fail
    bga_read 8
    test ax, ax
    jnz .fail
    bga_read 9
    test ax, ax
    jnz .fail
    bga_read 1
    cmp ax, 1024
    jne .fail
    mov [__fb_info_start + 16], ax
    bga_read 2
    cmp ax, 768
    jne .fail
    mov [__fb_info_start + 20], ax
    bga_read 3
    cmp ax, 32
    jne .fail
    mov [__fb_info_start + 28], ax
    bga_read 6
    cmp ax, 1024
    jb .fail
    movzx eax, ax
    shl eax, 2
    mov [__fb_info_start + 24], eax
    imul eax, 768
    cmp eax, esi
    ja .fail
    mov byte  [__fb_info_start + 32], 6       ; memory model 6 = direct color
    mov dword [__fb_info_start + 40], 0xff0000 ; known QEMU x86 BGRX format
    mov dword [__fb_info_start + 44], 0xff00   ; green mask
    mov dword [__fb_info_start + 48], 0xff     ; blue mask
    mov dword [__fb_info_start + 8], 1        ; publish complete handoff
.fail:
    ret
.pci_read:
    mov dx, 0xcf8
    out dx, eax
    mov dx, 0xcfc
    in eax, dx
    ret
.pci_write:
    mov dx, 0xcf8
    out dx, eax
    mov dx, 0xcfc
    mov eax, ecx
    out dx, eax
    ret
.bga_read:
    mov dx, 0x1ce
    out dx, ax
    mov dx, 0x1cf
    in ax, dx
    ret
.bga_write:
    mov dx, 0x1ce
    out dx, ax
    mov dx, 0x1cf
    mov ax, cx
    out dx, ax
    ret

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
    ; Identity 2 MiB pages covering low RAM plus the kernel extent; the
    ; count comes from the validated header (1..PD_MAX), never a guess.
    xor ecx, ecx
.fill_pd:
    cmp cx, [pd_count]
    jae .pd_done
    mov eax, ecx
    shl eax, 21
    or eax, 0x83               ; present, writable, 2 MiB page
    mov ebx, ecx
    shl ebx, 3
    mov [__page_tables_start + 8192 + ebx], eax
    inc ecx
    jmp .fill_pd
.pd_done:
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
    dq 0x00009a000000ffff       ; 0x20: 16-bit code for the unreal return.
gdt_end:
gdt_pointer:
    dw gdt_end - gdt - 1
    dd gdt

; Boot-loader state (real-mode addresses; .boot-resident, never moved).
align 4
packet:
    db 16, 0
packet_count: dw 1              ; offset 2
    dw 0                        ; offset 4: buffer offset, always zero
packet_seg: dw 0                ; offset 6: buffer segment per call
packet_lba: dd 0, 0             ; offset 8: 64-bit LBA (high stays zero)
boot_drive_save: db 0
align 4
file_sectors: dd 0
mem_pages: dd 0
mem_end: dd 0
file_bytes: dd 0
bytes_done: dd 0
disk_lba: dd 0
disk_lba_hi: dd 0
chunk_bytes: dd 0
chunk_sectors: dd 0
call_sectors: dd 0
stage_off: dd 0
copy_dst: dd 0
copy_len: dd 0
fnv: dd 0
alias_lo: dd 0, 0
alias_hi: dd 0
pd_count: dw 0
msg_header: db 'Rynor boot: bad header.', 13, 10, 0
msg_ram: db 'Rynor boot: kernel exceeds usable RAM.', 13, 10, 0
msg_disk: db 'Rynor boot: BIOS disk read failed.', 13, 10, 0
msg_checksum: db 'Rynor boot: kernel checksum mismatch.', 13, 10, 0
msg_a20: db 'Rynor boot: A20 gate failed.', 13, 10, 0

section .note.GNU-stack noalloc noexec nowrite progbits
