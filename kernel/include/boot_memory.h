#ifndef RYNOR_BOOT_MEMORY_H
#define RYNOR_BOOT_MEMORY_H
#include "cpu.h"

#define BOOT_MAP_MAGIC 0x50414d52u
#define BOOT_MAP_CAPACITY 64u
struct boot_memory_entry {
    cpu_u64 base, length;
    cpu_u32 type, attributes, returned_size, reserved;
};
struct boot_memory_map {
    cpu_u32 magic, version, count, capacity, stride, status, bytes, reserved;
    struct boot_memory_entry entries[BOOT_MAP_CAPACITY];
};
_Static_assert(sizeof(struct boot_memory_entry) == 32, "E820 handoff stride");
_Static_assert(__builtin_offsetof(struct boot_memory_map, entries) == 32, "E820 header");

/* Validated QEMU PCI/BGA display handoff, version 2: one linker-owned 4 KiB
   page retained read-only/NX; acquired in real mode, status published last. */
#define BOOT_FB_MAGIC 0x44484246u /* 'FBHD' */
#define BOOT_FB_VERSION 2u
#define BOOT_FB_OK 1u
#define BOOT_FB_FAILED 2u
struct boot_fb_info {
    cpu_u32 magic, version, status, mode;
    cpu_u32 width, height, pitch, bpp;
    cpu_u32 memory_model, lfb_phys, red_mask, green_mask;
    cpu_u32 blue_mask, reserved;
    cpu_u32 aperture_bytes, pci_id;
};
_Static_assert(sizeof(struct boot_fb_info) == 64, "FB handoff stride");
_Static_assert(__builtin_offsetof(struct boot_fb_info, lfb_phys) == 36, "FB physical offset");
_Static_assert(__builtin_offsetof(struct boot_fb_info, aperture_bytes) == 56, "FB aperture offset");

extern char __boot_map_start[], __boot_map_end[];
extern char __fb_info_start[], __fb_info_end[];
extern char __page_tables_start[], __page_tables_end[];
extern char __boot_stack_start[], __boot_stack_end[];
extern char __boot_sector_start[], __boot_sector_end[];
extern char __kernel_start[], __kernel_end[], __payload_end[];
extern char __kernel_stack_start[], __kernel_stack_end[], __identity_limit[];
#endif
