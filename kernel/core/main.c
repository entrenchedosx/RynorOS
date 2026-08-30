#include "serial.h"

/* RYNOR_VERSION is supplied from project.json, without timestamps or host paths. */
void kernel_main(void)
{
    serial_init();
    if (!serial_write("Rynorkernel booted.\r\n"))
        return;
    if (!serial_write("RynorOS " RYNOR_VERSION " | x86_64 | stage1\r\n"))
        return;
    (void)serial_flush();
    /* Returning reaches the entry stub's CLI/HLT loop, never BIOS or host code. */
}
