/* RynorOS Stage 18d Slice C v2 link: same fixed bases as rynoros_rt.ld,
 * but the code and data windows may tile the bounded RYNX v2 maxima
 * (code <= 32 KiB from 0x400000, data <= 16 KiB from 0x600000). Same
 * merged-RW-data placement rule (converter requires contiguous tiling
 * from the data base); same /DISCARD/ set. Size caps are enforced by
 * the converter (rnyx.elf_to_rnyx version=2), never trusted from here.
 * v1 scripts and artifacts are untouched.
 */
ENTRY(_start)
SECTIONS {
  . = 0x400000;
  .text : {
    *(.text.start)
    KEEP(*(.text.probe_pad))
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
