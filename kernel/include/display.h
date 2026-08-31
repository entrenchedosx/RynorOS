#ifndef RYNOR_DISPLAY_H
#define RYNOR_DISPLAY_H
#include "cpu.h"

/* QEMU standard-VGA BGRX framebuffer, private uncached RW/NX slot 509.
   Single serialized kernel caller; no drawing from IRQ context. Rectangles and
   glyphs clip right/bottom; invalid origins fail. Text is transparent 5x7 in
   8x8 cells: A-Z, 0-9, space, . - : / ?, newline and carriage return. At most
   128 characters plus NUL in caller-owned storage, readable and immutable for
   the call. Invalid text fails
   before drawing. No Unicode, terminal emulation, automatic wrap or scroll. */
int display_initialize(void);
const char *display_error(void);
cpu_u32 display_width(void);
cpu_u32 display_height(void);
int display_put_pixel(cpu_u32 x, cpu_u32 y, cpu_u8 r, cpu_u8 g, cpu_u8 b);
int display_read_pixel(cpu_u32 x, cpu_u32 y, cpu_u8 *r, cpu_u8 *g, cpu_u8 *b);
int display_fill_rect(cpu_u32 x, cpu_u32 y, cpu_u32 w, cpu_u32 h, cpu_u8 r, cpu_u8 g, cpu_u8 b);
int display_draw_text(cpu_u32 x, cpu_u32 y, const char *s, cpu_u8 r, cpu_u8 g, cpu_u8 b);
void display_self_test(void);
#endif
