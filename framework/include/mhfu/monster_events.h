/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Monster events: the moments a big monster's state changes that a mod may answer. em_vhook's
 * brain reads the engine's own cells every AI frame of the species it wrapped, before the host
 * step, so a change the engine made in one AI frame is seen in the next, where the brain's rules
 * on it fire (mhfu_em_rule); the registry poll (5 Hz) raises it with that frame and the emulated
 * clock.
 *
 *   NOTICED          the player's ENTITY.AWARE bit rises: the notice (AWARENESS_ADD)
 *   COMBAT_ENTERED   the player's yellow eye comes on for this monster: COMBAT_MODE 1, the
 *   COMBAT_LEFT      player's AWARE bit, the same section, FLAGS & 0x8, alive (EYE_UPDATE); and off
 *   FLINCH           parts flinched (ENTITY.FLINCH_MASK); the pair is the reaction entered
 *   PART_BROKEN      ENTITY.BROKEN gains bits (BREAK_RECORD)
 *   TAIL_CUT         ENTITY.SEVERED bit 0 rises
 *   ENRAGED          ENTITY.FLAGS bit 0x20 rises: the monster enrages
 *   CALMED           and falls: it calms down
 *
 * A monster first seen sets the baseline and raises nothing. */
#ifndef MHFU_MONSTER_EVENTS_H
#define MHFU_MONSTER_EVENTS_H

#include <stdint.h>

#include "addresses.gen.h"
#include "mhfu/events.h"

#ifdef __cplusplus
extern "C" {
#endif

/* mhfu_monster_event_t.kind (enum MONSTER_EVENT_KIND in addresses.toml);
 * MHFU_EVENT_BIGMONSTER_NOTICED + kind - 1 is its event */
enum {
    MHFU_MONSTER_NOTICED        = MHFU_MONSTER_EVENT_KIND_NOTICED,
    MHFU_MONSTER_COMBAT_ENTERED = MHFU_MONSTER_EVENT_KIND_COMBAT_ENTERED,
    MHFU_MONSTER_COMBAT_LEFT    = MHFU_MONSTER_EVENT_KIND_COMBAT_LEFT,
    MHFU_MONSTER_FLINCH         = MHFU_MONSTER_EVENT_KIND_FLINCH,
    MHFU_MONSTER_PART_BROKEN    = MHFU_MONSTER_EVENT_KIND_PART_BROKEN,
    MHFU_MONSTER_TAIL_CUT       = MHFU_MONSTER_EVENT_KIND_TAIL_CUT,
    MHFU_MONSTER_ENRAGED        = MHFU_MONSTER_EVENT_KIND_ENRAGED,
    MHFU_MONSTER_CALMED         = MHFU_MONSTER_EVENT_KIND_CALMED,
};
#define MHFU_MONSTER_KINDS MHFU_MONSTER_EVENT_KIND_COUNT

/* ENTITY.FLAGS: the monster is enraged (the ENRAGED and CALMED events, the rules' conditions) */
#define MHFU_MONSTER_FLAG_ENRAGED 0x20u

/* the kinds' names, MHFU_MONSTER_NOTICED's first, then NULL: Lua's and a manifest's `on` */
extern const char *const mhfu_monster_event_names[];

#define MHFU_MONSTER_EVENT_RING  32
#define MHFU_MONSTER_EVENT_WATCH 2    /* monsters of the wrapped species followed at once */
#define MHFU_MONSTER_NO_PART     0xFFu

/* struct MONSTER_EVENT in addresses.toml */
typedef struct {
    uint32_t entity;
    uint32_t frame;     /* the monster's AI frame (its watch's FRAMES) the change was seen in */
    uint32_t usec;      /* the emulated clock then */
    uint8_t  kind;      /* MHFU_MONSTER_* */
    uint8_t  part;      /* FLINCH, PART_BROKEN: the lowest flinched part; else MHFU_MONSTER_NO_PART */
    uint8_t  main_state, sub_state;  /* the pair then; for FLINCH the reaction the engine entered */
    uint16_t data;      /* FLINCH the flinched parts, PART_BROKEN the new BROKEN bits,
                         * NOTICED the AWARE bits, TAIL_CUT ENTITY.SEVER_COUNT */
    uint16_t _pad;
} mhfu_monster_event_t;

/* struct MONSTER_WATCH: what the step compares against, per monster */
typedef struct {
    uint32_t entity;
    uint32_t frames;    /* AI frames seen */
    uint8_t  aware, combat, severed, enraged;
    uint16_t broken;
    uint16_t edges;     /* bit kind: what this frame raised */
} mhfu_monster_watch_t;

/* struct MONSTER_EVENTS: the block, in partition memory */
typedef struct {
    uint32_t magic;
    uint32_t head;      /* records written, by the step */
    uint32_t tail;      /* records raised, by the poll */
    uint32_t dropped;   /* records lost to a full ring */
    mhfu_monster_watch_t watch[MHFU_MONSTER_EVENT_WATCH];
    mhfu_monster_event_t ring[MHFU_MONSTER_EVENT_RING];
} mhfu_monster_events_t;

typedef struct {
    mhfu_event_id_t      event_id;
    mhfu_monster_event_t ev;
    uint32_t             delay;  /* usec from ev.usec to this call */
} mhfu_monster_event_ctx_t;

typedef void (*mhfu_monster_event_cb_t)(const mhfu_monster_event_ctx_t *ctx);

/* id is one of MHFU_EVENT_BIGMONSTER_NOTICED .. _CALMED; callbacks run on the poll thread. */
static inline mhfu_hook_rc_t mhfu_on_monster_event(mhfu_event_id_t id, mhfu_monster_event_cb_t cb,
                                                   int priority, const char *owner) {
    return mhfu_event_on(id, (mhfu_event_fn_t)cb, priority, owner);
}

/* A big monster now, from the cells the events step reads: what a mod asks before it plays. */
typedef struct {
    uint8_t  aware;      /* the player's ENTITY.AWARE bit: it has noticed the player */
    uint8_t  combat;     /* the player's yellow eye is on for it (EYE_UPDATE's rule) */
    uint8_t  noticing;   /* aware, COMBAT_MODE still 0: the notice runs; a move asked waits */
    uint8_t  dead;
    uint8_t  main_state, sub_state;
    uint8_t  flinched;   /* ENTITY.FLINCH_MASK: the parts that flinched this AI frame */
    uint8_t  severed;    /* the tail is cut */
    uint16_t broken;     /* ENTITY.BROKEN */
    uint16_t _pad;
} mhfu_monster_state_t;

/* 0 (and *out zeroed) for no entity. Any thread; it only reads. */
int mhfu_monster_state(uint32_t entity, mhfu_monster_state_t *out);

/* The brain's call, each AI frame of entity before the host step; stale: the host step did not
 * run in the frame before, so the per-frame cells are left over from an earlier one. Returns
 * what this frame raised (bit kind) and the parts that flinched in *parts. */
uint16_t mhfu_monster_events_frame(uint32_t entity, int stale, uint8_t *parts);

/* Raises what the step found, oldest first; the registry poll calls it. */
void mhfu_monster_events_drain(void);

/* The block, or 0 before the framework's init. */
const volatile mhfu_monster_events_t *mhfu_monster_events(void);

#ifdef __cplusplus
}
#endif
#endif /* MHFU_MONSTER_EVENTS_H */
