#include "proctest.h"
#include "proc.h"
#include "user.h"
#include "load.h"
#include "uapi.h"
#include "syscall.h"
#include "ksched.h"
#include "irq.h"
#include "fs.h"
#include "blk.h"
#include "pmm.h"
#include "heap.h"
#include "vm.h"
#include "serial.h"

/* Stage 18d Slice C gated self-test: process table, handles, lifecycle,
   spawn/wait/terminate, kill flag, argv, RYNX v1/v2 loader, owner
   cleanup. Runs after read_self_test when RYNOR_PROC_TEST=1; silent
   otherwise. Executable images come from the /t/ filesystem image
   (absolute paths only; no Slice D discovery). The timer stays masked:
   all child progress is cooperative (yields) plus explicit driver
   yields, so every count is deterministic. Validation of hostile
   userspace pointers happens at the syscall trust boundary (guest
   programs p_regprobe/p_alias); the driver uses the internal API with
   kernel-memory arguments. */

static void fail(const char *why) __attribute__((noreturn));
static void fail(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[PROC] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}
static void require(int ok, const char *why) { if (!ok) fail(why); }
static void text(const char *s) { require(serial_write(s), "serial"); }
static void number(cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    text(b + i);
}
static void field(const char *s, cpu_u64 n) { text(s); number(n); }

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
            vm_check(vm_kernel_space()) && heap_check() && user_check() &&
            proc_check(), "resource_balance");
    text("[PROC] accounting balanced\r\n");
}

/* Image heap staging (transient per phase; balanced). */
static cpu_u8 *load_image(const char *path, cpu_u64 *len_out, const char *why)
{
    struct fs_stat st;
    cpu_u32 h = 0;
    cpu_u8 *img = 0;
    cpu_u64 got = 0;
    require(fs_stat(path, &st) == FS_OK, why);
    require(st.type == FS_TYPE_FILE, why);
    require(st.size >= RNYX_HEADER_LEN && st.size <= 28u + 32768u + 16384u, why);
    require(fs_open(path, &h) == FS_OK, why);
    require(heap_alloc(st.size, 8, (void **)&img) == HEAP_OK, why);
    while (got < st.size) {
        cpu_u64 chunk = st.size - got > FS_MAX_READ_BYTES ? FS_MAX_READ_BYTES : st.size - got;
        cpu_u64 n = 0;
        require(fs_read(h, got, img + got, chunk, &n) == FS_OK && n == chunk, why);
        got += n;
    }
    require(fs_close(h) == FS_OK, why);
    *len_out = st.size;
    return img;
}
static void drop_image(cpu_u8 *img, const char *why)
{
    require(heap_free(img) == HEAP_OK, why);
}

/* Spawn with kernel-memory argv; owner kernel; prints handle evidence. */
static cpu_u64 do_spawn(const cpu_u8 *img, cpu_u64 len, const cpu_u64 *ptrs,
                        const cpu_u64 *lens, cpu_u64 nargs, unsigned int stdin_sel,
                        int owner, const char *why)
{
    cpu_u64 h = 0;
    int rc = proc_spawn_image(img, len, ptrs, lens, nargs, stdin_sel, owner, &h);
    require(rc == SYS_OK, why);
    field("[PROC] spawn slot=", (cpu_u32)(h & 0xFFFFFFFFULL));
    field(" gen=", h >> 32);
    text("\r\n");
    return h;
}
/* Poll to a terminal state (cooperative yields); consumes on first
   terminal observation and asserts state+code. */
static void wait_consume(cpu_u64 h, int owner, unsigned int want_state, cpu_u64 want_code,
                         const char *why)
{
    struct proc_status st;
    for (unsigned int i = 0; i < 1000000u; ++i) {
        int rc = proc_wait(h, &st, owner, -1);
        require(rc == SYS_OK, why);
        if (st.state == PROC_RUNNING) {
            require(thread_yield(), why);
            continue;
        }
        require(st.state == want_state, why);
        field("[PROC] wait state=", st.state);
        field(" code=", st.code);
        text("\r\n");
        if (want_state == PROC_EXITED || want_state == PROC_FAULTED)
            require(st.code == (cpu_u32)want_code, why);
        return;
    }
    fail(why);
}
/* Poll until RUNNING is observed (child admitted and alive). */
static void wait_running(cpu_u64 h, int owner, const char *why)
{
    struct proc_status st;
    for (unsigned int i = 0; i < 1000000u; ++i) {
        require(proc_wait(h, &st, owner, -1) == SYS_OK, why);
        if (st.state == PROC_RUNNING) return;
        if (!thread_yield()) fail(why);
    }
    fail(why);
}

/* Mount the /t/ test image (probe devs for the marker path). Returns
   nonzero with the filesystem mounted, zero with nothing printed... the
   caller prints the skip marker to keep the transcript explicit. */
static int mount_tests(void)
{
    static const char *marker = "/t/exit42.rnx";
    for (cpu_u32 id = 0; id < 4u; ++id) {
        cpu_u32 h = 0;
        if (!blk_device(id)) continue;
        if (fs_mount(id) != FS_OK) continue;
        if (fs_open(marker, &h) == FS_OK) {
            require(fs_close(h) == FS_OK, "probe-close");
            return 1;
        }
        fs_unmount();
    }
    return 0;
}

/* P0: ABI pins + fresh-table invariants. */
static void phase_pins(void)
{
    struct accounting before = account();
    require(PROC_MAX == 3, "pins_procs");
    require(USER_MAX_CODE_PAGES == 8 && USER_MAX_DATA_PAGES == 4, "pins_pages");
    require(UAPI_MAX_ARGC == 8 && UAPI_MAX_ARGV_BYTES == 256, "pins_argv");
    require(RNYX_V2_CODE_MAX == 32768u && RNYX_V2_DATA_MAX == 16384u, "pins_rynx");
    require(proc_check(), "pins_check");
    for (unsigned int i = 0; i < PROC_MAX; ++i)
        require(proc_gen(i) == 1, "pins_gen");
    balanced(before);
    text("[PROC] abi pins ok\r\n");
}

/* P1a: self-terminate arrangement (FIRST spawn: slot 0, gen 1). The
   child terminates its own exact handle, expects INVAL, and must still
   exit normally afterwards. */
static void phase_selfterm(const cpu_u8 *self_img, cpu_u64 self_len)
{
    struct accounting before = account();
    struct proc_status dummy;
    cpu_u64 h = do_spawn(self_img, self_len, 0, 0, 0, STDIN_KBD,
                         PROC_OWNER_KERNEL, "self_spawn");
    require(h == ((cpu_u64)1 << 32), "self_handle");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "self_wait");
    require(proc_wait(h, &dummy, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE, "self_double");
    balanced(before);
    text("[PROC] selfterm ok\r\n");
}

/* P1b: table/generation lifecycle. */
static void phase_table(const cpu_u8 *img, cpu_u64 len)
{
    struct accounting before = account();
    struct proc_status st;
    cpu_u64 h1, h2;
    h1 = do_spawn(img, len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "tab_spawn1");
    wait_consume(h1, PROC_OWNER_KERNEL, PROC_EXITED, 42, "tab_consume1");
    h2 = do_spawn(img, len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "tab_spawn2");
    require((h1 & 0xFFFFFFFFULL) == (h2 & 0xFFFFFFFFULL), "tab_slot_reuse");
    require((h2 >> 32) == (h1 >> 32) + 1, "tab_gen_bump");
    require(proc_wait(h1, &st, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE, "tab_stale");
    require(proc_wait(9 | ((cpu_u64)1 << 32), &st, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE,
            "tab_badslot");
    require(proc_wait((h2 & 0xFFFFFFFFULL) | ((cpu_u64)0xFFFF << 32), &st,
                      PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE, "tab_badgen");
    require(proc_wait(h2 & 0xFFFFFFFFULL, &st, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE,
            "tab_gen0");
    wait_consume(h2, PROC_OWNER_KERNEL, PROC_EXITED, 42, "tab_consume2");
    balanced(before);
    text("[PROC] table matrix ok\r\n");
}

/* P2: ten sequential spawn/run/wait/reap cycles. */
static void phase_seq(const cpu_u8 *img, cpu_u64 len)
{
    struct accounting before = account();
    cpu_u64 last_gen = 0;
    for (unsigned int i = 0; i < 10; ++i) {
        cpu_u64 h = do_spawn(img, len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "seq_spawn");
        if (i > 0) require((h >> 32) > last_gen, "seq_gen");
        last_gen = h >> 32;
        wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 42, "seq_wait");
        require(proc_check(), "seq_check");
    }
    balanced(before);
    text("[PROC] sequential ok\r\n");
}

/* P3: table full (3 spinners) + 4th BUSY.
 *
 * NOTE on NOMEM: forcing PMM exhaustion would require holding ~16K
 * frame addresses (128 KiB), which fits neither the bounded kernel BSS
 * window nor the 64 KiB heap arena. The NOMEM rollback path is therefore
 * covered structurally (identical destroy+free shape to proven paths)
 * plus mutant C-M4, which forces the create-failure branch and proves
 * the slot is recycled. Table exhaustion (BUSY) is covered live below.
 */
static void phase_full(const cpu_u8 *spin_img, cpu_u64 spin_len,
                       const cpu_u8 *exit_img, cpu_u64 exit_len)
{
    struct accounting before = account();
    cpu_u64 h[3];
    for (unsigned int i = 0; i < 3; ++i)
        h[i] = do_spawn(spin_img, spin_len, 0, 0, 0, STDIN_KBD,
                        PROC_OWNER_KERNEL, "full_spawn");
    for (unsigned int i = 0; i < 3; ++i)
        wait_running(h[i], PROC_OWNER_KERNEL, "full_running");
    {
        cpu_u64 extra = 0;
        require(proc_spawn_image(exit_img, exit_len, 0, 0, 0, STDIN_KBD,
                                 PROC_OWNER_KERNEL, &extra) == SYS_BUSY, "full_busy");
    }
    require(proc_check(), "full_check");
    for (unsigned int i = 0; i < 3; ++i) {
        require(proc_terminate(h[i], PROC_OWNER_KERNEL, -1) == SYS_OK, "full_term");
        wait_consume(h[i], PROC_OWNER_KERNEL, PROC_ABORTED, 0, "full_wait");
    }
    balanced(before);
    text("[PROC] full matrix ok\r\n");
}

/* P4: wait semantics (RUNNING/consume-once/stale/hostile/owner). */
static void phase_wait(const cpu_u8 *exit_img, cpu_u64 exit_len,
                       const cpu_u8 *spin_img, cpu_u64 spin_len)
{
    struct accounting before = account();
    struct proc_status st;
    cpu_u64 h, owned;
    /* RUNNING never consumes: repeated polls all OK. */
    h = do_spawn(spin_img, spin_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "wait_spawn");
    for (unsigned int i = 0; i < 3; ++i) {
        require(proc_wait(h, &st, PROC_OWNER_KERNEL, -1) == SYS_OK, "wait_running_rc");
        require(st.state == PROC_RUNNING, "wait_running");
        require(thread_yield(), "wait_yield");
    }
    /* Hostile status pointer (NULL): BADARG-equivalent INVAL here (kernel
       pointer class), zombie intact, valid retry consumes. */
    require(proc_wait(h, 0, PROC_OWNER_KERNEL, -1) == SYS_INVAL, "wait_nullst");
    require(proc_terminate(h, PROC_OWNER_KERNEL, -1) == SYS_OK, "wait_term");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_ABORTED, 0, "wait_aborted");
    require(proc_wait(h, &st, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE, "wait_double");
    /* Non-owner: child owned by a live slot is invisible to others. */
    owned = do_spawn(exit_img, exit_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL,
                     "wait_owned");
    {
        cpu_u64 kid = 0;
        require(proc_spawn_image(exit_img, exit_len, 0, 0, 0, STDIN_KBD,
                                 (int)(owned & 0xFFFFFFFFULL), &kid) == SYS_OK,
                "wait_nest");
        require(proc_wait(kid, &st, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE, "wait_nonowner");
        require(proc_terminate(kid, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE, "term_nonowner");
        wait_consume(kid, (int)(owned & 0xFFFFFFFFULL), PROC_EXITED, 42, "wait_nest_run");
    }
    wait_consume(owned, PROC_OWNER_KERNEL, PROC_EXITED, 42, "wait_owned_run");
    balanced(before);
    text("[PROC] wait matrix ok\r\n");
}

/* P5: terminate races (live, natural-first, repeat, stale, non-owner). */
static void phase_term(const cpu_u8 *spin_img, cpu_u64 spin_len,
                       const cpu_u8 *exit_img, cpu_u64 exit_len)
{
    struct accounting before = account();
    cpu_u64 h;
    /* Live child -> ABORTED (Order A). */
    h = do_spawn(spin_img, spin_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "term_spawn");
    wait_running(h, PROC_OWNER_KERNEL, "term_running");
    require(proc_terminate(h, PROC_OWNER_KERNEL, -1) == SYS_OK, "term_req");
    require(proc_terminate(h, PROC_OWNER_KERNEL, -1) == SYS_OK, "term_idempotent");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_ABORTED, 0, "term_aborted");
    /* Natural exit first -> ALREADY_GONE (Order B), repeat stays GONE. */
    h = do_spawn(exit_img, exit_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "term_exit");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 42, "term_natural");
    require(proc_terminate(h, PROC_OWNER_KERNEL, -1) == SYS_BADHANDLE, "term_consumed");
    /* Natural exit first -> ALREADY_GONE (Order B). Staged without
       polling: one RUNNING observation, then yields let the worker
       complete with no wait consuming, so the proc is
       terminal-yet-unwaited when terminate lands. */
    h = do_spawn(exit_img, exit_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "term_exit2");
    wait_running(h, PROC_OWNER_KERNEL, "term_exit2_run");
    for (unsigned int i = 0; i < 100; ++i)
        require(thread_yield(), "term_exit2_yield");
    require(proc_terminate(h, PROC_OWNER_KERNEL, -1) == SYS_ALREADY_GONE, "term_gone");
    require(proc_terminate(h, PROC_OWNER_KERNEL, -1) == SYS_ALREADY_GONE, "term_gone2");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 42, "term_reap");
    require(proc_terminate(0xFFFFFFFFULL | ((cpu_u64)7 << 32), PROC_OWNER_KERNEL, -1) ==
            SYS_BADHANDLE, "term_stale");
    balanced(before);
    text("[PROC] terminate matrix ok\r\n");
}

/* P6: fault containment (#UD vector 6). */
static void phase_fault(const cpu_u8 *fault_img, cpu_u64 fault_len)
{
    struct accounting before = account();
    cpu_u64 h = do_spawn(fault_img, fault_len, 0, 0, 0, STDIN_KBD,
                         PROC_OWNER_KERNEL, "fault_spawn");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_FAULTED, 6, "fault_wait");
    balanced(before);
    text("[PROC] fault matrix ok\r\n");
}

/* P7: argv vectors (valid paths; goldens via child [LOAD] writes). */
static void phase_argv(const cpu_u8 *argv_img, cpu_u64 argv_len)
{
    struct accounting before = account();
    static const char a_hi[] = "hi";
    static const char a0[] = "a0";
    static const char a1[] = "a1";
    static const char a2[] = "a2";
    static const char a3[] = "a3";
    static const char a4[] = "a4";
    static const char a5[] = "a5";
    static const char a6[] = "a6";
    static const char a7[] = "a7";
    static const char a_q[] = "q";
    static char bigx[255], bigy[256];
    cpu_u64 h;
    for (unsigned int i = 0; i < sizeof(bigx) - 1; ++i) bigx[i] = 'X';
    bigx[sizeof(bigx) - 1] = 0;
    for (unsigned int i = 0; i < sizeof(bigy) - 1; ++i) bigy[i] = 'Y';
    bigy[sizeof(bigy) - 1] = 0;
    /* argc 0: no bytes, code 0. */
    h = do_spawn(argv_img, argv_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "argv0");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "argv0_wait");
    /* argc 1. */
    {
        cpu_u64 ptrs[1] = {(cpu_u64)a_hi};
        cpu_u64 lens[1] = {2};
        h = do_spawn(argv_img, argv_len, ptrs, lens, 1, STDIN_KBD,
                     PROC_OWNER_KERNEL, "argv1");
        wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 1, "argv1_wait");
    }
    /* argc 8. */
    {
        cpu_u64 ptrs[8] = {(cpu_u64)a0, (cpu_u64)a1, (cpu_u64)a2, (cpu_u64)a3,
                           (cpu_u64)a4, (cpu_u64)a5, (cpu_u64)a6, (cpu_u64)a7};
        cpu_u64 lens[8] = {2, 2, 2, 2, 2, 2, 2, 2};
        h = do_spawn(argv_img, argv_len, ptrs, lens, 8, STDIN_KBD,
                     PROC_OWNER_KERNEL, "argv8");
        wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 8, "argv8_wait");
    }
    /* total 255 and 256 (boundary accept: sum(len+1) exactly). */
    {
        cpu_u64 ptrs[1] = {(cpu_u64)bigx};
        cpu_u64 lens[1] = {254};
        h = do_spawn(argv_img, argv_len, ptrs, lens, 1, STDIN_KBD,
                     PROC_OWNER_KERNEL, "argv255");
        wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 1, "argv255_wait");
    }
    {
        cpu_u64 ptrs[1] = {(cpu_u64)bigy};
        cpu_u64 lens[1] = {255};
        h = do_spawn(argv_img, argv_len, ptrs, lens, 1, STDIN_KBD,
                     PROC_OWNER_KERNEL, "argv256");
        wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 1, "argv256_wait");
    }
    /* argv[0] verbatim: path + flag echo. */
    {
        static const char path[] = "/t/p_argv.rnx";
        cpu_u64 ptrs[2] = {(cpu_u64)path, (cpu_u64)a_q};
        cpu_u64 lens[2] = {13, 1};
        h = do_spawn(argv_img, argv_len, ptrs, lens, 2, STDIN_KBD,
                     PROC_OWNER_KERNEL, "argv0path");
        wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 2, "argv0path_wait");
    }
    balanced(before);
    text("[PROC] argv matrix ok\r\n");
}

/* P8: RYNX v1 compat + v2 boundaries (images from /t/). */
static void phase_rynx(void)
{
    struct accounting before = account();
    static const struct { const char *path; int ok; cpu_u64 code; } rows[] = {
        {"/t/v1exit.rnx", 1, 42},
        {"/t/v1badmagic.rnx", 0, 0},
        /* Version 2 with v1 sizes is a legitimate small v2 image (runs). */
        {"/t/v1badver.rnx", 1, 42},
        {"/t/v1badshape.rnx", 0, 0},
        {"/t/maxcode.rnx", 1, 42},
        {"/t/maxcode1.rnx", 0, 0},
        {"/t/maxdata.rnx", 1, 42},
        {"/t/maxdata1.rnx", 0, 0},
        {"/t/badv2ver.rnx", 0, 0},
        {"/t/badv2rsv.rnx", 0, 0},
        {"/t/badv2entry.rnx", 0, 0},
        {"/t/badv2code0.rnx", 0, 0},
        {"/t/badv2codeovf.rnx", 0, 0},
    };
    for (unsigned int i = 0; i < sizeof(rows) / sizeof(rows[0]); ++i) {
        cpu_u64 len = 0;
        cpu_u8 *img = load_image(rows[i].path, &len, "rynx_load");
        if (rows[i].ok) {
            cpu_u64 h = do_spawn(img, len, 0, 0, 0, STDIN_KBD,
                                 PROC_OWNER_KERNEL, "rynx_spawn");
            wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, rows[i].code, "rynx_wait");
        } else {
            cpu_u64 h = 0;
            require(proc_spawn_image(img, len, 0, 0, 0, STDIN_KBD,
                                     PROC_OWNER_KERNEL, &h) == SYS_MALFORMED,
                    "rynx_reject");
            text("[PROC] rynx reject ok\r\n");
        }
        drop_image(img, "rynx_drop");
        require(proc_check(), "rynx_check");
    }
    balanced(before);
    text("[PROC] rynx matrix ok\r\n");
}

/* P9: owner-exit cleanup (zombie reaped + live reparented/killed). */
static void phase_owner(const cpu_u8 *spin_img, cpu_u64 spin_len,
                        const cpu_u8 *exit_img, cpu_u64 exit_len)
{
    struct accounting before = account();
    struct proc_status st;
    cpu_u64 a, b;
    /* Owner A (spinner) with terminal zombie B: consuming A reaps B. */
    a = do_spawn(spin_img, spin_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "own_a");
    b = 0;
    require(proc_spawn_image(exit_img, exit_len, 0, 0, 0, STDIN_KBD,
                             (int)(a & 0xFFFFFFFFULL), &b) == SYS_OK, "own_b");
    wait_consume(b, (int)(a & 0xFFFFFFFFULL), PROC_EXITED, 42, "own_b_run");
    require(proc_terminate(a, PROC_OWNER_KERNEL, -1) == SYS_OK, "own_a_term");
    wait_consume(a, PROC_OWNER_KERNEL, PROC_ABORTED, 0, "own_a_wait");
    require(proc_wait(b, &st, (int)(a & 0xFFFFFFFFULL), -1) == SYS_BADHANDLE, "own_b_gone");
    /* Live owned child: reparented to kernel + killed on owner free. */
    a = do_spawn(spin_img, spin_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "own_c");
    b = 0;
    require(proc_spawn_image(spin_img, spin_len, 0, 0, 0, STDIN_KBD,
                             (int)(a & 0xFFFFFFFFULL), &b) == SYS_OK, "own_d");
    wait_running(b, (int)(a & 0xFFFFFFFFULL), "own_d_run");
    require(proc_terminate(a, PROC_OWNER_KERNEL, -1) == SYS_OK, "own_c_term");
    wait_consume(a, PROC_OWNER_KERNEL, PROC_ABORTED, 0, "own_c_wait");
    wait_consume(b, PROC_OWNER_KERNEL, PROC_ABORTED, 0, "own_d_wait");
    balanced(before);
    text("[PROC] owner matrix ok\r\n");
}

/* P10: guest syscall chains (nest/alias/regprobe run their own matrices
   in CPL3 and exit 0 only if every sub-check passes). */
static void phase_guests(const cpu_u8 *nest_img, cpu_u64 nest_len,
                         const cpu_u8 *alias_img, cpu_u64 alias_len,
                         const cpu_u8 *reg_img, cpu_u64 reg_len,
                         const cpu_u8 *eof_img, cpu_u64 eof_len)
{
    struct accounting before = account();
    cpu_u64 h;
    h = do_spawn(nest_img, nest_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "guest_nest");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "guest_nest_wait");
    h = do_spawn(alias_img, alias_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL,
                 "guest_alias");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "guest_alias_wait");
    h = do_spawn(reg_img, reg_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "guest_reg");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "guest_reg_wait");
    h = do_spawn(eof_img, eof_len, 0, 0, 0, STDIN_CLOSED, PROC_OWNER_KERNEL, "guest_eof");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "guest_eof_wait");
    balanced(before);
    text("[PROC] guest matrix ok\r\n");
}

/* P11: real v2 multi-page program (2 code + 2 data pages execute). */
static void phase_big(const cpu_u8 *big_img, cpu_u64 big_len)
{
    struct accounting before = account();
    struct rnyx_layout lay;
    cpu_u64 h;
    require(rnyx_validate(big_img, big_len, &lay) == RNYX_OK, "big_valid");
    require(lay.code_len > 4096 && lay.data_memsz > 4096, "big_multi");
    h = do_spawn(big_img, big_len, 0, 0, 0, STDIN_KBD, PROC_OWNER_KERNEL, "big_spawn");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "big_wait");
    balanced(before);
    text("[PROC] big matrix ok\r\n");
}

void proc_self_test(void)
{
    cpu_u64 exit_len = 0, argv_len = 0, fault_len = 0, spin_len = 0, big_len = 0;
    cpu_u64 nest_len = 0, alias_len = 0, reg_len = 0, eof_len = 0, self_len = 0;
    cpu_u8 *exit_img = 0, *argv_img = 0, *fault_img = 0, *spin_img = 0, *big_img = 0;
    cpu_u8 *nest_img = 0, *alias_img = 0, *reg_img = 0, *eof_img = 0, *self_img = 0;
    text("[PROC] self-test started\r\n");
    require(proc_initialize(), "proc_init");
    if (!mount_tests()) {
        text("[PROC] no image, skipped\r\n");
        (void)serial_flush();
        return;
    }
    require(irq_set_enabled(0, 0), "quiesce");
    phase_pins();
    /* Load every image once up front (heap-staged, dropped at the end). */
    self_img = load_image("/t/p_selfterm.rnx", &self_len, "img_self");
    exit_img = load_image("/t/exit42.rnx", &exit_len, "img_exit");
    argv_img = load_image("/t/p_argv.rnx", &argv_len, "img_argv");
    fault_img = load_image("/t/p_fault.rnx", &fault_len, "img_fault");
    spin_img = load_image("/t/p_spin.rnx", &spin_len, "img_spin");
    big_img = load_image("/t/p_big.rnx", &big_len, "img_big");
    nest_img = load_image("/t/p_nest.rnx", &nest_len, "img_nest");
    alias_img = load_image("/t/p_alias.rnx", &alias_len, "img_alias");
    reg_img = load_image("/t/p_regprobe.rnx", &reg_len, "img_reg");
    eof_img = load_image("/t/p_eof.rnx", &eof_len, "img_eof");
    phase_selfterm(self_img, self_len);
    phase_table(exit_img, exit_len);
    phase_seq(exit_img, exit_len);
    phase_full(spin_img, spin_len, exit_img, exit_len);
    phase_wait(exit_img, exit_len, spin_img, spin_len);
    phase_term(spin_img, spin_len, exit_img, exit_len);
    phase_fault(fault_img, fault_len);
    phase_argv(argv_img, argv_len);
    phase_rynx();
    phase_owner(spin_img, spin_len, exit_img, exit_len);
    phase_guests(nest_img, nest_len, alias_img, alias_len, reg_img, reg_len,
                 eof_img, eof_len);
    phase_big(big_img, big_len);
    drop_image(self_img, "drop");
    drop_image(exit_img, "drop");
    drop_image(argv_img, "drop");
    drop_image(fault_img, "drop");
    drop_image(spin_img, "drop");
    drop_image(big_img, "drop");
    drop_image(nest_img, "drop");
    drop_image(alias_img, "drop");
    drop_image(reg_img, "drop");
    drop_image(eof_img, "drop");
    fs_unmount();
    {
        struct accounting before = account();
        balanced(before);
    }
    /* NOTE: no [TEST] line here (unlike load/rt/user sections): the proc
       run must stay a contiguous [PROC]/[LOAD]-write run so the host
       splitter can separate it from the rt section. */
    text("[PROC] proc verified\r\n");
    (void)serial_flush();
}
