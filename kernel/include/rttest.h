#ifndef RYNOR_RTTEST_H
#define RYNOR_RTTEST_H

/* Stage 18c runtime-library conformance driver: loads rt programs
 * (built against user/lib/rt) through the 18b loader into CPL3 and checks
 * their [RT] evidence. Always terminates the transcript with either
 * [RT] rt verified or [RT] no image, skipped. See
 * docs/design/native-runtime.md. */

void rt_self_test(void);

#endif
