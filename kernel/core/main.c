#include "serial.h"
#include "cpu.h"
#include "irq.h"

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
    timer_self_test();
    /* Returning reaches the entry stub's CLI/HLT loop, never BIOS or host code. */
}
