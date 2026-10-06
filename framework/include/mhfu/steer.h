/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* A big monster's own move, steered one AI frame at a time: its turn, and whether a wall ends it.
 * The engine carries the travel itself (the clip's root motion) and pushes the monster out of
 * walls (WALL_COLLIDE, after the AI step); this reads that frame's wall result and turns YAW.
 * Leaf functions: no engine or library call. */
#ifndef MHFU_STEER_H
#define MHFU_STEER_H

#include <stdint.h>

#include "mhfu/types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* YAW units, as ENTITY.YAW: 0x10000 a full turn, atan2(dx, dz). */
#define MHFU_STEER_TURN 0x10000

enum {
    MHFU_STEER_STILL = 0,  /* no turn */
    MHFU_STEER_HUNTER = 1, /* toward the hunter, at most `rate` a frame (TURN_TOWARD's step) */
    MHFU_STEER_AWAY = 2,   /* away from the hunter, the same way */
    MHFU_STEER_FIXED = 3,  /* `total` spread evenly over `frames` frames */
};

enum {
    MHFU_STEER_GO = 0,    /* no wall ahead: the move goes on */
    MHFU_STEER_WALL = 1,  /* a wall ahead in the direction of travel: end the move */
    MHFU_STEER_STUCK = 2, /* and of class 2 (ENTITY.STUCK_WALL): end it into (0, 6) mode 1 */
};

typedef struct {
    uint8_t  turn;   /* MHFU_STEER_STILL .. MHFU_STEER_FIXED */
    uint8_t  walls;  /* nonzero: report walls ahead */
    uint16_t rate;   /* HUNTER, AWAY: most YAW units a frame; the Tigrex charge steers 64 */
    int32_t  total;  /* FIXED: YAW units over the move, under one turn either way */
    uint16_t frames; /* FIXED: frames to spread `total` over, 1..0x7FFF */
    uint16_t dir;    /* direction of travel against YAW, YAW units: 0 forward, 0x8000 back */
} mhfu_steer_t;

/* atan2(dx, dz) in YAW units, within one unit. */
uint16_t mhfu_steer_bearing(float dx, float dz);
/* `yaw` turned toward `want` by at most `rate`, the short way; `want` once within `rate`. */
uint16_t mhfu_steer_toward(uint16_t yaw, uint16_t want, uint16_t rate);
/* Frame `frame`'s share of `total` over `frames`: the shares of frames 0..frames-1 sum to it. */
int32_t mhfu_steer_share(int32_t total, uint16_t frames, uint16_t frame);
/* ENTITY.WALL_SECTORS bits within 22.5 degrees of `dir` (0xC0000003 for 0, EM75_COLLIDED's). */
uint32_t mhfu_steer_ahead(uint16_t dir);
/* MHFU_STEER_GO, _WALL or _STUCK for one frame's ENTITY.WALL_SECTORS and STUCK_WALL. */
int mhfu_steer_wall(uint32_t sectors, uint8_t stuck, uint16_t dir);

/* One AI frame of a move on `entity` (its ENTITY fields), `frame` frames in: the wall result of
 * the frame before, and, while it is MHFU_STEER_GO, YAW turned as `p` says toward `hunter`. */
int mhfu_steer_step(void *entity, const mhfu_vec3_t *hunter, const mhfu_steer_t *p,
                    uint16_t frame);
#ifndef MHFU_HOST
/* mhfu_steer_step on a game entity, toward PLAYER_ENTITY. */
int mhfu_steer_frame(uint32_t entity, const mhfu_steer_t *p, uint16_t frame);
#endif

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_STEER_H */
