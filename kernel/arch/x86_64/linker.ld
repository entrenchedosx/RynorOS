ENTRY(rynorkernel_entry)
/* Single source of truth for the fixed BIOS bootstrap ownership contract. */
__page_tables_start = 0x1000;
__page_tables_end = 0x4000;
__boot_map_start = 0x4000;
__boot_map_end = 0x5000;
__boot_stack_start = 0x7000;
__boot_stack_end = 0x7c00;
__boot_sector_start = 0x7c00;
__boot_sector_end = 0x7e00;
__kernel_stack_start = 0x7c000;
__kernel_stack_end = 0x80000;
__identity_limit = 0x200000;
SECTIONS
{
    . = 0x8000;
    __kernel_start = .;
    .boot : { KEEP(*(.boot)) }
    .text ALIGN(16) : { *(.text.entry) *(.text .text.*) }
    . = ALIGN(4096);
    __text_end = .;
    .rodata : { *(.rodata .rodata.*) }
    . = ALIGN(4096);
    __rodata_end = .;
    __data_start = .;
    .data : { *(.data .data.*) }
    __payload_end = .;
    .bss ALIGN(16) (NOLOAD) : {
        __bss_start = .;
        *(.bss .bss.*) *(COMMON)
        __bss_end = .;
    }
    __kernel_end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.llvm_addrsig) }
    ASSERT(boot_transition == 0x8000, "boot transition must start the flat payload")
    ASSERT(__payload_end <= 0x70000, "Boot payload exceeds bounded BIOS load window")
    ASSERT(__bss_end <= 0x70000, "Kernel BSS overlaps reserved stack area")
}
