#include "pipetest.h"
#include "pipe.h"
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

/* Stage 18d Slice D gated self-test: stateless fread, final discovery,
 * kernel-owned pipes, atomic spawn_pipe, and true concurrent streaming
 * proof. Runs after proc_self_test when RYNOR_PIPE_TEST=1; silent
 * otherwise. Images and data come from the Slice D filesystem image
 * (/bin/ programs, /f/ data). The timer stays masked except for the
 * tick-observability phase, so every count below is deterministic;
 * the tick phase asserts advancement (never exact counts).
 *
 * Transcript tags: [FREAD] for the files/discovery phases (ending in
 * "[FREAD] fread verified"), [PIPE] for the pipe phases (ending in
 * "[PIPE] pipe verified"). The host validator splits the two runs.
 */

static void ffail(const char *why) __attribute__((noreturn));
static void ffail(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[FREAD] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}
static void pfail(const char *why) __attribute__((noreturn));
static void pfail(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[PIPE] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}
static void frequire(int ok, const char *why) { if (!ok) ffail(why); }
static void prequire(int ok, const char *why) { if (!ok) pfail(why); }
static void ftext(const char *s) { frequire(serial_write(s), "serial"); }
static void ptext(const char *s) { prequire(serial_write(s), "serial"); }
static void fnumber(cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    ftext(b + i);
}
static void pnumber(cpu_u64 n)
{
    char b[21]; unsigned int i = 20; b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    ptext(b + i);
}
static void ffield(const char *s, cpu_u64 n) { ftext(s); fnumber(n); }
static void pfield(const char *s, cpu_u64 n) { ptext(s); pnumber(n); }

struct accounting { struct pmm_statistics pmm; struct heap_statistics heap; cpu_u64 tables; };
static struct accounting account(void)
{
    struct accounting a;
    if (pmm_statistics(&a.pmm) != PMM_OK || heap_statistics(&a.heap) != HEAP_OK)
        ffail("statistics");
    a.tables = vm_kernel_space()->table_pages;
    return a;
}
/* Extended balance: Slice C resources plus zero live pipes. */
static void fbalanced(struct accounting before, const char *tag)
{
    struct accounting after = account();
    frequire(after.pmm.allocated_bytes == before.pmm.allocated_bytes &&
             after.pmm.free_bytes == before.pmm.free_bytes && after.tables == before.tables &&
             after.heap.used_bytes == before.heap.used_bytes &&
             after.heap.free_blocks == before.heap.free_blocks && pmm_check() &&
             vm_check(vm_kernel_space()) && heap_check() && user_check() &&
             proc_check() && pipe_check() && !pipe_allocated(), tag);
    ftext("[FREAD] accounting balanced\r\n");
}
static void pbalanced(struct accounting before, const char *tag)
{
    struct accounting after = account();
    prequire(after.pmm.allocated_bytes == before.pmm.allocated_bytes &&
             after.pmm.free_bytes == before.pmm.free_bytes && after.tables == before.tables &&
             after.heap.used_bytes == before.heap.used_bytes &&
             after.heap.free_blocks == before.heap.free_blocks && pmm_check() &&
             vm_check(vm_kernel_space()) && heap_check() && user_check() &&
             proc_check() && pipe_check() && !pipe_allocated(), tag);
    ptext("[PIPE] accounting balanced\r\n");
}

/* Shared 16 KiB test staging (defined in load-test.c; drivers run
   sequentially and always fill before reading). */
extern cpu_u8 load_file_buf[16384u];

/* Image heap staging (transient per driver run; balanced). */
static cpu_u8 *load_image(const char *path, cpu_u64 *len_out, const char *why)
{
    struct fs_stat st;
    cpu_u32 h = 0;
    cpu_u8 *img = 0;
    cpu_u64 got = 0;
    if (fs_stat(path, &st) != FS_OK) pfail(why);
    if (st.type != FS_TYPE_FILE) pfail(why);
    if (st.size < RNYX_HEADER_LEN || st.size > 28u + 32768u + 16384u) pfail(why);
    if (fs_open(path, &h) != FS_OK) pfail(why);
    if (heap_alloc(st.size, 8, (void **)&img) != HEAP_OK) pfail(why);
    while (got < st.size) {
        cpu_u64 chunk = st.size - got > FS_MAX_READ_BYTES ? FS_MAX_READ_BYTES : st.size - got;
        cpu_u64 n = 0;
        if (fs_read(h, got, img + got, chunk, &n) != FS_OK || n != chunk) pfail(why);
        got += n;
    }
    if (fs_close(h) != FS_OK) pfail(why);
    *len_out = st.size;
    return img;
}

/* Mount the Slice D test image (probe devs for the marker path). */
static int mount_tests(void)
{
    static const char *marker = "/t/exit42.rnx";
    for (cpu_u32 id = 0; id < 4u; ++id) {
        cpu_u32 h = 0;
        if (!blk_device(id)) continue;
        if (fs_mount(id) != FS_OK) continue;
        if (fs_open(marker, &h) == FS_OK) {
            if (fs_close(h) != FS_OK) pfail("probe-close");
            return 1;
        }
        fs_unmount();
    }
    return 0;
}

/* Poll one handle to a terminal state; consumes on first terminal
   observation and asserts state+code. */
static void wait_consume(cpu_u64 h, int owner, unsigned int want_state, cpu_u64 want_code,
                         const char *why)
{
    struct proc_status st;
    for (unsigned int i = 0; i < 1000000u; ++i) {
        int rc = proc_wait(h, &st, owner, -1);
        if (rc != SYS_OK) pfail(why);
        if (st.state == PROC_RUNNING) {
            if (!thread_yield()) pfail(why);
            continue;
        }
        if (st.state != want_state) pfail(why);
        pfield("[PIPE] wait state=", st.state);
        pfield(" code=", st.code);
        ptext("\r\n");
        if ((want_state == PROC_EXITED || want_state == PROC_FAULTED) &&
            st.code != (cpu_u32)want_code)
            pfail(why);
        return;
    }
    pfail(why);
}

/* Latching transfer wait: polls both pipe children, snapshots pipe
   counters every poll (keeping maxima: the pipe may free before the
   last poll), and records overlap (both RUNNING after the first byte
   moved). Asserts both terminal states+codes. */
struct latch {
    cpu_u64 full, empty, turns, written, read;
    int overlap, started;
};
static void wait_pipe(cpu_u64 ha, cpu_u64 hb, int owner,
                      unsigned int wa, cpu_u64 ca,
                      unsigned int wb, cpu_u64 cb,
                      struct latch *la, const char *why)
{
    struct proc_status sta, stb;
    int da = 0, db = 0;
    struct pipe_snapshot snap;
    la->full = 0;
    la->empty = 0;
    la->turns = 0;
    la->written = 0;
    la->read = 0;
    la->overlap = 0;
    la->started = 0;
    /* Bound: green transfers finish in dozens of polls; a wedge trips
       this in ~seconds (kept far below the boot deadline so deadlock
       mutants fail on the phase marker, not on timeout). */
    for (unsigned int i = 0; i < 200000u; ++i) {
        int runa = 0, runb = 0;
        if (!da) {
            if (proc_wait(ha, &sta, owner, -1) != SYS_OK) pfail(why);
            if (sta.state == PROC_RUNNING) runa = 1;
            else {
                if (sta.state != wa) pfail(why);
                if ((wa == PROC_EXITED || wa == PROC_FAULTED) &&
                    sta.code != (cpu_u32)ca)
                    pfail(why);
                da = 1;
            }
        }
        if (!db) {
            if (proc_wait(hb, &stb, owner, -1) != SYS_OK) pfail(why);
            if (stb.state == PROC_RUNNING) runb = 1;
            else {
                if (stb.state != wb) pfail(why);
                if ((wb == PROC_EXITED || wb == PROC_FAULTED) &&
                    stb.code != (cpu_u32)cb)
                    pfail(why);
                db = 1;
            }
        }
        pipe_snapshot(&snap);
        if (snap.full_stalls > la->full) la->full = snap.full_stalls;
        if (snap.empty_stalls > la->empty) la->empty = snap.empty_stalls;
        if (snap.turns > la->turns) la->turns = snap.turns;
        if (snap.total_written > la->written) la->written = snap.total_written;
        if (snap.total_read > la->read) la->read = snap.total_read;
        if (la->written > 0) la->started = 1;
        if (la->started && runa && runb) la->overlap = 1;
        if (!pipe_check()) pfail(why);
        if (da && db) return;
        if (!thread_yield()) pfail(why);
    }
    pfail(why);
}

/* Kernel-argv spawn_pipe (driver): resolved absolute paths + kernel
   strings (up to three producer args for the yield-first flag, two
   consumer args). */
static void do_spawn_pipe(const char *pa,
                          const char *aa0, const char *aa1, const char *aa2,
                          unsigned int stdin_a,
                          const char *pb,
                          const char *ab0, const char *ab1, const char *ab2,
                          cpu_u64 *ha_out, cpu_u64 *hb_out, const char *why)
{
    cpu_u64 ptrs_a[3], lens_a[3], ptrs_b[3], lens_b[3];
    cpu_u64 na = 0, nb = 0;
    const char *avec[3], *bvec[3];
    int rc;
    avec[0] = aa0;
    avec[1] = aa1;
    avec[2] = aa2;
    for (cpu_u64 i = 0; i < 3; ++i) {
        if (!avec[i]) break;
        ptrs_a[i] = (cpu_u64)avec[i];
        lens_a[i] = 0;
        while (avec[i][lens_a[i]]) ++lens_a[i];
        na = i + 1;
    }
    bvec[0] = ab0;
    bvec[1] = ab1;
    bvec[2] = ab2;
    for (cpu_u64 j = 0; j < 3; ++j) {
        if (!bvec[j]) break;
        ptrs_b[j] = (cpu_u64)bvec[j];
        lens_b[j] = 0;
        while (bvec[j][lens_b[j]]) ++lens_b[j];
        nb = j + 1;
    }
    rc = proc_spawn_pipe(pa, na ? ptrs_a : 0, na ? lens_a : 0, na, stdin_a,
                         pb, nb ? ptrs_b : 0, nb ? lens_b : 0, nb,
                         PROC_OWNER_KERNEL, ha_out, hb_out);
    if (rc != SYS_OK) pfail(why);
    pfield("[PIPE] spawn_pipe a=", (cpu_u32)(*ha_out & 0xFFFFFFFFULL));
    pfield(" b=", (cpu_u32)(*hb_out & 0xFFFFFFFFULL));
    ptext("\r\n");
}

/* Quiet single spawn for probe programs (kernel image, absolute load
   path already resolved by the caller). Returns the handle. */
static cpu_u64 fspawn(const cpu_u8 *img, cpu_u64 len, const char *why)
{
    cpu_u64 h = 0;
    int rc = proc_spawn_image(img, len, 0, 0, 0, STDIN_KBD,
                              PROC_OWNER_KERNEL, &h);
    if (rc != SYS_OK) ffail(why);
    ffield("[FREAD] spawn slot=", (cpu_u32)(h & 0xFFFFFFFFULL));
    ffield(" gen=", h >> 32);
    ftext("\r\n");
    return h;
}

/* Poll one handle to a terminal state with an [FREAD]-tagged wait row. */
static void fwait_consume(cpu_u64 h, int owner, unsigned int want_state,
                          cpu_u64 want_code, const char *why)
{
    struct proc_status st;
    for (unsigned int i = 0; i < 1000000u; ++i) {
        int rc = proc_wait(h, &st, owner, -1);
        if (rc != SYS_OK) ffail(why);
        if (st.state == PROC_RUNNING) {
            if (!thread_yield()) ffail(why);
            continue;
        }
        if (st.state != want_state) ffail(why);
        ffield("[FREAD] wait state=", st.state);
        ffield(" code=", st.code);
        ftext("\r\n");
        if ((want_state == PROC_EXITED || want_state == PROC_FAULTED) &&
            st.code != (cpu_u32)want_code)
            ffail(why);
        return;
    }
    ffail(why);
}

static int streq(const char *a, const char *b)
{
    while (*a && *b && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

static int memeq(const cpu_u8 *a, const cpu_u8 *b, cpu_u64 n)
{
    for (cpu_u64 i = 0; i < n; ++i)
        if (a[i] != b[i]) return 0;
    return 1;
}

/* D-F0: ABI pins. */
static void phase_fread_pins(void)
{
    struct accounting before = account();
    frequire(UAPI_PIPE_BUF == 4096u, "pins_pipebuf");
    frequire(UAPI_FREAD_MAX == 16384u, "pins_freadmax");
    frequire(UAPI_MAX_BIN_NAME == 27u, "pins_binname");
    frequire(UAPI_PIPE_MAX == 1u, "pins_pipemax");
    frequire(PIPE_CAP == 4096u, "pins_pipecap");
    frequire(FS_MAX_PATH == 32u, "pins_path");
    frequire(pipe_check(), "pins_check");
    frequire(!pipe_allocated(), "pins_free");
    fbalanced(before, "pins_balance");
    ftext("[FREAD] abi pins ok\r\n");
}

/* D-F1: basic reads (self-consistent chunking; host pins exact bytes
   from the probe echo). */
static void phase_fread_basic(void)
{
    struct accounting before = account();
    cpu_u64 n = 0, m = 0, size = 0, k;
    frequire(kern_fread("/f/hello.txt", 0, load_file_buf, 4096, &n) == SYS_OK,
             "hello_full");
    frequire(n > 0 && n <= 4096, "hello_size");
    size = n;
    /* Odd chunks must tile the full buffer exactly. */
    frequire(kern_fread("/f/hello.txt", 0, load_file_buf + 4096, 7, &m) == SYS_OK,
             "hello_c0");
    frequire(m == (size < 7 ? size : 7) && memeq(load_file_buf, load_file_buf + 4096, m),
             "hello_c0_eq");
    if (size > 7) {
        frequire(kern_fread("/f/hello.txt", 7, load_file_buf + 4096, 100, &m) == SYS_OK,
                 "hello_c1");
        frequire(m == (size - 7 < 100 ? size - 7 : 100) &&
                 memeq(load_file_buf + 7, load_file_buf + 4096, m), "hello_c1_eq");
    }
    /* Last byte, exact EOF, and zero length. */
    frequire(kern_fread("/f/hello.txt", size - 1, load_file_buf + 4096, 8, &m) == SYS_OK,
             "hello_last");
    frequire(m == 1 && load_file_buf[4096] == load_file_buf[size - 1], "hello_last_eq");
    frequire(kern_fread("/f/hello.txt", size, load_file_buf + 4096, 8, &m) == SYS_OK,
             "hello_eof");
    frequire(m == 0, "hello_eof_zero");
    frequire(kern_fread("/f/hello.txt", 0, load_file_buf + 4096, 0, &m) == SYS_OK,
             "hello_zero");
    frequire(m == 0, "hello_zero_n");
    /* Empty file: exact EOF. */
    frequire(kern_fread("/f/empty.txt", 0, load_file_buf + 4096, 16, &m) == SYS_OK,
             "empty_eof");
    frequire(m == 0, "empty_zero");
    /* Middle of the big pattern file, verified against the formula. */
    frequire(kern_fread("/f/big.bin", 5000, load_file_buf + 8192, 4096, &m) == SYS_OK,
             "big_mid");
    frequire(m == 4096, "big_mid_n");
    for (k = 0; k < m; ++k)
        if (load_file_buf[8192 + k] != (cpu_u8)(((5000u + k) * 13u + 0x41u) & 0xffu))
            ffail("big_mid_data");
    fbalanced(before, "basic_balance");
    ftext("[FREAD] basic ok\r\n");
}

/* D-F2: bounds (max chunk, max+1, offset edges, 64-bit wrap). */
static void phase_fread_bounds(void)
{
    struct accounting before = account();
    struct fs_stat st;
    cpu_u64 m = 0, k;
    frequire(fs_stat("/f/big.bin", &st) == FS_OK && st.type == FS_TYPE_FILE,
             "big_stat");
    frequire(st.size == 20000, "big_size");
    /* Max allowed chunk succeeds whole. */
    frequire(kern_fread("/f/big.bin", 0, load_file_buf, 16384, &m) == SYS_OK,
             "max_ok");
    frequire(m == 16384, "max_n");
    for (k = 0; k < m; ++k)
        if (load_file_buf[k] != (cpu_u8)((k * 13u + 0x41u) & 0xffu))
            ffail("max_data");
    /* Max+1 rejects; offset edges behave. */
    frequire(kern_fread("/f/big.bin", 0, load_file_buf, 16385, &m) == SYS_INVAL,
             "max1_reject");
    frequire(kern_fread("/f/big.bin", st.size - 1, load_file_buf, 16, &m) == SYS_OK &&
             m == 1, "off_last");
    frequire(kern_fread("/f/big.bin", st.size, load_file_buf, 16, &m) == SYS_OK &&
             m == 0, "off_end");
    frequire(kern_fread("/f/big.bin", st.size + 1, load_file_buf, 16, &m) == SYS_BADARG,
             "off_past");
    frequire(kern_fread("/f/big.bin", (cpu_u64)-1, load_file_buf, 16, &m) == SYS_BADARG,
             "off_wrap");
    frequire(kern_fread("/f/big.bin", (cpu_u64)1 << 32, load_file_buf, 16, &m) ==
             SYS_BADARG, "off_high");
    fbalanced(before, "bounds_balance");
    ftext("[FREAD] bounds ok\r\n");
}

/* D-F5: discovery unit matrix + failure-class distinction. */
static void phase_discovery(void)
{
    struct accounting before = account();
    char out[33];
    char name27[28], name28[29];
    cpu_u64 m = 0;
    unsigned int i;
    for (i = 0; i < 27; ++i) name27[i] = 'q';
    name27[27] = 0;
    for (i = 0; i < 28; ++i) name28[i] = 'q';
    name28[28] = 0;
    /* Absolute passes through verbatim. */
    {
        const char *abs = "/t/exit42.rnx";
        cpu_u64 alen = 0;
        while (abs[alen]) ++alen;
        frequire(resolve_exec_path(abs, alen, out) == SYS_OK, "d_abs");
        frequire(streq(out, "/t/exit42.rnx"), "d_abs_eq");
    }
    /* Bare resolves under /bin/. */
    frequire(resolve_exec_path("ok.rnx", 6, out) == SYS_OK, "d_bare");
    frequire(streq(out, "/bin/ok.rnx"), "d_bare_eq");
    /* Boundary pair: 27 resolves (5+27=32), 28 rejects untruncated. */
    frequire(resolve_exec_path(name27, 27, out) == SYS_OK, "d_27");
    {
        char want[33];
        unsigned int j;
        cpu_u64 L = 0;
        want[0] = '/';
        want[1] = 'b';
        want[2] = 'i';
        want[3] = 'n';
        want[4] = '/';
        for (j = 0; j < 27; ++j) want[5 + j] = 'q';
        want[32] = 0;
        frequire(streq(out, want), "d_27_eq");
        while (out[L]) ++L;
        frequire(L == 32, "d_27_len");
    }
    frequire(resolve_exec_path(name28, 28, out) == SYS_BADARG, "d_28");
    /* Shapes: empty, overlong, traversal, doubled slash, trailing
       slash, dot names. */
    frequire(resolve_exec_path("", 0, out) == SYS_BADARG, "d_empty");
    frequire(resolve_exec_path("/t/exit42.rnx", 33, out) == SYS_BADARG, "d_long");
    frequire(resolve_exec_path("a/../b", 6, out) == SYS_BADARG, "d_dotdot");
    frequire(resolve_exec_path("a//b", 4, out) == SYS_BADARG, "d_dbl");
    frequire(resolve_exec_path("ab/", 3, out) == SYS_BADARG, "d_trail");
    frequire(resolve_exec_path(".", 1, out) == SYS_BADARG, "d_dot");
    frequire(resolve_exec_path("..", 2, out) == SYS_BADARG, "d_dot2");
    /* Interior NUL shortens (validated == used, never rescanned). */
    {
        char staged[8] = {'o', 'k', 0, 'z', 'z', 0, 0, 0};
        frequire(resolve_exec_path(staged, 5, out) == SYS_OK, "d_nul");
        frequire(streq(out, "/bin/ok"), "d_nul_eq");
    }
    /* Failure classes at the read layer: missing vs directory stay
       distinct, and non-executable bytes are plain data (no RYNX
       check in the read path). */
    frequire(kern_fread("/f/nope.txt", 0, load_file_buf, 16, &m) == SYS_NOTFOUND,
             "d_notfound");
    frequire(kern_fread("/f", 0, load_file_buf, 16, &m) == SYS_MALFORMED,
             "d_malformed");
    frequire(kern_fread("/f/bad.rnx", 0, load_file_buf, 64, &m) == SYS_OK && m > 0,
             "d_data");
    fbalanced(before, "discovery_balance");
    ftext("[FREAD] discovery matrix ok\r\n");
}

/* Loaded Slice D probe images (file-static for the PIPE phases). */
static const cpu_u8 *g_pipeprobe, *g_discprobe, *g_spin;
static cpu_u64 g_pipeprobe_len, g_discprobe_len, g_spin_len;

static void transfer_row(const char *name, cpu_u64 bytes, struct latch *la)
{
    ptext("[PIPE] transfer ");
    ptext(name);
    pfield(" bytes=", bytes);
    pfield(" turns=", la->turns);
    pfield(" full=", la->full);
    pfield(" empty=", la->empty);
    pfield(" overlap=", (cpu_u64)la->overlap);
    ptext("\r\n");
}

/* D-P0: pipe invariants + admission shape. Workers have not run yet at
   snapshot time (no schedule point since admission), so count==0 and
   both endpoints open are exact, not racy. */
static void phase_pipe_pins(void)
{
    struct accounting before = account();
    prequire(UAPI_PIPE_BUF == 4096u, "pins_pipebuf");
    prequire(UAPI_FREAD_MAX == 16384u, "pins_freadmax");
    prequire(UAPI_PIPE_MAX == 1u, "pins_pipemax");
    prequire(PIPE_CAP == 4096u, "pins_pipecap");
    prequire(pipe_check(), "pins_check");
    prequire(!pipe_allocated(), "pins_free");
    /* FREAD phases reaped everything: bootstrap holds the only thread. */
    prequire(thread_free_count() == 7, "pins_threads");
    pbalanced(before, "pins_balance");
    ptext("[PIPE] abi pins ok\r\n");
}

static void phase_invariants(void)
{
    struct accounting before = account();
    struct pipe_snapshot snap;
    cpu_u64 ha = 0, hb = 0;
    cpu_u64 gen_a, gen_b;
    unsigned int sin_a, sout_a, sin_b, sout_b;
    int live_a, live_b;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "256", "0", 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "256", "0", 0, &ha, &hb, "inv_spawn");
    prequire(pipe_allocated(), "inv_live");
    pipe_snapshot(&snap);
    prequire(snap.allocated && snap.count == 0, "inv_zero");
    prequire(snap.reader_open && snap.writer_open, "inv_open");
    prequire(snap.reader_slot == (hb & 0xFFFFFFFFULL) &&
             snap.writer_slot == (ha & 0xFFFFFFFFULL), "inv_owners");
    prequire(snap.writer_slot == (ha & 0xFFFFFFFFULL) &&
             proc_gen(snap.writer_slot) == (ha >> 32), "inv_wgen");
    prequire(proc_slot_info((unsigned int)(ha & 0xFFFFFFFFULL), &gen_a,
                            &sin_a, &sout_a, &live_a) && live_a &&
             sout_a == STDOUT_PIPE, "inv_role_a");
    prequire(proc_slot_info((unsigned int)(hb & 0xFFFFFFFFULL), &gen_b,
                            &sin_b, &sout_b, &live_b) && live_b &&
             sin_b == STDIN_PIPE, "inv_role_b");
    prequire(gen_a == (ha >> 32) && gen_b == (hb >> 32), "inv_gens");
    prequire(pipe_check(), "inv_check");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "inv_wait");
    prequire(!pipe_allocated(), "inv_free");
    prequire(pipe_check(), "inv_check2");
    pbalanced(before, "inv_balance");
    ptext("[PIPE] pipe invariants ok\r\n");
}

/* D-P1: small stream, exact bytes. */
static void phase_small(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "256", "0", 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "256", "0", 0, &ha, &hb, "small_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "small_wait");
    prequire(la.written == 256 && la.read == 256, "small_bytes");
    transfer_row("small", 256, &la);
    pbalanced(before, "small_balance");
    ptext("[PIPE] small stream ok\r\n");
}

/* D-P2: wraparound (8192 bytes through a 4096 ring = exactly 2 write
   turns + 2 read turns from a fresh pipe, regardless of chunking). */
static void phase_wrap(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "8192", "1", 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "8192", "1", 0, &ha, &hb, "wrap_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "wrap_wait");
    prequire(la.written == 8192 && la.read == 8192, "wrap_bytes");
    prequire(la.turns == 4, "wrap_turns");
    prequire(la.overlap, "wrap_overlap");
    transfer_row("wrap", 8192, &la);
    pbalanced(before, "wrap_balance");
    ptext("[PIPE] wraparound ok\r\n");
}

/* D-P3a: producer-side backpressure. The gate return path
   round-robins, so unthrottled peers lockstep and never fill the pipe;
   a throttled consumer (two yields per read) lets the producer outrun
   it deterministically: with 8192 bytes through 4096, the producer
   MUST observe a full pipe. */
static void phase_stream_full(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "8192", "2", 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "8192", "2", "t", &ha, &hb, "sf_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "sf_wait");
    prequire(la.written == 8192 && la.read == 8192, "sf_bytes");
    prequire(la.full >= 1, "sf_full");
    prequire(la.overlap, "sf_overlap");
    transfer_row("stream_full", 8192, &la);
    pbalanced(before, "sf_balance");
    ptext("[PIPE] oversized full ok\r\n");
}

/* D-P3b: consumer-side backpressure (mirror image: a throttled
   producer lets the fast consumer drain onto the AGAIN path). */
static void phase_stream_empty(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "8192", "8", "t", STDIN_CLOSED,
                  "/bin/p_cons.rnx", "8192", "8", 0, &ha, &hb, "se_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "se_wait");
    prequire(la.written == 8192 && la.read == 8192, "se_bytes");
    prequire(la.empty >= 1, "se_empty");
    prequire(la.overlap, "se_overlap");
    transfer_row("stream_empty", 8192, &la);
    pbalanced(before, "se_balance");
    ptext("[PIPE] oversized empty ok\r\n");
}

/* D-P4a: empty-live (yield-first producer forces the consumer onto the
   AGAIN path whichever worker runs first). */
static void phase_emptylive(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "2048", "3", "y", STDIN_CLOSED,
                  "/bin/p_cons.rnx", "2048", "3", 0, &ha, &hb, "el_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "el_wait");
    prequire(la.written == 2048 && la.read == 2048, "el_bytes");
    prequire(la.empty >= 1, "el_empty");
    transfer_row("emptylive", 2048, &la);
    pbalanced(before, "el_balance");
    ptext("[PIPE] empty-live ok\r\n");
}

/* D-P4b: terminal EOF on an empty pipe (zero-length producer). */
static void phase_eof(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "0", "0", 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "0", "0", 0, &ha, &hb, "eof_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "eof_wait");
    prequire(la.written == 0 && la.read == 0, "eof_zero");
    transfer_row("eof", 0, &la);
    pbalanced(before, "eof_balance");
    ptext("[PIPE] terminal eof ok\r\n");
}

/* D-M8 gate: the consumer reaches EOF (and exits) before anyone waits
   on the producer. A writer-close-at-reap implementation would wedge
   here and trip the poll bound. */
static void phase_eofwait(void)
{
    struct accounting before = account();
    struct proc_status st;
    cpu_u64 ha = 0, hb = 0;
    unsigned int i;
    do_spawn_pipe("/bin/p_prod.rnx", "512", "4", 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "512", "4", 0, &ha, &hb, "ew_spawn");
    /* Poll the consumer to its terminal observation (which consumes
       it): EOF must already be visible, with the producer unwaited. */
    for (i = 0; i < 200000u; ++i) {
        if (proc_wait(hb, &st, PROC_OWNER_KERNEL, -1) != SYS_OK) pfail("ew_poll");
        if (st.state == PROC_RUNNING) {
            if (!thread_yield()) pfail("ew_yield");
            continue;
        }
        break;
    }
    prequire(st.state == PROC_EXITED && st.code == 42, "ew_cons");
    ptext("[PIPE] consumer exited before producer wait\r\n");
    wait_consume(ha, PROC_OWNER_KERNEL, PROC_EXITED, 42, "ew_prod");
    prequire(!pipe_allocated(), "ew_free");
    pbalanced(before, "ew_balance");
    ptext("[PIPE] eof-before-wait ok\r\n");
}

/* Hostile-output loss check (D-M9): an RX pipe read fails closed and
   consumes nothing; the stream that follows is offset-exact. */
static void phase_rxread(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    do_spawn_pipe("/bin/p_prod.rnx", "512", "0", 0, STDIN_CLOSED,
                  "/bin/p_rxread.rnx", "512", "0", 0, &ha, &hb, "rx_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
              &la, "rx_wait");
    prequire(la.written == 512 && la.read == 512, "rx_bytes");
    transfer_row("rxread", 512, &la);
    pbalanced(before, "rx_balance");
    ptext("[PIPE] hostile read ok\r\n");
}

/* In-guest syscall matrices (spawned absolute from /bin/). */
static void phase_pipeprobe(void)
{
    struct accounting before = account();
    cpu_u64 h = 0;
    int rc = proc_spawn_image(g_pipeprobe, g_pipeprobe_len, 0, 0, 0,
                              STDIN_KBD, PROC_OWNER_KERNEL, &h);
    if (rc != SYS_OK) pfail("pp_spawn");
    wait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "pp_wait");
    pbalanced(before, "pp_balance");
    ptext("[PIPE] syscall probe ok\r\n");
}

static void phase_discprobe(void)
{
    struct accounting before = account();
    cpu_u64 h = 0;
    int rc = proc_spawn_image(g_discprobe, g_discprobe_len, 0, 0, 0,
                              STDIN_KBD, PROC_OWNER_KERNEL, &h);
    if (rc != SYS_OK) ffail("dp_spawn");
    fwait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "dp_wait");
    fbalanced(before, "dp_balance");
    ftext("[FREAD] discovery probe ok\r\n");
}

/* Live-slot census + generation snapshot for rollback proofs. */
static unsigned int live_slots(void)
{
    unsigned int n = 0;
    for (unsigned int i = 0; i < PROC_MAX; ++i) {
        cpu_u64 gen = 0;
        unsigned int si = 0, so = 0;
        int live = 0;
        if (!proc_slot_info(i, &gen, &si, &so, &live)) pfail("census");
        if (live) ++n;
    }
    return n;
}

/* D-P5: transaction rollback. Every pre-commit failure must leave zero
   visible children, zero pipes, unchanged generations, and balanced
   resources. */
static void phase_rollback(void)
{
    struct accounting before = account();
    cpu_u64 g0, g1, g2, ha = 0, hb = 0;
    unsigned int i;
    int rc;
    g0 = proc_gen(0);
    g1 = proc_gen(1);
    g2 = proc_gen(2);
    prequire(live_slots() == 0, "rb_quiet");
    /* A valid + B malformed image. */
    rc = proc_spawn_pipe("/bin/p_prod.rnx", 0, 0, 0, STDIN_CLOSED,
                         "/bin/bad.rnx", 0, 0, 0,
                         PROC_OWNER_KERNEL, &ha, &hb);
    prequire(rc == SYS_MALFORMED, "rb_b_rc");
    /* A malformed image + B valid. */
    rc = proc_spawn_pipe("/bin/bad.rnx", 0, 0, 0, STDIN_CLOSED,
                         "/bin/p_cons.rnx", 0, 0, 0,
                         PROC_OWNER_KERNEL, &ha, &hb);
    prequire(rc == SYS_MALFORMED, "rb_a_rc");
    /* A valid + B missing. */
    rc = proc_spawn_pipe("/bin/p_prod.rnx", 0, 0, 0, STDIN_CLOSED,
                         "/bin/nope.rnx", 0, 0, 0,
                         PROC_OWNER_KERNEL, &ha, &hb);
    prequire(rc == SYS_NOTFOUND, "rb_miss_rc");
    /* Over-argved A (prepare-side budget failure after reservation). */
    {
        cpu_u64 ptrs[2] = {0, 0}, lens[2] = {200, 200};
        static cpu_u8 argfill[400];
        for (i = 0; i < sizeof(argfill); ++i) argfill[i] = (cpu_u8)('a' + (i % 26));
        ptrs[0] = (cpu_u64)argfill;
        ptrs[1] = (cpu_u64)(argfill + 200);
        rc = proc_spawn_pipe("/bin/p_prod.rnx", ptrs, lens, 2, STDIN_CLOSED,
                             "/bin/p_cons.rnx", 0, 0, 0,
                             PROC_OWNER_KERNEL, &ha, &hb);
        prequire(rc == SYS_NOMEM, "rb_argv_rc");
    }
    /* B-side argv budget failure (second-half prepare rollback:
       fetch succeeds, admission must still be zero). */
    {
        cpu_u64 ptrs[2] = {0, 0}, lens[2] = {200, 200};
        static cpu_u8 argfill_b[400];
        for (i = 0; i < sizeof(argfill_b); ++i) argfill_b[i] = (cpu_u8)('A' + (i % 26));
        ptrs[0] = (cpu_u64)argfill_b;
        ptrs[1] = (cpu_u64)(argfill_b + 200);
        rc = proc_spawn_pipe("/bin/p_prod.rnx", 0, 0, 0, STDIN_CLOSED,
                             "/bin/p_cons.rnx", ptrs, lens, 2,
                             PROC_OWNER_KERNEL, &ha, &hb);
        prequire(rc == SYS_NOMEM, "rb_bargv_rc");
    }
    /* Ninth argument: shape failure before any reservation. */
    rc = proc_spawn_pipe("/bin/p_prod.rnx", 0, 0, 9, STDIN_CLOSED,
                         "/bin/p_cons.rnx", 0, 0, 0,
                         PROC_OWNER_KERNEL, &ha, &hb);
    prequire(rc == SYS_BADARG, "rb_nargs_rc");
    prequire(live_slots() == 0, "rb_nochild");
    prequire(!pipe_allocated(), "rb_nopipe");
    prequire(proc_gen(0) == g0 && proc_gen(1) == g1 && proc_gen(2) == g2,
             "rb_gens");
    prequire(proc_check() && pipe_check(), "rb_checks");
    ptext("[PIPE] rollback core ok\r\n");
    /* Table-full: two spinners leave one slot; the pair needs two. */
    {
        cpu_u64 s0 = 0, s1 = 0;
        int r0 = proc_spawn_image(g_spin, g_spin_len, 0, 0, 0, STDIN_KBD,
                                  PROC_OWNER_KERNEL, &s0);
        int r1 = proc_spawn_image(g_spin, g_spin_len, 0, 0, 0, STDIN_KBD,
                                  PROC_OWNER_KERNEL, &s1);
        struct proc_status st;
        prequire(r0 == SYS_OK && r1 == SYS_OK, "rb_fill");
        prequire(live_slots() == 2, "rb_full");
        rc = proc_spawn_pipe("/bin/p_prod.rnx", 0, 0, 0, STDIN_CLOSED,
                             "/bin/p_cons.rnx", 0, 0, 0,
                             PROC_OWNER_KERNEL, &ha, &hb);
        prequire(rc == SYS_BUSY, "rb_busy_rc");
        prequire(live_slots() == 2, "rb_busy_live");
        prequire(!pipe_allocated(), "rb_busy_pipe");
        prequire(proc_wait(s0, &st, PROC_OWNER_KERNEL, -1) == SYS_OK &&
                 st.state == PROC_RUNNING, "rb_run0");
        prequire(proc_terminate(s0, PROC_OWNER_KERNEL, -1) == SYS_OK, "rb_t0");
        prequire(proc_terminate(s1, PROC_OWNER_KERNEL, -1) == SYS_OK, "rb_t1");
        wait_consume(s0, PROC_OWNER_KERNEL, PROC_ABORTED, 0, "rb_w0");
        wait_consume(s1, PROC_OWNER_KERNEL, PROC_ABORTED, 0, "rb_w1");
        prequire(live_slots() == 0, "rb_drained");
    }
    ptext("[PIPE] rollback table ok\r\n");
    /* Pipe-busy: a held pipeline blocks a second admission. */
    {
        cpu_u64 pa = 0, pb = 0;
        struct latch la;
        do_spawn_pipe("/bin/p_prod.rnx", "256", "0", 0, STDIN_CLOSED,
                      "/bin/p_cons.rnx", "256", "0", 0, &pa, &pb, "rb_hold");
        rc = proc_spawn_pipe("/bin/p_prod.rnx", 0, 0, 0, STDIN_CLOSED,
                             "/bin/p_cons.rnx", 0, 0, 0,
                             PROC_OWNER_KERNEL, &ha, &hb);
        prequire(rc == SYS_BUSY, "rb_pbusy_rc");
        wait_pipe(pa, pb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
                  &la, "rb_hold_wait");
        prequire(!pipe_allocated(), "rb_hold_free");
    }
    ptext("[PIPE] rollback pipe-busy ok\r\n");
    /* Table-full and pipe-busy phases above legitimately reap (their
       generations advance by design); the zero-side-effect proof for
       pure failures is rb_nochild/rb_nopipe/rb_gens/rb_checks. */
    (void)g0;
    (void)g1;
    (void)g2;
    pbalanced(before, "rb_balance");
    ptext("[PIPE] rollback matrix ok\r\n");
}

/* Thread-fill dummy (returns at once; the trampoline exits it). */
static void dummy_worker(void *arg) { (void)arg; }

/* Thread exhaustion: no worker slots, zero admission. */
static void phase_threadbusy(void)
{
    struct accounting before = account();
    thread_id ids[7];
    cpu_u64 ha = 0, hb = 0;
    unsigned int i;
    int rc;
    prequire(thread_free_count() == 7, "tb_free7");
    for (i = 0; i < 7; ++i)
        prequire(thread_create(&ids[i], dummy_worker, 0), "tb_fill");
    prequire(thread_free_count() == 0, "tb_free0");
    rc = proc_spawn_pipe("/bin/p_prod.rnx", 0, 0, 0, STDIN_CLOSED,
                         "/bin/p_cons.rnx", 0, 0, 0,
                         PROC_OWNER_KERNEL, &ha, &hb);
    prequire(rc == SYS_NOMEM, "tb_rc");
    prequire(live_slots() == 0, "tb_nochild");
    prequire(!pipe_allocated(), "tb_nopipe");
    for (i = 0; i < 50; ++i)
        if (!thread_yield()) pfail("tb_yield");
    for (i = 0; i < 7; ++i)
        prequire(thread_join(ids[i]), "tb_join");
    prequire(thread_free_count() == 7, "tb_free7b");
    prequire(proc_check(), "tb_check");
    pbalanced(before, "tb_balance");
    ptext("[PIPE] thread exhaustion ok\r\n");
}

/* D-P6: termination and fault interaction (no wedge, exact closes). */
static void phase_interact(void)
{
    struct accounting before = account();
    cpu_u64 ha = 0, hb = 0;
    struct latch la;
    struct pipe_snapshot snap;
    unsigned int i;
    /* Producer aborted (spinner producer never emits): the consumer
       drains nothing, sees EOF, and exits short. */
    do_spawn_pipe("/t/p_spin.rnx", 0, 0, 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "4096", "5", 0, &ha, &hb, "ia_spawn");
    for (i = 0; i < 1000000u; ++i) {
        pipe_snapshot(&snap);
        if (snap.empty_stalls >= 1) break;
        if (!thread_yield()) pfail("ia_yield");
    }
    prequire(snap.empty_stalls >= 1, "ia_empty");
    prequire(proc_terminate(ha, PROC_OWNER_KERNEL, -1) == SYS_OK, "ia_term");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_ABORTED, 0, PROC_EXITED, 11,
              &la, "ia_wait");
    prequire(!pipe_allocated(), "ia_free");
    ptext("[PIPE] producer abort ok\r\n");
    /* Consumer aborted (spinner consumer never drains): the producer
       must observe the broken reader and exit clean, never spin. */
    do_spawn_pipe("/bin/p_prod.rnx", "8192", "6", 0, STDIN_CLOSED,
                  "/t/p_spin.rnx", 0, 0, 0, &ha, &hb, "ib_spawn");
    for (i = 0; i < 1000000u; ++i) {
        pipe_snapshot(&snap);
        if (snap.full_stalls >= 1) break;
        if (!thread_yield()) pfail("ib_yield");
    }
    prequire(snap.full_stalls >= 1, "ib_full");
    prequire(proc_terminate(hb, PROC_OWNER_KERNEL, -1) == SYS_OK, "ib_term");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 77, PROC_ABORTED, 0,
              &la, "ib_wait");
    prequire(!pipe_allocated(), "ib_free");
    ptext("[PIPE] consumer abort ok\r\n");
    /* Both aborted. */
    do_spawn_pipe("/t/p_spin.rnx", 0, 0, 0, STDIN_CLOSED,
                  "/t/p_spin.rnx", 0, 0, 0, &ha, &hb, "ic_spawn");
    prequire(proc_terminate(ha, PROC_OWNER_KERNEL, -1) == SYS_OK, "ic_ta");
    prequire(proc_terminate(hb, PROC_OWNER_KERNEL, -1) == SYS_OK, "ic_tb");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_ABORTED, 0, PROC_ABORTED, 0,
              &la, "ic_wait");
    prequire(!pipe_allocated(), "ic_free");
    ptext("[PIPE] double abort ok\r\n");
    /* Producer faults after a prefix: buffered bytes drain, then EOF. */
    do_spawn_pipe("/bin/p_fprod.rnx", 0, 0, 0, STDIN_CLOSED,
                  "/bin/p_cons.rnx", "4096", "0", 0, &ha, &hb, "id_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_FAULTED, 6, PROC_EXITED, 11,
              &la, "id_wait");
    prequire(la.written == 1000 && la.read == 1000, "id_bytes");
    prequire(!pipe_allocated(), "id_free");
    ptext("[PIPE] producer fault ok\r\n");
    /* Consumer faults mid-stream: the producer sees -1 and exits 77. */
    do_spawn_pipe("/bin/p_prod.rnx", "8192", "0", 0, STDIN_CLOSED,
                  "/bin/p_fcons.rnx", 0, 0, 0, &ha, &hb, "ie_spawn");
    wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 77, PROC_FAULTED, 6,
              &la, "ie_wait");
    prequire(!pipe_allocated(), "ie_free");
    ptext("[PIPE] consumer fault ok\r\n");
    pbalanced(before, "ia_balance");
    ptext("[PIPE] interaction matrix ok\r\n");
}

/* D-P7: ten sequential pipelines with rotating seeds (stale bytes
   would fail the in-guest pattern check). */
static void phase_reuse(void)
{
    struct accounting before = account();
    static const char *seeds[10] = {"10", "11", "12", "13", "14",
                                    "15", "16", "17", "18", "19"};
    unsigned int i;
    for (i = 0; i < 10; ++i) {
        struct accounting it = account();
        cpu_u64 ha = 0, hb = 0;
        struct latch la;
        do_spawn_pipe("/bin/p_prod.rnx", "8192", seeds[i], 0, STDIN_CLOSED,
                      "/bin/p_cons.rnx", "8192", seeds[i], 0, &ha, &hb, "re_spawn");
        wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
                  &la, "re_wait");
        prequire(la.written == 8192 && la.read == 8192, "re_bytes");
        prequire(!pipe_allocated(), "re_free");
        prequire(proc_check() && pipe_check(), "re_checks");
        pbalanced(it, "re_balance");
        pfield("[PIPE] reuse iter=", i);
        ptext("\r\n");
    }
    pbalanced(before, "re_balance_all");
    ptext("[PIPE] reuse ok\r\n");
}

/* Scheduler observability: with the timer on, a bounded series of
   pipelines must advance the CPL3 tick count (asserted as advancement,
   never exact counts) while every transfer stays byte-exact. */
static void phase_ticks(void)
{
    struct accounting before = account();
    struct sched_statistics s0, s1;
    cpu_u64 t0, t1, iters = 0;
    prequire(scheduler_statistics(&s0) == 1, "tk_stat0");
    prequire(irq_set_enabled(0, 1), "tk_on");
    t0 = user_cpl3_ticks();
    while (iters < 60) {
        cpu_u64 ha = 0, hb = 0;
        struct latch la;
        do_spawn_pipe("/bin/p_prod.rnx", "8192", "7", 0, STDIN_CLOSED,
                      "/bin/p_cons.rnx", "8192", "7", 0, &ha, &hb, "tk_spawn");
        wait_pipe(ha, hb, PROC_OWNER_KERNEL, PROC_EXITED, 42, PROC_EXITED, 42,
                  &la, "tk_wait");
        ++iters;
        if (user_cpl3_ticks() != t0) break;
    }
    t1 = user_cpl3_ticks();
    prequire(irq_set_enabled(0, 0), "tk_off");
    prequire(t1 > t0, "tk_advance");
    prequire(scheduler_statistics(&s1) == 1, "tk_stat1");
    prequire(s1.switches > s0.switches, "tk_switches");
    pfield("[PIPE] ticks iters=", iters);
    pfield(" cpl3_ticks=", t1 - t0);
    pfield(" switches=", s1.switches - s0.switches);
    ptext("\r\n");
    pbalanced(before, "tk_balance");
    ptext("[PIPE] tick observability ok\r\n");
}

void pipe_self_test(void)
{
    cpu_u64 pp_len = 0, dp_len = 0, spin_len = 0, fread_len = 0;
    cpu_u8 *pp_img = 0, *dp_img = 0, *spin_img = 0, *fread_img = 0;
    if (!mount_tests()) {
        ptext("[PIPE] no image, skipped\r\n");
        (void)serial_flush();
        return;
    }
    /* Quiesce the timer for deterministic (cooperative-only) phases;
       the tick-observability phase re-enables it explicitly. */
    prequire(irq_set_enabled(0, 0), "quiesce");
    ftext("[FREAD] self-test started\r\n");
    phase_fread_pins();
    phase_fread_basic();
    phase_fread_bounds();
    fread_img = load_image("/bin/p_freadprobe.rnx", &fread_len, "img_freadprobe");
    {
        cpu_u64 h = fspawn(fread_img, fread_len, "freadprobe_spawn");
        fwait_consume(h, PROC_OWNER_KERNEL, PROC_EXITED, 0, "freadprobe_wait");
        ftext("[FREAD] probe ok\r\n");
    }
    dp_img = load_image("/bin/p_discprobe.rnx", &dp_len, "img_discprobe");
    g_discprobe = dp_img;
    g_discprobe_len = dp_len;
    phase_discprobe();
    phase_discovery();
    ftext("[FREAD] fread verified\r\n");
    (void)serial_flush();
    pp_img = load_image("/bin/p_pipeprobe.rnx", &pp_len, "img_pipeprobe");
    spin_img = load_image("/t/p_spin.rnx", &spin_len, "img_spin");
    g_pipeprobe = pp_img;
    g_pipeprobe_len = pp_len;
    g_spin = spin_img;
    g_spin_len = spin_len;
    ptext("[PIPE] self-test started\r\n");
    /* Self-sufficient quiesce (the proc test normally masks first, but
       pipe-only images must not depend on that ordering). */
    prequire(irq_set_enabled(0, 0), "quiesce");
    phase_pipe_pins();
    phase_invariants();
    phase_small();
    phase_wrap();
    phase_stream_full();
    phase_stream_empty();
    phase_emptylive();
    phase_eof();
    phase_eofwait();
    phase_rxread();
    phase_pipeprobe();
    phase_rollback();
    phase_threadbusy();
    phase_interact();
    phase_reuse();
    phase_ticks();
    if (heap_free(fread_img) != HEAP_OK) pfail("drop");
    if (heap_free(dp_img) != HEAP_OK) pfail("drop");
    if (heap_free(pp_img) != HEAP_OK) pfail("drop");
    if (heap_free(spin_img) != HEAP_OK) pfail("drop");
    fs_unmount();
    {
        struct accounting before = account();
        pbalanced(before, "final_balance");
    }
    ptext("[PIPE] pipe verified\r\n");
    (void)serial_flush();
}



