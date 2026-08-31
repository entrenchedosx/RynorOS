#ifndef RYNOR_VM_H
#define RYNOR_VM_H
#include "pmm.h"

#define VM_PAGE_SIZE 4096ULL
#define VM_ENTRIES 512u
#define VM_LEVELS 4u
#define VM_WINDOW 0xffffff0000000000ULL
#define VM_TEST_BASE 0x40000000ULL
#define VM_TEST_HIGH 0xffff800000000000ULL
#define VM_MMIO_BASE 0xfffffe8000000000ULL /* PML4 slot 509: device mappings */
#define VM_MMIO_END 0xffffff0000000000ULL
enum vm_result { VM_OK, VM_NOT_READY, VM_INVALID, VM_ALIGNMENT, VM_NONCANONICAL,
    VM_OVERFLOW, VM_PHYSICAL, VM_OOM, VM_EXISTS, VM_NOT_MAPPED, VM_PERMISSION,
    VM_UNSUPPORTED, VM_BUSY, VM_CONTEXT, VM_CORRUPT };
enum vm_permission { VM_WRITE = 1, VM_USER = 2, VM_EXECUTE = 4 };
/* Caller-owned, zero-initialized, immovable handle. Fields private to vm.c.
   Tables owned by this handle; mapped data frames remain owned by the caller. */
struct vm_space { cpu_u64 root, table_pages; struct vm_space *identity; };
struct vm_mapping { cpu_u64 physical; unsigned int permissions, accessed, dirty, uncached; };
int vm_canonical(cpu_u64 address);
enum vm_result vm_initialize(void);
struct vm_space *vm_kernel_space(void);
enum vm_result vm_create(struct vm_space *space);
enum vm_result vm_destroy(struct vm_space *space);
enum vm_result vm_map(struct vm_space *, cpu_u64 va, cpu_u64 pa, unsigned int permissions);
enum vm_result vm_map_range(struct vm_space *, cpu_u64 va, cpu_u64 pa, cpu_u64 pages, unsigned int permissions);
/* Kernel-space-only foreign MMIO in slot 509, IF=0. Reject all usable RAM,
   boot/metadata/ACPI/bad ranges; driver must prove an actual device aperture.
   Permissions exactly VM_WRITE; leaves supervisor RW/NX, PCD|PWT (PAT3 UC
   verified when PAT exists). Ordinary APIs cannot edit this slot. Data frames
   never enter PMM ownership; tables do. Range failure rolls back new tables. */
enum vm_result vm_map_device(struct vm_space *, cpu_u64 va, cpu_u64 pa, cpu_u64 pages, unsigned int permissions);
enum vm_result vm_unmap_device(struct vm_space *, cpu_u64 va, cpu_u64 pages);
enum vm_result vm_unmap(struct vm_space *, cpu_u64 va);
enum vm_result vm_unmap_range(struct vm_space *, cpu_u64 va, cpu_u64 pages);
enum vm_result vm_protect(struct vm_space *, cpu_u64 va, unsigned int permissions);
enum vm_result vm_query(struct vm_space *, cpu_u64 va, struct vm_mapping *out);
enum vm_result vm_translate(struct vm_space *, cpu_u64 va, cpu_u64 *physical);
int vm_check(struct vm_space *space);
/* Serialized temporary access to an allocated frame. Pointer expires on ANY
   following VM operation; never retain it, enable IRQs or call VM through it. */
void *vm_frame_access(cpu_u64 physical);
void vm_self_test(void);
int vm_fault_dispatch(struct exception_frame *frame, cpu_u64 cr2);
#endif
