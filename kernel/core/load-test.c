#include "load.h"
#include "ksched.h"
#include "irq.h"
#include "io.h"
#include "heap.h"
#include "pmm.h"
#include "serial.h"
#include "blk.h"
#include "fs.h"

/* Stage 18b loader self-test: filesystem-backed RYNX programs through
   the loader into CPL3 (exit, write, preemption), isolation across two
   slots, BSS zeroing, malformed-envelope rejection, and OOM rollback.
   Runs after user_self_test; its [LOAD] lines trail [USER] verified.
   Transcript always ends with [LOAD] load verified or
   [LOAD] no image, skipped (completion-race discipline). */

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[LOAD] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}
static void text(const char *s) { require(serial_write(s), "serial"); }
static void number(cpu_u64 n)
{
    char b[21];
    unsigned int i = 20;
    b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    text(b + i);
}
static void field(const char *s, cpu_u64 n) { text(s); number(n); }
static void hex(cpu_u64 v)
{
    char b[19] = "0x0000000000000000";
    for (unsigned int n = 0; n < 16; ++n) b[17 - n] = "0123456789abcdef"[(v >> (n * 4)) & 15];
    text(b);
}

struct accounting { struct pmm_statistics pmm; struct heap_statistics heap; cpu_u64 tables; };
static struct accounting account(void)
{
    struct accounting a;
    require(pmm_statistics(&a.pmm) == PMM_OK && heap_statistics(&a.heap) == HEAP_OK, "statistics");
    a.tables = vm_kernel_space()->table_pages;
    return a;
}
static void balanced(struct accounting before)
{
    struct accounting after = account();
    require(after.pmm.allocated_bytes == before.pmm.allocated_bytes &&
            after.pmm.free_bytes == before.pmm.free_bytes && after.tables == before.tables &&
            after.heap.used_bytes == before.heap.used_bytes &&
            after.heap.free_blocks == before.heap.free_blocks && pmm_check() &&
            vm_check(vm_kernel_space()) && heap_check() && user_check(), "resource_balance");
    text("[LOAD] accounting balanced\r\n");
}

/* File staging: largest RYNX is header + 4K code + 4K data (under the
   single-read cap); 2-byte aligned for the block layer. */
static _Alignas(2) cpu_u8 file_buf[16384u];

/* Run to a terminal code, resuming yields/writes/preemptions. */
static cpu_u64 run_loaded(struct user_link *link)
{
    cpu_u64 rc = user_enter_image(link);
    while (rc == USER_RUN_PREEMPTED || rc == USER_RUN_YIELDED || rc == USER_RUN_WRITTEN)
        rc = user_resume(link);
    return rc;
}

static void map_evidence(struct user_context *c)
{
    static const struct { cpu_u64 va; unsigned int perm; const char *kind, *ps; } rows[] = {
        {USER_CODE_BASE, VM_USER | VM_EXECUTE, "code", "rx"},
        {USER_DATA_BASE, VM_USER | VM_WRITE, "data", "rw"},
        {USER_STACK_PAGE, VM_USER | VM_WRITE, "stack", "rw"},
    };
    for (unsigned int i = 0; i < 3; ++i) {
        struct vm_mapping m;
        require(vm_query(&c->space, rows[i].va, &m) == VM_OK &&
                m.permissions == rows[i].perm, "map_perm");
        text("[LOAD] map slot=");
        number(c->slot);
        text(" kind=");
        text(rows[i].kind);
        text(" va=");
        hex(rows[i].va);
        text(" pa=");
        hex(m.physical);
        text(" perm=");
        text(rows[i].ps);
        text("\r\n");
        require(!(m.physical & (VM_PAGE_SIZE - 1)), "map_align");
    }
}

static void exit_evidence(struct user_context *c)
{
    text("[LOAD] exit slot=");
    number(c->slot);
    field(" code=", c->exit_code);
    text("\r\n");
}

static void destroy_evidence(struct user_context *c)
{
    cpu_u64 slot = c->slot;
    require(thread_detach_user(), "detach");
    require(user_destroy(c), "destroy");
    text("[LOAD] destroy slot=");
    number(slot);
    text("\r\n");
}

/* Load the staged file bytes into a fresh context (validates first). */
static struct user_context *load_staged(const cpu_u8 *img, cpu_u64 len, const char *why)
{
    struct user_context *c = 0;
    require(load_program(&c, img, len) && c, why);
    text("[LOAD] create slot=");
    number(c->slot);
    field(" code_size=", c->code_size);
    field(" tables=", c->table_pages_at_create);
    text("\r\n");
    return c;
}

static void program_evidence(const char *path, cpu_u64 code, cpu_u64 fsz, cpu_u64 msz)
{
    text("[LOAD] program path=");
    text(path);
    text(" entry=");
    hex(USER_CODE_BASE);
    field(" code=", code);
    field(" data=", fsz);
    text("/");
    number(msz);
    text("\r\n");
}

/* Read a whole file into the staging buffer (exact size, no short read). */
static cpu_u64 read_file(cpu_u32 h, cpu_u64 size, const char *why)
{
    cpu_u64 n = 0;
    require(size <= sizeof file_buf, "too-big");
    require(fs_read(h, 0, file_buf, size, &n) == FS_OK && n == size, why);
    return n;
}

static int try_open(const char *path, cpu_u32 *h)
{
    return fs_open(path, h) == FS_OK;
}

static void phase_exit42(void)
{
    struct accounting before = account();
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat("/rnyx/exit42.rnx", &st) == FS_OK, "stat");
    require(try_open("/rnyx/exit42.rnx", &h), "open");
    cpu_u64 len = read_file(h, st.size, "read");
    require(fs_close(h) == FS_OK, "close");
    struct rnyx_layout lay;
    require(rnyx_validate(file_buf, len, &lay) == RNYX_OK, "validate");
    program_evidence("/rnyx/exit42.rnx", lay.code_len, lay.data_filesz, lay.data_memsz);
    struct user_context *c = load_staged(file_buf, len, "load");
    map_evidence(c);
    require(run_loaded(&c->link) == USER_RUN_EXITED, "run");
    require(c->state == USER_EXITED && c->exit_code == 42, "status");
    exit_evidence(c);
    destroy_evidence(c);
    balanced(before);
}

static void phase_writehello(void)
{
    struct accounting before = account();
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat("/rnyx/writehello.rnx", &st) == FS_OK, "stat");
    require(try_open("/rnyx/writehello.rnx", &h), "open");
    cpu_u64 len = read_file(h, st.size, "read");
    require(fs_close(h) == FS_OK, "close");
    struct rnyx_layout lay;
    require(rnyx_validate(file_buf, len, &lay) == RNYX_OK, "validate");
    program_evidence("/rnyx/writehello.rnx", lay.code_len, lay.data_filesz, lay.data_memsz);
    struct user_context *c = load_staged(file_buf, len, "load");
    map_evidence(c);
    require(run_loaded(&c->link) == USER_RUN_EXITED, "run");
    require(c->state == USER_EXITED && c->exit_code == 0, "status");
    require(c->sys_writes == 1 && c->sys_result == 5, "writeback");
    exit_evidence(c);
    destroy_evidence(c);
    balanced(before);
}

static void phase_sysprobe(void)
{
    struct accounting before = account();
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat("/rnyx/sysprobe.rnx", &st) == FS_OK, "stat");
    require(try_open("/rnyx/sysprobe.rnx", &h), "open");
    cpu_u64 len = read_file(h, st.size, "read");
    require(fs_close(h) == FS_OK, "close");
    struct rnyx_layout lay;
    require(rnyx_validate(file_buf, len, &lay) == RNYX_OK, "validate");
    program_evidence("/rnyx/sysprobe.rnx", lay.code_len, lay.data_filesz, lay.data_memsz);
    struct user_context *c = load_staged(file_buf, len, "load");
    map_evidence(c);
    require(run_loaded(&c->link) == USER_RUN_EXITED, "run");
    require(c->state == USER_EXITED && c->exit_code == 0, "status");
    exit_evidence(c);
    destroy_evidence(c);
    balanced(before);
}

static void phase_fib27(void)
{
    struct accounting before = account();
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat("/rnyx/fib27.rnx", &st) == FS_OK, "stat");
    require(try_open("/rnyx/fib27.rnx", &h), "open");
    cpu_u64 len = read_file(h, st.size, "read");
    require(fs_close(h) == FS_OK, "close");
    struct rnyx_layout lay;
    require(rnyx_validate(file_buf, len, &lay) == RNYX_OK, "validate");
    program_evidence("/rnyx/fib27.rnx", lay.code_len, lay.data_filesz, lay.data_memsz);
    struct user_context *c = load_staged(file_buf, len, "load");
    map_evidence(c);
    cpu_u64 ticks0 = user_cpl3_ticks();
    /* Ticks on: the spin window must see timer preemption. */
    require(irq_set_enabled(0, 1), "unmask");
    cpu_u64 rc = run_loaded(&c->link);
    require(irq_set_enabled(0, 0), "remask");
    require(rc == USER_RUN_EXITED, "run");
    require(c->state == USER_EXITED && c->exit_code == 196418, "status");
    require(user_cpl3_ticks() > ticks0 && c->preemptions > 0, "preempted");
    text("[LOAD] tickspin cpl3_delta=");
    number(user_cpl3_ticks() - ticks0);
    field(" preemptions=", c->preemptions);
    text("\r\n");
    exit_evidence(c);
    destroy_evidence(c);
    balanced(before);
}

static void phase_isolation(void)
{
    struct accounting before = account();
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat("/rnyx/exit42.rnx", &st) == FS_OK, "stat");
    require(try_open("/rnyx/exit42.rnx", &h), "open");
    cpu_u64 len = read_file(h, st.size, "read");
    require(fs_close(h) == FS_OK, "close");
    /* Same image, two live contexts: identical VAs must resolve to
       disjoint physical frames (CR3 isolation, not luck). */
    struct rnyx_layout isolay;
    require(rnyx_validate(file_buf, len, &isolay) == RNYX_OK, "isolayout");
    struct user_context *a = load_staged(file_buf, len, "load-a");
    program_evidence("/rnyx/exit42.rnx", isolay.code_len, isolay.data_filesz, isolay.data_memsz);
    struct user_context *b = load_staged(file_buf, len, "load-b");
    program_evidence("/rnyx/exit42.rnx", isolay.code_len, isolay.data_filesz, isolay.data_memsz);
    require(a != b && a->slot != b->slot, "slots");
    require(a->code_frame[0] != b->code_frame[0] && a->data_frame[0] != b->data_frame[0] &&
            a->stack_frame != b->stack_frame, "frames-disjoint");
    require(a->code_frame[0] != b->data_frame[0] && a->code_frame[0] != b->stack_frame &&
            a->data_frame[0] != b->code_frame[0] && a->data_frame[0] != b->stack_frame &&
            a->stack_frame != b->code_frame[0] && a->stack_frame != b->data_frame[0],
            "frames-cross-disjoint");
    map_evidence(a);
    map_evidence(b);
    require(run_loaded(&a->link) == USER_RUN_EXITED && a->exit_code == 42, "run-a");
    exit_evidence(a);
    require(thread_detach_user(), "detach-a");
    require(run_loaded(&b->link) == USER_RUN_EXITED && b->exit_code == 42, "run-b");
    exit_evidence(b);
    cpu_u64 aslot = a->slot, bslot = b->slot;
    require(thread_detach_user(), "detach-b");
    require(user_destroy(a), "destroy-a");
    text("[LOAD] destroy slot=");
    number(aslot);
    text("\r\n");
    require(user_destroy(b), "destroy-b");
    text("[LOAD] destroy slot=");
    number(bslot);
    text("\r\n");
    balanced(before);
}

static void phase_bsszero(void)
{
    struct accounting before = account();
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat("/rnyx/bsszero.rnx", &st) == FS_OK, "stat");
    require(try_open("/rnyx/bsszero.rnx", &h), "open");
    cpu_u64 len = read_file(h, st.size, "read");
    require(fs_close(h) == FS_OK, "close");
    struct rnyx_layout lay;
    require(rnyx_validate(file_buf, len, &lay) == RNYX_OK, "validate");
    require(lay.data_filesz == 0 && lay.data_memsz == 64, "bss-shape");
    program_evidence("/rnyx/bsszero.rnx", lay.code_len, lay.data_filesz, lay.data_memsz);
    struct user_context *c = load_staged(file_buf, len, "load");
    /* BSS tail must read as zeros before first entry (no stale bytes). */
    volatile cpu_u8 *d = vm_frame_access(c->data_frame[0]);
    require(d != 0, "bss-access");
    for (cpu_u64 i = 0; i < lay.data_memsz; ++i)
        require(d[i] == 0, "bss-zero");
    map_evidence(c);
    require(run_loaded(&c->link) == USER_RUN_EXITED, "run");
    require(c->state == USER_EXITED && c->exit_code == 42, "status");
    exit_evidence(c);
    destroy_evidence(c);
    balanced(before);
}

static const struct { const char *path; int reason; } bad_files[] = {
    {"/rnyx/bad00.rnx", RNYX_ERR_MAGIC},
    {"/rnyx/bad01.rnx", RNYX_ERR_VERSION},
    {"/rnyx/bad02.rnx", RNYX_ERR_ARCH},
    {"/rnyx/bad03.rnx", RNYX_ERR_HEADER},
    {"/rnyx/bad04.rnx", RNYX_ERR_HEADER},
    {"/rnyx/bad05.rnx", RNYX_ERR_ENTRY},
    {"/rnyx/bad06.rnx", RNYX_ERR_CODE_SIZE},
    {"/rnyx/bad07.rnx", RNYX_ERR_CODE_SIZE},
    {"/rnyx/bad08.rnx", RNYX_ERR_DATA_SIZE},
    {"/rnyx/bad09.rnx", RNYX_ERR_DATA_SIZE},
    {"/rnyx/bad10.rnx", RNYX_ERR_SHAPE},
    {"/rnyx/bad11.rnx", RNYX_ERR_SHAPE},
    {"/rnyx/bad12.rnx", RNYX_ERR_DATA_SIZE},
};

static const char *reason_word(int reason)
{
    switch (reason) {
    case RNYX_ERR_MAGIC: return "magic";
    case RNYX_ERR_VERSION: return "version";
    case RNYX_ERR_ARCH: return "arch";
    case RNYX_ERR_HEADER: return "header";
    case RNYX_ERR_ENTRY: return "entry";
    case RNYX_ERR_CODE_SIZE: return "code_size";
    case RNYX_ERR_DATA_SIZE: return "data_size";
    case RNYX_ERR_SHAPE: return "shape";
    default: return "truncated";
    }
}

static void phase_badmatrix(void)
{
    struct accounting before = account();
    for (unsigned int i = 0; i < sizeof bad_files / sizeof bad_files[0]; ++i) {
        cpu_u32 h = 0;
        struct fs_stat st;
        require(fs_stat(bad_files[i].path, &st) == FS_OK, "bad-stat");
        require(try_open(bad_files[i].path, &h), "bad-open");
        cpu_u64 len = read_file(h, st.size, "bad-read");
        require(fs_close(h) == FS_OK, "bad-close");
        struct rnyx_layout lay;
        require(rnyx_validate(file_buf, len, &lay) == bad_files[i].reason, "bad-reason");
        /* The loader itself must also refuse (no half-created context). */
        struct user_context *c = (void *)1;
        require(!load_program(&c, file_buf, len) && c == (void *)1, "bad-load");
        text("[LOAD] reject path=");
        text(bad_files[i].path);
        text(" reason=");
        text(reason_word(bad_files[i].reason));
        text("\r\n");
    }
    balanced(before);
}

static void phase_oom(void)
{
    struct accounting before = account();
    cpu_u64 head = 0, frame = 0;
    while (pmm_allocate(&frame) == PMM_OK) {
        volatile cpu_u64 *p = vm_frame_access(frame);
        require(p != 0, "oom_access");
        *p = head;
        head = frame;
    }
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat("/rnyx/exit42.rnx", &st) == FS_OK, "oom-stat");
    require(try_open("/rnyx/exit42.rnx", &h), "oom-open");
    /* File staging uses the static buffer (no PMM), so the read works;
       the load itself must fail clean with nothing created. */
    cpu_u64 n = 0;
    require(fs_read(h, 0, file_buf, st.size, &n) == FS_OK && n == st.size, "oom-read");
    require(fs_close(h) == FS_OK, "oom-close");
    struct user_context *x = (void *)1;
    require(!load_program(&x, file_buf, n) && x == (void *)1, "oom-fail");
    while (head) {
        frame = head;
        head = *(volatile cpu_u64 *)vm_frame_access(frame);
        require(pmm_release(frame) == PMM_OK, "oom_restore");
    }
    balanced(before);
}

static const char *want_paths[] = {
    "/rnyx/exit42.rnx", "/rnyx/writehello.rnx", "/rnyx/fib27.rnx",
    "/rnyx/bsszero.rnx", "/rnyx/sysprobe.rnx",
    "/rnyx/bad00.rnx", "/rnyx/bad01.rnx", "/rnyx/bad02.rnx",
    "/rnyx/bad03.rnx", "/rnyx/bad04.rnx", "/rnyx/bad05.rnx",
    "/rnyx/bad06.rnx", "/rnyx/bad07.rnx", "/rnyx/bad08.rnx",
    "/rnyx/bad09.rnx", "/rnyx/bad10.rnx", "/rnyx/bad11.rnx",
    "/rnyx/bad12.rnx",
};

void load_self_test(void)
{
    require(cpu_interrupts_disabled(), "context");
    /* Probe every block device for the program set. A device carrying
       any wanted path commits the boot to the full suite (a partial set
       fails loudly); no device with any wanted path means skip. */
    int found = 0;
    for (cpu_u32 id = 0; id < 4u; ++id) {
        if (!blk_device(id)) continue;
        if (fs_mount(id) != FS_OK) continue;
        cpu_u32 h = 0;
        unsigned int i = 0;
        for (; i < sizeof want_paths / sizeof want_paths[0]; ++i)
            if (try_open(want_paths[i], &h)) {
                require(fs_close(h) == FS_OK, "probe-close");
                break;
            }
        if (i < sizeof want_paths / sizeof want_paths[0]) {
            found = 1;
            break;
        }
        fs_unmount();
    }
    if (!found) {
        text("[LOAD] no image, skipped\r\n");
        (void)serial_flush();
        return;
    }
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage18b program loader\r\n");
    /* Deterministic CPL3 runs: mask the timer drive (nothing here needs
       ticks except the tickspin phase, which unmasks explicitly). */
    require(irq_set_enabled(0, 0), "quiesce");
    phase_exit42();
    phase_writehello();
    phase_fib27();
    phase_isolation();
    phase_bsszero();
    phase_sysprobe();
    phase_badmatrix();
    phase_oom();
    fs_unmount();
    text("[TEST] load self-test passed\r\n[LOAD] load verified\r\n");
    (void)serial_flush();
}
