#include "readtest.h"
#include "user.h"
#include "load.h"
#include "uapi.h"
#include "syscall.h"
#include "ksched.h"
#include "irq.h"
#include "kbd.h"
#include "../drivers/keyboard-internal.h"
#include "pmm.h"
#include "heap.h"
#include "vm.h"
#include "serial.h"

/* Stage 18d Slices A/B gated self-test: CPL3 IRQ1 park path, keyboard
   scan staging with Ctrl/Pause/LOST discipline, syscall 3 read matrix,
   copy_to_user matrix, and six-register probe evidence. Runs after
   rt_self_test (new transcript terminator when enabled); with
   RYNOR_INPUT_TEST=0 it is never called. Host keys arrive through
   [INPUT] markers (a third injection stream beside [KBD]/[SHELL]);
   Pause bytes have no sendkey name and are covered by local decoder
   feeds instead. */

extern const char readargs_probe[], readargs_probe_end[];
extern const char yieldspin_probe[], yieldspin_probe_end[];

static void fail(const char *why) __attribute__((noreturn));
static void fail(const char *why)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[INPUT] failure=");
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
static void hexbytes(const cpu_u8 *p, cpu_u64 n)
{
    static const char digits[] = "0123456789abcdef";
    for (cpu_u64 i = 0; i < n; ++i) {
        char pair[3];
        pair[0] = digits[(p[i] >> 4) & 15u];
        pair[1] = digits[p[i] & 15u];
        pair[2] = 0;
        text(pair);
    }
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
    text("[INPUT] accounting balanced\r\n");
}

/* Host key request stream: one make+break pair per marker. */
static unsigned int next_key;
static void marker(void)
{
    text("[INPUT] waiting for input=");
    number(next_key++);
    text("\r\n");
    (void)serial_flush();
}
static cpu_u64 stream_total(void)
{
    struct kbd_statistics st;
    require(kbd_statistics(&st), "stats");
    return st.received + st.dropped;
}
/* Bounded wait for N more ISR bytes. Each iteration pulses STI so a
   pending IRQ1 actually delivers (the driver runs IF=0); the loop exits
   with IF=0 restored. A dead host fails closed on the iteration bound. */
static void wait_stream(cpu_u64 n, const char *why)
{
    cpu_u64 target = stream_total() + n;
    for (unsigned int i = 0; i < 100000000u; ++i) {
        irq_restore(0x200);
        if (stream_total() >= target) {
            irq_restore(0);
            return;
        }
        irq_restore(0);
    }
    fail(why);
}
static void set_irq(unsigned int irq, int on, const char *why)
{
    require(irq_set_enabled(irq, on), why);
}

/* Data-page vector layout for readargs_probe (all u64). */
#define V_FD 0xa0u
#define V_BUF 0xa8u
#define V_LEN 0xb0u
#define V_NREAD 0xb8u
#define V_FLAGS 0xc0u
#define V_RBP 0xc8u
#define V_RC 0xd0u
#define GUEST_BUF 0x200u
#define GUEST_NREAD 0x300u

static volatile cpu_u8 *data_window(struct user_context *c)
{
    volatile cpu_u8 *w = vm_frame_access(c->data_frame[0]);
    require(w != 0, "data_window");
    return w;
}
static void store64(volatile cpu_u8 *w, cpu_u64 off, cpu_u64 v)
{
    for (unsigned int i = 0; i < 8; ++i)
        w[off + i] = (cpu_u8)(v >> (i * 8));
}
static cpu_u64 load64(volatile cpu_u8 *w, cpu_u64 off)
{
    cpu_u64 v = 0;
    for (unsigned int i = 0; i < 8; ++i)
        v |= (cpu_u64)w[off + i] << (i * 8);
    return v;
}

static struct user_context *make_probe(const cpu_u8 *code, cpu_u64 len, const char *why)
{
    struct user_context *c = 0;
    require(len > 0 && len <= VM_PAGE_SIZE, "probe_size");
    require(user_create_loaded(&c, (const char *)code, len, "", 0, 0) && c, why);
    return c;
}
/* Enter and run to EXITED (IRQ masked in matrix phases, so only the
   gate exit terminates; PREEMPTED/READ resume transparently). */
static void run_exit(struct user_context *c, const char *why)
{
    cpu_u64 rc = user_enter_image(&c->link);
    while (rc == USER_RUN_PREEMPTED || rc == USER_RUN_YIELDED ||
           rc == USER_RUN_WRITTEN || rc == USER_RUN_READ)
        rc = user_resume(&c->link);
    require(rc == USER_RUN_EXITED, why);
    require(c->state == USER_EXITED && c->exit_code == 7, why);
}
static void teardown(struct user_context *c, const char *why)
{
    require(thread_detach_user(), why);
    require(user_destroy(c), why);
}
/* Release a pristine context that was never entered (nothing bound). */
static void discard(struct user_context *c, const char *why)
{
    require(user_destroy(c), why);
}
/* Prefill one readargs vector in the live data window. */
static void set_vector(struct user_context *c, cpu_u64 fd, cpu_u64 buf, cpu_u64 len,
                       cpu_u64 nread, cpu_u64 flags, cpu_u64 rbp)
{
    volatile cpu_u8 *w = data_window(c);
    store64(w, V_FD, fd);
    store64(w, V_BUF, buf);
    store64(w, V_LEN, len);
    store64(w, V_NREAD, nread);
    store64(w, V_FLAGS, flags);
    store64(w, V_RBP, rbp);
    store64(w, V_RC, 0xAAAAAAAAAAAAAAAAULL);
}

/* P0: local decoder matrix (no IRQ): Ctrl/Pause/E0 discipline. */
static void decode_tests(void)
{
    struct kbd_decoder d = {0};
    struct kbd_event e;
    /* Left Ctrl make/break: single bytes, no prefix. */
    require(kbd_decode(&d, 0x1d, &e) && e.key == KBD_KEY_CTRL &&
            e.type == KBD_EVENT_PRESS && !e.extended, "dec_lmake");
    require(kbd_decode(&d, 0x9d, &e) && e.key == KBD_KEY_CTRL &&
            e.type == KBD_EVENT_RELEASE && !e.extended, "dec_lbreak");
    /* Right Ctrl: E0-prefixed pair promotes to key with extended set. */
    require(!kbd_decode(&d, 0xe0, &e) && e.type == KBD_EVENT_UNKNOWN, "dec_e0");
    require(kbd_decode(&d, 0x1d, &e) && e.key == KBD_KEY_CTRL &&
            e.type == KBD_EVENT_PRESS && e.extended, "dec_rmake");
    require(!kbd_decode(&d, 0xe0, &e), "dec_e0b");
    require(kbd_decode(&d, 0x9d, &e) && e.key == KBD_KEY_CTRL &&
            e.type == KBD_EVENT_RELEASE && e.extended, "dec_rbreak");
    /* Full Pause: six bytes, zero Ctrl events (the 0x1D inside must never
       alias Ctrl: pause state is checked before the 0x1D comparison). */
    {
        static const cpu_u8 pause[] = {0xe1, 0x1d, 0x45, 0xe1, 0x9d, 0xc5};
        for (unsigned int i = 0; i < sizeof(pause); ++i)
            require(!kbd_decode(&d, pause[i], &e) && e.type == KBD_EVENT_UNKNOWN &&
                    !e.key && !e.extended, "dec_pause");
    }
    /* Malformed Pause resets; stream resynchronizes. */
    require(!kbd_decode(&d, 0xe1, &e), "dec_e1");
    require(!kbd_decode(&d, 0x00, &e), "dec_pause_bad");
    require(kbd_decode(&d, 0x1e, &e) && e.key == 0x1e &&
            e.type == KBD_EVENT_PRESS && !e.extended, "dec_resync");
    /* E0 keypad Enter stays suppressed (never ordinary Enter). */
    require(!kbd_decode(&d, 0xe0, &e), "dec_e0c");
    require(!kbd_decode(&d, 0x1c, &e) && e.type == KBD_EVENT_UNKNOWN &&
            !e.key, "dec_kpenter");
    /* Right Ctrl still works after a completed Pause (no stuck state). */
    {
        static const cpu_u8 pause[] = {0xe1, 0x1d, 0x45, 0xe1, 0x9d, 0xc5};
        for (unsigned int i = 0; i < sizeof(pause); ++i)
            require(!kbd_decode(&d, pause[i], &e), "dec_pause2");
        require(!kbd_decode(&d, 0xe0, &e), "dec_e0d");
        require(kbd_decode(&d, 0x1d, &e) && e.key == KBD_KEY_CTRL && e.extended,
                "dec_rafter");
    }
    /* Ordinary + unknown behavior unchanged. */
    require(kbd_decode(&d, 0x30, &e) && e.key == 0x30 &&
            e.type == KBD_EVENT_PRESS && !e.extended, "dec_plain");
    require(!kbd_decode(&d, 0x2d, &e) && !e.key, "dec_unknown");
    text("[INPUT] decode matrix ok\r\n");
}

/* P1: copy_to_user matrix against a pristine context. */
static void copy_tests(void)
{
    static const cpu_u8 src[64] = {
        0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,
        0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
        0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80,
        0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0xf0, 0xe0, 0xd0, 0xc0, 0xb0, 0xa0, 0x90, 0x80,
        0x0f, 0x1f, 0x2f, 0x3f, 0x4f, 0x5f, 0x6f, 0x7f,
        0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11, 0x00,
        0xde, 0xad, 0xbe, 0xef, 0xca, 0xfe, 0xba, 0xbe
    };
    struct accounting before = account();
    struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
        (cpu_u64)(readargs_probe_end - readargs_probe), "copy_create");
    volatile cpu_u8 *w = data_window(c);
    unsigned int pass = 0, fail = 0;
    require(copy_to_user(c, USER_DATA_BASE, src, sizeof(src)) == sizeof(src), "copy_data");
    /* Re-acquire: copy_to_user repoints the shared frame window, so no
       window pointer survives a VM-touching call (load.c documents the
       same rule for its staging pointers). */
    w = data_window(c);
    for (unsigned int i = 0; i < sizeof(src); ++i)
        require(w[i] == src[i], "copy_data_bytes");
    ++pass;
    require(copy_to_user(c, USER_CODE_BASE, src, 8) == (cpu_u64)-1, "copy_code_rc");
    {
        volatile cpu_u8 *cw = vm_frame_access(c->code_frame[0]);
        require(cw != 0, "copy_code_window");
        for (unsigned int i = 0; i < 8; ++i)
            require(cw[i] == (cpu_u8)readargs_probe[i], "copy_code_intact");
    }
    ++fail;
    require(copy_to_user(c, USER_STACK_TOP - 8, src, 8) == 8, "copy_stack");
    ++pass;
    require(copy_to_user(c, USER_GUARD_PAGE, src, 8) == (cpu_u64)-1, "copy_guard");
    ++fail;
    require(copy_to_user(c, 0x8000, src, 8) == (cpu_u64)-1, "copy_kern");
    ++fail;
    require(copy_to_user(c, 0x500000, src, 8) == (cpu_u64)-1, "copy_hole");
    ++fail;
    require(copy_to_user(c, USER_DATA_BASE, src, VM_PAGE_SIZE + 8) == (cpu_u64)-1,
            "copy_cross");
    ++fail;
    require(copy_to_user(c, USER_STACK_TOP - 4, src, 8) == (cpu_u64)-1, "copy_spill");
    ++fail;
    require(copy_to_user(c, (cpu_u64)-8, src, 8) == (cpu_u64)-1, "copy_wrap");
    require(copy_to_user(c, USER_STACK_TOP - 8, src, (cpu_u64)-1) == (cpu_u64)-1,
            "copy_lenwrap");
    fail += 2;
    require(copy_to_user(c, USER_CODE_BASE, src, 0) == 0, "copy_zero");
    ++pass;
    discard(c, "copy_teardown");
    balanced(before);
    field("[INPUT] copy matrix pass=", pass);
    field(" fail=", fail);
    text("\r\n");
}

/* P2: read() matrix. Bytes are staged with markers; every rejected call
   must leave ring bytes and guest sentinels intact (verified by the next
   successful drain). Cumulative staged/consumed bytes reconcile at end. */
static cpu_u64 staged_bytes, consumed_bytes;
static void stage_keys(unsigned int nkeys, cpu_u64 nbytes, const char *why)
{
    /* Staging needs live IRQ1 delivery; blob runs below mask it again
       for determinism. */
    set_irq(1, 1, why);
    for (unsigned int i = 0; i < nkeys; ++i) marker();
    wait_stream(nbytes, why);
    set_irq(1, 0, why);
    staged_bytes += nbytes;
}
/* Drain exactly n bytes with one valid vector; assert rc/nread/payload. */
static void drain_expect(cpu_u64 n, const cpu_u8 *want, const char *why)
{
    struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
        (cpu_u64)(readargs_probe_end - readargs_probe), why);
    volatile cpu_u8 *w = data_window(c);
    for (unsigned int i = 0; i < 64; ++i) w[GUEST_BUF + i] = 0x5a;
    for (unsigned int i = 0; i < 8; ++i) w[GUEST_NREAD + i] = 0x5a;
    set_vector(c, 0, USER_DATA_BASE + GUEST_BUF, n,
               USER_DATA_BASE + GUEST_NREAD, 0, 0);
    run_exit(c, why);
    w = data_window(c);
    require(load64(w, V_RC) == SYS_OK, why);
    require(load64(w, GUEST_NREAD) == n, why);
    for (cpu_u64 i = 0; i < n; ++i)
        require(w[GUEST_BUF + i] == want[i], why);
    consumed_bytes += n;
    text("[INPUT] payload n=");
    number(n);
    text(" hex=");
    hexbytes((const cpu_u8 *)&w[GUEST_BUF], n);
    text("\r\n");
    teardown(c, why);
}
/* One rejected vector; guest + ring state must be provably intact. */
static void reject_vector(cpu_u64 fd, cpu_u64 buf, cpu_u64 len, cpu_u64 nread,
                          cpu_u64 flags, cpu_u64 rbp, const char *why)
{
    struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
        (cpu_u64)(readargs_probe_end - readargs_probe), why);
    volatile cpu_u8 *w = data_window(c);
    for (unsigned int i = 0; i < 64; ++i) w[GUEST_BUF + i] = 0x5a;
    for (unsigned int i = 0; i < 8; ++i) w[GUEST_NREAD + i] = 0x5a;
    set_vector(c, fd, buf, len, nread, flags, rbp);
    run_exit(c, why);
    w = data_window(c);
    require(load64(w, V_RC) == SYS_INVAL, why);
    for (unsigned int i = 0; i < 64; ++i)
        require(w[GUEST_BUF + i] == 0x5a, why);
    for (unsigned int i = 0; i < 8; ++i)
        require(w[GUEST_NREAD + i] == 0x5a, why);
    teardown(c, why);
}
static void read_tests(void)
{
    /* a/b makes+breaks as staged below. */
    static const cpu_u8 ab[4] = {0x1e, 0x9e, 0x30, 0xb0};
    static const cpu_u8 cd[4] = {0x2e, 0xae, 0x20, 0xa0};
    struct accounting before = account();
    /* Empty ring -> AGAIN, outputs untouched (sentinels intact). */
    {
        struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
            (cpu_u64)(readargs_probe_end - readargs_probe), "r_empty_create");
        volatile cpu_u8 *w = data_window(c);
        for (unsigned int i = 0; i < 8; ++i) w[GUEST_NREAD + i] = 0x5a;
        set_vector(c, 0, USER_DATA_BASE + GUEST_BUF, 4,
                   USER_DATA_BASE + GUEST_NREAD, 0, 0);
        run_exit(c, "r_empty_run");
        w = data_window(c);
        require(load64(w, V_RC) == SYS_AGAIN, "r_empty_rc");
        for (unsigned int i = 0; i < 8; ++i)
            require(w[GUEST_NREAD + i] == 0x5a, "r_empty_nread");
        teardown(c, "r_empty_teardown");
    }
    text("[INPUT] read empty-again ok\r\n");
    /* Exact 4-byte read of staged a/b. */
    stage_keys(2, 4, "r_stage_ab");
    drain_expect(4, ab, "r_exact");
    text("[INPUT] read exact ok\r\n");
    /* Zero-length with bytes pending: OK + nread 0, no consume. */
    stage_keys(2, 4, "r_stage_cd");
    {
        struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
            (cpu_u64)(readargs_probe_end - readargs_probe), "r_zero_create");
        volatile cpu_u8 *w = data_window(c);
        for (unsigned int i = 0; i < 8; ++i) w[GUEST_NREAD + i] = 0x5a;
        set_vector(c, 0, USER_DATA_BASE + GUEST_BUF, 0,
                   USER_DATA_BASE + GUEST_NREAD, 0, 0);
        run_exit(c, "r_zero_run");
        w = data_window(c);
        require(load64(w, V_RC) == SYS_OK, "r_zero_rc");
        require(load64(w, GUEST_NREAD) == 0, "r_zero_nread");
        teardown(c, "r_zero_teardown");
    }
    drain_expect(4, cd, "r_zero_preserved");
    text("[INPUT] read zero-length ok\r\n");
    /* Short read: len 4096 with 4 pending -> OK + nread 4. The buffer
       must actually map 4096 writable bytes, so this vector uses the
       whole stack page (a 64-byte scratch would rightly be INVAL). */
    stage_keys(2, 4, "r_stage_short");
    {
        struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
            (cpu_u64)(readargs_probe_end - readargs_probe), "r_short_create");
        volatile cpu_u8 *w = data_window(c);
        set_vector(c, 0, USER_STACK_PAGE, 4096,
                   USER_DATA_BASE + GUEST_NREAD, 0, 0);
        run_exit(c, "r_short_run");
        w = data_window(c);
        require(load64(w, V_RC) == SYS_OK, "r_short_rc");
        require(load64(w, GUEST_NREAD) == 4, "r_short_nread");
        {
            volatile cpu_u8 *sw = vm_frame_access(c->stack_frame);
            require(sw != 0, "r_short_window");
            for (unsigned int i = 0; i < 4; ++i)
                require(sw[i] == ab[i], "r_short_bytes");
            consumed_bytes += 4;
            text("[INPUT] payload n=4 hex=");
            hexbytes((const cpu_u8 *)&sw[0], 4);
            text("\r\n");
        }
        teardown(c, "r_short_teardown");
    }
    text("[INPUT] read short ok\r\n");
    /* Hostile scalars (ring state must survive each). */
    stage_keys(2, 4, "r_stage_hostile");
    reject_vector(1, USER_DATA_BASE + GUEST_BUF, 4,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_badfd");
    reject_vector(0x100000000ULL, USER_DATA_BASE + GUEST_BUF, 4,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_fd64");
    reject_vector(0, USER_DATA_BASE + GUEST_BUF, 4,
                  USER_DATA_BASE + GUEST_NREAD, 1, 0, "r_flags");
    reject_vector(0, USER_DATA_BASE + GUEST_BUF, 4,
                  USER_DATA_BASE + GUEST_NREAD, 0x100000000ULL, 0, "r_flags64");
    reject_vector(0, USER_DATA_BASE + GUEST_BUF, 4097,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_overlen");
    reject_vector(0, USER_DATA_BASE + GUEST_BUF, 4,
                  USER_DATA_BASE + GUEST_NREAD, 0, 1, "r_rbp");
    text("[INPUT] read hostile-scalars ok\r\n");
    /* Hostile pointers (bytes must survive each: preservation). */
    reject_vector(0, 0, 4, USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_nullbuf");
    reject_vector(0, USER_CODE_BASE, 4,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_rxbuf");
    reject_vector(0, 0x500000, 4,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_holebuf");
    reject_vector(0, (cpu_u64)-4, 8,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_wrapbuf");
    reject_vector(0, USER_STACK_TOP - 4, 8,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_spillbuf");
    reject_vector(0, 0x10000600200ULL, 4,
                  USER_DATA_BASE + GUEST_NREAD, 0, 0, "r_buf64");
    reject_vector(0, USER_DATA_BASE + GUEST_BUF, 4, USER_CODE_BASE, 0, 0,
                  "r_rxnread");
    reject_vector(0, USER_DATA_BASE + GUEST_BUF, 4, 0, 0, 0, "r_nullnread");
    text("[INPUT] read hostile-pointers ok\r\n");
    /* All 4 staged bytes survived every rejection: exact drain. */
    drain_expect(4, ab, "r_preserved");
    text("[INPUT] read preservation ok\r\n");
    require(staged_bytes == consumed_bytes, "r_reconcile");
    field("[INPUT] read matrix bytes=", staged_bytes);
    text("\r\n");
    balanced(before);
    text("[INPUT] read matrix ok\r\n");
}

/* P3: IRQ1 landing on CPL3. Burst windows release K host keys at once
   (canonical order fixed below; the host test passes the same tuple and
   the validator cross-checks names against guest-observed bytes). The
   blob spins under IRQ1; each window asserts park_count delta >= 1 with
   bounded retries, and every staged byte reconciles cumulatively. */
static void window_marker(unsigned int attempt, const char *names, unsigned int keys)
{
    text("[INPUT] window attempt=");
    number(attempt);
    text(" keys=");
    text(names);
    text("\r\n");
    (void)serial_flush();
    (void)keys;
}
/* Cumulative guest-observed byte log (all windows/phases). */
static cpu_u8 byte_log[128];
static cpu_u64 byte_log_n;
/* Drain up to max bytes (short OK); append exact payload to the log. */
static cpu_u64 drain_any(cpu_u64 max, const char *why)
{
    struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
        (cpu_u64)(readargs_probe_end - readargs_probe), why);
    volatile cpu_u8 *w = data_window(c);
    require(max <= 64, why);
    for (unsigned int i = 0; i < max; ++i) w[GUEST_BUF + i] = 0x5a;
    for (unsigned int i = 0; i < 8; ++i) w[GUEST_NREAD + i] = 0x5a;
    set_vector(c, 0, USER_DATA_BASE + GUEST_BUF, max,
               USER_DATA_BASE + GUEST_NREAD, 0, 0);
    run_exit(c, why);
    w = data_window(c);
    cpu_u64 rc = load64(w, V_RC);
    cpu_u64 n = load64(w, GUEST_NREAD);
    require(rc == SYS_OK && n <= max, why);
    for (cpu_u64 i = 0; i < n; ++i) {
        require(byte_log_n < sizeof(byte_log), why);
        byte_log[byte_log_n++] = w[GUEST_BUF + i];
    }
    consumed_bytes += n;
    text("[INPUT] payload n=");
    number(n);
    text(" hex=");
    hexbytes((const cpu_u8 *)&w[GUEST_BUF], n);
    text("\r\n");
    teardown(c, why);
    return n;
}
/* Canonical window shapes (guest-expected bytes; host names in parens):
   A: a,ctrl,b,ret  -> 1E 9E 1D 9D 30 B0 1C 9C (8 bytes)
   B: c,ctrl_r,d,ret -> 2E AE E0 1D E0 9D 20 A0 1C 9C (10 bytes) */
static const cpu_u8 shape_a[8] = {0x1e, 0x9e, 0x1d, 0x9d, 0x30, 0xb0, 0x1c, 0x9c};
static const cpu_u8 shape_b[10] = {0x2e, 0xae, 0xe0, 0x1d, 0xe0, 0x9d, 0x20, 0xa0, 0x1c, 0x9c};
static void park_window(const char *names, const cpu_u8 *want, cpu_u64 want_n,
                        unsigned int nkeys, int irq0, const char *why)
{
    cpu_u64 log0 = byte_log_n;
    for (unsigned int attempt = 0; attempt < 3; ++attempt) {
        cpu_u64 parks0 = sched_park_count();
        cpu_u64 stream0 = stream_total();
        window_marker(attempt, names, nkeys);
        staged_bytes += want_n;
        struct user_context *c = make_probe((const cpu_u8 *)yieldspin_probe,
            (cpu_u64)(yieldspin_probe_end - yieldspin_probe), why);
        if (irq0) set_irq(0, 1, why);
        else set_irq(0, 0, why);
        set_irq(1, 1, why);
        run_exit(c, why);
        /* Await this attempt's bytes BEFORE masking IRQ1: masking first
           would strand in-flight host keys and fail falsely on slow hosts.
           IRQ1 stays unmasked across this wait (delivery needs it). */
        {
            cpu_u64 target = stream0 + want_n;
            unsigned int spun = 0;
            while (stream_total() < target) {
                irq_restore(0x200);
                if (stream_total() >= target) break;
                irq_restore(0);
                if (++spun > 100000000u) fail(why);
            }
            irq_restore(0);
        }
        set_irq(1, 0, why);
        set_irq(0, 0, why);
        teardown(c, why);
        require(user_check(), why);
        (void)drain_any(40, why);
        if (sched_park_count() > parks0) {
            text("[INPUT] window parked attempt=");
            number(attempt);
            text("\r\n");
            break;
        }
        if (attempt == 2) fail(why);
    }
    /* This window's bytes arrived in FIFO order across its attempts. */
    require(byte_log_n - log0 >= want_n, why);
    for (cpu_u64 i = 0; i < want_n; ++i)
        require(byte_log[log0 + i] == want[i], why);
    field("[INPUT] window bytes=", want_n);
    text("\r\n");
}
static void park_tests(void)
{
    struct accounting before = account();
    cpu_u64 ticks0 = user_cpl3_ticks();
    park_window("a,ctrl,b,ret", shape_a, sizeof(shape_a), 4, 0, "park_a");
    park_window("c,ctrl_r,d,ret", shape_b, sizeof(shape_b), 4, 1, "park_b");
    require(user_cpl3_ticks() > ticks0, "park_ticks");
    balanced(before);
    text("[INPUT] park matrix ok\r\n");
}

/* P4: LOST burst. 34 indexed 'a' keys (68 bytes) without draining: the
   31-byte ring keeps the first 31, drops 37. First drain delivers one
   0x00 loss marker plus the retained bytes, in order; next read is AGAIN. */
static void lost_tests(void)
{
    struct accounting before = account();
    struct kbd_statistics pre, post;
    require(kbd_statistics(&pre), "lost_stats");
    set_irq(1, 1, "lost_unmask");
    for (unsigned int i = 0; i < 34; ++i) marker();
    wait_stream(68, "lost_burst");
    set_irq(1, 0, "lost_mask");
    staged_bytes += 68;
    require(kbd_statistics(&post), "lost_stats2");
    require(post.received - pre.received == 31, "lost_received");
    require(post.dropped - pre.dropped == 37, "lost_dropped");
    field("[INPUT] lost dropped=", post.dropped - pre.dropped);
    text("\r\n");
    /* Drop-newest keeps the OLDEST 31 bytes; draining them reaches the
       advanced epoch, so the same call appends the single 0x00 loss
       marker (honest order: retained bytes precede the loss). */
    {
        struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
            (cpu_u64)(readargs_probe_end - readargs_probe), "lost_drain");
        volatile cpu_u8 *w = data_window(c);
        set_vector(c, 0, USER_DATA_BASE + GUEST_BUF, 40,
                   USER_DATA_BASE + GUEST_NREAD, 0, 0);
        run_exit(c, "lost_run");
        w = data_window(c);
        require(load64(w, V_RC) == SYS_OK, "lost_rc");
        require(load64(w, GUEST_NREAD) == 32, "lost_nread");
        for (unsigned int i = 0; i < 15; ++i) {
            require(w[GUEST_BUF + 2 * i] == 0x1e, "lost_make");
            require(w[GUEST_BUF + 1 + 2 * i] == 0x9e, "lost_break");
        }
        require(w[GUEST_BUF + 30] == 0x1e, "lost_tail");
        require(w[GUEST_BUF + 31] == 0x00, "lost_marker");
        consumed_bytes += 32;
        text("[INPUT] payload n=32 hex=");
        hexbytes((const cpu_u8 *)&w[GUEST_BUF], 32);
        text("\r\n");
        teardown(c, "lost_teardown");
    }
    {
        struct user_context *c = make_probe((const cpu_u8 *)readargs_probe,
            (cpu_u64)(readargs_probe_end - readargs_probe), "lost_empty");
        volatile cpu_u8 *w = data_window(c);
        for (unsigned int i = 0; i < 8; ++i) w[GUEST_NREAD + i] = 0x5a;
        set_vector(c, 0, USER_DATA_BASE + GUEST_BUF, 40,
                   USER_DATA_BASE + GUEST_NREAD, 0, 0);
        run_exit(c, "lost_run2");
        w = data_window(c);
        require(load64(w, V_RC) == SYS_AGAIN, "lost_again");
        for (unsigned int i = 0; i < 8; ++i)
            require(w[GUEST_NREAD + i] == 0x5a, "lost_again_nread");
        teardown(c, "lost_teardown2");
    }
    text("[INPUT] lost matrix ok\r\n");
    balanced(before);
}

void read_self_test(void)
{
    text("[INPUT] self-test started\r\n");
    next_key = 0;
    staged_bytes = 0;
    consumed_bytes = 0;
    byte_log_n = 0;
    decode_tests();
    copy_tests();
    set_irq(0, 0, "irq0_mask");
    set_irq(1, 0, "irq1_mask");
    read_tests();
    park_tests();
    lost_tests();
    /* Staged total: P2 16 + P3 windows (attempts x shape) + P4 68.
       consumed_bytes tracks drains; the 0x00 loss marker accounts for
       exactly one byte that was never staged (68 staged -> 32 drained
       incl. marker means 31 consumed-from-staged + 1 marker). */
    require(consumed_bytes + 37 == staged_bytes + 1, "byte_reconcile");
    set_irq(0, 0, "irq0_restore");
    set_irq(1, 0, "irq1_restore");
    text("[INPUT] input verified\r\n");
}
