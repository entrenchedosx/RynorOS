#include "pmm.h"
#include "io.h"

static struct pmm_region regions[PMM_MAX_REGIONS];
static unsigned int region_count;
static struct pmm_statistics stats;
static cpu_u8 *allocated;
static cpu_u64 frame_count, search_cursor;
static int ready;

static enum pmm_result context(void)
{
    if (!cpu_interrupts_disabled()) return PMM_WRONG_CONTEXT;
    return ready ? PMM_OK : PMM_NOT_READY;
}

/* Initialization only: split usable intervals, preserving firmware classifications.
   Reserving memory never turns a firmware-reserved interval into usable RAM. */
static int reserve(cpu_u64 base, cpu_u64 end, unsigned int kind)
{
    for (unsigned int i = 0; i < region_count; ++i) {
        struct pmm_region r = regions[i];
        if (r.kind != PMM_USABLE || end <= r.base || base >= r.end) continue;
        cpu_u64 low = base > r.base ? base : r.base;
        cpu_u64 high = end < r.end ? end : r.end;
        unsigned int extra = (r.base < low) + (high < r.end);
        if (region_count + extra > PMM_MAX_REGIONS) return 0;
        for (unsigned int j = region_count; j > i + 1; --j)
            regions[j + extra - 1] = regions[j - 1];
        region_count += extra;
        if (r.base < low) regions[i++] = (struct pmm_region){r.base, low, PMM_USABLE};
        regions[i] = (struct pmm_region){low, high, kind};
        if (high < r.end) regions[++i] = (struct pmm_region){high, r.end, PMM_USABLE};
    }
    return 1;
}

static int originally_usable(cpu_u64 base, cpu_u64 end)
{
    for (unsigned int i = 0; i < region_count && base < end; ++i) {
        if (regions[i].end <= base) continue;
        if (regions[i].base > base || regions[i].kind != PMM_USABLE) return 0;
        base = regions[i].end;
    }
    return base >= end;
}

enum pmm_result pmm_initialize(const struct boot_memory_map *map, unsigned int bits)
{
    if (!cpu_interrupts_disabled()) return PMM_WRONG_CONTEXT;
    if (ready) return PMM_INVALID;
    if (!pmm_normalize(map, bits, regions, &region_count)) return PMM_BAD_MAP;
    /* All bootstrap-owned RAM must actually be reported usable. Addresses are
       linker symbols also consumed by startup; low 1 MiB retention is explicit
       legacy-PC policy, not an invented RAM-size or allocation-pool limit. */
    const cpu_u64 owned[][2] = {
        {(cpu_u64)__page_tables_start, (cpu_u64)__page_tables_end},
        {(cpu_u64)__boot_map_start, (cpu_u64)__boot_map_end},
        {(cpu_u64)__fb_info_start, (cpu_u64)__fb_info_end},
        {(cpu_u64)__boot_stack_start, (cpu_u64)__boot_stack_end},
        {(cpu_u64)__boot_sector_start, (cpu_u64)__boot_sector_end},
        {(cpu_u64)__kernel_start, (cpu_u64)__kernel_end},
        {(cpu_u64)__kernel_stack_start, (cpu_u64)__kernel_stack_end},
    };
    for (unsigned int i = 0; i < sizeof(owned) / sizeof(owned[0]); ++i)
        if (owned[i][0] >= owned[i][1] || owned[i][1] > 0x100000 ||
            !originally_usable(owned[i][0], owned[i][1])) return PMM_BAD_LAYOUT;
    stats = (struct pmm_statistics){0};
    for (unsigned int i = 0; i < region_count; ++i)
        if (regions[i].kind == PMM_USABLE)
            stats.firmware_usable_bytes += regions[i].end - regions[i].base;
    if (!reserve(0, 0x100000, PMM_BOOT_RESERVED)) return PMM_BAD_MAP;

    cpu_u64 candidate_frames = 0;
    for (unsigned int i = 0; i < region_count; ++i)
        if (regions[i].kind == PMM_USABLE)
            candidate_frames += (regions[i].end - regions[i].base) / PMM_PAGE_SIZE;
    if (!candidate_frames) return PMM_OUT_OF_MEMORY;
    cpu_u64 bytes = (candidate_frames + 7) / 8;
    bytes = (bytes + PMM_PAGE_SIZE - 1) & ~(PMM_PAGE_SIZE - 1);
    if (candidate_frames <= bytes / PMM_PAGE_SIZE) return PMM_OUT_OF_MEMORY;
    cpu_u64 address = 0;
    for (unsigned int i = 0; i < region_count; ++i) {
        if (regions[i].kind != PMM_USABLE) continue;
        cpu_u64 end = regions[i].end < (cpu_u64)__identity_limit ?
                      regions[i].end : (cpu_u64)__identity_limit;
        if (regions[i].base < end && bytes <= end - regions[i].base) {
            address = regions[i].base;
            break;
        }
    }
    if (!address) return PMM_NO_METADATA;
    if (!reserve(address, address + bytes, PMM_METADATA)) return PMM_BAD_MAP;
    stats.metadata_base = address;
    stats.metadata_bytes = bytes;
    allocated = (cpu_u8 *)address; /* Real E820 RAM already identity-mapped. */
    for (cpu_u64 i = 0; i < bytes; ++i) allocated[i] = 0;
    for (unsigned int i = 0; i < region_count; ++i) {
        cpu_u64 size = regions[i].end - regions[i].base;
        stats.described_bytes += size;
        if (regions[i].kind == PMM_USABLE) stats.usable_bytes += size;
        else stats.reserved_bytes += size;
    }
    stats.free_bytes = stats.usable_bytes;
    frame_count = stats.usable_bytes / PMM_PAGE_SIZE;
    search_cursor = 0;
    ready = 1;
    return PMM_OK;
}

static int bit(cpu_u64 index)
{
    return (allocated[index / 8] >> (index % 8)) & 1;
}

static int locate(cpu_u64 physical, cpu_u64 *index, enum pmm_state *state)
{
    cpu_u64 offset = 0;
    for (unsigned int i = 0; i < region_count; ++i) {
        const struct pmm_region *r = &regions[i];
        if (physical >= r->base && physical < r->end) {
            if (r->kind != PMM_USABLE) { *state = PMM_STATE_RESERVED; return 0; }
            *index = offset + (physical - r->base) / PMM_PAGE_SIZE;
            *state = bit(*index) ? PMM_STATE_ALLOCATED : PMM_STATE_FREE;
            return 1;
        }
        if (r->kind == PMM_USABLE) offset += (r->end - r->base) / PMM_PAGE_SIZE;
    }
    *state = PMM_STATE_UNAVAILABLE;
    return 0;
}

enum pmm_result pmm_allocate(cpu_u64 *physical)
{
    enum pmm_result result = context();
    if (result != PMM_OK) return result;
    if (!physical) return PMM_INVALID;
    if (!stats.free_bytes) return PMM_OUT_OF_MEMORY;
    cpu_u64 index = search_cursor;
    while (index < frame_count && bit(index)) ++index;
    if (index == frame_count) return PMM_INVALID; /* Accounting/cursor corruption. */
    cpu_u64 remaining = index;
    for (unsigned int i = 0; i < region_count; ++i) {
        const struct pmm_region *r = &regions[i];
        if (r->kind != PMM_USABLE) continue;
        cpu_u64 pages = (r->end - r->base) / PMM_PAGE_SIZE;
        if (remaining >= pages) { remaining -= pages; continue; }
        *physical = r->base + remaining * PMM_PAGE_SIZE;
        allocated[index / 8] |= (cpu_u8)(1u << (index % 8));
        search_cursor = index + 1;
        stats.free_bytes -= PMM_PAGE_SIZE;
        stats.allocated_bytes += PMM_PAGE_SIZE;
        return PMM_OK;
    }
    return PMM_INVALID;
}

enum pmm_result pmm_query(cpu_u64 physical, enum pmm_state *state)
{
    enum pmm_result result = context();
    if (result != PMM_OK) return result;
    if (!state || physical % PMM_PAGE_SIZE) return PMM_INVALID;
    cpu_u64 index;
    (void)locate(physical, &index, state);
    return PMM_OK;
}

enum pmm_result pmm_release(cpu_u64 physical)
{
    enum pmm_result result = context();
    if (result != PMM_OK) return result;
    if (physical % PMM_PAGE_SIZE) return PMM_INVALID;
    cpu_u64 index;
    enum pmm_state state;
    if (!locate(physical, &index, &state)) return PMM_UNAVAILABLE;
    if (state != PMM_STATE_ALLOCATED) return PMM_NOT_ALLOCATED;
    allocated[index / 8] &= (cpu_u8)~(1u << (index % 8));
    if (index < search_cursor) search_cursor = index;
    stats.free_bytes += PMM_PAGE_SIZE;
    stats.allocated_bytes -= PMM_PAGE_SIZE;
    return PMM_OK;
}

enum pmm_result pmm_statistics(struct pmm_statistics *out)
{
    enum pmm_result result = context();
    if (result != PMM_OK) return result;
    if (!out) return PMM_INVALID;
    *out = stats;
    return PMM_OK;
}

const struct pmm_region *pmm_regions(unsigned int *count)
{
    if (!count) return (void *)0;
    *count = 0;
    if (context() != PMM_OK) return (void *)0;
    *count = region_count;
    return regions;
}

int pmm_check(void)
{
    if (context() != PMM_OK) return 0;
    if (search_cursor > frame_count || frame_count > stats.metadata_bytes * 8) return 0;
    cpu_u64 used = 0, usable = 0, reserved = 0;
    for (unsigned int i = 0; i < region_count; ++i) {
        const struct pmm_region *r = &regions[i];
        if (r->base >= r->end || (r->base | r->end) % PMM_PAGE_SIZE ||
            (i && regions[i - 1].end > r->base)) return 0;
        if (r->kind == PMM_USABLE) usable += r->end - r->base;
        else reserved += r->end - r->base;
    }
    for (cpu_u64 i = 0; i < frame_count; ++i) {
        if (bit(i)) ++used;
        else if (i < search_cursor) return 0;
    }
    return usable == stats.usable_bytes && reserved == stats.reserved_bytes &&
           usable + reserved == stats.described_bytes &&
           frame_count == usable / PMM_PAGE_SIZE &&
           stats.allocated_bytes == used * PMM_PAGE_SIZE &&
           stats.free_bytes == (frame_count - used) * PMM_PAGE_SIZE;
}
