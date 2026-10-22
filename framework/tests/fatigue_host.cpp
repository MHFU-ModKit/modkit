/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The fatigue helper's pure parts (src/core/fatigue_logic.h) for the host test, through ctypes. */
#include "fatigue_logic.h"

extern "C" {
int32_t fat_host_low(int32_t gauge_max) { return fat_low(gauge_max); }
int32_t fat_host_gauge(int32_t max, int32_t stamina, int tired, int32_t gauge_max)
{
    return fat_gauge(max, stamina, tired, gauge_max);
}
const char *fat_host_check(const mhfu_fatigue_cfg_t *c) { return fat_check(c); }
int32_t fat_host_pair_cost(mhfu_fatigue_t *f, uint32_t main, uint32_t sub)
{
    return fat_pair_cost(&f->cfg, main, sub);
}
int32_t fat_host_own_cost(mhfu_fatigue_t *f, uint32_t slot) { return fat_own_cost(&f->cfg, slot); }
int fat_host_low_hp(uint32_t hp, uint32_t max_hp, uint32_t pct) { return fat_low_hp(hp, max_hp, pct); }
void fat_host_reset(mhfu_fatigue_t *f) { fat_reset(f); }
void fat_host_start(mhfu_fatigue_t *f, uint32_t roll, uint32_t pair) { fat_start(f, roll, pair); }
void fat_host_charge(mhfu_fatigue_t *f, int32_t cost, int low_hp) { fat_charge(f, cost, low_hp); }
int fat_host_settle(mhfu_fatigue_t *f, int enraged) { return fat_settle(f, enraged); }
int fat_host_tick(mhfu_fatigue_t *f) { return fat_tick(f); }
}
