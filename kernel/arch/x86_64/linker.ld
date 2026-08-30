ENTRY(rynorkernel_entry)
SECTIONS
{
    . = 0x8000;
    .boot : { KEEP(*(.boot)) }
    .text ALIGN(16) : { *(.text.entry) *(.text .text.*) }
    .rodata ALIGN(16) : { *(.rodata .rodata.*) }
    .data ALIGN(16) : { *(.data .data.*) }
    .bss ALIGN(16) (NOLOAD) : {
        __bss_start = .;
        *(.bss .bss.*) *(COMMON)
        __bss_end = .;
    }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.llvm_addrsig) }
    ASSERT(boot_transition == 0x8000, "boot transition must start the flat payload")
    ASSERT(__bss_end <= 0x10000, "Boot payload/BSS exceeds reserved 32 KiB")
}
