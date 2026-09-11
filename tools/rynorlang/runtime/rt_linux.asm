; RynorLang Stage 16 host program runtime (Linux x86-64).
;
; HOST BOOTSTRAP -- NOT a RynorOS syscall interface. This object exists so
; compiled .rl programs link into real host-native executables for testing.
; It provides process startup/exit plus the three print helpers the Stage 16
; RIR runtime table (rt_print_int/bool/str) resolves against, plus the
; Stage 19e file builtins (rt_fread/rt_fjoin over a 1 MiB bump arena for
; stable result strings). No heap, no allocator, no GC: one 32-byte static
; conversion buffer plus caller stack.
; All single writes are bounded (<= 4096 bytes, the str cap), hence atomic
; on pipes (PIPE_BUF) -- never partial, never truncated. Only caller-saved
; registers are touched; rsp discipline follows docs/design/rynorlang-abi.md.
bits 64
default rel
global _start
global rl_12_rt_print_int
global rl_13_rt_print_bool
global rl_12_rt_print_str
global rl_8_rt_fread
global rl_8_rt_fjoin
extern rl_4_main
section .text
_start:
    call rl_4_main
    mov rdi, rax
    mov rax, 60
    syscall

; void rl_12_rt_print_int(int64 rdi): decimal, exact bytes, no newline.
rl_12_rt_print_int:
    mov rax, rdi
    lea rsi, [rel _rl_rt_buf + 32]
    mov rcx, rsi
    test rax, rax
    jnz .nonzero
    dec rsi
    mov byte [rsi], '0'
    jmp .write
.nonzero:
    mov r8, rax
    test rax, rax
    jns .digits
    neg r8
.digits:
    mov rax, r8
.digit:
    xor edx, edx
    mov r9, 10
    div r9
    add dl, '0'
    dec rsi
    mov [rsi], dl
    test rax, rax
    jnz .digit
    cmp rdi, 0
    jns .write
    dec rsi
    mov byte [rsi], '-'
.write:
    mov rdx, rcx
    sub rdx, rsi
    mov rdi, 1
    mov rax, 1
    syscall
    ret

; void rl_13_rt_print_bool(bool rdi): "true"/"false", exact bytes.
rl_13_rt_print_bool:
    test rdi, rdi
    jnz .true
    lea rsi, [rel _rl_rt_false]
    mov rdx, 5
    jmp .write
.true:
    lea rsi, [rel _rl_rt_true]
    mov rdx, 4
.write:
    mov rdi, 1
    mov rax, 1
    syscall
    ret

; void rl_12_rt_print_str(ptr rdi, len rsi): raw bytes, exact length.
; Zero-length sides never dereference (no touch when len == 0).
rl_12_rt_print_str:
    test rsi, rsi
    jz .done
    mov rdx, rsi
    mov rsi, rdi
    mov rdi, 1
    mov rax, 1
    syscall
.done:
    ret

section .rodata align=8
_rl_rt_true:
    db 0x74, 0x72, 0x75, 0x65
_rl_rt_false:
    db 0x66, 0x61, 0x6c, 0x73, 0x65

section .text

; Stage 19e file builtins. Both return rax = byte count (>= 0) with the
; stable result address in rdx, or rax = negative err code (-2 NOTFOUND,
; -3 RANGE/invalid-arg, -4 NOMEM arena overflow, -5 IO/other). Results
; live in a 1 MiB bump arena (8-aligned bump; addresses never leak into
; program output, so ASLR cannot perturb determinism). Only caller-saved
; registers and caller stack are touched.

; long rl_8_rt_fread(ptr rdi, len rsi, off rdx, max rcx): open + fstat
; size check + pread into the arena + close. Short-at-EOF reads are ok
; (kernel fread semantics); offset past end is RANGE.
rl_8_rt_fread:
    test rcx, rcx
    js .range
    cmp rcx, 16384
    ja .range
    test rdx, rdx
    js .range
    test rsi, rsi
    js .range
    cmp rsi, 4096
    ja .range
    sub rsp, 4288
    mov [rsp + 4264], rdx
    mov [rsp + 4272], rcx
    mov r8, rsi
    mov rsi, rdi
    lea rdi, [rsp]
    mov rcx, r8
    cld
    rep movsb
    mov byte [rdi], 0
    lea rdi, [rsp]
    xor esi, esi
    xor edx, edx
    mov eax, 2
    syscall
    test rax, rax
    js .openfail
    mov r8, rax
    lea rsi, [rsp + 4112]
    mov rdi, r8
    mov eax, 5
    syscall
    test rax, rax
    js .close_ioerr
    mov rax, [rsp + 4112 + 48]
    cmp qword [rsp + 4264], rax
    ja .close_range
    mov r9, [rel _rl_fread_next]
    test r9, r9
    jnz .have_next
    lea r9, [rel _rl_fread_base]
.have_next:
    mov r10, r9
    add r10, [rsp + 4272]
    lea rax, [rel _rl_fread_base + 1048576]
    cmp r10, rax
    ja .close_nomem
    mov rdi, r8
    mov rsi, r9
    mov rdx, [rsp + 4272]
    mov r10, [rsp + 4264]
    mov eax, 17
    syscall
    test rax, rax
    js .close_ioerr
    mov [rsp + 4280], rax
    mov [rsp + 4272], r9
    mov rdi, r8
    mov eax, 3
    syscall
    mov rax, [rsp + 4280]
    mov r9, [rsp + 4272]
    add r9, rax
    add r9, 7
    and r9, -8
    mov [rel _rl_fread_next], r9
    mov rdx, [rsp + 4272]
    add rsp, 4288
    ret
.openfail:
    cmp eax, -2
    je .notfound
    mov rax, -5
    add rsp, 4288
    ret
.notfound:
    mov rax, -2
    add rsp, 4288
    ret
.close_ioerr:
    mov rdi, r8
    mov eax, 3
    syscall
    mov rax, -5
    add rsp, 4288
    ret
.close_range:
    mov rdi, r8
    mov eax, 3
    syscall
    mov rax, -3
    add rsp, 4288
    ret
.close_nomem:
    mov rdi, r8
    mov eax, 3
    syscall
    mov rax, -4
    add rsp, 4288
    ret
.range:
    mov rax, -3
    ret

; long rl_8_rt_fjoin(dirptr rdi, dirlen rsi, relptr rdx, rellen rcx):
; pure byte join into the arena (no syscalls).
rl_8_rt_fjoin:
    test rcx, rcx
    jz .range
    movzx eax, byte [rdx]
    cmp al, '/'
    je .range
    test rsi, rsi
    js .range
    mov r8, rsi
    test r8, r8
    jz .no_sep
    inc r8
.no_sep:
    add r8, rcx
    cmp r8, 4096
    ja .range
    mov r9, [rel _rl_fread_next]
    test r9, r9
    jnz .have_next
    lea r9, [rel _rl_fread_base]
.have_next:
    mov r10, r9
    add r10, r8
    lea rax, [rel _rl_fread_base + 1048576]
    cmp r10, rax
    ja .nomem
    mov r11, r9
    test rsi, rsi
    jz .skip_dir
    mov r10, rsi
.copy_dir:
    test r10, r10
    jz .dir_done
    mov al, [rdi]
    mov [r11], al
    inc rdi
    inc r11
    dec r10
    jmp .copy_dir
.dir_done:
    mov byte [r11], '/'
    inc r11
.skip_dir:
    mov r10, rcx
.copy_rel:
    test r10, r10
    jz .rel_done
    mov al, [rdx]
    mov [r11], al
    inc rdx
    inc r11
    dec r10
    jmp .copy_rel
.rel_done:
    mov rdx, r9
    mov rax, r8
    add r9, r8
    add r9, 7
    and r9, -8
    mov [rel _rl_fread_next], r9
    ret
.range:
    mov rax, -3
    ret
.nomem:
    mov rax, -4
    ret

section .bss align=8
_rl_rt_buf:
    resb 32
_rl_fread_base:
    resb 1048576
_rl_fread_next:
    resq 1
