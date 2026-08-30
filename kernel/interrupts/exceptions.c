#include "cpu.h"
#include "serial.h"

extern int cpu_test_trigger(void);
extern const char cpu_test_fault[], cpu_test_after[];
cpu_u64 cpu_test_rsp, cpu_test_return_flags;
static volatile unsigned int test_armed, handled_count, diagnostic_active;

static const char *const names[32] = {
    "divide_error", "debug", "nmi", "breakpoint", "overflow", "bound_range",
    "invalid_opcode", "device_unavailable", "double_fault", "reserved_09",
    "invalid_tss", "segment_not_present", "stack_fault", "general_protection",
    "page_fault", "reserved_15", "x87_floating_point", "alignment_check",
    "machine_check", "simd_floating_point", "virtualization", "control_protection",
    "reserved_22", "reserved_23", "reserved_24", "reserved_25", "reserved_26",
    "reserved_27", "hypervisor_injection", "vmm_communication", "security",
    "reserved_31"
};

static int has_error_code(cpu_u64 vector)
{
    return vector == 8 || (vector >= 10 && vector <= 14) || vector == 17 ||
           vector == 21 || vector == 29 || vector == 30;
}

static void emit(const char *text)
{
    if (!serial_write(text)) cpu_halt();
}

static void field(const char *label, cpu_u64 value)
{
    static const char digits[] = "0123456789abcdef";
    char text[19] = "0x0000000000000000";
    for (unsigned int i = 0; i < 16; ++i)
        text[17 - i] = digits[(value >> (4 * i)) & 15];
    emit(label);
    emit(text);
}

static void diagnose(const struct exception_frame *f, cpu_u64 cr2)
{
    char vector[3] = {(char)('0' + f->vector / 10), (char)('0' + f->vector % 10), 0};
    emit("[EXCEPTION] vector="); emit(vector);
    emit(" name="); emit(f->vector < 32 ? names[f->vector] : "unknown");
    emit(has_error_code(f->vector) ? " error_source=cpu" : " error_source=synthetic");
    field(" error=", f->error); emit("\r\n");
    emit("[STATE]");
    field(" rip=", f->rip); field(" cs=", f->cs); field(" rflags=", f->rflags);
    field(" rsp=", f->rsp); field(" ss=", f->ss); emit("\r\n");
    emit("[GPR]");
    field(" rax=", f->rax); field(" rbx=", f->rbx); field(" rcx=", f->rcx); field(" rdx=", f->rdx);
    emit("\r\n[GPR]");
    field(" rbp=", f->rbp); field(" rsi=", f->rsi); field(" rdi=", f->rdi); field(" r8=", f->r8);
    emit("\r\n[GPR]");
    field(" r9=", f->r9); field(" r10=", f->r10); field(" r11=", f->r11); field(" r12=", f->r12);
    emit("\r\n[GPR]");
    field(" r13=", f->r13); field(" r14=", f->r14); field(" r15=", f->r15); emit("\r\n");
    if (f->vector == 14) { emit("[PAGE]"); field(" cr2=", cr2); emit("\r\n"); }
}

static int expected_test_frame(const struct exception_frame *f, cpu_u64 cr2)
{
    const cpu_u64 saved[] = {f->rax, f->rbx, f->rcx, f->rdx, f->rbp, f->rsi, f->rdi,
                            f->r8, f->r9, f->r10, f->r11, f->r12, f->r13, f->r14, f->r15};
    for (unsigned int i = 0; i < 15; ++i) {
        cpu_u64 expected = 0x101 + i;
        if (RYNOR_TEST_VECTOR == 0 && i == 0) expected = 1;
        if (RYNOR_TEST_VECTOR == 0 && (i == 2 || i == 3)) expected = 0;
        if (RYNOR_TEST_VECTOR == 13 && i == 0) expected = 0x18;
        if (saved[i] != expected) return 0;
    }
    cpu_u64 rip = (cpu_u64)((RYNOR_TEST_VECTOR == 3 || RYNOR_TEST_VECTOR == 1) ?
                           cpu_test_after : cpu_test_fault);
    cpu_u64 flags = RYNOR_TEST_VECTOR == 3 ? 0x402 :
                   (RYNOR_TEST_VECTOR == 1 ? 0x102 : 0x10002);
    cpu_u64 error = RYNOR_TEST_VECTOR == 13 ? 0x18 : 0;
    return f->vector == RYNOR_TEST_VECTOR && f->rip == rip &&
           f->cs == CPU_CODE_SELECTOR && f->ss == CPU_DATA_SELECTOR &&
           f->rsp == cpu_test_rsp && f->rflags == flags && f->error == error &&
           (RYNOR_TEST_VECTOR != 14 || cr2 == 0x200000);
}

void exception_dispatch(const struct exception_frame *frame, cpu_u64 cr2)
{
    /* Best-effort recursion guard, not a substitute for an IST emergency stack. */
    if (diagnostic_active) {
        emit("[EXCEPTION] action=halt reason=nested\r\n");
        cpu_halt();
    }
    diagnostic_active = 1;
    diagnose(frame, cr2);
    if (!test_armed || handled_count != 0 || !expected_test_frame(frame, cr2)) {
        emit("[EXCEPTION] action=halt reason=unexpected\r\n");
        (void)serial_flush();
        cpu_halt();
    }
    test_armed = 0;
    handled_count = 1;
    if (RYNOR_TEST_VECTOR != 3) {
        /* Diagnostic test images stop here; faulting instructions are not retried. */
        emit("[EXCEPTION] action=halt\r\n[TEST] exception handling verified\r\n");
        (void)serial_flush();
        cpu_halt();
    }
    emit("[EXCEPTION] action=resume\r\n");
    diagnostic_active = 0;
    /* The common entry restores the original captured frame with IRETQ. */
}

void cpu_exception_self_test(void)
{
    handled_count = 0;
    test_armed = RYNOR_TEST_ARMED;
    emit("[TEST] triggering controlled exception\r\n");
    int restored = cpu_test_trigger();
    if (restored != 1 || handled_count != 1 || test_armed != 0) {
        emit("[TEST] exception handling failed\r\n");
        cpu_halt();
    }
    emit("[TEST] exception handling verified\r\n");
    (void)serial_flush();
}
