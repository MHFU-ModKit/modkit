/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Code cave: a bump allocator over one RWX BSS block for wrappers and stubs; callers flush
 * the caches after writing (mhfu_hook_flush_caches). */
#include "internal.h"

#define CAVE_WORDS 1024   /* 4 KiB of instruction space */

__attribute__((aligned(64)))
static uint32_t g_cave[CAVE_WORDS];
static int      g_used = 0;

extern "C" uint32_t *mhfu_cave_alloc(int n_insns)
{
    if (n_insns <= 0) return 0;
    /* 4-word aligned chunks */
    int n = (n_insns + 3) & ~3;
    if (g_used + n > CAVE_WORDS) return 0;
    uint32_t *p = &g_cave[g_used];
    g_used += n;
    return p;
}
