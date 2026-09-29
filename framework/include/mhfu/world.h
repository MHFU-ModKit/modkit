/* Game state outside a single entity: screen, area, quest clock, player and map. */
#ifndef MHFU_WORLD_H
#define MHFU_WORLD_H

#include <stdint.h>
#include "types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* MHFU_SCREEN_STATE values. Code patches take only in MENU and TITLE, while the JIT is cold. */
#define MHFU_WORLD_SCREEN_MENU    1
#define MHFU_WORLD_SCREEN_TITLE   4
#define MHFU_WORLD_SCREEN_IN_AREA 17
#define MHFU_WORLD_SCREEN_VILLAGE 22   /* 22: in the village, per the log gate; unverified */

uint8_t     mhfu_world_screen_state(void);
uint16_t    mhfu_world_area_index(void);      /* the visible section; ENTITY.SECTION's encoding */
uint32_t    mhfu_world_quest_timer(void);     /* frames left at 30 Hz */
uint32_t    mhfu_world_player_hp(void);
mhfu_vec3_t mhfu_world_player_pos(void);      /* read from MHFU_CAM_VIEW_EYE */

/* 1 while in an area or the village. Gate every ms0 access on it: the savedata utility
 * shares the non-reentrant Memory Stick driver, and a concurrent write freezes the load. */
int         mhfu_world_ms0_io_safe(void);

/* Shows every big monster on the map (MHFU_MAP_PAINT); call at 2 Hz or faster. */
void        mhfu_world_paint_map(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_WORLD_H */
