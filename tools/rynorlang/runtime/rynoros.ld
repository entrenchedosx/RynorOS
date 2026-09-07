/* RynorOS Stage 18b user-program link: fixed RYNX windows, no Linux layout.
 *
 * .text (code) is pinned at USER_CODE_BASE (0x400000) with _start first so
 * the RYNX entry offset is defined as zero. Read-only data lands in the
 * data window at USER_DATA_BASE (0x600000); .bss follows inside the same
 * 4 KiB page. Everything else is discarded. The host RYNX converter
 * re-verifies every address independently -- this script is placement,
 * not trust. See docs/design/executable-format.md.
 */
ENTRY(_start)
SECTIONS {
  . = 0x400000;
  .text : {
    *(.text.start)
    *(.text*)
  }
  . = 0x600000;
  .rodata : {
    *(.rodata*)
  }
  .data : {
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
