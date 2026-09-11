/* Stage 18d Slice F arenas (CPL3, freestanding).
 *
 * Two logically distinct bump allocators with explicit reset:
 *
 * - session arena: survives successful commits only. Holds decoded
 *   bytes of committed persistent string values. Never reset on
 *   failure (rollback = don't touch); repacked wholesale on commit
 *   planning (see rl_sem.h) so no orphan can accumulate.
 * - submission arena: all transient per-item state (node pool, arg
 *   vectors, decoded scratch, eval temps). Reset per item during
 *   rebuild and wholesale after every submission attempt, on every
 *   path (success, all error classes, Ctrl-C aborts).
 *
 * Monotonic bump without reset is NOT sufficient: every use site
 * pairs allocation with a baseline mark and an unconditional reset.
 */
#ifndef RYNOR_RL_MEM_H
#define RYNOR_RL_MEM_H

struct rl_arena {
    unsigned char *base;
    unsigned int cap;
    unsigned int used;
    unsigned int high;
};

static void __attribute__((unused)) rl_arena_init(struct rl_arena *a, unsigned char *base,
                          unsigned int cap)
{
    a->base = base;
    a->cap = cap;
    a->used = 0;
    a->high = 0;
}

static unsigned int __attribute__((unused)) rl_arena_mark(const struct rl_arena *a)
{
    return a->used;
}

/* Bump n bytes (4-aligned). Returns byte offset, or ~0u when full. */
static unsigned int __attribute__((unused)) rl_arena_bump(struct rl_arena *a, unsigned int n)
{
    unsigned int at;
    n = (n + 3u) & ~3u;
    if (!a || !a->base) return ~0u;
    if (n > a->cap || a->used > a->cap - n) return ~0u;
    at = a->used;
    a->used += n;
    if (a->used > a->high) a->high = a->used;
    return at;
}

static void __attribute__((unused)) rl_arena_reset_to(struct rl_arena *a, unsigned int mark)
{
    if (mark <= a->used)
        a->used = mark;
}

static void __attribute__((unused)) rl_arena_reset(struct rl_arena *a)
{
    a->used = 0;
}

#endif
