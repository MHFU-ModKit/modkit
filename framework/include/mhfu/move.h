/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The move player: a big monster plays one of its executor entries as a move of its own.
 *
 * A move rides a native CARRIER pair, entered through the engine's enter-action so the host
 * provisions it and its handler runs; on the next AI step the move's entry goes to the executor,
 * which puts the clip on every body part. Each attack spawns when the timing part's cursor
 * crosses its frame. The move ends when its clip does (the carrier hands off to the brain, or
 * the move enters its back pair), after LENGTH frames, or when anything else changes the pair, a
 * reaction first of all. With SKIP the host AI step does not run while the clip plays, except
 * on a frame with a hit pending, so the brain cannot cut in; the host still takes the damage.
 *
 * It runs on em_vhook's slot-29 step (mhfu_em_step), so the species must be wrapped
 * (mhfu_em_installed). The calls only write the move block: safe from any thread. */
#ifndef MHFU_MOVE_H
#define MHFU_MOVE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_MOVE_MAX_ATTACKS 4
#define MHFU_MOVE_NO_PAIR 0xFFu   /* back_main: no back pair, the carrier hands off itself */

/* struct MOVE_ATTACK and MOVE in addresses.toml */
typedef struct {
    uint16_t frame;   /* clip frame */
    uint16_t id;      /* the species' attack record */
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
} mhfu_move_state_t;

/* entry on the carrier (0,1): no back pair, no attacks, timed on part 0 */
void mhfu_move_init_spec(mhfu_move_t *mv, uint16_t entry);

/* Starts mv on entity at its next AI step, ending a move it plays; 0 while nothing is wrapped. */
int  mhfu_move_play(uint32_t entity, const mhfu_move_t *mv);

/* Ends the move at the next AI step; the carrier keeps running and hands off itself. */
void mhfu_move_stop(void);

/* The block, or 0 before the framework's init. */
const volatile mhfu_move_state_t *mhfu_move_state(void);

#ifdef __cplusplus
}
#endif
#endif /* MHFU_MOVE_H */
