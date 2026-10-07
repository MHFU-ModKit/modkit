/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The move player: a big monster plays one of its executor entries as a move of its own.
 *
 * A move rides a native CARRIER pair, entered through the engine's enter-action so the host
 * provisions it and its handler runs; on the next AI step the move's entry goes to the executor,
 * which puts the clip on every body part. Each attack spawns when the timing part's cursor
 * crosses its frame, and an attack with an end frame is ended there (or when the move ends
 * first) through the node's own end state; without one it lives as long as its record says. The
 * move ends when its clip does (the carrier hands off to the brain, or
 * the move enters its back pair), after LENGTH frames, or when anything else changes the pair, a
 * reaction first of all. With SKIP the host AI step does not run while the clip plays, except
 * on a frame with a hit pending, so the brain cannot cut in; the host still takes the damage.
 * While a move plays, the host's animation events for the entry its clip sits in (attacks and
 * effects at the host clip's frames) are skipped, unless HOST_ATTACKS keeps them.
 *
 * A move asked for while the monster's notice runs (the player's ENTITY.AWARE bit set,
 * COMBAT_MODE still 0: the roar, the hub, the AI script's combat entry) waits for it, up to
 * MHFU_MOVE_WAIT AI frames, unless the call FORCEs it.
 *
 * A reaction can be replaced by a move (mhfu_move_react): the engine's own reaction runs up to
 * its enter-action (FLINCH_PARTS counters, pending damage into HP), which then enters the move's
 * carrier instead; the move plays from the next AI step.
 *
 * It runs on em_vhook's slot-29 step (mhfu_em_step), so the species must be wrapped
 * (mhfu_em_installed); the step is in from the species' spawn. A brain rule on the flinch
 * (mhfu_em_rule) takes the reaction replacement over. The calls only write the move block: safe
 * from any thread. */
#ifndef MHFU_MOVE_H
#define MHFU_MOVE_H

#include <stdint.h>

#include "mhfu/steer.h"
#include "mhfu/monster_events.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_MOVE_MAX_ATTACKS 4
#define MHFU_MOVE_NO_PAIR 0xFFu   /* back_main: no back pair, the carrier hands off itself */

/* struct MOVE_ATTACK and MOVE in addresses.toml */
typedef struct {
    uint16_t frame;   /* clip frame */
    uint16_t id;      /* the species' attack record */
    uint16_t end;     /* clip frame the node is ended at; 0 = its own life */
    uint16_t _pad;
} mhfu_move_attack_t;

typedef struct {
    uint16_t entry;                       /* executor entry: the clip */
    uint16_t length;                      /* AI frames from the dispatch; 0 = until the clip ends */
    uint8_t  carrier_main, carrier_sub;
    uint8_t  back_main, back_sub, back_mode;
    uint8_t  skip;
    uint8_t  part;                        /* body part whose cursor times the attacks */
    uint8_t  attack_count;
    mhfu_move_attack_t attacks[MHFU_MOVE_MAX_ATTACKS];
    uint32_t spawner;                     /* 0 = MHFU_TIGREX_ATTACK_SPAWN */
    uint8_t  host_attacks;                /* 1 keeps the host entry's animation events */
    uint8_t  force;                       /* this call: 1 starts while the monster's notice runs */
    uint8_t  _pad[2];
} mhfu_move_t;

/* MOVE_STATE.STATE */
enum { MHFU_MOVE_IDLE, MHFU_MOVE_ENTERING, MHFU_MOVE_PLAYING, MHFU_MOVE_AFTER, MHFU_MOVE_DONE };
/* MOVE_STATE.END */
enum {
    MHFU_MOVE_END_CLIP = 1,   /* the clip ended; the carrier handed off */
    MHFU_MOVE_END_BACK,       /* the clip or LENGTH ended; the back pair was entered */
    MHFU_MOVE_END_PAIR,       /* the pair changed under the move */
    MHFU_MOVE_END_STOPPED,
    MHFU_MOVE_END_REPLACED,
    MHFU_MOVE_END_REFUSED,    /* enter-action did not land the carrier */
    MHFU_MOVE_END_LOST,       /* another dispatch replaced the clip */
    MHFU_MOVE_END_WALL,       /* a wall ahead (mhfu/steer.h); the carrier or back pair takes over */
    MHFU_MOVE_END_STUCK,      /* a class-2 wall ahead: the spec's stuck pair was entered */
    MHFU_MOVE_END_REACTION,   /* a reaction mhfu_move_react replaces started its move */
};

#define MHFU_MOVE_WAIT 450      /* AI frames a move waits for the monster's notice to run */

/* mhfu_move_react kinds */
enum {
    MHFU_REACT_FLINCH,        /* em75's flinch pairs (4, 0|1|5|6|8) in an AI frame with FLINCH_MASK */
};

/* struct MOVE_STATE in addresses.toml */
typedef struct {
    uint32_t magic, started, pending;
    uint8_t  state, end, skipping, _pad0;
    uint32_t entity, frames, skipped;
    uint16_t end_pair, _pad1;
    uint32_t end_frame;
    uint32_t node[3];
    float    clip_end[3], peak[3];
    uint32_t spawn_frame[MHFU_MOVE_MAX_ATTACKS];
    float    spawn_cursor[MHFU_MOVE_MAX_ATTACKS];
    uint32_t spawn_node[MHFU_MOVE_MAX_ATTACKS];
    mhfu_move_t move, next;
    uint32_t next_entity;
    uint32_t node_vtable[MHFU_MOVE_MAX_ATTACKS];
    uint32_t ended_frame[MHFU_MOVE_MAX_ATTACKS];
    uint8_t  ended_state[MHFU_MOVE_MAX_ATTACKS];
    mhfu_steer_state_t steer;
    mhfu_move_t react;                    /* the move that replaces REACT_KIND on REACT_ENTITY */
    mhfu_steer_spec_t react_steer;
    uint32_t react_entity;                /* 0: no reaction is replaced */
    uint8_t  react_kind, react_parts, react_part, _pad2;  /* FLINCH_MASK and MOST_DAMAGED_PART */
    uint32_t reactions;                   /* reactions replaced */
    uint32_t react_seen;                  /* mhfu_em_react_hits taken */
    uint32_t react_pair;                  /* the replaced enter-action: (mode<<16)|(main<<8)|sub */
    uint32_t waited;                      /* AI frames NEXT waited for the notice */
    uint32_t events;                      /* the monster-event block (MONSTER_EVENTS) */
} mhfu_move_state_t;

/* entry on the carrier (0,2), em75's alert hub (one dispatch, then the brain once the clip
 * ends; (0,1) becomes (0,2) under ENTITY+0x4B9): no back pair, no attacks, timed on part 0 */
void mhfu_move_init_spec(mhfu_move_t *mv, uint16_t entry);

/* Starts mv on entity at its next AI step, ending a move it plays; 0 while nothing is wrapped. */
int  mhfu_move_play(uint32_t entity, const mhfu_move_t *mv);

/* Ends the move at the next AI step; the carrier keeps running and hands off itself. */
void mhfu_move_stop(void);

/* The steering the next move takes on its first playing frame (STEER_STATE.NEXT): give one
 * before each mhfu_move_play, mhfu_steer_init_spec's for none. */
void mhfu_move_steer(const mhfu_steer_spec_t *s);

/* The block, or 0 before the framework's init. */
const volatile mhfu_move_state_t *mhfu_move_state(void);

/* Replaces reaction `kind` of entity with mv (and steer, NULL for none) from the next one on, until
 * called again or a brain rule on the flinch is installed; mv NULL stops it. 0 while nothing is
 * wrapped. */
int  mhfu_move_react(int kind, uint32_t entity, const mhfu_move_t *mv,
                     const mhfu_steer_spec_t *steer);

#ifdef __cplusplus
}
#endif
#endif /* MHFU_MOVE_H */
