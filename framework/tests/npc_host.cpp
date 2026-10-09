/* SPDX-License-Identifier: MIT */
/* SPDX-FileCopyrightText: 2026 sp00ktober */
/* npc.cpp's pure parts (src/core/npc_logic.h) for the host test, through ctypes. */
#include "npc_logic.h"

extern "C" {
int npc_host_rows(uint8_t *out, const uint8_t *shipped, const uint8_t *chars, const float *sizes,
                  int n, uint32_t home)
{
    return npc_rows(out, shipped, chars, sizes, n, home);
}
void npc_host_home(uint8_t *out, uint32_t at) { npc_home(out, at); }
int npc_host_chunk(uint32_t buf, uint32_t bytes, uint32_t rows, uint8_t count)
{
    return npc_chunk_patches(buf, bytes, rows, count);
}
int npc_host_slot(uint32_t index, uint32_t count) { return npc_slot_of(index, count); }
void npc_host_point(float hx, float hz, uint16_t yaw, float right, float ahead, float *x, float *z)
{
    npc_hunter_point(hx, hz, yaw, right, ahead, x, z);
}
uint16_t npc_host_turn(uint16_t yaw, const npc_face_t *f, float px, float pz, float hx, float hz,
                       uint16_t hyaw)
{
    return npc_turn(yaw, f, px, pz, hx, hz, hyaw);
}
uint32_t npc_host_clip(const uint8_t *pack, uint32_t stream, uint32_t entry)
{
    return npc_clip(pack, stream, entry);
}
uint32_t npc_host_first_clip(const uint8_t *pack, uint32_t stream, uint32_t entry)
{
    return npc_first_clip(pack, stream, entry);
}
int npc_host_roots(const uint8_t *skel, uint32_t size, uint16_t *roots)
{
    return npc_part_roots(skel, size, roots);
}
uint32_t npc_host_sub(const uint8_t *pac, uint32_t size, uint32_t magic, uint32_t *sub_size)
{
    return npc_pac_sub(pac, size, magic, sub_size);
}
uint16_t npc_host_step(npc_anim_t *a, const npc_play_t *req, int playing)
{
    return npc_anim_step(a, req, playing);
}
uint16_t npc_host_spawn(npc_anim_t *a, const npc_play_t *req) { return npc_anim_spawn(a, req); }
void npc_host_take(npc_course_t *c, const npc_face_order_t *f, const npc_arrive_order_t *a)
{
    npc_course_take(c, f, a);
}
uint16_t npc_host_frame(npc_anim_t *a, const npc_play_t *req, int playing, npc_course_t *c,
                        float px, float pz, float hx, float hz, uint16_t hyaw)
{
    return npc_frame_entry(a, req, playing, c, px, pz, hx, hz, hyaw);
}
int npc_host_alive(uint32_t vtable, uint8_t kind, uint16_t index, int slot, uint32_t age_us,
                   uint32_t fresh_us)
{
    return npc_alive(vtable, kind, index, slot, age_us, fresh_us);
}
}
