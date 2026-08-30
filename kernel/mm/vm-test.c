#include "vm.h"
#include "paging.h"
#include "serial.h"

extern void vm_test_read(cpu_u64), vm_test_write(cpu_u64, cpu_u64), vm_test_execute(cpu_u64);
extern char vm_test_read_fault[], vm_test_read_after[], vm_test_write_fault[], vm_test_write_after[];
extern char vm_test_execute_after[], __text_end[], __rodata_end[], __data_start[];
cpu_u64 vm_fault_rsp;
static struct { cpu_u64 address, error, rip, resume; unsigned int armed, seen; } fault;

static void text(const char *s) { if (!serial_write(s)) cpu_halt(); }
static void require(int condition, const char *reason)
{
    if (condition) return;
    text("[VM] failure="); text(reason); text("\r\n"); serial_flush(); cpu_halt();
}
static void number(cpu_u64 value)
{
    char buffer[21]; unsigned int n = 20; buffer[n] = 0;
    do { buffer[--n] = (char)('0' + value % 10); value /= 10; } while (value);
    text(buffer + n);
}
static void field(const char *label, cpu_u64 n) { text(label); number(n); }
static void hex(const char *label, cpu_u64 value)
{
    const char *digits = "0123456789abcdef";
    char buffer[19] = "0x0000000000000000";
    for (unsigned int i = 0; i < 16; ++i) buffer[17-i] = digits[(value >> (i*4)) & 15];
    text(label); text(buffer);
}
int vm_fault_dispatch(struct exception_frame *f, cpu_u64 cr2)
{
    if (!vm_kernel_space()) return 0; /* Stage 2 pre-VM test path remains unchanged. */
    text("[VM] page fault\r\n");
    hex("[VM] fault_address=", cr2); hex(" error=", f->error); hex(" rip=", f->rip); text("\r\n");
    field("[VM] present=", f->error & 1); field(" write=", (f->error >> 1) & 1);
    field(" user=", (f->error >> 2) & 1); field(" reserved=", (f->error >> 3) & 1);
    field(" fetch=", (f->error >> 4) & 1); field(" cpl=", f->cs & 3); text("\r\n");
    if (!fault.armed || fault.seen || f->vector != 14 || cr2 != fault.address ||
        f->error != fault.error || f->rip != fault.rip || f->rsp != vm_fault_rsp ||
        f->cs != CPU_CODE_SELECTOR || f->ss != CPU_DATA_SELECTOR || (f->rflags & 0x200)) {
        text("[VM] page fault action=halt reason=unexpected\r\n");
        return 0;
    }
    fault.armed = 0; fault.seen = 1;
    f->rip = fault.resume;
    text("[VM] page fault action=resume_test\r\n");
    return 1;
}

static void fault_test(cpu_u64 va, cpu_u64 error)
{
    require(!fault.armed, "fault_already_armed");
    fault.address = va; fault.error = error; fault.seen = 0;
    fault.rip = error == 3 ? (cpu_u64)vm_test_write_fault :
                error == 17 ? va : (cpu_u64)vm_test_read_fault;
    fault.resume = error == 3 ? (cpu_u64)vm_test_write_after :
                   error == 17 ? (cpu_u64)vm_test_execute_after : (cpu_u64)vm_test_read_after;
    fault.armed = 1;
    if (error == 3) vm_test_write(va, 0xdead);
    else if (error == 17) vm_test_execute(va);
    else vm_test_read(va);
    require(fault.seen == 1 && !fault.armed, "expected_hardware_page_fault_missing");
}

/* Real OOM using an intrusive list in the frames this test actually allocates.
   No extra allocation pool or capacity knob; the frame window reaches high RAM. */
static void oom_test(struct vm_space *k, cpu_u64 data)
{
    struct vm_space empty_space = {0}, failed_space = {0};
    require(vm_create(&empty_space) == VM_OK, "oom_space_create");
    struct pmm_statistics before, exhausted, after;
    pmm_statistics(&before);
    cpu_u64 head = 0, count = 0, frame;
    enum pmm_result status;
    while ((status = pmm_allocate(&frame)) == PMM_OK) {
        volatile cpu_u64 *memory = vm_frame_access(frame);
        require(memory != (void *)0, "oom_frame_access");
        memory[0] = head; head = frame; ++count;
    }
    require(status == PMM_OUT_OF_MEMORY && pmm_statistics(&exhausted) == PMM_OK &&
            !exhausted.free_bytes && count * VM_PAGE_SIZE == before.free_bytes, "vm_real_exhaustion");
    require(vm_create(&failed_space) == VM_OOM && !failed_space.root && !failed_space.table_pages,
            "root_allocation_oom");
    require(vm_map(&empty_space, VM_TEST_HIGH, data, VM_WRITE) == VM_OOM &&
            empty_space.table_pages == 1 && vm_check(&empty_space), "table_allocation_oom");
    /* Allow two table allocations then fail the third: rollback must free both. */
    for (unsigned int i = 0; i < 2; ++i) {
        frame = head; head = *(volatile cpu_u64 *)vm_frame_access(frame);
        require(pmm_release(frame) == PMM_OK, "oom_partial_release"); --count;
    }
    require(vm_map(&empty_space, VM_TEST_HIGH, data, VM_WRITE) == VM_OOM &&
            empty_space.table_pages == 1 && vm_check(&empty_space), "partial_table_rollback");
    pmm_statistics(&after);
    require(after.free_bytes == 2 * VM_PAGE_SIZE, "partial_rollback_accounting");
    frame = head; head = *(volatile cpu_u64 *)vm_frame_access(frame);
    require(pmm_release(frame) == PMM_OK, "range_oom_release"); --count;
    require(vm_map_range(&empty_space, VM_TEST_BASE + 0x1ff000, data, 3, VM_WRITE) == VM_OOM &&
            empty_space.table_pages == 1 && vm_check(&empty_space), "range_oom_rollback");
    pmm_statistics(&after);
    require(after.free_bytes == 3 * VM_PAGE_SIZE, "range_rollback_accounting");
    while (head) {
        frame = head; head = *(volatile cpu_u64 *)vm_frame_access(frame);
        require(pmm_release(frame) == PMM_OK, "oom_release"); --count;
    }
    require(!count && pmm_statistics(&after) == PMM_OK && after.free_bytes == before.free_bytes &&
            vm_destroy(&empty_space) == VM_OK && pmm_check() && vm_check(k), "oom_restore");
    text("[TEST] VM real OOM rollback verified\r\n");
}

void vm_self_test(void)
{
    struct pmm_statistics initial, baseline, final;
    pmm_statistics(&initial);
    enum vm_result result = vm_initialize();
    if (result != VM_OK) { field("[VM] init_error=", result); text("\r\n"); }
    require(result == VM_OK, "initialization");
    struct vm_space *k = vm_kernel_space();
    text("[VM] paging subsystem initialized\r\n[VM] kernel address space created\r\n[VM] CR3 loaded\r\n");
    field("[VM] root=", k->root); field(" table_pages=", k->table_pages); text("\r\n");
    require(pmm_statistics(&baseline) == PMM_OK && k->table_pages == 7 &&
            baseline.allocated_bytes == initial.allocated_bytes + k->table_pages * VM_PAGE_SIZE &&
            vm_initialize() == VM_BUSY && vm_destroy(k) == VM_BUSY, "table_ownership");
    struct vm_mapping m;
    cpu_u64 translated = ~0ULL;
    require(vm_query(k, (cpu_u64)vm_self_test, &m) == VM_OK && m.permissions == VM_EXECUTE &&
            m.physical == (cpu_u64)vm_self_test &&
            vm_query(k, (cpu_u64)__data_start, &m) == VM_OK && m.permissions == VM_WRITE &&
            vm_query(k, (cpu_u64)__text_end, &m) == VM_OK && m.permissions == 0 &&
            vm_query(k, (cpu_u64)__kernel_stack_end - 8, &m) == VM_OK && m.permissions == VM_WRITE &&
            vm_translate(k, 0, &translated) == VM_NOT_MAPPED &&
            vm_translate(k, (cpu_u64)__page_tables_start, &translated) == VM_NOT_MAPPED,
            "kernel_layout");
    text("[VM] kernel mappings verified\r\n[TEST] VM self-test started\r\n");
    cpu_u64 data[3];
    for (unsigned int i = 0; i < 3; ++i)
        require(pmm_allocate(&data[i]) == PMM_OK && (!i || data[i] == data[0] + i * VM_PAGE_SIZE), "data_frames");
    require(vm_map(k, VM_TEST_BASE, data[0], VM_WRITE) == VM_OK, "map_one");
    volatile cpu_u64 *alias = (void *)VM_TEST_BASE;
    alias[0] = 0x726e766d00000001ULL; alias[511] = ~alias[0];
    volatile cpu_u64 *physical = vm_frame_access(data[0]);
    require(physical[0] == 0x726e766d00000001ULL && physical[511] == ~physical[0] &&
            vm_translate(k, VM_TEST_BASE + 4088, &translated) == VM_OK && translated == data[0] + 4088 &&
            vm_query(k, VM_TEST_BASE, &m) == VM_OK && m.accessed && m.dirty && m.permissions == VM_WRITE,
            "hardware_translation_write");
    field("[VM] mapping va=", VM_TEST_BASE); field(" physical=", data[0]); field(" offset_physical=", translated); text("\r\n");
    text("[TEST] VM mapping verified\r\n");
    require(vm_map(k, VM_TEST_BASE, data[1], VM_WRITE) == VM_EXISTS &&
            vm_map(k, VM_TEST_BASE + 1, data[1], VM_WRITE) == VM_ALIGNMENT &&
            vm_map(k, VM_TEST_BASE + VM_PAGE_SIZE, data[1] + 1, 0) == VM_ALIGNMENT &&
            vm_map(k, 0x0000800000000000ULL, data[1], 0) == VM_NONCANONICAL &&
            vm_map_range(k, 0xfffffffffffff000ULL, data[1], 2, 0) == VM_OVERFLOW &&
            vm_map_range(k, 0x00007ffffffff000ULL, data[1], 2, 0) == VM_NONCANONICAL &&
            vm_map(k, VM_TEST_HIGH, 0x1000, 0) == VM_PHYSICAL &&
            vm_map(k, VM_TEST_HIGH, ~0xfffULL, 0) == VM_PHYSICAL &&
            vm_map(k, VM_TEST_HIGH, data[1], VM_WRITE | VM_EXECUTE) == VM_PERMISSION &&
            vm_map(k, VM_TEST_HIGH, data[1], VM_USER) == VM_PERMISSION &&
            vm_unmap(k, (cpu_u64)vm_self_test & ~0xfffULL) == VM_PERMISSION &&
            vm_map(k, VM_WINDOW, data[1], 0) == VM_PERMISSION, "negative_api");
    text("[TEST] VM invalid mappings rejected\r\n");
    /* Warm TLB with writes, remove W, and require a real supervisor write fault. */
    require(vm_protect(k, VM_TEST_BASE, 0) == VM_OK, "read_only");
    text("[TEST] triggering read-only page fault\r\n"); fault_test(VM_TEST_BASE, 3);
    require(alias[0] == 0x726e766d00000001ULL, "write_protection");
    require(vm_protect(k, VM_TEST_BASE, VM_WRITE) == VM_OK, "restore_write");
    alias[0] = 0xc3; /* Real x86 RET instruction for the RX/NX execution test. */
    require(vm_protect(k, VM_TEST_BASE, VM_EXECUTE) == VM_OK, "set_execute");
    vm_test_execute(VM_TEST_BASE); /* Must actually execute and return. */
    require(vm_protect(k, VM_TEST_BASE, 0) == VM_OK, "set_nx");
    text("[TEST] triggering non-executable page fault\r\n"); fault_test(VM_TEST_BASE, 17);
    text("[TEST] VM permissions verified\r\n");
    require(vm_unmap(k, VM_TEST_BASE) == VM_OK && vm_translate(k, VM_TEST_BASE, &translated) == VM_NOT_MAPPED &&
            vm_unmap(k, VM_TEST_BASE) == VM_NOT_MAPPED, "unmap");
    text("[TEST] VM unmapping verified\r\n[TEST] triggering controlled page fault\r\n");
    fault_test(VM_TEST_BASE, 0);
    text("[TEST] controlled page fault verified\r\n[TEST] page fault diagnostics verified\r\n");
    /* Remap the same warmed VA to a different physical frame, never stale RAM. */
    *(volatile cpu_u64 *)vm_frame_access(data[1]) = 0x1122334455667788ULL;
    require(vm_map(k, VM_TEST_BASE, data[1], VM_WRITE) == VM_OK && alias[0] == 0x1122334455667788ULL &&
            vm_unmap(k, VM_TEST_BASE) == VM_OK, "tlb_remap");
    text("[TEST] VM TLB invalidation verified\r\n");
    cpu_u64 range = VM_TEST_BASE + 0x1ff000;
    require(vm_map_range(k, range, data[0], 3, VM_WRITE) == VM_OK, "range_map");
    for (unsigned int i = 0; i < 3; ++i) {
        *(volatile cpu_u64 *)(range + i * VM_PAGE_SIZE) = 100 + i;
        require(*(volatile cpu_u64 *)vm_frame_access(data[i]) == 100 + i, "range_access");
    }
    require(vm_unmap_range(k, range, 4) == VM_NOT_MAPPED &&
            vm_query(k, range, &m) == VM_OK && vm_unmap_range(k, range, 3) == VM_OK &&
            vm_map(k, VM_TEST_HIGH, data[2], VM_WRITE) == VM_OK &&
            *(volatile cpu_u64 *)VM_TEST_HIGH == 102 && vm_unmap(k, VM_TEST_HIGH) == VM_OK, "range_high_half");
    text("[TEST] VM ranges and high addresses verified\r\n");
    struct vm_space other = {0};
    cpu_u64 poisoned;
    require(pmm_allocate(&poisoned) == PMM_OK, "zeroing_allocate");
    volatile cpu_u64 *poison = vm_frame_access(poisoned);
    for (unsigned int i = 0; i < VM_ENTRIES; ++i) poison[i] = ~0ULL;
    require(pmm_release(poisoned) == PMM_OK && vm_create(&other) == VM_OK &&
            other.root == poisoned && vm_check(&other), "table_zeroing");
    require(vm_map(&other, VM_TEST_BASE, data[0], VM_USER | VM_WRITE) == VM_OK &&
            vm_query(&other, VM_TEST_BASE, &m) == VM_OK && m.permissions == (VM_USER | VM_WRITE) &&
            vm_check(&other), "inactive_space_mapping");
    volatile struct page_table *root = vm_frame_access(other.root);
    page_entry saved = root->entry[0];
    root->entry[0].value |= PTE_HUGE;
    require(vm_query(&other, VM_TEST_BASE, &m) == VM_UNSUPPORTED &&
            vm_destroy(&other) == VM_CORRUPT, "huge_configuration_rejected");
    root = vm_frame_access(other.root); root->entry[0] = saved;
    require(vm_check(&other) && vm_destroy(&other) == VM_OK && !other.root && !other.table_pages,
            "inactive_space_ownership");
    text("[TEST] VM address-space destruction verified\r\n");
    oom_test(k, data[0]);
    for (unsigned int i = 0; i < 3; ++i) require(pmm_release(data[i]) == PMM_OK, "data_release");
    require(vm_check(k) && pmm_check() && pmm_statistics(&final) == PMM_OK &&
            final.free_bytes == baseline.free_bytes && final.allocated_bytes == baseline.allocated_bytes &&
            k->table_pages == 7, "final_vm_accounting");
    field("[VM] final table_pages=", k->table_pages); field(" allocated_bytes=", final.allocated_bytes);
    field(" free_bytes=", final.free_bytes); text("\r\n[TEST] VM self-test passed\r\n");
}
