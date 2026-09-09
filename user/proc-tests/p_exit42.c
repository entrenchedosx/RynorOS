/* Slice C probe: exit with a fixed code (spawn/wait/exit-status). */
#include "rt.h"

int rt_main(void)
{
    rt_exit(42);
    return 0;
}
