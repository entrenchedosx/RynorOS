/* Stage 17b read-only native filesystem (RYNORFS v1) over blk_read only.
   Flat entry storage with hierarchical path resolution; contiguous file
   extents; static state only (no heap, no allocation); single-threaded
   boot-time context. Production code never touches ports, registers, or
   host image files. See kernel/include/fs.h and
   docs/design/filesystem.md. */
#include "fs.h"
#include "blk.h"
#include "cpu.h"
#include "serial.h"

/* On-disk superblock (block 0), all little-endian, explicit offsets. */
#define FS_MAGIC "RYNORFS\0"
#define FS_MAGIC_LEN 8u
#define FS_VERSION 1u
#define FS_SB_VERSION 8u
#define FS_SB_BLKSIZE 12u
#define FS_SB_TOTAL 16u
#define FS_SB_DIR_START 24u
#define FS_SB_DIR_BLOCKS 32u
#define FS_SB_DATA_START 40u
#define FS_SB_DATA_BLOCKS 48u
#define FS_SB_RESERVED 56u
/* Directory entry (64 bytes). */
#define FS_D_NAME 0u
#define FS_D_NAMELEN 32u
#define FS_D_TYPE 32u
#define FS_D_FIRST 40u
#define FS_D_COUNT 48u
#define FS_D_LENGTH 56u
/* resolve() success for root: positive and disjoint from slots (<4096)
   and from every negative fs_result. */
#define FS_ROOT_SENTINEL 0x7fffffff

static int fs_is_mounted;
static cpu_u32 fs_dev;
/* R1 halt-on-rollback-failure: a failed rollback step means block-layer
   corruption, so halt with the exact subsystem marker instead of
   discarding the error. Unreachable while device slots are static (no
   hot-removal exists); if one ever fires the transcript names the step. */
static void rollback_fail(const char *step) __attribute__((noreturn));
static void rollback_fail(const char *step)
{
    __asm__ volatile ("cli" ::: "memory");
    (void)serial_write("[FS] failure=rollback_");
    (void)serial_write(step);
    (void)serial_write("\r\n");
    (void)serial_flush();
    cpu_halt();
}
/* Write-authorization lifetime: blk writable is granted on validated mount
   and must die on every remount attempt and every teardown, not just on
   successful unmount. Without this, a remount (success to another device
   or failure on a corrupt image) orphans a writable unmounted device and
   defeats the sole BLK_DENIED barrier. Idempotent clears make this safe. */
static int fs_writable_authorized;
static cpu_u64 fs_total, fs_dir_start, fs_dir_blocks, fs_data_start, fs_data_blocks;
/* Directory copy reads straight from word PIO: 2-byte aligned by
   construction, like fs_scratch (u8 statics are only align-1). */
static _Alignas(2) cpu_u8 fs_dir[FS_MAX_DIR_BLOCKS * 512u];
static cpu_u32 fs_dir_slots;
static struct { int in_use; cpu_u32 entry; cpu_u32 gen; } fs_handles[FS_MAX_OPEN];
static cpu_u32 fs_generations[FS_MAX_OPEN];
static const char *fs_stage = "none";
/* Scratch block staged through word PIO: 2-byte aligned by construction. */
static _Alignas(2) cpu_u8 fs_scratch[512u];

static cpu_u32 rd32le(const cpu_u8 *p)
{ return (cpu_u32)p[0] | ((cpu_u32)p[1] << 8) | ((cpu_u32)p[2] << 16) | ((cpu_u32)p[3] << 24); }

static cpu_u64 rd64le(const cpu_u8 *p)
{
    cpu_u64 v = 0;
    for (unsigned i = 0; i < 8; ++i) v |= (cpu_u64)p[i] << (i * 8);
    return v;
}

static void mem_copy(cpu_u8 *dst, const cpu_u8 *src, cpu_u64 n)
{ for (cpu_u64 i = 0; i < n; ++i) dst[i] = src[i]; }

/* [start, start+count) inside [0, total), overflow-safe by ordering. */
static int range_ok(cpu_u64 start, cpu_u64 count, cpu_u64 total)
{
    if (count > total) return 0;
    if (start > total - count) return 0;
    return 1;
}

static int ranges_overlap(cpu_u64 a_start, cpu_u64 a_count, cpu_u64 b_start, cpu_u64 b_count)
{
    /* Self-contained against wrap: every caller pre-checks range_ok, but
       the helper must not depend on that ordering to stay correct. */
    if (a_count > (cpu_u64)-1 - a_start || b_count > (cpu_u64)-1 - b_start)
        return 1;
    cpu_u64 a_end = a_start + a_count;
    cpu_u64 b_end = b_start + b_count;
    return a_start < b_end && b_start < a_end;
}

/* Entry accessors over the validated RAM directory copy. */
static const cpu_u8 *entry_at(cpu_u32 index)
{ return fs_dir + (cpu_u64)index * 64u; }

static int entry_free(const cpu_u8 *e)
{
    for (unsigned i = 0; i < 64; ++i)
        if (e[i]) return 0;
    return 1;
}

/* Name length, or 0 for malformed (empty, unterminated, bad byte, or
   nonzero padding after the terminator). */
static unsigned name_len(const cpu_u8 *e)
{
    unsigned n = 0;
    while (n < FS_D_NAMELEN && e[FS_D_NAME + n]) {
        cpu_u8 c = e[FS_D_NAME + n];
        if (c < 0x20u || c > 0x7eu) return 0;
        ++n;
    }
    if (!n || n > FS_MAX_NAME) return 0;
    if (n < FS_D_NAMELEN && e[FS_D_NAME + n]) return 0;
    for (unsigned i = n + 1; i < FS_D_NAMELEN; ++i)
        if (e[FS_D_NAME + i]) return 0;
    return n;
}

static int path_len(const char *path)
{
    if (!path) return -1;
    int n = 0;
    while (n <= (int)FS_MAX_PATH) {
        if (!path[n]) return n;
        ++n;
    }
    return -1;
}

/* Validate strict path rules. Returns length or -1. */
static int path_ok(const char *path)
{
    int len = path_len(path);
    if (len < 1 || len > (int)FS_MAX_PATH) return -1;
    if (path[0] != '/') return -1;
    for (int i = 1; i < len; ++i) {
        unsigned char c = (unsigned char)path[i];
        if (c < 0x20u || c > 0x7eu) return -1;
    }
    if (len > 1 && path[len - 1] == '/') return -1;
    for (int i = 1; i < len; ++i)
        if (path[i] == '/' && path[i - 1] == '/') return -1;
    /* No '.' / '..' components: there is no current/parent directory. */
    for (int i = 1; i < len;) {
        int j = i;
        while (j < len && path[j] != '/') ++j;
        int clen = j - i;
        if ((clen == 1 && path[i] == '.') || (clen == 2 && path[i] == '.' && path[i + 1] == '.'))
            return -1;
        i = j + 1;
    }
    return len;
}

/* Exact key lookup in the RAM directory. Returns slot or -1. */
static int find_key(const char *key, unsigned klen)
{
    for (cpu_u32 i = 0; i < fs_dir_slots; ++i) {
        const cpu_u8 *e = entry_at(i);
        if (entry_free(e)) continue;
        unsigned nlen = name_len(e);
        if (!nlen || nlen != klen) continue;
        unsigned k = 0;
        while (k < klen && e[FS_D_NAME + k] == (cpu_u8)key[k]) ++k;
        if (k == klen) return (int)i;
    }
    return -1;
}

/* Resolve a validated path. Returns slot, FS_ROOT_SENTINEL for root,
   or a negative fs_result (NOTFOUND/NOTDIR). */
static int resolve(const char *path, int len)
{
    char key[FS_MAX_NAME + 1u];
    unsigned klen = 0;
    int pos = 1;
    int slot = -1;
    if (len == 1) return FS_ROOT_SENTINEL;
    while (1) {
        int start = pos;
        while (pos < len && path[pos] != '/') ++pos;
        unsigned clen = (unsigned)(pos - start);
        if (klen) key[klen++] = '/';
        for (unsigned i = 0; i < clen; ++i) key[klen++] = path[start + i];
        key[klen] = 0;
        slot = find_key(key, klen);
        if (slot < 0) { fs_stage = "lookup-missing"; return FS_NOTFOUND; }
        int last = (pos == len);
        int type = entry_at((cpu_u32)slot)[FS_D_TYPE];
        if (!last) {
            if (type != FS_TYPE_DIR) { fs_stage = "lookup-notdir"; return FS_NOTDIR; }
            ++pos;
            continue;
        }
        return slot;
    }
}

static int decode_handle(cpu_u32 handle, cpu_u32 *slot_out)
{
    cpu_u32 slot = handle & 7u;
    cpu_u32 gen = handle >> 3;
    if (slot >= FS_MAX_OPEN || !gen) return FS_BADHANDLE;
    if (!fs_is_mounted) return FS_BADHANDLE;
    if (!fs_handles[slot].in_use || fs_generations[slot] != gen) return FS_BADHANDLE;
    *slot_out = slot;
    return FS_OK;
}

int fs_mount(cpu_u32 dev)
{
    fs_stage = "none";
    /* Fail-closed entry: a failed (re)mount never leaves the previous
       directory copy live behind new geometry, nor old handles valid,
       nor the previous device writable. Revoke first: success below
       re-authorizes the new device; every error return then leaves
        nothing writable behind. */
    if (fs_writable_authorized) {
        if (blk_clear_writable(fs_dev) != BLK_OK) rollback_fail("revoke");
        fs_writable_authorized = 0;
    }
    fs_is_mounted = 0;
    for (cpu_u32 s = 0; s < FS_MAX_OPEN; ++s) fs_handles[s].in_use = 0;
    const struct blk_device *info = blk_device(dev);
    if (!info) { fs_stage = "bad-device"; return FS_INVALID; }
    cpu_u64 capacity = info->block_count;
    static _Alignas(2) cpu_u8 sb[512u];
    int rc = blk_read(dev, 0, 1, sb, sizeof sb);
    if (rc) { fs_stage = "io-sb"; return FS_IOERR; }
    for (unsigned k = 0; k < FS_MAGIC_LEN; ++k)
        if (sb[k] != (cpu_u8)FS_MAGIC[k]) { fs_stage = "bad-magic"; return FS_INVALID; }
    if (rd32le(sb + FS_SB_VERSION) != FS_VERSION) { fs_stage = "bad-version"; return FS_UNSUPPORTED; }
    if (rd32le(sb + FS_SB_BLKSIZE) != 512u) { fs_stage = "bad-blksize"; return FS_UNSUPPORTED; }
    if (rd64le(sb + FS_SB_RESERVED)) { fs_stage = "reserved"; return FS_CORRUPT; }
    for (unsigned i = 64; i < 512; ++i)
        if (sb[i]) { fs_stage = "padding"; return FS_CORRUPT; }
    cpu_u64 total = rd64le(sb + FS_SB_TOTAL);
    cpu_u64 dir_start = rd64le(sb + FS_SB_DIR_START);
    cpu_u64 dir_blocks = rd64le(sb + FS_SB_DIR_BLOCKS);
    cpu_u64 data_start = rd64le(sb + FS_SB_DATA_START);
    cpu_u64 data_blocks = rd64le(sb + FS_SB_DATA_BLOCKS);
    if (!total || total > capacity) { fs_stage = "total-range"; return FS_CORRUPT; }
    if (!dir_blocks || dir_blocks > FS_MAX_DIR_BLOCKS) {
        fs_stage = !dir_blocks ? "dir-empty" : "dir-too-big";
        return !dir_blocks ? FS_CORRUPT : FS_UNSUPPORTED;
    }
    if (!range_ok(dir_start, dir_blocks, total) || !dir_start) {
        fs_stage = "dir-range";
        return FS_CORRUPT;
    }
    if (!data_blocks || !range_ok(data_start, data_blocks, total) || !data_start) {
        fs_stage = "data-range";
        return FS_CORRUPT;
    }
    /* Superblock (block 0), directory, and data extents pairwise disjoint. */
    if (ranges_overlap(0, 1, dir_start, dir_blocks) ||
        ranges_overlap(0, 1, data_start, data_blocks) ||
        ranges_overlap(dir_start, dir_blocks, data_start, data_blocks)) {
        fs_stage = "overlap";
        return FS_CORRUPT;
    }
    /* Read the directory in blk-sized chunks straight into the RAM copy. */
    cpu_u64 got = 0;
    while (got < dir_blocks) {
        cpu_u64 rest = dir_blocks - got;
        cpu_u32 n = rest > 32u ? 32u : (cpu_u32)rest;
        rc = blk_read(dev, dir_start + got, n, fs_dir + got * 512u, (cpu_u64)n * 512u);
        if (rc) { fs_stage = "io-dir"; return FS_IOERR; }
        got += n;
    }
    fs_dir_slots = (cpu_u32)(dir_blocks * 8u);
    /* Validate every entry. */
    for (cpu_u32 i = 0; i < fs_dir_slots; ++i) {
        const cpu_u8 *e = entry_at(i);
        if (entry_free(e)) continue;
        unsigned nlen = name_len(e);
        if (!nlen) { fs_stage = "bad-name"; return FS_CORRUPT; }
        /* Every slash-separated component must be non-empty and not
           "." / "..": such names can never resolve (lookup rejects
           empty components and dot names), so mounting them would hide
           an unreachable slot/extent. The host builder and decoder
           enforce the same rule. */
        {
            unsigned cstart = 0, k = 0;
            int badcomp = 0;
            for (; k <= nlen; ++k) {
                if (k == nlen || e[k] == '/') {
                    unsigned clen = k - cstart;
                    if (!clen) { badcomp = 1; break; }
                    if (clen == 1 && e[cstart] == '.') { badcomp = 1; break; }
                    if (clen == 2 && e[cstart] == '.' && e[cstart + 1] == '.') {
                        badcomp = 1;
                        break;
                    }
                    cstart = k + 1;
                }
            }
            if (badcomp) { fs_stage = "bad-name"; return FS_CORRUPT; }
        }
        int type = e[FS_D_TYPE];
        if (type != FS_TYPE_FILE && type != FS_TYPE_DIR) { fs_stage = "bad-type"; return FS_CORRUPT; }
        for (unsigned k = 33; k < 40; ++k)
            if (e[k]) { fs_stage = "entry-reserved"; return FS_CORRUPT; }
        cpu_u64 first = rd64le(e + FS_D_FIRST);
        cpu_u64 count = rd64le(e + FS_D_COUNT);
        cpu_u64 length = rd64le(e + FS_D_LENGTH);
        if (type == FS_TYPE_DIR) {
            if (first || count || length) { fs_stage = "dir-data"; return FS_CORRUPT; }
            continue;
        }
        /* Files: zero-length is canonical (0,0,0); nonzero needs blocks. */
        if (!count) {
            if (length || first) { fs_stage = "empty-extent"; return FS_CORRUPT; }
        } else {
            if (!length) { fs_stage = "empty-extent"; return FS_CORRUPT; }
            if (count > (cpu_u64)-1 / 512u || length > count * 512u) {
                fs_stage = "length-range";
                return FS_CORRUPT;
            }
            /* Ordered so no subtraction can wrap: data_end is safe because
               the data extent itself was range-checked at mount. */
            cpu_u64 data_end = data_start + data_blocks;
            if (first < data_start || first >= data_end || count > data_end - first) {
                fs_stage = "extent-range";
                return FS_CORRUPT;
            }
        }
    }
    /* Duplicates and dangling hierarchy rejected (first match would lie). */
    for (cpu_u32 i = 0; i < fs_dir_slots; ++i) {
        const cpu_u8 *a = entry_at(i);
        if (entry_free(a)) continue;
        unsigned alen = name_len(a);
        for (cpu_u32 j = i + 1; j < fs_dir_slots; ++j) {
            const cpu_u8 *b = entry_at(j);
            if (entry_free(b)) continue;
            unsigned blen = name_len(b);
            if (alen != blen) continue;
            unsigned k = 0;
            while (k < alen && a[k] == b[k]) ++k;
            if (k == alen) { fs_stage = "duplicate"; return FS_CORRUPT; }
        }
        /* Every proper prefix of a nested name must be a directory. */
        for (unsigned s = 0; s < alen; ++s) {
            if (a[s] != '/') continue;
            int parent = find_key((const char *)a, s);
            if (parent < 0 || entry_at((cpu_u32)parent)[FS_D_TYPE] != FS_TYPE_DIR) {
                fs_stage = "dangling-parent";
                return FS_CORRUPT;
            }
        }
    }
    /* File extents pairwise disjoint (shared data illegal in v1). */
    for (cpu_u32 i = 0; i < fs_dir_slots; ++i) {
        const cpu_u8 *a = entry_at(i);
        if (entry_free(a) || a[FS_D_TYPE] != FS_TYPE_FILE) continue;
        cpu_u64 af = rd64le(a + FS_D_FIRST), ac = rd64le(a + FS_D_COUNT);
        if (!ac) continue;
        for (cpu_u32 j = i + 1; j < fs_dir_slots; ++j) {
            const cpu_u8 *b = entry_at(j);
            if (entry_free(b) || b[FS_D_TYPE] != FS_TYPE_FILE) continue;
            cpu_u64 bf = rd64le(b + FS_D_FIRST), bc = rd64le(b + FS_D_COUNT);
            if (!bc) continue;
            if (ranges_overlap(af, ac, bf, bc)) { fs_stage = "overlap"; return FS_CORRUPT; }
        }
    }
    fs_total = total;
    fs_dir_start = dir_start;
    fs_dir_blocks = dir_blocks;
    fs_data_start = data_start;
    fs_data_blocks = data_blocks;
    (void)fs_total;
    (void)fs_dir_start;
    (void)fs_dir_blocks;
    (void)fs_data_start;
    (void)fs_data_blocks;
    fs_dev = dev;
    /* Authorize device writes now that the image is fully validated:
       from here on blk_write serves this filesystem only. Entry already
       revoked any previous authorization, so at most one device is
       writable at a time. */
    if (blk_set_writable(dev)) { fs_stage = "writable"; return FS_IOERR; }
    fs_writable_authorized = 1;
    for (cpu_u32 s = 0; s < FS_MAX_OPEN; ++s) {
        fs_handles[s].in_use = 0;
        if (++fs_generations[s] == 0) ++fs_generations[s];
    }
    fs_is_mounted = 1;
    return FS_OK;
}

void fs_unmount(void)
{
    if (fs_writable_authorized) {
        if (blk_clear_writable(fs_dev) != BLK_OK) rollback_fail("unmount");
        fs_writable_authorized = 0;
    }
    fs_is_mounted = 0;
    for (cpu_u32 s = 0; s < FS_MAX_OPEN; ++s) fs_handles[s].in_use = 0;
}

int fs_mounted(void) { return fs_is_mounted; }

cpu_u64 fs_total_blocks(void) { return fs_is_mounted ? fs_total : 0; }

int fs_open(const char *path, cpu_u32 *handle)
{
    if (!fs_is_mounted) { fs_stage = "not-mounted"; return FS_INVALID; }
    if (!handle) { fs_stage = "bad-arg"; return FS_INVALID; }
    int len = path_ok(path);
    if (len < 0) { fs_stage = "bad-path"; return FS_INVALID; }
    int slot = resolve(path, len);
    if (slot < 0) return slot;
    if (slot == FS_ROOT_SENTINEL || entry_at((cpu_u32)slot)[FS_D_TYPE] != FS_TYPE_FILE) {
        fs_stage = "not-file";
        return FS_NOTFILE;
    }
    for (cpu_u32 s = 0; s < FS_MAX_OPEN; ++s) {
        if (fs_handles[s].in_use) continue;
        if (++fs_generations[s] == 0) ++fs_generations[s];
        fs_handles[s].in_use = 1;
        fs_handles[s].entry = (cpu_u32)slot;
        *handle = s | (fs_generations[s] << 3);
        return FS_OK;
    }
    fs_stage = "busy";
    return FS_BUSY;
}

int fs_stat(const char *path, struct fs_stat *st)
{
    if (!fs_is_mounted) { fs_stage = "not-mounted"; return FS_INVALID; }
    if (!st) { fs_stage = "bad-arg"; return FS_INVALID; }
    int len = path_ok(path);
    if (len < 0) { fs_stage = "bad-path"; return FS_INVALID; }
    int slot = resolve(path, len);
    if (slot < 0) return slot;
    if (slot == FS_ROOT_SENTINEL) {
        st->type = FS_TYPE_DIR;
        st->size = 0;
        st->blocks = 0;
        return FS_OK;
    }
    const cpu_u8 *e = entry_at((cpu_u32)slot);
    st->type = e[FS_D_TYPE];
    st->size = rd64le(e + FS_D_LENGTH);
    st->blocks = rd64le(e + FS_D_COUNT);
    return FS_OK;
}

int fs_read(cpu_u32 handle, cpu_u64 offset, void *buf, cpu_u64 len, cpu_u64 *nread)
{
    cpu_u32 slot = 0;
    int ok = decode_handle(handle, &slot);
    if (ok) { fs_stage = "bad-handle"; return ok; }
    if (len > FS_MAX_READ_BYTES) { fs_stage = "too-long"; return FS_INVALID; }
    const cpu_u8 *e = entry_at(fs_handles[slot].entry);
    if (e[FS_D_TYPE] != FS_TYPE_FILE) { fs_stage = "not-file"; return FS_NOTFILE; }
    cpu_u64 size = rd64le(e + FS_D_LENGTH);
    if (offset > size) { fs_stage = "past-end"; return FS_RANGE; }
    cpu_u64 avail = size - offset;
    cpu_u64 n = len < avail ? len : avail;
    if (len && (!buf || ((cpu_u64)buf & 1u))) { fs_stage = "bad-buf"; return FS_INVALID; }
    if (nread) *nread = n;
    if (!n) return FS_OK;
    cpu_u64 first = rd64le(e + FS_D_FIRST);
    cpu_u64 cur = first + offset / 512u;
    cpu_u64 pos = offset % 512u;
    cpu_u8 *out = (cpu_u8 *)buf;
    cpu_u64 done = 0;
    while (done < n) {
        /* Full-block transfers need an even buffer for word PIO; an odd
           cursor (after an odd fragment) goes through scratch instead. */
        if (!pos && n - done >= 512u && !((cpu_u64)out & 1u)) {
            cpu_u64 full = (n - done) / 512u;
            cpu_u32 chunk = full > 32u ? 32u : (cpu_u32)full;
            int rc = blk_read(fs_dev, cur, chunk, out, (cpu_u64)chunk * 512u);
            if (rc) { if (nread) *nread = done; fs_stage = "io-data"; return FS_IOERR; }
            out += (cpu_u64)chunk * 512u;
            done += (cpu_u64)chunk * 512u;
            cur += chunk;
        } else {
            cpu_u64 take = 512u - pos < n - done ? 512u - pos : n - done;
            int rc = blk_read(fs_dev, cur, 1, fs_scratch, sizeof fs_scratch);
            if (rc) { if (nread) *nread = done; fs_stage = "io-data"; return FS_IOERR; }
            mem_copy(out, fs_scratch + pos, take);
            out += take;
            done += take;
            ++cur;
            pos = 0;
        }
    }
    return FS_OK;
}

int fs_close(cpu_u32 handle)
{
    cpu_u32 slot = 0;
    int ok = decode_handle(handle, &slot);
    if (ok) { fs_stage = "bad-handle"; return ok; }
    fs_handles[slot].in_use = 0;
    return FS_OK;
}

#if RYNOR_TEST_ARMED
/* One-shot fault counter for failure-injection tests: while positive,
   each data_write decrements it, and the transfer that reaches zero is
   skipped with a simulated I/O error. Zero/negative means disarmed. */
static int fs_fault_countdown;
void fs_inject_fault_at(int n) { fs_fault_countdown = n > 0 ? n : 0; }
#endif

/* Single block write through the fault choke point: the only path by
   which filesystem data reaches blk_write. Returns FS_OK or FS_IOERR. */
static int data_write(cpu_u64 lba, const cpu_u16 *words)
{
#if RYNOR_TEST_ARMED
    if (fs_fault_countdown > 0 && --fs_fault_countdown == 0)
        return FS_IOERR;
#endif
    int rc = blk_write(fs_dev, lba, 1, (void *)words, 512u);
    return rc ? FS_IOERR : FS_OK;
}

int fs_write(cpu_u32 handle, cpu_u64 offset, const void *buf, cpu_u64 len, cpu_u64 *nwritten)
{
    cpu_u32 slot = 0;
    int ok = decode_handle(handle, &slot);
    if (ok) { fs_stage = "bad-handle"; return ok; }
    if (len > FS_MAX_WRITE_BYTES) { fs_stage = "too-long"; return FS_INVALID; }
    const cpu_u8 *e = entry_at(fs_handles[slot].entry);
    if (e[FS_D_TYPE] != FS_TYPE_FILE) { fs_stage = "not-file"; return FS_NOTFILE; }
    cpu_u64 size = rd64le(e + FS_D_LENGTH);
    /* Overwrite-only: the range must lie entirely within the file.
       Extension, truncation, and creation are explicit FS_RANGE errors. */
    if (offset > size || len > size - offset) { fs_stage = "past-end"; return FS_RANGE; }
    if (len && (!buf || ((cpu_u64)buf & 1u))) { fs_stage = "bad-buf"; return FS_INVALID; }
    if (nwritten) *nwritten = len;
    if (!len) return FS_OK;
    cpu_u64 first = rd64le(e + FS_D_FIRST);
    cpu_u64 cur = first + offset / 512u;
    cpu_u64 pos = offset % 512u;
    const cpu_u8 *in = (const cpu_u8 *)buf;
    cpu_u64 done = 0;
    while (done < len) {
        /* Full-block writes need an even source for word PIO; an odd
           cursor (after an odd fragment) goes through scratch instead. */
        if (!pos && len - done >= 512u && !((cpu_u64)in & 1u)) {
            cpu_u64 full = (len - done) / 512u;
            cpu_u32 chunk = full > 32u ? 32u : (cpu_u32)full;
            cpu_u64 bytes = (cpu_u64)chunk * 512u;
            for (cpu_u32 i = 0; i < chunk; ++i) {
                int rc = data_write(cur + i, (const cpu_u16 *)(in + (cpu_u64)i * 512u));
                if (rc) {
                    if (nwritten) *nwritten = done + (cpu_u64)i * 512u;
                    fs_stage = "io-data";
                    return FS_IOERR;
                }
            }
            in += bytes;
            done += bytes;
            cur += chunk;
        } else {
            cpu_u64 take = 512u - pos < len - done ? 512u - pos : len - done;
            int rc = blk_read(fs_dev, cur, 1, fs_scratch, sizeof fs_scratch);
            if (rc) {
                if (nwritten) *nwritten = done;
                fs_stage = "io-data";
                return FS_IOERR;
            }
            mem_copy(fs_scratch + pos, in, take);
            rc = data_write(cur, (const cpu_u16 *)fs_scratch);
            if (rc) {
                if (nwritten) *nwritten = done;
                fs_stage = "io-data";
                return FS_IOERR;
            }
            in += take;
            done += take;
            ++cur;
            pos = 0;
        }
    }
    return FS_OK;
}

const char *fs_error_str(int code)
{
    switch (code) {
    case FS_OK: return "ok";
    case FS_INVALID: return "invalid";
    case FS_NOTFOUND: return "notfound";
    case FS_NOTFILE: return "notfile";
    case FS_NOTDIR: return "notdir";
    case FS_BADHANDLE: return "badhandle";
    case FS_RANGE: return "range";
    case FS_IOERR: return "ioerr";
    case FS_CORRUPT: return "corrupt";
    case FS_UNSUPPORTED: return "unsupported";
    case FS_BUSY: return "busy";
    default: return "unknown";
    }
}

const char *fs_stage_detail(void) { return fs_stage; }
