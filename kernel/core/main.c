#include "serial.h"
#include "apic.h"
#include "cpu.h"
#include "irq.h"
#include "pmm.h"
#include "vm.h"
#include "heap.h"
#include "ksched.h"
#include "kbd.h"
#include "display.h"
#include "pci.h"
#include "dma.h"
#include "krst.h"
#include "msi.h"
#include "shell.h"
#include "blk.h"
#include "fs.h"
#include "user.h"
#include "load.h"
#include "rttest.h"
#include "readtest.h"
#include "proctest.h"
#include "pipetest.h"
#include "shd.h"

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
    /* PCI registry is silent and allocation-free (port I/O only) and
       leaves config space bit-identical (BAR sizing fully restores),
       but it runs after the bus-independent drivers anyway: each
       layer's own checks then fire on its own failures instead of
       observing another layer's residue. A FULL registry still boots
       (partial discovery beats no OS; the gated test reports the
       exact count). */
    (void)pci_initialize();
    /* DMA registry is silent and allocation-free until drivers ask;
       a failed init is a broken memory subsystem, so fail closed. */
    if (dma_initialize() != DMA_OK)
        cpu_halt();
    runtime_self_test();
    if (!pmm_check() || !vm_check(vm_kernel_space()) || !heap_check() || !scheduler_check() ||
        !dma_check()) {
        serial_write("[GATE] failure=");
        serial_write(!pmm_check() ? "pmm" : !vm_check(vm_kernel_space()) ? "vm" :
                     !heap_check() ? "heap" : !scheduler_check() ? "scheduler" : "dma");
        serial_write("\r\n");
        serial_flush();
        cpu_halt();
    }
    serial_write("[TEST] PMM post-IRQ accounting verified\r\n");
    serial_flush();
    /* Modern interrupt path: ACPI discovery, LAPIC/IOAPIC bring-up, and the
       PIC-to-APIC switch with live timer proof. Falls back to PIC (with a
       backend=pic announcement) when firmware lacks usable APIC topology;
       everything after this point runs on whichever backend won. */
    apic_self_test();
    shell_self_test();
    serial_flush();
    /* Storage last: its evidence lines trail the shell section so the
       runtime/shell transcript grammars stay exact on normal boots. */
    blk_self_test();
    serial_flush();
    fs_self_test();
    serial_flush();
    /* Userspace last: its evidence lines trail the filesystem section so
       all earlier transcript grammars stay exact on normal boots. */
    user_self_test();
    serial_flush();
    /* Loader terminates the transcript (verified or skipped marker). */
    load_self_test();
    serial_flush();
    /* Runtime conformance trails the loader the same way (verified or
       skipped marker); it is now the transcript terminator. */
    rt_self_test();
    serial_flush();
    /* Stage 18d Slices A/B gated input test (test images only): when
       enabled its [INPUT] section becomes the transcript terminator.
       When disabled nothing prints here, so default transcripts stay
       byte-identical. */
    if (RYNOR_INPUT_TEST) read_self_test();
    serial_flush();
    /* Stage 18d Slice C gated process test (test images only): when
       enabled its [PROC] section becomes the transcript terminator.
       When disabled nothing prints here, so default transcripts stay
       byte-identical. */
    if (RYNOR_PROC_TEST) proc_self_test();
    serial_flush();
    /* Stage 18d Slice D gated pipe/file test (test images only): runs
       after the proc test; its [FREAD]/[PIPE] sections become the
       transcript terminator when enabled. Disabled prints nothing. */
    if (RYNOR_PIPE_TEST) pipe_self_test();
    serial_flush();
    /* PCI-A1 gated hardware test (test images only): its [PCI]
       section becomes the transcript terminator when enabled.
       Disabled prints nothing, so default transcripts stay
       byte-identical. */
    if (RYNOR_PCI_TEST) pci_self_test();
    serial_flush();
    /* DMA-A1 gated self-test (test images only): its [DMA]
       section becomes the transcript terminator when enabled.
       Disabled prints nothing, so default transcripts stay
       byte-identical. */
    if (RYNOR_DMA_TEST) dma_self_test();
    serial_flush();
    /* INT-A2 gated self-test (test images only): its [MSI]
       section becomes the transcript terminator when enabled.
       Disabled prints nothing, so default transcripts stay
       byte-identical. */
    if (RYNOR_MSI_TEST) msi_self_test();
    serial_flush();
    /* Stage 18d Slice E shell boot (shell images only): mounts the
       filesystem, enters /bin/sh on the bootstrap thread, and never
       returns (shell exit ends in [SHD] halt). Disabled prints
       nothing, so default transcripts stay byte-identical. */
    if (RYNOR_SHELL_BOOT) shd_boot();
    serial_flush();
    serial_flush();
    /* Returning reaches the entry stub's CLI/HLT loop, never BIOS or host code. */
}
