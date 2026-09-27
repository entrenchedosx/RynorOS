/* DMA-A1 gated self-test (RYNOR_DMA_TEST images only).
 *
 * Two phases: (1) synthetic pmm_scan_run fixtures through the shared
 * pure scan (first-fit, alignment, fragmentation, multi-region,
 * limits, edges; the host replays an independent model over the
 * printed rows); (2) live dma_alloc/dma_free through the public API
 * (rounding, PTE structure, patterns, reuse, rollback, dma32, OOM,
 * guards). Terminates with "[DMA] dma verified". Production builds
 * never call this.
 */
#include "dma.h"
#include "pmm.h"
#include "vm.h"
#include "cpu.h"
#include "io.h"
#include "serial.h"

static void say(const char *s) { (void)serial_write(s); }

static void say_hex(cpu_u64 v)
{
    char buf[17];
    int n = 0;
    if (!v) { say("0"); return; }
    while (v) {
        cpu_u64 d = v & 0xFu;
        buf[n++] = (char)(d < 10u ? '0' + d : 'a' + d - 10u);
        v >>= 4;
    }
    while (n--) {
        char c[2];
        c[0] = buf[n];
        c[1] = 0;
        say(c);
    }
}

static void say_dec(cpu_u64 v)
{
    char buf[21];
    int n = 0;
    if (!v) { say("0"); return; }
    while (v) {
        buf[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    }
    while (n--) {
        char c[2];
        c[0] = buf[n];
        c[1] = 0;
        say(c);
    }
}

static void fail(const char *tag) __attribute__((noreturn));
static void fail(const char *tag)
{
    say("[DMA] failure=");
    say(tag);
    say("\r\n");
    (void)serial_flush();
    cpu_halt();
}

static void require(int ok, const char *tag)
{
    if (!ok) fail(tag);
}

/* ---------- synthetic scan fixtures ---------- */

struct scan_case {
    cpu_u64 b0, b1;
    cpu_u16 p0, p1;
    cpu_u32 bits; /* allocated-bit mask over p0+p1 (<= 24) frames */
    cpu_u16 n;
    cpu_u64 align, limit;
    cpu_u8 want;
    cpu_u16 want_start;
};

static const struct scan_case scan_cases[] = {
    /* all free / all used / single run */
    {0x100000, 0, 8, 0, 0x000000, 1, 0x1000, 0, 1, 0},
    {0x100000, 0, 8, 0, 0x0000FF, 1, 0x1000, 0, 0, 0},
    {0x100000, 0, 8, 0, 0x000007, 2, 0x1000, 0, 1, 3},
    /* M1: last-frame check skipped would report 0, truth is 4 */
    {0x100000, 0, 8, 0, 0x000008, 4, 0x1000, 0, 1, 4},
    /* M2: base 0x101000 needs first=1 at 8K align */
    {0x101000, 0, 8, 0, 0x000000, 2, 0x2000, 0, 1, 1},
    /* M4: exact last-frame fit */
    {0x100000, 0, 8, 0, 0x00007F, 1, 0x1000, 0, 1, 7},
    /* fragmentation: 6 free, no run of 3; then bridged */
    {0x100000, 0, 8, 0, 0x000024, 3, 0x1000, 0, 0, 0},
    {0x100000, 0, 8, 0, 0x000020, 3, 0x1000, 0, 1, 0},
    /* two regions: first exhausted, run lives at compact index 4 */
    {0x100000, 0x200000, 4, 4, 0x00000F, 2, 0x1000, 0, 1, 4},
    /* no cross-span merge: 2+2 clear bits are not a run of 3 */
    {0x100000, 0x200000, 2, 2, 0x000000, 3, 0x1000, 0, 0, 0},
    /* M13/limits: only-high run excluded; capped span rejects/accepts */
    {0x100000000ULL, 0, 4, 0, 0x000000, 1, 0x1000, 0x100000000ULL, 0, 0},
    {0x100000, 0, 8, 0, 0x000003, 2, 0x1000, 0x103000, 0, 0},
    {0x100000, 0, 8, 0, 0x000003, 2, 0x1000, 0x104000, 1, 2},
    /* exact end fit / one frame short */
    {0x100000, 0, 8, 0, 0x00001F, 3, 0x1000, 0, 1, 5},
    {0x100000, 0, 8, 0, 0x00003F, 3, 0x1000, 0, 0, 0},
    /* alignment candidates skip a set frame: 1 taken, 3 wins */
    {0x101000, 0, 8, 0, 0x000002, 1, 0x2000, 0, 1, 3},
    /* request larger than the pool */
    {0x100000, 0, 4, 0, 0x000000, 5, 0x1000, 0, 0, 0},
};
#define SCAN_CASES (sizeof(scan_cases) / sizeof(scan_cases[0]))

static void scan_phase(void)
{
    for (cpu_u64 i = 0; i < SCAN_CASES; ++i) {
        const struct scan_case *c = &scan_cases[i];
        struct pmm_region regions[2] = {
            {c->b0, c->b0 + (cpu_u64)c->p0 * PMM_PAGE_SIZE, PMM_USABLE},
            {c->b1, c->b1 + (cpu_u64)c->p1 * PMM_PAGE_SIZE, PMM_USABLE},
        };
        unsigned int count = c->p1 ? 2u : 1u;
        cpu_u64 frames = (cpu_u64)c->p0 + c->p1;
        cpu_u8 bits[3] = {(cpu_u8)c->bits, (cpu_u8)(c->bits >> 8), (cpu_u8)(c->bits >> 16)};
        cpu_u8 snap[3] = {bits[0], bits[1], bits[2]};
        cpu_u64 start = 0xDEADu;
        int found = pmm_scan_run(regions, count, bits, frames, c->n,
                                 c->align, c->limit, &start);
        require(found == c->want, "s-found");
        require(!found || start == c->want_start, "s-start");
        /* The scan is pure: inputs bit-identical, re-scan agrees. */
        require(bits[0] == snap[0] && bits[1] == snap[1] && bits[2] == snap[2], "s-pure");
        cpu_u64 start2 = 0xDEADu;
        require(pmm_scan_run(regions, count, bits, frames, c->n, c->align,
                             c->limit, &start2) == found &&
                (!found || start2 == start), "s-deterministic");
        say("[DMA] scase i=");
        say_dec(i);
        say(" n=");
        say_dec(c->n);
        say(" align=");
        say_hex(c->align);
        say(" lim=");
        say_hex(c->limit);
        say(" b0=");
        say_hex(c->b0);
        say(" p0=");
        say_dec(c->p0);
        say(" b1=");
        say_hex(c->b1);
        say(" p1=");
        say_dec(c->p1);
        say(" bits=");
        say_hex(c->bits);
        say(" found=");
        say_dec((cpu_u64)found);
        say(" start=");
        say_hex(start);
        say("\r\n");
    }
    /* Edges: all fail closed without touching anything. */
    {
        struct pmm_region r = {0x100000, 0x108000, PMM_USABLE};
        cpu_u8 bits[1] = {0};
        cpu_u64 start = 0xDEADu;
        require(!pmm_scan_run((void *)0, 1, bits, 8, 1, 0x1000, 0, &start), "s-null");
        require(!pmm_scan_run(&r, 1, (void *)0, 8, 1, 0x1000, 0, &start), "s-null");
        require(!pmm_scan_run(&r, 1, bits, 8, 1, 0x1000, 0, (void *)0), "s-null");
        require(!pmm_scan_run(&r, 1, bits, 8, 0, 0x1000, 0, &start), "s-n0");
        require(!pmm_scan_run(&r, 1, bits, 8, 1, 0, 0, &start), "s-a0");
        require(!pmm_scan_run(&r, 1, bits, 8, 1, 3, 0, &start), "s-anp2");
        require(!pmm_scan_run(&r, 1, bits, 0, 1, 0x1000, 0, &start), "s-f0");
        r.base = r.end;
        require(!pmm_scan_run(&r, 1, bits, 8, 1, 0x1000, 0, &start), "s-span");
        r.base = 0x100001;
        r.end = 0x108000;
        require(!pmm_scan_run(&r, 1, bits, 8, 1, 0x1000, 0, &start), "s-unalign");
        require(start == 0xDEADu, "s-untouched");
    }
    say("[DMA] scan ok cases=");
    say_dec(SCAN_CASES);
    say("\r\n");
}

/* ---------- live helpers ---------- */

static cpu_u32 alloc_id;

static void check_mapping(const struct dma_buffer *b, const char *tag)
{
    struct vm_space *space = vm_kernel_space();
    require(space != (void *)0, tag);
    require((cpu_u64)b->virt != b->phys, tag);
    require((cpu_u64)b->virt >= DMA_VA_BASE, tag);
    require(b->bus == b->phys, tag);
    require(b->phys % b->align == 0, tag);
    require((cpu_u64)b->virt + b->alloc_size < VM_MMIO_BASE ||
            (cpu_u64)b->virt >= VM_MMIO_END, tag);
    for (cpu_u64 i = 0; i < b->alloc_size / DMA_PAGE_SIZE; ++i) {
        struct vm_mapping m;
        require(vm_query(space, (cpu_u64)b->virt + i * DMA_PAGE_SIZE, &m) == VM_OK, tag);
        require(m.physical == b->phys + i * DMA_PAGE_SIZE, tag);
        require(m.permissions == VM_WRITE && !m.uncached, tag);
        enum pmm_state state;
        require(pmm_query(b->phys + i * DMA_PAGE_SIZE, &state) == PMM_OK &&
                state == PMM_STATE_ALLOCATED, tag);
    }
    require(pmm_check() && dma_check() && vm_check(space), tag);
}

static void emit_alloc(const struct dma_buffer *b)
{
    say("[DMA] alloc id=");
    say_dec(alloc_id++);
    say(" virt=");
    say_hex((cpu_u64)b->virt);
    say(" phys=");
    say_hex(b->phys);
    say(" bus=");
    say_hex(b->bus);
    say(" size=");
    say_hex(b->size);
    say(" alloc=");
    say_hex(b->alloc_size);
    say(" align=");
    say_hex(b->align);
    say("\r\n");
}

static void fill_seq(void *p, cpu_u64 n)
{
    volatile cpu_u8 *b = (volatile cpu_u8 *)p;
    for (cpu_u64 i = 0; i < n; ++i) b[i] = (cpu_u8)((i * 31u + 7u) & 0xFFu);
}

static void check_seq(const void *p, cpu_u64 n, const char *tag)
{
    const volatile cpu_u8 *b = (const volatile cpu_u8 *)p;
    for (cpu_u64 i = 0; i < n; ++i)
        require(b[i] == (cpu_u8)((i * 31u + 7u) & 0xFFu), tag);
}

static void check_zero(const void *p, cpu_u64 n, const char *tag)
{
    const volatile cpu_u8 *b = (const volatile cpu_u8 *)p;
    for (cpu_u64 i = 0; i < n; ++i) require(!b[i], tag);
}

static void validation_phase(void)
{
    /* Untouched-output sentinel across every failure path. */
    struct dma_buffer bad = {(void *)0xAA, 1, 2, 3, 4, 5, 6, 7};
    struct dma_buffer keep = bad;
    require(dma_alloc(0, DMA_PAGE_SIZE, DMA_ADDR_ANY, &bad) == DMA_INVALID, "v-size0");
    require(dma_alloc(1, 0, DMA_ADDR_ANY, &bad) == DMA_ALIGNMENT, "v-align0");
    require(dma_alloc(1, 3, DMA_ADDR_ANY, &bad) == DMA_ALIGNMENT, "v-align3");
    require(dma_alloc(1, DMA_PAGE_SIZE, DMA_ADDR_ANY, (void *)0) == DMA_INVALID, "v-null");
    require(dma_alloc(~0ULL, DMA_PAGE_SIZE, DMA_ADDR_ANY, &bad) == DMA_OVERFLOW, "v-over");
    require(dma_alloc(~0ULL - DMA_PAGE_SIZE + 2, DMA_PAGE_SIZE, DMA_ADDR_ANY, &bad) ==
            DMA_OVERFLOW, "v-over2");
    /* Largest non-overflowing size rounds cleanly, then OOMs on frames. */
    require(dma_alloc(~0ULL - DMA_PAGE_SIZE + 1, DMA_PAGE_SIZE, DMA_ADDR_ANY, &bad) ==
            DMA_OOM, "v-maxoom");
    require(bad.virt == keep.virt && bad.phys == keep.phys && bad.bus == keep.bus &&
            bad.size == keep.size && bad.alloc_size == keep.alloc_size &&
            bad.align == keep.align && bad.magic == keep.magic && bad.slot == keep.slot,
            "v-untouched");
    require(dma_free((void *)0) == DMA_INVALID, "v-fnull");
    /* Rounding ladder: 1, PAGE-1, PAGE, PAGE+1, multi-page. */
    {
        static const cpu_u64 sizes[] = {1, DMA_PAGE_SIZE - 1, DMA_PAGE_SIZE,
                                        DMA_PAGE_SIZE + 1, 3 * DMA_PAGE_SIZE};
        static const cpu_u64 wants[] = {1, 1, 1, 2, 3};
        for (unsigned int i = 0; i < 5; ++i) {
            struct dma_buffer b;
            require(dma_alloc(sizes[i], DMA_PAGE_SIZE, DMA_ADDR_ANY, &b) == DMA_OK, "v-round");
            require(b.size == sizes[i] && b.alloc_size == wants[i] * DMA_PAGE_SIZE, "v-round");
            check_mapping(&b, "v-round");
            check_zero(b.virt, b.alloc_size, "v-zero");
            emit_alloc(&b);
            cpu_u64 phys = b.phys, va = (cpu_u64)b.virt;
            require(dma_free(&b) == DMA_OK, "v-round");
            require(!b.virt && !b.phys && !b.bus && !b.size && !b.alloc_size &&
                    b.magic == DMA_MAGIC_DEAD, "v-poison");
            {
                struct vm_mapping m;
                require(vm_query(vm_kernel_space(), va, &m) == VM_NOT_MAPPED, "v-unmapped");
            }
            for (cpu_u64 j = 0; j < wants[i]; ++j) {
                enum pmm_state state;
                require(pmm_query(phys + j * DMA_PAGE_SIZE, &state) == PMM_OK &&
                        state == PMM_STATE_FREE, "v-refree");
            }
        }
    }
    say("[DMA] validation ok\r\n");
}

static void witness_phase(void)
{
    /* 3-page witness: PTE structure, spanning pattern, cache flags. */
    struct dma_buffer w;
    require(dma_alloc(3 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &w) == DMA_OK, "w-alloc");
    check_mapping(&w, "w-map");
    emit_alloc(&w);
    check_zero(w.virt, w.alloc_size, "w-zero");
    fill_seq(w.virt, w.alloc_size);
    check_seq(w.virt, w.alloc_size, "w-pattern");
    say("[DMA] pattern ok pages=3\r\n");
    /* Cross-page span: tail of 0, all of 1, head of 2, verified twice. */
    volatile cpu_u8 *p = (volatile cpu_u8 *)w.virt;
    for (cpu_u64 i = 0; i < DMA_PAGE_SIZE + 256; ++i)
        p[DMA_PAGE_SIZE - 128 + i] = (cpu_u8)(i & 0xFFu);
    for (cpu_u64 i = 0; i < DMA_PAGE_SIZE + 256; ++i)
        require(p[DMA_PAGE_SIZE - 128 + i] == (cpu_u8)(i & 0xFFu), "w-cross");
    check_seq(w.virt, DMA_PAGE_SIZE - 128, "w-cross");
    say("[DMA] crosspage ok\r\n");
    require(dma_free(&w) == DMA_OK, "w-free");
    say("[DMA] free id=");
    say_dec(alloc_id - 1);
    say(" ok=1\r\n");
}

static void reuse_phase(void)
{
    /* Security: stale 0xA5 must not survive free+realloc of same frames. */
    struct dma_buffer a, b;
    require(dma_alloc(2 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &a) == DMA_OK, "r-alloc");
    check_mapping(&a, "r-alloc");
    emit_alloc(&a);
    volatile cpu_u8 *p = (volatile cpu_u8 *)a.virt;
    for (cpu_u64 i = 0; i < a.alloc_size; ++i) p[i] = 0xA5;
    cpu_u64 phys = a.phys;
    require(dma_free(&a) == DMA_OK, "r-free");
    require(dma_alloc(2 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &b) == DMA_OK, "r-re");
    check_mapping(&b, "r-re");
    require(b.phys == phys, "r-same");
    emit_alloc(&b);
    check_zero(b.virt, b.alloc_size, "r-zero");
    say("[DMA] reuse ok phys=");
    say_hex(b.phys);
    say("\r\n");
    require(dma_free(&b) == DMA_OK, "r-free2");
}

static void multi_phase(void)
{
    /* A/B/C isolation, free-B survival, deterministic D reuse. */
    struct dma_buffer a, b, c, d;
    require(dma_alloc(DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &a) == DMA_OK, "m-a");
    check_mapping(&a, "m-a");
    emit_alloc(&a);
    require(dma_alloc(2 * DMA_PAGE_SIZE, 2 * DMA_PAGE_SIZE, DMA_ADDR_ANY, &b) == DMA_OK, "m-b");
    check_mapping(&b, "m-b");
    emit_alloc(&b);
    require(dma_alloc(4 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &c) == DMA_OK, "m-c");
    check_mapping(&c, "m-c");
    emit_alloc(&c);
    const struct dma_buffer *all[3] = {&a, &b, &c};
    for (unsigned int i = 0; i < 3; ++i)
        for (unsigned int j = i + 1; j < 3; ++j) {
            cpu_u64 a0 = all[i]->phys, a1 = a0 + all[i]->alloc_size;
            cpu_u64 b0 = all[j]->phys, b1 = b0 + all[j]->alloc_size;
            require(!(a0 < b1 && b0 < a1), "m-overlap");
            a0 = (cpu_u64)all[i]->virt;
            a1 = a0 + all[i]->alloc_size;
            b0 = (cpu_u64)all[j]->virt;
            b1 = b0 + all[j]->alloc_size;
            require(!(a0 < b1 && b0 < a1), "m-overlap");
        }
    volatile cpu_u8 *pa = (volatile cpu_u8 *)a.virt;
    volatile cpu_u8 *pc = (volatile cpu_u8 *)c.virt;
    for (cpu_u64 i = 0; i < a.alloc_size; ++i) pa[i] = 0xA1;
    for (cpu_u64 i = 0; i < c.alloc_size; ++i) pc[i] = 0xC3;
    cpu_u64 bphys = b.phys;
    require(dma_free(&b) == DMA_OK, "m-freeb");
    for (cpu_u64 i = 0; i < a.alloc_size; ++i) require(pa[i] == 0xA1, "m-intact");
    for (cpu_u64 i = 0; i < c.alloc_size; ++i) require(pc[i] == 0xC3, "m-intact");
    for (cpu_u64 i = 0; i < c.alloc_size / DMA_PAGE_SIZE; ++i) {
        enum pmm_state state;
        require(pmm_query(c.phys + i * DMA_PAGE_SIZE, &state) == PMM_OK &&
                state == PMM_STATE_ALLOCATED, "m-cframe");
    }
    require(dma_check(), "m-check");
    require(dma_alloc(2 * DMA_PAGE_SIZE, 2 * DMA_PAGE_SIZE, DMA_ADDR_ANY, &d) == DMA_OK, "m-d");
    check_mapping(&d, "m-d");
    require(d.phys == bphys, "m-reuse");
    emit_alloc(&d);
    say("[DMA] reuse ok phys=");
    say_hex(d.phys);
    say("\r\n");
    require(dma_free(&a) == DMA_OK && dma_free(&c) == DMA_OK && dma_free(&d) == DMA_OK, "m-free");
}

static void guard_phase(void)
{
    /* Neighbors prove no out-of-range zeroing or mapping mistakes. */
    struct dma_buffer g1, t, g2;
    require(dma_alloc(DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &g1) == DMA_OK, "g-1");
    check_mapping(&g1, "g-1");
    emit_alloc(&g1);
    require(dma_alloc(2 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &t) == DMA_OK, "g-t");
    check_mapping(&t, "g-t");
    emit_alloc(&t);
    require(dma_alloc(DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &g2) == DMA_OK, "g-2");
    check_mapping(&g2, "g-2");
    emit_alloc(&g2);
    volatile cpu_u8 *p1 = (volatile cpu_u8 *)g1.virt;
    volatile cpu_u8 *p2 = (volatile cpu_u8 *)g2.virt;
    for (cpu_u64 i = 0; i < DMA_PAGE_SIZE; ++i) {
        p1[i] = 0x1B;
        p2[i] = 0x2B;
    }
    fill_seq(t.virt, t.alloc_size);
    check_seq(t.virt, t.alloc_size, "g-target");
    for (cpu_u64 i = 0; i < DMA_PAGE_SIZE; ++i)
        require(p1[i] == 0x1B && p2[i] == 0x2B, "g-intact");
    require(dma_free(&g1) == DMA_OK && dma_free(&t) == DMA_OK && dma_free(&g2) == DMA_OK, "g-free");
    say("[DMA] guard ok\r\n");
}

static void double_free_phase(void)
{
    struct dma_buffer b;
    require(dma_alloc(DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &b) == DMA_OK, "d-alloc");
    check_mapping(&b, "d-alloc");
    emit_alloc(&b);
    cpu_u64 phys = b.phys;
    require(dma_free(&b) == DMA_OK, "d-free");
    require(dma_free(&b) == DMA_INVALID, "d-double");
    {
        enum pmm_state state;
        require(pmm_query(phys, &state) == PMM_OK && state == PMM_STATE_FREE, "d-bitmap");
    }
    require(pmm_check() && dma_check(), "d-check");
    say("[DMA] free ok=1 double=detected\r\n");
}

static void rollback_phase(void)
{
    /* Physical succeeds, VA fails: frames return, output untouched. */
    struct pmm_statistics before, after;
    require(pmm_statistics(&before) == PMM_OK, "rb-stats");
    dma_debug_va_limit(1);
    struct dma_buffer bad = {(void *)0xBB, 1, 2, 3, 4, 5, 6, 7};
    require(dma_alloc(2 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &bad) == DMA_OOM, "rb-oom");
    require(bad.virt == (void *)0xBB && bad.phys == 1 && bad.bus == 2 && bad.size == 3 &&
            bad.alloc_size == 4 && bad.align == 5, "rb-untouched");
    dma_debug_va_limit(0);
    require(pmm_statistics(&after) == PMM_OK, "rb-stats");
    require(after.allocated_bytes == before.allocated_bytes &&
            after.free_bytes == before.free_bytes, "rb-leak");
    require(pmm_check() && dma_check(), "rb-check");
    say("[DMA] rollback ok\r\n");
}

static void dma32_phase(void)
{
    struct dma_buffer b;
    require(dma_alloc(2 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_32BIT, &b) == DMA_OK, "h-alloc");
    check_mapping(&b, "h-alloc");
    require(b.bus + b.alloc_size - 1 <= DMA_ADDR_32BIT, "h-bound");
    emit_alloc(&b);
    say("[DMA] dma32 ok phys=");
    say_hex(b.phys);
    say("\r\n");
    require(dma_free(&b) == DMA_OK, "h-free");
}

static void oom_phase(void)
{
    /* Real exhaustion: over-sized request fails, allocator stays healthy. */
    struct pmm_statistics stats;
    require(pmm_statistics(&stats) == PMM_OK, "o-stats");
    struct dma_buffer bad = {(void *)0xCC, 1, 2, 3, 4, 5, 6, 7};
    require(dma_alloc(stats.free_bytes + DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &bad) ==
            DMA_OOM, "o-oom");
    require(bad.virt == (void *)0xCC, "o-untouched");
    struct dma_buffer survivor;
    require(dma_alloc(16 * DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &survivor) == DMA_OK,
            "o-survivor");
    check_mapping(&survivor, "o-survivor");
    emit_alloc(&survivor);
    require(dma_free(&survivor) == DMA_OK, "o-free");
    /* Registry cap: 16 live singles, 17th is DMA_BUSY. */
    struct dma_buffer herd[DMA_MAX_BUFFERS];
    for (unsigned int i = 0; i < DMA_MAX_BUFFERS; ++i) {
        require(dma_alloc(DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &herd[i]) == DMA_OK,
                "o-herd");
        check_mapping(&herd[i], "o-herd");
        emit_alloc(&herd[i]);
    }
    struct dma_buffer extra;
    require(dma_alloc(DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &extra) == DMA_BUSY, "o-busy");
    for (unsigned int i = 0; i < DMA_MAX_BUFFERS; ++i)
        require(dma_free(&herd[i]) == DMA_OK, "o-herdfree");
    say("[DMA] oom ok\r\n");
    say("[DMA] busy ok\r\n");
}

void dma_self_test(void)
{
    alloc_id = 0;
    scan_phase();
    validation_phase();
    witness_phase();
    reuse_phase();
    multi_phase();
    guard_phase();
    double_free_phase();
    rollback_phase();
    dma32_phase();
    oom_phase();
    /* Barrier/sync smoke: exercised, documented no-ops on coherent x86. */
    dma_rmb();
    dma_wmb();
    {
        struct dma_buffer b;
        require(dma_alloc(DMA_PAGE_SIZE, DMA_PAGE_SIZE, DMA_ADDR_ANY, &b) == DMA_OK, "s-alloc");
        check_mapping(&b, "s-alloc");
        emit_alloc(&b);
        dma_sync_for_device(&b);
        dma_sync_for_cpu(&b);
        require(dma_free(&b) == DMA_OK, "s-free");
    }
    say("[DMA] sync ok\r\n");
    require(pmm_check() && dma_check() && vm_check(vm_kernel_space()), "final");
    say("[DMA] dma verified\r\n");
}
