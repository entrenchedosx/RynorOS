#ifndef RYNOR_RUNTIME_REGION_H
#define RYNOR_RUNTIME_REGION_H
#include "cpu.h"
/* Arithmetic checks, NOT page-table/provenance validation. Trusted callers
   supply live mapped objects of the declared sizes, stable for the call. */
static inline int rt_region(const void *p, cpu_u64 n)
{
    return (!n || p) && n <= ~(cpu_u64)0 - (cpu_u64)p;
}
static inline int rt_overlap(const void *a, cpu_u64 an, const void *b, cpu_u64 bn)
{
    if (!an || !bn) return 0;
    cpu_u64 x = (cpu_u64)a, y = (cpu_u64)b;
    return x <= y ? y - x < an : x - y < bn;
}
#endif
