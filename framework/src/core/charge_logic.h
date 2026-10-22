/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The charge helper's arithmetic and state machine (mhfu/charge.h), on the block em_vhook keeps.
 * Free of PSP headers and of game memory, so the host test compiles it (tests/charge_host.cpp);
 * em_vhook.cpp's brain reads the monster and calls these. */
#ifndef MHFU_CORE_CHARGE_LOGIC_H
#define MHFU_CORE_CHARGE_LOGIC_H

#include <stdint.h>

#include "mhfu/charge.h"

typedef volatile mhfu_charge_t chg_t;

/* Why a configuration cannot run, or NULL. */
static inline const char *chg_check(const mhfu_charge_cfg_t *c)
{
    if (c->up_count > MHFU_CHARGE_MAX_UPS) return "too many charge-ups";
    if (c->pair_count > MHFU_CHARGE_MAX_PAIRS) return "too many discharge pairs";
    if (c->transform != MHFU_CHARGE_NO_SLOT && c->transform >= MHFU_EM_MOVES_MOVES_COUNT)
        return "a transform slot past the registry";
    for (unsigned k = 0; k < c->up_count; k++) {
        if (c->ups[k].slot >= MHFU_EM_MOVES_MOVES_COUNT) return "a charge-up slot past the registry";
        for (int b = 0; b < 4; b++)
            if (c->ups[k].gain[b] < 0 || c->ups[k].gain[b] > MHFU_CHARGE_FULL)
                return "a gain is 0..100";
    }
    if (c->chip_gauge < 0 || c->chip_gauge > MHFU_CHARGE_FULL
        || c->chip_durability < 0 || c->chip_durability > MHFU_CHARGE_FULL)
        return "a chip is 0..100";
    return 0;
}

/* The HP band of a charge-up's gain: 0 at or above 95 % of max_hp, 1 at or above 75 %, 2 at or
 * above 40 %, else 3; no maximum reads as full HP. */
static inline int chg_band(uint32_t hp, uint32_t max_hp)
{
    if (!max_hp || hp * 100u >= 95u * max_hp) return 0;
    if (hp * 100u >= 75u * max_hp) return 1;
    return hp * 100u >= 40u * max_hp ? 2 : 3;
}

/* The index of the charge-up of own-move slot `slot`, or -1. */
static inline int chg_up_index(const volatile mhfu_charge_cfg_t *c, uint32_t slot)
{
    for (uint32_t k = 0; k < c->up_count && k < MHFU_CHARGE_MAX_UPS; k++)
        if (c->ups[k].slot == slot) return (int)k;
    return -1;
}

/* The host pair is in the discharge set. */
static inline int chg_pair_discharges(const volatile mhfu_charge_cfg_t *c, uint32_t pair)
{
    for (uint32_t k = 0; k < c->pair_count && k < MHFU_CHARGE_MAX_PAIRS; k++)
        if (c->pairs[k].main == (pair >> 8) && c->pairs[k].sub == (pair & 0xFF)) return 1;
    return 0;
}

/* A new monster, or none: the state after the configuration goes back to nothing seen. */
static inline void chg_reset(chg_t *c)
{
    for (unsigned k = 0; k < MHFU_CHARGE_ANSWERS_N; k++) c->answers[k] = 0;
    c->entity = 0;
    c->gauge = c->durability = c->cooldown = 0;
    c->charged = c->seen = c->hp = c->xform_at = 0;
    c->up_starts = c->transforms = c->discharges = c->hits = 0;
}

/* What the query slot says for the state: "charged" and "the cooldown runs", nothing else. */
static inline void chg_answers(chg_t *c)
{
    for (unsigned k = 0; k < MHFU_CHARGE_ANSWERS_N; k++) c->answers[k] = 0;
    c->answers[MHFU_CHARGE_QUERY_CHARGED] = c->charged != 0;
    c->answers[MHFU_CHARGE_QUERY_COOLDOWN] = c->cooldown > 0;
}

/* The first AI step: the baseline the hits are measured against. */
static inline void chg_start(chg_t *c, uint32_t entity, uint32_t hp)
{
    c->entity = entity;
    c->hp = hp;
    c->seen = 1;
}

/* One AI frame of the cooldown, which only runs while the monster is not charged. */
static inline void chg_tick(chg_t *c)
{
    if (!c->charged && c->cooldown > 0) c->cooldown--;
}

/* The move player took own move `slot`: a charge-up raises the gauge by the HP band and starts
 * the cooldown (1); a charged monster takes nothing. */
static inline int chg_up_start(chg_t *c, uint32_t slot, uint32_t hp, uint32_t max_hp)
{
    const int k = chg_up_index(&c->cfg, slot);
    if (k < 0 || c->charged) return 0;
    int32_t g = c->gauge + c->cfg.ups[k].gain[chg_band(hp, max_hp)];
    if (g > MHFU_CHARGE_FULL) g = MHFU_CHARGE_FULL;
    c->gauge = g;
    c->cooldown = c->cfg.cooldown_up;
    c->up_starts++;
    return 1;
}

/* The move player took the transform's slot (1): charged, full gauge and durability. */
static inline int chg_transform_start(chg_t *c, uint32_t slot)
{
    if (c->cfg.transform == MHFU_CHARGE_NO_SLOT || slot != c->cfg.transform) return 0;
    c->charged = 1;
    c->durability = c->gauge = MHFU_CHARGE_FULL;
    c->transforms++;
    return 1;
}

/* A hit: the gauge's chip while not charged, the durability's while charged. */
static inline void chg_hit(chg_t *c)
{
    volatile int32_t *v = c->charged ? &c->durability : &c->gauge;
    const int32_t chip = c->charged ? c->cfg.chip_durability : c->cfg.chip_gauge;
    *v = *v > chip ? *v - chip : 0;
    c->hits++;
}

/* Charged to not: gauge and durability 0, the cooldown starts; 1 on that edge. */
static inline int chg_discharge(chg_t *c)
{
    if (!c->charged) return 0;
    c->charged = 0;
    c->gauge = c->durability = 0;
    c->cooldown = c->cfg.cooldown_out;
    c->discharges++;
    return 1;
}

/* A charged monster in a discharge pair or with no durability left discharges; 1 on that edge. */
static inline int chg_settle(chg_t *c, uint32_t pair)
{
    if (!c->charged) return 0;
    if (c->durability > 0 && !chg_pair_discharges(&c->cfg, pair)) return 0;
    return chg_discharge(c);
}

/* The gauge is full, nothing is charged and a transform is set: the helper plays it. */
static inline int chg_wants_transform(const chg_t *c)
{
    return !c->charged && c->gauge >= MHFU_CHARGE_FULL && c->cfg.transform != MHFU_CHARGE_NO_SLOT;
}

#endif /* MHFU_CORE_CHARGE_LOGIC_H */
