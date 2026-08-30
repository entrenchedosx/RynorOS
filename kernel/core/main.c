#include "serial.h"
#include "cpu.h"
#include "irq.h"
#include "pmm.h"
#include "vm.h"
#include "heap.h"

/* RYNOR_VERSION is supplied from project.json, without timestamps or host paths. */
void kernel_main(void)
{
    serial_init();
    if (!serial_write("Rynorkernel booted.\r\n"))
        return;
    if (!serial_write("RynorOS " RYNOR_VERSION " | x86_64 | stage1\r\n"))
        return;
    (void)serial_flush();
    if (!cpu_initialize())
        cpu_halt();
    cpu_exception_self_test();
    pmm_bootstrap_and_test();
    vm_self_test();
    heap_self_test();
    timer_self_test();
    if (!pmm_check() || !vm_check(vm_kernel_space()) || !heap_check()) {
        serial_write("[MM] failure=post_irq_accounting\r\n");
        cpu_halt();
    }
    serial_write("[TEST] PMM post-IRQ accounting verified\r\n");
    serial_flush();
    /* Returning reaches the entry stub's CLI/HLT loop, never BIOS or host code. */
}
