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

extern char __boot_map_start[], __boot_map_end[];
extern char __page_tables_start[], __page_tables_end[];
extern char __boot_stack_start[], __boot_stack_end[];
extern char __boot_sector_start[], __boot_sector_end[];
extern char __kernel_start[], __kernel_end[], __payload_end[];
extern char __kernel_stack_start[], __kernel_stack_end[], __identity_limit[];
#endif
