/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The registry poll's memory: the pointer each slot last reported. Free of PSP headers, so the
 * host test compiles it (tests/registry_host.cpp). The registry is emptied when a quest ends and
 * refilled when the next loads, both while the poll sleeps (it runs only in an area), and the
 * engine gives a monster the address it had before; so a new quest makes the poll forget, or its
 * monsters read as unchanged and are never reported. */
#ifndef MHFU_CORE_REGISTRY_WATCH_H
#define MHFU_CORE_REGISTRY_WATCH_H

#include <stdint.h>
#include "addresses.gen.h"

typedef struct {
    uint32_t          last[MHFU_ENTITY_REGISTRY_COUNT];   /* the poll thread's alone */
    volatile uint32_t forget;                             /* set from any thread */
} registry_watch_t;

static inline void watch_forget(registry_watch_t *w) { w->forget = 1; }

/* the start of a poll pass: carries out a forget on the poll's own thread */
static inline void watch_begin(registry_watch_t *w)
{
    if (!w->forget) return;
    w->forget = 0;
    for (int k = 0; k < MHFU_ENTITY_REGISTRY_COUNT; k++) w->last[k] = 0;
}

/* `cur` is what slot holds (0 for empty): 1 when it is a monster to report */
static inline int watch_spawned(registry_watch_t *w, int slot, uint32_t cur)
{
    if (cur == w->last[slot]) return 0;
    w->last[slot] = cur;
    return cur != 0;
}

#endif
