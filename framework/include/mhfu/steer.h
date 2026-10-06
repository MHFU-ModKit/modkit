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

/* --- a move's steering, as the move player runs it (move.cpp) ------------------------------- */

#define MHFU_STEER_KEY_STEP 2  /* clip frames between turn keys: mhfu_port.travel.KEY_STEP */
#define MHFU_STEER_KEYS 256

/* struct STEER_SPEC in addresses.toml */
typedef struct {
    mhfu_steer_t steer;
    uint16_t key_count;   /* 0: the clip does not turn YAW */
    uint8_t  stuck_main, stuck_sub, stuck_mode; /* entered on a class-2 wall: em75's (0,6) mode 1 */
    uint8_t  _pad[3];
    uint16_t keys[MHFU_STEER_KEYS]; /* YAW every KEY_STEP clip frames, the last at the clip's end:
                                     * the clips module's `_turns` (mhfu_port.travel.Turn) */
} mhfu_steer_spec_t;

/* struct STEER_STATE in addresses.toml */
typedef struct {
    mhfu_steer_spec_t next; /* taken on the move's first playing frame */
    mhfu_steer_spec_t now;
    uint16_t base;          /* YAW the curve adds to */
    uint8_t  wall;          /* the last frame's MHFU_STEER_* */
    uint8_t  _pad;
    float    cursor;        /* the cursor the step saw last */
    uint16_t yaw0, yaw;     /* YAW on the first playing frame, and as the step left it */
} mhfu_steer_state_t;

/* No turn, no walls; a class-2 wall ends into (0,6) mode 1, as the Tigrex charge does. */
void mhfu_steer_init_spec(mhfu_steer_spec_t *s);
/* The curve's YAW at clip frame `cursor`, the keys joined by straight lines and held past the
 * last; 0 without keys. Exact on the keys, where the engine's cursor sits at speed 2. */
uint16_t mhfu_steer_curve(const volatile mhfu_steer_spec_t *s, float cursor);
/* One AI frame of a move (move.cpp's seam), `frame` its playing frames from 1: frame 1 takes
 * NEXT; a cursor that went back (a loop) carries the curve's turn over; YAW = base + curve, the
 * base turned by the spec's mode. Returns the wall result of the frame before, from frame 2. */
int mhfu_steer_move(void *entity, const mhfu_vec3_t *hunter, volatile mhfu_steer_state_t *st,
                    uint32_t frame, float cursor, float loop_start, float end);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_STEER_H */
