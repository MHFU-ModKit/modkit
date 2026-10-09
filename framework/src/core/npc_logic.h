/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* npc.cpp's pure parts: the spawn rows and when to patch them, a row index's slot, the hunter's
 * frame and the turn, the clip lookup and the clip state machine. Free of PSP headers, so the
 * host test compiles them (tests/npc_host.cpp). */
#ifndef MHFU_CORE_NPC_LOGIC_H
#define MHFU_CORE_NPC_LOGIC_H

#include <math.h>
#include <stdint.h>
#include <string.h>
#include <type_traits>

#include "mhfu/npc.h"
#include "mhfu/steer.h"
#include "addresses.gen.h"

#define NPC_SHIPPED 16   /* the village's own rows in NPC_SPAWN_ROWS_VILLAGE; ours follow */
#define NPC_VILLAGE 1    /* the village's index in NPC_SPAWN_ROWS and NPC_SPAWN_COUNTS */
#define NPC_PARTS   3    /* clip slots we drive: body, head, tail; a 4th is the severed tail */
#define NPC_ROW_BYTES MHFU_NPC_SPAWN_ROW_SIZE

static inline uint32_t npc_u32(const uint8_t *p)
{
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

/* NPC_SPAWN_ROW.HOME of ours, to sit at `at`: {s16 count, s16, ptr list}, the list 8-byte
 * {s16 mesh group, s16 texture swaps, ptr swaps}. The brain's setup reads it whatever the kind
 * (a 0 faults); one entry, no swaps, and kind 5 draws every group anyway. */
#define NPC_HOME_BYTES 0x10
static inline void npc_home(uint8_t *out, uint32_t at)
{
    const uint32_t list = at + 8;
    memset(out, 0, NPC_HOME_BYTES);
    out[0] = 1;
    memcpy(out + 4, &list, 4);
}

/* One row of ours: no AI, our kind, not solid, ANIM_SCALE 1, at the origin (npc.cpp places it). */
static inline void npc_row(uint8_t *out, uint8_t character, float size, uint32_t home)
{
    const float one = 1.0f;
    memset(out, 0, NPC_ROW_BYTES);
    out[MHFU_NPC_SPAWN_ROW_CHAR] = character;
    out[MHFU_NPC_SPAWN_ROW_BEHAVIOUR] = 0xFF;
    out[MHFU_NPC_SPAWN_ROW_KIND] = MHFU_NPC_OWN_KIND;
    memcpy(out + MHFU_NPC_SPAWN_ROW_SCALE, &size, 4);
    memcpy(out + MHFU_NPC_SPAWN_ROW_ANIM_SCALE, &one, 4);
    memcpy(out + MHFU_NPC_SPAWN_ROW_HOME, &home, 4);
}

/* The village's table into out: its shipped rows, then a row per (chars[k], sizes[k]) with
 * HOME `home`; the row count. */
static inline int npc_rows(uint8_t *out, const uint8_t *shipped, const uint8_t *chars,
                           const float *sizes, int n, uint32_t home)
{
    if (out != shipped) memcpy(out, shipped, NPC_SHIPPED * NPC_ROW_BYTES);
    for (int k = 0; k < n; k++)
        npc_row(out + (NPC_SHIPPED + k) * NPC_ROW_BYTES, chars[k], sizes[k], home);
    return NPC_SHIPPED + n;
}

/* 1 when the chunk [buf, buf + bytes) just decrypted part of the lobby's NPC table and the
 * village entry reads as shipped (rows_ptr, count): patch it now. A table split over two chunks
 * patches on the second, and any other overlay over these addresses fails the values. */
static inline int npc_chunk_patches(uint32_t buf, uint32_t bytes, uint32_t rows_ptr,
                                    uint8_t count)
{
    const uint32_t lo = MHFU_NPC_SPAWN_ROWS_VILLAGE;
    const uint32_t hi = MHFU_NPC_SPAWN_COUNTS + MHFU_NPC_SPAWN_COUNTS_COUNT;
    return buf < hi && buf + bytes > lo && rows_ptr == MHFU_NPC_SPAWN_ROWS_VILLAGE
           && count == NPC_SHIPPED;
}

/* Our slot of village row `index`, or -1. */
static inline int npc_slot_of(uint32_t index, uint32_t count)
{
    return index >= NPC_SHIPPED && index - NPC_SHIPPED < count ? (int)(index - NPC_SHIPPED) : -1;
}

/* (right, ahead) in the frame of a hunter at (hx, hz) facing yaw, into world (x, z). YAW is
 * atan2(dx, dz): ahead is (sin, cos) and right (-cos, sin), as the stick maps (navigation). */
static inline void npc_hunter_point(float hx, float hz, uint16_t yaw, float right, float ahead,
                                    float *x, float *z)
{
    const float a = (float)yaw * (6.28318531f / MHFU_STEER_TURN);
    const float s = sinf(a), c = cosf(a);
    *x = hx + ahead * s - right * c;
    *z = hz + ahead * c + right * s;
}

typedef struct {
    uint8_t  mode;    /* MHFU_NPC_FACE_* */
    uint8_t  _pad;
    uint16_t rate;    /* most YAW units a frame */
    float    x, z;
} npc_face_t;

/* YAW after one frame's turn of an NPC at (px, pz) as `f` says, the hunter at (hx, hz) facing
 * hyaw; `yaw` when there is nothing to turn to. */
static inline uint16_t npc_turn(uint16_t yaw, const npc_face_t *f, float px, float pz, float hx,
                                float hz, uint16_t hyaw)
{
    float tx, tz;
    if (f->mode == MHFU_NPC_FACE_POINT) {
        tx = f->x;
        tz = f->z;
    } else if (f->mode == MHFU_NPC_FACE_HUNTER) {
        npc_hunter_point(hx, hz, hyaw, f->x, f->z, &tx, &tz);
    } else {
        return yaw;
    }
    const float dx = tx - px, dz = tz - pz;
    if (dx * dx + dz * dz < 1.0f) return yaw;   /* on the spot: no bearing */
    return mhfu_steer_toward(yaw, mhfu_steer_bearing(dx, dz), f->rate);
}

/* The offset in `pack` (an animation pack, docs/formats/animation.md) of slot `entry` of
 * `stream`, 0 when the stream or the slot is empty or out of range. */
static inline uint32_t npc_clip(const uint8_t *pack, uint32_t stream, uint32_t entry)
{
    const uint32_t streams = npc_u32(pack + 4) / 8 - 1;   /* stream 0's table follows the header */
    if (streams > 16 || stream >= streams) return 0;
    if (entry >= npc_u32(pack + 8 * stream)) return 0;
    const uint32_t off = npc_u32(pack + npc_u32(pack + 8 * stream + 4) + 4 * entry);
    return off == 0xFFFFFFFFu ? 0 : off;
}

/* npc_clip, or the stream's first clip when `entry` has none there: a fresh block needs one. */
static inline uint32_t npc_first_clip(const uint8_t *pack, uint32_t stream, uint32_t entry)
{
    uint32_t off = npc_clip(pack, stream, entry);
    for (uint32_t e = 0; !off && e < 100; e++) off = npc_clip(pack, stream, e);
    return off;
}

/* The first joint of parts 1..NPC_PARTS-1 into roots (0 for a part the skeleton lacks) from a
 * skeleton sub (docs/formats/pac.md: header 0x0C, parameter section, then the bone records,
 * Bone.stream at +0x50); 0, or -1 when the skeleton does not parse. */
static inline int npc_part_roots(const uint8_t *skel, uint32_t size, uint16_t *roots)
{
    for (int k = 0; k < NPC_PARTS; k++) roots[k] = 0;
    if (size < 0x18) return -1;
    const uint32_t bones = npc_u32(skel + 4) - 1;
    uint32_t at = 0x0C + npc_u32(skel + 0x14);
    for (uint32_t i = 0; i < bones; i++) {
        if (at + 0x54 > size) return -1;
        const uint32_t part = npc_u32(skel + at + 0x50), next = npc_u32(skel + at + 8);
        if (part > 0 && part < NPC_PARTS && !roots[part]) roots[part] = (uint16_t)i;
        if (next < 0x54) return -1;
        at += next;
    }
    return 0;
}

/* The offset of the first sub of a PAC (u32 count, then {offset, size} pairs) that starts with
 * magic, and its size; 0 when there is none. */
static inline uint32_t npc_pac_sub(const uint8_t *pac, uint32_t size, uint32_t magic,
                                   uint32_t *sub_size)
{
    if (size < 4) return 0;
    const uint32_t n = npc_u32(pac);
    for (uint32_t t = 0; t < n && 12 + 8 * t <= size; t++) {
        const uint32_t off = npc_u32(pac + 4 + 8 * t), len = npc_u32(pac + 8 + 8 * t);
        if (off && len >= 4 && off <= size && len <= size - off && npc_u32(pac + off) == magic) {
            *sub_size = len;
            return off;
        }
    }
    return 0;
}

/* mhfu_npc_play's request, written on any thread: the fields, then SEQ bumped. Every field is
 * volatile, so the compiler keeps that order and npc_anim_step's (SEQ, then the fields); one core,
 * so the CPU keeps it too. Two requests between two frames are taken once, as the later. */
typedef struct {
    volatile uint32_t seq;
    volatile uint16_t entry, then;
    volatile uint8_t  blend, _pad[3];
} npc_play_t;
static_assert(std::is_volatile<decltype(npc_play_t::seq)>::value
                  && std::is_volatile<decltype(npc_play_t::entry)>::value
                  && std::is_volatile<decltype(npc_play_t::then)>::value
                  && std::is_volatile<decltype(npc_play_t::blend)>::value,
              "npc_play_t's stores and loads keep their order only while volatile");

/* What slot 0 plays, as the wrapper last left it. */
typedef struct {
    uint32_t seq;    /* the request taken last */
    uint16_t entry, then;
    uint8_t  blend;
    uint8_t  armed;  /* the clip was seen playing since it started */
    uint8_t  _pad[2];
} npc_anim_t;

/* The entry to start this frame, or MHFU_NPC_NONE: a new request; else `then` once a clip seen
 * playing has stopped (one that loops never does). */
static inline uint16_t npc_anim_step(npc_anim_t *a, const npc_play_t *req, int playing)
{
    const uint32_t seq = req->seq;
    if (seq != a->seq) {
        a->seq = seq;
        a->entry = req->entry;
        a->then = req->then;
        a->blend = req->blend;
        a->armed = 0;
        return a->entry;
    }
    if (playing) {
        a->armed = 1;
        return MHFU_NPC_NONE;
    }
    if (!a->armed || a->then == MHFU_NPC_NONE) return MHFU_NPC_NONE;
    a->entry = a->then;
    a->then = MHFU_NPC_NONE;
    a->armed = 0;
    return a->entry;
}

/* The entry a fresh spawn starts on: a waiting request, else what it played when it last left. */
static inline uint16_t npc_anim_spawn(npc_anim_t *a, const npc_play_t *req)
{
    a->armed = 0;   /* so only a request moves it */
    npc_anim_step(a, req, 0);
    return a->entry;
}

/* 1 while a stored object is still our slot's NPC: its vtable, kind and row, and the wrapper ran
 * within `fresh_us`. */
static inline int npc_alive(uint32_t vtable, uint8_t kind, uint16_t index, int slot,
                            uint32_t age_us, uint32_t fresh_us)
{
    return vtable == MHFU_NPC_VTABLE && kind == MHFU_NPC_OWN_KIND
           && index == (uint16_t)(NPC_SHIPPED + slot) && age_us <= fresh_us;
}

#endif /* MHFU_CORE_NPC_LOGIC_H */
