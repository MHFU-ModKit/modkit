/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The fatigue helper's arithmetic and state machine (mhfu/fatigue.h), on the block em_vhook keeps.
 * Free of PSP headers and of game memory, so the host test compiles it (tests/fatigue_host.cpp);
 * em_vhook.cpp's brain reads the monster and calls these. */
#ifndef MHFU_CORE_FATIGUE_LOGIC_H
#define MHFU_CORE_FATIGUE_LOGIC_H

#include <stdint.h>

#include "mhfu/fatigue.h"

typedef volatile mhfu_fatigue_t fat_t;

/* gauge A's low threshold as if_gauge_a_low has it: at or below max * 30 / 100, the engine's
 * trunc(max * 0.3f), taken in 32 bits (no libgcc division) as q * 30 + r * 30 / 100 for
 * max = q * 100 + r */
static inline int32_t fat_low(int32_t gauge_max)
{
    return gauge_max > 0 ? gauge_max / 100 * 30 + gauge_max % 100 * 30 / 100 : 0;
}

/* Gauge A for a stamina: above fat_low when not tired, at or below it while tired, never 0. */
static inline int32_t fat_gauge(int32_t max, int32_t stamina, int tired, int32_t gauge_max)
{
    const int32_t t = fat_low(gauge_max);
    if (max < 1) max = 1;   /* a configuration being rewritten on another thread */
    if (tired) return t / 2 > 1 ? t / 2 : 1;
    if (stamina < 0) stamina = 0;
    if (stamina > max) stamina = max;
    const int32_t span = gauge_max - t - 1 > 0 ? gauge_max - t - 1 : 0;
    /* span * stamina / max without a 64-bit product: span = q * max + r, stamina <= max <= 30000 */
    return t + 1 + span / max * stamina + span % max * stamina / max;
}

/* Why a configuration cannot run, or NULL. */
static inline const char *fat_check(const mhfu_fatigue_cfg_t *c)
{
    if (c->max < 1 || c->max > MHFU_FATIGUE_MAX) return "max is 1..30000";
    for (int k = 0; k < 2; k++)
        if (c->starts[k] < 0 || c->starts[k] > c->max) return "a start outside 0..max";
    if (c->refill < 1 || c->refill > c->max) return "refill is 1..max";
    if (c->tired_frames < 1) return "tired is at least 1 frame";
    if (c->low_hp_pct > 100) return "low_hp is a percent";
    if (c->pair_count > MHFU_FATIGUE_PAIRS) return "too many pairs";
    return 0;
}

/* The cost of entering (main, sub); 0 for a pair the table does not list. */
static inline int32_t fat_pair_cost(const volatile mhfu_fatigue_cfg_t *c, uint32_t main,
                                    uint32_t sub)
{
    for (uint32_t k = 0; k < c->pair_count && k < MHFU_FATIGUE_PAIRS; k++)
        if (c->pairs[k].main == main && c->pairs[k].sub == sub) return c->pairs[k].cost;
    return 0;
}

static inline int32_t fat_own_cost(const volatile mhfu_fatigue_cfg_t *c, uint32_t slot)
{
    return slot < MHFU_FATIGUE_OWN ? c->own[slot] : 0;
}

/* HP at or below pct percent of max_hp; never with pct 0 or no maximum */
static inline int fat_low_hp(uint32_t hp, uint32_t max_hp, uint32_t pct)
{
    return pct && max_hp && hp * 100u <= pct * max_hp;
}

/* A new monster: the state after the configuration goes back to nothing seen. */
static inline void fat_reset(fat_t *f)
{
    f->stamina = 0;
    f->tired = f->left = f->seen = f->pair = f->held = 0;
    f->charges = f->spells = 0;
    f->gauge = 0;
}

/* The first AI step: stamina is starts[roll & 1] and `pair` the pair already being entered. */
static inline void fat_start(fat_t *f, uint32_t roll, uint32_t pair)
{
    f->stamina = f->cfg.starts[roll & 1];
    f->pair = pair;
    f->seen = 1;
}

/* A cost taken, halved at low HP; a tired monster has nothing to lose. */
static inline void fat_charge(fat_t *f, int32_t cost, int low_hp)
{
    if (!cost || f->tired) return;
    if (cost > 0 && low_hp) cost /= 2;
    int32_t s = f->stamina - cost;
    if (s < 0) s = 0;
    if (s > f->cfg.max) s = f->cfg.max;
    f->stamina = s;
    f->charges++;
}

/* Stamina at 0 outside rage begins the tired spell; 1 on that edge. */
static inline int fat_settle(fat_t *f, int enraged)
{
    if (f->tired || f->stamina > 0 || enraged) return 0;
    f->tired = 1;
    f->left = f->cfg.tired_frames;
    f->spells++;
    return 1;
}

/* One AI frame of the tired spell; 1 on the frame it ends, with stamina refilled. */
static inline int fat_tick(fat_t *f)
{
    if (!f->tired) return 0;
    if (f->left) f->left--;
    if (f->left) return 0;
    f->tired = 0;
    f->stamina = f->cfg.refill;
    return 1;
}

#endif /* MHFU_CORE_FATIGUE_LOGIC_H */
