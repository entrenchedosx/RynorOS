/* INT-A1 synthetic ACPI fixtures: pure-parser adversarial cases over static
   buffers. No hardware, no mapping. Every case must pass for the boot to
   continue; acpi_test_cases() pins the executed count for the host. */
#include "acpi.h"
#include "serial.h"

static unsigned int cases;

static void require(int condition, const char *reason)
{
    if (condition) {
        ++cases;
        return;
    }
    serial_write("[ACPI] failure=");
    serial_write(reason);
    serial_write(" detail=");
    serial_write(acpi_error());
    serial_write("\r\n");
    serial_flush();
    cpu_halt();
}

static void copy(cpu_u8 *dst, const cpu_u8 *src, unsigned int n)
{
    for (unsigned int i = 0; i < n; ++i) dst[i] = src[i];
}

/* Stamp a 36-byte SDT header (signature + length + valid checksum). */
static void fix_header(cpu_u8 *t, const char *sig, unsigned int len)
{
    for (unsigned int i = 0; i < 4; ++i) t[i] = (cpu_u8)sig[i];
    t[4] = (cpu_u8)len;
    t[5] = (cpu_u8)(len >> 8);
    t[6] = 0;
    t[7] = 0;
    t[8] = 1;
    t[9] = 0;
    for (unsigned int i = 10; i < 36; ++i) t[i] = 0;
    cpu_u8 sum = 0;
    for (unsigned int i = 0; i < len; ++i) sum = (cpu_u8)(sum + t[i]);
    t[9] = (cpu_u8)(0 - sum);
}

static void build_rsdp_v1(cpu_u8 *r)
{
    const char sig[8] = {'R', 'S', 'D', ' ', 'P', 'T', 'R', ' '};
    copy(r, (const cpu_u8 *)sig, 8);
    r[8] = 0;
    for (unsigned int i = 9; i < 15; ++i) r[i] = 0;
    r[15] = 0;
    r[16] = 0x34;
    r[17] = 0x12;
    r[18] = 0;
    r[19] = 0;
    cpu_u8 sum = 0;
    for (unsigned int i = 0; i < 20; ++i) sum = (cpu_u8)(sum + r[i]);
    r[8] = (cpu_u8)(0 - sum);
}

static void build_rsdp_v2(cpu_u8 *r, cpu_u64 xsdt)
{
    const char sig[8] = {'R', 'S', 'D', ' ', 'P', 'T', 'R', ' '};
    copy(r, (const cpu_u8 *)sig, 8);
    r[8] = 0;
    for (unsigned int i = 9; i < 15; ++i) r[i] = 0;
    r[15] = 2;
    r[16] = 0x34;
    r[17] = 0x12;
    r[18] = 0;
    r[19] = 0;
    r[20] = 36;
    r[21] = 0;
    r[22] = 0;
    r[23] = 0;
    for (unsigned int i = 0; i < 8; ++i) r[24 + i] = (cpu_u8)(xsdt >> (8 * i));
    r[32] = 0;
    r[33] = 0;
    r[34] = 0;
    r[35] = 0;
    cpu_u8 sum = 0;
    for (unsigned int i = 0; i < 20; ++i) sum = (cpu_u8)(sum + r[i]);
    r[8] = (cpu_u8)(0 - sum);
    sum = 0;
    for (unsigned int i = 0; i < 36; ++i) sum = (cpu_u8)(sum + r[i]);
    r[32] = (cpu_u8)(0 - sum);
}

/* Valid MADT: 2 LAPIC CPUs + x2APIC CPU + IOAPIC + 2 ISOs + NMI + override
   + skipped type-3 + unknown type 0x7f. Returns total length. */
static unsigned int build_madt(cpu_u8 *m)
{
    unsigned int at = 44;
    for (unsigned int i = 0; i < 44; ++i) m[i] = 0;
    /* MADT header tail: LAPIC base + flags. */
    m[36] = 0x00;
    m[37] = 0x00;
    m[38] = 0xe0;
    m[39] = 0xfe;
    m[40] = 0x01;
    m[41] = 0x00;
    m[42] = 0x00;
    m[43] = 0x00;
    /* Type 0: uid 0, apic 0, enabled. */
    m[at + 0] = 0;
    m[at + 1] = 8;
    m[at + 2] = 0;
    m[at + 3] = 0;
    m[at + 4] = 1;
    m[at + 5] = 0;
    m[at + 6] = 0;
    m[at + 7] = 0;
    at += 8;
    /* Type 0: uid 1, apic 2, enabled. */
    m[at + 0] = 0;
    m[at + 1] = 8;
    m[at + 2] = 1;
    m[at + 3] = 2;
    m[at + 4] = 1;
    m[at + 5] = 0;
    m[at + 6] = 0;
    m[at + 7] = 0;
    at += 8;
    /* Type 9 x2APIC: id 0x100, enabled, uid 2. */
    m[at + 0] = 9;
    m[at + 1] = 16;
    m[at + 2] = 0;
    m[at + 3] = 0;
    m[at + 4] = 0x00;
    m[at + 5] = 0x01;
    m[at + 6] = 0x00;
    m[at + 7] = 0x00;
    m[at + 8] = 1;
    m[at + 9] = 0;
    m[at + 10] = 0;
    m[at + 11] = 0;
    m[at + 12] = 2;
    m[at + 13] = 0;
    m[at + 14] = 0;
    m[at + 15] = 0;
    at += 16;
    /* Type 1 IOAPIC: id 1, base 0xfec00000, gsi 0. */
    m[at + 0] = 1;
    m[at + 1] = 12;
    m[at + 2] = 1;
    m[at + 3] = 0;
    m[at + 4] = 0x00;
    m[at + 5] = 0x00;
    m[at + 6] = 0xc0;
    m[at + 7] = 0xfe;
    m[at + 8] = 0;
    m[at + 9] = 0;
    m[at + 10] = 0;
    m[at + 11] = 0;
    at += 12;
    /* Type 2 ISO: bus 0 irq 0 -> gsi 2, conforms. */
    m[at + 0] = 2;
    m[at + 1] = 10;
    m[at + 2] = 0;
    m[at + 3] = 0;
    m[at + 4] = 2;
    m[at + 5] = 0;
    m[at + 6] = 0;
    m[at + 7] = 0;
    m[at + 8] = 0;
    m[at + 9] = 0;
    at += 10;
    /* Type 2 ISO: bus 0 irq 9 -> gsi 9, active-low level. */
    m[at + 0] = 2;
    m[at + 1] = 10;
    m[at + 2] = 0;
    m[at + 3] = 9;
    m[at + 4] = 9;
    m[at + 5] = 0;
    m[at + 6] = 0;
    m[at + 7] = 0;
    m[at + 8] = 0x0f;
    m[at + 9] = 0x00;
    at += 10;
    /* Type 4 NMI: all processors, conforms, LINT1. */
    m[at + 0] = 4;
    m[at + 1] = 6;
    m[at + 2] = 0xff;
    m[at + 3] = 0;
    m[at + 4] = 0;
    m[at + 5] = 1;
    at += 6;
    /* Type 5 override: LAPIC at 0x1fee00000 (above 4G). Spec shape is
       12 bytes with the address at +4. */
    m[at + 0] = 5;
    m[at + 1] = 12;
    m[at + 2] = 0;
    m[at + 3] = 0;
    for (unsigned int i = 0; i < 8; ++i) m[at + 4 + i] = 0;
    m[at + 4] = 0x00;
    m[at + 5] = 0x00;
    m[at + 6] = 0xe0;
    m[at + 7] = 0xfe;
    m[at + 8] = 0x01;
    at += 12;
    /* Type 3 (deliberately skipped) + unknown 0x7f (skipped). */
    m[at + 0] = 3;
    m[at + 1] = 8;
    for (unsigned int i = 2; i < 8; ++i) m[at + i] = 0;
    at += 8;
    m[at + 0] = 0x7f;
    m[at + 1] = 4;
    m[at + 2] = 0;
    m[at + 3] = 0;
    at += 4;
    fix_header(m, "APIC", at);
    return at;
}

void acpi_test_synthetic(void);
unsigned int acpi_test_cases(void) { return cases; }

void acpi_test_synthetic(void)
{
    cpu_u8 buf[512];
    cpu_u8 bad[512];
    cpu_u32 revision = 0;
    int have_xsdt = 0;
    cpu_u64 rsdt = 0, xsdt = 0, phys = 0;
    unsigned int entries = 0;
    struct acpi_topology t;

    /* Checksum primitive. */
    require(!acpi_checksum_ok(0, 4), "sum_null");
    require(!acpi_checksum_ok(buf, 0), "sum_empty");
    buf[0] = 1;
    buf[1] = 0xff;
    require(acpi_checksum_ok(buf, 2), "sum_ok");
    buf[1] = 0xfe;
    require(!acpi_checksum_ok(buf, 2), "sum_bad");

    /* RSDP v1. */
    build_rsdp_v1(buf);
    require(acpi_parse_rsdp(buf, 20, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_OK &&
            revision == 0 && !have_xsdt && rsdt == 0x1234 && !xsdt,
            "rsdp_v1");
    copy(bad, buf, 20);
    bad[0] = 'X';
    require(acpi_parse_rsdp(bad, 20, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_INVALID,
            "rsdp_sig");
    copy(bad, buf, 20);
    bad[19] ^= 1;
    require(acpi_parse_rsdp(bad, 20, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_CHECKSUM,
            "rsdp_sum1");
    require(acpi_parse_rsdp(buf, 19, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_LENGTH,
            "rsdp_short");

    /* RSDP v2 with a 64-bit XSDT address (upper bits meaningful). */
    build_rsdp_v2(buf, 0x123456789ULL);
    require(acpi_parse_rsdp(buf, 36, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_OK &&
            revision == 2 && have_xsdt && xsdt == 0x123456789ULL,
            "rsdp_v2");
    copy(bad, buf, 36);
    bad[35] ^= 1;
    require(acpi_parse_rsdp(bad, 36, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_CHECKSUM,
            "rsdp_sum2");
    copy(bad, buf, 36);
    bad[20] = 16;
    bad[21] = 0;
    require(acpi_parse_rsdp(bad, 36, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_LENGTH,
            "rsdp_len_small");
    copy(bad, buf, 36);
    bad[20] = 0;
    bad[21] = 0x20;
    require(acpi_parse_rsdp(bad, 36, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_LENGTH,
            "rsdp_len_huge");
    /* Zero XSDT falls back to RSDT shape (no XSDT available). */
    build_rsdp_v2(buf, 0);
    require(acpi_parse_rsdp(buf, 36, &revision, &have_xsdt, &rsdt, &xsdt) == ACPI_OK &&
            !have_xsdt && !xsdt,
            "rsdp_v2_no_xsdt");

    /* RSDT root with two entries. */
    for (unsigned int i = 0; i < 44; ++i) buf[i] = 0;
    buf[36] = 0x00;
    buf[37] = 0x10;
    buf[38] = 0x00;
    buf[39] = 0x00;
    buf[40] = 0x00;
    buf[41] = 0x20;
    buf[42] = 0x00;
    buf[43] = 0x00;
    fix_header(buf, "RSDT", 44);
    require(acpi_parse_root(buf, 44, 0, &entries) == ACPI_OK && entries == 2, "rsdt_ok");
    require(acpi_root_entry(buf, 44, 0, 0, &phys) == ACPI_OK && phys == 0x1000,
            "rsdt_e0");
    require(acpi_root_entry(buf, 44, 0, 1, &phys) == ACPI_OK && phys == 0x2000,
            "rsdt_e1");
    require(acpi_root_entry(buf, 44, 0, 2, &phys) == ACPI_INVALID, "rsdt_oob");
    copy(bad, buf, 44);
    bad[0] = 'X';
    require(acpi_parse_root(bad, 44, 0, &entries) == ACPI_INVALID, "rsdt_sig");
    copy(bad, buf, 44);
    bad[43] ^= 1;
    require(acpi_parse_root(bad, 44, 0, &entries) == ACPI_CHECKSUM, "rsdt_sum");
    copy(bad, buf, 44);
    bad[4] = 43;
    bad[5] = 0;
    {
        cpu_u8 sum = 0;
        bad[9] = 0;
        for (unsigned int i = 0; i < 43; ++i) sum = (cpu_u8)(sum + bad[i]);
        bad[9] = (cpu_u8)(0 - sum);
    }
    require(acpi_parse_root(bad, 44, 0, &entries) == ACPI_LENGTH, "rsdt_ragged");

    /* XSDT root: 64-bit entries must not truncate. */
    for (unsigned int i = 0; i < 52; ++i) buf[i] = 0;
    for (unsigned int i = 0; i < 8; ++i) buf[36 + i] = (cpu_u8)(0x1abcd0000ULL >> (8 * i));
    for (unsigned int i = 0; i < 8; ++i) buf[44 + i] = (cpu_u8)(0x2000ULL >> (8 * i));
    fix_header(buf, "XSDT", 52);
    require(acpi_parse_root(buf, 52, 1, &entries) == ACPI_OK && entries == 2, "xsdt_ok");
    require(acpi_root_entry(buf, 52, 1, 0, &phys) == ACPI_OK && phys == 0x1abcd0000ULL,
            "xsdt_wide");
    require(acpi_root_entry(buf, 52, 1, 1, &phys) == ACPI_OK && phys == 0x2000,
            "xsdt_e1");

    /* Root entry-count bound: 257 XSDT entries exceed the scan cap. */
    {
        static cpu_u8 bigroot[36 + 257 * 8];
        for (unsigned int i = 0; i < sizeof(bigroot); ++i) bigroot[i] = 0;
        fix_header(bigroot, "XSDT", sizeof(bigroot));
        require(acpi_parse_root(bigroot, sizeof(bigroot), 1, &entries) == ACPI_CAPACITY,
                "root_cap");
    }

    /* Valid MADT. */
    unsigned int mlen = build_madt(buf);
    require(acpi_parse_madt(buf, mlen, &t) == ACPI_OK, "madt_ok");
    require(t.cpu_count == 3 && t.cpus[0].apic_id == 0 && t.cpus[1].apic_id == 2 &&
            t.cpus[2].apic_id == 0x100 && t.cpus[2].uid == 2,
            "madt_cpus");
    require(t.ioapic_count == 1 && t.ioapics[0].id == 1 &&
            t.ioapics[0].base == 0xfec00000ULL && !t.ioapics[0].gsi_base,
            "madt_ioapic");
    require(t.iso_count == 2 && t.isos[0].source == 0 && t.isos[0].gsi == 2 &&
            t.isos[1].source == 9 && t.isos[1].gsi == 9 && t.isos[1].flags == 0x0f,
            "madt_isos");
    require(t.nmi_count == 1 && t.nmis[0].processor == 0xff && t.nmis[0].lint == 1,
            "madt_nmi");
    require(t.lapic_override && t.lapic_base == 0x1fee00000ULL, "madt_override");
    require(t.skipped_records == 2 && !t.duplicate_isos && t.madt_flags == 1, "madt_skip");

    /* MADT corruptions. */
    copy(bad, buf, mlen);
    bad[0] = 'X';
    require(acpi_parse_madt(bad, mlen, &t) == ACPI_INVALID, "madt_sig");
    copy(bad, buf, mlen);
    bad[mlen - 1] ^= 1;
    require(acpi_parse_madt(bad, mlen, &t) == ACPI_CHECKSUM, "madt_sum");
    require(acpi_parse_madt(buf, 43, &t) == ACPI_LENGTH, "madt_short");
    /* Truncated record tail: valid header/checksum, last record overruns. */
    copy(bad, buf, mlen);
    bad[mlen - 1] = 0;
    bad[mlen - 2] = 0;
    bad[4] = (cpu_u8)(mlen - 1);
    {
        cpu_u8 sum = 0;
        bad[9] = 0;
        for (unsigned int i = 0; i < mlen - 1; ++i) sum = (cpu_u8)(sum + bad[i]);
        bad[9] = (cpu_u8)(0 - sum);
    }
    require(acpi_parse_madt(bad, mlen, &t) == ACPI_LENGTH, "madt_trunc");
    /* Zero-length record must fail, never loop. Checksum repaired so
       the walker (not the header check) is what rejects it. */
    copy(bad, buf, mlen);
    bad[44 + 1] = 0;
    {
        cpu_u8 sum = 0;
        bad[9] = 0;
        for (unsigned int i = 0; i < mlen; ++i) sum = (cpu_u8)(sum + bad[i]);
        bad[9] = (cpu_u8)(0 - sum);
    }
    require(acpi_parse_madt(bad, mlen, &t) == ACPI_LENGTH, "madt_zero");
    /* Record length running past the table end. */
    copy(bad, buf, mlen);
    bad[44 + 1] = (cpu_u8)(mlen - 44 + 1);
    {
        cpu_u8 sum = 0;
        bad[9] = 0;
        for (unsigned int i = 0; i < mlen; ++i) sum = (cpu_u8)(sum + bad[i]);
        bad[9] = (cpu_u8)(0 - sum);
    }
    require(acpi_parse_madt(bad, mlen, &t) == ACPI_LENGTH, "madt_overrun");
    /* Under-minimum type length. */
    copy(bad, buf, mlen);
    bad[44 + 1] = 7;
    {
        cpu_u8 sum = 0;
        bad[9] = 0;
        for (unsigned int i = 0; i < mlen; ++i) sum = (cpu_u8)(sum + bad[i]);
        bad[9] = (cpu_u8)(0 - sum);
    }
    require(acpi_parse_madt(bad, mlen, &t) == ACPI_LENGTH, "madt_short_rec");
    /* Short FINAL record: the type minimum must fire even at end of
       table, where no trailing record exists to fail downstream (without
       it the fixed-field reads walk past the table end). */
    {
        cpu_u8 fin[512];
        copy(fin, buf, 44 + 7);
        fin[44 + 0] = 0;
        fin[44 + 1] = 7;
        fin[4] = 51;
        fin[5] = 0;
        fin[6] = 0;
        fin[7] = 0;
        cpu_u8 fsum = 0;
        fin[9] = 0;
        for (unsigned int i = 0; i < 51; ++i) fsum = (cpu_u8)(fsum + fin[i]);
        fin[9] = (cpu_u8)(0 - fsum);
        require(acpi_parse_madt(fin, 51, &t) == ACPI_LENGTH, "madt_short_final");
    }

    /* Duplicate ISO: first wins, duplicate counted, values kept. */
    copy(bad, buf, mlen);
    /* Second ISO (at 44+8+8+16+12+10) becomes a second bus0/irq0 override. */
    bad[44 + 8 + 8 + 16 + 12 + 10 + 3] = 0;
    bad[44 + 8 + 8 + 16 + 12 + 10 + 4] = 7;
    {
        cpu_u8 sum = 0;
        bad[9] = 0;
        for (unsigned int i = 0; i < mlen; ++i) sum = (cpu_u8)(sum + bad[i]);
        bad[9] = (cpu_u8)(0 - sum);
    }
    require(acpi_parse_madt(bad, mlen, &t) == ACPI_OK && t.iso_count == 1 &&
            t.duplicate_isos == 1 && t.isos[0].gsi == 2,
            "madt_dup_iso");

    /* Second LAPIC override is skipped, first kept. Insert a rival type-5
       (address 0xfee00000) ahead of the type-3 record. */
    {
        cpu_u8 two[512];
        unsigned int at = 44 + 8 + 8 + 16 + 12 + 10 + 10 + 6 + 12;
        copy(two, buf, at);
        two[at + 0] = 5;
        two[at + 1] = 12;
        two[at + 2] = 0;
        two[at + 3] = 0;
        for (unsigned int i = 0; i < 8; ++i) two[at + 4 + i] = 0;
        two[at + 4] = 0x00;
        two[at + 5] = 0x00;
        two[at + 6] = 0xe0;
        two[at + 7] = 0xfe;
        copy(two + at + 12, buf + at, mlen - at);
        unsigned int nlen = mlen + 12;
        two[4] = (cpu_u8)nlen;
        two[5] = (cpu_u8)(nlen >> 8);
        two[9] = 0;
        cpu_u8 sum = 0;
        for (unsigned int i = 0; i < nlen; ++i) sum = (cpu_u8)(sum + two[i]);
        two[9] = (cpu_u8)(0 - sum);
        require(acpi_parse_madt(two, nlen, &t) == ACPI_OK && t.lapic_override &&
                t.lapic_base == 0x1fee00000ULL && t.skipped_records == 3,
                "madt_dup_override");
    }

    /* Capacity: 17 type-0 records exceed ACPI_MAX_CPUS. */
    {
        cpu_u8 big[44 + 17 * 8];
        for (unsigned int i = 0; i < sizeof(big); ++i) big[i] = 0;
        for (unsigned int i = 0; i < 17; ++i) {
            big[44 + i * 8] = 0;
            big[44 + i * 8 + 1] = 8;
        }
        fix_header(big, "APIC", sizeof(big));
        require(acpi_parse_madt(big, sizeof(big), &t) == ACPI_CAPACITY, "madt_cpu_cap");
    }
    /* Capacity: 9 IOAPIC records exceed ACPI_MAX_IOAPICS. */
    {
        cpu_u8 big[44 + 9 * 12];
        for (unsigned int i = 0; i < sizeof(big); ++i) big[i] = 0;
        for (unsigned int i = 0; i < 9; ++i) {
            big[44 + i * 12] = 1;
            big[44 + i * 12 + 1] = 12;
        }
        fix_header(big, "APIC", sizeof(big));
        require(acpi_parse_madt(big, sizeof(big), &t) == ACPI_CAPACITY, "madt_io_cap");
    }
}
