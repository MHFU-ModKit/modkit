/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The event registry (mhfu/events.h): each event's callbacks with their owners, highest
 * priority first, and the hook each event needs, installed for its first subscriber.
 * Writers change a table with thread dispatch suspended, so a write is atomic on the one
 * CPU; readers copy it lock-free and copy again when a write landed meanwhile. Free of PSP
 * headers under MHFU_HOST, for the host test. */
#include <string.h>

#include "mhfu/events.h"
#include "mhfu/log.h"
#include "internal.h"

/* ThreadManForUser, not the interrupt calls: Kernel_Library would split across the link's
 * two passes over libpspuser and leave libc's imports from it unbound. */
#ifndef MHFU_HOST
#include <pspthreadman.h>
static int  no_switch(void)  { return sceKernelSuspendDispatchThread(); }
static void switch_on(int s) { sceKernelResumeDispatchThread(s); }
static void nap(void)        { sceKernelDelayThread(1000); }
#else
static int  no_switch(void)  { return 0; }
static void switch_on(int)   {}
static void nap(void)        {}
#endif

#define BARRIER() __asm__ __volatile__("" ::: "memory")

enum { INST_NONE, INST_BUSY, INST_DONE };

typedef struct {
    mhfu_handler_t    h[MHFU_EVENT_MAX_HANDLERS];
    volatile int      n;
    volatile uint32_t seq;    /* bumped by every write */
    volatile int      inst;
} event_t;

static event_t g_events[MHFU_EVENT_COUNT_];

static int valid(mhfu_event_id_t id) { return (unsigned)id < (unsigned)MHFU_EVENT_COUNT_; }

/* The hook an event needs; 0 when there is none or it is in place, <0 when it failed. The
 * quest anchors and the registry poll run from boot. An installer may subscribe to other
 * events, never to its own. */
static int install(mhfu_event_id_t id)
{
    switch (id) {
    case MHFU_EVENT_QUEST_TARGETS_BUILDING:    return mhfu_quest_install_targets();
    case MHFU_EVENT_AI_OVERLAY_LOADED:         return mhfu_ai_install_overlay_loaded();
    case MHFU_EVENT_BIGMONSTER_SLOT_PICKED:    return mhfu_ai_install_slot_picked();
    case MHFU_EVENT_BIGMONSTER_ACTION_INPUT:
    case MHFU_EVENT_BIGMONSTER_ACTION_DECIDED: return mhfu_ai_install_picker();
    case MHFU_EVENT_BIGMONSTER_AI_STEP:        return mhfu_ai_install_ai_step();
    case MHFU_EVENT_BIGMONSTER_ACTION:         return mhfu_ai_install_action();
    default:                                   return 0;
    }
}

/* Runs id's installer until one succeeds; a second thread waits for the first. */
static int install_once(mhfu_event_id_t id)
{
    event_t *e = &g_events[id];
    for (;;) {
        int f = no_switch();
        int st = e->inst;
        if (st == INST_NONE) e->inst = INST_BUSY;
        switch_on(f);
        if (st == INST_DONE) return 0;
        if (st == INST_NONE) break;
        nap();
    }
    int rc = install(id);
    e->inst = rc < 0 ? INST_NONE : INST_DONE;
    if (rc < 0) mhfu_log("[events] event %d: hook not installed (rc=%d)", (int)id, rc);
    return rc < 0 ? -1 : 0;
}

/* Called with dispatch suspended. */
static int index_of(const event_t *e, mhfu_event_fn_t cb, const char *owner)
{
    for (int i = 0; i < e->n; i++)
        if (e->h[i].fn == cb && strcmp(e->h[i].owner, owner) == 0) return i;
    return -1;
}

static mhfu_hook_rc_t insert(event_t *e, mhfu_event_fn_t cb, int priority, const char *owner)
{
    if (index_of(e, cb, owner) >= 0) return MHFU_HOOK_OK;
    if (e->n >= MHFU_EVENT_MAX_HANDLERS) return MHFU_HOOK_NOSPACE;
    int pos = e->n;
    while (pos > 0 && e->h[pos - 1].priority < priority) {   /* after equal priorities */
        e->h[pos] = e->h[pos - 1];
        pos--;
    }
    e->h[pos].fn = cb;
    e->h[pos].priority = priority;
    e->h[pos].owner = owner;
    e->n = e->n + 1;
    e->seq = e->seq + 1;
    return MHFU_HOOK_OK;
}

static void remove_owner(event_t *e, const char *owner)
{
    int k = 0;
    for (int i = 0; i < e->n; i++)
        if (strcmp(e->h[i].owner, owner) != 0) e->h[k++] = e->h[i];
    if (k == e->n) return;
    e->n = k;
    e->seq = e->seq + 1;
}

extern "C" {

mhfu_hook_rc_t mhfu_event_on(mhfu_event_id_t id, mhfu_event_fn_t cb, int priority,
                             const char *owner)
{
    if (!valid(id) || !cb || !owner || !*owner) return MHFU_HOOK_BADARG;
    event_t *e = &g_events[id];

    int f = no_switch();
    int dup  = index_of(e, cb, owner) >= 0;
    int full = e->n >= MHFU_EVENT_MAX_HANDLERS;
    switch_on(f);
    if (dup) return MHFU_HOOK_OK;
    if (full) return MHFU_HOOK_NOSPACE;
    if (install_once(id) != 0) return MHFU_HOOK_CONFLICT;

    f = no_switch();
    mhfu_hook_rc_t rc = insert(e, cb, priority, owner);
    switch_on(f);
    return rc;
}

void mhfu_event_release(const char *owner)
{
    if (!owner) return;
    for (int id = 0; id < MHFU_EVENT_COUNT_; id++) {
        int f = no_switch();
        remove_owner(&g_events[id], owner);
        switch_on(f);
    }
}

int mhfu_event_handlers(mhfu_event_id_t id, mhfu_handler_t *out)
{
    if (!valid(id)) return 0;
    const event_t *e = &g_events[id];
    for (;;) {
        uint32_t seq = e->seq;
        BARRIER();
        int n = e->n;
        for (int i = 0; i < n; i++) out[i] = e->h[i];
        BARRIER();
        if (e->seq == seq) return n;
    }
}

int mhfu_event_count(mhfu_event_id_t id)
{
    return valid(id) ? g_events[id].n : 0;
}

void mhfu_event_fire(mhfu_event_id_t id, const void *ctx)
{
    mhfu_handler_t h[MHFU_EVENT_MAX_HANDLERS];
    int n = mhfu_event_handlers(id, h);
    for (int i = 0; i < n; i++) ((void (*)(const void *))h[i].fn)(ctx);
}

} /* extern "C" */
