#include "heap.h"
#include "pmm.h"
#include "vm.h"
#include "io.h"
#include "serial.h"
#define HEAP_HDR 16u

/* --- Guest self-test (matches tools/host/heap_output.py markers). --- */

static void htext(const char *s) { if (!serial_write(s)) cpu_halt(); }
static void hfail(const char *reason)
{
    htext("[HEAP] failure="); htext(reason); htext("\r\n"); serial_flush(); cpu_halt();
}
static void hrequire(int condition, const char *reason)
{ if (!condition) hfail(reason); }
static void hnumber(cpu_u64 value)
{
    char buffer[21]; unsigned int n = 20; buffer[n] = 0;
    do { buffer[--n] = (char)('0' + (unsigned char)(value % 10)); value /= 10; } while (value);
    htext(&buffer[n]);
}
static void hfield(const char *label, cpu_u64 value)
{ htext(label); hnumber(value); }
static void hex_byte(cpu_u64 value)
{
    static const char digits[] = "0123456789abcdef";
    char pair[3]; pair[0] = digits[(value >> 4) & 0xf]; pair[1] = digits[value & 0xf]; pair[2] = 0;
    htext(pair);
}
static void hfield_hex(const char *label, cpu_u64 value)
{
    htext(label); htext("0x");
    for (int shift = 56; shift >= 0; shift -= 8) hex_byte((value >> shift) & 0xff);
}

static void initialization_tests(void)
{
    struct pmm_statistics before, after;
    struct vm_space *k = vm_kernel_space();
    cpu_u64 tables = k->table_pages, head = 0, frame;
    hrequire(pmm_statistics(&before) == PMM_OK, "init_before");
    void *untouched = (void *)1;
    hrequire(heap_alloc(1, 8, &untouched) == HEAP_NOT_READY && untouched == (void *)1 &&
             heap_free(0) == HEAP_NOT_READY, "not_ready");
    enum pmm_result r;
    while ((r = pmm_allocate(&frame)) == PMM_OK) {
        volatile cpu_u64 *p = vm_frame_access(frame);
        hrequire(p != 0, "init_oom_access");
        *p = head; head = frame;
    }
    hrequire(r == PMM_OUT_OF_MEMORY, "init_exhaustion");
    const unsigned int available[] = {0, 1, 4, 18};
    unsigned int released = 0;
    for (unsigned int i = 0; i < 4; ++i) {
        while (released < available[i]) {
            hrequire(head != 0, "init_oom_pool");
            frame = head; head = *(volatile cpu_u64 *)vm_frame_access(frame);
            hrequire(pmm_release(frame) == PMM_OK, "init_oom_release"); ++released;
        }
        hrequire(heap_initialize() == HEAP_OUT_OF_MEMORY &&
                 pmm_statistics(&after) == PMM_OK && after.free_bytes == released * VM_PAGE_SIZE &&
                 k->table_pages == tables && pmm_check() && vm_check(k), "init_oom_rollback");
        struct vm_mapping m;
        for (unsigned int page = 0; page < HEAP_ARENA_PAGES; ++page)
            hrequire(vm_query(k, HEAP_BASE + page * VM_PAGE_SIZE, &m) == VM_NOT_MAPPED,
                     "init_rollback_no_mappings");
    }
    while (head) {
        frame = head; head = *(volatile cpu_u64 *)vm_frame_access(frame);
        hrequire(pmm_release(frame) == PMM_OK, "init_restore_release");
    }
    hrequire(pmm_statistics(&after) == PMM_OK && after.free_bytes == before.free_bytes &&
             after.allocated_bytes == before.allocated_bytes, "init_restore_accounting");
    cpu_u64 conflict = HEAP_BASE + HEAP_ARENA_BYTES - VM_PAGE_SIZE;
    hrequire(pmm_allocate(&frame) == PMM_OK && vm_map(k, conflict, frame, VM_WRITE) == VM_OK,
             "init_conflict_setup");
    *(volatile cpu_u64 *)conflict = 0x726e68656170ULL;
    hrequire(heap_initialize() == HEAP_MAPPING_CONFLICT &&
             *(volatile cpu_u64 *)conflict == 0x726e68656170ULL &&
             vm_unmap(k, conflict) == VM_OK && pmm_release(frame) == PMM_OK &&
             k->table_pages == tables, "init_conflict_preserved");
}

static void boundary_tests(void)
{
    struct heap_statistics stats;
    /* Whole arena and unsplittable 16/32-byte tails, plain AND aligned paths. */
    for (cpu_u64 tail = 0; tail <= 32; tail += 16) {
        void *p = 0;
        hrequire(heap_alloc(HEAP_ARENA_BYTES - 32 - tail, 8, &p) == HEAP_OK && heap_check() &&
                 heap_statistics(&stats) == HEAP_OK && stats.used_bytes == HEAP_ARENA_BYTES,
                 "tail_coverage");
        hrequire((cpu_u64)p % 8 == 0, "tail_alignment");
        volatile cpu_u8 *bytes = p;
        cpu_u64 length = HEAP_ARENA_BYTES - 32 - tail;
        for (cpu_u64 i = 0; i < length; ++i) bytes[i] = (cpu_u8)i;
        for (cpu_u64 i = 0; i < length; ++i) hrequire(bytes[i] == (cpu_u8)i, "arena_pattern");
        hrequire(heap_free(p) == HEAP_OK && heap_check(), "tail_free");
        hrequire(heap_alloc(HEAP_ARENA_BYTES - 4080 - 32 - tail, 4096, &p) == HEAP_OK &&
                 (cpu_u64)p % 4096 == 0 && heap_check() && heap_free(p) == HEAP_OK && heap_check(),
                 "aligned_tail");
    }
    void *p = 0, *q = 0;
    /* Force the remaining free payload to be already page aligned. There is
       exactly enough room: inventing an unnecessary leading gap must fail. */
    hrequire(heap_alloc(4048, 8, &p) == HEAP_OK &&
             heap_alloc(HEAP_ARENA_BYTES - 4080 - 32, 4096, &q) == HEAP_OK &&
             (cpu_u64)q == HEAP_BASE + 4096 && heap_check() &&
             heap_free(p) == HEAP_OK && heap_free(q) == HEAP_OK && heap_check(), "zero_alignment_gap");
    hrequire(heap_alloc(256, 8, &p) == HEAP_OK, "interior_setup");
    /* Plausible metadata inside caller data must NOT authorize a free. */
    volatile cpu_u64 *inside = (void *)((cpu_u64)p + 16);
    inside[0] = 48; inside[1] = 0x524e484541504f42ULL;
    inside[4] = 48; inside[5] = 0x524e484541504f42ULL;
    hrequire(heap_free((void *)((cpu_u64)p + 32)) == HEAP_INVALID && heap_check(), "interior_pointer");
    volatile cpu_u64 *header = (void *)((cpu_u64)p - 16);
    cpu_u64 saved = header[0];
    const cpu_u64 bad_sizes[] = {0, 16, 49, HEAP_ARENA_BYTES + 4096, ~15ULL};
    for (unsigned int i = 0; i < 5; ++i) {
        header[0] = bad_sizes[i]; q = (void *)1;
        hrequire(!heap_check() && heap_free(p) == HEAP_CORRUPT &&
                 heap_alloc(16, 8, &q) == HEAP_CORRUPT && q == (void *)1 &&
                 heap_statistics(&stats) == HEAP_CORRUPT, "corrupt_size_rejected");
        header[0] = saved;
        hrequire(heap_check(), "corrupt_size_restore");
    }
    volatile cpu_u64 *footer = (void *)((cpu_u64)header + saved - 16);
    footer[1] ^= 2;
    hrequire(heap_free(p) == HEAP_CORRUPT && !heap_check(), "corrupt_footer");
    footer[1] ^= 2;
    hrequire(heap_free(p) == HEAP_OK && heap_alloc(256, 8, &q) == HEAP_OK && q == p &&
             heap_free(q) == HEAP_OK && heap_check(), "exact_reuse");
    /* Mixed sizes, all alignments, interleaved frees and live payload preservation. */
    void *held[32]; cpu_u64 sizes[32]; /* Every element assigned before use. */
    for (unsigned int round = 0; round < 8; ++round) {
        for (unsigned int i = 0; i < 32; ++i) {
            sizes[i] = 1 + (i * 37 + round * 13) % 511;
            cpu_u64 align = 8ULL << (i % 10);
            hrequire(heap_alloc(sizes[i], align, &held[i]) == HEAP_OK &&
                     (cpu_u64)held[i] % align == 0, "mixed_allocation");
            for (cpu_u64 j = 0; j < sizes[i]; ++j) ((volatile cpu_u8 *)held[i])[j] = (cpu_u8)(i + j);
        }
        for (unsigned int order = 0; order < 32; ++order) {
            unsigned int i = (order * 13) % 32;
            for (cpu_u64 j = 0; j < sizes[i]; ++j)
                hrequire(((volatile cpu_u8 *)held[i])[j] == (cpu_u8)(i + j), "mixed_payload");
            hrequire(heap_free(held[i]) == HEAP_OK && heap_check(), "mixed_free");
        }
    }
}

static void heap_self_test_inner(void)
{
    hrequire(cpu_interrupts_disabled(), "context_if0");
    struct pmm_statistics pre;
    hrequire(pmm_statistics(&pre) == PMM_OK, "pmm_statistics_before");
    initialization_tests();
    hrequire(heap_initialize() == HEAP_OK, "initialize");
    hrequire(heap_initialize() == HEAP_BUSY, "double_initialize");
    hfield("[HEAP] initialize arena=", HEAP_ARENA_BYTES);
    hfield(" mapped=", HEAP_ARENA_BYTES); htext("\r\n");

    /* The arena must be real RW/NX pages in the Stage 5 kernel space. */
    struct vm_mapping m;
    hrequire(vm_query(vm_kernel_space(), HEAP_BASE, &m) == VM_OK &&
             m.permissions == VM_WRITE && m.physical != 0, "arena_mapped_rw");

    struct heap_statistics stats;
    hrequire(heap_statistics(&stats) == HEAP_OK && stats.arena_bytes == HEAP_ARENA_BYTES &&
             stats.mapped_bytes == HEAP_ARENA_BYTES && stats.used_bytes == 0 &&
             stats.free_bytes == HEAP_ARENA_BYTES && stats.allocated_blocks == 0 &&
             stats.free_blocks == 1 && heap_check(), "initial_layout");
    hfield("[HEAP] free_blocks=", stats.free_blocks); htext("\r\n");
    htext("[TEST] HEAP initialization rollback verified\r\n");
    boundary_tests();
    htext("[TEST] HEAP adversarial boundaries and corruption verified\r\n");

    /* Boundary and alignment: a small, a mid, and a page-aligned allocation. */
    void *a = 0, *b = 0, *c = 0;
    hrequire(heap_alloc(32, 8, &a) == HEAP_OK && a && (cpu_u64)a % 8 == 0, "alloc_small");
    hrequire(heap_alloc(64, 16, &b) == HEAP_OK && b && (cpu_u64)b % 16 == 0 && b != a, "alloc_mid");
    hrequire(heap_alloc(8, 4096, &c) == HEAP_OK && c && (cpu_u64)c % 4096 == 0 &&
             c != a && c != b, "alloc_align_page");
    cpu_u64 arena_lo = HEAP_BASE + HEAP_HDR, arena_hi = HEAP_BASE + HEAP_ARENA_BYTES;
    hrequire((cpu_u64)a >= arena_lo && (cpu_u64)a < arena_hi &&
             (cpu_u64)b >= arena_lo && (cpu_u64)b < arena_hi &&
             (cpu_u64)c >= arena_lo && (cpu_u64)c < arena_hi, "alloc_in_arena");
    /* Fill each payload and verify the bytes round-trip through those exact VAs. */
    volatile cpu_u8 *pa = a, *pb = b, *pc = c;
    for (unsigned int i = 0; i < 32; ++i) pa[i] = (cpu_u8)(0xa0 + i);
    for (unsigned int i = 0; i < 64; ++i) pb[i] = (cpu_u8)(0x10 + i);
    for (unsigned int i = 0; i < 8; ++i) pc[i] = (cpu_u8)(0xe0 + i);
    for (unsigned int i = 0; i < 32; ++i) hrequire(pa[i] == (cpu_u8)(0xa0 + i), "pattern_small");
    for (unsigned int i = 0; i < 64; ++i) hrequire(pb[i] == (cpu_u8)(0x10 + i), "pattern_mid");
    for (unsigned int i = 0; i < 8; ++i) hrequire(pc[i] == (cpu_u8)(0xe0 + i), "pattern_align");
    hfield_hex("[HEAP] small=", (cpu_u64)a); hfield_hex(" mid=", (cpu_u64)b);
    hfield_hex(" align4096=", (cpu_u64)c); htext("\r\n");
    htext("[TEST] HEAP boundary and alignment verified\r\n");

    /* Free everything and confirm adjacent frees fully coalesce into one block. */
    hrequire(heap_free(a) == HEAP_OK, "free_small");
    hrequire(heap_free(b) == HEAP_OK, "free_mid");
    hrequire(heap_free(c) == HEAP_OK, "free_align");
    hrequire(heap_statistics(&stats) == HEAP_OK && stats.used_bytes == 0 &&
             stats.free_bytes == HEAP_ARENA_BYTES && stats.allocated_blocks == 0 &&
             stats.free_blocks == 1 && heap_check(), "coalesce_all");
    hfield("[HEAP] coalesced free_blocks=", stats.free_blocks);
    hfield(" used=", stats.used_bytes); htext("\r\n");
    htext("[TEST] HEAP coalescing verified\r\n");

    /* Invalid and corrupted invocations must be rejected, not panic. */
    void *x = 0;
    hrequire(heap_alloc(32, 8, &x) == HEAP_OK, "alloc_for_corruption");
    hrequire(heap_free(x) == HEAP_OK, "free_once");
    hrequire(heap_free(x) == HEAP_CORRUPT, "double_free_corrupt");
    hrequire(heap_free(0) == HEAP_INVALID, "free_null");
    hrequire(heap_free((void *)(~0ULL)) == HEAP_INVALID, "free_high");
    hrequire(heap_alloc(0, 8, &a) == HEAP_OVERFLOW, "alloc_zero");
    hrequire(heap_alloc(~0ULL - 1ULL, 8, &a) == HEAP_OVERFLOW, "alloc_overflow");
    hrequire(heap_alloc(32, 0, &a) == HEAP_ALIGNMENT, "alloc_align_zero");
    hrequire(heap_alloc(32, 6, &a) == HEAP_ALIGNMENT, "alloc_align_nonpow2");
    hrequire(heap_alloc(32, 8192, &a) == HEAP_ALIGNMENT, "alloc_align_too_big");
    hrequire(heap_alloc(32, 8, 0) == HEAP_INVALID, "alloc_null_out");
    hrequire(heap_statistics(0) == HEAP_INVALID, "stats_null");
    htext("[TEST] HEAP invalid calls rejected\r\n");

    /* Stress: fill the bounded arena with uniform blocks until OOM, then free. */
    enum { STRESS = 256 };
    void *held[STRESS];
    unsigned int count = 0;
    for (; count < STRESS; ++count) {
        if (heap_alloc(256, 8, &held[count]) != HEAP_OK) break;
    }
    hrequire(count > 0 && count < STRESS, "stress_overflow");
    void *extra = 0;
    hrequire(heap_alloc(256, 8, &extra) == HEAP_OUT_OF_MEMORY, "stress_oom");
    hfield("[HEAP] stress blocks=", count);
    hfield(" oom=", (cpu_u64)(extra == 0)); htext("\r\n");
    for (unsigned int i = 0; i < count; ++i) hrequire(heap_free(held[i]) == HEAP_OK, "stress_free");
    hrequire(heap_statistics(&stats) == HEAP_OK && stats.used_bytes == 0 &&
             stats.allocated_blocks == 0 && stats.free_blocks == 1 && heap_check(),
             "stress_restore");
    htext("[TEST] HEAP stress and OOM verified\r\n");

    /* The whole subsystem must still be structurally sound; the allocated
       arena frames remain part of ordinary PMM accounting. */
    struct pmm_statistics post;
    hrequire(heap_check() && pmm_check() && vm_check(vm_kernel_space()) &&
             pmm_statistics(&post) == PMM_OK &&
             post.allocated_bytes == pre.allocated_bytes + (HEAP_ARENA_PAGES + 3) * VM_PAGE_SIZE &&
             post.free_bytes + post.allocated_bytes == pre.free_bytes + pre.allocated_bytes &&
             vm_kernel_space()->table_pages == 10, "post_heap_integrity");
    cpu_u64 frames[HEAP_ARENA_PAGES];
    for (unsigned int i = 0; i < HEAP_ARENA_PAGES; ++i) {
        enum pmm_state state;
        hrequire(vm_query(vm_kernel_space(), HEAP_BASE + i * VM_PAGE_SIZE, &m) == VM_OK &&
                 m.permissions == VM_WRITE && pmm_query(m.physical, &state) == PMM_OK &&
                 state == PMM_STATE_ALLOCATED, "arena_page_ownership");
        frames[i] = m.physical;
        for (unsigned int j = 0; j < i; ++j) hrequire(frames[j] != frames[i], "arena_unique_frames");
    }
    hfield("[HEAP] PMM allocated_bytes=", post.allocated_bytes);
    hfield(" free_bytes=", post.free_bytes); hfield(" table_pages=", vm_kernel_space()->table_pages); htext("\r\n");
    hfield("[HEAP] final used=", stats.used_bytes);
    hfield(" mapped=", HEAP_ARENA_BYTES); htext("\r\n[TEST] HEAP self-test passed\r\n");
}

void heap_self_test(void)
{
    if (!cpu_interrupts_disabled()) { hfail("context_if0"); }
    heap_self_test_inner();
    serial_flush();
}
