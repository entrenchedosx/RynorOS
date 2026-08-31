#include "display-internal.h"
#include "display-font.h"
#include "vm.h"

int display_surface_valid(const struct display_surface *s)
{
    if (!s || !s->pixels || (cpu_u64)s->pixels % 4 || !s->width || !s->height ||
        s->width > DISPLAY_MAX_DIM || s->height > DISPLAY_MAX_DIM ||
        s->pitch % 4 || (cpu_u64)s->width * 4 > s->pitch ||
        s->bytes != (cpu_u64)s->height * s->pitch || s->bytes > DISPLAY_MAX_BYTES)
        return 0;
    cpu_u64 start = (cpu_u64)s->pixels;
    if (s->bytes > ~0ULL - start) return 0;
    cpu_u64 last = start + s->bytes - 1;
    return vm_canonical(start) && vm_canonical(last) && (start >> 47) == (last >> 47);
}

int display_surface_pixel(const struct display_surface *s, cpu_u32 x, cpu_u32 y, cpu_u32 color)
{
    if (!display_surface_valid(s) || x >= s->width || y >= s->height) return 0;
    s->pixels[(cpu_u64)y * (s->pitch / 4) + x] = color;
    return 1;
}

int display_surface_rect(const struct display_surface *s, cpu_u32 x, cpu_u32 y,
                         cpu_u32 w, cpu_u32 h, cpu_u32 color)
{
    if (!display_surface_valid(s) || !w || !h || x >= s->width || y >= s->height) return 0;
    /* Widen before adding; subtractive clipping also avoids unsigned wrap. */
    cpu_u64 right = (cpu_u64)x + w, bottom = (cpu_u64)y + h;
    if (right > s->width) right = s->width;
    if (bottom > s->height) bottom = s->height;
    for (cpu_u64 row = y; row < bottom; ++row)
        for (cpu_u64 col = x; col < right; ++col)
            s->pixels[row * (s->pitch / 4) + col] = color;
    return 1;
}

static const struct display_glyph *glyph(cpu_u8 c)
{
    for (unsigned int i = 0; i < sizeof(display_font) / sizeof(display_font[0]); ++i)
        if (display_font[i].code == c) return &display_font[i];
    return (void *)0;
}

int display_surface_text(const struct display_surface *s, cpu_u32 x, cpu_u32 y,
                         const char *text, cpu_u32 color)
{
    if (!display_surface_valid(s) || !text || x >= s->width || y >= s->height) return 0;
    /* Caller supplies readable kernel memory through NUL or LIMIT+1 bytes.
       Validate the ENTIRE bounded request before touching device memory. */
    unsigned int length = 0;
    for (; length <= DISPLAY_TEXT_LIMIT && text[length]; ++length)
        if (text[length] != '\n' && text[length] != '\r' && !glyph((cpu_u8)text[length])) return 0;
    if (length > DISPLAY_TEXT_LIMIT) return 0;
    cpu_u64 cx = x, cy = y;
    for (unsigned int i = 0; i < length; ++i) {
        if (text[i] == '\n') { cx = x; cy += DISPLAY_CELL; continue; }
        if (text[i] == '\r') { cx = x; continue; }
        const struct display_glyph *g = glyph((cpu_u8)text[i]);
        for (unsigned int row = 0; row < 7; ++row)
            for (unsigned int col = 0; col < 5; ++col) {
                cpu_u64 px = cx + col, py = cy + row;
                if (px < s->width && py < s->height && (g->row[row] & (1u << (4-col))))
                    s->pixels[py * (s->pitch / 4) + px] = color;
            }
        cx += DISPLAY_CELL;
    }
    return 1;
}
