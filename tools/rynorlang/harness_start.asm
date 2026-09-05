; RynorLang Stage 15a host execution harness entry.
;
; Reviewed test infrastructure, NOT emitted code and NOT part of any RynorOS
; image. Links against compiler-emitted objects (which define rl_main) and
; provides process startup/exit only: call rl_4_main, exit with its low 8 bits.
; The emitter under test never defines _start and never emits syscall.
bits 64
default rel
global _start
extern rl_4_main
section .text
_start:
    call rl_4_main
    mov rdi, rax
    mov rax, 60
    syscall
