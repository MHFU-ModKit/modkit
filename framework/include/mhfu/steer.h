/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* A big monster's own move, steered one AI frame at a time: its turn, whether a wall ends it, and
 * travel the clip does not carry. The engine carries the clip's root motion itself and pushes the
 * monster out of walls (WALL_COLLIDE, after the AI step); this reads that frame's wall result,
 * turns YAW and moves POSITION, which that push-out then corrects as it does root motion.
 * Leaf functions: no engine call. */
#ifndef MHFU_STEER_H
#define MHFU_STEER_H

#include <stdint.h>

#include "addresses.gen.h"
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
    MHFU_STEER_FIXED = 3,  /* `total` spread evenly over `frames` frames, or across the window */
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
    uint16_t win_from, win_to; /* HUNTER, AWAY: turn only while the clip frame is in
                                * [win_from, win_to); both 0: the whole move. FIXED, in
                                * mhfu_steer_move: `total` is spread across the window by the
                                * clip cursor and `frames` is not read; win_to 0: over `frames` */
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
 * the frame before, and, while it is MHFU_STEER_GO, YAW turned as `p` says toward `hunter`. The
 * window is read against `frame`, the clip frame there. */
int mhfu_steer_step(void *entity, const mhfu_vec3_t *hunter, const mhfu_steer_t *p,
                    uint16_t frame);
#ifndef MHFU_HOST
/* mhfu_steer_step on a game entity, toward PLAYER_ENTITY. */
int mhfu_steer_frame(uint32_t entity, const mhfu_steer_t *p, uint16_t frame);
#endif

/* --- a move's steering, as the move player runs it (move.cpp) ------------------------------- */

/* MHFU_STEER_KEY_STEP, _KEYS, _SAMPLES and _TRAVEL_SCALE are consts in addresses.toml */

/* struct TRAVEL_SAMPLE in addresses.toml: x, z in 1/MHFU_STEER_TRAVEL_SCALE world units */
typedef struct {
    int16_t x, z;
} mhfu_travel_sample_t;

/* struct STEER_LUNGE in addresses.toml; near, span, base and range are times ENTITY.SIZE_SCALE */
typedef struct {
    uint16_t lo, hi;  /* clip frames, the window (lo, hi]; hi 0: no lunge */
    float near, span, base, range;
} mhfu_steer_lunge_t;

/* struct STEER_SPEC in addresses.toml */
typedef struct {
    mhfu_steer_t steer;
    uint16_t key_count;   /* 0: the clip does not turn YAW */
    uint8_t  stuck_main, stuck_sub, stuck_mode; /* entered on a class-2 wall: em75's (0,6) mode 1 */
    uint8_t  _pad[3];
    uint16_t keys[MHFU_STEER_KEYS]; /* YAW every KEY_STEP clip frames, the last at the clip's end:
                                     * the port's turns module (mhfu_port.travel.Turn) */
    uint16_t sample_count;          /* 0: no travel by code */
    mhfu_steer_lunge_t lunge;
    mhfu_travel_sample_t samples[MHFU_STEER_SAMPLES]; /* the monster's own (x, z) at clip frame
                                     * start + k * KEY_STEP, cumulative, in its frame at the first
                                     * playing frame (x its left, z forward); held past the last */
} mhfu_steer_spec_t;

/* struct STEER_STATE in addresses.toml */
typedef struct {
    mhfu_steer_spec_t next; /* taken on the move's first playing frame */
    mhfu_steer_spec_t now;
    uint16_t base;          /* the entity's own YAW: the curve and the ramp add to it */
    uint8_t  wall;          /* the last frame's MHFU_STEER_* */
    float    cursor;        /* the cursor the step saw last */
    uint16_t yaw0, yaw;     /* BASE on the first playing frame (travel's frame), and YAW as the step
                             * left it */
    float    lunge_x, lunge_z; /* the lunge's whole length, world axes, set on the first frame */
    uint8_t  keep;          /* the step started in place: its first frame keeps `base` */
    uint16_t blend;         /* part 0's CLIP_BLOCK.BLEND right after the dispatch */
    int16_t  ramp;          /* the YAW jump at the switch, falling to 0 as the blend counts down */
} mhfu_steer_state_t;

/* Part 0's clip block as the move player reads it each AI frame. */
typedef struct {
    float   start;      /* the clip frame the move began at */
    float   cursor;
    float   loop_start;
    float   end;
    float   speed;      /* CLIP_BLOCK.SPEED: clip frames an AI frame, 2.0 calm */
    int32_t blend;      /* CLIP_BLOCK.BLEND now */
} mhfu_steer_clip_t;

/* No turn, no walls; a class-2 wall ends into (0,6) mode 1, as the Tigrex charge does. */
void mhfu_steer_init_spec(mhfu_steer_spec_t *s);
/* The curve's YAW at clip frame `cursor`, the keys joined by straight lines and held past the
 * last; 0 without keys. Exact on the keys, where the engine's cursor sits at speed 2. */
uint16_t mhfu_steer_curve(const volatile mhfu_steer_spec_t *s, float cursor);
/* sin and cos of YAW `yaw`, within 1e-6. */
void mhfu_steer_sincos(uint16_t yaw, float *s, float *c);
/* One AI frame of a move (move.cpp's seam), `frame` its playing frames from 1: frame 1 takes
 * NEXT; a cursor that went back (a loop) carries the curve's turn over; YAW = base + curve + ramp,
 * the base turned by the spec's mode (HUNTER and AWAY toward `target`, at the rate times
 * clip->speed / 2, only while the cursor, the clip frame, is in the window; FIXED with a window
 * across it by the cursor). Frame 1 keeps `base` when the step started in place (st->keep), else
 * sets it to YAW, and the ramp starts at the jump that keeps YAW continuous, falling to 0 as
 * clip->blend counts down from st->blend. Then POSITION moves by the change of the travel curve
 * and of the lunge between the cursor of the frame before (frame 1: clip->start) and this one:
 * the curve turned by the entity's own yaw (`base`) on frame 1, the lunge along the bearing to
 * `target` on frame 1, both scaled by ENTITY.SIZE_SCALE. Returns the wall result of the frame before, from frame 2, and
 * then moves nothing. */
int mhfu_steer_move(void *entity, const mhfu_vec3_t *target, volatile mhfu_steer_state_t *st,
                    uint32_t frame, const mhfu_steer_clip_t *clip);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_STEER_H */
