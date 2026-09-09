/* Slice C probe: deliberate fault (ud2 -> FAULTED terminal record). */
#include "rt.h"

int rt_main(void)
{
    __asm__ volatile ("ud2" ::: "memory");
    rt_exit(99);
    return 0;
}
