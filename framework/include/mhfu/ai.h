/* AI events for monsters on the generic AI engine: priority chains where each handler gets
 * the previous one's value and returns the value to forward (the input unchanged abstains).
 *
 * A big monster runs on two channels. These hooks reach the ANIMATION channel: the executor
 * (MHFU_ACTION_EXECUTOR) picks the clip its body slots play. The MOVE, with its hitbox and
 * damage, is ENTITY.MAIN_STATE / SUB_STATE, written by MHFU_ACT_SET; forcing an animation
 * does not change what an attack does. */
#ifndef MHFU_AI_H
#define MHFU_AI_H

#include <stdint.h>
#include "events.h"

#ifdef __cplusplus
extern "C" {
#endif

/* --- contexts and handlers, one pair per event ---------------------------------------- */

/* on_ai_overlay_loaded, postfix on MHFU_OVERLAY_LOAD_CALL: the overlay's bytes are in RAM and
 * not yet translated, the window to patch overlay code. Observe only. */
typedef struct {
    uint32_t dest;    /* MHFU_AI_OVERLAY */
    uint32_t src;     /* 0: not introspectable */
    uint32_t size;
} mhfu_ai_overlay_loaded_ctx_t;
typedef void (*mhfu_ai_overlay_loaded_cb_t)(const mhfu_ai_overlay_loaded_ctx_t *ctx);

/* on_bigmonster_slot_picked, before each pass of the slot loop (MHFU_SLOT_LOOP, and
 * MHFU_BIGMON_SLOT_LOOP once the overlay loads): return the slot to process. A slot outside
 * 0..action_count-1 crashes the engine. */
typedef struct {
    uint32_t entity_ptr;
    uint8_t  monster_type;
    uint8_t  original_slot;
    uint16_t action_count;   /* ENTITY.SLOT_COUNT */
} mhfu_bigmonster_slot_picked_ctx_t;
typedef uint8_t (*mhfu_bigmonster_slot_picked_cb_t)(
    const mhfu_bigmonster_slot_picked_ctx_t *ctx, uint8_t current_slot);

/* on_bigmonster_action_input, before the vt[8] picker (a vtable swap, JIT-immune): return
 * the input it reads (low 16 bits). Fires for one body slot only, so forcing here while the
 * engine drives the others desyncs the body; force through on_bigmonster_action instead. */
typedef struct {
    uint32_t entity_ptr;
    uint8_t  monster_type;
    uint8_t  slot;
    uint16_t vt8_input;
} mhfu_bigmonster_action_input_ctx_t;
typedef uint16_t (*mhfu_bigmonster_action_input_cb_t)(
    const mhfu_bigmonster_action_input_ctx_t *ctx, uint16_t current_input);

/* on_bigmonster_action_decided, after the vt[8] picker: return its outcome. Popo, Anteka
 * and Giadrome return a u16 action id. Tigrex returns a pointer into ENTITY.ACTION_TABLE
 * that changes per run: cache it per input (mhfu_ai_action_ptr_for) and never return a small
 * literal. The picker has already written the slot's bookkeeping, so an outcome it did not
 * pick can desync. */
typedef struct {
    uint32_t entity_ptr;
    uint8_t  monster_type;
    uint8_t  slot;
    uint16_t vt8_input;       /* ENTITY.SLOT_INPUTS[slot] */
} mhfu_bigmonster_action_decided_ctx_t;
typedef uint32_t (*mhfu_bigmonster_action_decided_cb_t)(
    const mhfu_bigmonster_action_decided_ctx_t *ctx, uint32_t engine_action_id);

/* on_bigmonster_ai_step, prefix on MHFU_AI_TICK, every frame. Observe only. */
typedef struct {
    uint32_t entity_ptr;
    uint8_t  monster_type;
    uint8_t  _pad[3];
} mhfu_bigmonster_ai_step_ctx_t;
typedef void (*mhfu_bigmonster_ai_step_cb_t)(const mhfu_bigmonster_ai_step_ctx_t *ctx);

/* on_bigmonster_action, entry detour on the shared executor (MHFU_ACTION_EXECUTOR): return
 * the action id to run. The engine fans the one id out to every body slot, so any valid id
 * is coherent: action_id = vt8_input(slot 0) - 0x3E8 = vt8_input(slot 2) - 0x578. Check
 * monster_type, since other entities use the executor too. */
typedef struct {
    uint32_t entity_ptr;
    uint8_t  monster_type;
    uint16_t action_id;
} mhfu_bigmonster_action_ctx_t;
typedef uint32_t (*mhfu_bigmonster_action_cb_t)(
    const mhfu_bigmonster_action_ctx_t *ctx, uint32_t action_id);

/* Spawn, death and damage come from the 5 Hz registry poll; big monsters only. */
typedef struct {
    uint32_t entity_ptr;
    int      slot;          /* registry slot, 1..20 */
    uint8_t  monster_type;
    uint8_t  _pad[3];
    uint16_t initial_hp;
} mhfu_bigmonster_spawn_ctx_t;

typedef struct {
    uint32_t entity_ptr;
    int      slot;
    uint8_t  monster_type;
    uint8_t  _pad[3];
} mhfu_bigmonster_death_ctx_t;

/* An HP drop between two polls: hits inside one poll add up, and the engine's own flinch has
 * already happened (intercept that with on_bigmonster_action). For reacting to being hit. */
typedef struct {
    uint32_t entity_ptr;
    int      slot;
    uint8_t  monster_type;
    uint8_t  _pad[3];
    uint16_t hp;            /* after the drop */
    uint16_t prev_hp;       /* at the previous poll */
    uint16_t amount;        /* prev_hp - hp, > 0 */
    uint16_t _pad2;
} mhfu_bigmonster_damaged_ctx_t;

typedef void (*mhfu_bigmonster_spawn_cb_t)(const mhfu_bigmonster_spawn_ctx_t *ctx);
typedef void (*mhfu_bigmonster_death_cb_t)(const mhfu_bigmonster_death_ctx_t *ctx);
typedef void (*mhfu_bigmonster_damaged_cb_t)(const mhfu_bigmonster_damaged_ctx_t *ctx);

/* --- registration (events.h): higher priority runs first, ties in registration order --- */

static inline mhfu_hook_rc_t mhfu_on_ai_overlay_loaded(
    mhfu_ai_overlay_loaded_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_AI_OVERLAY_LOADED, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_slot_picked(
    mhfu_bigmonster_slot_picked_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_SLOT_PICKED, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_action_input(
    mhfu_bigmonster_action_input_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_ACTION_INPUT, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_action_decided(
    mhfu_bigmonster_action_decided_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_ACTION_DECIDED, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_ai_step(
    mhfu_bigmonster_ai_step_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_AI_STEP, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_action(
    mhfu_bigmonster_action_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_ACTION, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_spawn(
    mhfu_bigmonster_spawn_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_SPAWN, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_death(
    mhfu_bigmonster_death_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_DEATH, (mhfu_event_fn_t)cb, priority, owner);
}
static inline mhfu_hook_rc_t mhfu_on_bigmonster_damaged(
    mhfu_bigmonster_damaged_cb_t cb, int priority, const char *owner) {
    return mhfu_event_on(MHFU_EVENT_BIGMONSTER_DAMAGED, (mhfu_event_fn_t)cb, priority, owner);
}

/* What the picker last returned for vt8_input on this monster type (a pointer for Tigrex;
 * every pick is cached, 96 per type), or 0 before the engine has picked it once. */
uint32_t mhfu_ai_action_ptr_for(uint8_t monster_type, uint16_t vt8_input);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_AI_H */
