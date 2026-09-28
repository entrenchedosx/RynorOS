/* INT-A1 synthetic interrupt-core fixtures: vector allocator, GSI math,
   ISO decoding, redirection bits, range overlap. Pure logic over static
   buffers; no hardware. apic_test_cases() pins the executed count. */
#include "apic.h"
#include "serial.h"

static unsigned int cases;

static void require(int condition, const char *reason)
{
    if (condition) {
        ++cases;
        return;
    }
    serial_write("[APIC] failure=");
    serial_write(reason);
    serial_write(" detail=");
    serial_write(apic_error());
    serial_write("\r\n");
    serial_flush();
    cpu_halt();
}

unsigned int apic_test_cases(void) { return cases; }

void apic_test_synthetic(void)
{
    /* Snapshot live vector state: timer/keyboard already claimed 32/33.
       Fixtures run on a pristine pool, then the live state is restored. */
    cpu_u8 saved_state[256];
    unsigned int saved_owner[256];
    for (unsigned int v = 0; v < 256; ++v) {
        saved_state[v] = (cpu_u8)apic_vector_state(v);
        saved_owner[v] = apic_vector_owner(v);
    }
    apic_vector_init();
    require(apic_vector_state(0) == 1 && apic_vector_state(31) == 1 &&
            apic_vector_state(32) == 1 && apic_vector_state(47) == 1 &&
            apic_vector_state(128) == 1 && apic_vector_state(129) == 1 &&
            apic_vector_state(254) == 1 && apic_vector_state(255) == 1,
            "vec_reserved");
    require(apic_vector_state(48) == 0 && apic_vector_state(127) == 0 &&
            apic_vector_free() == 80,
            "vec_pool");
    require(apic_vector_alloc() == 48, "vec_first");
    require(apic_vector_alloc() == 49, "vec_second");
    for (unsigned int i = 0; i < 78; ++i) {
        int v = apic_vector_alloc();
        require(v == (int)(50 + i), "vec_fill");
    }
    require(apic_vector_free() == 0, "vec_full");
    require(apic_vector_alloc() == -1, "vec_exhausted");
    require(apic_vector_release(48) == APIC_OK && apic_vector_state(48) == 0,
            "vec_release");
    require(apic_vector_alloc() == 48, "vec_reuse");
    require(apic_vector_release(48) == APIC_OK &&
            apic_vector_release(48) == APIC_STATE,
            "vec_double_free");
    require(apic_vector_release(200) == APIC_INVALID &&
            apic_vector_release(32) == APIC_INVALID,
            "vec_release_range");
    require(apic_vector_claim(32, 0xaa) == APIC_OK && apic_vector_owner(32) == 0xaa &&
            apic_vector_state(32) == 2,
            "vec_claim");
    require(apic_vector_claim(32, 0xaa) == APIC_STATE, "vec_claim_busy");
    require(apic_vector_claim(48, 0xaa) == APIC_INVALID &&
            apic_vector_claim(128, 0xaa) == APIC_INVALID,
            "vec_claim_range");
    require(apic_vector_owner(200) == 0 && apic_vector_state(300) == 1, "vec_oob");

    /* GSI ownership across two IOAPICs plus a nonzero-base singleton. */
    {
        cpu_u32 bases[2] = {0, 24};
        unsigned int maxes[2] = {23, 23};
        unsigned int index = 0, pin = 0;
        require(apic_gsi_owner_at(0, bases, maxes, 2, &index, &pin) == APIC_OK && !index &&
                !pin,
                "gsi_first");
        require(apic_gsi_owner_at(23, bases, maxes, 2, &index, &pin) == APIC_OK && !index &&
                pin == 23,
                "gsi_edge0");
        require(apic_gsi_owner_at(24, bases, maxes, 2, &index, &pin) == APIC_OK &&
                index == 1 && !pin,
                "gsi_edge1");
        require(apic_gsi_owner_at(47, bases, maxes, 2, &index, &pin) == APIC_OK &&
                index == 1 && pin == 23,
                "gsi_last");
        require(apic_gsi_owner_at(48, bases, maxes, 2, &index, &pin) == APIC_NOT_FOUND,
                "gsi_gap");
        cpu_u32 b1[1] = {24};
        unsigned int m1[1] = {7};
        require(apic_gsi_owner_at(23, b1, m1, 1, &index, &pin) == APIC_NOT_FOUND &&
                apic_gsi_owner_at(24, b1, m1, 1, &index, &pin) == APIC_OK && !index &&
                !pin &&
                apic_gsi_owner_at(31, b1, m1, 1, &index, &pin) == APIC_OK && pin == 7 &&
                apic_gsi_owner_at(32, b1, m1, 1, &index, &pin) == APIC_NOT_FOUND,
                "gsi_nonzero");
        require(apic_gsi_owner_at(0, 0, maxes, 2, &index, &pin) == APIC_INVALID &&
                apic_gsi_owner_at(0, bases, maxes, 0, &index, &pin) == APIC_INVALID,
                "gsi_args");
    }

    /* Range overlap matrix. */
    {
        cpu_u32 ok_b[2] = {0, 24};
        unsigned int ok_m[2] = {23, 23};
        require(!apic_ranges_overlap(ok_b, ok_m, 2), "ovl_disjoint");
        cpu_u32 ov_b[2] = {0, 16};
        unsigned int ov_m[2] = {23, 23};
        require(apic_ranges_overlap(ov_b, ov_m, 2), "ovl_hit");
        cpu_u32 adj_b[2] = {0, 24};
        unsigned int adj_m[2] = {23, 0};
        require(!apic_ranges_overlap(adj_b, adj_m, 2), "ovl_adjacent");
        cpu_u32 one_b[2] = {5, 5};
        unsigned int one_m[2] = {0, 0};
        require(apic_ranges_overlap(one_b, one_m, 2), "ovl_single");
        require(apic_ranges_overlap(0, ok_m, 2), "ovl_null");
    }

    /* ISO decoding with ISA conforms-defaults. */
    {
        cpu_u32 gsi = 0;
        int level = 0, low = 0;
        require(apic_irq_gsi_at(5, 0, 0, &gsi, &level, &low) == APIC_OK && gsi == 5 &&
                !level && !low,
                "iso_identity");
        struct acpi_iso isos[3] = {
            {0, 0, 2, 0},
            {0, 9, 9, 0x0f},
            {2, 0, 20, 0},
        };
        require(apic_irq_gsi_at(0, isos, 3, &gsi, &level, &low) == APIC_OK && gsi == 2 &&
                !level && !low,
                "iso_conforms");
        require(apic_irq_gsi_at(9, isos, 3, &gsi, &level, &low) == APIC_OK && gsi == 9 &&
                level && low,
                "iso_low_level");
        struct acpi_iso exp[1] = {{0, 1, 1, 0x05}};
        require(apic_irq_gsi_at(1, exp, 1, &gsi, &level, &low) == APIC_OK && !level &&
                !low,
                "iso_explicit");
        struct acpi_iso badpol[1] = {{0, 1, 1, 0x02}};
        require(apic_irq_gsi_at(1, badpol, 1, &gsi, &level, &low) == APIC_INVALID,
                "iso_reserved_pol");
        struct acpi_iso badtrg[1] = {{0, 1, 1, 0x08}};
        require(apic_irq_gsi_at(1, badtrg, 1, &gsi, &level, &low) == APIC_INVALID,
                "iso_reserved_trg");
        struct acpi_iso nonisa[1] = {{2, 3, 30, 0x0f}};
        require(apic_irq_gsi_at(3, nonisa, 1, &gsi, &level, &low) == APIC_OK && gsi == 3 &&
                !level && !low,
                "iso_nonisa");
        struct acpi_iso dup[2] = {{0, 4, 4, 0}, {0, 4, 9, 0x0f}};
        require(apic_irq_gsi_at(4, dup, 2, &gsi, &level, &low) == APIC_OK && gsi == 4,
                "iso_first_wins");
        require(apic_irq_gsi_at(16, isos, 3, &gsi, &level, &low) == APIC_INVALID,
                "iso_irq_range");
    }

    /* Redirection-entry bit layout (checked against silicon readback live). */
    {
        cpu_u32 lo = 0, hi = 0;
        apic_redir_bits(32, 0, 0, 0, 1, &lo, &hi);
        require(lo == 0x10020u && !hi, "redir_edge_high");
        apic_redir_bits(33, 5, 1, 1, 0, &lo, &hi);
        require(lo == 0xa021u && hi == 0x05000000u, "redir_low_level");
        apic_redir_bits(48, 0, 0, 0, 1, &lo, &hi);
        require(lo == 0x10030u && !hi, "redir_mask");
        apic_redir_bits(0x1ff, 0x1ff, 0, 0, 0, &lo, &hi);
        require(lo == 0xffu && hi == 0xff000000u, "redir_trunc");
        apic_redir_bits(0, 0, 1, 0, 0, &lo, &hi);
        require(lo == 0x8000u && !hi, "redir_level_only");
    }

    /* Reset is itself verified, then the live state comes back exactly. */
    apic_vector_init();
    require(apic_vector_free() == 80 && apic_vector_state(32) == 1 &&
            apic_vector_state(48) == 0,
            "vec_pristine");
    /* The allocator has no unregister path by design, so the snapshot is
       written back through the test-only restore helper. */
    apic_vector_restore(saved_state, saved_owner);
    {
        int same = 1;
        for (unsigned int v = 0; v < 256; ++v)
            same &= apic_vector_state(v) == saved_state[v] &&
                    apic_vector_owner(v) == saved_owner[v];
        require(same, "vec_restored");
    }
}
