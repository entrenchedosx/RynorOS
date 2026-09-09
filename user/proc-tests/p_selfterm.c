/* Slice C probe: self-terminate must be a defined rejection.
 *
 * Arrangement (driver-enforced, asserted): this program runs as the very
 * first spawn of the boot, so it occupies slot 0 with generation 1 and
 * its own handle is exactly (0 | 1<<32). Terminating that handle must
 * return INVAL (2) — never destroy the live bound caller — after which
 * the program exits normally, proving it survived.
 */
#include "rt.h"

extern unsigned long long rt_gate6(unsigned int num, unsigned long long a,
                                   unsigned long long b, unsigned long long c,
                                   unsigned long long d, unsigned long long e,
                                   unsigned long long f);

int rt_main(void)
{
    unsigned long long rc = rt_gate6(6, (1ULL << 32), 0, 0, 0, 0, 0);
    if (rc != 2)
        rt_exit(120);
    rt_exit(0);
    return 0;
}
