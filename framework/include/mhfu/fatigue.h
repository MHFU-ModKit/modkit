/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Fatigue: a stamina for the wrapped species' monster, and "tired" shown to its AI script.
 *
 * MHFU has no fatigue, and its AI script cannot count (no increment op; the timers and counters
 * are the engine's). The helper keeps the stamina in C, on em_vhook's AI step (the board's monster
 * only), and shows it through gauge A (ENTITY.GAUGE_A), which `if_gauge_a_low` tests: true at or
 * below 30 % of the gauge's maximum. em75 never tests it and nothing drains it there, so a script
 * that asks `if_gauge_a_low` asks "is it tired". A species whose AI step calls GAUGE_A_LATCH drains
 * the gauge itself: do not arm on one.
 *
 * Charges, once an AI frame:
 *   - the monster entered a pair (a different one from the last AI frame): that pair's cost, from
 *     the pair table; a pair not in it costs nothing, the hub that carries own moves among them;
 *   - the move player started one of our own moves: its cost, by slot.
 * A cost halves while HP is at or below `low_hp_pct` of MAX_HP, and nothing is charged while the
 * monster is enraged (ENTITY.FLAGS 0x20) or tired. A negative cost gives stamina back. At 0
 * stamina, outside rage, a tired spell of `tired_frames` AI frames begins; it ends with
 * `refill` stamina. With `calm` the species' own anger starts no rage while tired
 * (mhfu_em_natural_rage), as MHP3rd's does not.
 *
 * The first AI step after arming picks the starting stamina from `starts` by the board's
 * generator, and a new monster of the species starts afresh. Gauge A follows the state, written
 * when it differs: stamina s of max, with t the engine's threshold (gauge max * 30 / 100),
 *   not tired: t + 1 + (gauge max - t - 1) * s / max        (above t, and never 0)
 *   tired:     max(1, t / 2)                                (at or below t, so "low")
 * A spawn refills a gauge found at 0, which is why neither is ever 0.
 *
 * The pure arithmetic is src/core/fatigue_logic.h; the seam is em_vhook.cpp's brain. Both calls
 * only write the fatigue block, so they are safe from any thread. */
#ifndef MHFU_FATIGUE_H
#define MHFU_FATIGUE_H

#include <stdint.h>

#include "addresses.gen.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_FATIGUE_PAIRS MHFU_FATIGUE_CFG_PAIRS_COUNT        /* costs by pair */
#define MHFU_FATIGUE_OWN   MHFU_OWN_MOVES                      /* costs by own-move slot */

/* struct FATIGUE_PAIR in addresses.toml */
typedef struct {
    uint8_t main, sub;
    int16_t cost;
} mhfu_fatigue_pair_t;

/* struct FATIGUE_CFG: what mhfu_em_fatigue takes */
typedef struct {
    int32_t  max;            /* stamina maximum, 1..MHFU_FATIGUE_MAX */
    int32_t  starts[2];      /* stamina at the first AI step, one by chance, 0..max */
    int32_t  refill;         /* stamina after a tired spell, 1..max */
    uint32_t tired_frames;   /* AI frames a tired spell lasts, at least 1 */
    uint8_t  low_hp_pct;     /* costs halve at or below this HP percent; 0 never; at most 100 */
    uint8_t  calm;           /* 1: no natural rage while tired */
    uint16_t pair_count;     /* pairs[] in use, at most MHFU_FATIGUE_PAIRS */
    mhfu_fatigue_pair_t pairs[MHFU_FATIGUE_PAIRS];
    int16_t  own[MHFU_FATIGUE_OWN];   /* by own-move slot; 0 free */
} mhfu_fatigue_cfg_t;

#define MHFU_FATIGUE_MAX 30000   /* a bound that keeps the gauge arithmetic in 32 bits */

/* struct FATIGUE: em_vhook's block, CFG first */
typedef struct {
    uint32_t enabled;
    mhfu_fatigue_cfg_t cfg;
    int32_t  stamina;
    uint32_t tired, left;    /* in a tired spell; AI frames of it left */
    uint32_t seen;           /* the first AI step chose the start */
    uint32_t pair;           /* (main << 8) | sub, the last AI frame's */
    uint32_t held;           /* this block keeps the species' anger off */
    uint32_t charges, spells;
    int32_t  gauge;          /* gauge A as last wanted */
} mhfu_fatigue_t;

typedef struct {
    uint32_t armed;
    int32_t  stamina;
    uint32_t tired, left, charges, spells;
    int32_t  gauge;
} mhfu_fatigue_status_t;

/* Arms the helper with cfg (copied), for the board's monster from its next AI step; NULL turns it
 * off, as the quest's end and mhfu_em_clear do. 1 when it took effect; 0 for a cfg it refuses
 * (logged), or before the framework's init. */
int  mhfu_em_fatigue(const mhfu_fatigue_cfg_t *cfg);

void mhfu_em_fatigue_status(mhfu_fatigue_status_t *out);

#ifdef __cplusplus
}
#endif
#endif /* MHFU_FATIGUE_H */
