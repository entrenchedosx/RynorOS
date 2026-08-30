#ifndef RYNORKERNEL_CPU_H
#define RYNORKERNEL_CPU_H

typedef unsigned long long cpu_u64;
typedef unsigned int cpu_u32;
typedef unsigned short cpu_u16;
typedef unsigned char cpu_u8;

#define CPU_CODE_SELECTOR 0x08
#define CPU_DATA_SELECTOR 0x10

/* Low-to-high layout at exception_common's RSP; see docs/design/cpu.md. */
struct exception_frame {
    cpu_u64 r15, r14, r13, r12, r11, r10, r9, r8;
    cpu_u64 rdi, rsi, rbp, rdx, rcx, rbx, rax;
    cpu_u64 vector, error;
    cpu_u64 rip, cs, rflags, rsp, ss;
};

_Static_assert(sizeof(cpu_u64) == 8, "64-bit CPU word required");
_Static_assert(sizeof(struct exception_frame) == 176, "assembly frame size");
_Static_assert(__builtin_offsetof(struct exception_frame, vector) == 120, "vector offset");
_Static_assert(__builtin_offsetof(struct exception_frame, rip) == 136, "RIP offset");
_Static_assert(__builtin_offsetof(struct exception_frame, rsp) == 160, "RSP offset");

int cpu_initialize(void);
void cpu_exception_self_test(void);
void exception_dispatch(const struct exception_frame *frame, cpu_u64 cr2);
__attribute__((noreturn)) void cpu_halt(void);

#endif
