/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Events: one registry for every event the framework raises. A callback belongs to an owner,
 * the mod id, and the framework drops an owner's callbacks when that mod shuts down. Higher
 * priority runs first, ties in registration order. An event's hook is installed for its
 * first subscriber; a code patch then lands at the next title or menu screen. */
#ifndef MHFU_EVENTS_H
#define MHFU_EVENTS_H

#include <stdint.h>
#include "types.h"
#include "hooks.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MHFU_EVENT_QUEST_BEGINNING,          /* the player commits to a quest */
    MHFU_EVENT_QUEST_ENTERED,            /* the first area after QUEST_BEGINNING */
    MHFU_EVENT_MAP_SECTION_ENTERED,      /* the area index changed */
    MHFU_EVENT_MONSTER_SPAWNED,          /* a new entity in registry slot 1 or above */
    MHFU_EVENT_QUEST_TARGETS_BUILDING,   /* edit the quest's monster list here (quest.h) */
    /* big monsters, ai.h */
    MHFU_EVENT_AI_OVERLAY_LOADED,
    MHFU_EVENT_BIGMONSTER_SLOT_PICKED,
    MHFU_EVENT_BIGMONSTER_ACTION_INPUT,
    MHFU_EVENT_BIGMONSTER_ACTION_DECIDED,
    MHFU_EVENT_BIGMONSTER_AI_STEP,
    MHFU_EVENT_BIGMONSTER_ACTION,
    MHFU_EVENT_BIGMONSTER_SPAWN,
    MHFU_EVENT_BIGMONSTER_DEATH,
    MHFU_EVENT_BIGMONSTER_DAMAGED,
    /* big monsters, monster_events.h: in mhfu_monster_event_t.kind order */
    MHFU_EVENT_BIGMONSTER_NOTICED,
    MHFU_EVENT_BIGMONSTER_COMBAT_ENTERED,
    MHFU_EVENT_BIGMONSTER_COMBAT_LEFT,
    MHFU_EVENT_BIGMONSTER_FLINCH,
    MHFU_EVENT_BIGMONSTER_PART_BROKEN,
    MHFU_EVENT_BIGMONSTER_TAIL_CUT,
    MHFU_EVENT_BIGMONSTER_ENRAGED,
    MHFU_EVENT_BIGMONSTER_CALMED,

    MHFU_EVENT_COUNT_
} mhfu_event_id_t;

/* A callback of any event, stored untyped; the event calls it through its own type. */
typedef void (*mhfu_event_fn_t)(void);

/* BADARG on a bad id, callback or owner; NOSPACE when the event is full; CONFLICT when its
 * hook cannot be installed. The same owner and callback again is OK and adds nothing. The
 * owner string is kept, not copied: a mod id or another literal. */
mhfu_hook_rc_t mhfu_event_on(mhfu_event_id_t id, mhfu_event_fn_t cb, int priority,
                             const char *owner);

/* Drop every callback owner registered; the framework calls it on mod shutdown. */
void mhfu_event_release(const char *owner);

/* QUEST_BEGINNING and QUEST_ENTERED: the registers at the anchor instruction. */
typedef struct {
    mhfu_event_id_t event_id;
    uint32_t        cell_value;
    uint32_t a0, a1, a2, a3;
    uint32_t v0, v1;
    uint32_t ra, sp;
    uint32_t pc;
} mhfu_event_ctx_t;

typedef struct {
    mhfu_event_id_t event_id;
    uint16_t section_id;          /* new area index */
    uint16_t prev_section_id;
    uint32_t quest_timer;
    uint8_t  screen_state;
    uint8_t  is_in_quest_area;    /* screen_state == MHFU_WORLD_SCREEN_IN_AREA */
    uint16_t _pad;
} mhfu_map_section_ctx_t;

typedef struct {
    mhfu_event_id_t event_id;
    int      slot;                /* registry slot, 1..MHFU_ENTITY_REGISTRY_COUNT-1 */
    uint32_t entity_ptr;
    uint8_t  monster_type;        /* ENTITY.SPECIES */
    uint8_t  entity_id;           /* ENTITY.ID */
    uint16_t hp;                  /* ENTITY.HP */
    float    size_scale;          /* ENTITY.SIZE_SCALE */
} mhfu_monster_spawn_ctx_t;

typedef struct {
    mhfu_event_id_t event_id;
    mhfu_quest_t    quest;
} mhfu_quest_ctx_t;

typedef void (*mhfu_quest_cb_t)          (const mhfu_event_ctx_t *ctx);
typedef void (*mhfu_map_section_cb_t)    (const mhfu_map_section_ctx_t *ctx);
typedef void (*mhfu_monster_spawn_cb_t)  (const mhfu_monster_spawn_ctx_t *ctx);
typedef void (*mhfu_quest_targets_cb_t)  (const mhfu_quest_ctx_t *ctx);

static inline mhfu_hook_rc_t mhfu_on_quest_beginning(mhfu_quest_cb_t cb, int priority,
                                                     const char *owner) {
    return mhfu_event_on(MHFU_EVENT_QUEST_BEGINNING, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_quest_entered(mhfu_quest_cb_t cb, int priority,
                                                   const char *owner) {
    return mhfu_event_on(MHFU_EVENT_QUEST_ENTERED, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_map_section_entered(mhfu_map_section_cb_t cb,
                                                         int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_MAP_SECTION_ENTERED, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_monster_spawned(mhfu_monster_spawn_cb_t cb, int priority,
                                                     const char *owner) {
    return mhfu_event_on(MHFU_EVENT_MONSTER_SPAWNED, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_quest_targets_building(mhfu_quest_targets_cb_t cb,
                                                            int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_QUEST_TARGETS_BUILDING, (mhfu_event_fn_t)cb, priority,
                         owner);
}

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_EVENTS_H */
