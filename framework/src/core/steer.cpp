/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* A move's per-frame steering (mhfu/steer.h). */
#include "mhfu/steer.h"
#include "addresses.gen.h"

/* EM75_COLLIDED's mask: sectors 30, 31, 0 and 1, 22.5 degrees either side of straight ahead. */
static const uint32_t AHEAD = 0xC0000003u;
static const int SECTOR_SHIFT = 11; /* 32 sectors of 0x800 YAW units */

#define INLINE static inline __attribute__((always_inline))

/* atan(a) for a in [0, 1], within 1e-5 rad (Abramowitz and Stegun 4.4.49). */
INLINE float atan01(float a)
{
    float s = a * a;
    return a * (0.9998660f +
                s * (-0.3302995f + s * (0.1801410f + s * (-0.0851330f + s * 0.0208351f))));
}

/* The public functions below, inlined so a step runs with no call and no stack frame. */
INLINE uint16_t bearing(float dx, float dz)
{
    const float PI = 3.14159265f;
    float ax = __builtin_fabsf(dx), az = __builtin_fabsf(dz);
    if (ax == 0 && az == 0) return 0;
    float r = ax < az ? atan01(ax / az) : PI / 2 - atan01(az / ax);
    if (dz < 0) r = PI - r;
    if (dx < 0) r = -r;
    float u = r * (MHFU_STEER_TURN / (2 * PI));
    return (uint16_t)(int32_t)(u < 0 ? u - 0.5f : u + 0.5f);
}

INLINE uint16_t toward(uint16_t yaw, uint16_t want, uint16_t rate)
{
    int32_t d = (uint16_t)(want - yaw); /* TURN_TOWARD turns a half turn the positive way */
    if (d > 0x8000) d -= MHFU_STEER_TURN;
    if (d > rate) return (uint16_t)(yaw + rate);
    if (d < -(int32_t)rate) return (uint16_t)(yaw - rate);
    return want;
}

INLINE int32_t share(int32_t total, uint16_t frames, uint16_t frame)
{
    if (frames == 0 || frames > 0x7FFF || frame >= frames) return 0;
    if (total >= MHFU_STEER_TURN || total <= -MHFU_STEER_TURN) return 0;
    return total * (frame + 1) / frames - total * frame / frames;
}

INLINE uint32_t ahead(uint16_t dir)
{
    uint32_t s = dir >> SECTOR_SHIFT;
    return (AHEAD << s) | (AHEAD >> ((32 - s) & 31));
}

INLINE int wall(uint32_t sectors, uint8_t stuck, uint16_t dir)
{
    if (!(sectors & ahead(dir))) return MHFU_STEER_GO;
    return stuck ? MHFU_STEER_STUCK : MHFU_STEER_WALL;
}

/* YAW at clip frame `cursor`: keys every KEY_STEP frames, the last at `end` when that is
 * nearer, joined by straight lines the short way; held outside them. */
INLINE uint16_t curve(const volatile mhfu_steer_spec_t *s, float cursor, float end)
{
    uint32_t n = s->key_count < MHFU_STEER_KEYS ? s->key_count : MHFU_STEER_KEYS;
    if (!n) return 0;
    if (cursor <= 0) return s->keys[0];
    uint32_t i = (uint32_t)(cursor / MHFU_STEER_KEY_STEP);
    if (i >= n - 1) return s->keys[n - 1];
    float f0 = (float)(i * MHFU_STEER_KEY_STEP), f1 = (float)((i + 1) * MHFU_STEER_KEY_STEP);
    if (i + 1 == n - 1 && end > f0 && end < f1) f1 = end;
    float t = (cursor - f0) / (f1 - f0);
    if (t > 1) t = 1;
    float d = (float)(int16_t)(uint16_t)(s->keys[i + 1] - s->keys[i]) * t;
    return (uint16_t)(s->keys[i] + (int32_t)(d < 0 ? d - 0.5f : d + 0.5f));
}

extern "C" {

uint16_t mhfu_steer_bearing(float dx, float dz) { return bearing(dx, dz); }

uint16_t mhfu_steer_toward(uint16_t yaw, uint16_t want, uint16_t rate)
{
    return toward(yaw, want, rate);
}

int32_t mhfu_steer_share(int32_t total, uint16_t frames, uint16_t frame)
{
    return share(total, frames, frame);
}

uint32_t mhfu_steer_ahead(uint16_t dir) { return ahead(dir); }

int mhfu_steer_wall(uint32_t sectors, uint8_t stuck, uint16_t dir)
{
    return wall(sectors, stuck, dir);
}

int mhfu_steer_step(void *entity, const mhfu_vec3_t *hunter, const mhfu_steer_t *p,
                    uint16_t frame)
{
    volatile uint8_t *e = (volatile uint8_t *)entity;
    if (p->walls) {
        uint32_t sectors = *(volatile uint32_t *)(e + MHFU_ENTITY_WALL_SECTORS);
        int w = wall(sectors, e[MHFU_ENTITY_STUCK_WALL], p->dir);
        if (w != MHFU_STEER_GO) return w;
    }
    volatile uint16_t *yaw = (volatile uint16_t *)(e + MHFU_ENTITY_YAW);
    if (p->turn == MHFU_STEER_HUNTER || p->turn == MHFU_STEER_AWAY) {
        volatile float *pos = (volatile float *)(e + MHFU_ENTITY_POSITION);
        uint16_t want = bearing(hunter->x - pos[0], hunter->z - pos[2]);
        if (p->turn == MHFU_STEER_AWAY) want = (uint16_t)(want + MHFU_STEER_TURN / 2);
        *yaw = toward(*yaw, want, p->rate);
    } else if (p->turn == MHFU_STEER_FIXED) {
        *yaw = (uint16_t)(*yaw + share(p->total, p->frames, frame));
    }
    return MHFU_STEER_GO;
}

void mhfu_steer_init_spec(mhfu_steer_spec_t *s)
{
    uint8_t *b = (uint8_t *)s;
    for (unsigned k = 0; k < sizeof(*s); k++) b[k] = 0;
    s->stuck_sub = 6;
    s->stuck_mode = 1;
}

uint16_t mhfu_steer_curve(const volatile mhfu_steer_spec_t *s, float cursor)
{
    return curve(s, cursor, (float)(MHFU_STEER_KEYS * MHFU_STEER_KEY_STEP));
}

int mhfu_steer_move(void *entity, const mhfu_vec3_t *hunter, volatile mhfu_steer_state_t *st,
                    uint32_t frame, float cursor, float loop_start, float end)
{
    volatile uint8_t *e = (volatile uint8_t *)entity;
    volatile uint16_t *yaw = (volatile uint16_t *)(e + MHFU_ENTITY_YAW);
    volatile mhfu_steer_spec_t *sp = &st->now;
    if (frame <= 1) {
        volatile uint32_t *to = (volatile uint32_t *)&st->now;
        volatile uint32_t *from = (volatile uint32_t *)&st->next;
        for (unsigned k = 0; k < sizeof(mhfu_steer_spec_t) / 4; k++) to[k] = from[k];
        st->yaw0 = *yaw;
        st->base = (uint16_t)(*yaw - curve(sp, cursor, end));
        st->wall = MHFU_STEER_GO;
    } else {
        if (sp->steer.walls) {
            int w = wall(*(volatile uint32_t *)(e + MHFU_ENTITY_WALL_SECTORS),
                         e[MHFU_ENTITY_STUCK_WALL], sp->steer.dir);
            if (w != MHFU_STEER_GO) {
                st->wall = (uint8_t)w;
                return w;
            }
        }
        if (cursor < st->cursor) /* a loop: the turn of the frames it skipped back over stays */
            st->base = (uint16_t)(st->base + curve(sp, end, end) - curve(sp, loop_start, end));
    }
    st->cursor = cursor;
    uint8_t mode = sp->steer.turn;
    if ((mode == MHFU_STEER_HUNTER || mode == MHFU_STEER_AWAY) && hunter) {
        volatile float *pos = (volatile float *)(e + MHFU_ENTITY_POSITION);
        uint16_t want = bearing(hunter->x - pos[0], hunter->z - pos[2]);
        if (mode == MHFU_STEER_AWAY) want = (uint16_t)(want + MHFU_STEER_TURN / 2);
        uint16_t now = *yaw;
        st->base = (uint16_t)(st->base + (uint16_t)(toward(now, want, sp->steer.rate) - now));
    } else if (mode == MHFU_STEER_FIXED && frame >= 1) {
        st->base = (uint16_t)(st->base + share(sp->steer.total, sp->steer.frames,
                                               (uint16_t)(frame - 1)));
    }
    *yaw = (uint16_t)(st->base + curve(sp, cursor, end));
    st->yaw = *yaw;
    return MHFU_STEER_GO;
}

#ifndef MHFU_HOST
int mhfu_steer_frame(uint32_t entity, const mhfu_steer_t *p, uint16_t frame)
{
    const mhfu_vec3_t *hunter =
        (const mhfu_vec3_t *)(MHFU_PLAYER_ENTITY + MHFU_ENTITY_POSITION);
    return mhfu_steer_step((void *)entity, hunter, p, frame);
}
#endif

} /* extern "C" */
