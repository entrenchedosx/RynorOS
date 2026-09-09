#ifndef RYNOR_READTEST_H
#define RYNOR_READTEST_H

/* Stage 18d Slices A/B gated self-test driver. Runs only when the image
   is built with RYNOR_INPUT_TEST=1 (test images); with 0 it is never
   called and prints nothing, so default transcripts stay byte-identical.
   See kernel/core/read-test.c. */
#ifndef RYNOR_INPUT_TEST
#define RYNOR_INPUT_TEST 0
#endif
void read_self_test(void);

#endif
