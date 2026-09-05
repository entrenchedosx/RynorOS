; RynorLang Stage 16 host program runtime (Linux x86-64).
;
; HOST BOOTSTRAP -- NOT a RynorOS syscall interface. This object exists so
; compiled .rl programs link into real host-native executables for testing.
; It provides process startup/exit plus the three print helpers the Stage 16
; RIR runtime table (rt_print_int/bool/str) resolves against. No heap, no
; allocator, no GC: one 32-byte static conversion buffer plus caller stack.
; All single writes are bounded (<= 4096 bytes, the str cap), hence atomic
; on pipes (PIPE_BUF) — never partial, never truncated. Only caller-saved
; registers are touched; rsp discipline follows docs/design/rynorlang-abi.md.
bits 64
default rel
global _start
global rl_12_rt_print_int
global rl_13_rt_print_bool
global rl_12_rt_print_str
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

section .bss align=8
_rl_rt_buf:
    resb 32
