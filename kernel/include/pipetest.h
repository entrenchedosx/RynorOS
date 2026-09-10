#ifndef RYNOR_PIPETEST_H
#define RYNOR_PIPETEST_H

/* Stage 18d Slice D gated self-test driver: stateless fread matrix,
 * final discovery, kernel-owned pipes, atomic spawn_pipe, and true
 * concurrent streaming proof. Runs after proc_self_test only when the
 * image is built with RYNOR_PIPE_TEST=1 (test images); with 0 it is
 * never called and prints nothing, so default transcripts stay
 * byte-identical. See kernel/core/pipe-test.c. */
#ifndef RYNOR_PIPE_TEST
#define RYNOR_PIPE_TEST 0
#endif
void pipe_self_test(void);

#endif
