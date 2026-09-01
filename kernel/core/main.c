#include "serial.h"
#include "cpu.h"
#include "irq.h"
#include "pmm.h"
#include "vm.h"
#include "heap.h"
#include "ksched.h"
#include "kbd.h"
#include "display.h"
#include "krst.h"
#include "shell.h"

/* RYNOR_VERSION is supplied from project.json, without timestamps or host paths. */
void kernel_main(void)
{
    serial_init();
    if (!serial_write("Rynorkernel booted.\r\n") || !serial_write("RynorOS " RYNOR_VERSION " | x86_64 | stage1\r\n"))
        cpu_halt(); /* No serial channel means no diagnostics possible; fail closed. */
    (void)serial_flush();
    if (!cpu_initialize())
        cpu_halt();
    cpu_exception_self_test();
    pmm_bootstrap_and_test();
    vm_self_test();
    heap_self_test();
    timer_self_test();
    scheduler_self_test();
    keyboard_self_test();
    display_self_test();
    runtime_self_test();
    if (!pmm_check() || !vm_check(vm_kernel_space()) || !heap_check() || !scheduler_check()) {
        serial_write("[GATE] failure=");
        serial_write(!pmm_check() ? "pmm" : !vm_check(vm_kernel_space()) ? "vm" :
                     !heap_check() ? "heap" : "scheduler");
        serial_write("\r\n");
        serial_flush();
        cpu_halt();
    }
    serial_write("[TEST] PMM post-IRQ accounting verified\r\n");
    serial_flush();
    /* Incoming Stage 11 work is preserved, opt-in and not certified by the
       Stage 10 audit. Normal Stage 10 images end after the integrity gate. */
#if defined(RYNOR_SHELL_INTERACTIVE)
    shell_self_test();
#endif
    serial_flush();
    /* Returning reaches the entry stub's CLI/HLT loop, never BIOS or host code. */
}
