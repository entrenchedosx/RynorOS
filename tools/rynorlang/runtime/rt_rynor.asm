; RynorLang Stage 18b program runtime (RynorOS ABI, NOT Linux).
;
; RYNOROS BOOTSTRAP -- NOT a host interface. This object exists so compiled
; .rl programs link into RynorOS userspace executables (see
; docs/design/executable-format.md). It provides process startup/exit plus
; the three print helpers the RIR runtime table (rt_print_int/bool/str)
; resolves against. Syscalls go through the int $0x80 gate only:
; exit (EAX=0, EBX=status), write (EAX=2, EBX=fd, ECX=buf, EDX=len).
; Only fd 1 exists; syscall addresses are 32-bit (the RynorOS user window
; lives below 4 GiB by layout -- see docs/design/syscall-abi.md). Only
; caller-saved registers are touched except RAX (return); rsp discipline
; follows docs/design/rynorlang-abi.md. Scratch lives on the caller
; stack (no .bss needed).
bits 64
default rel
global _start
global rl_12_rt_print_int
global rl_13_rt_print_bool
global rl_12_rt_print_str
extern rl_4_main
; _start opens its own input section so the linker script pins it at the
; code base: the RYNX entry offset is defined as zero (see executable-format.md).
section .text.start
_start:
    call rl_4_main
    mov ebx, eax
    mov eax, 0
    int 0x80

section .text
; void rl_12_rt_print_int(int64 rdi): decimal, exact bytes, no newline.
rl_12_rt_print_int:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov rax, rdi
    lea rsi, [rbp - 16]
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
    mov edx, ecx
    sub edx, esi
    mov ecx, esi
    mov ebx, 1
    mov eax, 2
    int 0x80
    add rsp, 48
    pop rbp
    ret

; void rl_13_rt_print_bool(bool rdi): "true"/"false", exact bytes.
rl_13_rt_print_bool:
    push rbp
    mov rbp, rsp
    sub rsp, 16
    test rdi, rdi
    jnz .true
    mov dword [rbp - 8], 0x736c6146
    mov byte [rbp - 4], 0x65
    lea rsi, [rbp - 8]
    mov rdx, 5
    jmp .write
.true:
    mov dword [rbp - 8], 0x65757274
    lea rsi, [rbp - 8]
    mov rdx, 4
.write:
    mov ecx, esi
    mov ebx, 1
    mov eax, 2
    int 0x80
    add rsp, 16
    pop rbp
    ret

; void rl_12_rt_print_str(ptr rdi, len rsi): raw bytes, exact length.
; Zero-length sides never dereference (no touch when len == 0).
rl_12_rt_print_str:
    test rsi, rsi
    jz .done
    mov edx, esi
    mov ecx, edi
    mov ebx, 1
    mov eax, 2
    int 0x80
.done:
    ret

section .note.GNU-stack noalloc noexec no progbits
