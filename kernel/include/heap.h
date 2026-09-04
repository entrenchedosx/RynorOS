#ifndef RYNOR_HEAP_H
#define RYNOR_HEAP_H
#include "cpu.h"

#define HEAP_PAGE_SIZE 4096ULL
#define HEAP_ALIGN_MIN 8ULL
#define HEAP_ALIGN_MAX 4096ULL
#define HEAP_ARENA_BYTES 65536ULL   /* 16 pages, all mapped during initialization. */
#define HEAP_ARENA_PAGES (HEAP_ARENA_BYTES / HEAP_PAGE_SIZE)
#define HEAP_BASE 0xffffc00000000000ULL

enum heap_result {
    HEAP_OK, HEAP_NOT_READY, HEAP_INVALID, HEAP_ALIGNMENT, HEAP_OVERFLOW,
    HEAP_OUT_OF_MEMORY, HEAP_CORRUPT, HEAP_BUSY, HEAP_CONTEXT,
    HEAP_VM_ERROR, HEAP_MAPPING_CONFLICT
};
struct heap_statistics {
    cpu_u64 arena_bytes, mapped_bytes, used_bytes, free_bytes, allocated_blocks, free_blocks;
};

/* Bounded kernel heap. Single CPU, IF=0 and !irq_in_context() required; no IRQ handler may call it.
   Arena pages are real PMM frames mapped through the Stage 5 VM kernel space.
   Not a user allocator; handles are trusted kernel pointers. */
enum heap_result heap_initialize(void);
enum heap_result heap_alloc(cpu_u64 size, cpu_u64 align, void **out);
enum heap_result heap_free(void *pointer);
enum heap_result heap_statistics(struct heap_statistics *out);
int heap_check(void);
void heap_self_test(void);
#endif
