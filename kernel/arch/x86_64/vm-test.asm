bits 64
default rel
section .text
extern vm_fault_rsp
global vm_test_read, vm_test_read_fault, vm_test_read_after
global vm_test_write, vm_test_write_fault, vm_test_write_after
global vm_test_execute, vm_test_execute_after
vm_test_read:
    mov [vm_fault_rsp], rsp
vm_test_read_fault:
    mov rax, [rdi]
vm_test_read_after:
    ret
vm_test_write:
    mov [vm_fault_rsp], rsp
vm_test_write_fault:
    mov [rdi], rsi
vm_test_write_after:
    ret
vm_test_execute:
    mov [vm_fault_rsp], rsp
    jmp rdi
vm_test_execute_after:
    ret
section .note.GNU-stack noalloc noexec nowrite progbits
