; Stage 18c library entry: the one asm stub file (everything else is C).
;
; _start opens its own input section so the linker script pins it at the
; code base (RYNX entry offset zero). Default flavor calls the C test
; main (rt_main); -DRL_ENTRY calls the RynorLang main (rl_4_main) for
; the print-rebind runtime. Either way the return status becomes the
; exit code through gate 0. Absolute addressing only, like rt_rynor.asm.
bits 64
default rel
global _start
%ifdef RL_ENTRY
extern rl_4_main
%else
extern rt_main
%endif
section .text.start progbits alloc exec
_start:
%ifdef RL_ENTRY
    call rl_4_main
%else
    call rt_main
%endif
    mov ebx, eax
    mov eax, 0
    int 0x80

; Six-argument gate for Stage 18d syscalls (frozen register file:
; EAX number, EBX ECX EDX ESI EDI EBP args, RAX return, all else
; preserved by the kernel). SysV input: rdi=num, rsi=a, rdx=b, rcx=c,
; r8=d, r9=e, [rsp+8]=f. Written in asm (not C constraints) so the
; register placement is auditable, not optimizer-dependent. No stack
; use before reading [rsp+8], so the 7th argument address is exact.
global rt_gate6
section .text
rt_gate6:
    mov eax, edi
    mov rbx, rsi
    mov rdx, r8
    mov rsi, r9
    mov rdi, r9
    mov rbp, [rsp + 8]
    int 0x80
    ret

; One writable data byte. Without a truly writable input, an output
; section holding only mergeable strings is emitted read-only, and lld
; then splits the data window into R-only/RW loads with an alignment
; gap the RYNX tiler rejects. This pins exactly one RW data LOAD. The
; linker script KEEPs this section so --gc-sections (used to fit each
; conformance program in 4 KiB) never drops it. Explicit write attribute:
; NASM custom sections default to alloc-only, which would leave the data
; output section read-only and split the data window again.
section .data.rtanchor progbits alloc write
global rt_data_anchor
rt_data_anchor:
    db 0

section .note.GNU-stack noalloc noexec nowrite progbits
