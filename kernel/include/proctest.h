#ifndef RYNOR_PROCTEST_H
#define RYNOR_PROCTEST_H

/* Stage 18d Slice C gated self-test driver. Runs only when the image
   is built with RYNOR_PROC_TEST=1 (test images); with 0 it is never
   called and prints nothing, so default transcripts stay byte-identical.
   See kernel/core/proc-test.c. */
#ifndef RYNOR_PROC_TEST
#define RYNOR_PROC_TEST 0
#endif
void proc_self_test(void);

#endif
