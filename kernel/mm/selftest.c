#include "pmm.h"
#include "serial.h"

static void require(int condition, const char *reason)
{
    if (condition) return;
    serial_write("[MM] failure=");
    serial_write(reason);
    serial_write(" detail=");
    serial_write(pmm_error());
    serial_write("\r\n");
    serial_flush();
    cpu_halt();
}

static void text(const char *value) { require(serial_write(value), "serial"); }
static void number(cpu_u64 value)
{
    char buffer[21];
    unsigned int at = sizeof(buffer) - 1;
    buffer[at] = 0;
    do { buffer[--at] = (char)('0' + value % 10); value /= 10; } while (value);
    text(buffer + at);
}
static void field(const char *name, cpu_u64 value) { text(name); number(value); }
static void line(const char *name, cpu_u64 value) { field(name, value); text("\r\n"); }

static unsigned int physical_bits(void)
{
    cpu_u32 a, b, c, d;
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0x80000000), "c"(0));
    require(a >= 0x80000008, "cpuid_address_width_unavailable");
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0x80000008), "c"(0));
    return a & 0xff;
}

static void verify_existing_mapping(void)
{
    cpu_u64 root;
    __asm__ volatile ("mov %%cr3, %0" : "=r"(root));
    require(root == (cpu_u64)__page_tables_start &&
            (cpu_u64)__page_tables_end - root == 3 * PMM_PAGE_SIZE &&
            (cpu_u64)__identity_limit == 0x200000, "boot_mapping_contract");
    const volatile cpu_u64 *pml4 = (const volatile cpu_u64 *)root;
    const volatile cpu_u64 *pdpt = (const volatile cpu_u64 *)(root + PMM_PAGE_SIZE);
    const volatile cpu_u64 *pd = (const volatile cpu_u64 *)(root + 2 * PMM_PAGE_SIZE);
    require((pml4[0] & ~0x20ULL) == ((root + PMM_PAGE_SIZE) | 3) &&
            (pdpt[0] & ~0x20ULL) == ((root + 2 * PMM_PAGE_SIZE) | 3) &&
            (pd[0] & ~0x60ULL) == 0x83, "boot_mapping_readback");
}

/* Explicitly synthetic adversarial map fixtures test the same normalizer used
   for firmware. They never initialize the allocator or supply reported totals. */
static void normalization_tests(void)
{
    static struct boot_memory_map fixture;
    static struct pmm_region result[PMM_MAX_REGIONS];
    unsigned int count;
    fixture.magic = BOOT_MAP_MAGIC;
    fixture.version = 1;
    fixture.count = 3;
    fixture.capacity = BOOT_MAP_CAPACITY;
    fixture.stride = 32;
    fixture.status = 1;
    fixture.bytes = 4096;
    fixture.entries[0] = (struct boot_memory_entry){0x5000, 0x4000, 1, 1, 24, 0};
    fixture.entries[1] = (struct boot_memory_entry){0x1001, 0x6fff, 1, 1, 24, 0};
    fixture.entries[2] = (struct boot_memory_entry){0x4001, 1, 4, 1, 24, 0};
    require(pmm_normalize(&fixture, 36, result, &count) && count == 4 &&
            result[0].base == 0x1000 && result[0].end == 0x2000 && result[0].kind == 2 &&
            result[1].base == 0x2000 && result[1].end == 0x4000 && result[1].kind == 1 &&
            result[2].base == 0x4000 && result[2].end == 0x5000 && result[2].kind == 4 &&
            result[3].base == 0x5000 && result[3].end == 0x9000 && result[3].kind == 1,
            "normalization_overlap_alignment");
    fixture.entries[2].type = 99;
    require(pmm_normalize(&fixture, 36, result, &count) && result[2].kind == 2, "unknown_type");
    fixture.entries[2].attributes = 0;
    require(pmm_normalize(&fixture, 36, result, &count) && count == 2, "disabled_entry");
    fixture.entries[2].returned_size = 20;
    require(pmm_normalize(&fixture, 36, result, &count) && count == 4, "legacy_entry");
    fixture.entries[2].returned_size = 21;
    require(!pmm_normalize(&fixture, 36, result, &count), "reject_entry_size");
    fixture.entries[2] = (struct boot_memory_entry){0, 0, 1, 1, 24, 0};
    require(pmm_normalize(&fixture, 36, result, &count) && count == 2, "zero_length");
    fixture.entries[2].base = ~0ULL - 4095;
    fixture.entries[2].length = 8192;
    require(!pmm_normalize(&fixture, 36, result, &count), "reject_overflow");
    fixture.entries[2].base = 1ULL << 36;
    fixture.entries[2].length = 4096;
    require(!pmm_normalize(&fixture, 36, result, &count), "reject_physical_width");
    fixture.entries[2] = (struct boot_memory_entry){1ULL << 32, 8192, 3, 1, 24, 0};
    require(pmm_normalize(&fixture, 36, result, &count) && result[count - 1].kind == 3 &&
            result[count - 1].base == 1ULL << 32, "retain_acpi_high_address");
    fixture.count = BOOT_MAP_CAPACITY + 1;
    require(!pmm_normalize(&fixture, 36, result, &count), "reject_map_capacity");
    fixture.count = 3;
    fixture.status = 2;
    require(!pmm_normalize(&fixture, 36, result, &count), "reject_incomplete_map");
    fixture.status = 1;
    require(pmm_normalize(&fixture, 36, result, &count), "normalizer_recovery_after_rejection");
    text("[TEST] PMM map validation passed\r\n");
}

void pmm_bootstrap_and_test(void)
{
    cpu_u64 unavailable_output = ~0ULL;
    require(pmm_allocate(&unavailable_output) == PMM_NOT_READY && unavailable_output == ~0ULL &&
            pmm_release(0) == PMM_NOT_READY, "uninitialized_api");
    verify_existing_mapping();
    require((cpu_u64)__boot_map_start % PMM_PAGE_SIZE == 0 &&
            (cpu_u64)__boot_map_end - (cpu_u64)__boot_map_start == PMM_PAGE_SIZE &&
            (cpu_u64)__boot_map_end <= (cpu_u64)__identity_limit &&
            sizeof(struct boot_memory_map) <= PMM_PAGE_SIZE, "handoff_storage");
    const struct boot_memory_map *map = (const struct boot_memory_map *)__boot_map_start;
    unsigned int bits = physical_bits();
    enum pmm_result init = pmm_initialize(map, bits);
    if (init != PMM_OK) line("[MM] init_error=", init);
    require(init == PMM_OK, "allocator_initialization");
    require(pmm_initialize(map, bits) == PMM_INVALID, "reinitialization_rejected");
    text("[MM] firmware memory map acquired\r\n");
    line("[MM] entries=", map->count);
    line("[MM] physical_bits=", bits);
    for (unsigned int i = 0; i < map->count; ++i) {
        const struct boot_memory_entry *entry = &map->entries[i];
        field("[MM] raw base=", entry->base);
        field(" length=", entry->length);
        field(" type=", entry->type);
        field(" attributes=", entry->attributes);
        line(" size=", entry->returned_size);
    }
    struct pmm_statistics before, after;
    require(pmm_statistics(&before) == PMM_OK && pmm_check(), "initial_accounting");
    unsigned int count;
    const struct pmm_region *regions = pmm_regions(&count);
    line("[MM] regions=", count);
    for (unsigned int i = 0; i < count; ++i) {
        field("[MM] region base=", regions[i].base);
        field(" end=", regions[i].end);
        line(" kind=", regions[i].kind);
    }
    line("[MM] firmware_usable_bytes=", before.firmware_usable_bytes);
    line("[MM] described_bytes=", before.described_bytes);
    line("[MM] usable_bytes=", before.usable_bytes);
    line("[MM] reserved_bytes=", before.reserved_bytes);
    line("[MM] free_bytes=", before.free_bytes);
    line("[MM] allocated_bytes=", before.allocated_bytes);
    field("[MM] metadata base=", before.metadata_base);
    line(" bytes=", before.metadata_bytes);
    text("[MM] allocator initialized\r\n[TEST] PMM self-test started\r\n");
    normalization_tests();
    require(before.free_bytes >= 8 * PMM_PAGE_SIZE && !before.allocated_bytes, "discovered_usable_ram");
    enum pmm_state state;
    for (unsigned int i = 0; i < count; ++i) {
        if (regions[i].kind == PMM_USABLE) continue;
        require(pmm_query(regions[i].base, &state) == PMM_OK && state == PMM_STATE_RESERVED &&
                pmm_release(regions[i].base) == PMM_UNAVAILABLE &&
                pmm_release(regions[i].end - PMM_PAGE_SIZE) == PMM_UNAVAILABLE,
                "reserved_region_protection");
    }
    for (cpu_u64 address = 0; address < 0x100000; address += PMM_PAGE_SIZE)
        require(pmm_query(address, &state) == PMM_OK && state != PMM_STATE_FREE &&
                state != PMM_STATE_ALLOCATED, "bootstrap_reservation");
    require(pmm_allocate((void *)0) == PMM_INVALID && pmm_release(1) == PMM_INVALID &&
            pmm_release(1ULL << bits) == PMM_UNAVAILABLE, "invalid_api_requests");
    text("[TEST] PMM reservations verified\r\n");
    cpu_u64 frames[8];
    for (unsigned int i = 0; i < 8; ++i) {
        require(pmm_allocate(&frames[i]) == PMM_OK && frames[i] % PMM_PAGE_SIZE == 0 &&
                pmm_query(frames[i], &state) == PMM_OK && state == PMM_STATE_ALLOCATED &&
                (!i || frames[i] > frames[i - 1]), "allocate_unique_frames");
        field("[TEST] PMM allocated frame=", frames[i]);
        text("\r\n");
    }
    require(pmm_statistics(&after) == PMM_OK && after.allocated_bytes == 8 * PMM_PAGE_SIZE &&
            after.free_bytes + after.allocated_bytes == before.free_bytes && pmm_check(), "allocation_accounting");
    /* Exercise actual owned RAM, only when it lies in the existing boot mapping.
       PMM itself never dereferences returned frames or adds a mapping. */
    require(frames[0] + PMM_PAGE_SIZE <= (cpu_u64)__identity_limit, "mapped_frame_test_available");
    volatile cpu_u64 *memory = (volatile cpu_u64 *)frames[0];
    memory[0] = 0x726e726f504d4d31ULL;
    memory[511] = ~memory[0];
    require(memory[0] == 0x726e726f504d4d31ULL && memory[511] == ~memory[0], "physical_ram_read_write");
    text("[TEST] PMM physical RAM write verified\r\n");
    require(pmm_release(frames[0]) == PMM_OK && pmm_release(frames[0]) == PMM_NOT_ALLOCATED &&
            pmm_query(frames[0], &state) == PMM_OK && state == PMM_STATE_FREE, "release_double_free");
    cpu_u64 reused;
    require(pmm_allocate(&reused) == PMM_OK && reused == frames[0], "reuse");
    line("[TEST] PMM reused frame=", reused);
    for (unsigned int i = 0; i < 8; ++i) require(pmm_release(frames[i]) == PMM_OK, "release_multiple");

    /* Exhaust the real discovered pool, not an artificial quota or mock bitmap.
       This single-context test owns every allocation and releases it afterward. */
    cpu_u64 obtained = 0, previous = 0, current = ~0ULL;
    enum pmm_result status;
    while ((status = pmm_allocate(&current)) == PMM_OK) {
        require(current > previous && pmm_query(current, &state) == PMM_OK &&
                state == PMM_STATE_ALLOCATED, "exhaustion_uniqueness");
        previous = current;
        ++obtained;
        require(obtained <= before.free_bytes / PMM_PAGE_SIZE, "exhaustion_bound");
    }
    require(status == PMM_OUT_OF_MEMORY && obtained == before.free_bytes / PMM_PAGE_SIZE &&
            pmm_statistics(&after) == PMM_OK && !after.free_bytes &&
            after.allocated_bytes == before.usable_bytes && pmm_check(), "real_out_of_memory");
    current = ~0ULL;
    require(pmm_allocate(&current) == PMM_OUT_OF_MEMORY && current == ~0ULL, "oom_output_unchanged");
    field("[TEST] PMM exhausted frames=", obtained);
    line(" last=", previous);
    for (unsigned int i = 0; i < count; ++i)
        if (regions[i].kind == PMM_USABLE)
            for (cpu_u64 address = regions[i].base; address < regions[i].end; address += PMM_PAGE_SIZE)
                require(pmm_release(address) == PMM_OK, "release_exhausted_pool");
    require(pmm_statistics(&after) == PMM_OK && after.free_bytes == before.free_bytes &&
            !after.allocated_bytes && pmm_check(), "final_accounting");
    field("[MM] final free_bytes=", after.free_bytes);
    line(" allocated_bytes=", after.allocated_bytes);
    text("[TEST] PMM self-test passed\r\n");
}
