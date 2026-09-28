ENTRY(rynorkernel_entry)
/* Single source of truth for the fixed BIOS bootstrap ownership contract. */
__page_tables_start = 0x1000;
__page_tables_end = 0x4000;
__boot_map_start = 0x4000;
__boot_map_end = 0x5000;
__fb_info_start = 0x5000;
__fb_info_end = 0x6000;
__boot_stack_start = 0x7000;
__boot_stack_end = 0x7c00;
__boot_sector_start = 0x7c00;
__boot_sector_end = 0x7e00;
__kernel_stack_start = 0x7c000;
__kernel_stack_end = 0x80000;
__identity_limit = 0x200000;
/* BOOT-A1: the kernel proper lives at 8 MiB (identity-mapped), exactly
   where userspace ends (USER_STACK_TOP). The 4 KiB boot part stays at
   0x8000 and is loaded fixed; the image builder drops the VMA gap. */
__kernel_phys_base = 0x800000;
__boot_file_size = 0x1000;
__kernel_file_max = 0x800000;
__kernel_mem_max = 0x1000000;
SECTIONS
{
    . = 0x8000;
    __boot_start = .;
    .boot : { KEEP(*(.boot)) }
    . = 0x8000 + __boot_file_size;
    __boot_end = .;
    . = __kernel_phys_base;
    __kernel_start = .;
    .text ALIGN(16) : {
        __text_start = .; *(.text.entry)
        __runtime_service_start = .; *(.text.runtime_service)
        __runtime_service_end = .; *(.text .text.*)
    }
    . = ALIGN(4096);
    __text_end = .;
    .rodata : { *(.rodata .rodata.*) }
    . = ALIGN(4096);
    __rodata_end = .;
    __data_start = .;
    .data : { *(.data .data.*) }
    __payload_end = .;
    .bss ALIGN(4096) (NOLOAD) : {
        __bss_start = .;
        *(.bss .bss.*) *(COMMON)
        __bss_end = .;
    }
    __kernel_end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.llvm_addrsig) }
    ASSERT(boot_transition == 0x8000, "boot transition must start the flat payload")
    ASSERT(SIZEOF(.boot) <= __boot_file_size, "boot part exceeds fixed 4 KiB load")
    ASSERT(rynorkernel_entry == __kernel_phys_base, "kernel entry must equal load base")
    ASSERT(__payload_end - __kernel_start <= __kernel_file_max, "kernel file exceeds 8 MiB")
    ASSERT(__kernel_end - __kernel_start <= __kernel_mem_max, "kernel mem exceeds 16 MiB")
    ASSERT(__kernel_end <= 0x40000000, "kernel must stay under PDPT[0] (1 GiB)")
}
