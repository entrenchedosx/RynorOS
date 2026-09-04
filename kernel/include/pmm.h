#ifndef RYNOR_PMM_H
#define RYNOR_PMM_H
#include "boot_memory.h"

#define PMM_PAGE_SIZE 4096ULL
#define PMM_MAX_REGIONS (BOOT_MAP_CAPACITY * 6 + 8)
enum pmm_kind {
    PMM_HOLE = 0, PMM_USABLE = 1, PMM_RESERVED = 2, PMM_ACPI_RECLAIM = 3,
    PMM_ACPI_NVS = 4, PMM_BAD = 5, PMM_PERSISTENT = 7,
    PMM_BOOT_RESERVED = 8, PMM_METADATA = 9
};
enum pmm_result { PMM_OK, PMM_NOT_READY, PMM_INVALID, PMM_UNAVAILABLE,
                  PMM_NOT_ALLOCATED, PMM_OUT_OF_MEMORY, PMM_WRONG_CONTEXT,
                  PMM_BAD_MAP, PMM_BAD_LAYOUT, PMM_NO_METADATA };
enum pmm_state { PMM_STATE_UNAVAILABLE, PMM_STATE_RESERVED, PMM_STATE_FREE, PMM_STATE_ALLOCATED };
struct pmm_region { cpu_u64 base, end; cpu_u32 kind; };
struct pmm_statistics {
    cpu_u64 described_bytes, usable_bytes, reserved_bytes, free_bytes, allocated_bytes;
    cpu_u64 firmware_usable_bytes, metadata_base, metadata_bytes;
};

/* Physical addresses only. No mapping/zeroing; single CPU, IF=0 and !irq_in_context() required.
   usable_bytes == free_bytes + allocated_bytes; described == usable + reserved. */
enum pmm_result pmm_initialize(const struct boot_memory_map *map, unsigned int physical_bits);
enum pmm_result pmm_allocate(cpu_u64 *physical);
enum pmm_result pmm_release(cpu_u64 physical);
enum pmm_result pmm_query(cpu_u64 physical, enum pmm_state *state);
enum pmm_result pmm_statistics(struct pmm_statistics *out);
const struct pmm_region *pmm_regions(unsigned int *count);
int pmm_check(void);
const char *pmm_error(void);
void pmm_bootstrap_and_test(void);

/* Bounded normalization with shared scratch; callers must serialize access. */
int pmm_normalize(const struct boot_memory_map *map, unsigned int physical_bits,
                  struct pmm_region *regions, unsigned int *count);
#endif
