/* Fan-out events: the framework installs the anchors and poll threads, and every callback
 * registered for an event runs, higher priority first (mhfu_hook_event in hooks.h). */
#ifndef MHFU_EVENTS_H
#define MHFU_EVENTS_H

#include <stdint.h>
#include "types.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MHFU_EVENT_QUEST_BEGINNING       = 0, /* the player commits to a quest */
    MHFU_EVENT_QUEST_ENTERED         = 1, /* area index becomes 98, the snowy-mountains camp */
    MHFU_EVENT_MAP_SECTION_ENTERED   = 2, /* the area index changed */
    MHFU_EVENT_MONSTER_SPAWNED       = 3, /* a new entity in registry slot 1 or above */
    MHFU_EVENT_QUEST_TARGETS_BUILDING = 4, /* edit the quest's monster list here (quest.h) */

    MHFU_EVENT_COUNT_
} mhfu_event_id_t;

/* Quest events: the registers at the anchor instruction. */
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
    int      slot;                /* registry slot, 1..20 */
    uint32_t entity_ptr;
    uint8_t  monster_type;        /* ENTITY.SPECIES */
    uint8_t  entity_id;           /* ENTITY.ID */
    uint16_t hp;                  /* ENTITY.HP */
    float    size_scale;          /* ENTITY.SIZE_SCALE */
} mhfu_monster_spawn_ctx_t;

/* Context of MHFU_EVENT_QUEST_TARGETS_BUILDING. */
typedef struct {
    mhfu_event_id_t event_id;
    mhfu_quest_t    quest;
} mhfu_quest_ctx_t;

/* The registry stores this; the typed aliases below check signatures at the caller. */
typedef void (*mhfu_event_cb_t)(const void *ctx);

typedef void (*mhfu_quest_cb_t)        (const mhfu_event_ctx_t *ctx);
typedef void (*mhfu_map_section_cb_t)  (const mhfu_map_section_ctx_t *ctx);
typedef void (*mhfu_monster_spawn_cb_t)(const mhfu_monster_spawn_ctx_t *ctx);

/* Priority 0. Returns 0, or negative on a bad id or a full event. */
int mhfu_event_register(mhfu_event_id_t event_id, mhfu_event_cb_t cb);

static inline int mhfu_on_quest_beginning(mhfu_quest_cb_t cb) {
    return mhfu_event_register(MHFU_EVENT_QUEST_BEGINNING,
                               (mhfu_event_cb_t)(void *)cb);
}
static inline int mhfu_on_quest_entered(mhfu_quest_cb_t cb) {
    return mhfu_event_register(MHFU_EVENT_QUEST_ENTERED,
                               (mhfu_event_cb_t)(void *)cb);
}
static inline int mhfu_on_map_section_entered(mhfu_map_section_cb_t cb) {
    return mhfu_event_register(MHFU_EVENT_MAP_SECTION_ENTERED,
                               (mhfu_event_cb_t)(void *)cb);
}
static inline int mhfu_on_monster_spawned(mhfu_monster_spawn_cb_t cb) {
    return mhfu_event_register(MHFU_EVENT_MONSTER_SPAWNED,
                               (mhfu_event_cb_t)(void *)cb);
}

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_EVENTS_H */
