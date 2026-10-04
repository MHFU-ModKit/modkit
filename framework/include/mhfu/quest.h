/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* The active quest's monster list. Edit it from a MHFU_EVENT_QUEST_TARGETS_BUILDING callback
 * (the quest handle is in its mhfu_quest_ctx_t), before the loading screen loads the models. */
#ifndef MHFU_QUEST_H
#define MHFU_QUEST_H

#include <stdint.h>
#include "ids.h"
#include "types.h"
#include "hooks.h"   /* mhfu_hook_rc_t */

#ifdef __cplusplus
extern "C" {
#endif

int mhfu_quest_monster_count(mhfu_quest_t q);
int mhfu_quest_has(mhfu_quest_t q, mhfu_monster_type_t id);

/* emId of the first big-monster record, or -1. */
int mhfu_quest_first_monster(mhfu_quest_t q);

/* Retag the monster `from` as `to` in place; the engine then loads `to` natively. BADARG if
 * `from` is missing or `to`'s record layout is unknown (only Tigrex's is). */
mhfu_hook_rc_t mhfu_quest_replace_monster(mhfu_quest_t q, mhfu_monster_type_t from,
                                          mhfu_monster_type_t to);

/* Add `id` as another big monster in its own target group, which is what makes it deal
 * damage. The quest keeps its first target group, so NOSPACE once the others are taken.
 * x/z are the spawn point, 0 keeps the source record's. Only Tigrex's record layout is known. */
mhfu_hook_rc_t mhfu_quest_add_monster(mhfu_quest_t q, mhfu_monster_type_t id,
                                      float x, float z);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_QUEST_H */
