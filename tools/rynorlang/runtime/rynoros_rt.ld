/* RynorOS Stage 18c library link: same fixed RYNX windows as rynoros.ld.
 *
 * .text (code) is pinned at USER_CODE_BASE (0x400000) with _start first so
 * the RYNX entry offset is defined as zero. Unlike rynoros.ld, ALL data
 * sections share one RW LOAD: clang objects carry .rodata (string
 * literals) that the linker would otherwise place in a separate R-only
 * segment, and the RYNX converter requires data loads to tile
 * contiguously from the data base. The kernel maps the whole page U-RW
 * either way (string immutability stays a language property), so this
 * changes placement, never permissions. .bss follows inside the same
 * 4 KiB page; everything else is discarded. The 18b rynoros.ld script is
 * frozen and untouched: existing 18b artifacts rebuild byte-identically.
 */
ENTRY(_start)
SECTIONS {
  . = 0x400000;
  .text : {
    *(.text.start)
    *(.text*)
  }
  . = 0x600000;
  .data : {
    KEEP(*(.data.rtanchor))
    *(.rodata*)
    *(.data*)
  }
  .bss : {
    *(.bss*)
    *(COMMON)
  }
  /DISCARD/ : {
    *(.note*)
    *(.comment*)
    *(.eh_frame*)
    *(.debug*)
  }
}
