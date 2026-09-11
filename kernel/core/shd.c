#include "shd.h"
#include "io.h"
#include "load.h"
#include "fs.h"
#include "blk.h"
#include "user.h"
#include "proc.h"
#include "pipe.h"
#include "ksched.h"
#include "irq.h"
#include "pmm.h"
#include "heap.h"
#include "vm.h"
#include "serial.h"
#include "cpu.h"

/* Stage 18d Slice E shell boot: mount, load /bin/sh, enter on the
 * bootstrap thread. Policy lives in CPL3; this driver only moves
 * bytes and contexts. Markers (exact): [SHD] missing /bin/sh,
 * [SHD] malformed /bin/sh, [SHD] halt code=N, each with [SHD]
 * balanced on the verified path. Controlled halts only (never
 * thread_exit on bootstrap, never a ring-0 evaluator fallback). */

static void halt(const char *why) __attribute__((noreturn));
static void halt(const char *why)
{
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}

struct shd_account {
    struct pmm_statistics pmm;
    struct heap_statistics heap;
    cpu_u64 tables;
};

static struct shd_account shd_account_now(void)
{
    struct shd_account a;
    if (pmm_statistics(&a.pmm) != PMM_OK || heap_statistics(&a.heap) != HEAP_OK)
        halt("[SHD] failure=statistics");
    a.tables = vm_kernel_space()->table_pages;
    return a;
}

static void shd_balanced(struct shd_account before)
{
    struct shd_account after = shd_account_now();
    if (after.pmm.allocated_bytes != before.pmm.allocated_bytes ||
        after.pmm.free_bytes != before.pmm.free_bytes ||
        after.tables != before.tables ||
        after.heap.used_bytes != before.heap.used_bytes ||
        after.heap.free_blocks != before.heap.free_blocks ||
        !pmm_check() || !vm_check(vm_kernel_space()) || !heap_check() ||
        !user_check() || !proc_check() || !pipe_check())
        halt("[SHD] failure=resource_balance");
    (void)serial_write("[SHD] balanced\r\n");
    (void)serial_flush();
}

static int foreground(void) { return cpu_interrupts_disabled() && !irq_in_context(); }

/* Find a mounted filesystem carrying /bin/sh (probe blk 0..3; first
   hit stays mounted for the shell's fread/script use). */
static int mount_shell_fs(void)
{
    static const char *marker = "/bin/sh";
    struct fs_stat st;
    for (cpu_u32 id = 0; id < 4u; ++id) {
        if (!blk_device(id)) continue;
        if (fs_mount(id) != FS_OK) continue;
        if (fs_stat(marker, &st) == FS_OK && st.type == FS_TYPE_FILE)
            return 1;
        fs_unmount();
    }
    return 0;
}

void shd_boot(void)
{
    struct shd_account base = shd_account_now();
    struct fs_stat st;
    cpu_u32 h = 0;
    cpu_u8 *img = 0;
    cpu_u64 got = 0;
    struct user_context *ctx = 0;
    struct rnyx_layout lay;
    cpu_u64 rc;
    static const char script_path[] = SHELL_SCRIPT_PATH;
    if (!foreground()) halt("[SHD] failure=context");
    /* Ticks (preemption evidence) and keyboard delivery for the
       interactive shell; the test drivers quiesced both. */
    if (!irq_set_enabled(0, 1)) halt("[SHD] failure=timer");
    if (!irq_set_enabled(1, 1)) halt("[SHD] failure=kbd");
    if (!mount_shell_fs()) {
        shd_balanced(base);
        halt("[SHD] missing /bin/sh");
    }
    if (fs_stat("/bin/sh", &st) != FS_OK || st.type != FS_TYPE_FILE) {
        fs_unmount();
        shd_balanced(base);
        halt("[SHD] missing /bin/sh");
    }
    if (st.size < RNYX_HEADER_LEN || st.size > 28u + 65536u + 32768u) {
        fs_unmount();
        shd_balanced(base);
        halt("[SHD] malformed /bin/sh");
    }
    if (fs_open("/bin/sh", &h) != FS_OK) {
        fs_unmount();
        shd_balanced(base);
        halt("[SHD] missing /bin/sh");
    }
    if (heap_alloc(st.size, 8, (void **)&img) != HEAP_OK)
        halt("[SHD] failure=heap");
    while (got < st.size) {
        cpu_u64 chunk = st.size - got > FS_MAX_READ_BYTES ? FS_MAX_READ_BYTES : st.size - got;
        cpu_u64 n = 0;
        if (fs_read(h, got, img + got, chunk, &n) != FS_OK || n != chunk) {
            (void)fs_close(h);
            (void)heap_free(img);
            fs_unmount();
            shd_balanced(base);
            halt("[SHD] malformed /bin/sh");
        }
        got += n;
    }
    if (fs_close(h) != FS_OK) {
        (void)heap_free(img);
        fs_unmount();
        shd_balanced(base);
        halt("[SHD] malformed /bin/sh");
    }
    if (rnyx_validate(img, st.size, &lay) != RNYX_OK) {
        (void)heap_free(img);
        fs_unmount();
        shd_balanced(base);
        halt("[SHD] malformed /bin/sh");
    }
    if (!load_program(&ctx, img, st.size)) {
        (void)heap_free(img);
        fs_unmount();
        shd_balanced(base);
        halt("[SHD] malformed /bin/sh");
    }
    if (heap_free(img) != HEAP_OK) halt("[SHD] failure=heap");
    img = 0;
    /* Bootstrap argv: empty (interactive) or one script path. */
    if (script_path[0]) {
        cpu_u64 ptrs[1], lens[1], L = 0;
        while (L < 33 && script_path[L]) ++L;
        if (L == 0 || L > 32) halt("[SHD] failure=script-path");
        ptrs[0] = (cpu_u64)script_path;
        lens[0] = L;
        if (!user_prepare_argv(ctx, 1, ptrs, lens)) halt("[SHD] failure=argv");
    }
    /* Entered on the bootstrap thread (never a disposable worker: the
       bootstrap thread cannot thread_exit, and the shell is never
       reaped). user_enter_image attaches the link here. No proc slot:
       children are kernel-owned, and fd routing defaults to
       KBD/SERIAL automatically. */
    /* Drive the shell like the loader test driver: terminal-for-call
       gates (read/write/spawn/wait/terminate/fread/spawn_pipe),
       yields, and preemptions resume transparently; only EXITED ends
       the session and only FAULTED aborts it. */
    rc = user_enter_image(&ctx->link);
    for (;;) {
        if (rc == USER_RUN_EXITED) break;
        if (rc == USER_RUN_FAULTED) {
            /* Diagnostic path only (no green session faults): halt is
               terminal for the machine, so no balance is taken with
               possibly live orphaned children. */
            if (!thread_detach_user()) halt("[SHD] failure=detach");
            if (!user_destroy(ctx)) halt("[SHD] failure=destroy");
            fs_unmount();
            halt("[SHD] fault shell");
        }
        if (rc != USER_RUN_YIELDED && rc != USER_RUN_WRITTEN &&
            rc != USER_RUN_READ && rc != USER_RUN_PREEMPTED &&
            rc != USER_RUN_SPAWNED && rc != USER_RUN_WAITED &&
            rc != USER_RUN_TERMINATED && rc != USER_RUN_FREAD &&
            rc != USER_RUN_SPAWN_PIPE)
            halt("[SHD] failure=run");
        rc = user_resume(&ctx->link);
    }
    {
        cpu_u64 code = ctx->exit_code;
        if (!thread_detach_user()) halt("[SHD] failure=detach");
        if (!user_destroy(ctx)) halt("[SHD] failure=destroy");
        fs_unmount();
        shd_balanced(base);
        (void)serial_write("[SHD] halt code=");
        {
            char b[21];
            unsigned int i = 20;
            b[i] = 0;
            do { b[--i] = (char)('0' + code % 10); code /= 10; } while (code);
            (void)serial_write(b + i);
        }
        (void)serial_write("\r\n");
        (void)serial_flush();
        cpu_halt();
    }
}
