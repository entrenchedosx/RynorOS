#include "display.h"
#include "display-internal.h"
#include "vm.h"
#include "io.h"
#include "irq.h"
#include "serial.h"

static struct display_surface screen;
static const char *fault = "not_mapped";

const char *display_validate_info(const struct boot_fb_info *h)
{
    if (!h || h->magic != BOOT_FB_MAGIC) return "handoff_magic";
    if (h->version != BOOT_FB_VERSION) return "handoff_version";
    if (h->status != BOOT_FB_OK) return "handoff_status";
    if (h->bpp != 32) return "bpp";
    if (h->memory_model != 6) return "memory_model";
    if (h->red_mask != 0xff0000u || h->green_mask != 0xff00u || h->blue_mask != 0xffu)
        return "pixel_masks";
    if (!h->width || !h->height || h->width > DISPLAY_MAX_DIM || h->height > DISPLAY_MAX_DIM)
        return "resolution";
    if ((cpu_u64)h->width * 4u > h->pitch) return "short_pitch";
    if (h->pitch % 4u) return "pitch_alignment";
    cpu_u64 bytes = (cpu_u64)h->height * h->pitch;
    if (!bytes || bytes > DISPLAY_MAX_BYTES) return "framebuffer_too_large";
    if (h->lfb_phys % VM_PAGE_SIZE) return "lfb_alignment";
    /* Base is encoded as u32; validate the exclusive end in 64-bit arithmetic. */
    if (h->lfb_phys < 0x100000 ||
        bytes > 0x100000000ULL - h->lfb_phys) return "lfb_window";
    if (h->pci_id != 0x11111234 || h->mode != 0xb0c5 ||
        h->aperture_bytes < VM_PAGE_SIZE || h->aperture_bytes > 256u * 1024u * 1024u ||
        (h->aperture_bytes & (h->aperture_bytes - 1u)) || bytes > h->aperture_bytes ||
        h->lfb_phys % h->aperture_bytes ||
        h->aperture_bytes > 0x100000000ULL - h->lfb_phys) return "device_aperture";
    return (void *)0;
}

/* Fixed Stage 9 QEMU standard VGA at 00:02.0. Re-read device state: plausible
   RAM in a corrupted handoff must never authorize MMIO. IF=0 serializes CF8. */
static cpu_u32 pci_read(cpu_u32 reg)
{ io_out32(0xcf8, 0x80001000u | reg); return io_in32(0xcfc); }
static cpu_u16 bga_read(cpu_u16 reg)
{ io_out16(0x1ce, reg); return io_in16(0x1cf); }
int display_device_matches(const struct boot_fb_info *h)
{
    cpu_u32 bar = pci_read(0x10);
    return pci_read(0) == h->pci_id && (pci_read(8) >> 16) == 0x0300 &&
        (pci_read(4) & 3) == 3 && (bar & 15) == 8 &&
        (bar & 0xfffffff0u) == h->lfb_phys && bga_read(0) == h->mode &&
        bga_read(1) == h->width && bga_read(2) == h->height && bga_read(3) == h->bpp &&
        bga_read(4) == 0x41 && (cpu_u32)bga_read(6) * 4u == h->pitch &&
        !bga_read(8) && !bga_read(9) && (cpu_u32)bga_read(10) * 65536u == h->aperture_bytes;
}

int display_initialize(void)
{
    if (screen.pixels) { fault = "double_init"; return 0; }
    if (!cpu_interrupts_disabled() || irq_in_context()) { fault = "interrupts"; return 0; }
    const struct boot_fb_info *h = (const void *)__fb_info_start;
    fault = display_validate_info(h);
    if (fault) return 0;
    if (!display_device_matches(h)) { fault = "device_state"; return 0; }
    cpu_u64 bytes = (cpu_u64)h->height * h->pitch;
    cpu_u64 pages = (bytes + VM_PAGE_SIZE - 1u) / VM_PAGE_SIZE;
    if (vm_map_device(vm_kernel_space(), VM_MMIO_BASE, h->lfb_phys, pages, VM_WRITE) != VM_OK) {
        fault = "mapping"; return 0;
    }
    for (cpu_u64 i = 0; i < pages; ++i) {
        struct vm_mapping m;
        if (vm_query(vm_kernel_space(), VM_MMIO_BASE + i * VM_PAGE_SIZE, &m) != VM_OK ||
            m.physical != h->lfb_phys + i * VM_PAGE_SIZE || m.permissions != VM_WRITE || !m.uncached) {
            if (vm_unmap_device(vm_kernel_space(), VM_MMIO_BASE, pages) != VM_OK) {
                (void)serial_write("[FB] failure=mapping_cleanup\r\n");
                (void)serial_flush(); cpu_halt();
            }
            fault = "mapping_state"; return 0;
        }
    }
    screen = (struct display_surface){(volatile cpu_u32 *)VM_MMIO_BASE, h->width, h->height, h->pitch, bytes};
    fault = "none";
    return 1;
}

const char *display_error(void) { return fault; }
cpu_u32 display_width(void) { return screen.width; }
cpu_u32 display_height(void) { return screen.height; }
static cpu_u32 color(cpu_u8 r, cpu_u8 g, cpu_u8 b)
{ return (cpu_u32)b | ((cpu_u32)g << 8) | ((cpu_u32)r << 16); }
int display_put_pixel(cpu_u32 x, cpu_u32 y, cpu_u8 r, cpu_u8 g, cpu_u8 b)
{ return display_surface_pixel(&screen, x, y, color(r, g, b)); }
int display_fill_rect(cpu_u32 x, cpu_u32 y, cpu_u32 w, cpu_u32 h, cpu_u8 r, cpu_u8 g, cpu_u8 b)
{ return display_surface_rect(&screen, x, y, w, h, color(r, g, b)); }
int display_draw_text(cpu_u32 x, cpu_u32 y, const char *s, cpu_u8 r, cpu_u8 g, cpu_u8 b)
{ return display_surface_text(&screen, x, y, s, color(r, g, b)); }
int display_read_pixel(cpu_u32 x, cpu_u32 y, cpu_u8 *r, cpu_u8 *g, cpu_u8 *b)
{
    if (!display_surface_valid(&screen) || x >= screen.width || y >= screen.height || !r || !g || !b) return 0;
    cpu_u32 v = screen.pixels[(cpu_u64)y * (screen.pitch / 4u) + x];
    *b = (cpu_u8)v; *g = (cpu_u8)(v >> 8); *r = (cpu_u8)(v >> 16);
    return 1;
}
