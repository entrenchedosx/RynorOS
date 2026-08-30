#ifndef RYNORKERNEL_SERIAL_H
#define RYNORKERNEL_SERIAL_H

/* Stage 1 internal API: COM1, polled output, returns zero on polling exhaustion. */
void serial_init(void);
int serial_write(const char *text);
int serial_flush(void);

#endif
