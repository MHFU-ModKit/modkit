/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Monster events (mhfu/monster_events.h). The step side runs on the game thread inside the
 * engine's AI step: it compares cells, appends to a ring and never logs. The poll side empties the
 * ring into the event registry. One writer and one reader: the step moves HEAD after writing a
 * record, the poll moves TAIL after reading one. The block lives in partition memory, like the
 * move player's, because thread stacks reach into the PRX image. */
#include "mhfu/monster_events.h"
#include "mhfu/memory.h"
#include "mhfu/log.h"
#include "addresses.gen.h"
#include "internal.h"
#include <stddef.h>

#ifndef MHFU_HOST
#include <pspsysmem.h>
#include <pspthreadman.h>
static uint32_t now_usec(void) { return sceKernelGetSystemTimeLow(); }
#else
extern "C" uint32_t mhfu_host_usec(void);
static uint32_t now_usec(void) { return mhfu_host_usec(); }
#endif

#define OWNER "mhfu_events"
#define MAGIC 0x45564E54u   /* 'EVNT' */
#define DEAD  5             /* MAIN_STATE of a dead monster */
#define CAUSE_FLAG 0x8u     /* ENTITY.FLAGS bit EYE_UPDATE wants */
#define TAIL_BIT 0x1u       /* ENTITY.SEVERED */

static_assert(sizeof(mhfu_monster_event_t) == MHFU_MONSTER_EVENT_SIZE, "MONSTER_EVENT layout");
static_assert(offsetof(mhfu_monster_event_t, kind) == MHFU_MONSTER_EVENT_KIND, "MONSTER_EVENT layout");
static_assert(offsetof(mhfu_monster_event_t, data) == MHFU_MONSTER_EVENT_DATA, "MONSTER_EVENT layout");
static_assert(sizeof(mhfu_monster_watch_t) == MHFU_MONSTER_WATCH_SIZE, "MONSTER_WATCH layout");
static_assert(offsetof(mhfu_monster_watch_t, edges) == MHFU_MONSTER_WATCH_EDGES, "MONSTER_WATCH layout");
static_assert(offsetof(mhfu_monster_events_t, watch) == MHFU_MONSTER_EVENTS_WATCH, "MONSTER_EVENTS layout");
static_assert(offsetof(mhfu_monster_events_t, ring) == MHFU_MONSTER_EVENTS_RING, "MONSTER_EVENTS layout");
static_assert(sizeof(mhfu_monster_events_t) == MHFU_MONSTER_EVENTS_SIZE, "MONSTER_EVENTS layout");
static_assert(MHFU_MONSTER_EVENTS_RING_COUNT == MHFU_MONSTER_EVENT_RING, "MONSTER_EVENTS layout");
static_assert(MHFU_MONSTER_EVENTS_WATCH_COUNT == MHFU_MONSTER_EVENT_WATCH, "MONSTER_EVENTS layout");
static_assert(MHFU_EVENT_BIGMONSTER_TAIL_CUT - MHFU_EVENT_BIGMONSTER_NOTICED
              == MHFU_MONSTER_TAIL_CUT - MHFU_MONSTER_NOTICED, "kind order is event order");

static volatile mhfu_monster_events_t *B;

/* --- the step side ----------------------------------------------------------------------- */

/* the player's bit in a monster's AWARE */
static uint8_t player_bit(void)
{
    return (uint8_t)(1u << (mhfu_mem_read_u16(MHFU_PLAYER_ENTITY + MHFU_ENTITY_HUNTER_INDEX) & 7));
}

/* EYE_UPDATE's rule for the player and this monster: the yellow eye beside the name */
static uint8_t in_combat(uint32_t ent, uint8_t aware)
{
    return mhfu_mem_read_u8(ent + MHFU_ENTITY_COMBAT_MODE) == 1
        && (aware & player_bit())
        && mhfu_mem_read_u16(ent + MHFU_ENTITY_SECTION)
               == mhfu_mem_read_u16(MHFU_PLAYER_ENTITY + MHFU_ENTITY_SECTION)
        && (mhfu_mem_read_u32(ent + MHFU_ENTITY_FLAGS) & CAUSE_FLAG)
        && mhfu_mem_read_u8(ent + MHFU_ENTITY_MAIN_STATE) != DEAD;
}

static volatile mhfu_monster_watch_t *watch_of(uint32_t ent, int *fresh)
{
    *fresh = 0;
    volatile mhfu_monster_watch_t *w = 0;
    for (int i = 0; i < MHFU_MONSTER_EVENT_WATCH; i++) {
        if (B->watch[i].entity == ent) return &B->watch[i];
        if (!w || B->watch[i].frames < w->frames) w = &B->watch[i];   /* free, else least seen */
    }
    w->entity = ent;
    w->frames = 0;
    *fresh = 1;
    return w;
}

static void put(volatile mhfu_monster_watch_t *w, uint32_t ent, uint8_t kind, uint8_t part,
                uint16_t data, uint32_t usec)
{
    w->edges = (uint16_t)(w->edges | (1u << kind));
    if (B->head - B->tail >= MHFU_MONSTER_EVENT_RING) {
        B->dropped++;
        return;
    }
    volatile mhfu_monster_event_t *e = &B->ring[B->head % MHFU_MONSTER_EVENT_RING];
    e->entity = ent;
    e->frame = w->frames;
    e->usec = usec;
    e->kind = kind;
    e->part = part;
    e->main_state = mhfu_mem_read_u8(ent + MHFU_ENTITY_MAIN_STATE);
    e->sub_state = mhfu_mem_read_u8(ent + MHFU_ENTITY_SUB_STATE);
    e->data = data;
    e->_pad = 0;
    B->head++;
}

static uint8_t lowest(uint16_t bits)
{
    for (uint8_t k = 0; k < 16; k++)
        if (bits & (1u << k)) return k;
    return MHFU_MONSTER_NO_PART;
}

extern "C" void mhfu_monster_events_frame(uint32_t ent, int stale)
{
    if (!B || !ent) return;
    int fresh;
    volatile mhfu_monster_watch_t *w = watch_of(ent, &fresh);
    w->frames++;
    w->edges = 0;
    uint8_t aware = mhfu_mem_read_u8(ent + MHFU_ENTITY_AWARE);
    uint8_t combat = in_combat(ent, aware);
    uint8_t severed = (uint8_t)(mhfu_mem_read_u8(ent + MHFU_ENTITY_SEVERED) & TAIL_BIT);
    uint16_t broken = mhfu_mem_read_u16(ent + MHFU_ENTITY_BROKEN);
    uint8_t flinched = stale ? 0 : mhfu_mem_read_u8(ent + MHFU_ENTITY_FLINCH_MASK);
    if (!fresh) {
        uint32_t usec = now_usec();
        uint8_t part = lowest(flinched);
        if (aware & ~w->aware & player_bit())
            put(w, ent, MHFU_MONSTER_NOTICED, MHFU_MONSTER_NO_PART, aware, usec);
        if (combat != w->combat)
            put(w, ent, combat ? MHFU_MONSTER_COMBAT_ENTERED : MHFU_MONSTER_COMBAT_LEFT,
                MHFU_MONSTER_NO_PART, combat, usec);
        if (flinched) put(w, ent, MHFU_MONSTER_FLINCH, part, flinched, usec);
        if (broken & ~w->broken)
            put(w, ent, MHFU_MONSTER_PART_BROKEN, part, (uint16_t)(broken & ~w->broken), usec);
        if (severed && !w->severed)
            put(w, ent, MHFU_MONSTER_TAIL_CUT, MHFU_MONSTER_NO_PART,
                mhfu_mem_read_u8(ent + MHFU_ENTITY_SEVER_COUNT), usec);
    }
    w->aware = aware;
    w->combat = combat;
    w->severed = severed;
    w->broken = broken;
}

/* --- the poll side ----------------------------------------------------------------------- */

extern "C" void mhfu_monster_events_drain(void)
{
    if (!B) return;
    while (B->tail != B->head) {
        mhfu_monster_event_ctx_t c;
        const volatile mhfu_monster_event_t *e = &B->ring[B->tail % MHFU_MONSTER_EVENT_RING];
        c.ev.entity = e->entity;
        c.ev.frame = e->frame;
        c.ev.usec = e->usec;
        c.ev.kind = e->kind;
        c.ev.part = e->part;
        c.ev.main_state = e->main_state;
        c.ev.sub_state = e->sub_state;
        c.ev.data = e->data;
        c.ev._pad = 0;
        B->tail++;
        if (c.ev.kind < MHFU_MONSTER_NOTICED || c.ev.kind > MHFU_MONSTER_TAIL_CUT) continue;
        c.event_id = (mhfu_event_id_t)(MHFU_EVENT_BIGMONSTER_NOTICED + c.ev.kind - 1);
        c.delay = now_usec() - c.ev.usec;
        mhfu_event_fire(c.event_id, &c);
    }
}

extern "C" const volatile mhfu_monster_events_t *mhfu_monster_events(void) { return B; }

static void reset(void)
{
    for (int i = 0; i < MHFU_MONSTER_EVENT_WATCH; i++) {
        B->watch[i].entity = 0;
        B->watch[i].frames = 0;
    }
    B->tail = B->head;
}

static void on_quest(const mhfu_event_ctx_t *ctx)
{
    (void)ctx;
    reset();
}

#ifndef MHFU_HOST
extern "C" int mhfu_monster_events_init(void)
{
    if (B) return 0;
    SceUID blk = sceKernelAllocPartitionMemory(2, OWNER, PSP_SMEM_Low,
                                               sizeof(mhfu_monster_events_t) + 64, 0);
    if (blk < 0) {
        mhfu_log("[%s] partition alloc FAILED (%d)", OWNER, (int)blk);
        return -1;
    }
    uintptr_t at = ((uintptr_t)sceKernelGetBlockHeadAddr(blk) + 63) & ~(uintptr_t)63;
    B = (volatile mhfu_monster_events_t *)at;
    volatile uint8_t *b = (volatile uint8_t *)B;
    for (unsigned k = 0; k < sizeof(mhfu_monster_events_t); k++) b[k] = 0;
    B->magic = MAGIC;
    if (mhfu_on_quest_beginning(on_quest, 0, OWNER) != MHFU_HOOK_OK)
        mhfu_log("[%s] quest event registration failed", OWNER);
    mhfu_log("[%s] block @0x%08X", OWNER, (unsigned)at);
    return 0;
}
#else
static mhfu_monster_events_t g_host_block;
extern "C" int mhfu_monster_events_init(void)
{
    B = &g_host_block;
    volatile uint8_t *b = (volatile uint8_t *)B;
    for (unsigned k = 0; k < sizeof(mhfu_monster_events_t); k++) b[k] = 0;
    B->magic = MAGIC;
    return 0;
}
extern "C" void mhfu_monster_events_host_quest(void) { on_quest(0); }
#endif
