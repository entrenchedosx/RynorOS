#include "rttest.h"
#include "load.h"
#include "ksched.h"
#include "irq.h"
#include "io.h"
#include "heap.h"
#include "pmm.h"
#include "serial.h"
#include "blk.h"
#include "fs.h"

/* Stage 18c runtime conformance: real user/lib/rt programs through the
   validated 18b loader into CPL3 (exit/write/yield only, timer masked so
   every number is deterministic). Runs after load_self_test; its [RT]
   lines trail [LOAD] verified. Transcript always ends with [RT] rt
   verified or [RT] no image, skipped (completion-race discipline). */

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[RT] failure=");
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
    text("[RT] accounting balanced\r\n");
}

/* File staging: largest rt program stays under header + 4K code + 4K
   data (single-read cap); 2-byte aligned for the block layer. */
static _Alignas(2) cpu_u8 file_buf[16384u];

/* Run to a terminal code, resuming yields/writes. The timer stays
   masked, so preemptions cannot occur; any other return fails. */
static cpu_u64 run_rt(struct user_link *link)
{
    cpu_u64 rc = user_enter_image(link);
    while (rc == USER_RUN_YIELDED || rc == USER_RUN_WRITTEN)
        rc = user_resume(link);
    return rc;
}

static struct user_context *load_staged(const cpu_u8 *img, cpu_u64 len, const char *why)
{
    struct user_context *c = 0;
    require(load_program(&c, img, len) && c, why);
    text("[RT] create slot=");
    number(c->slot);
    field(" code_size=", c->code_size);
    field(" tables=", c->table_pages_at_create);
    text("\r\n");
    return c;
}

static void program_evidence(const char *path, cpu_u64 code, cpu_u64 fsz, cpu_u64 msz)
{
    text("[RT] program path=");
    text(path);
    text(" entry=");
    hex(USER_CODE_BASE);
    field(" code=", code);
    field(" data=", fsz);
    text("/");
    number(msz);
    text("\r\n");
}

static void exit_evidence(struct user_context *c)
{
    text("[RT] exit slot=");
    number(c->slot);
    field(" code=", c->exit_code);
    text("\r\n");
}

static void destroy_evidence(struct user_context *c)
{
    cpu_u64 slot = c->slot;
    require(thread_detach_user(), "detach");
    require(user_destroy(c), "destroy");
    text("[RT] destroy slot=");
    number(slot);
    text("\r\n");
}

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

static void phase(const char *path)
{
    struct accounting before = account();
    cpu_u32 h = 0;
    struct fs_stat st;
    require(fs_stat(path, &st) == FS_OK, "stat");
    require(try_open(path, &h), "open");
    cpu_u64 len = read_file(h, st.size, "read");
    require(fs_close(h) == FS_OK, "close");
    struct rnyx_layout lay;
    require(rnyx_validate(file_buf, len, &lay) == RNYX_OK, "validate");
    program_evidence(path, lay.code_len, lay.data_filesz, lay.data_memsz);
    struct user_context *c = load_staged(file_buf, len, "load");
    require(run_rt(&c->link) == USER_RUN_EXITED, "run");
    require(c->state == USER_EXITED && c->exit_code == 0, "status");
    exit_evidence(c);
    destroy_evidence(c);
    balanced(before);
}

static const char *want_paths[] = {
    "/rt/fmt.rnx", "/rt/alloc.rnx", "/rt/write.rnx",
    "/rt/nap.rnx", "/rt/wait.rnx", "/rt/nosys.rnx",
    "/rt/rlprint.rnx",
};

void rt_self_test(void)
{
    require(cpu_interrupts_disabled(), "context");
    /* Probe every block device for the conformance set. A device carrying
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
        text("[RT] no image, skipped\r\n");
        (void)serial_flush();
        return;
    }
    text("[SYSTEM] RynorOS " RYNOR_VERSION " | Rynorkernel | stage18c runtime library\r\n");
    /* Cooperative conformance only: the timer drive stays masked, so
       yields (not preemptions) drive every number deterministically. */
    require(irq_set_enabled(0, 0), "quiesce");
    for (unsigned int i = 0; i < sizeof want_paths / sizeof want_paths[0]; ++i)
        phase(want_paths[i]);
    fs_unmount();
    text("[TEST] rt self-test passed\r\n[RT] rt verified\r\n");
    (void)serial_flush();
}
