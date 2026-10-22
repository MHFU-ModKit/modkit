/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Charge: a gauge, a charged state and a cooldown for the wrapped species' monster, shown to its AI
 * script through the species' query (the script's `switch_query`, vt + MONSTER_VTABLE.QUERY).
 *
 * MHP3rd's Zinogre charges up (a gauge filled by charge-up moves), transforms at 100 and stays
 * charged until hits wear its durability out; its script asks the species for "charged" (query
 * CHARGE_QUERY_CHARGED) and "the cooldown runs" (CHARGE_QUERY_COOLDOWN). MHFU's em75 answers every
 * query 0, so em_vhook wraps the slot: a frame-free, branchless stub answers the board's monster
 * from the table in the charge block (`answers`, by arg), and every other entity, and any arg
 * past 7, reaches the species' own query. The helper keeps the state in C on em_vhook's AI step
 * (the board's monster only), and it stays with the monster across areas: only a new monster of
 * the species, mhfu_em_charge and the quest's end reset it.
 *
 * Each AI frame, from the first one after arming:
 *   - the cooldown counts down while the monster is not charged;
 *   - a hit (ENTITY.HP lower than the frame before; hits within one frame count once) takes
 *     `chip_gauge` from the gauge while the monster is not charged, `chip_durability` from the
 *     durability while it is;
 *   - a charged monster whose pair is in `pairs` (the knockout, the traps), or whose durability is
 *     0, discharges: charged 0, gauge 0, cooldown `cooldown_out`;
 *   - with the gauge at 100 and the move player idle, the `transform` own move is played, forced
 *     (the request mhfu_em_play files); the pair set is refused, as MHP3rd refuses in a knockout.
 * When the move player takes a charge-up own move (a slot of `ups`, whoever asked for it: the AI
 * script, a rule, Lua), the gauge gains the up's gain[0] at or above 95 % of MAX_HP, gain[1] at or
 * above 75 %, gain[2] at or above 40 %, gain[3] below, and the cooldown is `cooldown_up`. When it
 * takes the transform slot, from the helper or from anywhere else: charged 1, durability 100,
 * gauge 100. A charged monster takes no gain from a charge-up.
 *
 * The pure arithmetic is src/core/charge_logic.h; the seam is em_vhook.cpp's brain. The calls
 * only write the charge block, so they are safe from any thread. */
#ifndef MHFU_CHARGE_H
#define MHFU_CHARGE_H

#include <stdint.h>

#include "addresses.gen.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_CHARGE_MAX_UPS    MHFU_CHARGE_CFG_UPS_COUNT     /* charge-up moves */
#define MHFU_CHARGE_MAX_PAIRS  MHFU_CHARGE_CFG_PAIRS_COUNT   /* host pairs that discharge */
#define MHFU_CHARGE_ANSWERS_N  MHFU_CHARGE_ANSWERS_COUNT     /* the query's answers: args 0..7 */
#define MHFU_CHARGE_NO_SLOT    0xFFu    /* mhfu_charge_cfg_t.transform: none */
#define MHFU_CHARGE_FULL       100      /* the gauge that transforms, and the durability it starts with */
#define MHFU_CHARGE_ASK_GAP    30       /* AI frames between two transform plays asked for */

/* struct CHARGE_UP in addresses.toml */
typedef struct {
    uint8_t  slot, _pad;
    int16_t  gain[4];    /* by HP: at least 95 %, 75 %, 40 % of MAX_HP, below */
} mhfu_charge_up_t;

/* struct CHARGE_PAIR */
typedef struct {
    uint8_t main, sub;
} mhfu_charge_pair_t;

/* struct CHARGE_CFG: what mhfu_em_charge takes */
typedef struct {
    uint8_t  transform;          /* own-move slot, MHFU_CHARGE_NO_SLOT for none */
    uint8_t  up_count;           /* ups[] in use, at most MHFU_CHARGE_MAX_UPS */
    uint8_t  pair_count;         /* pairs[] in use, at most MHFU_CHARGE_MAX_PAIRS */
    uint8_t  _pad;
    int16_t  chip_gauge;         /* 0..100 */
    int16_t  chip_durability;    /* 0..100 */
    uint16_t cooldown_up;        /* AI frames */
    uint16_t cooldown_out;
    mhfu_charge_up_t   ups[MHFU_CHARGE_MAX_UPS];
    mhfu_charge_pair_t pairs[MHFU_CHARGE_MAX_PAIRS];
} mhfu_charge_cfg_t;

/* struct CHARGE: em_vhook's block. The query stub reads ANSWERS and ENTITY by offset. */
typedef struct {
    uint32_t answers[MHFU_CHARGE_ANSWERS_N];
    uint32_t entity;             /* the monster the stub answers for; 0 none */
    uint32_t asked;              /* queries the stub answered */
    uint32_t enabled;
    mhfu_charge_cfg_t cfg;
    int32_t  gauge, durability, cooldown;
    uint32_t charged;
    uint32_t seen;               /* the first AI step took the baseline */
    uint32_t hp;                 /* ENTITY.HP the last AI frame saw */
    uint32_t xform_at;           /* AI tick of the last transform play asked for */
    uint32_t up_starts, transforms, discharges, hits;
} mhfu_charge_t;

typedef struct {
    uint32_t armed, charged;
    int32_t  gauge, durability, cooldown;
    uint32_t up_starts, transforms, discharges, hits, asked;
} mhfu_charge_status_t;

/* Arms the helper with cfg (copied), for the board's monster from its next AI step; NULL turns it
 * off, as the quest's end and mhfu_em_clear do. 1 when it took effect; 0 for a cfg it refuses
 * (logged), or when the species' query slot could not be wrapped. The same settings again change
 * nothing, the state included. */
int  mhfu_em_charge(const mhfu_charge_cfg_t *cfg);

void mhfu_em_charge_status(mhfu_charge_status_t *out);

#ifdef __cplusplus
}
#endif
#endif /* MHFU_CHARGE_H */
