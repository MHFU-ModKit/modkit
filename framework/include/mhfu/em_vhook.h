/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* em_vhook: the core wraps two slots of the spawned big monster's species vtable,
 * MONSTER_VTABLE.ENTER_ACTION (provisions a behaviour pair) and .AI_STEP (every frame).
 *
 *   substitution  the engine's (main, id) is rewritten before enter-action runs, so our
 *                 pair is provisioned like a native one;
 *   request       a pair entered on the game thread in the next AI frame, through
 *                 MHFU_ENTER_ACTION;
 *   rules         a 30 Hz brain in C: "in pair P (or own move M) for >= N frames, or on a
 *                 monster event, player distance in [lo, hi), receding or closing -> enter
 *                 (main, sub, mode) or play own move M'", with cooldown and budget; a rule on the
 *                 flinch plays its move in place of the host's reaction;
 *   own moves     a port's moves by slot, played by the move player (mhfu/move.h) when asked,
 *                 by a rule, or after the move before them;
 *   step          a C function on every AI frame, on the game thread, before the host step.
 *
 * Every call only writes the config block the stubs read, so it is safe from any thread,
 * and a no-op returning 0 while no vtable is wrapped. */
#ifndef MHFU_EM_VHOOK_H
#define MHFU_EM_VHOOK_H

#include <stdint.h>

#include "addresses.gen.h"
#include "mhfu/move.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_EM_SUBS   4    /* substitution entries */
#define MHFU_EM_RULES  MHFU_EM_CFG_RULES_COUNT   /* brain rules */
#define MHFU_EM_RING   8    /* last enter-action dispatches kept */

#define MHFU_EM_SUB_ANY   0xFEu        /* any sub state; 0xFF is the never-matching idle value */
#define MHFU_EM_ANY_PART  0xFFu        /* mhfu_em_rule_t.part: any part */
#define MHFU_EM_UNLIMITED 0xFFFFFFFFu  /* a fire budget that never runs out */

/* mhfu_em_rule_t.flags */
#define MHFU_EM_RULE_RECEDING 0x01   /* only while the distance grows */
#define MHFU_EM_RULE_CLOSING  0x02   /* only while the distance shrinks */

typedef struct {
    uint8_t  from_mask;    /* bit k set: may fire from main state k */
    uint8_t  from_sub;     /* exact sub, or MHFU_EM_SUB_ANY */
    uint8_t  to_main, to_sub, mode;
    uint8_t  flags;        /* MHFU_EM_RULE_* */
    uint8_t  from_move;    /* own move slot + 1: fires while it plays, its AI frames the dwell;
                            * 0: from the pair, which waits while any move plays */
    uint8_t  play_move;    /* own move slot + 1 played instead of entering (to_main, to_sub) */
    uint32_t min_frames;   /* the pair (or own move) must have stood this long */
    float    dist_lo, dist_hi;   /* player XZ distance window [lo, hi) */
    uint32_t cooldown;     /* frames between two fires */
    uint32_t count;        /* fires allowed; MHFU_EM_UNLIMITED for a standing rule */
    uint8_t  on;           /* MHFU_MONSTER_*: fires in the AI frame the event is seen, from_* and
                            * the distance still gating; MHFU_MONSTER_FLINCH plays its own move
                            * in place of the host's reaction; 0 none */
    uint8_t  part;         /* FLINCH, PART_BROKEN: only that part's; MHFU_EM_ANY_PART */
    uint8_t  force;        /* 1: play_move starts while the monster's notice runs (MOVE.FORCE) */
    uint8_t  _pad;
} mhfu_em_rule_t;

typedef struct {
    uint32_t installed;
    uint32_t ai_ticks, act_enters, last_pair, frames;
    float    dist;               /* player XZ distance at the last measurement */
    uint32_t sub_hits, sub_landed, sub_last_in;
    uint32_t brain_fires;
    uint32_t req_pending, req_done, req_result;
    uint32_t ring_idx;
    uint32_t ring[MHFU_EM_RING];  /* (subst<<24)|(mode<<16)|(main<<8)|sub, oldest at ring_idx+1 */
    uint32_t rule_fired[MHFU_EM_RULES];
    uint32_t rule_left[MHFU_EM_RULES];
    uint32_t sub_left[MHFU_EM_SUBS];
    uint32_t events_muted;       /* animation-event steps skipped (mhfu_em_mute_events) */
} mhfu_em_status_t;

/* 1 while a big monster's vtable is wrapped (from its spawn to the next quest_beginning). */
int  mhfu_em_installed(void);

/* Entry slot (0..MHFU_EM_SUBS-1): an enter-action whose main is in from_mask and whose id is
 * from_sub (or any) is entered as (to_main, to_sub) instead, count times; 0 clears it. */
void mhfu_em_substitute(int slot, uint8_t from_mask, uint8_t from_sub,
                        uint8_t to_main, uint8_t to_sub, uint32_t count);

/* Enter (main, sub, mode) on the next AI frame; 0 while nothing is wrapped. */
int  mhfu_em_request(uint8_t main_state, uint8_t sub_state, uint8_t mode);

/* Rule slot (0..MHFU_EM_RULES-1); NULL clears it. At most one rule fires an AI frame. While a
 * rule on the flinch is installed, the brain owns the reaction replacement (mhfu_move_react):
 * a flinch of a part a rule takes, in a frame its gates hold, enters its move's carrier instead,
 * and the move plays from the next AI frame (all such rules share the first's carrier). */
void mhfu_em_rule(int slot, const mhfu_em_rule_t *r);

/* fn(entity) runs at each AI step of the wrapped species, on the game thread, before the host
 * step; nonzero skips the host step this frame. One step at a time; 0 clears it, and so does
 * the quest's end. */
typedef uint32_t (*mhfu_em_step_fn)(uint32_t entity);
void mhfu_em_step(mhfu_em_step_fn fn);

/* The animation events (MONSTER_VTABLE.ANIM_EVENTS: the playing entry's attacks, effects and
 * sounds at clip frames) of entity are skipped until this is called again; 0 skips none. */
void mhfu_em_mute_events(uint32_t entity);

/* Drop every substitution, rule and pending request. */
void mhfu_em_clear(void);

void mhfu_em_status(mhfu_em_status_t *out);

/* --- own moves ------------------------------------------------------------------------------
 * A port's own moves, by slot, in partition memory. The brain, in C on the wrapped AI step, plays
 * one through the move player that same AI frame: one asked for (mhfu_em_play), else the AFTER of
 * one of ours that ended on its clip, its length or a wall (forced as that one was), else a
 * rule's. A slot with an AFTER ends into its carrier (its back pair), so the host brain does not
 * cut in before the next. A slot's FORCE is the call's, never the slot's. */
#define MHFU_EM_MOVES   16
#define MHFU_EM_KEYS    2048
#define MHFU_EM_NO_MOVE 0xFFu

/* struct EM_OWN in addresses.toml */
typedef struct {
    mhfu_move_t  move;
    mhfu_steer_t steer;
    uint16_t key_at, key_count;              /* its turn keys in mhfu_em_moves_t.keys */
    uint8_t  stuck_main, stuck_sub, stuck_mode;
    uint8_t  after;                          /* slot played when this one ends; MHFU_EM_NO_MOVE */
    uint32_t valid;
    uint32_t _pad;
} mhfu_em_own_t;

/* struct EM_MOVES in addresses.toml */
typedef struct {
    uint32_t magic;
    uint32_t req_ent, req_slot;              /* a play asked for, taken at req_ent's AI step */
    uint32_t tag_slot, tag_started;          /* the slot of the move player's move, if ours */
    uint32_t plays, chained;
    uint32_t key_top;
    mhfu_em_own_t moves[MHFU_EM_MOVES];
    uint16_t keys[MHFU_EM_KEYS];
    mhfu_steer_spec_t scratch;               /* the steering handed to the move player */
    uint32_t req_force;                      /* REQ_SLOT starts while the notice runs */
} mhfu_em_moves_t;

/* Empties every slot and the turn keys. */
void mhfu_em_moves_clear(void);
/* Slot `slot` plays mv, steered by s (its keys copied); `after` is a slot or MHFU_EM_NO_MOVE.
 * 0 for a bad slot or no room left for the keys. */
int  mhfu_em_move(int slot, const mhfu_move_t *mv, const mhfu_steer_spec_t *s, uint8_t after);
/* Plays slot on entity at its next AI step, forced past the notice wait (MOVE.FORCE) or not; 0
 * while nothing is wrapped or the slot is empty. */
int  mhfu_em_play(uint32_t entity, int slot, int force);
/* The slot of the move the move player runs (entering or playing), if it came from here; -1. */
int  mhfu_em_playing(void);
/* The registry, or 0 before the framework's init. */
const volatile mhfu_em_moves_t *mhfu_em_moves(void);

/* The reaction replacement, one entry, the move player's (mhfu_move_react): an enter-action of
 * entity with main_state and a sub in sub_mask (bit k = sub k), in an AI frame where the entity's
 * byte at offset gate shares a bit with parts, enters (to_main, to_sub) instead, unless a
 * substitution took it. Everything the engine did before the enter-action stands. entity 0 turns
 * it off. */
void mhfu_em_react(uint32_t entity, uint8_t main_state, uint32_t sub_mask, uint16_t gate,
                   uint8_t parts, uint8_t to_main, uint8_t to_sub);

/* Enter-actions the entry replaced since boot; last gets the latest's original
 * (mode << 16) | (main << 8) | sub. */
uint32_t mhfu_em_react_hits(uint32_t *last);

#ifdef __cplusplus
}
#endif
#endif /* MHFU_EM_VHOOK_H */
