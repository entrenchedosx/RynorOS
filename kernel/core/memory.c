/* Freestanding compiler-required structure copy/initialization support.
   Callers supply valid, nonoverlapping objects for memcpy (not memmove).
   Volatile byte accesses prevent lowering these implementations into themselves.
   This is original guest code, not a host libc dependency. */
typedef __SIZE_TYPE__ size_t;
void *memcpy(void *restrict destination, const void *restrict source, size_t size)
{
    volatile unsigned char *to = destination;
    const volatile unsigned char *from = source;
    for (size_t i = 0; i < size; ++i) to[i] = from[i];
    return destination;
}
void *memset(void *destination, int value, size_t size)
{
    volatile unsigned char *to = destination;
    for (size_t i = 0; i < size; ++i) to[i] = (unsigned char)value;
    return destination;
}
