/* Slice C probe: cooperative infinite loop (terminate target).
 * Yields forever; the driver terminates it and expects ABORTED. Never
 * exits on its own: any exit code other than "still running" is a bug. */
#include "rt.h"

int rt_main(void)
{
    for (;;) {
        if (rt_nap(1) != RT_OK)
            rt_exit(98);
    }
    return 0;
}
