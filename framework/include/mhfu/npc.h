/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* Village NPCs of our own (npc.cpp).
 *
 * Each one is a row appended to Pokke village's spawn table, so the lobby's spawner builds it on
 * every load of the main village area. Its model is a PAC staged in extra RAM. It runs no AI,
 * cannot be talked to and is not solid. OBJ_VTABLE.BRAIN of NPC_VTABLE is wrapped, so on every
 * frame C turns it and switches its clips as asked; the clips' root motion moves it.
 *
 * The calls only write the block the wrapper reads, so any thread may make them. */
#ifndef MHFU_NPC_H
#define MHFU_NPC_H

#include <stdint.h>
#include "mhfu/types.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MHFU_NPC_MAX          4        /* the lobby's registry holds 20 and the village uses 16 */
#define MHFU_NPC_OWN_KIND     5        /* ours: RESOURCE_TABLE row 21, unused in the village */
#define MHFU_NPC_DEFAULT_CHAR 0x1E     /* no spawn condition; ours are never talked to */
#define MHFU_NPC_NONE         0xFFFFu  /* mhfu_npc_play's `then`: none */

enum {
    MHFU_NPC_FACE_STILL  = 0,  /* no turn */
    MHFU_NPC_FACE_POINT  = 1,  /* toward world (x, z) */
    MHFU_NPC_FACE_HUNTER = 2,  /* toward the hunter, offset by (x right, z ahead) in their frame */
};

typedef struct {
    const char *pac;        /* the model's PAC, a path as mhfu_inject_register takes it; read now */
    float       size;       /* ENTITY.RENDER_SCALE; 1 is the model's own */
    uint8_t     character;  /* NPC_SPAWN_ROW.CHAR; MHFU_NPC_DEFAULT_CHAR unless you need another */
    float       right;      /* where it appears on each load, in the hunter's frame */
    float       ahead;
} mhfu_npc_spec_t;

typedef struct {
    uint32_t    object;   /* 0 while the village does not show it */
    uint32_t    frames;   /* frames since it spawned */
    uint16_t    entry;    /* the animation-pack entry slot 0 plays */
    uint8_t     playing;  /* slot 0's clip has not ended (CLIP_BLOCK.FLAGS bit 0) */
    uint16_t    yaw;      /* ENTITY.YAW */
    mhfu_vec3_t pos;      /* ENTITY.POSITION */
    float       dist;     /* XZ distance to the hunter */
} mhfu_npc_status_t;

/* Adds an NPC that spawns on every later load of the village; its slot, or < 0 when full, the
 * PAC is unreadable or extra RAM is short. Reads the PAC from the Memory Stick, so call it while
 * mods load, never in the village. */
int mhfu_npc_add(const mhfu_npc_spec_t *spec);

/* slot's live object while the village shows it, else 0. */
uint32_t mhfu_npc_object(int slot);

/* Plays `entry` of the PAC's animation pack on clip slots 0-2 from the next frame, cross-fading
 * over `blend` frames (0 cuts). When a clip that does not loop ends, `then` plays with the same
 * blend; MHFU_NPC_NONE holds the last frame. */
void mhfu_npc_play(int slot, uint16_t entry, uint8_t blend, uint16_t then);

/* Turns slot every frame as `mode` says (MHFU_NPC_FACE_*), by at most `rate` YAW units a frame
 * (mhfu/steer.h). */
void mhfu_npc_face(int slot, int mode, float x, float z, uint16_t rate);

/* slot's state as of its last frame into out; 0 if slot is not in use. */
int mhfu_npc_status(int slot, mhfu_npc_status_t *out);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* MHFU_NPC_H */
