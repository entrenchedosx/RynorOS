#include "pmm.h"

static struct pmm_region candidates[BOOT_MAP_CAPACITY * 3];
static const char *error = "none";
const char *pmm_error(void) { return error; }

static int fail(const char *message)
{
    error = message;
    return 0;
}

static unsigned int priority(unsigned int kind)
{
    switch (kind) {
    case PMM_USABLE: return 1;
    case PMM_ACPI_RECLAIM: return 2;
    case PMM_PERSISTENT: return 3;
    case PMM_ACPI_NVS: return 4;
    case PMM_BAD: return 6;
    default: return 5; /* Reserved/unknown wins over any usable/reclaimable claim. */
    }
}

int pmm_normalize(const struct boot_memory_map *map, unsigned int bits,
                  struct pmm_region *regions, unsigned int *count)
{
    if (!map || !regions || !count) return fail("null_map_argument");
    *count = 0;
    error = "none";
    if (bits < 32 || bits > 52) return fail("physical_address_width");
    if (map->magic != BOOT_MAP_MAGIC || map->version != 1 || map->status != 1 ||
        map->capacity != BOOT_MAP_CAPACITY || !map->count || map->count > BOOT_MAP_CAPACITY ||
        map->stride != sizeof(struct boot_memory_entry) || map->bytes != 4096 || map->reserved)
        return fail("invalid_handoff_header");
    cpu_u64 limit = 1ULL << bits;
    unsigned int used = 0;
    for (unsigned int i = 0; i < map->count; ++i) {
        const struct boot_memory_entry *entry = &map->entries[i];
        if ((entry->returned_size != 20 && entry->returned_size != 24) || entry->reserved)
            return fail("invalid_entry_size");
        if (entry->returned_size == 24 && !(entry->attributes & 1)) continue;
        if (!entry->length) continue;
        if (entry->base >= limit || entry->length > limit - entry->base)
            return fail("invalid_physical_range");
        cpu_u64 end = entry->base + entry->length;
        cpu_u64 low = entry->base & ~(PMM_PAGE_SIZE - 1);
        cpu_u64 high = (end + PMM_PAGE_SIZE - 1) & ~(PMM_PAGE_SIZE - 1);
        unsigned int kind = entry->type;
        if ((kind != 1 && kind != 3 && kind != 4 && kind != 5 && kind != 7) ||
            (entry->returned_size == 24 && (entry->attributes & ~1u))) kind = PMM_RESERVED;
        if (kind != PMM_USABLE) {
            candidates[used++] = (struct pmm_region){low, high, kind};
            continue;
        }
        cpu_u64 start = (entry->base + PMM_PAGE_SIZE - 1) & ~(PMM_PAGE_SIZE - 1);
        cpu_u64 finish = end & ~(PMM_PAGE_SIZE - 1);
        if (start >= finish) {
            candidates[used++] = (struct pmm_region){low, high, PMM_RESERVED};
        } else {
            if (low < start) candidates[used++] = (struct pmm_region){low, start, PMM_RESERVED};
            candidates[used++] = (struct pmm_region){start, finish, PMM_USABLE};
            if (finish < high) candidates[used++] = (struct pmm_region){finish, high, PMM_RESERVED};
        }
    }
    if (!used) return fail("empty_memory_map");
    /* Boundary sweep: disjoint sorted intervals, restrictive overlap precedence,
       adjacent equal kinds merged. Holes are omitted and never allocatable. */
    cpu_u64 cursor = limit;
    for (unsigned int i = 0; i < used; ++i)
        if (candidates[i].base < cursor) cursor = candidates[i].base;
    while (cursor < limit) {
        cpu_u64 next = limit;
        unsigned int kind = PMM_HOLE, rank = 0;
        for (unsigned int i = 0; i < used; ++i) {
            const struct pmm_region *r = &candidates[i];
            if (r->base > cursor && r->base < next) next = r->base;
            if (r->end > cursor && r->end < next) next = r->end;
            if (r->base <= cursor && cursor < r->end && priority(r->kind) > rank) {
                rank = priority(r->kind);
                kind = r->kind;
            }
        }
        if (kind != PMM_HOLE) {
            if (*count && regions[*count - 1].end == cursor && regions[*count - 1].kind == kind)
                regions[*count - 1].end = next;
            else {
                if (*count >= PMM_MAX_REGIONS) return fail("normalized_map_capacity");
                regions[(*count)++] = (struct pmm_region){cursor, next, kind};
            }
        }
        cursor = next;
    }
    return 1;
}
