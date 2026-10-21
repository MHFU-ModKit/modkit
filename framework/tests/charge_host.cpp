/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The charge helper's pure parts (src/core/charge_logic.h) for the host test, through ctypes. */
#include "charge_logic.h"

extern "C" {
const char *chg_host_check(const mhfu_charge_cfg_t *c) { return chg_check(c); }
int chg_host_band(uint32_t hp, uint32_t max_hp) { return chg_band(hp, max_hp); }
int chg_host_up_index(mhfu_charge_t *c, uint32_t slot) { return chg_up_index(&c->cfg, slot); }
int chg_host_pair(mhfu_charge_t *c, uint32_t pair) { return chg_pair_discharges(&c->cfg, pair); }
void chg_host_reset(mhfu_charge_t *c) { chg_reset(c); }
void chg_host_answers(mhfu_charge_t *c) { chg_answers(c); }
void chg_host_start(mhfu_charge_t *c, uint32_t entity, uint32_t hp) { chg_start(c, entity, hp); }
void chg_host_tick(mhfu_charge_t *c) { chg_tick(c); }
int chg_host_up_start(mhfu_charge_t *c, uint32_t slot, uint32_t hp, uint32_t max_hp)
{
    return chg_up_start(c, slot, hp, max_hp);
}
int chg_host_transform_start(mhfu_charge_t *c, uint32_t slot) { return chg_transform_start(c, slot); }
void chg_host_hit(mhfu_charge_t *c) { chg_hit(c); }
int chg_host_discharge(mhfu_charge_t *c) { return chg_discharge(c); }
int chg_host_settle(mhfu_charge_t *c, uint32_t pair) { return chg_settle(c, pair); }
int chg_host_wants_transform(mhfu_charge_t *c) { return chg_wants_transform(c); }
}
