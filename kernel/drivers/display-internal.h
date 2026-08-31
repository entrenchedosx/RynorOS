#ifndef RYNOR_DISPLAY_INTERNAL_H
#define RYNOR_DISPLAY_INTERNAL_H
#include "display.h"
#include "boot_memory.h"

#define DISPLAY_MAX_DIM 4096u
#define DISPLAY_MAX_BYTES (16ULL * 1024 * 1024)
#define DISPLAY_TEXT_LIMIT 128u
#define DISPLAY_CELL 8u
/* A borrowed, validated byte extent. Never a physical allocator. Production
   storage is mapped device memory; tests use explicitly local guarded buffers. */
struct display_surface {
    volatile cpu_u32 *pixels;
    cpu_u32 width, height, pitch;
    cpu_u64 bytes;
};
const char *display_validate_info(const struct boot_fb_info *info);
/* Call only after geometry validation and with IF=0; shared with device tests. */
int display_device_matches(const struct boot_fb_info *info);
void display_surface_self_test(void);
int display_surface_valid(const struct display_surface *s);
int display_surface_pixel(const struct display_surface *, cpu_u32, cpu_u32, cpu_u32);
int display_surface_rect(const struct display_surface *, cpu_u32, cpu_u32, cpu_u32, cpu_u32, cpu_u32);
int display_surface_text(const struct display_surface *, cpu_u32, cpu_u32, const char *, cpu_u32);
#endif
