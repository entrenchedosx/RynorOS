/* RynorOS Stage 18d Slice C v2 link: same fixed bases as rynoros_rt.ld,
 * but the code and data windows may tile the bounded RYNX v2 maxima
 * (code <= 64 KiB from 0x400000, data <= 32 KiB from 0x600000).
 * Immutable bytes (.rodata) ride the executable segment: the RYNX
 * converter accepts RX-without-W at the code base with memsz==filesz,
 * and the kernel maps that window user-readable, so string literals
 * and const tables cost code budget (ample) instead of data budget.
 * Same merged-RW-data placement rule (converter requires contiguous
 * tiling from the data base); same /DISCARD/ set. Size caps are
 * enforced by the converter (rnyx.elf_to_rnyx version=2), never
 * trusted from here. v1 scripts and artifacts are untouched.
 */
ENTRY(_start)
SECTIONS {
  . = 0x400000;
  .text : {
    *(.text.start)
    KEEP(*(.text.probe_pad))
    *(.text*)
    *(.rodata*)
  }
  . = 0x600000;
  .data : {
    KEEP(*(.data.rtanchor))
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
