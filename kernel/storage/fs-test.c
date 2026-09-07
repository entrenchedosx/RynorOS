/* Stage 17b filesystem self-test. Invalid-path/handle/mount cases run on
   every boot (no device needed). Device evidence runs only when a RYNORFS
   image is attached; normal boots stay silent on success so existing exact
   transcripts are unaffected. Failures halt with [FS] failure=. */
#include "fs.h"
#include "blk.h"
#include "serial.h"
#include "cpu.h"
#include "io.h"
#include "pmm.h"
#include "heap.h"
#include "vm.h"

static void require(int ok, const char *why)
{
    if (ok) return;
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[FS] failure=");
    (void)serial_write(why);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}

static void say(const char *s) { require(serial_write(s), "serial"); }

static void say_u64(cpu_u64 n)
{
    char b[21];
    unsigned i = 20;
    b[i] = 0;
    do { b[--i] = (char)('0' + n % 10); n /= 10; } while (n);
    say(b + i);
}

static cpu_u32 byte_sum(const cpu_u8 *b, cpu_u64 n)
{
    cpu_u32 s = 0;
    for (cpu_u64 i = 0; i < n; ++i) s += b[i];
    return s;
}

static cpu_u64 byte_wsum(const cpu_u8 *b, cpu_u64 n)
{
    cpu_u64 s = 0;
    for (cpu_u64 i = 0; i < n; ++i) s += i * b[i];
    return s;
}

static _Alignas(2) cpu_u8 fsbuf[16384u];
static _Alignas(2) cpu_u8 tiny[64u];

/* Fixed write payloads are generated here and printed as hex: the host
   validator patches expectations from the printed bytes, so no payload
   formula is duplicated on the host. Payloads stage in fsbuf (16 KiB). */
static void say_hex(const cpu_u8 *b, cpu_u64 n)
{
    static const char digits[] = "0123456789ABCDEF";
    for (cpu_u64 i = 0; i < n; ++i) {
        char pair[3];
        pair[0] = digits[(b[i] >> 4) & 15u];
        pair[1] = digits[b[i] & 15u];
        pair[2] = 0;
        say(pair);
    }
}

static void write_evidence(const char *path, cpu_u64 off, cpu_u64 len)
{
    cpu_u32 h = 0;
    cpu_u64 n = 0;
    require(len <= sizeof fsbuf, "w-too-big");
    for (cpu_u64 i = 0; i < len; ++i)
        fsbuf[i] = (cpu_u8)((off + i * 13u + 0x41u) & 0xffu);
    require(fs_open(path, &h) == FS_OK, "w-open");
    require(fs_write(h, off, fsbuf, len, &n) == FS_OK && n == len, "w-write");
    require(fs_close(h) == FS_OK, "w-close");
    say("[FS] write path=");
    say(path);
    say(" off=");
    say_u64(off);
    say(" len=");
    say_u64(len);
    say(" hex=");
    say_hex(fsbuf, len);
    say("\r\n");
}

static void invalid_cases(void)
{
    cpu_u32 h = 0;
    struct fs_stat st;
    cpu_u64 n = 0;
    /* No mount yet: everything structural fails closed. */
    require(fs_open("/hello", &h) == FS_INVALID, "unmounted-open");
    require(fs_stat("/hello", &st) == FS_INVALID, "unmounted-stat");
    require(fs_read(0, 0, fsbuf, 1, &n) == FS_BADHANDLE, "unmounted-read");
    require(fs_write(0, 0, fsbuf, 1, &n) == FS_BADHANDLE, "unmounted-write");
    require(fs_close(0) == FS_BADHANDLE, "unmounted-close");
    /* Mounting absent ids and the raw boot disk (MBR, not RYNORFS). */
    require(fs_mount(99) == FS_INVALID, "mount-badid");
    fs_unmount();
    /* Path rules: each malformed shape has one deterministic verdict. */
    static const char *bad[] = {
        "", "rel", "//a", "/a/", "/a//b", "/./x", "/../x", "/a/./b", "/a/../b",
    };
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; ++i)
        require(fs_open(bad[i], &h) == FS_INVALID, "bad-path");
    char longpath[34];
    longpath[0] = '/';
    for (unsigned i = 1; i < 33; ++i) longpath[i] = 'x';
    longpath[33] = 0;
    require(fs_open(longpath, &h) == FS_INVALID, "long-path");
    /* Handle abuse without any mount. */
    require(fs_close(0xffffffu) == FS_BADHANDLE, "close-huge");
    require(fs_read(0xffffffu, 0, fsbuf, 1, &n) == FS_BADHANDLE, "read-huge");
}

static void file_evidence(const char *path, int show_parts)
{
    cpu_u32 h = 0;
    struct fs_stat st;
    cpu_u64 n = 0;
    require(fs_stat(path, &st) == FS_OK, "evict-stat");
    require(st.type == 1, "evict-type");
    require(fs_open(path, &h) == FS_OK, "evict-open");
    if (st.size > sizeof fsbuf) {
        /* Chunked full read for files beyond one fs_read: sums add
           across chunks; the weighted sum is rebased per chunk. */
        cpu_u64 off = 0;
        cpu_u32 total = 0;
        cpu_u64 wtotal = 0;
        while (off < st.size) {
            cpu_u64 want = st.size - off > sizeof fsbuf ? sizeof fsbuf : st.size - off;
            require(fs_read(h, off, fsbuf, want, &n) == FS_OK && n == want, "evict-chunk");
            total += byte_sum(fsbuf, n);
            wtotal += off * byte_sum(fsbuf, n) + byte_wsum(fsbuf, n);
            off += n;
        }
        say("[FS] file path=");
        say(path);
        say(" size=");
        say_u64(st.size);
        say(" sum=");
        say_u64(total);
        say(" wsum=");
        say_u64(wtotal);
        say("\r\n");
        require(fs_close(h) == FS_OK, "evict-close");
        return;
    }
    require(fs_read(h, 0, fsbuf, sizeof fsbuf > st.size ? st.size : sizeof fsbuf, &n) == FS_OK,
            "evict-read");
    say("[FS] file path=");
    say(path);
    say(" size=");
    say_u64(st.size);
    say(" sum=");
    say_u64(byte_sum(fsbuf, n));
    say(" wsum=");
    /* Absolute weighted sum needs the base offset; full-from-zero reads
       start at zero so the chunk sum is already absolute here. */
    say_u64(byte_wsum(fsbuf, n));
    say("\r\n");
    /* Tails and edges: exact-boundary, one-past, over-read clamp. */
    if (show_parts) {
    if (st.size) {
        cpu_u64 last = st.size - 1u;
        require(fs_read(h, last, tiny, 1, &n) == FS_OK && n == 1, "evict-last");
        require(fs_read(h, st.size, tiny, 1, &n) == FS_OK && n == 0, "evict-eof");
        require(fs_read(h, st.size + 1u, tiny, 1, &n) == FS_RANGE, "evict-past");
        cpu_u64 mid = st.size / 2u;
        cpu_u64 poff = mid > 3u ? mid - 3u : 0;
        require(fs_read(h, poff, tiny, 7, &n) == FS_OK, "evict-part");
        say("[FS] part path=");
        say(path);
        say(" off=");
        say_u64(poff);
        say(" len=");
        say_u64(n);
        say(" sum=");
        say_u64(byte_sum(tiny, n));
        say(" wsum=");
        say_u64(byte_wsum(tiny, n));
        say("\r\n");
    } else {
        require(fs_read(h, 0, tiny, 1, &n) == FS_OK && n == 0, "evict-empty");
        require(fs_read(h, 1, tiny, 1, &n) == FS_RANGE, "evict-empty-past");
    }
    }
    /* Stale handle after close, then double close. */
    require(fs_close(h) == FS_OK, "evict-close");
    require(fs_read(h, 0, tiny, 1, &n) == FS_BADHANDLE, "evict-stale");
    require(fs_close(h) == FS_BADHANDLE, "evict-double");
}

void fs_self_test(void)
{
    require(cpu_interrupts_disabled(), "if0");
    invalid_cases();
    int mounted_dev = -1;
    for (cpu_u32 id = 0; id < 4u; ++id) {
        if (!blk_device(id)) continue;
        if (fs_mount(id) == FS_OK) { mounted_dev = (int)id; break; }
    }
    if (mounted_dev < 0) return; /* normal boot: silent success */
    say("[FS] mounted dev=");
    say_u64((cpu_u64)mounted_dev);
    say(" blocks=");
    say_u64(fs_total_blocks());
    say("\r\n");
    /* Fixed file set: the image builder guarantees these paths. */
    file_evidence("/hello", 1);
    file_evidence("/readme.txt", 1);
    file_evidence("/bin/test", 1);
    file_evidence("/docs/a.txt", 1);
    file_evidence("/docs/b.txt", 1);
    file_evidence("/nested/deep/file", 1);
    file_evidence("/empty", 1);
    file_evidence("/one", 1);
    file_evidence("/b511", 1);
    file_evidence("/b512", 1);
    file_evidence("/b513", 1);
    file_evidence("/b1500", 1);
    file_evidence("/bigfile", 1);
    file_evidence("/maxname-31-chars-abcdefg1234567", 1);
    /* Missing, dir-as-file, traversal-through-file, root. */
    cpu_u32 h = 0;
    struct fs_stat st;
    cpu_u64 n = 0;
    require(fs_open("/missing", &h) == FS_NOTFOUND, "neg-missing");
    require(fs_stat("/missing", &st) == FS_NOTFOUND, "neg-stat-missing");
    require(fs_open("/docs", &h) == FS_NOTFILE, "neg-dirfile");
    require(fs_open("/readme.txt/x", &h) == FS_NOTDIR, "neg-filedir");
    require(fs_open("/", &h) == FS_NOTFILE, "neg-root");
    require(fs_stat("/", &st) == FS_OK && st.type == 2, "neg-root-stat");
    /* Interleaved handles, then stale-after-remount. */
    cpu_u32 h1 = 0, h2 = 0;
    require(fs_open("/hello", &h1) == FS_OK, "hdl-1");
    require(fs_open("/readme.txt", &h2) == FS_OK, "hdl-2");
    require(fs_read(h1, 0, tiny, 5, &n) == FS_OK && n == 5, "hdl-r1");
    require(fs_read(h2, 0, tiny, 5, &n) == FS_OK && n == 5, "hdl-r2");
    require(fs_close(h1) == FS_OK && fs_close(h2) == FS_OK, "hdl-close");
    require(fs_mount((cpu_u32)mounted_dev) == FS_OK, "remount");
    require(fs_read(h1, 0, tiny, 1, &n) == FS_BADHANDLE, "hdl-stale");
    require(fs_close(h1) == FS_BADHANDLE, "hdl-stale-close");
    /* Repeated mount/read/unmount cycles must not move the allocator. */
    struct pmm_statistics pmm0, pmm1;
    struct heap_statistics heap0, heap1;
    require(pmm_statistics(&pmm0) == PMM_OK && heap_statistics(&heap0) == HEAP_OK, "acct0");
    for (unsigned c = 0; c < 3u; ++c) {
        require(fs_mount((cpu_u32)mounted_dev) == FS_OK, "acct-mount");
        require(fs_open("/hello", &h1) == FS_OK, "acct-open");
        require(fs_read(h1, 0, tiny, 5, &n) == FS_OK, "acct-read");
        require(fs_close(h1) == FS_OK, "acct-close");
        fs_unmount();
    }
    require(pmm_statistics(&pmm1) == PMM_OK && heap_statistics(&heap1) == HEAP_OK, "acct1");
    require(pmm0.allocated_bytes == pmm1.allocated_bytes &&
            pmm0.free_bytes == pmm1.free_bytes &&
            heap0.used_bytes == heap1.used_bytes &&
            heap0.free_blocks == heap1.free_blocks, "acct-flat");
    say("[FS] handles ok\r\n");
    say("[FS] accounting balanced\r\n");
    /* Overwrite-only writes within existing extents: fixed set with
       printed hex payloads (beginning, end, cross-block middle, full
       exact-size, multi-block). Invalid shapes fail without touching
       the disk. */
    require(fs_mount((cpu_u32)mounted_dev) == FS_OK, "w-mount");
    write_evidence("/hello", 0, 5);
    write_evidence("/b512", 511, 1);
    write_evidence("/b513", 512, 1);
    write_evidence("/b1500", 750, 8);
    write_evidence("/nested/deep/file", 0, 18);
    write_evidence("/bigfile", 0, 1024);
    write_evidence("/bigfile", 20000, 16384);
    /* Odd-offset cross-block I/O: the first fragment (511 bytes) leaves
       an odd cursor, which must route through scratch, never straight
       into word PIO (regression: used to fail as io-data). Disjoint
       from the other /bigfile writes so host readback stays exact. */
    write_evidence("/bigfile", 1025, 1024);
    require(fs_open("/bigfile", &h1) == FS_OK, "w-odd-open");
    require(fs_read(h1, 1025, fsbuf, 1024, &n) == FS_OK && n == 1024, "w-odd-read");
    for (cpu_u64 i = 0; i < 1024; ++i)
        require(fsbuf[i] == (cpu_u8)((1025u + i * 13u + 0x41u) & 0xffu), "w-odd-data");
    require(fs_close(h1) == FS_OK, "w-odd-close");
    require(fs_open("/one", &h1) == FS_OK, "w-handle");
    require(fs_write(h1, 0, 0, 0, &n) == FS_OK && n == 0, "w-empty-null");
    require(fs_open("/one", &h1) == FS_OK, "w-handle");
    require(fs_write(h1, 2, tiny, 1, &n) == FS_RANGE, "w-past-end");
    require(fs_write(h1, 0, tiny, 2, &n) == FS_RANGE, "w-over-length");
    require(fs_write(h1, 0, tiny, 16385, &n) == FS_INVALID, "w-too-long");
    require(fs_write(h1, 0, tiny, 1, 0) == FS_OK, "w-null-out");
    require(fs_write(0xffffffu, 0, tiny, 1, &n) == FS_BADHANDLE, "w-bad-handle");
    require(fs_write(h1, 0, tiny + 1, 1, &n) == FS_INVALID, "w-odd-buf");
    require(fs_close(h1) == FS_OK, "w-close");
    /* Stale handle after remount must not write. */
    require(fs_open("/one", &h1) == FS_OK, "w-stale-open");
    require(fs_mount((cpu_u32)mounted_dev) == FS_OK, "w-remount");
    require(fs_write(h1, 0, tiny, 1, &n) == FS_BADHANDLE, "w-stale");
    require(fs_close(h1) == FS_BADHANDLE, "w-stale-close");
    /* Read-after-remount: the overwrites persist across teardown.
       Full lines only (parts were already proven pre-write). */
    fs_unmount();
    require(fs_mount((cpu_u32)mounted_dev) == FS_OK, "w-remount2");
    file_evidence("/hello", 0);
    file_evidence("/b512", 0);
    file_evidence("/b513", 0);
    file_evidence("/b1500", 0);
    file_evidence("/nested/deep/file", 0);
    file_evidence("/bigfile", 0);
#if RYNOR_TEST_ARMED
    /* Fault injection (armed builds only): one-shot block failures with
       exact completed-prefix reporting. */
    require(fs_open("/b1500", &h1) == FS_OK, "f-open");
    fs_inject_fault_at(1);
    require(fs_write(h1, 0, tiny, 64, &n) == FS_IOERR && n == 0, "f-first");
    say("[FS] fault case=first written=0 code=ioerr\r\n");
    fs_inject_fault_at(2);
    require(fs_write(h1, 0, fsbuf, 1024, &n) == FS_IOERR && n == 512, "f-partial");
    say("[FS] fault case=partial written=512 code=ioerr\r\n");
    fs_inject_fault_at(99);
    require(fs_write(h1, 0, tiny, 64, &n) == FS_OK && n == 64, "f-after");
    say("[FS] fault case=after written=64 code=ok\r\n");
    fs_inject_fault_at(0);
    require(fs_close(h1) == FS_OK, "f-close");
#endif
    /* Every other present device must fail mounting with a classified
       code (the boot disk is not a filesystem; corrupt images fail by
       kind). A surprise success here is itself the failure. */
    int saw_bad_mount = 0;
    for (cpu_u32 id = 0; id < 4u; ++id) {
        if ((int)id == mounted_dev) continue;
        if (!blk_device(id)) continue;
        int rc = fs_mount(id);
        require(rc != FS_OK, "corrupt-mounted");
        saw_bad_mount = 1;
        say("[FS] corrupt slot=");
        say_u64(id);
        say(" code=");
        say(fs_error_str(rc));
        say("\r\n");
    }
    /* Write-authorization revocation: the failed (re)mounts above must
       have left the previous good device non-writable. Raw blk_write is
       the sole barrier probe (fsbuf is PIO-aligned). Unconditional:
       when no bad device exists the good device is still mounted here,
       so remount it through a failed mount first to force the revoke
       path (mounting the boot disk always fails). */
    if (!saw_bad_mount) {
        /* No bad device present (single-device topology): the boot disk
           (id 0) is never a filesystem, so mounting it fails and forces
           the revoke path. mounted_dev cannot be 0 here (it mounted OK
           above, and id 0 never does). */
        require((int)mounted_dev != 0, "good-is-boot");
        int bro = fs_mount(0u);
        require(bro != FS_OK, "boot-mount-fails");
        saw_bad_mount = 1;
        say("[FS] corrupt slot=0 code=");
        say(fs_error_str(bro));
        say("\r\n");
    }
    if (saw_bad_mount)
        require(blk_write((cpu_u32)mounted_dev, 0, 1, fsbuf, 512) == BLK_DENIED,
                "revoke-after-fail");
    fs_unmount();
    /* Teardown revocation: after unmount nothing may stay writable, and
       the boot disk is never writable at rest. */
    require(blk_write((cpu_u32)mounted_dev, 0, 1, fsbuf, 512) == BLK_DENIED,
            "revoke-after-unmount");
    require(blk_write(0u, 0, 1, fsbuf, 512) == BLK_DENIED, "boot-readonly");
    /* Leave unmounted: nothing persists past the self-test. */
    say("[FS] fs verified\r\n");
}
