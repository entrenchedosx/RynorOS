#ifndef RYNOR_PAGING_H
#define RYNOR_PAGING_H
#include "vm.h"
typedef struct { cpu_u64 value; } page_entry;
struct page_table { page_entry entry[VM_ENTRIES]; };
_Static_assert(sizeof(struct page_table) == VM_PAGE_SIZE, "page-table geometry");
#define PTE_PRESENT (1ULL << 0)
#define PTE_WRITE   (1ULL << 1)
#define PTE_USER    (1ULL << 2)
#define PTE_PWT     (1ULL << 3)
#define PTE_PCD     (1ULL << 4)
#define PTE_ACCESS  (1ULL << 5)
#define PTE_DIRTY   (1ULL << 6)
#define PTE_HUGE    (1ULL << 7)
#define PTE_NX     (1ULL << 63)
#define PTE_ADDRESS 0x000ffffffffff000ULL
#define PTE_TABLE_FLAGS (PTE_PRESENT | PTE_WRITE | PTE_USER)
static inline int pte_has(page_entry p, cpu_u64 flag) { return (p.value & flag) != 0; }
static inline cpu_u64 pte_address(page_entry p) { return p.value & PTE_ADDRESS; }
static inline unsigned int page_index(cpu_u64 va, unsigned int level)
{ return (unsigned int)((va >> (12 + 9 * level)) & (VM_ENTRIES - 1)); }
static inline void page_invalidate(cpu_u64 va)
{ __asm__ volatile ("invlpg (%0)" : : "r"(va) : "memory"); }
#endif
